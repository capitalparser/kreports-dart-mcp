from decimal import Decimal
import json

import pytest
from pydantic import ValidationError


def _model_result():
    from kreports.analysis.dcf_model import (
        DcfActualFact,
        DcfScenarioInput,
        build_dcf_valuation,
        dcf_result_to_dict,
    )

    amounts = {
        "revenue": "1000",
        "operating_profit": "100",
        "depreciation_amortization": "40",
        "purchase_ppe": "-30",
        "purchase_intangible_assets": "-10",
        "trade_receivables": "200",
        "inventories": "100",
        "trade_payables": "150",
        "cash_and_equivalents": "80",
        "interest_bearing_debt": "200",
    }
    facts = tuple(
        DcfActualFact(
            metric_key=key,
            amount=Decimal(value),
            unit="KRW",
            year=2024,
            fs_div="CFS",
            source_account_id=key,
            source_account_name=key,
            source_table="financial_facts_compact",
            fetched_at=None,
        )
        for key, value in amounts.items()
    )
    scenario = DcfScenarioInput(
        company="00126380",
        base_year=2024,
        fs_div="CFS",
        forecast_years=2,
        revenue_growth=Decimal("0.1"),
        operating_margin=Decimal("0.1"),
        tax_rate=Decimal("0.2"),
        da_to_revenue=Decimal("0.05"),
        capex_to_revenue=Decimal("0.04"),
        nwc_to_revenue=Decimal("0.2"),
        wacc=Decimal("0.1"),
        terminal_growth=Decimal("0.03"),
    )
    out = dcf_result_to_dict(build_dcf_valuation(scenario, facts))
    out["subject"] = {
        "corp_code": "00126380",
        "corp_name": "<script>주식회사</script>",
    }
    out["data_quality"] = {
        "status": "usable",
        "covered_years": [2024],
        "source": "financial_facts_compact",
    }
    return out


def test_build_dcf_model_pack_is_the_only_additive_32nd_tool():
    from kreports.mcp.catalog import TOOL_CATALOG
    from kreports.mcp.handlers import HANDLERS
    from kreports.mcp.tools import ALL_TOOLS

    assert len(TOOL_CATALOG) == 32
    assert list(TOOL_CATALOG)[-1] == "build_dcf_model_pack"
    assert [tool.name for tool in ALL_TOOLS][-1] == "build_dcf_model_pack"
    assert "build_dcf_model_pack" in HANDLERS
    assert "get_dcf_input_candidates" in HANDLERS


def test_dcf_tool_input_is_explicit_strict_and_defaults_to_five_years():
    from kreports.mcp.input_models import BuildDcfModelPackInput

    model = BuildDcfModelPackInput(
        company="00126380",
        base_year=2024,
        fs_div="CFS",
        wacc=0.1,
        terminal_growth=0.03,
    )
    assert model.forecast_years == 5
    with pytest.raises(ValidationError):
        BuildDcfModelPackInput(
            company="00126380",
            base_year=2024,
            fs_div="CFS",
            revenue_growth=True,
        )
    with pytest.raises(ValidationError):
        BuildDcfModelPackInput(
            company="00126380",
            base_year=2024,
            fs_div="CFS",
            forecast_years=True,
        )
    with pytest.raises(ValidationError):
        BuildDcfModelPackInput(
            company="00126380",
            base_year=2024,
            fs_div="CFS",
            wacc=0.03,
            terminal_growth=0.03,
        )


def test_dcf_handler_forwards_all_explicit_layers(monkeypatch):
    import kreports.mcp.handlers.investor as investor_handler
    from kreports.mcp.input_models import BuildDcfModelPackInput

    seen = {}

    def fake(**kwargs):
        seen.update(kwargs)
        return {"status": "partial_model"}

    monkeypatch.setattr(investor_handler, "build_dcf_model_pack", fake)
    args = BuildDcfModelPackInput(
        company="00126380",
        base_year=2024,
        fs_div="OFS",
        forecast_years=1,
        revenue_growth=0.1,
        normalized_revenue=1000,
        normalization_reason="검토 조정",
    )

    assert investor_handler.handle_build_dcf_model_pack(args) == {"status": "partial_model"}
    assert seen == {
        "company": "00126380",
        "base_year": 2024,
        "fs_div": "OFS",
        "forecast_years": 1,
        "revenue_growth": 0.1,
        "operating_margin": None,
        "tax_rate": None,
        "da_to_revenue": None,
        "capex_to_revenue": None,
        "nwc_to_revenue": None,
        "wacc": None,
        "terminal_growth": None,
        "normalized_revenue": 1000.0,
        "normalized_operating_profit": None,
        "normalization_reason": "검토 조정",
    }


def test_dcf_answer_pack_preserves_all_review_layers_and_escapes_subject():
    from kreports.mcp.answer_pack import build_answer_pack

    pack = build_answer_pack("build_dcf_model_pack", _model_result())

    assert pack["summary"]["title"].startswith("&lt;script&gt;")
    assert pack["summary"]["subject"] == "&lt;script&gt;주식회사&lt;/script&gt;"
    assert "<script>" not in json.dumps(pack, ensure_ascii=False)
    table_ids = {table["id"] for table in pack["tables"]}
    assert {
        "dcf_actuals",
        "dcf_normalization",
        "dcf_assumptions",
        "dcf_projections",
        "dcf_valuation_bridge",
        "dcf_sensitivity",
    } <= table_ids
    assert len(next(t for t in pack["tables"] if t["id"] == "dcf_sensitivity")["rows"]) == 25


