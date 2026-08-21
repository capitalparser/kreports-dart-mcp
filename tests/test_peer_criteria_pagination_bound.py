from __future__ import annotations


def test_criteria_table_reserves_one_chatbot_table_slot():
    from kreports.analysis.peer_selection_explanation import (
        enrich_peer_selection_explanation,
    )
    from kreports.mcp.chatbot_company_pagination import (
        paginate_company_view,
        pagination_metadata,
    )
    from kreports.mcp.chatbot_contracts import build_chatbot_view
    from kreports.mcp.chatbot_peer_transparency import (
        polish_peer_selection_transparency,
    )
    from kreports.mcp.chatbot_user_experience import polish_chatbot_view

    criteria = {
        "mode": "ranked",
        "industry_basis": "ksic",
        "prefix_len": 3,
        "fallback_prefix_len": 2,
        "excluded_sector_groups": [],
        "size_metric": "revenue",
        "size_log10_tolerance": 0.5,
        "required_business_tags": [],
        "excluded_corp_codes": [],
        "included_corp_codes": [],
        "required_features": [],
        "minimum_coverage": 0.0,
        "weights": {"industry": 1.0},
    }
    peers = [
        {
            "corp_code": f"{index:08d}",
            "stock_code": f"{index:06d}",
            "corp_name": f"비교회사 {index}",
            "market": "KOSPI",
            "induty_code": "26410",
            "total_assets": index * 1_000_000_000,
            "revenue": index * 800_000_000,
            "include_reasons": ["same_ksic_prefix"],
            "reason_components": {
                "industry_match": {
                    "matched": True,
                    "override": False,
                },
            },
            "selection_score": 1.0,
        }
        for index in range(1, 41)
    ]
    result = enrich_peer_selection_explanation({
        "subject": {
            "corp_code": "00126380",
            "corp_name": "기준회사",
        },
        "selection_policy": {
            "criteria_requested": criteria,
            "criteria_applied": criteria,
            "selection_mode": "ranked",
            "legacy_criteria": False,
            "matched_prefix_len": 3,
            "fallback_used": False,
            "fs_div_used": "CFS",
            "requested_year": 2024,
            "resolved_year": 2024,
        },
        "statistical_member_count": 40,
        "peer_count": 40,
        "returned_peer_count": 40,
        "confidence": "high",
        "peers": peers,
        "data_quality": {
            "status": "usable",
            "limitations": [],
        },
    })

    base = build_chatbot_view("select_peer_group", result)
    assert base is not None
    polished = polish_chatbot_view("select_peer_group", base, result)
    paged = paginate_company_view("select_peer_group", polished, result)
    transparent = polish_peer_selection_transparency(
        "select_peer_group",
        paged,
        result,
    )
    metadata = pagination_metadata(transparent)

    assert len(transparent.tables) == 8
    assert transparent.tables[0].id == "peer_applied_criteria"
    assert metadata["page_count"] == 7
    assert metadata["loaded_company_count"] == 35
    assert metadata["auxiliary_table_ids"] == ["peer_applied_criteria"]


def test_generic_pagination_uses_company_rows_not_criteria_rows():
    from kreports.mcp.chatbot_integration import (
        _synchronize_company_pagination,
    )

    layout = {
        "pagination": {
            "offset": 5,
            "page_size": 5,
            "total": 12,
            # This incorrect value simulates the criteria table being larger
            # than the current company page before synchronization.
            "returned": 5,
            "start": 6,
            "end": 10,
            "has_more": True,
            "next_offset": 10,
            "previous_offset": 0,
        },
    }
    company_pages = {
        "pages": [{
            "page": 1,
            "table_id": "peer_members_page_1",
            "title": "비교회사 6~7",
            "row_count": 2,
        }],
    }

    _synchronize_company_pagination(
        "select_peer_group",
        layout,
        company_pages,
    )

    assert layout["pagination"]["returned"] == 2
    assert layout["pagination"]["start"] == 6
    assert layout["pagination"]["end"] == 7
    assert layout["pagination"]["has_more"] is True
    assert layout["pagination"]["next_offset"] == 7
