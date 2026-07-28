from copy import deepcopy

from kreports.db.models import AuditMatterItem


def test_candidate_history_is_usable_while_valuation_readiness_is_blocked(monkeypatch):
    """Removing the analyst/source blockers must not erase usable history."""
    from kreports.analysis import dcf_inputs

    monkeypatch.setattr(dcf_inputs, "_financial_series", lambda *_args, **_kwargs: [
        {
            "bsns_year": 2021, "revenue": 100, "operating_profit": 10,
            "net_income": 8, "operating_cf": 9, "tax_expense": 2,
            "purchase_ppe": 4, "purchase_intangible_assets": 1,
        },
        {
            "bsns_year": 2022, "revenue": 110, "operating_profit": 12,
            "net_income": 9, "operating_cf": 10, "tax_expense": 2,
            "purchase_ppe": 5, "purchase_intangible_assets": 1,
        },
        {
            "bsns_year": 2023, "revenue": 121, "operating_profit": 13,
            "net_income": 10, "operating_cf": 11, "tax_expense": 3,
            "purchase_ppe": 5, "purchase_intangible_assets": 1,
        },
        {
            "bsns_year": 2024, "revenue": 133, "operating_profit": 15,
            "net_income": 12, "operating_cf": 13, "tax_expense": 3,
            "purchase_ppe": 6, "purchase_intangible_assets": 1,
        },
        {
            "bsns_year": 2025, "revenue": 146, "operating_profit": 16,
            "net_income": 13, "operating_cf": 14, "tax_expense": 3,
            "purchase_ppe": 6, "purchase_intangible_assets": 1,
        },
    ])

    result = dcf_inputs.dcf_input_candidates("001", start_year=2021, end_year=2025)

    assert result["candidate_status"] == "usable"
    assert result["valuation_readiness"] == "blocked"
    assert result["data_quality"]["status"] == "usable"
    assert result["valuation_blockers"] == [
        {
            "field": "working_capital_delta",
            "kind": "source_fact_missing",
            "impact": "운전자본 증감에 따른 UFCF 계산 불가",
            "owner": "filing_data",
            "next_action": "기준연도 CFS 운전자본 관련 계정의 전년 대비 증감을 확인하세요.",
        },
        {
            "field": "wacc",
            "kind": "analyst_input_missing",
            "impact": "기업가치 할인 계산 불가",
            "owner": "analyst",
            "next_action": "자본구조·무위험수익률·베타·시장위험프리미엄으로 WACC를 산정하세요.",
        },
        {
            "field": "terminal_growth",
            "kind": "analyst_input_missing",
            "impact": "터미널가치 계산 불가",
            "owner": "analyst",
            "next_action": "장기 거시성장률과 사업 지속가능성을 근거로 영구성장률을 정하세요.",
        },
    ]


def test_tax_outliers_are_retained_but_excluded_from_candidate_median(monkeypatch):
    """Changing the median to include negative tax observations is a valuation bug."""
    from kreports.analysis import dcf_inputs

    monkeypatch.setattr(dcf_inputs, "_financial_series", lambda *_args, **_kwargs: [
        {
            "bsns_year": 2022, "revenue": 100, "operating_profit": 10,
            "net_income": 10, "operating_cf": 10, "tax_expense": -2,
            "purchase_ppe": 2, "purchase_intangible_assets": 0,
        },
        {
            "bsns_year": 2023, "revenue": 110, "operating_profit": 11,
            "net_income": 10, "operating_cf": 10, "tax_expense": 2,
            "purchase_ppe": 2, "purchase_intangible_assets": 0,
        },
        {
            "bsns_year": 2024, "revenue": 120, "operating_profit": 12,
            "net_income": -3, "operating_cf": 2, "tax_expense": 4,
            "purchase_ppe": 2, "purchase_intangible_assets": 0,
        },
    ])

    tax = dcf_inputs.dcf_input_candidates("001", start_year=2022, end_year=2024)["candidate_assumptions"]["tax_rate"]

    assert tax["value"] == 0.1667
    assert tax["included_observation_count"] == 1
    assert tax["excluded_observation_count"] == 2
    assert [observation["outlier"] for observation in tax["observations"]] == [True, False, True]
    assert tax["outlier_policy"] == "negative_or_greater_than_one_excluded_from_median"