def test_dcf_narrative_is_bounded_reviewable_and_not_a_conclusion():
    from kreports.mcp.renderers import render_answer

    text = render_answer("build_dcf_model_pack", _model_result())

    assert "검토 가능한 DCF 모델" in text
    assert "투자 권유" in text
    assert "공정성 의견" in text
    assert "승인된 예측" in text
    assert "감사 결론" in text
    assert "EBIT * (1-tax) + D&A - capex - change_in_NWC" in text
    assert "final_UFCF * (1+g) / (wacc-g)" in text
    assert "터미널가치:" in text
    assert "최종연도 할인계수:" in text
    assert "기업가치 = 예측기간 현재가치 + 터미널가치 현재가치" in text
    assert len(text) < 20_000


def test_dcf_legacy_candidates_and_runtime_facade_identity_remain_compatible():
    from kreports.analysis import api
    from kreports.analysis import financial_analysis

    assert api.get_dcf_input_candidates is financial_analysis.get_dcf_input_candidates
    assert api.build_dcf_model_pack is financial_analysis.build_dcf_model_pack


def test_dcf_model_result_has_no_binary_float_drift():
    payload = _model_result()
    encoded = json.dumps(payload, ensure_ascii=False)
    assert "0.30000000000000004" not in encoded
    assert payload["assumptions"][0]["value"] == "0.10"


def test_dcf_bridge_exposes_raw_terminal_formula_discount_and_ev_reconciliation():
    payload = _model_result()
    bridge = payload["valuation_bridge"]

    assert bridge["terminal_value"] == payload["terminal_value"]
    assert bridge["gordon_growth_formula"] == "final_UFCF * (1+g) / (wacc-g)"
    assert bridge["final_year_discount_factor"] == payload["projections"][-1][
        "discount_factor"
    ]
    assert bridge["enterprise_value_formula"] == (
        "enterprise_value = forecast_period_present_value + "
        "terminal_value_present_value"
    )


def test_dcf_facade_marks_enterprise_only_or_source_partial_as_limited(
    temp_engine,
    monkeypatch,
):
    from kreports.analysis import dcf_source, financial_analysis
    from kreports.analysis.dcf_source import DcfSourceResult
    from kreports.db.engine import get_session
    from kreports.db.models import Company

    with get_session() as session:
        session.add(Company(
            corp_code="00126380",
            corp_name="정확회사",
            stock_code="005930",
            market="KOSPI",
        ))
    facts = tuple(
        fact
        for fact in _facts_for_facade()
        if fact.metric_key != "cash_and_equivalents"
    )
    monkeypatch.setattr(
        dcf_source,
        "load_dcf_actuals",
        lambda *_args, **_kwargs: DcfSourceResult(
            status="partial",
            facts=facts,
            missing_metrics=("cash_and_equivalents",),
            limitations=("source_partial",),
        ),
    )

    result = financial_analysis.build_dcf_model_pack(
        "005930",
        2024,
        revenue_growth=0.1,
        operating_margin=0.1,
        tax_rate=0.2,
        da_to_revenue=0.05,
        capex_to_revenue=0.04,
        nwc_to_revenue=0.2,
        wacc=0.1,
        terminal_growth=0.03,
    )

    assert result["status"] == "complete_model"
    assert result["confidence"] == "enterprise_complete_equity_partial"
    assert result["data_quality"]["status"] == "limited"
    assert result["data_quality"]["enterprise_completion"] == "complete"
    assert result["data_quality"]["equity_completion"] == "partial"


def _facts_for_facade():
    from kreports.analysis.dcf_model import DcfActualFact

    values = {
        "revenue": "1000",
        "operating_profit": "100",
        "depreciation_amortization": "40",
        "purchase_ppe": "-30",
        "purchase_intangible_assets": "-10",
        "trade_receivables": "200",
        "inventories": "100",
        "trade_payables": "150",
        "cash_and_equivalents": "80",
        "interest_bearing_debt": "200",
    }
    return tuple(
        DcfActualFact(
            metric_key=key,
            amount=Decimal(value),
            unit="KRW",
            year=2024,
            fs_div="CFS",
            source_account_id=f"ifrs-full_{key}",
            source_account_name=key,
            source_table="financial_facts_compact",
            fetched_at=None,
        )
        for key, value in values.items()
    )


def test_dcf_direct_api_rejects_fuzzy_or_ambiguous_names_and_numeric_coercion(
    temp_engine,
):
    from kreports.analysis.financial_analysis import build_dcf_model_pack
    from kreports.db.engine import get_session
    from kreports.db.models import Company

    with get_session() as session:
        session.add_all([
            Company(
                corp_code="00000001",
                corp_name="알파 전자",
                stock_code="000001",
                market="KOSPI",
            ),
            Company(
                corp_code="00000002",
                corp_name="알파 화학",
                stock_code="000002",
                market="KOSPI",
            ),
            Company(
                corp_code="00000003",
                corp_name="중복 회사",
                stock_code="000003",
                market="KOSPI",
            ),
            Company(
                corp_code="00000004",
                corp_name="  중복   회사  ",
                stock_code="000004",
                market="KOSPI",
            ),
        ])

    fuzzy = build_dcf_model_pack("알파", 2024)
    assert "error" in fuzzy
    assert "정확" in fuzzy["error"]

    ambiguous = build_dcf_model_pack("중복 회사", 2024)
    assert "error" in ambiguous
    assert "둘 이상" in ambiguous["error"]

    with pytest.raises((TypeError, ValueError), match="base_year"):
        build_dcf_model_pack("000001", True)
