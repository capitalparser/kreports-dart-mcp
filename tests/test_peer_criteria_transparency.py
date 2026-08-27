from __future__ import annotations


_CUSTOM_CRITERIA = {
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
    "required_features": ["notes"],
    "minimum_coverage": 1.0,
    "weights": {
        "industry": 0.4,
        "size": 0.4,
        "coverage": 0.2,
    },
}


def _peer_result(
    count: int = 12,
    *,
    criteria: dict | None = None,
    mode: str | None = None,
    matched_prefix_len: int = 3,
    fallback_used: bool = False,
) -> dict:
    applied = dict(criteria or _CUSTOM_CRITERIA)
    if mode is not None:
        applied["mode"] = mode
    peers = []
    for index in range(1, count + 1):
        peers.append({
            "corp_code": f"{index:08d}",
            "stock_code": f"{index:06d}",
            "corp_name": f"비교회사 {index}",
            "market": "KOSPI",
            "induty_code": "26410",
            "total_assets": index * 1_000_000_000_000,
            "revenue": index * 500_000_000_000,
            "include_reasons": ["same_ksic_prefix"],
            "reason_components": {
                "industry_match": {
                    "matched": True,
                    "basis": "same_ksic_prefix",
                    "requested_basis": applied.get("industry_basis"),
                    "override": False,
                    "matched_prefix_len": matched_prefix_len,
                },
            },
            "feature_coverage": 1.0,
            "selection_score": round(1.0 - index / 100.0, 4),
        })
    return {
        "subject": {
            "corp_code": "00126380",
            "corp_name": "기준회사",
            "stock_code": "005930",
        },
        "selection_policy": {
            "criteria_requested": applied,
            "criteria_applied": applied,
            "selection_mode": applied.get("mode"),
            "legacy_criteria": False,
            "matched_prefix_len": matched_prefix_len,
            "fallback_used": fallback_used,
            "fs_strategy": "CFS",
            "fs_div_used": "CFS",
            "requested_year": 2024,
            "resolved_year": 2024,
        },
        "statistical_member_count": count,
        "peer_count": count,
        "returned_peer_count": count,
        "confidence": "medium",
        "peers": peers,
        "data_quality": {
            "status": "usable",
            "limitations": [],
        },
    }


def _criterion(explanation: dict, key: str) -> dict:
    return next(
        item
        for item in explanation["applied_criteria"]
        if item["key"] == key
    )


def test_customized_criteria_and_selected_companies_are_shown_together():
    from kreports.analysis.peer_selection_explanation import (
        enrich_peer_selection_explanation,
    )
    from kreports.mcp.chatbot_company_pagination import (
        paginate_company_view,
        pagination_metadata,
        render_first_page_markdown,
    )
    from kreports.mcp.chatbot_contracts import build_chatbot_view
    from kreports.mcp.chatbot_peer_transparency import (
        polish_peer_selection_transparency,
    )
    from kreports.mcp.chatbot_user_experience import polish_chatbot_view

    result = enrich_peer_selection_explanation(_peer_result())
    explanation = result["selection_explanation"]

    assert explanation["criteria_origin"] == "user_customized"
    assert explanation["population"]["eligible_company_count"] == 12
    assert _criterion(explanation, "year")["value"] == "2024년"
    assert _criterion(explanation, "fs_basis")["value"] == "연결재무제표"
    assert "앞 3자리 일치" in _criterion(explanation, "industry")["value"]
    assert "매출" in _criterion(explanation, "size")["value"]
    assert "0.32배~3.16배" in _criterion(explanation, "size")["value"]
    assert "재무제표 주석 원문" in _criterion(
        explanation,
        "required_features",
    )["value"]
    assert explanation["ordering"]["is_relevance_ranking"] is True
    assert "가중치" in explanation["ordering"]["label"]

    base = build_chatbot_view("select_peer_group", result)
    assert base is not None
    polished = polish_chatbot_view("select_peer_group", base, result)
    paged = paginate_company_view("select_peer_group", polished, result)
    transparent = polish_peer_selection_transparency(
        "select_peer_group",
        paged,
        result,
    )
    answer = render_first_page_markdown(transparent, result)
    metadata = pagination_metadata(transparent)

    assert "적용한 비교 기준" in answer
    assert "사용자가 지정한 기준" in answer
    assert "한국표준산업분류 앞 3자리 일치" in answer
    assert "기준회사 매출의 0.32배~3.16배 범위" in answer
    assert "재무제표 주석 원문 중 최소 100% 확보" in answer
    assert "사용자가 지정한 가중치에 따른 기준 적합도 높은 순" in answer
    assert "기준 충족 근거" in answer
    assert "매출 규모 조건 충족" in answer
    assert "요청한 비교자료 100% 확보" in answer
    assert "비교회사 1" in answer
    assert "비교회사 5" in answer
    assert "비교회사 6" not in answer
    assert "업종·규모가 유사" not in answer

    assert metadata["page_count"] == 3
    assert metadata["loaded_company_count"] == 12
    assert metadata["auxiliary_table_ids"] == ["peer_applied_criteria"]