def test_missing_candidate_history_has_source_and_analyst_readiness_blockers(monkeypatch):
    """A cache miss is not a valuation-ready result merely because no median is shown."""
    from kreports.analysis import dcf_inputs

    monkeypatch.setattr(dcf_inputs, "_financial_series", lambda *_args, **_kwargs: [])

    result = dcf_inputs.dcf_input_candidates("001", start_year=2021, end_year=2025)

    assert result["candidate_status"] == "missing"
    assert result["valuation_readiness"] == "blocked"
    assert [blocker["field"] for blocker in result["valuation_blockers"]] == [
        "financial_facts_compact", "working_capital_delta", "wacc", "terminal_growth",
    ]


def test_unavailable_dcf_model_suppresses_value_bridge_and_chart():
    """A None enterprise value must never be rendered as a valuation result."""
    from kreports.mcp.answer_pack import build_answer_pack
    from kreports.mcp.renderers import render_answer

    result = {
        "subject": {"corp_name": "A"},
        "status": "partial_model",
        "enterprise_value": None,
        "equity_value": None,
        "actuals": [{"metric_key": "revenue", "amount": "100", "year": 2024, "fs_div": "CFS"}],
        "assumptions": [],
        "missing_inputs": ["accounts[2024,CFS]:cash_and_equivalents"],
        "valuation_bridge": {"enterprise_value": None, "equity_value": None},
        "data_quality": {"status": "limited"},
    }

    pack = build_answer_pack("build_dcf_model_pack", result)
    answer = render_answer("build_dcf_model_pack", result)

    assert pack is not None
    assert pack["summary"]["domain_status"] == "unavailable"
    assert {table["id"] for table in pack["tables"]}.isdisjoint({"dcf_valuation_bridge", "dcf_sensitivity"})
    assert not pack["charts"]
    assert answer.startswith("산출 불가: 필수 입력 또는 공시 실제값이 부족하여 기업가치를 계산하지 않았습니다.")
    assert "기업가치: None" not in answer


def test_dcf_candidate_public_answer_and_pack_keep_readiness_separate():
    """A renderer that opens with a value conclusion hides the blocked review step."""
    from kreports.mcp.answer_pack import build_answer_pack
    from kreports.mcp.renderers import render_answer

    result = {
        "subject": {"corp_name": "A"},
        "historical_actuals": [{"year": 2024, "revenue": 100, "operating_profit": 10, "operating_cf": 8}],
        "candidate_assumptions": {"revenue_growth": {"value": 0.1, "basis": "historical_median"}},
        "candidate_status": "usable",
        "valuation_readiness": "blocked",
        "valuation_blockers": [{
            "field": "wacc", "kind": "analyst_input_missing",
            "impact": "기업가치 할인 계산 불가", "owner": "analyst",
            "next_action": "자본구조·무위험수익률·베타·시장위험프리미엄으로 WACC를 산정하세요.",
        }],
        "data_quality": {"status": "usable"},
    }

    answer = render_answer("get_dcf_input_candidates", result)
    pack = build_answer_pack("get_dcf_input_candidates", result)

    assert answer.startswith("DCF 입력 후보 상태: usable\n가치평가 준비도: blocked")
    assert pack is not None
    assert pack["summary"]["domain_status"] == "blocked"
    assert any(table["id"] == "valuation_blockers" for table in pack["tables"])


def test_enriched_dcf_candidate_keeps_public_readiness_and_input_immutable():
    """Enrichment must not reclassify candidates or mutate the raw handler result."""
    from kreports.mcp.tools import _attach_meta

    raw = {
        "subject": {"corp_name": "A"},
        "historical_actuals": [{"year": 2024, "revenue": 100, "operating_profit": 10, "operating_cf": 8}],
        "candidate_assumptions": {"revenue_growth": {"value": 0.1, "basis": "historical_median"}},
        "candidate_status": "usable",
        "valuation_readiness": "blocked",
        "valuation_blockers": [{
            "field": "wacc", "kind": "analyst_input_missing",
            "impact": "기업가치 할인 계산 불가", "owner": "analyst",
            "next_action": "자본구조·무위험수익률·베타·시장위험프리미엄으로 WACC를 산정하세요.",
        }],
        "data_quality": {"status": "usable"},
    }
    before = deepcopy(raw)

    out = _attach_meta("get_dcf_input_candidates", raw)

    assert raw == before
    assert out["answer"].startswith("DCF 입력 후보 상태: usable\n가치평가 준비도: blocked")
    assert out["answer_pack"]["summary"]["domain_status"] == "blocked"
    assert any(table["id"] == "valuation_blockers" for table in out["answer_pack"]["tables"])


