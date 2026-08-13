"""Strict, bounded, read-only MCP resources over prepared KReports data."""
from __future__ import annotations

from contextlib import contextmanager
from collections import OrderedDict
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit

from mcp.types import Resource, ResourceTemplate
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

import kreports.db.engine as _engine_module
from kreports.db.models import (
    Company,
    CompanyYearQuality,
    DatasetManifest,
    EvidenceDocument,
    Financial,
    ReportSection,
    SourceDocument,
)
from kreports.quality.release_gate import (
    evaluate_release_gate,
    runtime_db_unavailable_report,
)
from kreports.storage.evidence_blobs import EvidenceBlobStore
from kreports.storage.raw_documents import RawDocumentStore
from kreports.mcp.visual_contracts import (
    VisualizationPackV1,
    render_visualization_html,
    validate_visualization_pack,
)


DATASET_READINESS_URI = "kreports://dataset/readiness"
COMPANY_URI_TEMPLATE = "kreports://company/{corp_code}/{year}"
EVIDENCE_URI_TEMPLATE = "kreports://evidence/{rcept_no}"
VISUALIZATION_URI_TEMPLATE = "kreports://visualization/{digest}"
MAX_EVIDENCE_CHARACTERS = 20_000
MAX_VISUALIZATION_RESOURCES = 32
MAX_VISUALIZATION_CACHE_BYTES = 4_000_000

_CORP_CODE = re.compile(r"[0-9]{8}", re.ASCII)
_RCEPT_NO = re.compile(r"[0-9]{14}", re.ASCII)
_COMPANY_RESOURCE_PATH = re.compile(
    r"/([0-9]{8})/([0-9]{4})",
    re.ASCII,
)
_EVIDENCE_RESOURCE_PATH = re.compile(r"/([0-9]{14})", re.ASCII)
_VISUALIZATION_RESOURCE_PATH = re.compile(r"/([0-9a-f]{64})", re.ASCII)
_DART_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"
_VISUALIZATION_RESOURCES: OrderedDict[str, dict[str, Any]] = OrderedDict()
_VISUALIZATION_CACHE_BYTES = 0


class ResourceRequestError(ValueError):
    """A stable public resource error that never embeds private state."""


class _ResourceDatabaseUnavailable(RuntimeError):
    pass


@contextmanager
def _resource_session():
    """Open one non-committing session, using immutable SQLite file access."""
    source_engine = _engine_module.engine
    if source_engine.dialect.name != "sqlite":
        with Session(bind=source_engine) as session:
            yield session
        return

    database = source_engine.url.database
    if database in {None, "", ":memory:"}:
        with Session(bind=source_engine) as session:
            yield session
        return

    database_path = Path(str(database)).expanduser().resolve()
    if not database_path.is_file():
        raise _ResourceDatabaseUnavailable("runtime_db_unavailable")
    wal_path = Path(f"{database_path}-wal")
    if wal_path.exists() and wal_path.stat().st_size > 0:
        raise _ResourceDatabaseUnavailable(
            "resource_db_unavailable:uncheckpointed_wal"
        )
    readonly_url = (
        f"sqlite:///file:{database_path.as_posix()}"
        "?mode=ro&immutable=1&uri=true"
    )
    readonly_engine = create_engine(
        readonly_url,
        connect_args={"check_same_thread": False},
    )
    try:
        with Session(bind=readonly_engine) as session:
            yield session
    finally:
        readonly_engine.dispose()


@dataclass(frozen=True)
class ResourceDescriptor:
    uri: str
    name: str
    description: str
    mime_type: str = "application/json"

    def to_mcp(self) -> Resource:
        return Resource(
            uri=self.uri,
            name=self.name,
            description=self.description,
            mimeType=self.mime_type,
        )


@dataclass(frozen=True)
class ResourceTemplateDescriptor:
    uri_template: str
    name: str
    description: str
    mime_type: str = "application/json"

    def to_mcp(self) -> ResourceTemplate:
        return ResourceTemplate(
            uriTemplate=self.uri_template,
            name=self.name,
            description=self.description,
            mimeType=self.mime_type,
        )


