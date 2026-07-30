import json

import pytest

from kreports.mcp.tools import call_tool
from kreports.mcp.renderers import render_answer


def test_search_dataset_returns_user_facing_narrative():
    out = json.loads(call_tool(
        "search_dataset",
        {
            "dataset": "report_sections",
            "company": "005930",
            "year": 2024,
            "source_type": "audit_report",
            "section_keys": ["kam", "other_matter"],
            "limit": 3,
        },
    ))

    assert isinstance(out.get("answer"), str)
    assert out["answer"].startswith("판정:")
    assert "근거" in out["answer"]
    assert "데이터" in out["answer"]
    assert "삼성전자" in out["answer"] or "005930" in out["answer"]


def test_compare_peer_kam_topics_returns_user_facing_narrative():
    out = json.loads(call_tool("compare_peer_kam_topics", {"company": "005930", "year": 2024, "peer_limit": 5}))

    assert isinstance(out.get("answer"), str)
    assert out["answer"].startswith("판정:")
    assert "핵심감사사항" in out["answer"] or "KAM" in out["answer"]
    assert "데이터" in out["answer"]


def test_build_audit_acceptance_pack_returns_user_facing_narrative():
    out = json.loads(call_tool("build_audit_acceptance_pack", {"company": "005930", "year": 2024, "peer_limit": 5}))

    assert isinstance(out.get("answer"), str)
    assert out["answer"].startswith("판정:")
    assert "수임" in out["answer"] or "감사" in out["answer"]
    assert "근거" in out["answer"]


def test_acceptance_narrative_includes_three_year_scale_table_for_plain_chatbots():
    text = render_answer("build_audit_acceptance_pack", {
        "subject": {"corp_name": "대상회사"},
        "year": 2024,
        "subject_scale_history": [
            {
                "year": 2024,
                "fs_div": "CFS",
                "total_assets_100m": 40_000.0,
                "revenue_100m": 20_000.0,
                "audit_fee_m": 800,
                "audit_hours": 8_000,
                "audit_hours_per_trillion_assets": 2_000.0,
                "audit_hours_per_trillion_revenue": 4_000.0,
                "audit_source_rcept_no": "20250318000123",
                "missing_fields": [],
            },
            {
                "year": 2023,
                "fs_div": "CFS",
                "total_assets_100m": 30_000.0,
                "revenue_100m": 15_000.0,
                "audit_fee_m": 600,
                "audit_hours": 6_000,
                "missing_fields": [],
            },
            {
                "year": 2022,
                "fs_div": "CFS",
                "missing_fields": [
                    "total_assets",
                    "revenue",
                    "audit_fee_m",
                    "audit_hours",
                ],
                "missing_fields_label": "총자산, 매출액, 감사보수, 감사시간",
            },
        ],
        "acceptance_signals": [],
        "data_quality": {"status": "limited"},
        "limitations": [
            "This pack supports acceptance/continuance screening only.",
        ],
    })

    assert text is not None
    assert "### 대상회사 3개년 규모·감사투입 추이" in text
    assert "| 연도 | 재무제표 기준 | 총자산 (억원) | 매출액 (억원)" in text
    assert "| 2024 | CFS | 40000.0 | 20000.0 | 800 | 8000" in text
    assert "총자산, 매출액, 감사보수, 감사시간" in text
    assert "표준감사시간 결론이 아닙니다." in text
    assert "판정: usable" not in text
    assert "판정:\n- limited" in text


def test_search_audit_report_matters_returns_user_facing_narrative():
    out = json.loads(call_tool("search_audit_report_matters", {"company": "005930", "year": 2024, "limit": 3}))

    assert isinstance(out.get("answer"), str)
    assert out["answer"].startswith("판정:")
    assert "감사보고서" in out["answer"]
    assert "데이터" in out["answer"]


def test_get_audit_report_sections_returns_user_facing_narrative():
    out = json.loads(call_tool("get_audit_report_sections", {"company": "005930", "year": 2024, "section_key": "kam"}))

    assert isinstance(out.get("answer"), str)
    assert out["answer"].startswith("판정:")
    assert "근거" in out["answer"]
    assert "KAM" in out["answer"] or "감사절차" in out["answer"]


