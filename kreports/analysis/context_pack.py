"""Read-only, source-separated context packs for LLM-assisted research."""
from __future__ import annotations

from collections.abc import Iterable
import json
from typing import Any

from kreports.analysis.filing_provenance import canonical_annual_filing_source_receipt
from kreports.mcp.answer_contracts import (
    ContextEvidenceV1,
    ContextPackV1,
    LLMAnalysisV1,
    MissingEvidenceV1,
    PeerNoteComparisonV1,
    SourceConflictV1,
    SOURCE_PRECEDENCE,
    SuppliedExternalEvidenceV1,
)


CONTEXT_PACK_VERSION = "context_pack.v1"
MAX_SUPPLIED_EVIDENCE_PER_SOURCE_CLASS = 50
MAX_MCP_CONTEXT_PACK_BYTES = 60_000
_MAX_ADAPTER_EVIDENCE_PER_BUCKET = 20
_MAX_ADAPTER_EXCERPT_CHARS = 400
DART_BUCKETS = (
    "business_report",
    "audit_report",
    "notes",
    "evidence_documents",
    "disclosures",
    "financials",
)
LLM_SOURCE_GUIDANCE = [
    "Cite source_id for every interpretation and keep DART filing evidence as confirmed facts.",
    "Treat company IR as management self-description and web/news as secondary context, never as DART facts.",
    "List conflicting claim keys and unavailable evidence instead of resolving or fabricating them.",
]


