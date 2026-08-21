"""Five-company page packaging for internal chatbot answer packs.

The plain Markdown answer shows auxiliary explanation tables plus only the first
five-company page. The structured answer pack keeps bounded five-company pages
so a capable chatbot UI can provide Next/Previous controls without repeating
the analytical tool call.
"""
from __future__ import annotations

import re
from typing import Any

from kreports.mcp.chatbot_contracts import (
    ChatbotTableV1,
    ChatbotViewV1,
)
from kreports.mcp.chatbot_user_experience import (
    COMPANY_PAGE_SIZE,
    _FS_SELECTION_LABELS,
    _format_amount,
    _fs_div_label,
    _reason_label,
    _receipt_link,
    _topic_label,
    render_user_markdown,
)


_MAX_PAGES = 8
_COMPANY_TOOLS = {
    "select_peer_group",
    "search_dataset",
    "compare_peer_accounting_notes",
}
_AUXILIARY_TABLE_IDS = {
    "peer_applied_criteria",
}
_PAGE_TABLE_RE = re.compile(r"_page_[0-9]+$", re.ASCII)


def _is_auxiliary_table(table: ChatbotTableV1) -> bool:
    return table.id in _AUXILIARY_TABLE_IDS


def _is_page_table(table: ChatbotTableV1) -> bool:
    return bool(_PAGE_TABLE_RE.search(str(table.id or "")))


def _has_peer_selection_explanation(result: dict[str, Any]) -> bool:
    if isinstance(result.get("selection_explanation"), dict):
        return True
    peer_group = result.get("peer_group")
    return (
        isinstance(peer_group, dict)
        and isinstance(peer_group.get("selection_explanation"), dict)
    )


def _chunks(
    rows: list[dict[str, Any]],
    *,
    max_pages: int,
) -> list[list[dict[str, Any]]]:
    return [
        rows[index:index + COMPANY_PAGE_SIZE]
        for index in range(
            0,
            min(len(rows), COMPANY_PAGE_SIZE * max_pages),
            COMPANY_PAGE_SIZE,
        )
    ]


def _peer_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for peer in result.get("peers") or []:
        if not isinstance(peer, dict):
            continue
        reasons: list[str] = []
        for reason in peer.get("include_reasons") or []:
            label = _reason_label(reason)
            if label not in reasons:
                reasons.append(label)
        rows.append({
            "company": peer.get("corp_name") or peer.get("corp_code"),
            "stock_code": peer.get("stock_code") or "-",
            "market": peer.get("market") or "-",
            "total_assets": _format_amount(peer.get("total_assets")),
            "revenue": _format_amount(peer.get("revenue")),
            "reason": ", ".join(reasons[:3]) or "선정 기준 충족",
            "source": _receipt_link(
                peer.get("rcept_no") or peer.get("source_rcept_no")
            ) or "-",
        })
    return rows


def _note_search_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    keyword = str((result.get("query") or {}).get("keyword") or "검색 문구")
    rows: list[dict[str, Any]] = []
    for company in result.get("companies") or []:
        if not isinstance(company, dict):
            continue
        records = [
            record
            for record in company.get("records") or []
            if isinstance(record, dict)
        ]
        record = records[0] if records else {}
        rows.append({
            "company": company.get("corp_name") or company.get("corp_code"),
            "year": record.get("year") or "-",
            "fs_div": _fs_div_label(record.get("fs_div")),
            "note_title": record.get("note_title") or record.get("note_no") or "-",
            "matched_term": record.get("matched_term") or keyword,
            "excerpt": record.get("body_excerpt") or "관련 문구 미확보",
            "source": _receipt_link(record.get("rcept_no")) or "-",
        })
    return rows


def _note_comparison_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    topics = [
        topic
        for topic in result.get("topics") or []
        if isinstance(topic, dict)
    ]
    differences = [
        item
        for item in result.get("differences") or []
        if isinstance(item, dict)
    ]
    subject_code = str((result.get("subject") or {}).get("corp_code") or "")
    companies: dict[str, dict[str, Any]] = {}

    for topic in topics:
        topic_key = str(topic.get("topic") or "")
        for row in topic.get("rows") or []:
            if not isinstance(row, dict):
                continue
            company = row.get("company") or {}
            code = str(company.get("corp_code") or "")
            if not code or code == subject_code:
                continue
            item = companies.setdefault(code, {
                "company": company.get("corp_name") or code,
                "available_topics": 0,
                "different_topics": [],
                "basis_notes": [],
                "rcept_no": None,
            })
            if str(row.get("availability") or "") in {
                "available",
                "summary_only",
            }:
                item["available_topics"] += 1
            selection = row.get("fs_div_selection") or {}
            status = (
                str(selection.get("status") or "")
                if isinstance(selection, dict)
                else ""
            )
            label = _FS_SELECTION_LABELS.get(status)
            if label and label not in item["basis_notes"]:
                item["basis_notes"].append(label)
            if row.get("rcept_no") and not item["rcept_no"]:
                item["rcept_no"] = row.get("rcept_no")

        for difference in differences:
            if str(difference.get("topic") or "") != topic_key:
                continue
            code = str(difference.get("peer_corp_code") or "")
            if code not in companies:
                continue
            label = _topic_label(topic_key)
            if label not in companies[code]["different_topics"]:
                companies[code]["different_topics"].append(label)

    rows: list[dict[str, Any]] = []
    for item in companies.values():
        rows.append({
            "company": item["company"],
            "different_topics": (
                ", ".join(item["different_topics"])
                if item["different_topics"]
                else "뚜렷한 문구 차이 없음"
            ),
            "available_topics": f"{item['available_topics']}개 주제 확인",
            "basis": ", ".join(item["basis_notes"]) or "확인 필요",
            "source": _receipt_link(item["rcept_no"]) or "-",
        })
    return rows


