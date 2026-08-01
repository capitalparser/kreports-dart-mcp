"""Read-only, source-separated context packs for LLM-assisted research."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from kreports.mcp.answer_contracts import (
    ContextEvidenceV1,
    ContextPackV1,
    LLMAnalysisV1,
    MissingEvidenceV1,
    PeerNoteComparisonV1,
    SourceConflictV1,
    SuppliedExternalEvidenceV1,
)


CONTEXT_PACK_VERSION = "context_pack.v1"
SOURCE_PRECEDENCE = ["dart_filing", "company_ir", "web_news", "llm_analysis"]
MAX_SUPPLIED_EVIDENCE_PER_SOURCE_CLASS = 50
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


def _local_excerpt(row: dict[str, Any]) -> str:
    for key in ("excerpt", "normalized_text", "body_text", "body", "report_nm", "title"):
        value = _bounded_text(row.get(key), limit=4000)
        if value:
            return value
    return "cached DART evidence without text excerpt"


def _local_evidence(bucket: str, rows: Iterable[object]) -> list[ContextEvidenceV1]:
    evidence: list[ContextEvidenceV1] = []
    for ordinal, raw_row in enumerate(rows):
        if not isinstance(raw_row, dict):
            continue
        row = dict(raw_row)
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
                "rcept_no": row.get("rcept_no"),
                "source_document_id": row.get("source_document_id"),
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
    dart_records = _dedupe(
        record
        for bucket in DART_BUCKETS
        for record in _local_evidence(bucket, local_dart_context.get(bucket) or [])
    )
    ir_records = _external_evidence(company_ir, expected_source_class="company_ir")
    web_records = _external_evidence(web_news, expected_source_class="web_news")
    llm_items = [LLMAnalysisV1.model_validate(item) for item in (llm_analysis or [])]
    all_source_ids = {item.source_id for item in [*dart_records, *ir_records, *web_records]}
    unknown_llm_sources = sorted({source_id for item in llm_items for source_id in item.source_ids} - all_source_ids)
    if unknown_llm_sources:
        raise ValueError(f"unknown source_ids: {', '.join(unknown_llm_sources)}")
    return ContextPackV1(
        subject=dict(local_dart_context.get("subject") or {}),
        year=int(local_dart_context.get("year")),
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
    return build_context_pack(local_dart_context, **kwargs).model_dump(mode="json")
