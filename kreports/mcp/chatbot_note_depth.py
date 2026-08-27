"""Source-first accounting-note presentation for the internal chatbot.

The canonical note-evidence service owns note references, text recovery, and
optional evidence facets. This adapter does not grade, summarize, or rewrite a
company's disclosure. It presents the company's actual wording, states whether
the excerpt comes from complete or partial stored note text, and exposes
application-only actions for the related paragraph, complete note pages, and
original DART filing.
"""
from __future__ import annotations

from typing import Any

from kreports.mcp.chatbot_contracts import (
    ChatbotColumnV1,
    ChatbotTableV1,
    ChatbotViewV1,
)


_MAX_ACTIONS = 40
_DART_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="


def _safe(value: Any, *, limit: int = 1_200) -> str:
    """Normalize spacing only and visibly mark bounded source text."""
    normalized = " ".join(str(value or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip() + " …"


def _search_evidence_by_company(
    result: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for company in result.get("companies") or []:
        if not isinstance(company, dict):
            continue
        name = str(
            company.get("corp_name")
            or company.get("corp_code")
            or ""
        )
        records = [
            record
            for record in company.get("records") or []
            if isinstance(record, dict)
        ]
        if name and records:
            mapping[name] = records[0]
    return mapping


def _text_scope_label(record: dict[str, Any]) -> str:
    text_meta = record.get("text")
    if not isinstance(text_meta, dict):
        text_meta = {}
    completeness = str(
        record.get("text_completeness")
        or text_meta.get("completeness")
        or ""
    ).lower()
    if completeness == "complete":
        return "전체 주석에서 발췌"
    if completeness == "partial":
        return "일부 저장 문구에서 발췌 · 전체 주석 확인 필요"
    if completeness == "missing":
        return "현재 확보된 원문 없음"

    availability = str(record.get("availability") or "").lower()
    if availability == "unavailable":
        return "현재 확보된 원문 없음"
    if availability == "summary_only":
        return "일부 저장 문구에서 발췌 · 전체 주석 확인 필요"
    if record.get("raw_text_truncated") or record.get(
        "comparison_text_truncated"
    ):
        return "일부 저장 문구에서 발췌 · 전체 주석 확인 필요"
    return "현재 확보된 문구에서 발췌"


def _source_text(record: dict[str, Any], fallback: Any = None) -> str:
    for key in (
        "related_paragraph",
        "body_excerpt",
        "raw_text",
        "value_or_excerpt",
        "comparison_text",
    ):
        value = record.get(key)
        if value:
            return _safe(value)
    return _safe(fallback or "관련 원문 미확보")


def _comparison_source_by_company(
    result: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Index existing comparison evidence without recalculating semantics."""
    mapping: dict[str, dict[str, Any]] = {}
    for topic in result.get("topics") or []:
        if not isinstance(topic, dict):
            continue
        topic_name = str(topic.get("topic") or "주석")
        for record in topic.get("rows") or []:
            if not isinstance(record, dict):
                continue
            company = record.get("company") or {}
            keys = [
                str(value)
                for value in (
                    company.get("corp_name"),
                    company.get("corp_code"),
                )
                if value
            ]
            if not keys:
                continue
            source = {
                "topic": topic_name,
                "note_title": (
                    record.get("note_title")
                    or record.get("note_no")
                    or topic_name
                ),
                "source_text": _source_text(record),
                "text_scope": _text_scope_label(record),
                "rcept_no": record.get("rcept_no"),
                "source_url": record.get("source_url"),
            }
            for key in keys:
                existing = mapping.get(key)
                if existing is None or (
                    existing.get("source_text") == "관련 원문 미확보"
                    and source["source_text"] != "관련 원문 미확보"
                ):
                    mapping[key] = source
    return mapping


def _note_search_view(
    view: ChatbotViewV1,
    result: dict[str, Any],
) -> ChatbotViewV1:
    evidence_by_company = _search_evidence_by_company(result)
    updated_tables: list[ChatbotTableV1] = []

    for table in view.tables:
        rows: list[dict[str, Any]] = []
        for raw_row in table.rows:
            company = str(raw_row.get("company") or "")
            evidence = evidence_by_company.get(company, {})
            matched = str(
                raw_row.get("matched_term")
                or evidence.get("matched_term")
                or "-"
            )
            year = raw_row.get("year") or "-"
            fs_div = raw_row.get("fs_div") or "미확정"
            title = raw_row.get("note_title") or "주석"
            rows.append({
                "company": company or "-",
                "note_basis": f"{year}년 {fs_div} · {title}",
                "actual_expression": matched,
                "source_text": _source_text(
                    evidence,
                    fallback=raw_row.get("excerpt"),
                ),
                "text_scope": _text_scope_label(evidence),
                "source": raw_row.get("source") or "-",
            })
        updated_tables.append(
            ChatbotTableV1(
                id=table.id,
                title=table.title,
                columns=[
                    ChatbotColumnV1(
                        key="company",
                        label="회사",
                    ),
                    ChatbotColumnV1(
                        key="note_basis",
                        label="주석·기준",
                    ),
                    ChatbotColumnV1(
                        key="actual_expression",
                        label="실제 사용 표현",
                    ),
                    ChatbotColumnV1(
                        key="source_text",
                        label="실제 공시 문구",
                    ),
                    ChatbotColumnV1(
                        key="text_scope",
                        label="원문 확인 범위",
                    ),
                    ChatbotColumnV1(
                        key="source",
                        label="원 공시",
                    ),
                ],
                rows=rows,
                note=(
                    "표에는 관련 부분만 발췌합니다. 공백과 줄바꿈만 정리하며, "
                    "회사의 표현을 표준 문장으로 다시 작성하지 않습니다."
                ),
            )
        )

    next_actions = list(view.next_actions)
    for action in (
        "특정 회사의 관련 문단을 보여줘.",
        "특정 회사의 주석 전체를 열어줘.",
        "선택한 회사들의 금액·조건·기간 관련 원문만 비교해줘.",
    ):
        if action not in next_actions:
            next_actions.append(action)
    return view.model_copy(update={
        "summary": (
            f"{view.summary} 아래에는 회사가 실제 공시에서 사용한 표현과 "
            "원문 발췌를 보여드립니다."
        ),
        "tables": updated_tables,
        "next_actions": next_actions[:6],
    })


def _note_comparison_view(
    view: ChatbotViewV1,
    result: dict[str, Any],
) -> ChatbotViewV1:
    source_by_company = _comparison_source_by_company(result)
    updated_tables: list[ChatbotTableV1] = []

    for table in view.tables:
        rows: list[dict[str, Any]] = []
        for raw_row in table.rows:
            company = str(raw_row.get("company") or "")
            source = source_by_company.get(company, {})
            rows.append({
                "company": company or "-",
                "note_title": (
                    source.get("note_title")
                    or raw_row.get("different_topics")
                    or "주석"
                ),
                "source_text": source.get("source_text") or "관련 원문 미확보",
                "comparison_status": (
                    raw_row.get("different_topics")
                    or "현재 확인된 문구에서 뚜렷한 차이 없음"
                ),
                "text_scope": source.get("text_scope") or "확인 필요",
                "basis": raw_row.get("basis") or "확인 필요",
                "source": raw_row.get("source") or "-",
            })
        updated_tables.append(
            ChatbotTableV1(
                id=table.id,
                title=table.title,
                columns=[
                    ChatbotColumnV1(
                        key="company",
                        label="회사",
                    ),
                    ChatbotColumnV1(
                        key="note_title",
                        label="대표 주석",
                    ),
                    ChatbotColumnV1(
                        key="source_text",
                        label="실제 공시 문구",
                    ),
                    ChatbotColumnV1(
                        key="comparison_status",
                        label="기준회사와 표현 차이",
                    ),
                    ChatbotColumnV1(
                        key="text_scope",
                        label="원문 확인 범위",
                    ),
                    ChatbotColumnV1(
                        key="basis",
                        label="재무제표 기준",
                    ),
                    ChatbotColumnV1(
                        key="source",
                        label="원 공시",
                    ),
                ],
                rows=rows,
                note=(
                    "회사별 실제 공시 문구의 관련 부분을 나란히 보여드립니다. "
                    "문구가 다르다는 사실만으로 회계정책이나 회계처리가 다르다고 "
                    "판단하지 않습니다."
                ),
            )
        )

    next_actions = list(view.next_actions)
    for action in (
        "회사별 관련 문단을 나란히 보여줘.",
        "선택한 회사의 주석 전체를 열어줘.",
        "금액·조건·기간처럼 내가 지정한 항목의 원문만 비교해줘.",
    ):
        if action not in next_actions:
            next_actions.append(action)
    return view.model_copy(update={
        "summary": (
            f"{view.summary} 아래에는 회사별 실제 공시 문구의 관련 부분을 "
            "나란히 보여드립니다."
        ),
        "tables": updated_tables,
        "next_actions": next_actions[:6],
    })


def polish_note_depth_view(
    tool_name: str,
    view: ChatbotViewV1,
    result: dict[str, Any],
) -> ChatbotViewV1:
    """Apply the source-first note view after generic five-company paging."""
    if (
        tool_name == "search_dataset"
        and (result.get("query") or {}).get("dataset")
        == "accounting_note_chapters"
    ):
        return _note_search_view(view, result)
    if tool_name == "compare_peer_accounting_notes":
        return _note_comparison_view(view, result)
    return view


def _canonical_filing_url(record: dict[str, Any]) -> tuple[str | None, str | None]:
    receipt = str(record.get("rcept_no") or "")
    source_url = record.get("source_url")
    if source_url:
        return str(source_url), receipt or None
    if len(receipt) == 14 and receipt.isdigit():
        return f"{_DART_URL}{receipt}", receipt
    return None, receipt or None


def note_resource_actions(
    tool_name: str,
    result: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return application-only actions; never render resource URIs as prose."""
    actions: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(
        *,
        company: Any,
        topic: Any,
        record: dict[str, Any],
    ) -> None:
        note_ref = str(record.get("note_ref") or "")
        if not note_ref or note_ref in seen or len(actions) >= _MAX_ACTIONS:
            return
        seen.add(note_ref)
        source_url, receipt = _canonical_filing_url(record)
        actions.append({
            "company": _safe(company, limit=160),
            "topic": _safe(topic, limit=160),
            "noteRef": note_ref,
            "relatedParagraph": {
                "label": "관련 문단",
                "resourceUri": record.get(
                    "paragraph_resource_uri"
                ),
            },
            "fullNote": {
                "label": "주석 전체",
                "resourceUri": record.get(
                    "full_note_resource_uri"
                ),
            },
            "filing": {
                "label": "원 공시",
                "url": source_url,
                "receiptNumber": receipt,
            },
        })

    if (
        tool_name == "search_dataset"
        and (result.get("query") or {}).get("dataset")
        == "accounting_note_chapters"
    ):
        for company in result.get("companies") or []:
            if not isinstance(company, dict):
                continue
            for record in company.get("records") or []:
                if isinstance(record, dict):
                    add(
                        company=(
                            company.get("corp_name")
                            or company.get("corp_code")
                        ),
                        topic=(
                            record.get("note_title")
                            or record.get("note_no")
                        ),
                        record=record,
                    )
                    break
    elif tool_name == "compare_peer_accounting_notes":
        for topic in result.get("topics") or []:
            if not isinstance(topic, dict):
                continue
            for row in topic.get("rows") or []:
                if not isinstance(row, dict):
                    continue
                company = row.get("company") or {}
                add(
                    company=(
                        company.get("corp_name")
                        or company.get("corp_code")
                    ),
                    topic=topic.get("topic"),
                    record=row,
                )
    return actions


__all__ = [
    "note_resource_actions",
    "polish_note_depth_view",
]
