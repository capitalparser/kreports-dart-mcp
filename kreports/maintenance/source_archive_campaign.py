"""Frozen, Drive-manifested annual DART source archive campaigns.

The campaign is a local collector workflow.  It derives its denominator from
historical listing evidence, retains exact source bytes before parsing, and
requires both the business-report and audit-report families before a
company-year can be structurally complete.
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol
from urllib.parse import unquote, urlsplit

from sqlalchemy import inspect, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from kreports.analysis.filing_provenance import latest_annual_filing_anchor_from_rows
from kreports.annual_filing_identity import audit_report_receipt_matches_business_year
from kreports.collector.fetcher import (
    DartApiAuthError,
    DartApiLimitExceeded,
    DartBoundedStop,
    DartRequestBudgetExceeded,
    DartTransportError,
    fetch_document_zip_asset_bytes,
    fetch_disclosure_list,
    request_budget,
)
from kreports.db.models import CompanyYearListingMembership, Disclosure
from kreports.processor.document_structure import PARSER_VERSION, parse_document_structure
from kreports.storage.drive_archive import (
    DriveArchiveCommandError,
    DriveArchiveCommandTimeoutError,
    DriveArchiveMetrics,
    DriveArchiveRateLimitError,
)
from kreports.storage.source_archive import archive_structured_document


__all__ = [
    "SourceArchiveCampaignError",
    "SourceArchivePlan",
    "SourceArchiveReport",
    "SourceArchiveTarget",
    "build_source_archive_plan",
    "run_source_archive_shard",
    "verify_source_archive_campaign",
    "write_source_archive_plan_preview",
]


CAMPAIGN_SCHEMA = "source-archive-campaign.v2"
ALL_ISSUER_CAMPAIGN_SCHEMA = "source-archive-campaign.v3"
DEFAULT_SHARD_COUNT = 64
AUDIT_XML_RESOLVER_VERSION = 2
_UNIVERSE_MODES = {"listed", "all_annual_issuers", "audit_report_only"}
_VERIFIED_KOSPI = ("verified_kospi", "KOSPI", "verified_year_specific_membership")
_VERIFIED_KOSDAQ = ("verified_kosdaq", "KOSDAQ", "verified_year_specific_membership")
_OUTSIDE_VERIFIED_MARKETS = (
    "annual_report_issuer_outside_verified_markets",
    "unclassified",
    "no_verified_kospi_kosdaq_membership",
)
_AUDIT_REPORT_ONLY = (
    "audit_report_only_no_business_report",
    "unclassified",
    "audit_report_receipt_without_business_report",
)
_REQUIRED_MEMBERSHIP_COLUMNS = {
    "corp_code", "bsns_year", "market", "status", "evidence_basis",
    "manifest_checksum", "manifest_storage_uri", "normalized_checksum",
    "normalized_storage_uri",
}


class SourceArchiveCampaignError(RuntimeError):
    """Campaign evidence, state, or safety precondition is invalid."""


class ArchiveWriter(Protocol):
    def archive_bytes(self, *, data: bytes, extension: str, metadata: Mapping[str, str]) -> Any:
        """Archive one immutable byte payload and return object identity."""


@dataclass(frozen=True)
class SourceArchiveTarget:
    corp_code: str
    bsns_year: int
    market: str | None
    shard: int
    source_receipt: str | None
    report_nm: str | None
    source_uri: str | None
    source_status: str
    required_report_kinds: tuple[str, str] = ("business_report", "audit_report")
    universe_cohort: str | None = None
    historical_listing_status: str | None = None
    historical_listing_basis: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["required_report_kinds"] = list(self.required_report_kinds)
        # `listed` is an already frozen v2 public identity.  The new evidence
        # fields are emitted only by the explicitly selected v3 denominator.
        if self.universe_cohort is None:
            result.pop("universe_cohort")
            result.pop("historical_listing_status")
            result.pop("historical_listing_basis")
        return result


@dataclass(frozen=True)
class SourceArchivePlan:
    years: tuple[int, ...]
    shard_count: int
    targets: tuple[SourceArchiveTarget, ...]
    target_digest: str
    state_dir: Path | None = None
    universe_mode: str = "listed"

    @property
    def campaign_schema(self) -> str:
        return CAMPAIGN_SCHEMA if self.universe_mode == "listed" else ALL_ISSUER_CAMPAIGN_SCHEMA

    @property
    def target_manifest(self) -> dict[str, Any]:
        manifest = {
            "schema": self.campaign_schema,
            "years": list(self.years),
            "shard_count": self.shard_count,
            "target_digest": self.target_digest,
            "target_count": len(self.targets),
            "targets": [target.to_dict() for target in self.targets],
        }
        if self.universe_mode != "listed":
            manifest["universe_mode"] = self.universe_mode
            manifest["cohort_counts"] = _cohort_counts(self.targets)
        return manifest

    @property
    def campaign_counts(self) -> dict[str, dict[str, int]]:
        """Return v3 cohort/status counts directly from this frozen target set."""
        if self.universe_mode == "listed":
            return {}
        return _all_issuer_campaign_counts(self.targets)

    def with_state_dir(self, state_dir: Path) -> "SourceArchivePlan":
        return replace(self, state_dir=Path(state_dir))

    def targets_for_shard(self, shard: int) -> tuple[SourceArchiveTarget, ...]:
        _validate_shard(shard, self.shard_count)
        return tuple(target for target in self.targets if target.shard == shard)


@dataclass(frozen=True)
class SourceArchiveReport:
    shard: int
    apply: bool
    status: str
    target_digest: str
    outcomes: tuple[dict[str, Any], ...]
    manifest_path: Path | None = None
    dart_calls_used: int = 0
    dart_calls_budget: int | None = None
    universe_mode: str = "listed"
    campaign_counts: Mapping[str, Mapping[str, int]] | None = None
    stop_reason: str | None = None
    unattempted_target_count: int = 0
    deferred_retry_count: int = 0
    permanent_gap_count: int = 0
    drive_metrics: Mapping[str, Any] | None = None
    target_year: int | None = None

    def to_dict(self) -> dict[str, Any]:
        result = {
            "schema": ALL_ISSUER_CAMPAIGN_SCHEMA if self.universe_mode == "all_annual_issuers" else CAMPAIGN_SCHEMA,
            "shard": self.shard,
            "apply": self.apply,
            "status": self.status,
            "target_digest": self.target_digest,
            "outcome_count": len(self.outcomes),
            "outcomes": list(self.outcomes),
            "manifest_path": str(self.manifest_path) if self.manifest_path else None,
            "dart_calls_used": self.dart_calls_used,
            "dart_calls_budget": self.dart_calls_budget,
            "stop_reason": self.stop_reason,
            "unattempted_target_count": self.unattempted_target_count,
            "deferred_retry_count": self.deferred_retry_count,
            "permanent_gap_count": self.permanent_gap_count,
            "drive_metrics": dict(self.drive_metrics or _empty_drive_metrics()),
            "target_year": self.target_year,
        }
        if self.universe_mode != "listed":
            result.update({"universe_mode": self.universe_mode, **dict(self.campaign_counts or {})})
        return result


@dataclass(frozen=True)
class _FamilyResult:
    complete: bool
    outcomes: tuple[dict[str, Any], ...]
    stop: DartBoundedStop | DriveArchiveRateLimitError | DriveArchiveCommandError | DriveArchiveCommandTimeoutError | None = None
    audit_xml_members: tuple[tuple[str, bytes], ...] = ()
    raw_container: Mapping[str, Any] | None = None
    audit_xml_checked: bool = False


@dataclass(frozen=True)
class _TargetResult:
    outcomes: tuple[dict[str, Any], ...]
    stop: DartBoundedStop | DriveArchiveRateLimitError | DriveArchiveCommandError | DriveArchiveCommandTimeoutError | None = None


@dataclass
class _ResumeState:
    """Verified source checkpoints reconstructed from append-only outcomes."""

    completed_company_years: set[tuple[str, int]]
    complete_families: set[tuple[tuple[str, int], str]]
    complete_assets: dict[tuple[tuple[str, int], str, str, str], dict[str, Any]]
    containers: dict[tuple[tuple[str, int], str], dict[str, Any]]
    latest_company_year_terminal: dict[tuple[str, int], dict[str, Any]]


def build_source_archive_plan(
    session: Session,
    years: Iterable[int],
    shard_count: int = DEFAULT_SHARD_COUNT,
    universe_mode: str = "listed",
    excluded_pairs: frozenset[tuple[str, int]] = frozenset(),
) -> SourceArchivePlan:
    """Build a no-write listed-v2, all-annual-issuer-v3, or audit-report-only source plan."""
    _require_non_runtime_source_session(session)
    normalized_years = _normalize_years(years)
    _validate_shard_count(shard_count)
    _validate_universe_mode(universe_mode)
    memberships = _verified_memberships(session, normalized_years)
    membership_by_pair = {(row["corp_code"], row["bsns_year"]): row for row in memberships}
    disclosure_query = select(
        Disclosure.corp_code, Disclosure.rcept_no, Disclosure.disc_date, Disclosure.report_nm,
    ).order_by(
        Disclosure.corp_code, Disclosure.disc_date.desc(), Disclosure.rcept_no.desc(),
    )
    if universe_mode == "listed":
        disclosure_query = disclosure_query.where(
            Disclosure.corp_code.in_({corp for corp, _year in membership_by_pair})
        )
    disclosure_rows = session.execute(disclosure_query).mappings().all() if (
        membership_by_pair or universe_mode in ("all_annual_issuers", "audit_report_only")
    ) else []
    rows_by_company: dict[str, list[dict[str, Any]]] = {}
    for row in disclosure_rows:
        rows_by_company.setdefault(str(row["corp_code"]), []).append(dict(row))

    targets: list[SourceArchiveTarget] = []

    if universe_mode in ("listed", "all_annual_issuers"):
        for corp_code, year in sorted(membership_by_pair):
            membership = membership_by_pair[(corp_code, year)]
            anchor = latest_annual_filing_anchor_from_rows(
                rows_by_company.get(corp_code, ()), corp_code=corp_code, bsns_year=year
            )
            if anchor is None:
                target = SourceArchiveTarget(
                    corp_code=corp_code, bsns_year=year, market=membership["market"],
                    shard=_company_shard(corp_code, shard_count), source_receipt=None,
                    report_nm=None, source_uri=None, source_status="no_source_metadata",
                )
                if universe_mode == "all_annual_issuers":
                    target = replace(target, **_listed_membership_evidence(membership["market"]))
                targets.append(target)
                continue
            receipt = str(anchor["rcept_no"])
            target = SourceArchiveTarget(
                corp_code=corp_code, bsns_year=year, market=membership["market"],
                shard=_company_shard(corp_code, shard_count), source_receipt=receipt,
                report_nm=str(anchor["report_nm"]), source_uri=_document_source_uri(receipt),
                source_status="discovered",
            )
            if universe_mode == "all_annual_issuers":
                target = replace(target, **_listed_membership_evidence(membership["market"]))
            targets.append(target)

    if universe_mode in ("all_annual_issuers", "audit_report_only"):
        for corp_code in sorted(rows_by_company):
            for year in normalized_years:
                if (corp_code, year) in membership_by_pair:
                    continue
                if (corp_code, year) in excluded_pairs:
                    continue
                anchor = latest_annual_filing_anchor_from_rows(
                    rows_by_company[corp_code], corp_code=corp_code, bsns_year=year
                )
                if anchor is None:
                    continue
                receipt = str(anchor["rcept_no"])
                targets.append(SourceArchiveTarget(
                    corp_code=corp_code, bsns_year=year, market=None,
                    shard=_company_shard(corp_code, shard_count), source_receipt=receipt,
                    report_nm=str(anchor["report_nm"]), source_uri=_document_source_uri(receipt),
                    source_status="discovered",
                    universe_cohort=_OUTSIDE_VERIFIED_MARKETS[0],
                    historical_listing_status=_OUTSIDE_VERIFIED_MARKETS[1],
                    historical_listing_basis=_OUTSIDE_VERIFIED_MARKETS[2],
                ))

    if universe_mode == "audit_report_only":
        for corp_code in sorted(rows_by_company):
            for year in normalized_years:
                if (corp_code, year) in membership_by_pair:
                    continue
                if (corp_code, year) in excluded_pairs:
                    continue
                if latest_annual_filing_anchor_from_rows(
                    rows_by_company[corp_code], corp_code=corp_code, bsns_year=year
                ) is not None:
                    continue  # already produced by the business-report loop above
                audit_anchor = _latest_audit_report_anchor_from_rows(
                    rows_by_company[corp_code], bsns_year=year
                )
                if audit_anchor is None:
                    continue
                receipt = str(audit_anchor["rcept_no"])
                targets.append(SourceArchiveTarget(
                    corp_code=corp_code, bsns_year=year, market=None,
                    shard=_company_shard(corp_code, shard_count), source_receipt=receipt,
                    report_nm=str(audit_anchor["report_nm"]), source_uri=_document_source_uri(receipt),
                    source_status="discovered",
                    required_report_kinds=("audit_report",),
                    universe_cohort=_AUDIT_REPORT_ONLY[0],
                    historical_listing_status=_AUDIT_REPORT_ONLY[1],
                    historical_listing_basis=_AUDIT_REPORT_ONLY[2],
                ))

    frozen_targets = tuple(sorted(targets, key=lambda target: (target.corp_code, target.bsns_year)))
    return SourceArchivePlan(
        years=normalized_years, shard_count=shard_count, targets=frozen_targets,
        target_digest=_target_digest(normalized_years, shard_count, frozen_targets, universe_mode=universe_mode),
        universe_mode=universe_mode,
    )


def run_source_archive_shard(
    plan: SourceArchivePlan,
    shard: int,
    archive: ArchiveWriter | None,
    *,
    apply: bool,
    max_dart_calls: int | None = None,
    partial_retry_after_seconds: int = 0,
    now: datetime | None = None,
    target_year: int | None = None,
) -> SourceArchiveReport:
    """Run one frozen shard, requiring a finite DART call budget for apply."""
    _validate_shard(shard, plan.shard_count)
    selected = plan.targets_for_shard(shard)
    if target_year is not None:
        if target_year not in plan.years:
            raise SourceArchiveCampaignError(
                f"target_year must be one of the frozen plan years: {target_year}"
            )
        selected = tuple(target for target in selected if target.bsns_year == target_year)
    if not apply:
        return SourceArchiveReport(
            shard, False, "dry_run", plan.target_digest, (),
            universe_mode=plan.universe_mode, campaign_counts=plan.campaign_counts,
            drive_metrics=_empty_drive_metrics(),
            target_year=target_year,
        )
    if archive is None:
        raise SourceArchiveCampaignError("--apply requires a configured immutable Drive archive")
    if not isinstance(max_dart_calls, int) or max_dart_calls < 1:
        raise SourceArchiveCampaignError("--apply requires a finite positive max_dart_calls budget")
    if not isinstance(partial_retry_after_seconds, int) or partial_retry_after_seconds < 0:
        raise SourceArchiveCampaignError("partial_retry_after_seconds must be a non-negative integer")
    from kreports.runtime import require_collector_mode

    require_collector_mode("source archive campaign")
    state_dir = _required_state_dir(plan)
    shard_dir = state_dir / f"shard-{shard:02d}"
    shard_dir.mkdir(parents=True, exist_ok=True)
    outcomes_path = shard_dir / "outcomes.jsonl"
    marker_path = shard_dir / "COMMITTED.json"
    # The lock spans target freeze, pending event flush, DART reads, and Drive
    # writes.  A test double may omit it; the production DriveArchive always
    # provides this process-exclusive remote lease.
    lease_factory = getattr(archive, "writer_lease", None)
    lease = lease_factory() if callable(lease_factory) else nullcontext()
    with lease:
        try:
            drive_target_manifest = _write_frozen_target_manifest(plan, state_dir, archive)
        except DriveArchiveRateLimitError as exc:
            return _rate_limit_report(
                plan, shard, selected, outcomes_path, archive, exc,
                unattempted=len(selected), target_year=target_year,
            )
        except (DriveArchiveCommandError, DriveArchiveCommandTimeoutError) as exc:
            return _drive_transport_report(
                plan, shard, selected, outcomes_path, archive, exc,
                unattempted=len(selected), target_year=target_year,
            )
        # COMMITTED.json represents the complete all-year shard. Year-major
        # batches must never create or validate that marker prematurely.
        if target_year is None and marker_path.exists():
            _verify_committed_marker(marker_path, plan, shard, outcomes_path, selected)
        resume_state = _resume_state(outcomes_path, plan.target_digest)
        previous = resume_state.completed_company_years
        scheduled, deferred_retry_count, permanent_gap_count = _scheduled_targets(
            selected,
            resume_state,
            now=now or datetime.now(UTC),
            partial_retry_after=timedelta(seconds=partial_retry_after_seconds),
        )
        outcomes: list[dict[str, Any]] = []

        # A prior worker may have completed DART processing but stopped while
        # publishing the company-year event.  Flush that durable outbox before
        # making a new DART request; a failure leaves it on disk for resume.
        try:
            _flush_event_bundles(archive, shard_dir)
        except DriveArchiveRateLimitError as exc:
            return _rate_limit_report(
                plan, shard, selected, outcomes_path, archive, exc,
                unattempted=len(selected), outcomes=tuple(outcomes),
                target_year=target_year,
            )
        except (DriveArchiveCommandError, DriveArchiveCommandTimeoutError) as exc:
            return _drive_transport_report(
                plan, shard, selected, outcomes_path, archive, exc,
                unattempted=len(selected), outcomes=tuple(outcomes),
                target_year=target_year,
            )

        stop_reason: str | None = None
        unattempted_target_count = 0
        with request_budget(max_dart_calls) as budget:
            for index, target in enumerate(scheduled):
                if (target.corp_code, target.bsns_year) in previous:
                    target_result = _TargetResult((_outcome(target, "already_structurally_complete"),))
                else:
                    try:
                        target_result = _process_target(
                            target, archive, drive_target_manifest, resume_state
                        )
                    except DartBoundedStop as exc:  # defensive boundary for new fetchers
                        target_result = _TargetResult((_stop_outcome(target, exc),), exc)
                    except DriveArchiveRateLimitError as exc:
                        target_result = _TargetResult((_stop_outcome(target, exc),), exc)
                    except (DriveArchiveCommandError, DriveArchiveCommandTimeoutError) as exc:
                        target_result = _TargetResult((_stop_outcome(target, exc),), exc)

                persisted: list[dict[str, Any]] = []
                for outcome in target_result.outcomes:
                    persisted.append(_append_outcome(
                        outcomes_path,
                        {**outcome, "drive_target_manifest": drive_target_manifest},
                        plan.target_digest,
                    ))
                outcomes.extend(target_result.outcomes)
                bundle_path = _write_event_bundle(shard_dir, persisted, plan.target_digest)

                # If source archival itself hit the quota, do not immediately
                # spend another Drive request on the event.  The outbox and
                # non-terminal stop outcome are durable and resumed later.
                if isinstance(target_result.stop, (DriveArchiveRateLimitError, DriveArchiveCommandError, DriveArchiveCommandTimeoutError)):
                    stop_reason = _stop_reason(target_result.stop)
                    unattempted_target_count = len(scheduled) - index - 1
                    break
                try:
                    _flush_event_bundle(archive, bundle_path)
                except DriveArchiveRateLimitError as exc:
                    stop_row = _append_outcome(
                        outcomes_path,
                        {**_stop_outcome(target, exc), "drive_target_manifest": drive_target_manifest},
                        plan.target_digest,
                    )
                    outcomes.append(stop_row)
                    _write_event_bundle(shard_dir, [*persisted, stop_row], plan.target_digest)
                    stop_reason = "drive_quota_exhausted"
                    unattempted_target_count = len(scheduled) - index - 1
                    break
                except (DriveArchiveCommandError, DriveArchiveCommandTimeoutError) as exc:
                    stop_row = _append_outcome(
                        outcomes_path,
                        {**_stop_outcome(target, exc), "drive_target_manifest": drive_target_manifest},
                        plan.target_digest,
                    )
                    outcomes.append(stop_row)
                    _write_event_bundle(shard_dir, [*persisted, stop_row], plan.target_digest)
                    stop_reason = "drive_transport_failure"
                    unattempted_target_count = len(scheduled) - index - 1
                    break
                if target_result.stop is not None:
                    stop_reason = _stop_reason(target_result.stop)
                    unattempted_target_count = len(scheduled) - index - 1
                    break

        terminal = [
            row for row in outcomes
            if row.get("company_year_terminal", True)
            and row.get("report_kind", "company_year") == "company_year"
        ]
        completed_after_run = set(previous)
        for row in terminal:
            key = (str(row["corp_code"]), int(row["bsns_year"]))
            if row["status"] == "structurally_complete":
                completed_after_run.add(key)
            else:
                completed_after_run.discard(key)
        expected_company_years = {
            (target.corp_code, target.bsns_year) for target in selected
        }
        complete = (
            stop_reason is None
            and bool(selected)
            and completed_after_run == expected_company_years
        )
        if complete and target_year is None:
            _write_committed_marker(marker_path, plan, shard, outcomes_path, selected)
        elif target_year is None and marker_path.exists():
            raise SourceArchiveCampaignError("partial shard cannot retain a COMMITTED.json marker")
        return SourceArchiveReport(
            shard, True, "complete" if complete else "partial", plan.target_digest, tuple(outcomes),
            outcomes_path, budget.used_calls, budget.max_calls,
            plan.universe_mode, plan.campaign_counts, stop_reason, unattempted_target_count,
            deferred_retry_count, permanent_gap_count,
            _drive_metrics(archive, _pending_event_bundle_count(shard_dir)),
            target_year,
        )


def verify_source_archive_campaign(state_dir: Path, *, shard: int | None = None) -> dict[str, Any]:
    """Verify local cache integrity only; this performs no DART or Drive call."""
    root = Path(state_dir)
    manifest = _read_json(root / "TARGET.json")
    schema = manifest.get("schema")
    if schema == CAMPAIGN_SCHEMA:
        universe_mode = "listed"
    elif schema == ALL_ISSUER_CAMPAIGN_SCHEMA and manifest.get("universe_mode") == "all_annual_issuers":
        universe_mode = "all_annual_issuers"
    else:
        raise SourceArchiveCampaignError("TARGET.json schema is unsupported")
    _validate_drive_target_manifest_identity(manifest)
    shard_count = int(manifest["shard_count"])
    targets = tuple(_target_from_dict(value) for value in manifest.get("targets", ()))
    target_digest = str(manifest["target_digest"])
    if _target_digest(tuple(manifest["years"]), shard_count, targets, universe_mode=universe_mode) != target_digest:
        raise SourceArchiveCampaignError("TARGET.json target digest does not match its frozen target plan")
    requested = [shard] if shard is not None else list(range(shard_count))
    records: list[dict[str, Any]] = []
    for shard_number in requested:
        _validate_shard(shard_number, shard_count)
        directory = root / f"shard-{shard_number:02d}"
        outcomes = directory / "outcomes.jsonl"
        marker = directory / "COMMITTED.json"
        selected = tuple(target for target in targets if target.shard == shard_number)
        if marker.exists():
            plan = SourceArchivePlan(tuple(manifest["years"]), shard_count, targets, target_digest, root, universe_mode)
            _verify_committed_marker(marker, plan, shard_number, outcomes, selected)
        records.append({
            "shard": shard_number,
            "outcome_count": len(outcomes.read_text(encoding="utf-8").splitlines()) if outcomes.exists() else 0,
            "committed": marker.exists(),
        })
    result: dict[str, Any] = {
        "schema": schema,
        "target_digest": target_digest,
        "target_count": manifest["target_count"],
        "shards": records,
    }
    if universe_mode == "all_annual_issuers":
        result.update({"universe_mode": universe_mode, **_all_issuer_campaign_counts(targets)})
    return result


def _verified_memberships(session: Session, years: tuple[int, ...]) -> list[dict[str, Any]]:
    inspector = inspect(session.get_bind())
    table = CompanyYearListingMembership.__tablename__
    if table not in inspector.get_table_names():
        raise SourceArchiveCampaignError("historical listing membership evidence is unavailable: table missing")
    columns = {column["name"] for column in inspector.get_columns(table)}
    missing = sorted(_REQUIRED_MEMBERSHIP_COLUMNS - columns)
    if missing:
        raise SourceArchiveCampaignError("historical listing membership evidence is unavailable: missing " + ",".join(missing))
    rows = session.execute(select(
        CompanyYearListingMembership.corp_code, CompanyYearListingMembership.bsns_year,
        CompanyYearListingMembership.market, CompanyYearListingMembership.evidence_basis,
        CompanyYearListingMembership.manifest_checksum, CompanyYearListingMembership.manifest_storage_uri,
        CompanyYearListingMembership.normalized_checksum, CompanyYearListingMembership.normalized_storage_uri,
    ).where(
        CompanyYearListingMembership.bsns_year.in_(years),
        CompanyYearListingMembership.market.in_(("KOSPI", "KOSDAQ")),
        CompanyYearListingMembership.status == "verified",
    )).mappings().all()
    market_years = {(int(row["bsns_year"]), str(row["market"])) for row in rows}
    missing_market_years = [f"{year}:{market}" for year in years for market in ("KOSPI", "KOSDAQ") if (year, market) not in market_years]
    if missing_market_years:
        raise SourceArchiveCampaignError("historical listing membership evidence is unavailable: missing verified " + ",".join(missing_market_years))
    normalized: list[dict[str, Any]] = []
    for row in rows:
        value = dict(row)
        if not all(str(value[field] or "").strip() for field in (
            "evidence_basis", "manifest_checksum", "manifest_storage_uri", "normalized_checksum", "normalized_storage_uri",
        )):
            raise SourceArchiveCampaignError("historical listing membership evidence is invalid")
        normalized.append({"corp_code": str(value["corp_code"]), "bsns_year": int(value["bsns_year"]), "market": str(value["market"])})
    return normalized


def _listed_membership_evidence(market: str) -> dict[str, str]:
    if market == "KOSPI":
        cohort, _market, basis = _VERIFIED_KOSPI
    elif market == "KOSDAQ":
        cohort, _market, basis = _VERIFIED_KOSDAQ
    else:  # _verified_memberships() is the only caller and already filters this.
        raise SourceArchiveCampaignError("verified membership market is unsupported")
    return {
        "universe_cohort": cohort,
        "historical_listing_status": market,
        "historical_listing_basis": basis,
    }


def _latest_audit_report_anchor_from_rows(
    rows: Iterable[Mapping[str, Any]], *, bsns_year: int,
) -> dict[str, Any] | None:
    """Find the latest primary audit-report disclosure anchoring one business year.

    Mirrors ``latest_annual_filing_anchor_from_rows`` but matches on the
    audit-report predicate, for company-years that never file a business
    report at all (KONEX issuers below the annual-report threshold, and
    unlisted 외감 issuers).
    """
    candidates = [
        row for row in rows
        if audit_report_receipt_matches_business_year(
            row.get("report_nm"), row.get("disc_date"), bsns_year,
        )
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda row: (row["disc_date"], row["rcept_no"]), reverse=True)
    return candidates[0]


def _cohort_counts(targets: Iterable[SourceArchiveTarget]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for target in targets:
        if target.universe_cohort is None:
            raise SourceArchiveCampaignError("all-issuer target lacks a universe cohort")
        counts[target.universe_cohort] = counts.get(target.universe_cohort, 0) + 1
    return dict(sorted(counts.items()))


def _all_issuer_campaign_counts(targets: Iterable[SourceArchiveTarget]) -> dict[str, dict[str, int]]:
    """Count the frozen v3 targets rather than estimating operational coverage."""
    frozen_targets = tuple(targets)
    return {
        "cohort_counts": _cohort_counts(frozen_targets),
        "cohort_target_counts": _cohort_counts(frozen_targets),
        "cohort_discovered_counts": _cohort_counts(
            target for target in frozen_targets if target.source_status == "discovered"
        ),
        "cohort_gap_counts": _cohort_counts(
            target for target in frozen_targets if target.source_status != "discovered"
        ),
        "historical_status_counts": _historical_status_counts(frozen_targets),
    }


def _historical_status_counts(targets: Iterable[SourceArchiveTarget]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for target in targets:
        if target.historical_listing_status is None:
            raise SourceArchiveCampaignError("all-issuer target lacks a historical listing status")
        counts[target.historical_listing_status] = counts.get(target.historical_listing_status, 0) + 1
    return dict(sorted(counts.items()))


def _target_universe_fields(target: SourceArchiveTarget) -> dict[str, str]:
    if target.universe_cohort is None:
        return {}
    if target.historical_listing_status is None or target.historical_listing_basis is None:
        raise SourceArchiveCampaignError("all-issuer target lacks historical listing evidence")
    return {
        "universe_cohort": target.universe_cohort,
        "historical_listing_status": target.historical_listing_status,
        "historical_listing_basis": target.historical_listing_basis,
    }


def _process_target(
    target: SourceArchiveTarget,
    archive: ArchiveWriter,
    drive_target_manifest: Mapping[str, Any],
    resume_state: _ResumeState,
) -> _TargetResult:
    if target.source_status != "discovered" or not target.source_receipt or not target.source_uri:
        return _TargetResult((_outcome(target, target.source_status),))
    outcomes = [_outcome(target, "discovered", report_kind="company_year", company_year_terminal=False)]
    if "business_report" in target.required_report_kinds:
        business_result = _business_family(target, archive, drive_target_manifest, resume_state)
        outcomes.extend(business_result.outcomes)
        if business_result.stop is not None:
            return _TargetResult(tuple(outcomes), business_result.stop)
    else:
        business_result = _FamilyResult(True, ())
    audit_result = _audit_family(
        target,
        archive,
        drive_target_manifest,
        resume_state,
        business_xml_members=business_result.audit_xml_members,
        business_raw_container=business_result.raw_container,
        business_xml_checked=business_result.audit_xml_checked,
    )
    outcomes.extend(audit_result.outcomes)
    if audit_result.stop is not None:
        return _TargetResult(tuple(outcomes), audit_result.stop)
    outcomes.append(_outcome(
        target,
        "structurally_complete" if business_result.complete and audit_result.complete else "partial_source",
        report_kind="company_year",
        audit_xml_resolver_version=AUDIT_XML_RESOLVER_VERSION,
    ))
    return _TargetResult(tuple(outcomes))


def _business_family(
    target: SourceArchiveTarget,
    archive: ArchiveWriter,
    drive_target_manifest: Mapping[str, Any],
    resume_state: _ResumeState,
) -> _FamilyResult:
    family_key = _family_key(target, "business_report")
    if family_key in resume_state.complete_families:
        return _FamilyResult(True, (_family_reused_outcome(target, "business_report"),))
    try:
        assets = fetch_document_zip_asset_bytes(target.source_receipt or "")
    except DartBoundedStop as exc:
        return _FamilyResult(False, (_stop_outcome(target, exc, report_kind="business_report", error="document_xml"),), exc)
    except Exception as exc:
        return _FamilyResult(False, (_outcome(target, "fetch_failed", report_kind="business_report", error=_bounded_error(exc)),))
    if not assets:
        return _FamilyResult(False, (_outcome(target, "partial_source", report_kind="business_report", error="document_xml_empty"),))
    container_bytes = getattr(assets, "container_bytes", None)
    if not isinstance(container_bytes, bytes) or not container_bytes:
        return _FamilyResult(False, (_outcome(
            target, "partial_source", report_kind="business_report",
            error="document_zip_container_missing",
        ),))
    container_is_zip = getattr(assets, "is_zip", None)
    container_content_type = getattr(assets, "container_content_type", None)
    if not isinstance(container_is_zip, bool) or not isinstance(container_content_type, str):
        return _FamilyResult(False, (_outcome(
            target, "partial_source", report_kind="business_report",
            error="document_container_media_metadata_missing",
        ),))
    container_extension = "zip" if container_is_zip else _container_extension(container_content_type)
    container_version = (
        "raw-document-zip-container-v1"
        if container_is_zip
        else "raw-document-response-container-v1"
    )
    container_sha256 = hashlib.sha256(container_bytes).hexdigest()
    cached_container = resume_state.containers.get(family_key)
    if _container_identity_matches(
        cached_container,
        sha256=container_sha256,
        byte_length=len(container_bytes),
        content_type=container_content_type,
        is_zip=container_is_zip,
    ):
        container = dict(cached_container)
    else:
        try:
            container_object = archive.archive_bytes(
                data=container_bytes,
                extension=container_extension,
                metadata={
                    "source_receipt": target.source_receipt or "",
                    "source_uri": target.source_uri or "",
                    "archive_version": container_version,
                    "corp_code": target.corp_code,
                    "bsns_year": str(target.bsns_year),
                    "report_kind": "business_report",
                    "container_content_type": container_content_type,
                    "container_is_zip": str(container_is_zip).lower(),
                },
            )
        except (DriveArchiveRateLimitError, DriveArchiveCommandError, DriveArchiveCommandTimeoutError):
            raise
        except Exception as exc:
            return _FamilyResult(False, (_outcome(
                target, "asset_failed", report_kind="business_report",
                error=_bounded_error(exc),
            ),))
        container = {
            **_object_summary(container_object),
            "content_type": container_content_type,
            "is_zip": container_is_zip,
        }
    if (
        not container.get("storage_uri")
        or container.get("sha256") != container_sha256
        or container.get("byte_length") != len(container_bytes)
    ):
        return _FamilyResult(False, (_outcome(
            target, "asset_failed", report_kind="business_report",
            error="document_zip_container_archive_identity_invalid",
        ),))
    audit_xml_members = _audit_xml_members(assets)
    audit_xml_names = {filename for filename, _raw in audit_xml_members}
    outcomes: list[dict[str, Any]] = []
    success = True
    for filename, raw in sorted(assets.items()):
        if filename in audit_xml_names:
            continue
        cached_asset = resume_state.complete_assets.get(
            _asset_key(target, "business_report", target.source_receipt or "", filename)
        )
        if cached_asset is not None:
            outcomes.append(_asset_reused_outcome(
                target, "business_report", filename, cached_asset
            ))
            success = success and cached_asset.get("structural_status") == "complete"
            continue
        complete, asset_outcomes = _archive_asset(
            target, archive, report_kind="business_report", source_locator=filename,
            filename=filename, content_type="xml", raw_bytes=raw,
            drive_target_manifest=drive_target_manifest,
            raw_container=container, container_member_name=filename,
        )
        success = success and complete
        outcomes.extend(asset_outcomes)
    if success:
        outcomes.append(_family_complete_outcome(
            target, "business_report", assets=tuple(sorted(assets)), container=container
        ))
    return _FamilyResult(
        success,
        tuple(outcomes),
        audit_xml_members=audit_xml_members,
        raw_container=container,
        audit_xml_checked=True,
    )


def _audit_family(
    target: SourceArchiveTarget,
    archive: ArchiveWriter,
    drive_target_manifest: Mapping[str, Any],
    resume_state: _ResumeState,
    *,
    business_xml_members: tuple[tuple[str, bytes], ...] = (),
    business_raw_container: Mapping[str, Any] | None = None,
    business_xml_checked: bool = False,
) -> _FamilyResult:
    family_key = _family_key(target, "audit_report")
    if family_key in resume_state.complete_families:
        return _FamilyResult(True, (_family_reused_outcome(target, "audit_report"),))
    if business_xml_members:
        return _archive_audit_xml_members(
            target,
            archive,
            drive_target_manifest,
            resume_state,
            business_xml_members,
            business_raw_container,
        )
    if not business_xml_checked:
        try:
            business_assets = fetch_document_zip_asset_bytes(target.source_receipt or "")
        except DartBoundedStop as exc:
            return _FamilyResult(False, (_stop_outcome(
                target, exc, report_kind="audit_report", error="embedded_audit_document_xml",
            ),), exc)
        except Exception as exc:
            return _FamilyResult(False, (_outcome(
                target, "fetch_failed", report_kind="audit_report", error=_bounded_error(exc),
            ),))
        embedded_members = _audit_xml_members(business_assets)
        if embedded_members:
            container = _archive_audit_xml_container(
                target, archive, target.source_receipt or "", business_assets,
            )
            if container is None:
                return _FamilyResult(False, (_outcome(
                    target, "asset_failed", report_kind="audit_report",
                    error="embedded_audit_document_container_archive_failed",
                ),))
            return _archive_audit_xml_members(
                target, archive, drive_target_manifest, resume_state, embedded_members, container,
            )
    try:
        receipts = _separate_audit_receipts(target)
    except DartBoundedStop as exc:
        return _FamilyResult(False, (_stop_outcome(
            target, exc, report_kind="audit_report", error="audit_receipt_discovery",
        ),), exc)
    for receipt in receipts:
        try:
            assets = fetch_document_zip_asset_bytes(receipt)
        except DartBoundedStop as exc:
            return _FamilyResult(False, (_stop_outcome(
                target, exc, report_kind="audit_report", error="audit_document_xml",
            ),), exc)
        except Exception as exc:
            return _FamilyResult(False, (_outcome(
                target, "fetch_failed", report_kind="audit_report", error=_bounded_error(exc),
            ),))
        members = _audit_xml_members(assets)
        # The receipt itself was classified as a primary audit report.  Preserve
        # every XML member of that package even when a member title is generic.
        if not members:
            members = tuple(sorted(assets.items()))
        if not members:
            continue
        container = _archive_audit_xml_container(target, archive, receipt, assets)
        if container is None:
            return _FamilyResult(False, (_outcome(
                target, "asset_failed", report_kind="audit_report", error="audit_document_container_archive_failed",
            ),))
        return _archive_audit_xml_members(
            target, archive, drive_target_manifest, resume_state, members, container,
            source_receipt=receipt,
        )
    return _FamilyResult(False, (_outcome(
        target, "partial_source", report_kind="audit_report", error="audit_xml_unavailable",
    ),))


def _archive_audit_xml_members(
    target: SourceArchiveTarget,
    archive: ArchiveWriter,
    drive_target_manifest: Mapping[str, Any],
    resume_state: _ResumeState,
    members: tuple[tuple[str, bytes], ...],
    raw_container: Mapping[str, Any] | None,
    *,
    source_receipt: str | None = None,
) -> _FamilyResult:
    """Archive audit XML embedded in the business filing under audit provenance."""
    outcomes: list[dict[str, Any]] = []
    success = True
    receipt = source_receipt or target.source_receipt or ""
    for filename, raw in members:
        cached_asset = resume_state.complete_assets.get(
            _asset_key(target, "audit_report", receipt, filename)
        )
        if cached_asset is not None:
            outcomes.append(_asset_reused_outcome(target, "audit_report", filename, cached_asset))
            success = success and cached_asset.get("structural_status") == "complete"
            continue
        complete, asset_outcomes = _archive_asset(
            target,
            archive,
            report_kind="audit_report",
            source_locator=filename,
            filename=filename,
            content_type="xml",
            raw_bytes=raw,
            source_receipt=receipt,
            drive_target_manifest=drive_target_manifest,
            raw_container=raw_container,
            container_member_name=filename,
        )
        success = success and complete
        outcomes.extend(asset_outcomes)
    if success:
        outcomes.append(_family_complete_outcome(
            target,
            "audit_report",
            assets=tuple(filename for filename, _raw in members),
        ))
    return _FamilyResult(success, tuple(outcomes))


def _separate_audit_receipts(target: SourceArchiveTarget) -> tuple[str, ...]:
    """Find primary audit-report submissions for the target fiscal year."""
    rows = fetch_disclosure_list(
        target.corp_code,
        f"{target.bsns_year + 1:04d}0101",
        f"{target.bsns_year + 1:04d}1231",
    )
    receipts: list[str] = []
    for row in rows:
        receipt = str(row.get("rcept_no") or "")
        receipt_date = str(row.get("rcept_dt") or receipt[:8])
        if not receipt or not audit_report_receipt_matches_business_year(
            row.get("report_nm"), receipt_date, target.bsns_year,
        ):
            continue
        receipts.append(receipt)
    return tuple(sorted(set(receipts)))


def _archive_audit_xml_container(
    target: SourceArchiveTarget,
    archive: ArchiveWriter,
    receipt: str,
    assets: Mapping[str, bytes],
) -> Mapping[str, Any] | None:
    container_bytes = getattr(assets, "container_bytes", None)
    container_content_type = getattr(assets, "container_content_type", None)
    container_is_zip = getattr(assets, "is_zip", None)
    if (
        not isinstance(container_bytes, bytes) or not container_bytes
        or not isinstance(container_content_type, str) or not isinstance(container_is_zip, bool)
    ):
        return None
    try:
        object_value = archive.archive_bytes(
            data=container_bytes,
            extension="zip" if container_is_zip else _container_extension(container_content_type),
            metadata={
                "source_receipt": receipt,
                "source_uri": _document_source_uri(receipt),
                "archive_version": (
                    "raw-document-zip-container-v1" if container_is_zip
                    else "raw-document-response-container-v1"
                ),
                "corp_code": target.corp_code,
                "bsns_year": str(target.bsns_year),
                "report_kind": "audit_report",
                "container_content_type": container_content_type,
                "container_is_zip": str(container_is_zip).lower(),
            },
        )
    except (DriveArchiveRateLimitError, DriveArchiveCommandError, DriveArchiveCommandTimeoutError):
        raise
    except Exception:
        return None
    container = {
        **_object_summary(object_value),
        "content_type": container_content_type,
        "is_zip": container_is_zip,
    }
    if (
        not container.get("storage_uri")
        or container.get("sha256") != hashlib.sha256(container_bytes).hexdigest()
        or container.get("byte_length") != len(container_bytes)
    ):
        return None
    return container


def _audit_xml_members(assets: Mapping[str, bytes]) -> tuple[tuple[str, bytes], ...]:
    """Return genuine audit-package XML members from a DART document response."""
    members: list[tuple[str, bytes]] = []
    for filename, raw in sorted(assets.items()):
        try:
            content = _decode_for_parser(raw)
        except UnicodeDecodeError:
            continue
        title = _xml_document_title(content) or filename
        compact_title = "".join(title.split())
        if "감사보고서" not in compact_title:
            continue
        if any(fragment in compact_title for fragment in (
            "내부회계", "감사의감사보고서", "내부감시장치",
        )):
            continue
        members.append((filename, raw))
    return tuple(members)


def _xml_document_title(content: str) -> str:
    for marker in ("DOCUMENT-NAME", "TITLE"):
        start = content.find(f"<{marker}")
        if start < 0:
            continue
        start = content.find(">", start)
        end = content.find(f"</{marker}>", start + 1)
        if start >= 0 and end >= 0:
            return " ".join(content[start + 1:end].split())
    return ""


def _archive_asset(
    target: SourceArchiveTarget,
    archive: ArchiveWriter,
    *,
    report_kind: str,
    source_locator: str,
    filename: str,
    content_type: str,
    raw_bytes: bytes,
    source_receipt: str | None = None,
    drive_target_manifest: Mapping[str, Any],
    raw_container: Mapping[str, Any] | None = None,
    container_member_name: str | None = None,
) -> tuple[bool, list[dict[str, Any]]]:
    receipt = source_receipt or target.source_receipt
    if not isinstance(raw_bytes, bytes) or not raw_bytes:
        return False, [_outcome(target, "asset_empty", report_kind=report_kind, source_locator=source_locator)]
    raw_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    source_uri = _asset_source_uri(receipt or "", source_locator, content_type)
    metadata = {
        "source_receipt": receipt or "", "source_uri": source_uri, "archive_version": "raw-source-v1",
        "corp_code": target.corp_code, "bsns_year": str(target.bsns_year), "report_kind": report_kind,
        "source_locator": source_locator,
    }
    if raw_container is not None:
        metadata.update({
            "container_storage_uri": str(raw_container.get("storage_uri") or ""),
            "container_sha256": str(raw_container.get("sha256") or ""),
            "container_member_name": container_member_name or filename,
            "container_content_type": str(raw_container.get("content_type") or ""),
            "container_is_zip": str(bool(raw_container.get("is_zip"))).lower(),
        })
    try:
        raw_object = archive.archive_bytes(data=raw_bytes, extension=_extension(content_type), metadata=metadata)
        outcomes = [_outcome(
            target, "archived", report_kind=report_kind, source_locator=source_locator,
            filename=filename, content_type=content_type, source_receipt=receipt,
            raw_object=_object_summary(raw_object), raw_sha256=raw_sha256, company_year_terminal=False,
            source_uri=source_uri,
            raw_container=dict(raw_container) if raw_container is not None else None,
            container_member_name=container_member_name,
        )]
        parsed = parse_document_structure(
            raw_bytes, content_type=content_type, source_sha256=raw_sha256,
            source_receipt=receipt, source_uri=source_uri,
        )
        parsed_object = archive_structured_document(  # type: ignore[arg-type]
            archive,
            parsed,
            archive_metadata={
                "corp_code": target.corp_code,
                "bsns_year": str(target.bsns_year),
                "report_kind": report_kind,
            },
        )
        outcomes.append(_outcome(
            target, "generically_parsed", report_kind=report_kind, source_locator=source_locator,
            filename=filename, content_type=content_type, source_receipt=receipt,
            structural_status=parsed.structural_status, parsed_object=_object_summary(parsed_object),
            raw_sha256=raw_sha256,
            parser_version=PARSER_VERSION, company_year_terminal=False,
            source_uri=source_uri,
            raw_container=dict(raw_container) if raw_container is not None else None,
            container_member_name=container_member_name,
        ))
        document = {
            "schema": "source-archive-document-manifest.v1", "corp_code": target.corp_code,
            "bsns_year": target.bsns_year, "market": target.market, "report_kind": report_kind,
            **_target_universe_fields(target),
            "source_receipt": receipt, "source_uri": source_uri, "source_locator": source_locator,
            "filename": filename, "content_type": content_type, "raw": _object_summary(raw_object),
            "raw_container": dict(raw_container) if raw_container is not None else None,
            "container_member_name": container_member_name,
            "parse": {**_object_summary(parsed_object), "parser_version": PARSER_VERSION, "structural_status": parsed.structural_status},
            "drive_target_manifest": dict(drive_target_manifest),
            "status": "structurally_complete" if parsed.structural_status == "complete" else "requires_review",
        }
        manifest = archive.archive_bytes(
            data=_canonical_json(document), extension="json",
            metadata={
                "source_receipt": receipt or "",
                "source_uri": source_uri,
                "archive_version": "source-archive-document-manifest-v1",
                "corp_code": target.corp_code,
                "bsns_year": str(target.bsns_year),
                "report_kind": report_kind,
            },
        )
        outcomes[-1]["document_manifest"] = _object_summary(manifest)
        return parsed.structural_status == "complete", outcomes
    except (DriveArchiveRateLimitError, DriveArchiveCommandError, DriveArchiveCommandTimeoutError):
        raise
    except Exception as exc:
        return False, [_outcome(target, "asset_failed", report_kind=report_kind, source_locator=source_locator, error=_bounded_error(exc))]


def _archive_campaign_event(archive: ArchiveWriter, row: Mapping[str, Any]) -> None:
    receipt = str(row.get("source_receipt") or "campaign")
    uri = str(row.get("source_uri") or f"campaign://{row['target_digest']}/{row['shard']}")
    outcomes = row.get("outcomes")
    if not isinstance(outcomes, list):
        outcomes = [dict(row)]
    payload = {"schema": "source-archive-campaign-manifest.v1", **dict(row), "outcomes": outcomes}
    archive.archive_bytes(
        data=_canonical_json(payload), extension="json",
        metadata={"source_receipt": receipt, "source_uri": uri, "archive_version": "source-archive-campaign-manifest-v1"},
    )


def _event_bundle_path(shard_dir: Path, row: Mapping[str, Any]) -> Path:
    identity = f"{row.get('target_digest', '')}:{row.get('corp_code', '')}:{row.get('bsns_year', '')}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return shard_dir / "outbox" / f"{digest}.json"


def _write_event_bundle(
    shard_dir: Path,
    rows: Iterable[Mapping[str, Any]],
    target_digest: str,
) -> Path:
    materialized = [dict(row) for row in rows]
    if not materialized:
        raise SourceArchiveCampaignError("cannot write an empty source archive event bundle")
    first = materialized[0]
    path = _event_bundle_path(shard_dir, {**first, "target_digest": target_digest})
    payload = {
        "schema": "source-archive-event-bundle.v1",
        "target_digest": target_digest,
        "corp_code": first.get("corp_code"),
        "bsns_year": first.get("bsns_year"),
        "shard": first.get("shard"),
        "source_receipt": first.get("source_receipt"),
        "source_uri": first.get("source_uri"),
        "drive_target_manifest": first.get("drive_target_manifest"),
        **_target_universe_fields_from_row(first),
        "outcomes": materialized,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, _canonical_json(payload))
    return path


def _flush_event_bundle(archive: ArchiveWriter, path: Path) -> None:
    if not path.exists():
        return
    payload = _read_json(path)
    _archive_campaign_event(archive, payload)
    # The local event is removed only after archive_bytes returns, which means
    # DriveArchive has completed upload and raw-byte readback verification.
    path.unlink()


def _flush_event_bundles(archive: ArchiveWriter, shard_dir: Path) -> None:
    outbox = shard_dir / "outbox"
    if not outbox.exists():
        return
    for path in sorted(outbox.glob("*.json")):
        _flush_event_bundle(archive, path)


def _pending_event_bundle_count(shard_dir: Path) -> int:
    outbox = shard_dir / "outbox"
    return len(tuple(outbox.glob("*.json"))) if outbox.exists() else 0


def _target_universe_fields_from_row(row: Mapping[str, Any]) -> dict[str, str]:
    fields = {}
    for key in ("universe_cohort", "historical_listing_status", "historical_listing_basis"):
        value = row.get(key)
        if value is not None:
            fields[key] = str(value)
    return fields


def _empty_drive_metrics() -> dict[str, Any]:
    return DriveArchiveMetrics().to_dict()


def _drive_metrics(archive: ArchiveWriter, pending: int) -> dict[str, Any]:
    metrics = getattr(archive, "metrics", None)
    if isinstance(metrics, DriveArchiveMetrics):
        result = metrics.to_dict()
    elif hasattr(metrics, "to_dict"):
        result = dict(metrics.to_dict())
    else:
        result = _empty_drive_metrics()
    result["pending_event_bundles"] = pending
    return result


def _rate_limit_report(
    plan: SourceArchivePlan,
    shard: int,
    selected: tuple[SourceArchiveTarget, ...],
    outcomes_path: Path,
    archive: ArchiveWriter,
    exc: DriveArchiveRateLimitError,
    *,
    unattempted: int,
    outcomes: tuple[dict[str, Any], ...] = (),
    target_year: int | None = None,
) -> SourceArchiveReport:
    return SourceArchiveReport(
        shard=shard,
        apply=True,
        status="partial",
        target_digest=plan.target_digest,
        outcomes=outcomes,
        manifest_path=outcomes_path,
        dart_calls_used=0,
        dart_calls_budget=None,
        universe_mode=plan.universe_mode,
        campaign_counts=plan.campaign_counts,
        stop_reason="drive_quota_exhausted",
        unattempted_target_count=unattempted,
        drive_metrics=_drive_metrics(archive, _pending_event_bundle_count(outcomes_path.parent)),
        target_year=target_year,
    )


def _drive_transport_report(
    plan: SourceArchivePlan,
    shard: int,
    selected: tuple[SourceArchiveTarget, ...],
    outcomes_path: Path,
    archive: ArchiveWriter,
    exc: DriveArchiveCommandError | DriveArchiveCommandTimeoutError,
    *,
    unattempted: int,
    outcomes: tuple[dict[str, Any], ...] = (),
    target_year: int | None = None,
) -> SourceArchiveReport:
    """Keep a transient Drive readback stall resumable without exiting the CLI."""
    del exc
    return SourceArchiveReport(
        shard=shard,
        apply=True,
        status="partial",
        target_digest=plan.target_digest,
        outcomes=outcomes,
        manifest_path=outcomes_path,
        dart_calls_used=0,
        dart_calls_budget=None,
        universe_mode=plan.universe_mode,
        campaign_counts=plan.campaign_counts,
        stop_reason="drive_transport_failure",
        unattempted_target_count=unattempted,
        drive_metrics=_drive_metrics(archive, _pending_event_bundle_count(outcomes_path.parent)),
        target_year=target_year,
    )


def _outcome(target: SourceArchiveTarget, status: str, *, report_kind: str = "company_year", source_locator: str | None = None, error: str | None = None, company_year_terminal: bool = True, **extra: Any) -> dict[str, Any]:
    return {
        "schema": ALL_ISSUER_CAMPAIGN_SCHEMA if target.universe_cohort is not None else CAMPAIGN_SCHEMA,
        "recorded_at": datetime.now(UTC).isoformat(),
        "corp_code": target.corp_code, "bsns_year": target.bsns_year, "market": target.market,
        **_target_universe_fields(target),
        "shard": target.shard, "source_receipt": target.source_receipt, "source_uri": target.source_uri,
        "report_kind": report_kind, "source_locator": source_locator, "status": status,
        "error": error, "company_year_terminal": company_year_terminal, **extra,
    }


def _family_key(target: SourceArchiveTarget, report_kind: str) -> tuple[tuple[str, int], str]:
    return ((target.corp_code, target.bsns_year), report_kind)


def _asset_key(
    target: SourceArchiveTarget,
    report_kind: str,
    source_receipt: str,
    source_locator: str,
) -> tuple[tuple[str, int], str, str, str]:
    return ((target.corp_code, target.bsns_year), report_kind, source_receipt, source_locator)


def _family_complete_outcome(
    target: SourceArchiveTarget,
    report_kind: str,
    *,
    assets: tuple[str, ...],
    container: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    extra: dict[str, Any] = {
        "source_family": report_kind,
        "asset_count": len(assets),
        "asset_locators": list(assets),
    }
    if container is not None:
        extra["raw_container"] = dict(container)
    return _outcome(
        target,
        "family_complete",
        report_kind=report_kind,
        company_year_terminal=False,
        **extra,
    )


def _family_reused_outcome(
    target: SourceArchiveTarget,
    report_kind: str,
) -> dict[str, Any]:
    return _outcome(
        target,
        "family_reused",
        report_kind=report_kind,
        company_year_terminal=False,
        source_family=report_kind,
    )


def _asset_reused_outcome(
    target: SourceArchiveTarget,
    report_kind: str,
    source_locator: str,
    cached: Mapping[str, Any],
) -> dict[str, Any]:
    """Expose a local resume decision without re-archiving the asset."""
    result: dict[str, Any] = {
        "source_uri": cached.get("source_uri"),
        "source_receipt": cached.get("source_receipt") or target.source_receipt,
        "filename": cached.get("filename"),
        "content_type": cached.get("content_type"),
        "raw_sha256": cached.get("raw_sha256"),
        "raw_object": cached.get("raw_object"),
        "parsed_object": cached.get("parsed_object"),
        "document_manifest": cached.get("document_manifest"),
        "structural_status": cached.get("structural_status"),
        "reused": True,
    }
    return _outcome(
        target,
        "asset_reused",
        report_kind=report_kind,
        source_locator=source_locator,
        company_year_terminal=False,
        **result,
    )


def _stop_reason(exc: DartBoundedStop | DriveArchiveRateLimitError | DriveArchiveCommandError | DriveArchiveCommandTimeoutError) -> str:
    if isinstance(exc, DriveArchiveRateLimitError):
        return "drive_quota_exhausted"
    if isinstance(exc, (DriveArchiveCommandError, DriveArchiveCommandTimeoutError)):
        return "drive_transport_failure"
    if isinstance(exc, DartRequestBudgetExceeded):
        return "api_budget_exhausted"
    if isinstance(exc, DartApiAuthError):
        return "dart_auth_failure"
    if isinstance(exc, DartApiLimitExceeded):
        return "dart_quota_failure"
    if isinstance(exc, DartTransportError):
        return "dart_transport_failure"
    return "collector_bounded_stop"


def _stop_status(exc: DartBoundedStop | DriveArchiveRateLimitError | DriveArchiveCommandError | DriveArchiveCommandTimeoutError) -> str:
    return {
        "drive_quota_exhausted": "drive_quota_exhausted",
        "drive_transport_failure": "drive_transport_failure",
        "api_budget_exhausted": "dart_budget_exhausted",
        "dart_auth_failure": "dart_auth_failure",
        "dart_quota_failure": "dart_quota_failure",
        "dart_transport_failure": "dart_transport_failure",
        "collector_bounded_stop": "collector_bounded_stop",
    }[_stop_reason(exc)]


def _stop_outcome(
    target: SourceArchiveTarget,
    exc: DartBoundedStop | DriveArchiveRateLimitError | DriveArchiveCommandError | DriveArchiveCommandTimeoutError,
    *,
    report_kind: str = "company_year",
    source_locator: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    return _outcome(
        target,
        _stop_status(exc),
        report_kind=report_kind,
        source_locator=source_locator,
        error=error or _bounded_error(exc),
        company_year_terminal=False,
        stop_reason=_stop_reason(exc),
    )


def _object_identity(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    storage_uri = value.get("storage_uri")
    sha256 = value.get("sha256")
    byte_length = value.get("byte_length")
    if (
        not isinstance(storage_uri, str) or not storage_uri
        or not isinstance(sha256, str) or len(sha256) != 64
        or not isinstance(byte_length, int) or byte_length < 1
    ):
        return None
    return {
        "storage_uri": storage_uri,
        "sha256": sha256,
        "byte_length": byte_length,
    }


def _container_identity_matches(
    container: Mapping[str, Any] | None,
    *,
    sha256: str,
    byte_length: int,
    content_type: str,
    is_zip: bool,
) -> bool:
    if container is None:
        return False
    identity = _object_identity(container)
    return bool(
        identity
        and identity["sha256"] == sha256
        and identity["byte_length"] == byte_length
        and container.get("content_type") == content_type
        and container.get("is_zip") is is_zip
    )


def _verified_asset_record(parts: Mapping[str, Any]) -> dict[str, Any] | None:
    if parts.get("status") == "asset_reused":
        row = parts.get("reused")
        if not isinstance(row, Mapping):
            return None
        if not _object_identity(row.get("raw_object")) or not _object_identity(row.get("parsed_object")):
            return None
        if not isinstance(row.get("document_manifest"), Mapping):
            return None
        return dict(row)

    archived = parts.get("archived")
    parsed = parts.get("parsed")
    if not isinstance(archived, Mapping) or not isinstance(parsed, Mapping):
        return None
    if not _object_identity(archived.get("raw_object")) or not _object_identity(parsed.get("parsed_object")):
        return None
    if not isinstance(parsed.get("document_manifest"), Mapping):
        return None
    result = {**dict(archived), **dict(parsed)}
    result["raw_object"] = dict(archived["raw_object"])
    result["parsed_object"] = dict(parsed["parsed_object"])
    result["raw_sha256"] = parsed.get("raw_sha256") or archived.get("raw_sha256") or archived["raw_object"].get("sha256")
    return result


def _resume_state(path: Path, target_digest: str) -> _ResumeState:
    """Rebuild resumable source/family/asset state from append-only outcomes."""
    if not path.exists():
        return _ResumeState(set(), set(), {}, {}, {})
    rows = _outcome_rows(path)
    completed_company_years: set[tuple[str, int]] = set()
    complete_families: set[tuple[tuple[str, int], str]] = set()
    asset_parts: dict[tuple[tuple[str, int], str, str, str], dict[str, Any]] = {}
    containers: dict[tuple[tuple[str, int], str], dict[str, Any]] = {}
    latest_company_year_terminal: dict[tuple[str, int], dict[str, Any]] = {}

    for row in rows:
        if row.get("target_digest") != target_digest:
            raise SourceArchiveCampaignError("shard outcomes belong to a different frozen target plan")
        target_key = (str(row.get("corp_code")), int(row.get("bsns_year")))
        status = row.get("status")
        report_kind = str(row.get("report_kind") or "")
        if row.get("company_year_terminal", True) and report_kind in {"", "company_year"}:
            latest_company_year_terminal[target_key] = dict(row)
        if status == "structurally_complete":
            completed_company_years.add(target_key)
        if report_kind in {"business_report", "audit_report"}:
            family_key = (target_key, report_kind)
            if status in {"family_complete", "family_reused"}:
                complete_families.add(family_key)
            if report_kind == "business_report" and isinstance(row.get("raw_container"), Mapping):
                container = row["raw_container"]
                if _object_identity(container):
                    containers[family_key] = dict(container)
            source_locator = row.get("source_locator")
            source_receipt = row.get("source_receipt")
            if isinstance(source_locator, str) and source_locator and isinstance(source_receipt, str):
                key = (target_key, report_kind, source_receipt, source_locator)
                parts = asset_parts.setdefault(key, {})
                if status in {"archived", "archived_verified"}:
                    parts.update({"archived": dict(row), "status": status})
                elif status == "generically_parsed":
                    parts.update({"parsed": dict(row), "status": status})
                elif status == "asset_reused":
                    parts.update({"reused": dict(row), "status": status})
                elif status in {
                    "asset_failed", "asset_empty", "partial_source", "requires_review",
                    "asset_requires_review",
                }:
                    asset_parts.pop(key, None)

    complete_assets: dict[tuple[tuple[str, int], str, str, str], dict[str, Any]] = {}
    for key, parts in asset_parts.items():
        verified = _verified_asset_record(parts)
        if verified is not None:
            complete_assets[key] = verified
    return _ResumeState(
        completed_company_years,
        complete_families,
        complete_assets,
        containers,
        latest_company_year_terminal,
    )


def _scheduled_targets(
    selected: tuple[SourceArchiveTarget, ...],
    resume_state: _ResumeState,
    *,
    now: datetime,
    partial_retry_after: timedelta,
) -> tuple[tuple[SourceArchiveTarget, ...], int, int]:
    """Prioritize untouched targets and defer recent terminal partials.

    Non-terminal budget and transport stops intentionally do not enter the
    terminal map, so the interrupted company-year is immediately resumable.
    Frozen targets without source metadata are permanent gaps for this plan;
    rebuilding a newer plan is the only way to supply a new receipt.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    untouched: list[SourceArchiveTarget] = []
    due_partial: list[tuple[datetime, SourceArchiveTarget]] = []
    deferred = 0
    permanent_gaps = 0
    for target in selected:
        key = (target.corp_code, target.bsns_year)
        if key in resume_state.completed_company_years:
            continue
        terminal = resume_state.latest_company_year_terminal.get(key)
        if terminal is None:
            untouched.append(target)
            continue
        if terminal.get("status") != "partial_source":
            permanent_gaps += 1
            continue
        recorded_at = _parse_recorded_at(terminal.get("recorded_at"))
        resolver_version = terminal.get("audit_xml_resolver_version")
        upgraded_partial = not isinstance(resolver_version, int) or resolver_version < AUDIT_XML_RESOLVER_VERSION
        if upgraded_partial or recorded_at is None or now - recorded_at >= partial_retry_after:
            due_partial.append((recorded_at or datetime.min.replace(tzinfo=UTC), target))
        else:
            deferred += 1
    due_partial.sort(key=lambda item: (item[0], item[1].corp_code, item[1].bsns_year))
    return tuple([*untouched, *(target for _at, target in due_partial)]), deferred, permanent_gaps


