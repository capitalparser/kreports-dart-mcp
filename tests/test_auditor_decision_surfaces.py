"""Public decision surfaces for professional auditor MCP tools."""
from __future__ import annotations

from copy import deepcopy

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


def test_effort_rows_require_distinct_normalized_receipts_for_each_requested_year():
    from kreports.analysis.auditor_decisions import build_acceptance_evidence

    rows = _effort_rows()
    for row in rows:
        year = row["year"]
        shared_attachment = f"fee-note:20260310002820:{year}"
        row["financial_source"] = {
            **_source(year),
            "rcept_no": shared_attachment,
        }
        row["audit_source"] = {
            **_source(year),
            "rcept_no": shared_attachment,
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
    assert "audit_effort_distinct_year_receipts_missing" in section["blockers"]


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


def test_financial_risk_fact_requires_a_nonempty_material_statement():
    from kreports.analysis.auditor_decisions import build_acceptance_evidence

    payload = _acceptance_payload()
    payload["risk_summary"]["confirmed_facts"][0]["statement"] = "   "

    out = build_acceptance_evidence(
        legacy_payload=payload,
        audit_effort_section=_usable_effort(),
        audit_effort_rows=_effort_rows(),
    )

    section = out["data_quality"]["section_statuses"]["financial_risk"]
    assert section["status"] == "limited"
    assert "financial_risk_confirmed_fact_missing" in section["blockers"]


def test_accepted_section_sources_and_risk_facts_are_promoted_to_top_level_resources():
    from kreports.analysis.auditor_decisions import build_acceptance_evidence

    result = build_acceptance_evidence(
        legacy_payload=_acceptance_payload(),
        audit_effort_section=_usable_effort(),
        audit_effort_rows=_effort_rows(),
    )
    out = enrich_answer_response("build_audit_acceptance_pack", result)

    assert any(
        fact.get("statement") == "요청 연도 재무위험 지표가 공시에서 확인됩니다."
        for fact in result["confirmed_facts"]
    )
    section_receipts = {
        source["rcept_no"]
        for section in result["data_quality"]["section_statuses"].values()
        if section["status"] == "usable"
        for source in section["sources"]
        if source.get("rcept_no")
    }
    assert section_receipts <= {
        source["rcept_no"] for source in result["sources"]
    }
    assert out["answer_pack"]["sources"]


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
def test_not_applicable_source_must_match_requested_business_year(
    section_key,
    payload_key,
):
    from kreports.analysis.auditor_decisions import build_acceptance_evidence

    payload = _acceptance_payload()
    payload[payload_key].update({
        "applicability": "not_applicable",
        "not_applicable_basis": "공시에서 해당 없음이 확인됩니다.",
        "source": _source(2024),
    })
    if payload_key == "audit_history":
        payload[payload_key]["history"] = []

    out = build_acceptance_evidence(
        legacy_payload=payload,
        audit_effort_section=_usable_effort(),
        audit_effort_rows=_effort_rows(),
    )

    section = out["data_quality"]["section_statuses"][section_key]
    assert section["status"] == "limited"
    assert "not_applicable_requested_year_source_missing" in section["blockers"]


def test_audit_effort_not_applicable_without_year_bearing_source_is_limited():
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
    assert effort["status"] == "limited"
    assert "not_applicable_requested_year_source_missing" in effort["blockers"]


def test_audit_effort_not_applicable_uses_requested_year_row_provenance():
    from kreports.analysis.auditor_decisions import build_acceptance_evidence

    section = SectionStatusV1(
        status="limited",
        required=True,
        applicability="not_applicable",
        not_applicable_basis="요청 연도 공시에서 감사노력 입력 비적용이 확인됩니다.",
        sources=[],
    )
    row_source = _source(2025)

    out = build_acceptance_evidence(
        legacy_payload=_acceptance_payload(),
        audit_effort_section=section,
        audit_effort_rows=[{
            "year": 2025,
            "input_status": "not_applicable",
            "financial_source": row_source,
        }],
    )

    effort = out["data_quality"]["section_statuses"]["audit_effort"]
    assert effort["status"] == "usable"
    assert effort["blockers"] == []
    assert effort["sources"][0]["rcept_no"] == "20260310002820"


def test_audit_effort_not_applicable_rejects_requested_year_usable_row_source():
    """A usable audit-effort row cannot prove that audit effort is inapplicable."""
    from kreports.analysis.auditor_decisions import build_acceptance_evidence

    section = SectionStatusV1(
        status="limited",
        required=True,
        applicability="not_applicable",
        not_applicable_basis="요청 연도 공시에서 감사노력 입력 비적용이 확인됩니다.",
        sources=[],
    )

    out = build_acceptance_evidence(
        legacy_payload=_acceptance_payload(),
        audit_effort_section=section,
        audit_effort_rows=[{
            "year": 2025,
            "input_status": "usable",
            "financial_source": _source(2025),
        }],
    )

    effort = out["data_quality"]["section_statuses"]["audit_effort"]
    assert effort["status"] == "limited"
    assert effort["sources"] == []
    assert "not_applicable_basis_or_source_missing" in effort["blockers"]


@pytest.mark.parametrize(
    ("section_key", "payload_key", "blocker"),
    [
        (
            "accounting_policy",
            "policy_summary",
            "policy_current_year_source_missing",
        ),
        ("kam", "kam_summary", "kam_current_year_source_missing"),
        (
            "audit_report_matters",
            "audit_report_matter_summary",
            "audit_report_current_year_source_missing",
        ),
    ],
)
def test_applicable_current_period_sections_reject_wrong_year_sources(
    section_key,
    payload_key,
    blocker,
):
    from kreports.analysis.auditor_decisions import build_acceptance_evidence

    payload = _acceptance_payload()
    payload[payload_key]["source"] = _source(2024)

    out = build_acceptance_evidence(
        legacy_payload=payload,
        audit_effort_section=_usable_effort(),
        audit_effort_rows=_effort_rows(),
    )

    section = out["data_quality"]["section_statuses"][section_key]
    assert section["status"] == "limited"
    assert blocker in section["blockers"]


def test_url_only_accepted_source_reaches_answer_pack_resources():
    from kreports.analysis.auditor_decisions import build_acceptance_evidence
    from kreports.mcp.resources import read_resource

    payload = _acceptance_payload()
    payload["policy_summary"]["source"] = {
        "source_label": "외부 회계정책 기준서",
        "source_url": "https://example.com/accounting-policy",
        "bsns_year": 2025,
    }
    result = build_acceptance_evidence(
        legacy_payload=payload,
        audit_effort_section=_usable_effort(),
        audit_effort_rows=_effort_rows(),
    )

    out = enrich_answer_response("build_audit_acceptance_pack", result)

    assert any(
        fact.get("source", {}).get("source_url")
        == "https://example.com/accounting-policy"
        for fact in result["confirmed_facts"]
        if isinstance(fact, dict)
    )
    assert {
        "label": "외부 회계정책 기준서",
        "url": "https://example.com/accounting-policy",
    } in out["answer_pack"]["sources"]
    assert "외부 회계정책 기준서" in read_resource(
        out["answer_pack"]["resource_uri"]
    )["text"]


def test_acceptance_coverage_uses_only_public_korean_labels():
    from kreports.analysis.auditor_decisions import build_acceptance_evidence

    result = build_acceptance_evidence(
        legacy_payload=_acceptance_payload(),
        audit_effort_section=_usable_effort(),
        audit_effort_rows=_effort_rows(),
    )
    out = enrich_answer_response("build_audit_acceptance_pack", result)
    serialized_tables = str(out["answer_pack"]["tables"])

    for internal_key in (
        "requested_years",
        "semantic_complete",
        "subject_metric_count",
        "current_audit_report_source",
    ):
        assert internal_key not in out["answer"]
        assert internal_key not in serialized_tables
    assert "요청연도 수" in out["answer"]
    assert "의미 완결" in out["answer"]


def test_real_acceptance_wrapper_chain_binds_risk_and_renders_all_selected_peers(
    monkeypatch,
):
    from kreports.analysis import auditor_decisions

    peers = [
        {
            "corp_code": f"00{i:06d}",
            "corp_name": f"Peer {i}",
            "stock_code": f"{i:06d}",
            "include_reasons": ["same_ksic_prefix"],
        }
        for i in range(1, 13)
    ]
    selected_cohort = {
        "subject": {"corp_code": "00126380", "corp_name": "삼성전자"},
        "peer_count": 12,
        "peers": peers,
        "selection_policy": {
            "requested_year": 2025,
            "resolved_year": 2025,
            "fs_div_used": "CFS",
        },
    }
    legacy = _acceptance_payload()
    legacy["peer_group"] = {
        "peer_count": 12,
        "sample_peers": peers[:10],
        "selection_policy": selected_cohort["selection_policy"],
    }
    metrics = legacy["risk_summary"]["subject_metrics"]
    benchmarks = legacy["risk_summary"]["benchmarks"]

    monkeypatch.setattr(
        auditor_decisions,
        "_legacy_select_peer_group",
        lambda **_: selected_cohort,
    )
    monkeypatch.setattr(
        auditor_decisions,
        "_legacy_build_audit_acceptance_pack",
        lambda **_: legacy,
    )

    def risk_from_bound_cohort(**kwargs):
        assert kwargs["_peer_group"] is selected_cohort
        return {
            "subject": selected_cohort["subject"],
            "subject_metrics": metrics,
            "benchmarks": benchmarks,
            "disclosure_event_counts": {"subject": {"total_disclosures": 1}},
        }

    monkeypatch.setattr(
        auditor_decisions,
        "_legacy_compare_peer_risk_profile",
        risk_from_bound_cohort,
    )
    monkeypatch.setattr(
        auditor_decisions,
        "annual_filing_source",
        lambda *_args, **_kwargs: _source(),
    )
    monkeypatch.setattr(
        auditor_decisions,
        "get_audit_history",
        lambda *_: legacy["audit_history"],
    )
    monkeypatch.setattr(auditor_decisions, "compare_peer_kam_topics", lambda **_: {"error": "fixture"})
    monkeypatch.setattr(auditor_decisions, "compare_peer_audit_report_matters", lambda **_: {"error": "fixture"})

    result = auditor_decisions.build_audit_acceptance_pack(
        "005930",
        year=2025,
        peer_limit=12,
    )
    out = enrich_answer_response("build_audit_acceptance_pack", result)
    peer_table = next(
        table
        for table in out["answer_pack"]["tables"]
        if table["id"] == "audit_acceptance_peer_group"
    )

    assert result["peer_group"]["cohort_identity_verified"] is False
    assert "peer_cohort_identity_mismatch" in (
        result["data_quality"]["section_statuses"]["peer_group"]["blockers"]
    )
    assert len(result["peer_group"]["selected_peers"]) == 12
    assert len(peer_table["rows"]) == 12


def test_acceptance_wrapper_fails_closed_when_legacy_cohort_identity_differs(
    monkeypatch,
):
    from kreports.analysis import auditor_decisions

    selected = {
        "subject": {"corp_code": "00126380", "corp_name": "삼성전자"},
        "peer_count": 5,
        "peers": [
            {"corp_code": f"00{i:06d}", "corp_name": f"Peer {i}"}
            for i in range(1, 6)
        ],
        "selection_policy": {"requested_year": 2025, "fs_div_used": "CFS"},
    }
    legacy = _acceptance_payload()
    legacy["peer_group"]["sample_peers"][0]["corp_code"] = "00999999"
    legacy["peer_group"]["peer_count"] = 5
    legacy["peer_group"]["selection_policy"] = selected["selection_policy"]
    monkeypatch.setattr(
        auditor_decisions, "_legacy_select_peer_group", lambda **_: selected,
    )
    monkeypatch.setattr(
        auditor_decisions,
        "_legacy_build_audit_acceptance_pack",
        lambda **_: legacy,
    )
    monkeypatch.setattr(
        auditor_decisions,
        "_legacy_compare_peer_risk_profile",
        lambda **_: {"error": "risk intentionally unavailable"},
    )
    monkeypatch.setattr(
        auditor_decisions,
        "get_audit_history",
        lambda *_: legacy["audit_history"],
    )
    monkeypatch.setattr(auditor_decisions, "compare_peer_kam_topics", lambda **_: {"error": "fixture"})
    monkeypatch.setattr(auditor_decisions, "compare_peer_audit_report_matters", lambda **_: {"error": "fixture"})

    result = auditor_decisions.build_audit_acceptance_pack("005930", year=2025)
    peer_section = result["data_quality"]["section_statuses"]["peer_group"]

    assert result["peer_group"]["cohort_identity_verified"] is False
    assert peer_section["status"] == "limited"
    assert "peer_cohort_identity_mismatch" in peer_section["blockers"]


def test_peer_table_denominator_uses_selected_rows_not_legacy_count(
    monkeypatch,
):
    from kreports.analysis import auditor_decisions

    peers = [
        {"corp_code": f"00{i:06d}", "corp_name": f"Peer {i}"}
        for i in range(1, 13)
    ]
    selected = {
        "subject": {"corp_code": "00126380", "corp_name": "삼성전자"},
        "peer_count": 12,
        "peers": peers,
        "selection_policy": {"requested_year": 2025, "fs_div_used": "CFS"},
    }
    legacy = _acceptance_payload()
    legacy["peer_group"] = {
        "peer_count": 30,
        "sample_peers": peers[:10],
        "selection_policy": selected["selection_policy"],
    }
    monkeypatch.setattr(
        auditor_decisions, "_legacy_select_peer_group", lambda **_: selected,
    )
    monkeypatch.setattr(
        auditor_decisions,
        "_legacy_build_audit_acceptance_pack",
        lambda **_: legacy,
    )
    monkeypatch.setattr(
        auditor_decisions,
        "_legacy_compare_peer_risk_profile",
        lambda **_: {"error": "risk intentionally unavailable"},
    )
    monkeypatch.setattr(
        auditor_decisions,
        "get_audit_history",
        lambda *_: legacy["audit_history"],
    )
    monkeypatch.setattr(auditor_decisions, "compare_peer_kam_topics", lambda **_: {"error": "fixture"})
    monkeypatch.setattr(auditor_decisions, "compare_peer_audit_report_matters", lambda **_: {"error": "fixture"})

    result = auditor_decisions.build_audit_acceptance_pack("005930", year=2025)
    out = enrich_answer_response("build_audit_acceptance_pack", result)
    peer_table = next(
        table
        for table in out["answer_pack"]["tables"]
        if table["id"] == "audit_acceptance_peer_group"
    )

    assert result["peer_group"]["peer_count"] == 12
    assert result["peer_group"]["legacy_peer_count"] == 30
    assert {row["peer_n"] for row in peer_table["rows"]} == {12}


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


def test_auditor_owned_peer_wrappers_canonicalize_and_fail_closed(monkeypatch):
    from kreports.analysis import auditor_decisions
    from kreports.mcp.handlers import auditor as auditor_handlers

    receipt = "20250101000001"
    kam_legacy = {
        "subject": {"corp_code": "001"},
        "subject_sections": [{
            "rcept_no": f"{receipt}_001_xml",
            "section_key": "kam",
        }],
        "peer_section_samples": {
            "002": [{"rcept_no": f"{receipt}_002_xml", "section_key": "kam"}],
        },
        "subject_business_report_kam_summary": [{
            "rcept_no": f"{receipt}_003_xml",
        }],
        "audit_report_sections": {"subject_section_count": 1},
        "data_quality": {"status": "usable"},
    }
    matter_legacy = {
        "subject_matters": [{
            "rcept_no": f"{receipt}_001_xml",
            "section_key": "emphasis",
            "matter_category": "emphasis",
            "body_excerpt": "감사인의 책임과 경영진과의 커뮤니케이션 사항",
        }],
        "peer_matter_samples": {
            "002": [{"rcept_no": f"{receipt}_004_xml"}],
        },
        "matter_counts": {"emphasis": {"subject_count": 1}},
    }
    originals = (deepcopy(kam_legacy), deepcopy(matter_legacy))
    monkeypatch.setattr(
        auditor_decisions,
        "_legacy_compare_peer_kam_topics",
        lambda **_: kam_legacy,
    )
    monkeypatch.setattr(
        auditor_decisions,
        "attach_kam_item_semantics",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        auditor_decisions,
        "_legacy_compare_peer_audit_report_matters",
        lambda **_: matter_legacy,
    )

    kam = auditor_decisions.compare_peer_kam_topics("001")
    matters = auditor_decisions.compare_peer_audit_report_matters("001")

    assert kam["data_quality"]["status"] == "limited"
    assert kam["subject_sections"][0]["rcept_no"] == receipt
    assert kam["peer_section_samples"]["002"][0]["rcept_no"] == receipt
    assert kam["subject_business_report_kam_summary"][0]["rcept_no"] == receipt
    assert kam["audit_report_sections"]["semantic_complete"] is False
    assert matters["subject_matters"][0]["rcept_no"] == receipt
    assert matters["peer_matter_samples"]["002"][0]["rcept_no"] == receipt
    assert matters["matter_counts"]["emphasis"]["subject_signal_count"] == 0
    assert (kam_legacy, matter_legacy) == originals
    assert auditor_handlers.compare_peer_kam_topics is auditor_decisions.compare_peer_kam_topics
    assert (
        auditor_handlers.compare_peer_audit_report_matters
        is auditor_decisions.compare_peer_audit_report_matters
    )


def test_acceptance_recomputes_matter_signals_from_hardened_counts(monkeypatch):
    from kreports.analysis import auditor_decisions

    legacy = _acceptance_payload()
    legacy["acceptance_signals"] = [{
        "area": "audit_report_matters",
        "severity": "review",
        "signal": "audit_report_emphasis_paragraph_present",
    }]
    selected = {
        "subject": legacy["subject"],
        "peer_count": 5,
        "peers": legacy["peer_group"]["sample_peers"],
        "selection_policy": legacy["peer_group"]["selection_policy"],
    }
    monkeypatch.setattr(auditor_decisions, "_legacy_select_peer_group", lambda **_: selected)
    monkeypatch.setattr(auditor_decisions, "_legacy_build_audit_acceptance_pack", lambda **_: legacy)
    monkeypatch.setattr(auditor_decisions, "_legacy_compare_peer_risk_profile", lambda **_: {"error": "fixture"})
    monkeypatch.setattr(auditor_decisions, "get_audit_history", lambda *_: legacy["audit_history"])
    monkeypatch.setattr(auditor_decisions, "compare_peer_kam_topics", lambda **_: {"error": "fixture"})
    monkeypatch.setattr(
        auditor_decisions,
        "compare_peer_audit_report_matters",
        lambda **_: {
            "subject_matters": [{
                "rcept_no": "20250101000001",
                "matter_category": "emphasis",
                "acceptance_signal": False,
            }],
            "matter_counts": {"emphasis": {"subject_count": 1, "subject_signal_count": 0}},
        },
    )

    result = auditor_decisions.build_audit_acceptance_pack("005930", year=2025)

    assert all(
        signal.get("label") != "감사보고서 강조사항 문단이 확인됩니다."
        for signal in result["acceptance_signals"]
    )


def test_peer_kam_wrapper_reloads_full_population_and_fails_on_incomplete_eleventh(
    monkeypatch,
):
    from copy import deepcopy

    from kreports.analysis import auditor_decisions

    receipt = "20250101000001"
    complete = [{
        "rcept_no": receipt,
        "section_key": "kam",
        "complete": True,
    } for _ in range(10)]
    legacy = {
        "subject": {"corp_code": "001"},
        "subject_sections": deepcopy(complete),
        "peer_section_samples": {},
        "subject_business_report_kam_summary": [],
        "audit_report_sections": {"subject_section_count": 11},
        "data_quality": {"status": "usable", "subject_kam_body_count": 11},
    }
    full = [*complete, {
        "rcept_no": "20250101000002",
        "section_key": "kam",
        "complete": False,
    }]
    monkeypatch.setattr(auditor_decisions, "_legacy_compare_peer_kam_topics", lambda **_: legacy)
    monkeypatch.setattr(auditor_decisions, "attach_kam_item_semantics", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        auditor_decisions,
        "_get_audit_report_sections",
        lambda *_args, **_kwargs: {"sections": deepcopy(full)},
        raising=False,
    )
    monkeypatch.setattr(
        auditor_decisions,
        "kam_semantic_coverage",
        lambda rows: {
            "semantic_complete": len(rows) == 11 and all(row["complete"] for row in rows),
            "topic_coverage": {"available": len(rows), "total": 11, "status": "usable"},
            "reason_coverage": {"available": len(rows), "total": 11, "status": "usable"},
            "procedure_coverage": {"available": sum(row["complete"] for row in rows), "total": 11, "status": "limited"},
            "source_coverage": {"available": len(rows), "total": 11, "status": "usable"},
        },
    )

    result = auditor_decisions.compare_peer_kam_topics("001")

    assert len(result["subject_sections"]) == 11
    assert result["audit_report_sections"]["semantic_complete"] is False
    assert result["data_quality"]["status"] == "limited"


def test_peer_kam_uses_kam_count_not_total_audit_section_count(monkeypatch):
    from kreports.analysis import auditor_decisions

    row = {
        "rcept_no": "20250101000001",
        "section_key": "kam",
        "complete": True,
    }
    monkeypatch.setattr(
        auditor_decisions,
        "_legacy_compare_peer_kam_topics",
        lambda **_: {
            "subject": {"corp_code": "001"},
            "subject_sections": [row],
            "peer_section_samples": {},
            "audit_report_sections": {"subject_section_count": 11},
            "data_quality": {
                "status": "usable",
                "subject_kam_body_count": 1,
            },
        },
    )
    monkeypatch.setattr(
        auditor_decisions,
        "_get_audit_report_sections",
        lambda *_args, **_kwargs: {"sections": [row]},
    )
    monkeypatch.setattr(
        auditor_decisions,
        "attach_kam_item_semantics",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        auditor_decisions,
        "kam_semantic_coverage",
        lambda rows: {
            "semantic_complete": len(rows) == 1 and rows[0]["complete"],
            "topic_coverage": {"available": 1, "total": 1, "status": "usable"},
            "reason_coverage": {"available": 1, "total": 1, "status": "usable"},
            "procedure_coverage": {"available": 1, "total": 1, "status": "usable"},
            "source_coverage": {"available": 1, "total": 1, "status": "usable"},
        },
    )

    result = auditor_decisions.compare_peer_kam_topics("001")

    assert result["audit_report_sections"]["semantic_complete"] is True
    assert result["data_quality"]["status"] == "usable"


@pytest.mark.parametrize("kam_count", [None, 0])
def test_peer_kam_missing_or_contradictory_specific_count_fails_closed(
    monkeypatch,
    kam_count,
):
    from kreports.analysis import auditor_decisions

    quality = {"status": "usable"}
    if kam_count is not None:
        quality["subject_kam_body_count"] = kam_count
    row = {
        "rcept_no": "20250101000001",
        "section_key": "kam",
        "complete": True,
    }
    monkeypatch.setattr(
        auditor_decisions,
        "_legacy_compare_peer_kam_topics",
        lambda **_: {
            "subject": {"corp_code": "001"},
            "subject_sections": [row],
            "peer_section_samples": {},
            "audit_report_sections": {"subject_section_count": 1},
            "data_quality": quality,
        },
    )
    monkeypatch.setattr(
        auditor_decisions,
        "attach_kam_item_semantics",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        auditor_decisions,
        "kam_semantic_coverage",
        lambda _rows: {
            "semantic_complete": True,
            "topic_coverage": {"available": 1, "total": 1, "status": "usable"},
            "reason_coverage": {"available": 1, "total": 1, "status": "usable"},
            "procedure_coverage": {"available": 1, "total": 1, "status": "usable"},
            "source_coverage": {"available": 1, "total": 1, "status": "usable"},
        },
    )

    result = auditor_decisions.compare_peer_kam_topics("001")

    assert result["audit_report_sections"]["semantic_complete"] is False
    assert result["data_quality"]["status"] == "limited"
