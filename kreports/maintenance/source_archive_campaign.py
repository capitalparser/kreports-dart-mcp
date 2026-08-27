"""Frozen, Drive-manifested annual DART source archive campaigns.

The campaign is a local collector workflow.  It derives its denominator from
historical listing evidence, retains exact source bytes before parsing, and
requires both the business-report and audit-report families before a
company-year can be structurally complete.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol
from urllib.parse import unquote, urlsplit

from sqlalchemy import inspect, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from kreports.analysis.filing_provenance import latest_annual_filing_anchor_from_rows
from kreports.collector.fetcher import (
    DartRequestBudgetExceeded,
    fetch_audit_report_pdf,
    fetch_dart_main_html,
    fetch_document_zip_asset_bytes,
    fetch_viewer_bytes,
    request_budget,
)
from kreports.collector.report_document_collector import (
    audit_viewer_requires_pdf_fallback,
    select_primary_audit_report_attachments,
)
from kreports.db.models import CompanyYearListingMembership, Disclosure
from kreports.processor.document_structure import PARSER_VERSION, parse_document_structure
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
DEFAULT_SHARD_COUNT = 64
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
    market: str
    shard: int
    source_receipt: str | None
    report_nm: str | None
    source_uri: str | None
    source_status: str
    required_report_kinds: tuple[str, str] = ("business_report", "audit_report")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["required_report_kinds"] = list(self.required_report_kinds)
        return result


@dataclass(frozen=True)
class SourceArchivePlan:
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
    shard: int
    apply: bool
    status: str
    target_digest: str
    outcomes: tuple[dict[str, Any], ...]
    manifest_path: Path | None = None
    dart_calls_used: int = 0
    dart_calls_budget: int | None = None

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
            "dart_calls_used": self.dart_calls_used,
            "dart_calls_budget": self.dart_calls_budget,
        }


def build_source_archive_plan(session: Session, years: Iterable[int], shard_count: int = DEFAULT_SHARD_COUNT) -> SourceArchivePlan:
    """Build a no-write plan from verified KOSPI/KOSDAQ year memberships only."""
    _require_non_runtime_source_session(session)
    normalized_years = _normalize_years(years)
    _validate_shard_count(shard_count)
    memberships = _verified_memberships(session, normalized_years)
    membership_by_pair = {(row["corp_code"], row["bsns_year"]): row for row in memberships}
    disclosure_rows = session.execute(select(
        Disclosure.corp_code, Disclosure.rcept_no, Disclosure.disc_date, Disclosure.report_nm,
    ).where(Disclosure.corp_code.in_({corp for corp, _year in membership_by_pair})).order_by(
        Disclosure.corp_code, Disclosure.disc_date.desc(), Disclosure.rcept_no.desc(),
    )).mappings().all() if membership_by_pair else []
    rows_by_company: dict[str, list[dict[str, Any]]] = {}
    for row in disclosure_rows:
        rows_by_company.setdefault(str(row["corp_code"]), []).append(dict(row))

    targets: list[SourceArchiveTarget] = []
    for corp_code, year in sorted(membership_by_pair):
        membership = membership_by_pair[(corp_code, year)]
        anchor = latest_annual_filing_anchor_from_rows(
            rows_by_company.get(corp_code, ()), corp_code=corp_code, bsns_year=year
        )
        if anchor is None:
            targets.append(SourceArchiveTarget(
                corp_code=corp_code, bsns_year=year, market=membership["market"],
                shard=_company_shard(corp_code, shard_count), source_receipt=None,
                report_nm=None, source_uri=None, source_status="no_source_metadata",
            ))
            continue
        receipt = str(anchor["rcept_no"])
        targets.append(SourceArchiveTarget(
            corp_code=corp_code, bsns_year=year, market=membership["market"],
            shard=_company_shard(corp_code, shard_count), source_receipt=receipt,
            report_nm=str(anchor["report_nm"]), source_uri=_document_source_uri(receipt),
            source_status="discovered",
        ))
    frozen_targets = tuple(targets)
    return SourceArchivePlan(
        years=normalized_years, shard_count=shard_count, targets=frozen_targets,
        target_digest=_target_digest(normalized_years, shard_count, frozen_targets),
    )


def run_source_archive_shard(
    plan: SourceArchivePlan,
    shard: int,
    archive: ArchiveWriter | None,
    *,
    apply: bool,
    max_dart_calls: int | None = None,
) -> SourceArchiveReport:
    """Run one frozen shard, requiring a finite DART call budget for apply."""
    _validate_shard(shard, plan.shard_count)
    selected = plan.targets_for_shard(shard)
    if not apply:
        return SourceArchiveReport(shard, False, "dry_run", plan.target_digest, ())
    if archive is None:
        raise SourceArchiveCampaignError("--apply requires a configured immutable Drive archive")
    if not isinstance(max_dart_calls, int) or max_dart_calls < 1:
        raise SourceArchiveCampaignError("--apply requires a finite positive max_dart_calls budget")
    from kreports.runtime import require_collector_mode

    require_collector_mode("source archive campaign")
    state_dir = _required_state_dir(plan)
    drive_target_manifest = _write_frozen_target_manifest(plan, state_dir, archive)
    shard_dir = state_dir / f"shard-{shard:02d}"
    shard_dir.mkdir(parents=True, exist_ok=True)
    outcomes_path = shard_dir / "outcomes.jsonl"
    marker_path = shard_dir / "COMMITTED.json"
    if marker_path.exists():
        _verify_committed_marker(marker_path, plan, shard, outcomes_path, selected)
    previous = _completed_company_years(outcomes_path, plan.target_digest)
    outcomes: list[dict[str, Any]] = []
    with request_budget(max_dart_calls) as budget:
        for target in selected:
            if (target.corp_code, target.bsns_year) in previous:
                outcomes.append(_outcome(target, "already_structurally_complete"))
                continue
            target_outcomes = _process_target(target, archive, drive_target_manifest)
            for outcome in target_outcomes:
                row = _append_outcome(
                    outcomes_path,
                    {**outcome, "drive_target_manifest": drive_target_manifest},
                    plan.target_digest,
                )
                _archive_campaign_event(archive, row)
            outcomes.extend(target_outcomes)

    terminal = [row for row in outcomes if row.get("company_year_terminal", True)]
    complete = bool(selected) and len(terminal) == len(selected) and all(
        row["status"] in {"structurally_complete", "already_structurally_complete"} for row in terminal
    )
    if complete:
        _write_committed_marker(marker_path, plan, shard, outcomes_path, selected)
    elif marker_path.exists():
        raise SourceArchiveCampaignError("partial shard cannot retain a COMMITTED.json marker")
    return SourceArchiveReport(
        shard, True, "complete" if complete else "partial", plan.target_digest, tuple(outcomes),
        outcomes_path, budget.used_calls, budget.max_calls,
    )


def verify_source_archive_campaign(state_dir: Path, *, shard: int | None = None) -> dict[str, Any]:
    """Verify local cache integrity only; this performs no DART or Drive call."""
    root = Path(state_dir)
    manifest = _read_json(root / "TARGET.json")
    if manifest.get("schema") != CAMPAIGN_SCHEMA:
        raise SourceArchiveCampaignError("TARGET.json schema is unsupported")
    _validate_drive_target_manifest_identity(manifest)
    shard_count = int(manifest["shard_count"])
    targets = tuple(_target_from_dict(value) for value in manifest.get("targets", ()))
    requested = [shard] if shard is not None else list(range(shard_count))
    records: list[dict[str, Any]] = []
    for shard_number in requested:
        _validate_shard(shard_number, shard_count)
        directory = root / f"shard-{shard_number:02d}"
        outcomes = directory / "outcomes.jsonl"
        marker = directory / "COMMITTED.json"
        selected = tuple(target for target in targets if target.shard == shard_number)
        if marker.exists():
            plan = SourceArchivePlan(tuple(manifest["years"]), shard_count, targets, str(manifest["target_digest"]), root)
            _verify_committed_marker(marker, plan, shard_number, outcomes, selected)
        records.append({
            "shard": shard_number,
            "outcome_count": len(outcomes.read_text(encoding="utf-8").splitlines()) if outcomes.exists() else 0,
            "committed": marker.exists(),
        })
    return {"schema": CAMPAIGN_SCHEMA, "target_digest": manifest["target_digest"], "target_count": manifest["target_count"], "shards": records}


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


def _process_target(
    target: SourceArchiveTarget,
    archive: ArchiveWriter,
    drive_target_manifest: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if target.source_status != "discovered" or not target.source_receipt or not target.source_uri:
        return [_outcome(target, target.source_status)]
    outcomes = [_outcome(target, "discovered", report_kind="company_year", company_year_terminal=False)]
    business, business_outcomes = _business_family(target, archive, drive_target_manifest)
    audit, audit_outcomes = _audit_family(target, archive, drive_target_manifest)
    outcomes.extend(business_outcomes)
    outcomes.extend(audit_outcomes)
    outcomes.append(_outcome(target, "structurally_complete" if business and audit else "partial_source", report_kind="company_year"))
    return outcomes


def _business_family(
    target: SourceArchiveTarget,
    archive: ArchiveWriter,
    drive_target_manifest: Mapping[str, Any],
) -> tuple[bool, list[dict[str, Any]]]:
    try:
        assets = fetch_document_zip_asset_bytes(target.source_receipt or "")
    except DartRequestBudgetExceeded:
        return False, [_outcome(target, "dart_budget_exhausted", report_kind="business_report", error="document_xml")]
    except Exception as exc:
        return False, [_outcome(target, "fetch_failed", report_kind="business_report", error=_bounded_error(exc))]
    if not assets:
        return False, [_outcome(target, "partial_source", report_kind="business_report", error="document_xml_empty")]
    container_bytes = getattr(assets, "container_bytes", None)
    if not isinstance(container_bytes, bytes) or not container_bytes:
        return False, [_outcome(
            target, "partial_source", report_kind="business_report",
            error="document_zip_container_missing",
        )]
    container_sha256 = hashlib.sha256(container_bytes).hexdigest()
    try:
        container_object = archive.archive_bytes(
            data=container_bytes,
            extension="zip",
            metadata={
                "source_receipt": target.source_receipt or "",
                "source_uri": target.source_uri or "",
                "archive_version": "raw-document-zip-container-v1",
                "corp_code": target.corp_code,
                "bsns_year": str(target.bsns_year),
                "report_kind": "business_report",
            },
        )
    except Exception as exc:
        return False, [_outcome(
            target, "asset_failed", report_kind="business_report",
            error=_bounded_error(exc),
        )]
    container = _object_summary(container_object)
    if (
        not container.get("storage_uri")
        or container.get("sha256") != container_sha256
        or container.get("byte_length") != len(container_bytes)
    ):
        return False, [_outcome(
            target, "asset_failed", report_kind="business_report",
            error="document_zip_container_archive_identity_invalid",
        )]
    outcomes: list[dict[str, Any]] = []
    success = True
    for filename, raw in sorted(assets.items()):
        complete, asset_outcomes = _archive_asset(
            target, archive, report_kind="business_report", source_locator=filename,
            filename=filename, content_type="xml", raw_bytes=raw,
            drive_target_manifest=drive_target_manifest,
            raw_container=container, container_member_name=filename,
        )
        success = success and complete
        outcomes.extend(asset_outcomes)
    return success, outcomes


def _audit_family(
    target: SourceArchiveTarget,
    archive: ArchiveWriter,
    drive_target_manifest: Mapping[str, Any],
) -> tuple[bool, list[dict[str, Any]]]:
    try:
        main_html = fetch_dart_main_html(target.source_receipt or "")
    except DartRequestBudgetExceeded:
        return False, [_outcome(target, "dart_budget_exhausted", report_kind="audit_report", error="main_html")]
    if not main_html:
        return False, [_outcome(target, "partial_source", report_kind="audit_report", error="audit_main_html_empty")]
    attachments = select_primary_audit_report_attachments(main_html)
    if not attachments:
        return False, [_outcome(target, "partial_source", report_kind="audit_report", error="audit_attachment_missing")]
    outcomes: list[dict[str, Any]] = []
    success = True
    for attachment in attachments:
        dcm_no = str(attachment.get("dcm_no") or "")
        receipt = str(attachment.get("rcept_no") or target.source_receipt)
        if not dcm_no:
            success = False
            outcomes.append(_outcome(target, "partial_source", report_kind="audit_report", error="audit_attachment_locator_missing"))
            continue
        content_type, raw, exhausted = _fetch_audit_attachment(receipt, dcm_no)
        if exhausted:
            success = False
            outcomes.append(_outcome(
                target, "dart_budget_exhausted", report_kind="audit_report",
                source_locator=f"dcm:{dcm_no}", error="audit_attachment",
            ))
            continue
        if raw is None:
            success = False
            outcomes.append(_outcome(target, "partial_source", report_kind="audit_report", source_locator=f"dcm:{dcm_no}", error="audit_attachment_unavailable"))
            continue
        complete, asset_outcomes = _archive_asset(
            target, archive, report_kind="audit_report", source_locator=f"dcm:{dcm_no}",
            filename=str(attachment.get("title") or f"{dcm_no}.{content_type}"), content_type=content_type,
            raw_bytes=raw, source_receipt=receipt, drive_target_manifest=drive_target_manifest,
        )
        success = success and complete
        outcomes.extend(asset_outcomes)
    return success, outcomes


def _fetch_audit_attachment(receipt: str, dcm_no: str) -> tuple[str, bytes | None, bool]:
    try:
        viewer = fetch_viewer_bytes(receipt, dcm_no)
    except DartRequestBudgetExceeded:
        return "html", None, True
    if viewer is not None:
        try:
            decoded = _decode_for_parser(viewer)
        except UnicodeDecodeError:
            decoded = None
        if decoded is not None and not audit_viewer_requires_pdf_fallback(decoded):
            return "html", viewer, False
    try:
        return "pdf", fetch_audit_report_pdf(receipt, dcm_no), False
    except DartRequestBudgetExceeded:
        return "pdf", None, True


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
        })
    try:
        raw_object = archive.archive_bytes(data=raw_bytes, extension=_extension(content_type), metadata=metadata)
        outcomes = [_outcome(
            target, "archived_verified", report_kind=report_kind, source_locator=source_locator,
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
        parsed_object = archive_structured_document(archive, parsed)  # type: ignore[arg-type]
        outcomes.append(_outcome(
            target, "generically_parsed", report_kind=report_kind, source_locator=source_locator,
            filename=filename, content_type=content_type, source_receipt=receipt,
            structural_status=parsed.structural_status, parsed_object=_object_summary(parsed_object),
            parser_version=PARSER_VERSION, company_year_terminal=False,
            source_uri=source_uri,
            raw_container=dict(raw_container) if raw_container is not None else None,
            container_member_name=container_member_name,
        ))
        document = {
            "schema": "source-archive-document-manifest.v1", "corp_code": target.corp_code,
            "bsns_year": target.bsns_year, "market": target.market, "report_kind": report_kind,
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
            metadata={"source_receipt": receipt or "", "source_uri": source_uri, "archive_version": "source-archive-document-manifest-v1"},
        )
        outcomes[-1]["document_manifest"] = _object_summary(manifest)
        return parsed.structural_status == "complete", outcomes
    except Exception as exc:
        return False, [_outcome(target, "asset_failed", report_kind=report_kind, source_locator=source_locator, error=_bounded_error(exc))]


def _archive_campaign_event(archive: ArchiveWriter, row: Mapping[str, Any]) -> None:
    receipt = str(row.get("source_receipt") or "campaign")
    uri = str(row.get("source_uri") or f"campaign://{row['target_digest']}/{row['shard']}")
    archive.archive_bytes(
        data=_canonical_json({"schema": "source-archive-campaign-manifest.v1", **dict(row)}), extension="json",
        metadata={"source_receipt": receipt, "source_uri": uri, "archive_version": "source-archive-campaign-manifest-v1"},
    )


def _outcome(target: SourceArchiveTarget, status: str, *, report_kind: str = "company_year", source_locator: str | None = None, error: str | None = None, company_year_terminal: bool = True, **extra: Any) -> dict[str, Any]:
    return {
        "schema": CAMPAIGN_SCHEMA, "recorded_at": datetime.now(UTC).isoformat(),
        "corp_code": target.corp_code, "bsns_year": target.bsns_year, "market": target.market,
        "shard": target.shard, "source_receipt": target.source_receipt, "source_uri": target.source_uri,
        "report_kind": report_kind, "source_locator": source_locator, "status": status,
        "error": error, "company_year_terminal": company_year_terminal, **extra,
    }


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
        if {key: value for key, value in existing.items() if key != "drive_target_manifest"} != canonical:
            raise SourceArchiveCampaignError("campaign TARGET.json conflicts with the supplied frozen target plan")
        _validate_drive_target_manifest_identity(existing, expected_sha256=expected_sha256)

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
    if existing is not None and existing["drive_target_manifest"] != identity:
        raise SourceArchiveCampaignError("campaign TARGET.json Drive manifest identity mismatch")
    if existing is None:
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
    if not path.exists():
        return set()
    completed: set[tuple[str, int]] = set()
    for row in _outcome_rows(path):
        if row.get("target_digest") != target_digest:
            raise SourceArchiveCampaignError("shard outcomes belong to a different frozen target plan")
        if row.get("status") == "structurally_complete":
            completed.add((str(row["corp_code"]), int(row["bsns_year"])))
    return completed


def _write_committed_marker(path: Path, plan: SourceArchivePlan, shard: int, outcomes: Path, selected: tuple[SourceArchiveTarget, ...]) -> None:
    if path.exists():
        _verify_committed_marker(path, plan, shard, outcomes, selected)
        return
    _atomic_write(path, _canonical_json({
        "schema": CAMPAIGN_SCHEMA, "target_digest": plan.target_digest, "shard": shard,
        "outcomes_sha256": hashlib.sha256(outcomes.read_bytes()).hexdigest(),
        "committed_at": datetime.now(UTC).isoformat(),
    }))


def _verify_committed_marker(path: Path, plan: SourceArchivePlan, shard: int, outcomes: Path, selected: tuple[SourceArchiveTarget, ...]) -> None:
    marker = _read_json(path)
    if marker.get("schema") != CAMPAIGN_SCHEMA or marker.get("target_digest") != plan.target_digest or marker.get("shard") != shard:
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


def _target_digest(years: tuple[int, ...], shard_count: int, targets: tuple[SourceArchiveTarget, ...]) -> str:
    return hashlib.sha256(_canonical_json({"schema": CAMPAIGN_SCHEMA, "years": list(years), "shard_count": shard_count, "targets": [target.to_dict() for target in targets]})).hexdigest()


def _target_from_dict(value: Mapping[str, Any]) -> SourceArchiveTarget:
    return SourceArchiveTarget(
        corp_code=str(value["corp_code"]), bsns_year=int(value["bsns_year"]), market=str(value["market"]),
        shard=int(value["shard"]), source_receipt=value.get("source_receipt"), report_nm=value.get("report_nm"),
        source_uri=value.get("source_uri"), source_status=str(value["source_status"]),
        required_report_kinds=tuple(value.get("required_report_kinds", ("business_report", "audit_report"))),  # type: ignore[arg-type]
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
