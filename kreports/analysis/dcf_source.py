"""Immutable local source adapter for the reviewable DCF model."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, inspect, text

import kreports.db.engine as engine_module
from kreports.analysis.dcf_model import DcfActualFact, parse_dcf_timestamp
from kreports.semantic.metrics import DCF_MODEL_METRICS, metric_definition


_REQUIRED_COLUMNS = {
    "corp_code",
    "bsns_year",
    "fs_div",
    "metric_key",
    "metric_name",
    "amount",
    "source_account_id",
    "source_account_nm",
    "fetched_at",
}


class DcfSourceUnavailable(RuntimeError):
    """The local compact source cannot be read without unsafe fallback."""


def dcf_source_failure(limitation: object) -> DcfSourceResult:
    """Return the bounded fail-closed source contract used by public adapters."""
    rendered = str(limitation).strip()[:200] or "runtime_db_unavailable"
    return DcfSourceResult(
        status="missing",
        facts=(),
        missing_metrics=DCF_MODEL_METRICS,
        limitations=(rendered,),
    )


@dataclass(frozen=True)
class DcfSourceResult:
    status: str
    facts: tuple[DcfActualFact, ...]
    missing_metrics: tuple[str, ...]
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.status not in {"usable", "partial", "missing"}:
            raise ValueError("source status is invalid")
        facts = tuple(self.facts)
        if not all(isinstance(fact, DcfActualFact) for fact in facts):
            raise TypeError("source facts contain invalid values")
        missing = tuple(self.missing_metrics)
        limitations = tuple(self.limitations)
        if (
            any(
                not isinstance(value, str)
                or not value.strip()
                or len(value.strip()) > 1_000
                for value in missing
            )
            or any(
                not isinstance(value, str)
                or not value.strip()
                or len(value.strip()) > 1_000
                for value in limitations
            )
            or len(missing) > len(DCF_MODEL_METRICS)
            or len(limitations) > 32
        ):
            raise ValueError("source result bounds are invalid")
        missing = tuple(value.strip() for value in missing)
        limitations = tuple(value.strip() for value in limitations)
        if (
            len(set(missing)) != len(missing)
            or not set(missing) <= set(DCF_MODEL_METRICS)
        ):
            raise ValueError("source missing metrics are invalid")
        if self.status == "usable" and (missing or limitations):
            raise ValueError("usable source cannot contain gaps")
        if self.status == "usable" and {
            fact.metric_key for fact in facts
        } != set(DCF_MODEL_METRICS):
            raise ValueError("usable source must contain every DCF metric")
        if self.status == "missing" and facts:
            raise ValueError("missing source cannot contain facts")
        if self.status == "partial" and not (missing or limitations):
            raise ValueError("partial source must disclose a gap")
        object.__setattr__(self, "facts", facts)
        object.__setattr__(self, "missing_metrics", missing)
        object.__setattr__(self, "limitations", limitations)


def _validate_schema(read_engine) -> set[str]:
    try:
        inspector = inspect(read_engine)
        tables = set(inspector.get_table_names())
    except Exception as exc:
        raise DcfSourceUnavailable(
            f"runtime_db_unavailable:{type(exc).__name__}"
        ) from exc
    if "financial_facts_compact" not in tables:
        raise DcfSourceUnavailable("missing_schema:financial_facts_compact")
    try:
        columns = {
            str(column["name"])
            for column in inspector.get_columns("financial_facts_compact")
        }
    except Exception as exc:
        raise DcfSourceUnavailable(
            f"runtime_db_unavailable:{type(exc).__name__}"
        ) from exc
    missing = sorted(_REQUIRED_COLUMNS - columns)
    if missing:
        raise DcfSourceUnavailable(
            f"missing_columns:financial_facts_compact:{','.join(missing)}"
        )
    return columns


@contextmanager
def dcf_read_engine() -> Iterator:
    """Open SQLite in immutable mode and reject uncheckpointed WAL state."""
    source_engine = engine_module.engine
    if source_engine.dialect.name == "sqlite":
        database = source_engine.url.database
        if database not in {None, "", ":memory:"}:
            database_path = Path(str(database)).expanduser().resolve()
            if not database_path.is_file():
                raise DcfSourceUnavailable("runtime_db_unavailable")
            wal_path = Path(f"{database_path}-wal")
            if wal_path.exists() and wal_path.stat().st_size > 0:
                raise DcfSourceUnavailable(
                    "runtime_db_unavailable:uncheckpointed_wal"
                )
            readonly = create_engine(
                (
                    f"sqlite:///file:{database_path.as_posix()}"
                    "?mode=ro&immutable=1&uri=true"
                ),
                connect_args={"check_same_thread": False},
            )
            try:
                _validate_schema(readonly)
                yield readonly
            finally:
                readonly.dispose()
            return
    _validate_schema(source_engine)
    yield source_engine


def _safe_amount(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return amount if amount.is_finite() else None


def _select_row(metric_key: str, rows: list[dict]) -> tuple[dict | None, str | None]:
    def row_id(row: dict) -> int | None:
        value = row.get("_id")
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            stripped = value.strip()
            if stripped and stripped.lstrip("+-").isdigit():
                return int(stripped)
        return None

    if len(rows) == 1:
        return rows[0], None
    signatures = {
        (
            row.get("amount"),
            row.get("source_account_id"),
            row.get("source_account_nm"),
        )
        for row in rows
    }
    identical_facts = len(signatures) == 1

    parsed_timestamps = []
    for row in rows:
        try:
            parsed_timestamps.append(
                parse_dcf_timestamp(row.get("fetched_at"))
            )
        except (TypeError, ValueError):
            return None, f"duplicate_ambiguous:{metric_key}"
    present = [
        timestamp
        for timestamp in parsed_timestamps
        if timestamp is not None
    ]
    if present and len(present) != len(parsed_timestamps):
        return None, f"duplicate_ambiguous:{metric_key}"
    if present and len({
        timestamp.tzinfo is not None
        and timestamp.utcoffset() is not None
        for timestamp in present
    }) > 1:
        return None, f"duplicate_ambiguous:{metric_key}"

    if present:
        latest = max(present)
        finalists = [
            row
            for row, timestamp in zip(
                rows,
                parsed_timestamps,
                strict=True,
            )
            if timestamp == latest
        ]
        if len(finalists) == 1:
            return (
                finalists[0],
                None
                if identical_facts
                else f"duplicate_resolved_latest:{metric_key}",
            )
    else:
        finalists = rows

    finalist_ids = [row_id(row) for row in finalists]
    if identical_facts and (
        any(value is None for value in finalist_ids)
        or len(set(finalist_ids)) != len(finalist_ids)
    ):
        return min(
            finalists,
            key=lambda row: (
                str(row.get("source_account_nm") or ""),
                str(row.get("source_account_id") or ""),
                str(row.get("fetched_at") or ""),
            ),
        ), None
    if (
        any(value is None for value in finalist_ids)
        or len(set(finalist_ids)) != len(finalist_ids)
    ):
        return None, f"duplicate_ambiguous:{metric_key}"
    latest_id = max(value for value in finalist_ids if value is not None)
    selected = finalists[finalist_ids.index(latest_id)]
    return (
        selected,
        None
        if identical_facts
        else f"duplicate_resolved_latest:{metric_key}",
    )


def _load_with_engine(
    read_engine,
    company: str,
    base_year: int,
    fs_div: str,
) -> DcfSourceResult:
    columns = _validate_schema(read_engine)
    id_select = "id AS _id" if "id" in columns else "NULL AS _id"
    metric_placeholders = ", ".join(
        f":metric_{index}" for index, _ in enumerate(DCF_MODEL_METRICS)
    )
    params = {
        "company": company,
        "base_year": base_year,
        "fs_div": fs_div,
        **{
            f"metric_{index}": metric_key
            for index, metric_key in enumerate(DCF_MODEL_METRICS)
        },
    }
    try:
        with read_engine.connect() as connection:
            rows = connection.execute(text(f"""
                SELECT {id_select}, metric_key, metric_name, amount,
                       source_account_id, source_account_nm, fetched_at
                FROM financial_facts_compact
                WHERE corp_code=:company
                  AND bsns_year=:base_year
                  AND fs_div=:fs_div
                  AND metric_key IN ({metric_placeholders})
                ORDER BY metric_key, fetched_at DESC
            """), params).mappings().all()
    except Exception as exc:
        raise DcfSourceUnavailable(
            f"runtime_db_unavailable:{type(exc).__name__}"
        ) from exc

    by_metric: dict[str, list[dict]] = {}
    for raw in rows:
        row = dict(raw)
        by_metric.setdefault(str(row["metric_key"]), []).append(row)

    facts: list[DcfActualFact] = []
    missing: list[str] = []
    limitations: list[str] = []
    for metric_key in DCF_MODEL_METRICS:
        candidates = by_metric.get(metric_key, [])
        if not candidates:
            missing.append(metric_key)
            continue
        selected, duplicate_note = _select_row(metric_key, candidates)
        if duplicate_note:
            limitations.append(duplicate_note)
        if selected is None:
            missing.append(metric_key)
            continue
        amount = _safe_amount(selected.get("amount"))
        if amount is None:
            missing.append(metric_key)
            limitations.append(f"corrupt_amount:{metric_key}")
            continue
        source_account_id = str(
            selected.get("source_account_id") or ""
        ).strip()
        source_account_name = str(
            selected.get("source_account_nm") or ""
        ).strip()
        if not source_account_id or not source_account_name:
            missing.append(metric_key)
            limitations.append(f"provenance_gap:{metric_key}")
            continue
        try:
            facts.append(DcfActualFact(
                metric_key=metric_key,
                amount=amount,
                unit=metric_definition(metric_key).unit,
                year=base_year,
                fs_div=fs_div,
                source_account_id=source_account_id,
                source_account_name=source_account_name,
                source_table="financial_facts_compact",
                fetched_at=(
                    str(selected["fetched_at"])
                    if selected.get("fetched_at") is not None
                    else None
                ),
            ))
        except (TypeError, ValueError):
            missing.append(metric_key)
            limitations.append(f"invalid_fact_contract:{metric_key}")
    status = (
        "usable"
        if not missing and not limitations
        else "partial" if facts or rows
        else "missing"
    )
    return DcfSourceResult(
        status=status,
        facts=tuple(facts),
        missing_metrics=tuple(missing),
        limitations=tuple(limitations),
    )


def load_dcf_actuals(
    company: str,
    base_year: int,
    fs_div: str,
    *,
    read_engine=None,
) -> DcfSourceResult:
    """Read only the exact requested company/year/FS basis, with no fallback."""
    if isinstance(base_year, bool) or not isinstance(base_year, int):
        return DcfSourceResult(
            status="missing",
            facts=(),
            missing_metrics=DCF_MODEL_METRICS,
            limitations=("invalid_base_year",),
        )
    if fs_div not in {"CFS", "OFS"}:
        return DcfSourceResult(
            status="missing",
            facts=(),
            missing_metrics=DCF_MODEL_METRICS,
            limitations=("invalid_fs_div",),
        )
    try:
        if read_engine is not None:
            return _load_with_engine(
                read_engine,
                str(company),
                base_year,
                fs_div,
            )
        with dcf_read_engine() as safe_engine:
            return _load_with_engine(
                safe_engine,
                str(company),
                base_year,
                fs_div,
            )
    except DcfSourceUnavailable as exc:
        return dcf_source_failure(exc)
