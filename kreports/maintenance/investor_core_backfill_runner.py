"""Fail-closed bounded execution for planner-selected investor-core targets."""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
from typing import Any, Callable, Iterator
from urllib.parse import unquote

import httpx
from sqlalchemy.engine import make_url
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kreports.collector.fetcher import (
    DartApiAuthError,
    DartApiLimitExceeded,
    DartBoundedStop,
    DartRequestBudgetExceeded,
    DartTransportError,
    request_budget,
)
from kreports.config import settings
from kreports.db.readonly_snapshot import (
    ReadonlySQLiteSnapshotUnavailable,
    open_checkpointed_readonly_sqlite,
)
from kreports.runtime import require_collector_mode

REPORT_SCHEMA = "investor_core_backfill_report"
REPORT_VERSION = 1
TARGET_SAMPLE_LIMIT = 20
MIN_FREE_SPACE_BYTES = 10 * 1024**3
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


class InvestorCoreBackfillError(RuntimeError):
    """Stable public error for fail-closed runner validation failures."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class _DatabaseIdentity:
    path: Path
    device: int
    inode: int


class _Target:
    __slots__ = ("corp_code", "stock_code", "year")

    def __init__(self, corp_code: str, stock_code: str, year: int) -> None:
        self.corp_code = corp_code
        self.stock_code = stock_code
        self.year = year

    def as_dict(self) -> dict[str, object]:
        return {
            "corp_code": self.corp_code,
            "stock_code": self.stock_code,
            "year": self.year,
        }

    def key(self) -> tuple[str, str, int]:
        return self.corp_code, self.stock_code, self.year


def _fail(code: str, message: str) -> InvestorCoreBackfillError:
    return InvestorCoreBackfillError(code, message)


def _absolute_input_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else Path.cwd() / path


def _reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            if stat.S_ISLNK(os.lstat(current).st_mode):
                raise _fail(
                    "database_symlink_rejected",
                    "database path must not contain symlinks",
                )
        except FileNotFoundError:
            break
        except OSError as exc:
            raise _fail(
                "database_unavailable",
                "database path cannot be inspected",
            ) from exc


def _resolve_regular_database(value: str | Path) -> Path:
    raw_path = _absolute_input_path(value)
    _reject_symlink_components(raw_path)
    try:
        resolved = raw_path.resolve(strict=True)
        path_stat = os.stat(resolved, follow_symlinks=False)
    except FileNotFoundError as exc:
        raise _fail(
            "database_unavailable",
            "database must be an existing regular SQLite file",
        ) from exc
    except OSError as exc:
        raise _fail(
            "database_unavailable",
            "database must be an existing regular SQLite file",
        ) from exc
    if not stat.S_ISREG(path_stat.st_mode):
        raise _fail(
            "database_unavailable",
            "database must be an existing regular SQLite file",
        )
    if path_stat.st_nlink != 1:
        raise _fail(
            "database_hardlink_rejected",
            "database path must have exactly one hard link",
        )
    try:
        with open_checkpointed_readonly_sqlite(resolved) as connection:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA schema_version").fetchone()
    except (ReadonlySQLiteSnapshotUnavailable, sqlite3.Error) as exc:
        raise _fail(
            "database_unavailable",
            "database must be a readable checkpointed SQLite file",
        ) from exc
    return resolved


def _capture_database_identity(database: Path) -> _DatabaseIdentity:
    """Capture the single-link file identity used by the bounded writer."""
    try:
        _reject_symlink_components(database)
        path_stat = os.stat(database, follow_symlinks=False)
    except (FileNotFoundError, OSError) as exc:
        raise _fail("database_unavailable", "database path cannot be inspected") from exc
    if not stat.S_ISREG(path_stat.st_mode):
        raise _fail("database_unavailable", "database must be a regular SQLite file")
    if path_stat.st_nlink != 1:
        raise _fail(
            "database_hardlink_rejected",
            "database path must have exactly one hard link",
        )
    return _DatabaseIdentity(database, int(path_stat.st_dev), int(path_stat.st_ino))


def _revalidate_database_identity(identity: _DatabaseIdentity) -> None:
    """Fail closed if the requested pathname no longer names the original file."""
    try:
        path_stat = os.stat(identity.path, follow_symlinks=False)
    except (FileNotFoundError, OSError) as exc:
        raise _fail(
            "database_identity_changed",
            "database identity changed during bounded execution",
        ) from exc
    if (
        not stat.S_ISREG(path_stat.st_mode)
        or path_stat.st_nlink != 1
        or int(path_stat.st_dev) != identity.device
        or int(path_stat.st_ino) != identity.inode
    ):
        raise _fail(
            "database_identity_changed",
            "database identity changed during bounded execution",
        )


def _verify_writer_connection_identity(connection: object, identity: _DatabaseIdentity) -> None:
    """Verify the actual SQLAlchemy writer points at the requested inode."""
    try:
        rows = connection.exec_driver_sql("PRAGMA database_list").fetchall()  # type: ignore[attr-defined]
        main_path = next(str(row[2]) for row in rows if row[1] == "main")
        path_stat = os.stat(main_path, follow_symlinks=False)
    except (OSError, StopIteration, AttributeError) as exc:
        raise _fail(
            "database_writer_identity_mismatch",
            "collector writer does not match the requested database",
        ) from exc
    if (
        not stat.S_ISREG(path_stat.st_mode)
        or path_stat.st_nlink != 1
        or int(path_stat.st_dev) != identity.device
        or int(path_stat.st_ino) != identity.inode
    ):
        raise _fail(
            "database_writer_identity_mismatch",
            "collector writer does not match the requested database",
        )


@contextmanager
def _bound_financial_writer(identity: _DatabaseIdentity) -> Iterator[Callable[..., str]]:
    """Bind fin_collector's actual session factory to the checked target file."""
    from kreports.collector import fin_collector

    writer_engine = create_engine(
        f"sqlite:///{identity.path}",
        connect_args={"check_same_thread": False, "timeout": 60},
        echo=False,
    )
    writer_session = sessionmaker(bind=writer_engine, autocommit=False, autoflush=False)
    original_get_session = fin_collector.get_session

    @contextmanager
    def exact_get_session() -> Iterator[object]:
        _revalidate_database_identity(identity)
        session = writer_session()
        try:
            _verify_writer_connection_identity(session.connection(), identity)
            yield session
            _revalidate_database_identity(identity)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    fin_collector.get_session = exact_get_session
    try:
        yield fin_collector.collect_financial
    finally:
        fin_collector.get_session = original_get_session
        writer_engine.dispose()


