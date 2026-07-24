import json

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
