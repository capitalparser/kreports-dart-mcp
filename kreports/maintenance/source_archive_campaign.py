"""Frozen, resumable source-archive campaigns for annual DART reports.

This module owns campaign orchestration only.  It deliberately keeps the
public/runtime database out of the write path: the campaign reads a supplied
collector session, writes append-only local manifests, and delegates object
storage and generic parsing to their respective canonical services.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from kreports.analysis.filing_provenance import latest_annual_filing_anchor_from_rows
from kreports.collector.fetcher import fetch_document_zip_files
from kreports.db.models import Company, Disclosure
from kreports.processor.document_structure import parse_document_structure
from kreports.storage.source_archive import archive_structured_document


__all__ = [
    "SourceArchiveCampaignError",
    "SourceArchivePlan",
    "SourceArchiveReport",
    "SourceArchiveTarget",
    "build_source_archive_plan",
    "run_source_archive_shard",
    "verify_source_archive_campaign",
]


CAMPAIGN_SCHEMA = "source-archive-campaign.v1"
DEFAULT_SHARD_COUNT = 64


class SourceArchiveCampaignError(RuntimeError):
    """Raised when campaign input or persisted state is unsafe to use."""


class ArchiveWriter(Protocol):
    """The narrow immutable-object boundary used by one source asset."""

    def archive_bytes(
        self, *, data: bytes, extension: str, metadata: Mapping[str, str]
    ) -> Any:
        """Archive verified bytes and return an object identity."""


@dataclass(frozen=True)
class SourceArchiveTarget:
    """One company-year canonical annual filing target (or explicit gap)."""

    corp_code: str
    bsns_year: int
    shard: int
    source_receipt: str | None
    report_nm: str | None
    source_uri: str | None
    source_status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SourceArchivePlan:
    """Deterministic target universe frozen before any DART source request."""

    years: tuple[int, ...]
    shard_count: int
    targets: tuple[SourceArchiveTarget, ...]
    target_digest: str
    state_dir: Path | None = None

    @property
    def target_manifest(self) -> dict[str, Any]:
        return {
            "schema": CAMPAIGN_SCHEMA,
            "years": list(self.years),
            "shard_count": self.shard_count,
            "target_digest": self.target_digest,
            "target_count": len(self.targets),
            "targets": [target.to_dict() for target in self.targets],
        }

    def with_state_dir(self, state_dir: Path) -> "SourceArchivePlan":
        return replace(self, state_dir=Path(state_dir))

    def targets_for_shard(self, shard: int) -> tuple[SourceArchiveTarget, ...]:
        _validate_shard(shard, self.shard_count)
        return tuple(target for target in self.targets if target.shard == shard)


@dataclass(frozen=True)
class SourceArchiveReport:
    """One shard attempt, including explicit gap/failure outcomes."""

    shard: int
    apply: bool
    status: str
    target_digest: str
    outcomes: tuple[dict[str, Any], ...]
    manifest_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CAMPAIGN_SCHEMA,
            "shard": self.shard,
            "apply": self.apply,
            "status": self.status,
            "target_digest": self.target_digest,
            "outcome_count": len(self.outcomes),
            "outcomes": list(self.outcomes),
            "manifest_path": str(self.manifest_path) if self.manifest_path else None,
        }


def build_source_archive_plan(
    session: Session,
    years: Iterable[int],
    shard_count: int = DEFAULT_SHARD_COUNT,
) -> SourceArchivePlan:
    """Create a no-write frozen target plan using canonical annual anchors.

    The plan includes every requested company-year.  A missing disclosure is a
    visible ``no_source_metadata`` target, never silently removed from the
    denominator.  The latest annual selector validates the newest correction
    instead of borrowing an older receipt.
    """
    _require_non_runtime_source_session(session)
    normalized_years = _normalize_years(years)
    _validate_shard_count(shard_count)
    corp_codes = tuple(session.scalars(select(Company.corp_code).order_by(Company.corp_code)))
    rows = session.execute(
        select(
            Disclosure.corp_code,
            Disclosure.rcept_no,
            Disclosure.disc_date,
            Disclosure.report_nm,
        ).where(Disclosure.corp_code.in_(corp_codes)).order_by(
            Disclosure.corp_code, Disclosure.disc_date.desc(), Disclosure.rcept_no.desc()
        )
    ).mappings().all() if corp_codes else []
    rows_by_company: dict[str, list[dict[str, Any]]] = {str(code): [] for code in corp_codes}
    for row in rows:
        rows_by_company[str(row["corp_code"])].append(dict(row))

    targets: list[SourceArchiveTarget] = []
    for corp_code in sorted(rows_by_company):
        shard = _company_shard(corp_code, shard_count)
        for year in normalized_years:
            anchor = latest_annual_filing_anchor_from_rows(
                rows_by_company[corp_code], corp_code=corp_code, bsns_year=year
            )
            if anchor is None:
                targets.append(SourceArchiveTarget(
                    corp_code=corp_code,
                    bsns_year=year,
                    shard=shard,
                    source_receipt=None,
                    report_nm=None,
                    source_uri=None,
                    source_status="no_source_metadata",
                ))
                continue
            receipt = str(anchor["rcept_no"])
            targets.append(SourceArchiveTarget(
                corp_code=corp_code,
                bsns_year=year,
                shard=shard,
                source_receipt=receipt,
                report_nm=str(anchor["report_nm"]),
                source_uri=_document_source_uri(receipt),
                source_status="discovered",
            ))
    frozen_targets = tuple(targets)
    digest = _target_digest(normalized_years, shard_count, frozen_targets)
    return SourceArchivePlan(
        years=normalized_years,
        shard_count=shard_count,
        targets=frozen_targets,
        target_digest=digest,
    )


def run_source_archive_shard(
    plan: SourceArchivePlan,
    shard: int,
    archive: ArchiveWriter | None,
    *,
    apply: bool,
) -> SourceArchiveReport:
    """Run one stable shard one source asset at a time.

    ``apply=False`` is a strict no-write/no-fetch preview: it neither creates
    a local manifest nor touches DART/Drive.  ``apply=True`` requires collector
    mode and writes the frozen target manifest before the first fetch.
    """
    _validate_shard(shard, plan.shard_count)
    selected = plan.targets_for_shard(shard)
    if not apply:
        return SourceArchiveReport(
            shard=shard,
            apply=False,
            status="dry_run",
            target_digest=plan.target_digest,
            outcomes=(),
        )
    if archive is None:
        raise SourceArchiveCampaignError("--apply requires a configured immutable Drive archive")
    from kreports.runtime import require_collector_mode

    require_collector_mode("source archive campaign")
    state_dir = _required_state_dir(plan)
    _write_frozen_target_manifest(plan, state_dir)
    shard_dir = state_dir / f"shard-{shard:02d}"
    shard_dir.mkdir(parents=True, exist_ok=True)
    outcomes_path = shard_dir / "outcomes.jsonl"
    previous = _completed_company_years(outcomes_path, plan.target_digest)
    outcomes: list[dict[str, Any]] = []

    for target in selected:
        target_key = (target.corp_code, target.bsns_year)
        if target_key in previous:
            outcomes.append(_outcome(target, "already_structurally_complete"))
            continue
        target_outcomes = _process_target(target, archive)
        for outcome in target_outcomes:
            _append_outcome(outcomes_path, outcome, plan.target_digest)
        outcomes.extend(target_outcomes)

    complete = bool(selected) and all(
        outcome["status"] in {"structurally_complete", "already_structurally_complete"}
        for outcome in outcomes
        if outcome.get("company_year_terminal", True)
    )
    marker = shard_dir / "COMMITTED.json"
    if complete:
        _write_committed_marker(marker, plan, shard, outcomes_path)
    elif marker.exists():
        raise SourceArchiveCampaignError(
            "partial shard cannot retain a COMMITTED.json marker; investigate state manually"
        )
    return SourceArchiveReport(
        shard=shard,
        apply=True,
        status="complete" if complete else "partial",
        target_digest=plan.target_digest,
        outcomes=tuple(outcomes),
        manifest_path=outcomes_path,
    )


def verify_source_archive_campaign(state_dir: Path, *, shard: int | None = None) -> dict[str, Any]:
    """Read local campaign state only; it never contacts Drive or DART."""
    root = Path(state_dir)
    manifest_path = root / "TARGET.json"
    if not manifest_path.is_file():
        raise SourceArchiveCampaignError("frozen TARGET.json is missing")
    manifest = _read_json(manifest_path)
    if manifest.get("schema") != CAMPAIGN_SCHEMA:
        raise SourceArchiveCampaignError("TARGET.json schema is unsupported")
    requested = [shard] if shard is not None else list(range(int(manifest["shard_count"])))
    records: list[dict[str, Any]] = []
    for shard_number in requested:
        _validate_shard(shard_number, int(manifest["shard_count"]))
        shard_dir = root / f"shard-{shard_number:02d}"
        outcome_path = shard_dir / "outcomes.jsonl"
        outcome_count = len(outcome_path.read_text(encoding="utf-8").splitlines()) if outcome_path.is_file() else 0
        records.append({
            "shard": shard_number,
            "outcome_count": outcome_count,
            "committed": (shard_dir / "COMMITTED.json").is_file(),
        })
    return {
        "schema": CAMPAIGN_SCHEMA,
        "target_digest": manifest["target_digest"],
        "target_count": manifest["target_count"],
        "shards": records,
    }


def _process_target(target: SourceArchiveTarget, archive: ArchiveWriter) -> list[dict[str, Any]]:
    if target.source_status != "discovered" or not target.source_receipt or not target.source_uri:
        return [_outcome(target, target.source_status)]
    discovered = _outcome(target, "discovered", company_year_terminal=False)
    try:
        documents = fetch_document_zip_files(target.source_receipt)
    except Exception as exc:  # The campaign records a retryable external failure.
        return [discovered, _outcome(target, "fetch_failed", error=_bounded_error(exc))]
    if not documents:
        return [discovered, _outcome(target, "partial_source", error="document_xml_empty")]
    asset_failures: list[dict[str, Any]] = []
    asset_successes: list[dict[str, Any]] = []
    for name, content in sorted(documents.items()):
        if not isinstance(content, str) or not content.strip():
            asset_failures.append(_outcome(target, "asset_empty", asset_name=name, company_year_terminal=False))
            continue
        try:
            raw_bytes = content.encode("utf-8")
            source_sha256 = hashlib.sha256(raw_bytes).hexdigest()
            metadata = {
                "source_receipt": target.source_receipt,
                "source_uri": target.source_uri,
                "archive_version": "raw-source-v1",
                "corp_code": target.corp_code,
                "bsns_year": str(target.bsns_year),
                "asset_name": name,
            }
            raw_object = archive.archive_bytes(data=raw_bytes, extension="xml", metadata=metadata)
            asset_successes.append(_outcome(
                target,
                "archived_verified",
                asset_name=name,
                company_year_terminal=False,
                raw_object=_object_summary(raw_object),
            ))
            parsed = parse_document_structure(
                raw_bytes,
                content_type="xml",
                source_sha256=source_sha256,
                source_receipt=target.source_receipt,
                source_uri=target.source_uri,
            )
            parsed_object = archive_structured_document(archive, parsed)  # type: ignore[arg-type]
            parsed_outcome = _outcome(
                target,
                "generically_parsed",
                asset_name=name,
                company_year_terminal=False,
                structural_status=parsed.structural_status,
                parsed_object=_object_summary(parsed_object),
            )
            if parsed.structural_status != "complete":
                asset_successes.append(parsed_outcome)
                asset_failures.append(_outcome(
                    target, "asset_requires_review", asset_name=name,
                    company_year_terminal=False,
                ))
                continue
            asset_successes.append(parsed_outcome)
        except Exception as exc:
            asset_failures.append(_outcome(
                target, "asset_failed", asset_name=name, error=_bounded_error(exc),
                company_year_terminal=False,
            ))
    if asset_failures:
        return [discovered, *asset_successes, *asset_failures, _outcome(target, "partial_source")]
    return [discovered, *asset_successes, _outcome(target, "structurally_complete")]


def _outcome(
    target: SourceArchiveTarget,
    status: str,
    *,
    asset_name: str | None = None,
    error: str | None = None,
    company_year_terminal: bool = True,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "schema": CAMPAIGN_SCHEMA,
        "recorded_at": datetime.now(UTC).isoformat(),
        "corp_code": target.corp_code,
        "bsns_year": target.bsns_year,
        "shard": target.shard,
        "source_receipt": target.source_receipt,
        "source_uri": target.source_uri,
        "status": status,
        "asset_name": asset_name,
        "error": error,
        "company_year_terminal": company_year_terminal,
        **extra,
    }


def _write_frozen_target_manifest(plan: SourceArchivePlan, state_dir: Path) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    destination = state_dir / "TARGET.json"
    payload = _canonical_json(plan.target_manifest)
    if destination.exists():
        if destination.read_bytes() != payload:
            raise SourceArchiveCampaignError(
                "campaign TARGET.json conflicts with the supplied frozen target plan"
            )
        return
    _atomic_write(destination, payload)


def _append_outcome(path: Path, outcome: Mapping[str, Any], target_digest: str) -> None:
    row = dict(outcome)
    row["target_digest"] = target_digest
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        handle.write("\n")


def _completed_company_years(path: Path, target_digest: str) -> set[tuple[str, int]]:
    if not path.is_file():
        return set()
    completed: set[tuple[str, int]] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("target_digest") != target_digest:
            raise SourceArchiveCampaignError("shard outcomes belong to a different frozen target plan")
        if row.get("status") == "structurally_complete":
            completed.add((str(row["corp_code"]), int(row["bsns_year"])))
    return completed


def _write_committed_marker(path: Path, plan: SourceArchivePlan, shard: int, outcomes_path: Path) -> None:
    payload = {
        "schema": CAMPAIGN_SCHEMA,
        "target_digest": plan.target_digest,
        "shard": shard,
        "outcomes_sha256": hashlib.sha256(outcomes_path.read_bytes()).hexdigest(),
        "committed_at": datetime.now(UTC).isoformat(),
    }
    if path.exists():
        existing = _read_json(path)
        if existing.get("target_digest") != plan.target_digest:
            raise SourceArchiveCampaignError("COMMITTED.json belongs to a different target plan")
        return
    _atomic_write(path, _canonical_json(payload))


def _target_digest(years: tuple[int, ...], shard_count: int, targets: tuple[SourceArchiveTarget, ...]) -> str:
    payload = {
        "schema": CAMPAIGN_SCHEMA,
        "years": list(years),
        "shard_count": shard_count,
        "targets": [target.to_dict() for target in targets],
    }
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def _company_shard(corp_code: str, shard_count: int) -> int:
    return int.from_bytes(hashlib.sha256(corp_code.encode("utf-8")).digest(), "big") % shard_count


def _normalize_years(years: Iterable[int]) -> tuple[int, ...]:
    try:
        normalized = tuple(sorted({int(year) for year in years}))
    except (TypeError, ValueError) as exc:
        raise SourceArchiveCampaignError("years must be integer business years") from exc
    if not normalized or any(year < 1900 or year > 3000 for year in normalized):
        raise SourceArchiveCampaignError("years must be a non-empty reasonable business-year set")
    return normalized


def _validate_shard_count(shard_count: int) -> None:
    if not isinstance(shard_count, int) or shard_count < 1 or shard_count > 1024:
        raise SourceArchiveCampaignError("shard_count must be an integer from 1 to 1024")


def _validate_shard(shard: int, shard_count: int) -> None:
    if not isinstance(shard, int) or shard < 0 or shard >= shard_count:
        raise SourceArchiveCampaignError(f"shard must be between 0 and {shard_count - 1}")


def _required_state_dir(plan: SourceArchivePlan) -> Path:
    if plan.state_dir is None:
        raise SourceArchiveCampaignError("--apply requires an explicit campaign state directory")
    return Path(plan.state_dir)


def _require_non_runtime_source_session(session: Session) -> None:
    """Refuse the configured public/runtime database even for read-only planning.

    A campaign target manifest becomes durable operational state, so accepting
    the active runtime handle would invite an operator to treat production as a
    collector source.  Tests and explicit candidate sessions use a different
    bind and remain valid.
    """
    from kreports.config import settings

    bind = session.get_bind()
    bound_url = str(getattr(bind, "url", ""))
    configured_url = str(settings.db_url)
    if bound_url == configured_url:
        raise SourceArchiveCampaignError(
            "source archive planning requires a non-runtime collector database"
        )
    if bound_url.startswith("sqlite:///") and configured_url.startswith("sqlite:///"):
        bound_path = Path(bound_url.removeprefix("sqlite:///"))
        configured_path = Path(configured_url.removeprefix("sqlite:///"))
        try:
            if bound_path.expanduser().resolve() == configured_path.expanduser().resolve():
                raise SourceArchiveCampaignError(
                    "source archive planning requires a non-runtime collector database"
                )
        except OSError:
            return


def _document_source_uri(receipt: str) -> str:
    return f"https://opendart.fss.or.kr/api/document.xml?rcept_no={receipt}"


def _object_summary(value: Any) -> dict[str, Any]:
    if hasattr(value, "sha256"):
        return {"sha256": value.sha256, "byte_length": value.byte_length}
    if isinstance(value, Mapping):
        return {key: value[key] for key in ("sha256", "byte_length") if key in value}
    return {"identity": str(value)}


def _bounded_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc)[:240]}"


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceArchiveCampaignError(f"invalid campaign state: {path}") from exc
    if not isinstance(loaded, dict):
        raise SourceArchiveCampaignError(f"campaign state must be an object: {path}")
    return loaded
