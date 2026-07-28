"""Public decision surfaces for professional auditor MCP tools."""
from __future__ import annotations

import pandas as pd
import pytest

from kreports.mcp.contracts import SectionStatusV1, enrich_answer_response


def _source(year: int = 2025) -> dict:
    return {
        "corp_code": "00126380",
        "corp_name": "삼성전자",
        "report_nm": f"사업보고서 ({year}.12)",
        "bsns_year": year,
        "rcept_no": f"{year + 1}0310002820",
        "section_title": "재무제표",
        "source_table": "financials",
    }


def _usable_effort() -> SectionStatusV1:
    return SectionStatusV1(
        status="usable",
        required=True,
        applicability="applicable",
        coverage={"requested_years": 3, "complete_years": 3, "cited_years": 3},
        sources=[],
    )


def _effort_rows() -> list[dict]:
    return [
        {
            "year": year,
            "fs_div": "CFS",
            "input_status": "usable",
            "financial_source": _source(year),
            "audit_source": {
                **_source(year),
                "section_title": "감사보수·감사시간",
                "source_table": "audit_fees",
            },
        }
        for year in (2025, 2024, 2023)
    ]


def _acceptance_payload() -> dict:
    source = _source()
    return {
        "subject": {"corp_code": "00126380", "corp_name": "삼성전자"},
        "year": 2025,
        "peer_group": {
            "sample_peers": [
                {"corp_code": f"00{i:06d}", "corp_name": f"Peer {i}"}
                for i in range(1, 6)
            ],
            "selection_policy": {"basis": "same industry and size"},
        },
        "risk_summary": {
            "subject_metrics": {
                "receivables_to_revenue": 10.0,
                "inventory_to_revenue": 8.0,
                "op_cf_to_operating_profit": 90.0,
                "accrual_ratio": 2.0,
                "beneish_m_score": -2.1,
            },
            "benchmarks": {
                metric: {"n": 5, "p25": 1, "p50": 2, "p75": 3}
                for metric in (
                    "receivables_to_revenue", "inventory_to_revenue",
                    "op_cf_to_operating_profit", "accrual_ratio", "beneish_m_score",
                )
            },
            "data_quality": {"status": "usable"},
            "source": source,
            "confirmed_facts": [{
                "statement": "요청 연도 재무위험 지표가 공시에서 확인됩니다.",
                "source": source,
            }],
        },
        "audit_history": {
            "history": [
                {"year": 2025, "rcept_no": "20260310002820"},
                {"year": 2024, "rcept_no": "20250310002820"},
            ],
        },
        "policy_summary": {
            "subject_policy_count": 2,
            "source": source,
        },
        "kam_summary": {
            "subject_sections": [{"rcept_no": "20260310002820"}],
            "semantic_complete": True,
            "source": source,
        },
        "audit_report_matter_summary": {
            "source": source,
            "classification_complete": True,
            "matter_counts": {"emphasis": {"subject_count": 0}},
        },
    }


def test_peer_risk_wrapper_keeps_nonempty_risk_out_of_missing_and_exposes_metric_rows(monkeypatch):
    from kreports.analysis import auditor_decisions

    monkeypatch.setattr(auditor_decisions, "_legacy_compare_peer_risk_profile", lambda **_: {
        "subject": {"corp_code": "00126380", "corp_name": "삼성전자"},
        "year": 2025,
        "subject_metrics": {"accrual_ratio": 1.2},
        "benchmarks": {"accrual_ratio": {"n": 5, "p25": 0.2, "p50": 0.5, "p75": 0.9}},
        "disclosure_event_counts": {"subject": {"total_disclosures": 2}, "peers": {}},
        "selection_policy": {"basis": "same industry"},
    })
    monkeypatch.setattr(auditor_decisions, "annual_filing_source", lambda *_args, **_kwargs: _source())

    out = auditor_decisions.compare_peer_risk_profile("005930", year=2025)

    assert out["data_quality"]["status"] == "limited"
    assert out["data_quality"]["status"] != "missing"
    row = out["metric_rows"][0]
    assert {"peer_n", "p25", "p50", "p75", "subject_value", "limitation"} <= set(row)
    assert out["confirmed_facts"][0]["source"]["rcept_no"] == "20260310002820"
    assert "매출채권" in " ".join(out["next_checks"])