def _bounded_text(value: object, *, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _serialized_bytes(value: dict[str, Any]) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def _bounded_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 4:
        return None
    if isinstance(value, str):
        return value[:_MAX_ADAPTER_EXCERPT_CHARS]
    if isinstance(value, list):
        return [
            _bounded_value(item, depth=depth + 1)
            for item in value[:_MAX_ADAPTER_EVIDENCE_PER_BUCKET]
        ]
    if isinstance(value, dict):
        return {
            str(key)[:120]: _bounded_value(item, depth=depth + 1)
            for key, item in list(value.items())[:20]
        }
    return value


def _compact_evidence(record: dict[str, Any]) -> dict[str, Any]:
    metadata = record.get("metadata")
    compact_metadata = {}
    if isinstance(metadata, dict):
        for key, limit in (
            ("bucket", 80),
            ("availability", 80),
            ("rcept_no", 80),
            ("source_document_id", 80),
            ("fs_div", 20),
        ):
            if metadata.get(key) is not None:
                compact_metadata[key] = _bounded_text(metadata[key], limit=limit)
        selection = metadata.get("fs_div_selection")
        if isinstance(selection, dict):
            compact_selection = {
                key: _bounded_text(selection[key], limit=120)
                for key in ("requested", "used", "status")
                if selection.get(key) is not None
            }
            if compact_selection:
                compact_metadata["fs_div_selection"] = compact_selection
    compact_metadata["source_locator"] = _bounded_text(
        record.get("source_id"), limit=300
    )
    return {
        "source_class": record.get("source_class"),
        "source_id": _bounded_text(record.get("source_id"), limit=300),
        "title": _bounded_text(record.get("title"), limit=120) or None,
        "excerpt": _bounded_text(record.get("excerpt"), limit=_MAX_ADAPTER_EXCERPT_CHARS),
        "url": _bounded_text(record.get("url"), limit=500) or None,
        "checksum": _bounded_text(record.get("checksum"), limit=128) or None,
        "claim_key": _bounded_text(record.get("claim_key"), limit=120) or None,
        "metadata": _bounded_value(compact_metadata),
    }


def _hard_minimal_context_pack(payload: dict[str, Any]) -> dict[str, Any]:
    """Last-resort bounded surface retaining one provenance-bearing source per class."""
    subject = payload.get("subject")
    subject_values = subject if isinstance(subject, dict) else {}
    return {
        "schema_version": _bounded_text(payload.get("schema_version"), limit=40),
        "subject": {
            key: _bounded_text(subject_values.get(key), limit=120)
            for key in ("corp_code", "corp_name", "stock_code")
            if subject_values.get(key) is not None
        },
        "year": _bounded_value(payload.get("year")),
        "read_only": bool(payload.get("read_only")),
        "source_precedence": [
            _bounded_text(item, limit=40)
            for item in (payload.get("source_precedence") or [])[:4]
        ],
        "dart_filing": [
            _compact_evidence(record)
            for record in (payload.get("dart_filing") or [])[:1]
            if isinstance(record, dict)
        ],
        "company_ir": [
            _compact_evidence(record)
            for record in (payload.get("company_ir") or [])[:1]
            if isinstance(record, dict)
        ],
        "web_news": [
            _compact_evidence(record)
            for record in (payload.get("web_news") or [])[:1]
            if isinstance(record, dict)
        ],
        "llm_analysis": [],
        "peer_note_comparison": {"truncated": True},
        "missing_evidence": [],
        "conflicts": [],
        "llm_guidance": [],
        "truncation": _truncation(
            applied=True,
            reason="context_pack_output_budget",
        ),
    }


def _truncation(*, applied: bool, reason: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "applied": applied,
        "max_output_bytes": MAX_MCP_CONTEXT_PACK_BYTES,
    }
    if reason is not None:
        result["reason"] = reason
    return result


def _bounded_mcp_context_pack(payload: dict[str, Any]) -> dict[str, Any]:
    """Enforce the public adapter byte budget without changing stored evidence."""
    candidate = {**payload, "truncation": _truncation(applied=False)}
    if _serialized_bytes(candidate) <= MAX_MCP_CONTEXT_PACK_BYTES:
        return candidate
    bounded = {
        "schema_version": payload.get("schema_version"),
        "subject": _bounded_value(payload.get("subject") or {}),
        "year": payload.get("year"),
        "read_only": payload.get("read_only"),
        "source_precedence": payload.get("source_precedence") or [],
        "dart_filing": [
            _compact_evidence(record)
            for record in (payload.get("dart_filing") or [])[:_MAX_ADAPTER_EVIDENCE_PER_BUCKET]
            if isinstance(record, dict)
        ],
        "company_ir": [
            _compact_evidence(record)
            for record in (payload.get("company_ir") or [])[:_MAX_ADAPTER_EVIDENCE_PER_BUCKET]
            if isinstance(record, dict)
        ],
        "web_news": [
            _compact_evidence(record)
            for record in (payload.get("web_news") or [])[:_MAX_ADAPTER_EVIDENCE_PER_BUCKET]
            if isinstance(record, dict)
        ],
        "llm_analysis": _bounded_value(payload.get("llm_analysis") or []),
        "peer_note_comparison": _bounded_value(payload.get("peer_note_comparison")),
        "missing_evidence": _bounded_value(payload.get("missing_evidence") or []),
        "conflicts": _bounded_value(payload.get("conflicts") or []),
        "llm_guidance": _bounded_value(payload.get("llm_guidance") or []),
        "truncation": _truncation(
            applied=True,
            reason="context_pack_output_budget",
        ),
    }
    if _serialized_bytes(bounded) <= MAX_MCP_CONTEXT_PACK_BYTES:
        return bounded
    emergency = {
        "schema_version": payload.get("schema_version"),
        "subject": _bounded_value(payload.get("subject") or {}, depth=2),
        "year": payload.get("year"),
        "read_only": payload.get("read_only"),
        "source_precedence": payload.get("source_precedence") or [],
        "dart_filing": [
            _compact_evidence(record)
            for record in (payload.get("dart_filing") or [])[:5]
            if isinstance(record, dict)
        ],
        "company_ir": [
            _compact_evidence(record)
            for record in (payload.get("company_ir") or [])[:5]
            if isinstance(record, dict)
        ],
        "web_news": [
            _compact_evidence(record)
            for record in (payload.get("web_news") or [])[:5]
            if isinstance(record, dict)
        ],
        "llm_analysis": [],
        "peer_note_comparison": {"truncated": True},
        "missing_evidence": _bounded_value((payload.get("missing_evidence") or [])[:10]),
        "conflicts": _bounded_value((payload.get("conflicts") or [])[:10]),
        "llm_guidance": _bounded_value((payload.get("llm_guidance") or [])[:3]),
        "truncation": _truncation(
            applied=True,
            reason="context_pack_output_budget",
        ),
    }
    if _serialized_bytes(emergency) <= MAX_MCP_CONTEXT_PACK_BYTES:
        return emergency
    hard_minimal = _hard_minimal_context_pack(payload)
    if _serialized_bytes(hard_minimal) <= MAX_MCP_CONTEXT_PACK_BYTES:
        return hard_minimal
    hard_final = {
        "schema_version": "context_pack.v1",
        "year": _bounded_value(payload.get("year")),
        "read_only": True,
        "source_precedence": [],
        "dart_filing": [],
        "company_ir": [],
        "web_news": [],
        "truncation": _truncation(
            applied=True,
            reason="context_pack_output_budget",
        ),
    }
    if _serialized_bytes(hard_final) <= MAX_MCP_CONTEXT_PACK_BYTES:
        return hard_final
    final = {
        "schema_version": "context_pack.v1",
        "read_only": True,
        "truncation": _truncation(
            applied=True,
            reason="context_pack_output_budget",
        ),
    }
    if _serialized_bytes(final) <= MAX_MCP_CONTEXT_PACK_BYTES:
        return final
    return {"truncation": {"applied": True}}


def _local_excerpt(row: dict[str, Any]) -> str:
    for key in ("excerpt", "normalized_text", "body_text", "body", "report_nm", "title"):
        value = _bounded_text(row.get(key), limit=4000)
        if value:
            return value
    return "cached DART evidence without text excerpt"


def _local_evidence(
    bucket: str,
    rows: Iterable[object],
    *,
    corp_code: object,
    year: object,
) -> list[ContextEvidenceV1]:
    evidence: list[ContextEvidenceV1] = []
    for ordinal, raw_row in enumerate(rows):
        if not isinstance(raw_row, dict):
            continue
        row = dict(raw_row)
        receipt = canonical_annual_filing_source_receipt(
            corp_code=corp_code,
            bsns_year=year,
            rcept_no=row.get("rcept_no"),
            source_document_id=row.get("source_document_id"),
            source_type=row.get("source_type"),
        )
        if receipt is None:
            continue
        source_id = _bounded_text(row.get("source_locator"), limit=300)
        if not source_id:
            source_id = f"dart:{bucket}:{ordinal}"
        checksum = _bounded_text(
            row.get("full_text_hash") or row.get("text_hash"), limit=128
        ) or None
        url = _bounded_text(
            row.get("full_text_uri") or row.get("source_storage_uri"), limit=2000
        ) or None
        title = _bounded_text(
            row.get("section_title") or row.get("note_title") or row.get("title") or row.get("report_nm"),
            limit=500,
        ) or None
        claim_key = _bounded_text(
            row.get("claim_key") or row.get("topic") or row.get("section_key"), limit=200
        ) or None
        evidence.append(ContextEvidenceV1(
            source_class="dart_filing",
            source_id=source_id,
            title=title,
            excerpt=_local_excerpt(row),
            url=url,
            checksum=checksum,
            claim_key=claim_key,
            metadata={
                "bucket": bucket,
                "availability": row.get("availability"),
                "rcept_no": receipt,
                "source_document_id": row.get("source_document_id"),
                "fs_div": row.get("fs_div"),
                "fs_div_selection": row.get("fs_div_selection"),
            },
        ))
    return sorted(evidence, key=lambda item: (item.source_id, item.title or ""))


def _dedupe(records: Iterable[ContextEvidenceV1]) -> list[ContextEvidenceV1]:
    seen: set[str] = set()
    result: list[ContextEvidenceV1] = []
    for record in records:
        keys = []
        if record.checksum:
            keys.append(f"checksum:{record.checksum}")
        if record.url:
            keys.append(f"url:{record.url}")
        if not keys:
            keys.append(f"source_id:{record.source_class}:{record.source_id}")
        if any(key in seen for key in keys):
            continue
        seen.update(keys)
        result.append(record)
    return result


def _external_evidence(
    supplied: Iterable[SuppliedExternalEvidenceV1 | dict[str, Any]] | None,
    *,
    expected_source_class: str,
) -> list[ContextEvidenceV1]:
    supplied_items = list(supplied or [])
    if len(supplied_items) > MAX_SUPPLIED_EVIDENCE_PER_SOURCE_CLASS:
        raise ValueError(
            f"{expected_source_class} evidence exceeds maximum "
            f"{MAX_SUPPLIED_EVIDENCE_PER_SOURCE_CLASS} items"
        )
    items = [SuppliedExternalEvidenceV1.model_validate(item) for item in supplied_items]
    wrong_bucket = [item.source_id for item in items if item.source_class != expected_source_class]
    if wrong_bucket:
        raise ValueError(
            f"{expected_source_class} evidence must be labelled {expected_source_class}: {', '.join(sorted(wrong_bucket))}"
        )
    records = sorted(
        (item.as_context_evidence() for item in items),
        key=lambda item: (item.source_id, item.url or "", item.checksum or ""),
    )
    return _dedupe(records)


def _missing_evidence(
    local_context: dict[str, Any],
    supplied: Iterable[MissingEvidenceV1 | dict[str, Any]] | None,
) -> list[MissingEvidenceV1]:
    availability = local_context.get("availability")
    local_missing = []
    if isinstance(availability, dict):
        for bucket in DART_BUCKETS:
            if availability.get(bucket) in {"missing", "unavailable"}:
                local_missing.append(MissingEvidenceV1(
                    evidence_type=bucket,
                    reason="local_dart_cache_unavailable",
                    source_class="dart_filing",
                ))
    caller_missing = [MissingEvidenceV1.model_validate(item) for item in (supplied or [])]
    values = local_missing + caller_missing
    seen: set[tuple[str, str, str | None]] = set()
    result = []
    for item in values:
        key = (item.evidence_type, item.reason, item.source_class)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return sorted(result, key=lambda item: (item.evidence_type, item.source_class or "", item.reason))


def _conflicts(records: list[ContextEvidenceV1]) -> list[SourceConflictV1]:
    by_claim_key: dict[str, list[ContextEvidenceV1]] = {}
    for record in records:
        if record.claim_key:
            by_claim_key.setdefault(record.claim_key, []).append(record)
    conflicts: list[SourceConflictV1] = []
    for claim_key, candidates in sorted(by_claim_key.items()):
        values = {_bounded_text(item.excerpt, limit=4000) for item in candidates}
        source_classes = {item.source_class for item in candidates}
        if len(values) > 1 and len(source_classes) > 1:
            conflicts.append(SourceConflictV1(
                claim_key=claim_key,
                source_ids=[item.source_id for item in candidates],
                source_classes=[item.source_class for item in candidates],
            ))
    return conflicts


def build_context_pack(
    local_dart_context: dict[str, Any],
    *,
    peer_note_comparison: dict[str, Any] | None = None,
    company_ir: Iterable[SuppliedExternalEvidenceV1 | dict[str, Any]] | None = None,
    web_news: Iterable[SuppliedExternalEvidenceV1 | dict[str, Any]] | None = None,
    llm_analysis: Iterable[LLMAnalysisV1 | dict[str, Any]] | None = None,
    missing_evidence: Iterable[MissingEvidenceV1 | dict[str, Any]] | None = None,
) -> ContextPackV1:
    """Combine caller-provided evidence only; this function performs no fetch or write."""
    if not isinstance(local_dart_context, dict):
        raise TypeError("local_dart_context must be a dict")
    subject = dict(local_dart_context.get("subject") or {})
    corp_code = subject.get("corp_code")
    year = local_dart_context.get("year")
    dart_records = _dedupe(
        record
        for bucket in DART_BUCKETS
        for record in _local_evidence(
            bucket,
            local_dart_context.get(bucket) or [],
            corp_code=corp_code,
            year=year,
        )
    )
    ir_records = _external_evidence(company_ir, expected_source_class="company_ir")
    web_records = _external_evidence(web_news, expected_source_class="web_news")
    llm_items = [LLMAnalysisV1.model_validate(item) for item in (llm_analysis or [])]
    all_source_ids = {item.source_id for item in [*dart_records, *ir_records, *web_records]}
    unknown_llm_sources = sorted({source_id for item in llm_items for source_id in item.source_ids} - all_source_ids)
    if unknown_llm_sources:
        raise ValueError(f"unknown source_ids: {', '.join(unknown_llm_sources)}")
    return ContextPackV1(
        subject=subject,
        year=int(year),
        source_precedence=SOURCE_PRECEDENCE,
        dart_filing=dart_records,
        company_ir=ir_records,
        web_news=web_records,
        llm_analysis=llm_items,
        peer_note_comparison=(
            PeerNoteComparisonV1(data=peer_note_comparison)
            if peer_note_comparison is not None
            else None
        ),
        missing_evidence=_missing_evidence(local_dart_context, missing_evidence),
        conflicts=_conflicts([*dart_records, *ir_records, *web_records]),
        llm_guidance=LLM_SOURCE_GUIDANCE,
    )


def build_mcp_context_pack(
    local_dart_context: dict[str, Any],
    **kwargs: Any,
) -> dict[str, Any]:
    """Return a JSON-safe bounded adapter without registering a new MCP tool."""
    payload = build_context_pack(local_dart_context, **kwargs).model_dump(mode="json")
    return _bounded_mcp_context_pack(payload)