def _page_tables(
    base_table: ChatbotTableV1,
    rows: list[dict[str, Any]],
    *,
    total_count: int,
    start_offset: int,
    max_pages: int,
) -> list[ChatbotTableV1]:
    pages = _chunks(rows, max_pages=max_pages)
    if not pages:
        return [base_table.model_copy(update={"rows": []})]
    loaded_count = len(rows)
    packaged_count = sum(len(page) for page in pages)
    tables: list[ChatbotTableV1] = []
    for page_index, page_rows in enumerate(pages, start=1):
        start = start_offset + (page_index - 1) * COMPANY_PAGE_SIZE + 1
        end = start + len(page_rows) - 1
        if total_count > packaged_count + start_offset:
            scope_note = (
                f"전체 {total_count}개 중 현재 포장한 {packaged_count}개를 "
                "5개씩 나눠 보여드립니다."
            )
        elif total_count > loaded_count + start_offset:
            scope_note = (
                f"전체 {total_count}개 중 현재 불러온 {loaded_count}개를 "
                "5개씩 나눠 보여드립니다."
            )
        else:
            scope_note = f"전체 {total_count}개를 5개씩 나눠 보여드립니다."
        tables.append(base_table.model_copy(update={
            "id": f"{base_table.id}_page_{page_index}",
            "title": f"{base_table.title.split(' · ')[0]} · {start}~{end}",
            "rows": page_rows,
            "note": (
                f"{scope_note} 현재 화면은 {start}~{end}번째 회사이며, "
                "이전·다음 방식으로 이동할 수 있습니다."
            ),
        }))
    return tables


def paginate_company_view(
    tool_name: str,
    view: ChatbotViewV1,
    result: dict[str, Any],
) -> ChatbotViewV1:
    """Keep every loaded company in bounded five-row structured pages."""
    if tool_name not in _COMPANY_TOOLS or not view.tables:
        return view
    if (
        tool_name == "search_dataset"
        and (result.get("query") or {}).get("dataset")
        != "accounting_note_chapters"
    ):
        return view

    if tool_name == "select_peer_group":
        rows = _peer_rows(result)
        total_count = int(
            result.get("statistical_member_count")
            or result.get("peer_count")
            or len(rows)
        )
        start_offset = 0
    elif tool_name == "search_dataset":
        rows = _note_search_rows(result)
        total_count = int(
            result.get("matched_company_count")
            or result.get("total_companies")
            or len(rows)
        )
        start_offset = int((result.get("query") or {}).get("offset") or 0)
    else:
        rows = _note_comparison_rows(result)
        pagination = result.get("pagination") or {}
        total_count = int(pagination.get("total_peer_count") or len(rows))
        start_offset = int(pagination.get("offset") or 0)

    # ChatbotViewV1 currently allows eight tables. Reserve one table for the
    # applied-criteria explanation when a peer-selection explanation is present.
    max_pages = (
        _MAX_PAGES - 1
        if _has_peer_selection_explanation(result)
        else _MAX_PAGES
    )
    tables = _page_tables(
        view.tables[0],
        rows,
        total_count=total_count,
        start_offset=start_offset,
        max_pages=max_pages,
    )
    return view.model_copy(update={
        "tables": tables,
        "initially_visible_rows": COMPANY_PAGE_SIZE,
    })


def _first_visible_tables(view: ChatbotViewV1) -> list[ChatbotTableV1]:
    """Return explanation tables plus the first analytical/content table."""
    auxiliary = [
        table for table in view.tables
        if _is_auxiliary_table(table)
    ]
    content = [
        table for table in view.tables
        if not _is_auxiliary_table(table)
    ]
    page_tables = [
        table for table in content
        if _is_page_table(table)
    ]
    primary = page_tables[:1] if page_tables else content[:1]
    return [*auxiliary, *primary]


def render_first_page_markdown(
    view: ChatbotViewV1,
    result: dict[str, Any],
) -> str:
    """Render criteria/explanation tables and only the first content page."""
    first_page = view.model_copy(update={
        "tables": _first_visible_tables(view),
    })
    return render_user_markdown(first_page, result)


def pagination_metadata(view: ChatbotViewV1) -> dict[str, Any]:
    """UI-safe page metadata that excludes auxiliary explanation tables."""
    auxiliary = [
        table for table in view.tables
        if _is_auxiliary_table(table)
    ]
    content = [
        table for table in view.tables
        if not _is_auxiliary_table(table)
    ]
    page_tables = [
        table for table in content
        if _is_page_table(table)
    ]
    effective_tables = page_tables or content
    pages = [
        {
            "page": index,
            "table_id": table.id,
            "title": table.title,
            "row_count": len(table.rows),
        }
        for index, table in enumerate(effective_tables, start=1)
    ]
    return {
        "page_size": COMPANY_PAGE_SIZE,
        "page_count": len(pages),
        "loaded_company_count": sum(page["row_count"] for page in pages),
        "pages": pages,
        "auxiliary_table_ids": [table.id for table in auxiliary],
    }