def test_auditor_history_normalizes_change_tenure_and_receipt(monkeypatch):
    from kreports.analysis import audit_reporting

    monkeypatch.setattr(audit_reporting, "resolve_company_identifier", lambda _: "00126380")
    monkeypatch.setattr(audit_reporting._queries, "get_auditors", lambda _: pd.DataFrame([
        {"회계연도": 2023, "구분": "CFS", "감사인": "삼일회계법인", "감사의견": "적정", "접수번호": "20240310002820", "교체여부": "최초", "연속연수": 1},
        {"회계연도": 2024, "구분": "CFS", "감사인": "삼정회계법인", "감사의견": "적정", "접수번호": "20250310002820", "교체여부": "교체", "연속연수": 1},
        {"회계연도": 2025, "구분": "CFS", "감사인": "삼정회계법인", "감사의견": "적정", "접수번호": "20260310002820", "교체여부": "유지", "연속연수": 2},
    ]))

    out = audit_reporting.get_audit_history("005930")

    assert out["history"][-1] == {
        "year": 2025,
        "fs_div": "CFS",
        "auditor_nm": "삼정회계법인",
        "audit_opinion": "적정",
        "auditor_changed": False,
        "consecutive_years": 2,
        "rcept_no": "20260310002820",
    }
    assert out["history"][1]["auditor_changed"] is True
    assert len(out["confirmed_facts"]) == 3
    assert out["data_quality"]["status"] == "usable"


def test_acceptance_matrix_requires_all_seven_exact_minimums_and_explicit_effort():
    from kreports.analysis.auditor_decisions import build_acceptance_evidence

    payload = _acceptance_payload()
    out = build_acceptance_evidence(
        legacy_payload=payload,
        audit_effort_section=_usable_effort(),
        audit_effort_rows=_effort_rows(),
    )

    sections = out["data_quality"]["section_statuses"]
    assert set(sections) == {
        "peer_group", "audit_effort", "financial_risk", "audit_history",
        "accounting_policy", "kam", "audit_report_matters",
    }
    assert all(section["status"] == "usable" for section in sections.values())
    assert out["data_quality"]["status"] == "usable"

    legacy_only = build_acceptance_evidence(
        legacy_payload=payload,
        audit_effort_section=None,
        audit_effort_rows=[],
    )
    assert legacy_only["data_quality"]["status"] == "limited"
    assert "audit_effort_helper_not_integrated" in legacy_only["data_quality"]["section_statuses"]["audit_effort"]["blockers"]


def test_kam_rows_without_semantic_completion_and_uncited_not_applicable_are_limited():
    from kreports.analysis.auditor_decisions import build_acceptance_evidence

    payload = _acceptance_payload()
    payload["kam_summary"] = {
        "subject_sections": [{"rcept_no": "20260310002820"}],
        "source": _source(),
    }
    payload["audit_report_matter_summary"] = {"not_applicable_basis": "해당 없음"}

    out = build_acceptance_evidence(
        legacy_payload=payload,
        audit_effort_section=_usable_effort(),
        audit_effort_rows=_effort_rows(),
    )

    assert out["data_quality"]["section_statuses"]["kam"]["status"] == "limited"
    assert out["data_quality"]["section_statuses"]["audit_report_matters"]["status"] == "limited"


def test_public_acceptance_response_never_leaks_internal_keys_or_approval_language():
    from kreports.analysis.auditor_decisions import build_acceptance_evidence

    result = build_acceptance_evidence(
        legacy_payload=_acceptance_payload(),
        audit_effort_section=_usable_effort(),
        audit_effort_rows=_effort_rows(),
    )
    result["acceptance_signals"] = [{
        "signal": "audit_report_other_matter_paragraph_present",
        "description": "승인 또는 거절을 뜻하지 않는 내부 signal",
    }]
    out = enrich_answer_response("build_audit_acceptance_pack", result)

    assert out["quality_status"] == out["answer_pack"]["summary"]["status"] == "usable"
    assert "승인" not in out["answer"]
    assert "거절" not in out["answer"]
    assert "적정 의견" not in out["answer"]
    assert "kam_body" not in out["answer"]
    assert "audit_report_other_matter_paragraph_present" not in out["answer"]


