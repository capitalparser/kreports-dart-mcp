import json

from kreports.analysis.api import (
    build_audit_acceptance_pack,
    compare_peer_accounting_policies,
    compare_peer_audit_fees,
    compare_peer_audit_report_matters,
    compare_peer_kam_topics,
    compare_peer_risk_profile,
    estimate_audit_hours_proxy,
    search_dataset,
    search_audit_report_matters,
)
from kreports.mcp.tools import call_tool


def test_compare_peer_audit_fees_real_db_shape():
    out = compare_peer_audit_fees("005930", year=2025, peer_limit=20)
    assert out["subject"]["corp_code"] == "00126380"
    assert out["year"] == 2025
    assert out["peer_count"] > 0
    assert "audit_fee_m" in out["subject_metrics"]
    assert "audit_fee_to_assets_bps" in out["benchmarks"]


def test_compare_peer_audit_fees_mcp_dispatch():
    out = json.loads(call_tool("compare_peer_audit_fees", {"company": "005930", "year": 2025}))
    assert out["_meta"]["tool"] == "compare_peer_audit_fees"
    assert out["peer_count"] > 0


def test_compare_peer_risk_profile_shape():
    out = compare_peer_risk_profile("005930", year=2025, peer_limit=20)
    assert out["subject"]["corp_code"] == "00126380"
    assert "receivables_to_revenue" in out["benchmarks"]
    assert "disclosure_event_counts" in out


def test_compare_peer_risk_profile_mcp_dispatch():
    out = json.loads(call_tool("compare_peer_risk_profile", {"company": "005930", "year": 2025}))
    assert out["_meta"]["tool"] == "compare_peer_risk_profile"
    assert out["peer_count"] > 0


def test_compare_peer_accounting_policies_shape():
    out = compare_peer_accounting_policies("005930", year=2025, peer_limit=20)
    assert out["subject"]["corp_code"] == "00126380"
    assert "subject_policy_count" in out
    assert "peer_item_coverage" in out
    assert "coverage_note" in out


def test_compare_peer_accounting_policies_mcp_dispatch():
    out = json.loads(call_tool("compare_peer_accounting_policies", {"company": "005930", "year": 2025}))
    assert out["_meta"]["tool"] == "compare_peer_accounting_policies"
    assert "peer_item_coverage" in out


def test_compare_peer_kam_topics_shape():
    out = compare_peer_kam_topics("005930", year=2025, peer_limit=20)
    assert out["subject"]["corp_code"] == "00126380"
    assert "audit_report_events" in out
    assert "kam_topics" in out
    assert out["limitations"]


def test_compare_peer_kam_topics_mcp_dispatch():
    out = json.loads(call_tool("compare_peer_kam_topics", {"company": "005930", "year": 2025}))
    assert out["_meta"]["tool"] == "compare_peer_kam_topics"
    assert "audit_report_events" in out


def test_compare_peer_audit_report_matters_shape():
    out = compare_peer_audit_report_matters("005930", year=2024, peer_limit=20)
    assert out["subject"]["corp_code"] == "00126380"
    assert "matter_counts" in out
    assert "other_matter" in out["matter_counts"]
    assert "emphasis" in out["matter_counts"]
    assert "going_concern" in out["matter_counts"]
    assert "subject_matters" in out
    assert "data_quality" in out


def test_compare_peer_audit_report_matters_mcp_dispatch():
    out = json.loads(call_tool("compare_peer_audit_report_matters", {"company": "005930", "year": 2024}))
    assert out["_meta"]["tool"] == "compare_peer_audit_report_matters"
    assert "matter_counts" in out


def test_search_audit_report_matters_company_question_shape():
    out = search_audit_report_matters(company="005930", year=2024, section_keys=["other_matter"], limit=20)
    assert out["query"]["company"] == "005930"
    assert out["query"]["year"] == 2024
    assert out["data_quality"]["source"] == "report_sections.audit_report"
    assert "companies" in out
    assert out["total_companies"] >= 0
    if out["companies"]:
        first = out["companies"][0]
        assert first["corp_code"] == "00126380"
        assert "matter_counts" in first
        assert "sections" in first


def test_search_audit_report_matters_industry_question_mcp_dispatch():
    out = json.loads(call_tool(
        "search_audit_report_matters",
        {
            "year": 2024,
            "market": "KOSPI",
            "induty_prefix": "26",
            "section_keys": ["emphasis", "other_matter"],
            "limit": 10,
        },
    ))
    assert out["_meta"]["tool"] == "search_audit_report_matters"
    assert out["query"]["year"] == 2024
    assert out["query"]["induty_prefix"] == "26"
    assert "companies" in out
    assert out["total_companies"] >= 0


def test_search_dataset_report_sections_shape():
    out = search_dataset(
        dataset="report_sections",
        company="005930",
        year=2024,
        source_type="audit_report",
        section_keys=["kam", "other_matter"],
        limit=10,
    )
    assert out["query"]["dataset"] == "report_sections"
    assert out["data_quality"]["source"] == "report_sections"
    assert "companies" in out
    if out["companies"]:
        assert out["companies"][0]["corp_code"] == "00126380"
        assert "records" in out["companies"][0]


def test_search_dataset_policy_and_structured_mcp_dispatch():
    policy = json.loads(call_tool(
        "search_dataset",
        {
            "dataset": "accounting_policies",
            "company": "005930",
            "year": 2025,
            "keyword": "수익",
            "limit": 5,
        },
    ))
    assert policy["_meta"]["tool"] == "search_dataset"
    assert policy["query"]["dataset"] == "accounting_policies"
    assert "companies" in policy

    fees = json.loads(call_tool(
        "search_dataset",
        {
            "dataset": "audit_fees",
            "year": 2025,
            "market": "KOSPI",
            "limit": 5,
        },
    ))
    assert fees["_meta"]["tool"] == "search_dataset"
    assert fees["query"]["dataset"] == "audit_fees"
    assert "companies" in fees


def test_estimate_audit_hours_proxy_shape():
    out = estimate_audit_hours_proxy("005930", year=2025, peer_limit=20)
    assert out["subject"]["corp_code"] == "00126380"
    assert out["peer_count"] > 0
    assert "complexity_score" in out
    assert "drivers" in out
    assert "peer_benchmarks" in out
    assert all("score_after" in d for d in out["drivers"])


def test_estimate_audit_hours_proxy_mcp_dispatch():
    out = json.loads(call_tool("estimate_audit_hours_proxy", {"company": "005930", "year": 2025}))
    assert out["_meta"]["tool"] == "estimate_audit_hours_proxy"
    assert "complexity_score" in out


def test_build_audit_acceptance_pack_shape():
    out = build_audit_acceptance_pack("005930", year=2025, peer_limit=20)
    assert out["subject"]["corp_code"] == "00126380"
    assert "acceptance_signals" in out
    assert "data_quality" in out
    assert "kam_reason_coverage" in out["data_quality"]["kam_body"]
    assert "kam_procedure_coverage" in out["data_quality"]["kam_body"]
    assert "recommended_review_areas" in out
    assert out["scope"] == "external_dart_evidence_pack"
    assert "audit_report_sections" in out["kam_summary"]
    assert "subject_sections" in out["kam_summary"]
    assert "audit_report_matter_summary" in out
    assert "audit_report_matters" in out["data_quality"]


def test_build_audit_acceptance_pack_mcp_dispatch():
    out = json.loads(call_tool("build_audit_acceptance_pack", {"company": "005930", "year": 2025}))
    assert out["_meta"]["tool"] == "build_audit_acceptance_pack"
    assert "acceptance_signals" in out
