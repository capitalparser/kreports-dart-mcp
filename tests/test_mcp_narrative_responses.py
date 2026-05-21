import json

from kreports.mcp.tools import call_tool


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