def test_effort_claimed_counts_and_duplicate_rows_cannot_replace_three_cited_years():
    from kreports.analysis.auditor_decisions import build_acceptance_evidence

    claimed = SectionStatusV1(
        status="usable",
        required=True,
        applicability="applicable",
        coverage={"requested_years": 3, "complete_years": 3, "cited_years": 3},
        sources=[{
            "source_label": "단일 공시",
            "source_url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260310002820",
            "rcept_no": "20260310002820",
        }],
    )
    duplicate = _effort_rows()[0]

    out = build_acceptance_evidence(
        legacy_payload=_acceptance_payload(),
        audit_effort_section=claimed,
        audit_effort_rows=[duplicate, duplicate, duplicate],
    )

    section = out["data_quality"]["section_statuses"]["audit_effort"]
    assert section["status"] == "limited"
    assert section["coverage"]["complete_years"] == 1
    assert section["coverage"]["cited_years"] == 1
    assert "audit_effort_three_year_cited_coverage_missing" in section["blockers"]


def test_effort_rows_cannot_reuse_one_year_provenance_for_three_requested_years():
    from kreports.analysis.auditor_decisions import build_acceptance_evidence

    reused_source = _source(2025)
    rows = _effort_rows()
    for row in rows:
        row["financial_source"] = reused_source
        row["audit_source"] = {
            **reused_source,
            "section_title": "감사보수·감사시간",
            "source_table": "audit_fees",
        }

    out = build_acceptance_evidence(
        legacy_payload=_acceptance_payload(),
        audit_effort_section=_usable_effort(),
        audit_effort_rows=rows,
    )

    section = out["data_quality"]["section_statuses"]["audit_effort"]
    assert section["status"] == "limited"
    assert section["coverage"]["complete_years"] == 1
    assert section["coverage"]["cited_years"] == 1


def test_financial_risk_requires_receipt_linked_source_and_confirmed_fact():
    from kreports.analysis.auditor_decisions import build_acceptance_evidence

    payload = _acceptance_payload()
    payload["risk_summary"].pop("source")
    payload["risk_summary"]["confirmed_facts"] = [{
        "statement": "수치만 존재합니다.",
        "source": {"rcept_no": "not-a-receipt"},
    }]

    out = build_acceptance_evidence(
        legacy_payload=payload,
        audit_effort_section=_usable_effort(),
        audit_effort_rows=_effort_rows(),
    )

    section = out["data_quality"]["section_statuses"]["financial_risk"]
    assert section["status"] == "limited"
    assert "financial_risk_filing_source_missing" in section["blockers"]
    assert "financial_risk_confirmed_fact_missing" in section["blockers"]
    assert out["data_quality"]["status"] == "limited"


def test_financial_risk_fact_must_cite_the_same_requested_year_filing():
    from kreports.analysis.auditor_decisions import build_acceptance_evidence

    payload = _acceptance_payload()
    payload["risk_summary"]["confirmed_facts"] = [{
        "statement": "다른 공시에 연결된 사실입니다.",
        "source": _source(2024),
    }]

    out = build_acceptance_evidence(
        legacy_payload=payload,
        audit_effort_section=_usable_effort(),
        audit_effort_rows=_effort_rows(),
    )

    section = out["data_quality"]["section_statuses"]["financial_risk"]
    assert section["status"] == "limited"
    assert "financial_risk_confirmed_fact_missing" in section["blockers"]