def test_generic_narrative_uses_professional_sections_without_internal_schema_labels():
    text = render_answer("get_business_overview", {
        "verdict": "conditional",
        "confirmed_facts": [{
            "statement": "사업 내용이 공시에서 확인되었습니다.",
            "source": {"rcept_no": "20250301000001", "source_table": "report_sections"},
        }],
        "analysis": [{"statement": "매출 인식 정책을 추가 검토해야 합니다.", "perspective": "auditor"}],
        "data_quality": {"status": "limited", "coverage_note": "일부 연도는 캐시에 없습니다."},
        "limitations": ["로컬 캐시는 원 공시 전체를 보장하지 않습니다."],
        "next_checks": ["원 공시 본문을 확인하세요."],
    })

    assert text is not None
    for heading in ("판정", "확인된 내용", "분석", "출처", "데이터 한계", "추가 확인사항"):
        assert heading in text
    assert "Fact 1" not in text
    assert "report_sections" not in text
    assert "_meta" not in text


def test_company_search_narrative_names_matches_without_internal_identifier():
    text = render_answer("search_company", {
        "query": "삼성전자",
        "count": 1,
        "results": [{
            "corp_code": "00126380",
            "corp_name": "삼성전자",
            "stock_code": "005930",
            "market": "KOSPI",
        }],
    })

    assert text is not None
    assert text.startswith("판정:")
    assert "삼성전자" in text
    assert "종목코드 005930" in text
    assert "00126380" not in text
    assert "search_company" not in text
    assert "corp_code" not in text


def test_going_concern_narrative_keeps_limited_verdict_and_public_labels():
    text = render_answer("score_going_concern", {
        "corp_code": "00126380",
        "score": 85,
        "grade": "안정",
        "risk": "ok",
        "has_data": True,
        "factors": [{
            "name": "최근 연도 영업CF 음수",
            "hit": True,
            "penalty": 10,
            "detail": "영업CF -100억원",
        }],
    })

    assert text is not None
    assert "판정:\n- limited" in text
    assert "계속기업 위험 스크리닝" in text
    assert "스크리닝 등급: 안정" in text
    assert "최근 연도 영업CF 음수" in text
    assert "감사인의 계속기업 결론을 대체하지 않습니다" in text
    assert "00126380" not in text
    assert "score_going_concern" not in text
    assert "corp_code" not in text
    assert "\"risk\"" not in text


def test_detailed_narrative_hides_internal_answer_pack_and_table_identifiers():
    text = render_answer("compare_to_industry_multi", {
        "subject": {"corp_name": "A"},
        "years": [2025],
        "metrics": ["ROE"],
        "n_peers": 3,
        "results": {2025: {"ROE": {"subject_value": 0.1, "percentile": 50, "p25": 0.05, "p50": 0.1, "p75": 0.15, "n": 3}}},
    })

    assert text is not None
    assert "answer_pack" not in text
    assert "peer_percentile_matrix" not in text


def test_missing_search_cache_suppresses_legacy_zero_count_detail_and_dataset_id():
    text = render_answer("search_dataset", {
        "query": {"dataset": "report_sections"},
        "total_companies": 0,
        "total_records": 0,
        "companies": [],
        "data_quality": {"status": "missing"},
    })

    assert text is not None
    assert "0건이 확인" not in text
    assert "report_sections" not in text


def test_kam_event_summary_never_interpolates_raw_dict_or_list():
    text = render_answer("compare_peer_kam_topics", {
        "subject": {"corp_name": "A"},
        "year": 2025,
        "kam_topics": [],
        "audit_report_events": {"new": ["revenue"], "repeated": 2},
        "data_quality": {"status": "limited"},
    })

    assert text is not None
    assert "{'new'" not in text
    assert "['revenue']" not in text