def list_resources() -> list[ResourceDescriptor]:
    return [
        ResourceDescriptor(
            uri=DATASET_READINESS_URI,
            name="dataset_readiness",
            description=(
                "Prepared dataset versions, feature gates, denominators, "
                "exclusions, and degraded capabilities."
            ),
        )
    ]


def list_resource_templates() -> list[ResourceTemplateDescriptor]:
    return [
        ResourceTemplateDescriptor(
            uri_template=COMPANY_URI_TEMPLATE,
            name="company_year",
            description=(
                "One company's prepared facts, feature quality, and DART "
                "evidence links for an exact business year."
            ),
        ),
        ResourceTemplateDescriptor(
            uri_template=EVIDENCE_URI_TEMPLATE,
            name="filing_evidence",
            description=(
                "Bounded filing evidence recovered from external raw, "
                "normalized evidence, or derived sections."
            ),
        ),
        ResourceTemplateDescriptor(
            uri_template=VISUALIZATION_URI_TEMPLATE,
            name="visualization_html",
            description=(
                "One bounded, process-local, content-addressed HTML rendering "
                "published by a tool response from a validated visualization pack."
            ),
            mime_type="text/html; charset=utf-8",
        ),
    ]


def mcp_resources() -> list[Resource]:
    return [descriptor.to_mcp() for descriptor in list_resources()]


def mcp_resource_templates() -> list[ResourceTemplate]:
    return [
        descriptor.to_mcp()
        for descriptor in list_resource_templates()
    ]