def test_non_ranked_result_does_not_claim_relevance_order():
    from kreports.analysis.peer_selection_explanation import (
        enrich_peer_selection_explanation,
    )

    criteria = {
        **_CUSTOM_CRITERIA,
        "mode": "adaptive",
        "weights": {},
    }
    result = enrich_peer_selection_explanation(
        _peer_result(7, criteria=criteria)
    )
    ordering = result["selection_explanation"]["ordering"]

    assert ordering["is_relevance_ranking"] is False
    assert ordering["label"] == "선택한 조건을 충족한 회사 중 총자산이 큰 순"
    assert "관련성" not in ordering["label"]
    assert "관련성 점수가 아니라 화면 표시 순서" in ordering["detail"]


def test_fallback_and_unsupported_requested_criteria_are_explicit():
    from kreports.analysis.peer_selection_explanation import (
        enrich_peer_selection_explanation,
    )

    criteria = {
        **_CUSTOM_CRITERIA,
        "mode": "adaptive",
        "prefix_len": 3,
        "fallback_prefix_len": 2,
        "size_metric": "employees",
        "size_log10_tolerance": 0.5,
        "required_business_tags": ["semiconductor"],
        "weights": {},
    }
    result = enrich_peer_selection_explanation(
        _peer_result(
            0,
            criteria=criteria,
            matched_prefix_len=2,
            fallback_used=True,
        )
    )
    explanation = result["selection_explanation"]

    assert "요청 3자리에서 2자리로 확대" in _criterion(
        explanation,
        "industry",
    )["value"]
    assert _criterion(explanation, "size")["status"] == "unsupported"
    assert _criterion(
        explanation,
        "business_tags",
    )["status"] == "unsupported"
    assert any("종업원 수 비교자료" in item for item in explanation["limitations"])
    assert any("사업 내용 태그 색인" in item for item in explanation["limitations"])


def test_nested_peer_analysis_reuses_the_same_selection_explanation():
    from kreports.analysis.peer_selection_explanation import (
        enrich_peer_selection_explanation,
    )

    peer_group = _peer_result(5)
    result = {
        "subject": peer_group["subject"],
        "peer_count": 5,
        "peer_group": peer_group,
        "results": {
            2024: {
                "영업이익률": {
                    "subject_value": 12.0,
                    "p50": 8.0,
                    "percentile": 75.0,
                    "n": 5,
                    "coverage_pct": 100.0,
                    "unit": "%",
                },
            },
        },
        "data_quality": {
            "status": "usable",
            "limitations": [],
        },
    }

    enriched = enrich_peer_selection_explanation(result)

    assert enriched["selection_explanation"] == enriched[
        "peer_group"
    ]["selection_explanation"]
    assert enriched["selection_explanation"]["population"][
        "eligible_company_count"
    ] == 5
