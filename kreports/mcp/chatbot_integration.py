"""Install user-first chatbot rendering without changing domain results."""
from __future__ import annotations

from typing import Any

from kreports.analysis.peer_selection_explanation import (
    enrich_peer_selection_explanation,
)
from kreports.mcp.chatbot_company_pagination import (
    paginate_company_view,
    pagination_metadata,
    render_first_page_markdown,
)
from kreports.mcp.chatbot_contracts import build_chatbot_view
from kreports.mcp.chatbot_note_depth import (
    note_resource_actions,
    polish_note_depth_view,
)
from kreports.mcp.chatbot_pack_cleanup import (
    build_clean_user_visualization_pack,
)
from kreports.mcp.chatbot_peer_transparency import (
    polish_peer_selection_transparency,
)
from kreports.mcp.chatbot_user_experience import (
    polish_chatbot_view,
    presentation_metadata,
)


_COMPANY_PAGE_TOOLS = {
    "select_peer_group",
    "search_dataset",
    "compare_peer_accounting_notes",
}


def _synchronize_company_pagination(
    tool_name: str,
    layout: dict[str, Any],
    company_pages: dict[str, Any],
) -> None:
    """Keep auxiliary criteria rows out of company pagination counts."""
    if tool_name not in _COMPANY_PAGE_TOOLS:
        return
    pages = [
        page
        for page in company_pages.get("pages") or []
        if isinstance(page, dict)
    ]
    returned = int(pages[0].get("row_count") or 0) if pages else 0
    pagination = dict(layout.get("pagination") or {})
    offset = int(pagination.get("offset") or 0)
    page_size = int(pagination.get("page_size") or 5)
    total = int(pagination.get("total") or returned)
    has_more = returned > 0 and offset + returned < total
    pagination.update({
        "returned": returned,
        "start": offset + 1 if returned else 0,
        "end": offset + returned,
        "has_more": has_more,
        "next_offset": offset + returned if has_more else None,
        "previous_offset": max(0, offset - page_size) if offset else None,
    })
    layout["pagination"] = pagination


def _chatbot_presentation(
    tool_name: str,
    enriched: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(enriched, dict):
        return enriched

    try:
        enriched = enrich_peer_selection_explanation(enriched)
        base_view = build_chatbot_view(
            tool_name,
            enriched,
        )
        if base_view is None:
            return enriched
        view = polish_chatbot_view(
            tool_name,
            base_view,
            enriched,
        )
        view = paginate_company_view(
            tool_name,
            view,
            enriched,
        )
        view = polish_note_depth_view(
            tool_name,
            view,
            enriched,
        )
        view = polish_peer_selection_transparency(
            tool_name,
            view,
            enriched,
        )
        presentation = dict(enriched)
        presentation["answer"] = render_first_page_markdown(
            view,
            enriched,
        )
        presentation["answer_pack"] = (
            build_clean_user_visualization_pack(
                view,
                enriched,
            )
        )
        meta = dict(presentation.get("_meta") or {})
        meta["presentation_contract"] = (
            "kreports.chatbot.user-first.v1"
        )
        layout = presentation_metadata(
            view,
            enriched,
        )
        company_pages = pagination_metadata(view)
        layout["company_pages"] = company_pages
        _synchronize_company_pagination(
            tool_name,
            layout,
            company_pages,
        )
        selection_explanation = enriched.get(
            "selection_explanation"
        )
        if isinstance(selection_explanation, dict):
            layout["peerSelection"] = selection_explanation
        resource_actions = note_resource_actions(
            tool_name,
            enriched,
        )
        if resource_actions:
            layout["noteResources"] = resource_actions
        meta["presentation_layout"] = layout
        presentation["_meta"] = meta
        return presentation
    except Exception as exc:
        # Presentation must never destroy the underlying evidence result.
        fallback = dict(enriched)
        meta = dict(fallback.get("_meta") or {})
        meta["presentation_error"] = type(exc).__name__
        fallback["_meta"] = meta
        return fallback


def enrich_chatbot_response(
    tool_name: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Build an opt-in user-first chatbot view over the public MCP result."""
    from kreports.mcp.contracts import enrich_answer_response

    return _chatbot_presentation(
        tool_name,
        enrich_answer_response(tool_name, result),
    )


def install_chatbot_enrichment():
    """Return the opt-in renderer without mutating global MCP contracts.

    Kept as a compatibility shim for callers that previously imported the
    installer. The normal MCP server intentionally remains on the canonical
    verdict-first response contract.
    """
    return enrich_chatbot_response