@pytest.mark.parametrize("tool_name,payload,internal_name", [
    ("search_dataset", {
        "query": {"dataset": "report_sections"},
        "total_companies": 1,
        "total_records": 1,
        "companies": [],
        "data_quality": {"status": "usable", "source": "report_sections"},
    }, "report_sections"),
    ("get_subsidiary_auditors", {
        "subsidiaries": [],
        "data_quality": {"status": "limited", "source": "local_subsidiary_auditor_matrix"},
    }, "local_subsidiary_auditor_matrix"),
    ("get_accounting_policy_changes", {
        "changed_items": [],
        "data_quality": {"status": "limited", "source": "accounting_note_chapters"},
    }, "accounting_note_chapters"),
])
def test_legacy_detail_uses_public_source_labels(tool_name, payload, internal_name):
    text = render_answer(tool_name, payload)

    assert text is not None
    assert internal_name not in text


@pytest.mark.parametrize("tool_name,payload,expected_status", [
    ("get_quality_of_earnings_pack", {"metrics": {"years": 3}}, "limited"),
    ("compare_to_industry_multi", {"results": {2025: {"ROE": {"percentile": 50}}}}, "missing"),
    ("get_subsidiary_auditors", {"consolidated_totals": {"assets_amount_m": 1000}}, "missing"),
])
def test_inferred_sparse_quality_never_has_optimistic_legacy_detail(
    tool_name, payload, expected_status,
):
    text = render_answer(tool_name, payload)

    assert text is not None
    assert "판정: usable" not in text
    if expected_status == "missing":
        assert text.startswith("판정:\n- missing")
    else:
        assert f"세부 결과:\n판정: {expected_status}" in text


def test_dcf_detail_uses_public_assumption_labels_and_safe_basis_text():
    text = render_answer("get_dcf_input_candidates", {
        "candidate_assumptions": {
            "revenue_growth": {"value": 0.1, "basis": "historical_median"},
            "operating_margin": {"value": 0.2},
            "cash_conversion": {"value": 1.1, "basis": "operating_cf_to_net_income"},
        },
        "data_quality": {"status": "usable"},
    })

    assert text is not None
    assert "매출 성장률" in text
    assert "영업이익률" in text
    assert "현금전환" in text
    assert "revenue_growth" not in text
    assert "operating_margin" not in text
    assert "cash_conversion" not in text
    assert "historical_median" not in text
    assert "operating_cf_to_net_income" not in text
    assert "basis 없음" not in text


def test_acceptance_narrative_uses_public_labels_without_approval_or_internal_signal_keys():
    sections = {
        key: {
            "status": "limited", "required": True, "applicability": "applicable",
            "coverage": {}, "blockers": ["audit_effort_helper_not_integrated"], "sources": [],
        }
        for key in (
            "peer_group", "audit_effort", "financial_risk", "audit_history",
            "accounting_policy", "kam", "audit_report_matters",
        )
    }
    text = render_answer("build_audit_acceptance_pack", {
        "subject": {"corp_name": "A"},
        "risk_summary": {"benchmarks": {"accrual_ratio": {"n": 5}}},
        "data_quality": {"status": "limited", "section_statuses": sections},
        "acceptance_signals": [{
            "signal": "audit_report_other_matter_paragraph_present",
            "label": "감사보고서 기타사항 문단이 확인되었습니다.",
        }],
        "next_checks": ["업무상 결정 또는 감사 결론을 제시하지 않습니다."],
    })

    assert text is not None
    assert "감사보고서 기타사항 문단" in text
    assert "kam_body" not in text
    assert "audit_report_other_matter_paragraph_present" not in text
    assert "승인" not in text
    assert "거절" not in text


def test_peer_risk_narrative_maps_internal_metric_keys_to_public_korean_labels():
    text = render_answer("compare_peer_risk_profile", {
        "subject": {"corp_name": "A"},
        "metric_rows": [{
            "metric": "receivables_to_revenue",
            "peer_n": 6,
            "p25": 0.1,
            "p50": 0.2,
            "p75": 0.3,
            "subject_value": 0.25,
            "limitation": None,
        }],
        "benchmarks": {"receivables_to_revenue": {"n": 6}},
        "data_quality": {"status": "usable"},
    })

    assert text is not None
    assert "매출채권/매출" in text
    assert "receivables_to_revenue" not in text