def _checkpoint_wal(identity: _DatabaseIdentity) -> bool:
    """Durably fold all released writer pages into the requested SQLite file."""
    _revalidate_database_identity(identity)
    try:
        with sqlite3.connect(identity.path) as connection:
            result = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    except sqlite3.Error as exc:
        raise _fail(
            "durability_checkpoint_failed",
            "SQLite WAL checkpoint could not be completed",
        ) from exc
    _revalidate_database_identity(identity)
    # SQLite reports (0, -1, -1) when the database is not in WAL mode.  There
    # are then no WAL frames to leave out of the immutable post-run evidence.
    if result is None or int(result[0]) != 0 or int(result[1]) not in {-1, 0}:
        raise _fail(
            "durability_checkpoint_failed",
            "SQLite WAL checkpoint could not be completed",
        )
    return True


def _database_path_from_url(url: object, *, label: str) -> Path:
    if not isinstance(url, str) or not url.strip():
        raise _fail("database_binding_mismatch", f"{label} must be a file SQLite URL")
    try:
        parsed = make_url(url)
    except Exception as exc:
        raise _fail("database_binding_mismatch", f"{label} must be a file SQLite URL") from exc
    if parsed.get_backend_name() != "sqlite" or parsed.query:
        raise _fail("database_binding_mismatch", f"{label} must be a file SQLite URL")
    database = parsed.database
    if not database or database == ":memory:" or database.startswith("file:"):
        raise _fail("database_binding_mismatch", f"{label} must be a file SQLite URL")
    try:
        return _resolve_regular_database(Path(unquote(database)))
    except InvestorCoreBackfillError as exc:
        if exc.code == "database_symlink_rejected":
            raise _fail("database_binding_mismatch", f"{label} path is ambiguous") from exc
        raise _fail("database_binding_mismatch", f"{label} does not resolve to the target DB") from exc