def _parse_uri(uri: object) -> tuple[str, dict[str, Any]]:
    raw = str(uri)
    if (
        raw != raw.strip()
        or not raw.isascii()
        or any(
            ord(character) <= 0x1F or ord(character) == 0x7F
            for character in raw
        )
    ):
        raise ResourceRequestError("invalid_resource_uri")
    try:
        parsed = urlsplit(raw)
    except ValueError:
        raise ResourceRequestError("invalid_resource_uri") from None
    if (
        parsed.scheme != "kreports"
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ResourceRequestError("invalid_resource_uri")
    if parsed.netloc == "dataset" and parsed.path == "/readiness":
        if raw != DATASET_READINESS_URI:
            raise ResourceRequestError("invalid_resource_uri")
        return "dataset_readiness", {}
    company_match = _COMPANY_RESOURCE_PATH.fullmatch(parsed.path)
    if parsed.netloc == "company" and company_match:
        corp_code, raw_year = company_match.groups()
        if not _CORP_CODE.fullmatch(corp_code):
            raise ResourceRequestError("invalid_corp_code")
        canonical = COMPANY_URI_TEMPLATE.format(
            corp_code=corp_code,
            year=raw_year,
        )
        if raw != canonical:
            raise ResourceRequestError("invalid_resource_uri")
        year = int(raw_year)
        if not 2000 <= year <= 2100:
            raise ResourceRequestError("invalid_year")
        return "company_year", {"corp_code": corp_code, "year": year}
    evidence_match = _EVIDENCE_RESOURCE_PATH.fullmatch(parsed.path)
    if parsed.netloc == "evidence" and evidence_match:
        rcept_no = evidence_match.group(1)
        canonical = EVIDENCE_URI_TEMPLATE.format(rcept_no=rcept_no)
        if raw != canonical:
            raise ResourceRequestError("invalid_resource_uri")
        return "filing_evidence", {"rcept_no": rcept_no}
    visualization_match = _VISUALIZATION_RESOURCE_PATH.fullmatch(parsed.path)
    if parsed.netloc == "visualization" and visualization_match:
        digest = visualization_match.group(1)
        canonical = VISUALIZATION_URI_TEMPLATE.format(digest=digest)
        if raw != canonical:
            raise ResourceRequestError("invalid_resource_uri")
        return "visualization", {"digest": digest}
    if parsed.netloc == "company":
        raise ResourceRequestError("invalid_company_resource")
    if parsed.netloc == "evidence":
        raise ResourceRequestError("invalid_evidence_resource")
    if parsed.netloc == "visualization":
        raise ResourceRequestError("invalid_visualization_resource")
    if parsed.netloc == "dataset":
        raise ResourceRequestError("invalid_dataset_resource")
    raise ResourceRequestError("unknown_resource")


def _dataset_readiness() -> dict[str, Any]:
    try:
        with _resource_session() as session:
            session.connection()
    except _ResourceDatabaseUnavailable as exc:
        gate = runtime_db_unavailable_report("public_runtime")
        failure = str(exc)
        if failure.startswith("resource_db_unavailable:"):
            gate["required_failures"] = [failure]
    except Exception:
        gate = runtime_db_unavailable_report("public_runtime")
    else:
        try:
            gate = evaluate_release_gate(
                "public_runtime",
                session_scope=_resource_session,
                include_legacy_diagnostics=False,
            )
        except Exception:
            gate = runtime_db_unavailable_report("public_runtime")
    required_failures = list(gate.get("required_failures") or [])
    schema_version = str(gate.get("schema_version") or "unknown")
    dataset_version = str(gate.get("dataset_version") or "unknown")
    manifest_available = (
        schema_version != "unknown"
        and dataset_version != "unknown"
        and "release_manifest_unavailable" not in required_failures
    )
    return {
        "resource_version": "1.0",
        "profile": gate.get("profile", "public_runtime"),
        "release_ready": bool(gate.get("ok")) and manifest_available,
        "manifest_available": manifest_available,
        "schema_version": schema_version,
        "dataset_version": dataset_version,
        "coverage_year": gate.get("coverage_year"),
        "feature_gates": gate.get("coverage") or {},
        "denominators": gate.get("denominators") or {},
        "excluded_populations": gate.get("excluded_populations") or {},
        "required_failures": required_failures,
        "degraded_features": list(gate.get("degraded_features") or []),
        "limitations": (
            []
            if manifest_available
            else [
                "A release manifest was not verified; this resource does "
                "not claim dataset release readiness."
            ]
        ),
    }


def _manifest_payload(session) -> dict[str, Any]:
    if "dataset_manifest" not in inspect(session.get_bind()).get_table_names():
        return {
            "available": False,
            "schema_version": "unknown",
            "dataset_version": "unknown",
        }
    row = (
        session.query(DatasetManifest)
        .order_by(DatasetManifest.generated_at.desc())
        .first()
    )
    if row is None:
        return {
            "available": False,
            "schema_version": "unknown",
            "dataset_version": "unknown",
        }
    return {
        "available": True,
        "schema_version": row.schema_version,
        "dataset_version": row.dataset_version,
        "generated_at": (
            row.generated_at.isoformat()
            if row.generated_at is not None
            else None
        ),
        "year_from": row.year_from,
        "year_to": row.year_to,
    }


def _quality_payload(row: CompanyYearQuality | None) -> dict[str, Any] | None:
    if row is None:
        return None
    try:
        blockers = json.loads(row.blockers_json or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        blockers = ["invalid_quality_blockers"]
    return {
        "quality_version": row.quality_version,
        "financial_core_status": row.financial_core_status,
        "auditor_status": row.auditor_status,
        "audit_fee_status": row.audit_fee_status,
        "policy_status": row.policy_status,
        "kam_status": row.kam_status,
        "audit_procedure_status": row.audit_procedure_status,
        "group_audit_status": row.group_audit_status,
        "investor_grade": row.investor_grade,
        "auditor_grade": row.auditor_grade,
        "group_audit_grade": row.group_audit_grade,
        "blockers": blockers if isinstance(blockers, list) else [],
    }


def _company_year(corp_code: str, year: int) -> dict[str, Any]:
    try:
        with _resource_session() as session:
            company = session.get(Company, corp_code)
            if company is None:
                raise ResourceRequestError("company_not_found")
            manifest = _manifest_payload(session)
            quality = session.get(
                CompanyYearQuality,
                {"corp_code": corp_code, "bsns_year": year},
            )
            facts = (
                session.query(Financial)
                .filter(
                    Financial.corp_code == corp_code,
                    Financial.year == year,
                    Financial.quarter == 4,
                )
                .order_by(
                    (Financial.fs_div == "CFS").desc(),
                    Financial.fs_div,
                )
                .limit(2)
                .all()
            )
            evidence_rows = (
                session.query(EvidenceDocument.rcept_no)
                .filter(
                    EvidenceDocument.corp_code == corp_code,
                    EvidenceDocument.bsns_year == year,
                )
                .order_by(EvidenceDocument.rcept_no)
                .limit(20)
                .all()
            )
            structured_facts = [
                {
                    "fs_div": fact.fs_div,
                    "revenue": fact.revenue,
                    "operating_profit": fact.operating_profit,
                    "net_income": fact.net_income,
                    "total_assets": fact.total_assets,
                    "total_debt": fact.total_debt,
                    "total_equity": fact.total_equity,
                    "operating_cf": fact.operating_cf,
                }
                for fact in facts
            ]
            evidence = [
                {
                    "rcept_no": row.rcept_no,
                    "source_url": _DART_URL.format(
                        rcept_no=row.rcept_no
                    ),
                }
                for row in evidence_rows
                if _RCEPT_NO.fullmatch(str(row.rcept_no))
            ]
            company_payload = {
                "corp_code": company.corp_code,
                "corp_name": company.corp_name,
                "stock_code": company.stock_code,
                "market": company.market,
                "induty_code": company.induty_code or company.sector,
            }
            quality_payload = _quality_payload(quality)
    except ResourceRequestError:
        raise
    except _ResourceDatabaseUnavailable as exc:
        failure = str(exc)
        return {
            "resource_version": "1.0",
            "company": {"corp_code": corp_code},
            "year": year,
            "cache_status": "error",
            "filing_status": "not_determined",
            "manifest": {
                "available": False,
                "schema_version": "unknown",
                "dataset_version": "unknown",
            },
            "quality": None,
            "structured_facts": [],
            "evidence": [],
            "data_quality": {
                "status": "error",
                "limitations": [failure],
            },
            "errors": [failure],
        }
    except Exception:
        return {
            "resource_version": "1.0",
            "company": {"corp_code": corp_code},
            "year": year,
            "cache_status": "error",
            "filing_status": "not_determined",
            "manifest": {
                "available": False,
                "schema_version": "unknown",
                "dataset_version": "unknown",
            },
            "quality": None,
            "structured_facts": [],
            "evidence": [],
            "data_quality": {"status": "error"},
            "errors": ["company_resource_read_failed"],
        }

    has_cache = bool(quality or structured_facts or evidence)
    return {
        "resource_version": "1.0",
        "company": company_payload,
        "year": year,
        "cache_status": "available" if has_cache else "missing",
        "filing_status": "not_determined",
        "manifest": manifest,
        "quality": quality_payload,
        "structured_facts": structured_facts,
        "evidence": evidence,
        "data_quality": {
            "status": "usable" if has_cache else "missing",
            "limitations": (
                []
                if has_cache
                else [
                    "No prepared cache was found. This does not establish "
                    "that the source filing is absent."
                ]
            ),
        },
        "errors": [],
    }


def _bounded_text(text: str) -> tuple[str, bool]:
    normalized = str(text or "")
    return (
        normalized[:MAX_EVIDENCE_CHARACTERS],
        len(normalized) > MAX_EVIDENCE_CHARACTERS,
    )


def _evidence(rcept_no: str) -> dict[str, Any]:
    recovery_errors: list[str] = []
    try:
        with _resource_session() as session:
            raw = (
                session.query(SourceDocument)
                .filter(
                    SourceDocument.rcept_no == rcept_no,
                    SourceDocument.storage_uri.isnot(None),
                    SourceDocument.storage_uri != "",
                )
                .order_by(SourceDocument.fetched_at.desc())
                .first()
            )
            normalized = (
                session.query(EvidenceDocument)
                .filter(EvidenceDocument.rcept_no == rcept_no)
                .order_by(EvidenceDocument.generated_at.desc())
                .first()
            )
            sections = (
                session.query(ReportSection)
                .filter(ReportSection.rcept_no == rcept_no)
                .order_by(
                    ReportSection.source_type,
                    ReportSection.ordinal,
                    ReportSection.section_key,
                )
                .limit(50)
                .all()
            )

            text_value: str | None = None
            source_basis: str | None = None
            if raw is not None:
                try:
                    raw_text = RawDocumentStore().read(
                        raw.storage_uri,
                        expected_hash=raw.doc_hash,
                    )
                    if raw_text.strip():
                        text_value = raw_text
                        source_basis = "raw_external"
                    else:
                        recovery_errors.append("raw_external_blank")
                except Exception:
                    recovery_errors.append("raw_external_read_failed")

            if text_value is None and normalized is not None:
                if normalized.full_text_uri:
                    try:
                        normalized_text = EvidenceBlobStore().read(
                            normalized.full_text_uri,
                            expected_hash=normalized.full_text_hash,
                        )
                        if normalized_text.strip():
                            text_value = normalized_text
                            source_basis = "normalized_evidence"
                        else:
                            recovery_errors.append(
                                "normalized_evidence_blank"
                            )
                    except Exception:
                        recovery_errors.append(
                            "normalized_external_read_failed"
                        )
                if text_value is None and normalized.normalized_text:
                    if normalized.normalized_text.strip():
                        text_value = normalized.normalized_text
                        source_basis = "normalized_evidence"
                    elif "normalized_evidence_blank" not in recovery_errors:
                        recovery_errors.append(
                            "normalized_evidence_blank"
                        )

            if text_value is None:
                section_parts: list[str] = []
                for section in sections:
                    section_text: str | None = None
                    if section.full_text_uri:
                        try:
                            section_text = EvidenceBlobStore().read(
                                section.full_text_uri,
                                expected_hash=section.full_text_hash,
                            )
                            if not section_text.strip():
                                recovery_errors.append(
                                    "derived_evidence_blank"
                                )
                                section_text = None
                        except Exception:
                            recovery_errors.append(
                                "derived_external_read_failed"
                            )
                    if section_text is None and section.body_text:
                        section_text = section.body_text
                    if section_text and section_text.strip():
                        section_parts.append(section_text)
                if section_parts:
                    text_value = "\n\n".join(section_parts)
                    source_basis = "derived_summary"
    except _ResourceDatabaseUnavailable as exc:
        failure = str(exc)
        return {
            "resource_version": "1.0",
            "rcept_no": rcept_no,
            "source_url": _DART_URL.format(rcept_no=rcept_no),
            "source_basis": None,
            "text": "",
            "truncated": False,
            "cache_status": "error",
            "filing_status": "not_determined",
            "data_quality": {
                "status": "error",
                "limitations": [failure],
            },
            "errors": [failure],
        }
    except Exception:
        return {
            "resource_version": "1.0",
            "rcept_no": rcept_no,
            "source_url": _DART_URL.format(rcept_no=rcept_no),
            "source_basis": None,
            "text": "",
            "truncated": False,
            "cache_status": "error",
            "filing_status": "not_determined",
            "data_quality": {"status": "error"},
            "errors": ["evidence_resource_read_failed"],
        }

    if text_value is None:
        status = "error" if recovery_errors else "missing"
        return {
            "resource_version": "1.0",
            "rcept_no": rcept_no,
            "source_url": _DART_URL.format(rcept_no=rcept_no),
            "source_basis": None,
            "text": "",
            "truncated": False,
            "cache_status": status,
            "filing_status": "not_determined",
            "data_quality": {
                "status": status,
                "limitations": (
                    recovery_errors
                    if recovery_errors
                    else [
                        "No prepared evidence cache was found. This does not "
                        "establish that the source filing is absent."
                    ]
                ),
            },
            "errors": recovery_errors,
        }

    bounded, truncated = _bounded_text(text_value)
    return {
        "resource_version": "1.0",
        "rcept_no": rcept_no,
        "source_url": _DART_URL.format(rcept_no=rcept_no),
        "source_basis": source_basis,
        "text": bounded,
        "truncated": truncated,
        "cache_status": "available",
        "filing_status": "linked",
        "data_quality": {
            "status": "limited" if recovery_errors or truncated else "usable",
            "limitations": [
                *recovery_errors,
                *(
                    [f"Evidence text was limited to {MAX_EVIDENCE_CHARACTERS} characters."]
                    if truncated
                    else []
                ),
            ],
        },
        "errors": recovery_errors,
    }


def read_resource(uri: object) -> dict[str, Any]:
    resource_type, arguments = _parse_uri(uri)
    if resource_type == "dataset_readiness":
        return _dataset_readiness()
    if resource_type == "company_year":
        return _company_year(**arguments)
    if resource_type == "visualization":
        return _visualization_resource(**arguments)
    return _evidence(**arguments)


def render_resource(uri: object) -> str:
    resource_type, _ = _parse_uri(uri)
    if resource_type == "visualization":
        return str(read_resource(uri)["text"])
    return json.dumps(
        read_resource(uri),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def render_visualization_resource(
    pack: VisualizationPackV1 | dict[str, Any],
) -> dict[str, str]:
    """Return a self-contained rich resource without reading or writing the DB."""
    validated = validate_visualization_pack(pack)
    publish_visualization_resource(validated)
    if validated.resource_uri is None:  # pragma: no cover - validator derives it
        raise ResourceRequestError("invalid_visualization_resource")
    return read_resource(validated.resource_uri)


def publish_visualization_resource(
    pack: VisualizationPackV1 | dict[str, Any],
) -> str:
    """Publish one bounded HTML resource in the current MCP server process."""
    global _VISUALIZATION_CACHE_BYTES

    validated = validate_visualization_pack(pack)
    if validated.resource_uri is None:  # pragma: no cover - validator derives it
        raise ResourceRequestError("invalid_visualization_resource")
    html_text = render_visualization_html(validated)
    size = len(html_text.encode())
    if size > 200_000:
        raise ResourceRequestError("visualization_resource_too_large")
    digest = validated.resource_uri.rsplit("/", 1)[-1]
    entry = {
        "uri": validated.resource_uri,
        "mimeType": "text/html; charset=utf-8",
        "text": html_text,
        "pack": validated.model_dump(mode="json"),
        "size": size,
    }
    _remove_visualization_resource(digest)
    _VISUALIZATION_RESOURCES[digest] = entry
    _VISUALIZATION_CACHE_BYTES += size
    while (
        _VISUALIZATION_RESOURCES
        and (
            len(_VISUALIZATION_RESOURCES) > MAX_VISUALIZATION_RESOURCES
            or _VISUALIZATION_CACHE_BYTES > MAX_VISUALIZATION_CACHE_BYTES
        )
    ):
        evicted_digest = next(iter(_VISUALIZATION_RESOURCES))
        _remove_visualization_resource(evicted_digest)
    return validated.resource_uri


def _remove_visualization_resource(digest: str) -> dict[str, Any] | None:
    """Remove one cache entry and restore the exact byte invariant."""
    global _VISUALIZATION_CACHE_BYTES

    entry = _VISUALIZATION_RESOURCES.pop(digest, None)
    if entry is not None:
        _VISUALIZATION_CACHE_BYTES = sum(
            int(item["size"])
            for item in _VISUALIZATION_RESOURCES.values()
        )
    return entry


def _visualization_resource(digest: str) -> dict[str, Any]:
    entry = _VISUALIZATION_RESOURCES.get(digest)
    if entry is None:
        raise ResourceRequestError("visualization_resource_unavailable")
    try:
        validated = validate_visualization_pack(entry["pack"])
        expected_uri = VISUALIZATION_URI_TEMPLATE.format(digest=digest)
        if validated.resource_uri != expected_uri:
            raise ValueError("digest mismatch")
        regenerated = render_visualization_html(validated)
        if regenerated != entry["text"]:
            raise ValueError("content mismatch")
    except Exception:
        _remove_visualization_resource(digest)
        raise ResourceRequestError(
            "visualization_resource_unavailable"
        ) from None
    _VISUALIZATION_RESOURCES.move_to_end(digest)
    return {
        "resource_version": "visualization.v1",
        "uri": entry["uri"],
        "mimeType": entry["mimeType"],
        "text": entry["text"],
    }


def resource_mime_type(uri: object) -> str:
    resource_type, _ = _parse_uri(uri)
    return (
        "text/html; charset=utf-8"
        if resource_type == "visualization"
        else "application/json"
    )


def _clear_visualization_resources_for_test() -> None:
    """Clear only the bounded ephemeral visualization cache."""
    global _VISUALIZATION_CACHE_BYTES
    _VISUALIZATION_RESOURCES.clear()
    _VISUALIZATION_CACHE_BYTES = 0