@pytest.mark.parametrize(
    ("section_key", "payload_key"),
    [
        ("peer_group", "peer_group"),
        ("financial_risk", "risk_summary"),
        ("audit_history", "audit_history"),
        ("accounting_policy", "policy_summary"),
        ("kam", "kam_summary"),
        ("audit_report_matters", "audit_report_matter_summary"),
    ],
)
def test_every_legacy_section_treats_unknown_applicability_as_limited(
    section_key,
    payload_key,
):
    from kreports.analysis.auditor_decisions import build_acceptance_evidence

    payload = _acceptance_payload()
    payload[payload_key]["applicability"] = "unknown"

    out = build_acceptance_evidence(
        legacy_payload=payload,
        audit_effort_section=_usable_effort(),
        audit_effort_rows=_effort_rows(),
    )

    section = out["data_quality"]["section_statuses"][section_key]
    assert section["applicability"] == "unknown"
    assert section["status"] == "limited"
    assert "applicability_unknown" in section["blockers"]


@pytest.mark.parametrize(
    ("section_key", "payload_key"),
    [
        ("peer_group", "peer_group"),
        ("financial_risk", "risk_summary"),
        ("audit_history", "audit_history"),
        ("accounting_policy", "policy_summary"),
        ("kam", "kam_summary"),
        ("audit_report_matters", "audit_report_matter_summary"),
    ],
)
def test_every_legacy_section_accepts_not_applicable_only_with_basis_and_source(
    section_key,
    payload_key,
):
    from kreports.analysis.auditor_decisions import build_acceptance_evidence

    payload = _acceptance_payload()
    payload[payload_key].update({
        "applicability": "not_applicable",
        "not_applicable_basis": "요청 연도 공시에서 해당 검토영역이 적용되지 않음이 확인됩니다.",
        "source": _source(),
    })

    out = build_acceptance_evidence(
        legacy_payload=payload,
        audit_effort_section=_usable_effort(),
        audit_effort_rows=_effort_rows(),
    )

    section = out["data_quality"]["section_statuses"][section_key]
    assert section["applicability"] == "not_applicable"
    assert section["status"] == "usable"
    assert section["blockers"] == []
    assert section["sources"][0]["rcept_no"] == "20260310002820"


def test_audit_effort_not_applicable_requires_its_own_basis_and_source():
    from kreports.analysis.auditor_decisions import build_acceptance_evidence

    source = {
        "source_label": "삼성전자 사업보고서",
        "source_url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260310002820",
        "rcept_no": "20260310002820",
    }
    section = SectionStatusV1(
        status="limited",
        required=True,
        applicability="not_applicable",
        not_applicable_basis="공시 근거상 감사노력 입력 적용 대상이 아닙니다.",
        sources=[source],
    )

    out = build_acceptance_evidence(
        legacy_payload=_acceptance_payload(),
        audit_effort_section=section,
        audit_effort_rows=[],
    )

    effort = out["data_quality"]["section_statuses"]["audit_effort"]
    assert effort["status"] == "usable"
    assert effort["blockers"] == []


def test_public_handler_accepts_real_sample_peers_shape_and_keeps_detail_tables(monkeypatch):
    import json

    from kreports.analysis.auditor_decisions import build_acceptance_evidence
    from kreports.mcp.handlers import auditor
    from kreports.mcp.tools import call_tool

    result = build_acceptance_evidence(
        legacy_payload=_acceptance_payload(),
        audit_effort_section=_usable_effort(),
        audit_effort_rows=_effort_rows(),
    )
    monkeypatch.setattr(auditor, "build_audit_acceptance_pack", lambda **_: result)
    monkeypatch.setattr(auditor, "resolve_company", lambda company: company)

    out = json.loads(call_tool(
        "build_audit_acceptance_pack",
        {"company": "005930", "year": 2025, "peer_limit": 5},
    ))

    assert out["data_quality"]["section_statuses"]["peer_group"]["status"] == "usable"
    assert out["answer_pack"]["summary"]["status"] == "usable"
    assert {table["id"] for table in out["answer_pack"]["tables"]} >= {
        "audit_acceptance_evidence",
        "audit_acceptance_peer_group",
        "audit_acceptance_risk_metrics",
    }
    assert out["answer"].count("| Peer 그룹 |") == 1