def _validate_process_binding(database: Path, settings_obj: object) -> None:
    configured = _database_path_from_url(
        getattr(settings_obj, "db_url", None),
        label="settings.db_url",
    )
    if configured != database:
        raise _fail(
            "database_binding_mismatch",
            "--db does not match settings.db_url",
        )
    if "DB_URL" in os.environ:
        process_path = _database_path_from_url(os.environ["DB_URL"], label="DB_URL")
        if process_path != database:
            raise _fail(
                "database_binding_mismatch",
                "--db does not match process DB_URL",
            )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_expected_hash(expected: str | None, actual: str, *, execute: bool) -> None:
    if execute and not expected:
        raise _fail(
            "expected_db_sha256_required",
            "--expected-db-sha256 is required with --execute",
        )
    if expected is None:
        return
    if not _SHA256.fullmatch(expected):
        raise _fail(
            "invalid_expected_db_sha256",
            "expected database SHA-256 must be 64 hexadecimal characters",
        )
    if expected.lower() != actual:
        raise _fail(
            "expected_db_sha256_mismatch",
            "database SHA-256 does not match --expected-db-sha256",
        )


def _default_free_space_probe(database: Path) -> int:
    return int(shutil.disk_usage(database.parent).free)


def _read_only_connection(database: Path) -> sqlite3.Connection:
    connection = open_checkpointed_readonly_sqlite(database)
    connection.row_factory = sqlite3.Row
    return connection


def _count_rows(
    connection: sqlite3.Connection,
    table: str,
    where: str,
    parameters: tuple[object, ...],
) -> int:
    try:
        return int(
            connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {where}",
                parameters,
            ).fetchone()[0]
        )
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return 0
        raise _fail("relevant_row_count_failed", "relevant database rows could not be counted") from exc


def _target_where(targets: list[_Target], year_column: str) -> tuple[str, tuple[object, ...]]:
    if not targets:
        return "1 = 0", ()
    clauses = [f"(corp_code = ? AND {year_column} = ?)" for _ in targets]
    parameters: list[object] = []
    for target in targets:
        parameters.extend((target.corp_code, target.year))
    return " OR ".join(clauses), tuple(parameters)


def _relevant_row_counts(database: Path, targets: list[_Target]) -> dict[str, int]:
    with _read_only_connection(database) as connection:
        financial_where, financial_params = _target_where(targets, "year")
        fact_where, fact_params = _target_where(targets, "bsns_year")
        fetch_where, fetch_params = _target_where(targets, "year")
        return {
            "financials": _count_rows(
                connection,
                "financials",
                f"quarter = 4 AND ({financial_where})",
                financial_params,
            ),
            "financial_facts": _count_rows(
                connection,
                "financial_facts",
                f"reprt_code = '11011' AND ({fact_where})",
                fact_params,
            ),
            "fetch_log": _count_rows(
                connection,
                "fetch_log",
                f"task_type = 'financial' AND quarter = 4 AND ({fetch_where})",
                fetch_params,
            ),
        }