def test_unavailable_model_reports_missing_accounts_with_requested_year_and_basis(
    temp_engine,
    monkeypatch,
):
    """A bare account name lets callers accidentally fill it from another year or FS."""
    from kreports.analysis import dcf_source, financial_analysis
    from kreports.analysis.dcf_model import DcfActualFact
    from kreports.analysis.dcf_source import DcfSourceResult
    from kreports.db.engine import get_session
    from kreports.db.models import Company

    with get_session() as session:
        session.add(Company(corp_code="00126380", corp_name="A", stock_code="005930", market="KOSPI"))
    facts = tuple(
        DcfActualFact(
            metric_key=key, amount=value, unit="KRW", year=2024, fs_div="OFS",
            source_account_id=key, source_account_name=key,
            source_table="financial_facts_compact", fetched_at=None,
        )
        for key, value in (("revenue", 100), ("operating_profit", 10))
    )
    monkeypatch.setattr(dcf_source, "load_dcf_actuals", lambda *_args, **_kwargs: DcfSourceResult(
        status="partial", facts=facts,
        missing_metrics=("cash_and_equivalents",), limitations=("source_partial",),
    ))

    result = financial_analysis.build_dcf_model_pack(
        "005930", 2024, fs_div="OFS", revenue_growth=0.1,
        operating_margin=0.1, tax_rate=0.2, da_to_revenue=0.05,
        capex_to_revenue=0.04, nwc_to_revenue=0.2, wacc=0.1,
        terminal_growth=0.03,
    )

    assert result["enterprise_value"] is None
    assert result["calculation_status"] == "unavailable"
    assert result["domain_verdict"] == "calculation_unavailable"
    assert {tuple(row.items()) for row in result["missing_accounts"]} >= {
        tuple({
            "field": "cash_and_equivalents", "year": 2024, "fs_div": "OFS",
            "basis": "requested_dcf_source_actual",
        }.items()),
    }


def test_qoe_matters_dedupe_parent_receipts_and_keep_receipt_sources(temp_engine, monkeypatch):
    """Collapsing section rows into one count must retain their audit-report receipt."""
    from kreports.analysis import investor_quality
    from kreports.mcp.answer_pack import build_answer_pack
    from kreports.db.engine import get_session

    monkeypatch.setattr(investor_quality, "engine", temp_engine)
    monkeypatch.setattr(investor_quality, "_financial_series", lambda *_args, **_kwargs: [
        {"bsns_year": 2024, "revenue": 100, "operating_profit": 10, "net_income": 8, "operating_cf": 9},
    ])
    with get_session() as session:
        session.add_all([
            AuditMatterItem(
                rcept_no="20250318001234-01", corp_code="001", bsns_year=2024,
                matter_type="going_concern", matter_text="계속기업 관련 중요한 불확실성",
                severity_hint="high", source_type="audit_report", section_ordinal=0,
            ),
            AuditMatterItem(
                rcept_no="20250318001234-02", corp_code="001", bsns_year=2024,
                matter_type="going_concern", matter_text="계속기업 관련 중요한 불확실성",
                severity_hint="high", source_type="audit_report", section_ordinal=1,
            ),
        ])

    result = investor_quality.quality_of_earnings_pack("001", start_year=2024, end_year=2024)
    pack = build_answer_pack("get_quality_of_earnings_pack", result)

    summary = result["audit_matter_summary"]
    assert summary["unique_receipt_count"] == 1
    assert summary["section_count"] == 2
    assert summary["dedupe_basis"] == "parent_rcept_no + matter_type + normalized_excerpt"
    assert summary["groups"][0]["source"]["rcept_no"] == "20250318001234"
    assert pack is not None
    matter_table = next(table for table in pack["tables"] if table["id"] == "audit_matter_groups")
    assert matter_table["rows"][0]["rcept_no"] == "20250318001234"
    assert {source["rcept_no"] for source in pack["sources"]} == {"20250318001234"}
