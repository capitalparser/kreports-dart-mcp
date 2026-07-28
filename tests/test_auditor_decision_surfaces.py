"""Public decision surfaces for professional auditor MCP tools."""
from __future__ import annotations

import pandas as pd

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
        sources=[{
            "source_label": "삼성전자 사업보고서",
            "source_url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260310002820",
            "rcept_no": "20260310002820",
        }],
    )


def _acceptance_payload() -> dict:
    source = _source()
    return {
        "subject": {"corp_code": "00126380", "corp_name": "삼성전자"},
        "year": 2025,
        "peer_group": {
            "peers": [{"corp_code": f"00{i:06d}"} for i in range(1, 6)],
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
        audit_effort_rows=[{"year": 2023}, {"year": 2024}, {"year": 2025}],
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
        audit_effort_rows=[{"year": 2023}, {"year": 2024}, {"year": 2025}],
    )

    assert out["data_quality"]["section_statuses"]["kam"]["status"] == "limited"
    assert out["data_quality"]["section_statuses"]["audit_report_matters"]["status"] == "limited"


def test_public_acceptance_response_never_leaks_internal_keys_or_approval_language():
    from kreports.analysis.auditor_decisions import build_acceptance_evidence

    result = build_acceptance_evidence(
        legacy_payload=_acceptance_payload(),
        audit_effort_section=_usable_effort(),
        audit_effort_rows=[{"year": 2023}, {"year": 2024}, {"year": 2025}],
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