def _annual_core_source_cached(
    database: Path,
    corp_code: str,
    year: int,
    quarter: int,
) -> bool:
    """Return whether local source rows can rebuild all annual core metrics."""
    if quarter != 4:
        return False
    from kreports.maintenance.financial_compact import METRIC_MAP, _compact_rows
    from kreports.semantic.metrics import CORE_FINANCIAL_METRICS

    connection = sqlite3.connect(
        f"{database.as_uri()}?mode=ro",
        uri=True,
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    try:
        summary_rows = connection.execute(
            "SELECT revenue, operating_profit, net_income, total_assets, "
            "total_debt, total_equity, operating_cf, source "
            "FROM financials WHERE corp_code=? AND year=? AND quarter=4",
            (corp_code, year),
        ).fetchall()
        if any(
            row["source"] in {"acnt", "acntall"}
            and all(row[field] is not None for field in (
                "revenue",
                "operating_profit",
                "net_income",
                "total_assets",
                "total_debt",
                "total_equity",
                "operating_cf",
            ))
            for row in summary_rows
        ):
            return True

        account_ids = tuple(METRIC_MAP)
        placeholders = ",".join("?" for _ in account_ids)
        fact_rows = connection.execute(
            "SELECT corp_code, bsns_year, fs_div, sj_div, account_id, "
            "account_nm, thstrm_amount FROM financial_facts "
            "WHERE corp_code=? AND bsns_year=? AND reprt_code='11011' "
            f"AND account_id IN ({placeholders})",
            (corp_code, year, *account_ids),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return False
        raise
    finally:
        connection.close()

    required = set(CORE_FINANCIAL_METRICS)
    metrics_by_fs: dict[str, set[str]] = {}
    for row in _compact_rows([dict(row) for row in fact_rows]):
        if row["amount"] is not None:
            metrics_by_fs.setdefault(str(row["fs_div"]), set()).add(
                str(row["metric_key"])
            )
    return any(required <= metrics for metrics in metrics_by_fs.values())


def _planner_summary(plan: dict[str, Any]) -> dict[str, int]:
    names = ("denominator", "numerator", "target_numerator", "shortfall")
    summary: dict[str, int] = {}
    for name in names:
        value = plan.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise _fail("invalid_planner_output", f"planner field {name} is invalid")
        summary[name] = value
    return summary


def _extract_targets(
    plan: dict[str, Any],
    *,
    source_ready_only: bool,
) -> tuple[list[_Target], int]:
    selected = plan.get("selected_companies")
    if not isinstance(selected, list):
        raise _fail("invalid_planner_output", "planner selected_companies is invalid")
    non_ready_count = sum(
        1 for candidate in selected
        if isinstance(candidate, dict) and candidate.get("source_ready") is not True
    )
    candidates: list[dict[str, Any]] = []
    for candidate in selected:
        if not isinstance(candidate, dict):
            raise _fail("invalid_planner_output", "planner selected company is invalid")
        if source_ready_only and candidate.get("source_ready") is not True:
            continue
        if not isinstance(candidate.get("corp_code"), str) or not candidate["corp_code"]:
            raise _fail("invalid_planner_output", "planner corp_code is invalid")
        if not isinstance(candidate.get("stock_code"), str) or not candidate["stock_code"]:
            raise _fail("invalid_planner_output", "planner stock_code is invalid")
        if not isinstance(candidate.get("selected_years"), list):
            raise _fail("invalid_planner_output", "planner selected_years is invalid")
        candidates.append(candidate)

    targets: list[_Target] = []
    seen: set[tuple[str, str, int]] = set()
    for candidate in candidates:
        corp_code = candidate["corp_code"]
        stock_code = candidate["stock_code"]
        for year in candidate["selected_years"]:
            if isinstance(year, bool) or not isinstance(year, int) or year <= 0:
                raise _fail("invalid_planner_output", "planner selected year is invalid")
            target = _Target(corp_code, stock_code, year)
            if target.key() in seen:
                raise _fail("duplicate_planner_target", "planner contains duplicate target entries")
            seen.add(target.key())
            targets.append(target)
    targets.sort(key=lambda target: target.key())
    return targets, non_ready_count


def _target_digest(targets: list[_Target]) -> str:
    canonical = [target.as_dict() for target in targets]
    payload = json.dumps(
        canonical,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _generic_stop(exc: BaseException) -> tuple[str, str]:
    if isinstance(exc, DartRequestBudgetExceeded):
        return "api_budget_exhausted", "DART request budget exhausted"
    if isinstance(exc, DartApiAuthError):
        return "dart_auth_failure", "DART authentication failed"
    if isinstance(exc, DartApiLimitExceeded):
        return "dart_quota_failure", "DART API quota or limit failure"
    if isinstance(exc, (DartTransportError, httpx.HTTPError)):
        return "dart_transport_failure", "DART transport or HTTP failure"
    return "collector_failure", "bounded collector failed"


@contextmanager
def _force_bounded_retries(settings_obj: object) -> Iterator[None]:
    original = getattr(settings_obj, "max_retries")
    setattr(settings_obj, "max_retries", 1)
    try:
        yield
    finally:
        setattr(settings_obj, "max_retries", original)


def _validate_free_space(
    free_space: int,
    *,
    disk_probe: Callable[[Path], int],
) -> None:
    del disk_probe
    if free_space < MIN_FREE_SPACE_BYTES:
        raise _fail(
            "insufficient_free_space",
            "free-space reserve is below the 10 GiB minimum",
        )


def run_investor_core_backfill(
    db_path: str | Path,
    *,
    expected_db_sha256: str | None = None,
    execute: bool = False,
    max_api_calls: int | None = None,
    coverage_year: int | None = None,
    threshold_pct: float = 95.0,
    source_ready_only: bool = True,
    planner_fn: Callable[..., dict[str, Any]] | None = None,
    collector_fn: Callable[..., str] | None = None,
    cache_checker: Callable[[str, int, int], bool] | None = None,
    disk_probe: Callable[[Path], int] = _default_free_space_probe,
    settings_obj: object = settings,
) -> dict[str, Any]:
    """Plan or execute a bounded annual investor-core backfill session."""
    database = _resolve_regular_database(db_path)
    identity = _capture_database_identity(database)
    _validate_process_binding(database, settings_obj)
    before_sha256 = _sha256_file(database)
    _validate_expected_hash(expected_db_sha256, before_sha256, execute=execute)

    if execute:
        if not isinstance(max_api_calls, int) or isinstance(max_api_calls, bool) or max_api_calls <= 0:
            raise _fail(
                "max_api_calls_required",
                "--max-api-calls must be a positive integer with --execute",
            )
        if not source_ready_only:
            raise _fail(
                "non_source_ready_execution_rejected",
                "execute mode accepts source-ready targets only",
            )
        try:
            require_collector_mode("run-investor-core-backfill")
        except RuntimeError as exc:
            raise _fail(
                "collector_mode_required",
                "collector runtime mode is required with --execute",
            ) from exc
        if not getattr(settings_obj, "dart_api_key", ""):
            raise _fail("dart_api_key_required", "DART API key is required for execute mode")

    if planner_fn is None:
        from kreports.maintenance.investor_core_backfill_plan import (
            plan_investor_core_backfill,
        )

        planner_fn = plan_investor_core_backfill
    plan = planner_fn(
        database,
        coverage_year=coverage_year,
        threshold_pct=threshold_pct,
    )
    if not isinstance(plan, dict):
        raise _fail("invalid_planner_output", "planner did not return an object")
    planner_summary = _planner_summary(plan)
    targets, non_ready_count = _extract_targets(
        plan,
        source_ready_only=source_ready_only,
    )
    if execute:
        _revalidate_database_identity(identity)
        if _sha256_file(database) != before_sha256:
            raise _fail(
                "database_changed_before_execution",
                "database changed after preflight and before execution",
            )
    target_dicts = [target.as_dict() for target in targets]
    target_digest = _target_digest(targets)
    before_rows = _relevant_row_counts(database, targets)
    try:
        free_before = int(disk_probe(database))
    except Exception as exc:
        raise _fail("free_space_probe_failed", "free-space probe failed") from exc

    budget = None
    stop_code: str | None = None
    stop_message: str | None = None
    wal_checkpointed: bool | None = None
    action_attempted = False
    outcome_counts: Counter[str] = Counter()
    outcome_samples: list[dict[str, object]] = []

    def record_outcome(target: _Target, outcome: str) -> None:
        outcome_counts[outcome] += 1
        if len(outcome_samples) < TARGET_SAMPLE_LIMIT:
            outcome_samples.append({**target.as_dict(), "outcome": outcome})

    def record_not_run(start_index: int) -> None:
        for pending in targets[start_index:]:
            record_outcome(pending, "not_run")

    if not execute:
        for target in targets:
            record_outcome(target, "planned")
    else:
        try:
            _validate_free_space(free_before, disk_probe=disk_probe)
        except InvestorCoreBackfillError as exc:
            stop_code, stop_message = exc.code, exc.message
            record_not_run(0)
        else:
            if cache_checker is None:
                def default_cache_checker(
                    corp_code: str,
                    year: int,
                    quarter: int,
                ) -> bool:
                    return _annual_core_source_cached(
                        database,
                        corp_code,
                        year,
                        quarter,
                    )

                cache_checker = default_cache_checker
            writer_scope = (
                _bound_financial_writer(identity)
                if collector_fn is None
                else nullcontext(collector_fn)
            )
            with writer_scope as active_collector, _force_bounded_retries(settings_obj), request_budget(max_api_calls) as budget_scope:
                budget = budget_scope
                for index, target in enumerate(targets):
                    try:
                        _revalidate_database_identity(identity)
                        free_for_target = int(disk_probe(database))
                        _validate_free_space(free_for_target, disk_probe=disk_probe)
                    except InvestorCoreBackfillError as exc:
                        stop_code, stop_message = exc.code, exc.message
                        record_not_run(index)
                        break
                    except Exception:
                        stop_code = "free_space_probe_failed"
                        stop_message = "free-space probe failed"
                        record_not_run(index)
                        break

                    try:
                        if cache_checker(target.corp_code, target.year, 4):
                            record_outcome(target, "cached")
                        else:
                            action_attempted = True
                            result = active_collector(target.stock_code, target.year, quarter=4)
                            status = str(result).strip().lower()
                            if status not in {"success", "no_data", "error", "skipped"}:
                                status = "error"
                            record_outcome(target, status)
                    except DartBoundedStop as exc:
                        stop_code, stop_message = _generic_stop(exc)
                        record_outcome(target, "stopped")
                        record_not_run(index + 1)
                        break
                    except httpx.HTTPError as exc:
                        stop_code, stop_message = _generic_stop(exc)
                        record_outcome(target, "stopped")
                        record_not_run(index + 1)
                        break
                    except Exception:
                        stop_code = "collector_failure"
                        stop_message = "bounded collector failed"
                        record_outcome(target, "stopped")
                        record_not_run(index + 1)
                        break

                    try:
                        _revalidate_database_identity(identity)
                        free_after_target = int(disk_probe(database))
                        _validate_free_space(free_after_target, disk_probe=disk_probe)
                    except InvestorCoreBackfillError as exc:
                        stop_code, stop_message = exc.code, exc.message
                        record_not_run(index + 1)
                        break
                    except Exception:
                        stop_code = "free_space_probe_failed"
                        stop_message = "free-space probe failed"
                        record_not_run(index + 1)
                        break

    after_sha256: str | None = None
    after_rows: dict[str, int] | None = None
    free_after: int | None = None
    if execute and action_attempted:
        try:
            wal_checkpointed = _checkpoint_wal(identity)
        except InvestorCoreBackfillError as exc:
            stop_code, stop_message = exc.code, exc.message
    try:
        _revalidate_database_identity(identity)
        after_sha256 = _sha256_file(database)
        after_rows = _relevant_row_counts(database, targets)
        _revalidate_database_identity(identity)
        free_after = int(disk_probe(database))
    except Exception:
        if stop_code is None:
            stop_code = "evidence_collection_failed"
            stop_message = "post-run evidence could not be collected"

    counts = dict(sorted(outcome_counts.items()))
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "schema_version": str(REPORT_VERSION),
        "version": REPORT_VERSION,
        "db_path": str(database),
        "db_sha256_before": before_sha256,
        "db_sha256_after": after_sha256,
        "before_db_sha256": before_sha256,
        "after_db_sha256": after_sha256,
        "planner": planner_summary,
        "planner_denominator": planner_summary["denominator"],
        "planner_numerator": planner_summary["numerator"],
        "planner_target_numerator": planner_summary["target_numerator"],
        "planner_shortfall": planner_summary["shortfall"],
        "target_count": len(targets),
        "target_digest": target_digest,
        "target_samples": target_dicts[:TARGET_SAMPLE_LIMIT],
        "excluded_non_source_ready_count": non_ready_count if source_ready_only else 0,
        "dry_run": not execute,
        "execute": execute,
        "max_api_calls": max_api_calls if execute else None,
        "used_api_calls": budget.used_calls if budget is not None else 0,
        "endpoint_call_counts": dict(sorted(budget.endpoint_counts.items())) if budget is not None else {},
        "target_outcomes": {
            "total": len(targets),
            "counts": counts,
            "samples": outcome_samples,
            "sample_limit": TARGET_SAMPLE_LIMIT,
        },
        "stop_reason": stop_code,
        "stop_message": stop_message,
        "completed": stop_code is None,
        "relevant_row_counts": {"before": before_rows, "after": after_rows},
        "before_row_counts": before_rows,
        "after_row_counts": after_rows,
        "free_space_before": free_before,
        "free_space_after": free_after,
        "free_space_minimum": MIN_FREE_SPACE_BYTES,
        "wal_checkpointed": wal_checkpointed,
    }
    return report