def _parse_recorded_at(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def write_source_archive_plan_preview(plan: SourceArchivePlan, state_dir: Path) -> Path:
    """Persist a no-network planning preview; apply freezes the Drive-backed target."""
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "TARGET.preview.json"
    payload = _canonical_json(plan.target_manifest)
    if path.exists() and path.read_bytes() != payload:
        raise SourceArchiveCampaignError("campaign target preview conflicts with the supplied frozen target plan")
    if not path.exists():
        _atomic_write(path, payload)
    return path


def _write_frozen_target_manifest(
    plan: SourceArchivePlan,
    state_dir: Path,
    archive: ArchiveWriter,
) -> dict[str, Any]:
    """Archive the full denominator before any DART request and bind local state to it."""
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "TARGET.json"
    canonical = plan.target_manifest
    payload = _canonical_json(canonical)
    expected_sha256 = hashlib.sha256(payload).hexdigest()
    existing: dict[str, Any] | None = None
    if path.exists():
        existing = _read_json(path)
        existing_payload = _canonical_json({
            key: value for key, value in existing.items() if key != "drive_target_manifest"
        })
        if existing_payload != payload:
            raise SourceArchiveCampaignError(
                "TARGET.json schema, universe mode, or target digest conflicts with the supplied frozen target plan"
            )
        # The existing identity was read back and verified when the frozen
        # campaign denominator was first archived.  Re-archiving it for every
        # resumed shard creates a multi-megabyte Drive round trip before the
        # first source request, while the immutable identity itself has not
        # changed.  Validate the bound identity locally and reuse it; every
        # newly archived source still has its own upload/readback verification.
        return _validate_drive_target_manifest_identity(existing, expected_sha256=expected_sha256)

    receipt = next((target.source_receipt for target in plan.targets if target.source_receipt), None)
    target_object = archive.archive_bytes(
        data=payload,
        extension="json",
        metadata={
            "source_receipt": receipt or f"campaign-{plan.target_digest}",
            "source_uri": f"campaign://source-archive/{plan.target_digest}/TARGET.json",
            "archive_version": "source-archive-target-manifest-v1",
        },
    )
    identity = {**_object_summary(target_object), "target_digest": plan.target_digest}
    if identity.get("sha256") != expected_sha256:
        raise SourceArchiveCampaignError("Drive target manifest checksum does not match frozen target bytes")
    if not identity.get("storage_uri") or not isinstance(identity.get("byte_length"), int):
        raise SourceArchiveCampaignError("Drive target manifest object identity is incomplete")
    _atomic_write(path, _canonical_json({**canonical, "drive_target_manifest": identity}))
    return identity


def _validate_drive_target_manifest_identity(
    manifest: Mapping[str, Any],
    *,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    identity = manifest.get("drive_target_manifest")
    if not isinstance(identity, Mapping):
        raise SourceArchiveCampaignError("TARGET.json lacks required immutable Drive target manifest identity")
    if not isinstance(identity.get("storage_uri"), str) or not identity["storage_uri"]:
        raise SourceArchiveCampaignError("TARGET.json Drive target manifest storage URI is invalid")
    if not isinstance(identity.get("sha256"), str) or len(identity["sha256"]) != 64:
        raise SourceArchiveCampaignError("TARGET.json Drive target manifest checksum is invalid")
    if not isinstance(identity.get("byte_length"), int) or identity["byte_length"] < 1:
        raise SourceArchiveCampaignError("TARGET.json Drive target manifest byte length is invalid")
    payload = {key: value for key, value in manifest.items() if key != "drive_target_manifest"}
    actual = hashlib.sha256(_canonical_json(payload)).hexdigest()
    if identity["sha256"] != (expected_sha256 or actual):
        raise SourceArchiveCampaignError("TARGET.json Drive target manifest checksum mismatch")
    if identity.get("target_digest") != manifest.get("target_digest"):
        raise SourceArchiveCampaignError("TARGET.json Drive target manifest target digest mismatch")
    return dict(identity)


def _append_outcome(path: Path, outcome: Mapping[str, Any], target_digest: str) -> dict[str, Any]:
    row = {**dict(outcome), "target_digest": target_digest}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    return row


def _completed_company_years(path: Path, target_digest: str) -> set[tuple[str, int]]:
    return _resume_state(path, target_digest).completed_company_years


def _write_committed_marker(path: Path, plan: SourceArchivePlan, shard: int, outcomes: Path, selected: tuple[SourceArchiveTarget, ...]) -> None:
    if path.exists():
        _verify_committed_marker(path, plan, shard, outcomes, selected)
        return
    _atomic_write(path, _canonical_json({
        "schema": plan.campaign_schema, "target_digest": plan.target_digest, "shard": shard,
        "outcomes_sha256": hashlib.sha256(outcomes.read_bytes()).hexdigest(),
        "committed_at": datetime.now(UTC).isoformat(),
    }))


def _verify_committed_marker(path: Path, plan: SourceArchivePlan, shard: int, outcomes: Path, selected: tuple[SourceArchiveTarget, ...]) -> None:
    marker = _read_json(path)
    if marker.get("schema") != plan.campaign_schema or marker.get("target_digest") != plan.target_digest or marker.get("shard") != shard:
        raise SourceArchiveCampaignError("COMMITTED.json identity does not match the frozen target plan")
    actual = hashlib.sha256(outcomes.read_bytes()).hexdigest() if outcomes.exists() else ""
    if marker.get("outcomes_sha256") != actual:
        raise SourceArchiveCampaignError("COMMITTED.json outcomes checksum mismatch")
    latest: dict[tuple[str, int], str] = {}
    for row in _outcome_rows(outcomes):
        if row.get("company_year_terminal", True):
            latest[(str(row["corp_code"]), int(row["bsns_year"]))] = str(row["status"])
    expected = {(target.corp_code, target.bsns_year) for target in selected}
    if set(latest) != expected or any(latest[key] != "structurally_complete" for key in expected):
        raise SourceArchiveCampaignError("COMMITTED.json terminal outcomes are incomplete")


def _outcome_rows(path: Path) -> list[dict[str, Any]]:
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceArchiveCampaignError("invalid shard outcomes manifest") from exc


def _require_non_runtime_source_session(session: Session) -> None:
    from kreports.config import settings

    left = _sqlite_identity(str(session.get_bind().url))
    right = _sqlite_identity(str(settings.db_url))
    if left is not None and right is not None and _same_file_identity(left, right):
        raise SourceArchiveCampaignError("source archive planning requires a non-runtime collector database")


def _sqlite_identity(value: str) -> Path | None:
    try:
        parsed = make_url(value)
    except Exception:
        return None
    if parsed.get_backend_name() != "sqlite" or not parsed.database or parsed.database == ":memory:":
        return None
    database = str(parsed.database)
    if database.startswith("file:"):
        uri = urlsplit(database)
        database = unquote(uri.path)
    return Path(database).expanduser().resolve()


def _same_file_identity(left: Path, right: Path) -> bool:
    try:
        return left.samefile(right)
    except OSError:
        return left == right


def _target_digest(
    years: tuple[int, ...],
    shard_count: int,
    targets: tuple[SourceArchiveTarget, ...],
    *,
    universe_mode: str = "listed",
) -> str:
    if universe_mode == "listed":
        # Preserve the v2 digest byte-for-byte for existing frozen campaigns.
        payload = {
            "schema": CAMPAIGN_SCHEMA,
            "years": list(years),
            "shard_count": shard_count,
            "targets": [target.to_dict() for target in targets],
        }
    else:
        payload = {
            "schema": ALL_ISSUER_CAMPAIGN_SCHEMA,
            "universe_mode": universe_mode,
            "years": list(years),
            "shard_count": shard_count,
            "targets": [target.to_dict() for target in targets],
        }
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def _target_from_dict(value: Mapping[str, Any]) -> SourceArchiveTarget:
    return SourceArchiveTarget(
        corp_code=str(value["corp_code"]), bsns_year=int(value["bsns_year"]),
        market=str(value["market"]) if value.get("market") is not None else None,
        shard=int(value["shard"]), source_receipt=value.get("source_receipt"), report_nm=value.get("report_nm"),
        source_uri=value.get("source_uri"), source_status=str(value["source_status"]),
        required_report_kinds=tuple(value.get("required_report_kinds", ("business_report", "audit_report"))),  # type: ignore[arg-type]
        universe_cohort=value.get("universe_cohort"),
        historical_listing_status=value.get("historical_listing_status"),
        historical_listing_basis=value.get("historical_listing_basis"),
    )


def _company_shard(corp_code: str, shard_count: int) -> int:
    return int.from_bytes(hashlib.sha256(corp_code.encode()).digest(), "big") % shard_count


def _normalize_years(years: Iterable[int]) -> tuple[int, ...]:
    try:
        result = tuple(sorted({int(year) for year in years}))
    except (TypeError, ValueError) as exc:
        raise SourceArchiveCampaignError("years must be integer business years") from exc
    if not result or any(year < 1900 or year > 3000 for year in result):
        raise SourceArchiveCampaignError("years must be a non-empty reasonable business-year set")
    return result


def _validate_shard_count(shard_count: int) -> None:
    if not isinstance(shard_count, int) or not 1 <= shard_count <= 1024:
        raise SourceArchiveCampaignError("shard_count must be an integer from 1 to 1024")


def _validate_universe_mode(universe_mode: str) -> None:
    if not isinstance(universe_mode, str) or universe_mode not in _UNIVERSE_MODES:
        allowed = ", ".join(sorted(_UNIVERSE_MODES))
        raise SourceArchiveCampaignError(f"universe_mode must be one of: {allowed}")


def _validate_shard(shard: int, shard_count: int) -> None:
    if not isinstance(shard, int) or not 0 <= shard < shard_count:
        raise SourceArchiveCampaignError(f"shard must be between 0 and {shard_count - 1}")


def _required_state_dir(plan: SourceArchivePlan) -> Path:
    if plan.state_dir is None:
        raise SourceArchiveCampaignError("--apply requires an explicit campaign state directory")
    return Path(plan.state_dir)


def _decode_for_parser(value: bytes) -> str:
    for encoding in ("utf-8", "euc-kr", "cp949"):
        try:
            return value.decode(encoding, errors="strict")
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("source", value, 0, len(value), "no strict DART text decoder matched")


def _document_source_uri(receipt: str) -> str:
    return f"https://opendart.fss.or.kr/api/document.xml?rcept_no={receipt}"


def _asset_source_uri(receipt: str, locator: str, content_type: str) -> str:
    if content_type == "pdf":
        dcm_no = locator[4:] if locator.startswith("dcm:") else locator
        return f"https://dart.fss.or.kr/pdf/download/pdf.do?rcp_no={receipt}&dcm_no={dcm_no}"
    if locator.startswith("dcm:"):
        return f"https://dart.fss.or.kr/report/viewer.do?rcpNo={receipt}&dcmNo={locator[4:]}"
    return _document_source_uri(receipt)


def _extension(content_type: str) -> str:
    return {"html": "html", "pdf": "pdf", "xml": "xml"}.get(content_type, "bin")


def _container_extension(content_type: str) -> str:
    media_type = content_type.split(";", 1)[0].strip().lower()
    return {
        "application/xml": "xml",
        "text/xml": "xml",
        "text/html": "html",
        "application/pdf": "pdf",
    }.get(media_type, "bin")


def _object_summary(value: Any) -> dict[str, Any]:
    if hasattr(value, "storage_uri"):
        return {"storage_uri": value.storage_uri, "sha256": value.sha256, "byte_length": value.byte_length}
    if isinstance(value, Mapping):
        return {key: value[key] for key in ("storage_uri", "sha256", "byte_length") if key in value}
    return {"identity": str(value)}


def _bounded_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc)[:240]}"


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceArchiveCampaignError(f"invalid campaign state: {path}") from exc
    if not isinstance(value, dict):
        raise SourceArchiveCampaignError(f"campaign state must be an object: {path}")
    return value
