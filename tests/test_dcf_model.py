from dataclasses import FrozenInstanceError
from decimal import Decimal
import json

import pytest


def _facts(*, include_cash: bool = True, include_debt: bool = True):
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
    }
    if include_cash:
        values["cash_and_equivalents"] = "80"
    if include_debt:
        values["interest_bearing_debt"] = "200"
    return tuple(
        DcfActualFact(
            metric_key=key,
            amount=Decimal(amount),
            unit="KRW",
            year=2024,
            fs_div="CFS",
            source_account_id=f"ifrs-full_{key}",
            source_account_name=key,
            source_table="financial_facts_compact",
            fetched_at="2025-03-31T00:00:00",
        )
        for key, amount in values.items()
    )


def _scenario(**overrides):
    from kreports.analysis.dcf_model import DcfScenarioInput

    values = {
        "company": "00126380",
        "base_year": 2024,
        "fs_div": "CFS",
        "forecast_years": 2,
        "revenue_growth": Decimal("0.10"),
        "operating_margin": Decimal("0.10"),
        "tax_rate": Decimal("0.20"),
        "da_to_revenue": Decimal("0.05"),
        "capex_to_revenue": Decimal("0.04"),
        "nwc_to_revenue": Decimal("0.20"),
        "wacc": Decimal("0.10"),
        "terminal_growth": Decimal("0.03"),
    }
    values.update(overrides)
    return DcfScenarioInput(**values)


def test_dcf_contracts_are_deeply_immutable_and_json_serializable():
    """A caller must not be able to mutate a scenario, projection, or nested result."""
    from kreports.analysis.dcf_model import build_dcf_valuation, dcf_result_to_dict

    scenario = _scenario()
    result = build_dcf_valuation(scenario, _facts())

    with pytest.raises(FrozenInstanceError):
        scenario.forecast_years = 5
    with pytest.raises(FrozenInstanceError):
        result.projections[0].revenue = Decimal("0")
    with pytest.raises(TypeError):
        result.actuals[0] = result.actuals[0]
    assert json.loads(json.dumps(dcf_result_to_dict(result)))["status"] == "complete_model"


@pytest.mark.parametrize("forecast_years", [0, 11])
def test_dcf_rejects_forecast_period_outside_one_to_ten(forecast_years):
    with pytest.raises(ValueError, match="forecast_years"):
        _scenario(forecast_years=forecast_years)


@pytest.mark.parametrize("forecast_years", [1, 10])
def test_dcf_accepts_forecast_period_boundary(forecast_years):
    assert _scenario(forecast_years=forecast_years).forecast_years == forecast_years


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("revenue_growth", True),
        ("revenue_growth", Decimal("NaN")),
        ("revenue_growth", Decimal("-1")),
        ("tax_rate", Decimal("-0.01")),
        ("tax_rate", Decimal("1.01")),
        ("da_to_revenue", Decimal("-0.01")),
        ("capex_to_revenue", Decimal("-0.01")),
        ("nwc_to_revenue", Decimal("-0.01")),
        ("wacc", Decimal("0")),
    ],
)
def test_dcf_rejects_invalid_numeric_domains(field, value):
    with pytest.raises((TypeError, ValueError), match=field):
        _scenario(**{field: value})


def test_dcf_rejects_terminal_growth_equal_to_wacc():
    with pytest.raises(ValueError, match="terminal_growth"):
        _scenario(terminal_growth=Decimal("0.10"))


def test_dcf_normalization_defaults_to_actual_and_requires_a_reason():
    from kreports.analysis.dcf_model import build_dcf_valuation

    unchanged = build_dcf_valuation(_scenario(), _facts())
    assert unchanged.normalization.revenue.original_actual == Decimal("1000")
    assert unchanged.normalization.revenue.normalized_amount == Decimal("1000")
    assert unchanged.normalization.revenue.reason is None

    with pytest.raises(ValueError, match="normalization_reason"):
        _scenario(normalized_revenue=Decimal("1100"))

    overridden = build_dcf_valuation(
        _scenario(
            normalized_operating_profit=Decimal("120"),
            normalization_reason="일회성 비용 20 조정",
        ),
        _facts(),
    )
    assert overridden.normalization.operating_profit.original_actual == Decimal("100")
    assert overridden.normalization.operating_profit.normalized_amount == Decimal("120")
    assert overridden.normalization.revenue.normalized_amount == Decimal("1000")


def test_dcf_arithmetic_reconciles_ufcf_terminal_ev_and_equity_with_decimal():
    """Changing any forecast formula component or bridge sign must break this fixture."""
    from kreports.analysis.dcf_model import build_dcf_valuation

    result = build_dcf_valuation(_scenario(), _facts())

    assert result.status == "complete_model"
    assert result.projections[0].formula == "EBIT * (1-tax) + D&A - capex - change_in_NWC"
    assert result.projections[0].revenue == Decimal("1100.00")
    assert result.projections[0].nwc_change == Decimal("70.00")
    assert result.projections[0].ufcf == Decimal("29.00")
    assert result.projections[0].discount_factor == Decimal("0.9090909091")
    assert result.projections[1].ufcf == Decimal("86.90")
    assert result.terminal_value == Decimal("1278.67")
    assert result.terminal_value_present_value == Decimal("1056.75")
    assert result.enterprise_value == Decimal("1154.93")
    assert result.net_debt == Decimal("120.00")
    assert result.equity_value == Decimal("1034.93")


def test_dcf_preserves_negative_ufcf_instead_of_flooring_it():
    from kreports.analysis.dcf_model import build_dcf_valuation

    result = build_dcf_valuation(
        _scenario(operating_margin=Decimal("-0.10")),
        _facts(),
    )

    assert result.projections[0].ufcf < 0
    assert result.enterprise_value < 0


@pytest.mark.parametrize(
    "missing",
    [
        "revenue_growth",
        "operating_margin",
        "tax_rate",
        "da_to_revenue",
        "capex_to_revenue",
        "nwc_to_revenue",
        "wacc",
        "terminal_growth",
    ],
)
def test_missing_assumptions_return_partial_and_are_never_auto_filled(missing):
    from kreports.analysis.dcf_model import build_dcf_valuation

    result = build_dcf_valuation(_scenario(**{missing: None}), _facts())

    assert result.status == "partial_model"
    assert missing in result.missing_inputs
    assert result.enterprise_value is None
    assert result.projections == ()


def test_complete_enterprise_value_can_have_unavailable_equity_bridge():
    from kreports.analysis.dcf_model import build_dcf_valuation

    result = build_dcf_valuation(_scenario(), _facts(include_cash=False))

    assert result.enterprise_value == Decimal("1154.93")
    assert result.equity_value is None
    assert result.net_debt is None
    assert result.status == "complete_model"
    assert "cash_and_equivalents" in result.missing_inputs
    assert result.confidence == "enterprise_complete_equity_partial"


def test_sensitivity_is_exactly_centered_five_by_five_with_invalid_pairs():
    from kreports.analysis.dcf_model import build_dcf_valuation

    result = build_dcf_valuation(
        _scenario(wacc=Decimal("0.03"), terminal_growth=Decimal("0.02")),
        _facts(),
    )

    assert len(result.sensitivity) == 25
    assert sorted({cell.wacc for cell in result.sensitivity}) == [
        Decimal("0.0100"),
        Decimal("0.0200"),
        Decimal("0.0300"),
        Decimal("0.0400"),
        Decimal("0.0500"),
    ]
    assert sorted({cell.terminal_growth for cell in result.sensitivity}) == [
        Decimal("0.0000"),
        Decimal("0.0100"),
        Decimal("0.0200"),
        Decimal("0.0300"),
        Decimal("0.0400"),
    ]
    center = next(
        cell
        for cell in result.sensitivity
        if cell.wacc == Decimal("0.0300")
        and cell.terminal_growth == Decimal("0.0200")
    )
    assert center.status == "valid"
    assert center.enterprise_value == result.enterprise_value
    invalid = [
        cell
        for cell in result.sensitivity
        if cell.terminal_growth >= cell.wacc
    ]
    assert invalid
    assert all(cell.status == "invalid_rate_pair" and cell.enterprise_value is None for cell in invalid)


def test_sensitivity_center_reconciles_under_the_published_rounding_policy():
    """Per-year PV rounding and the center sensitivity cell must never drift."""
    from kreports.analysis.dcf_model import DcfActualFact, build_dcf_valuation

    facts = tuple(
        DcfActualFact(
            metric_key=fact.metric_key,
            amount=(
                Decimal("1000.13")
                if fact.metric_key == "revenue"
                else fact.amount
            ),
            unit=fact.unit,
            year=fact.year,
            fs_div=fact.fs_div,
            source_account_id=fact.source_account_id,
            source_account_name=fact.source_account_name,
            source_table=fact.source_table,
            fetched_at=fact.fetched_at,
        )
        for fact in _facts()
    )
    result = build_dcf_valuation(
        _scenario(
            forecast_years=7,
            revenue_growth=Decimal("0.0777"),
            operating_margin=Decimal("0.1333"),
            tax_rate=Decimal("0.2345"),
            da_to_revenue=Decimal("0.0477"),
            capex_to_revenue=Decimal("0.0399"),
            nwc_to_revenue=Decimal("0.1777"),
            wacc=Decimal("0.1037"),
            terminal_growth=Decimal("0.0211"),
        ),
        facts,
    )
    center = next(
        cell
        for cell in result.sensitivity
        if cell.wacc == Decimal("0.1037")
        and cell.terminal_growth == Decimal("0.0211")
    )
    assert center.enterprise_value == result.enterprise_value


def test_nonpositive_normalized_base_revenue_returns_invalid_model():
    from kreports.analysis.dcf_model import DcfActualFact, build_dcf_valuation

    facts = tuple(
        DcfActualFact(
            metric_key=fact.metric_key,
            amount=Decimal("-1") if fact.metric_key == "revenue" else fact.amount,
            unit=fact.unit,
            year=fact.year,
            fs_div=fact.fs_div,
            source_account_id=fact.source_account_id,
            source_account_name=fact.source_account_name,
            source_table=fact.source_table,
            fetched_at=fact.fetched_at,
        )
        for fact in _facts()
    )
    result = build_dcf_valuation(_scenario(), facts)

    assert result.status == "invalid_model"
    assert result.enterprise_value is None
    assert "base_revenue_nonpositive" in result.missing_inputs


def test_dcf_discloses_nwc_definition_capex_sign_and_bridge_exclusions():
    from kreports.analysis.dcf_model import build_dcf_valuation

    result = build_dcf_valuation(_scenario(), _facts())
    text = " ".join(result.limitations)

    assert "receivables + inventory - payables" in text
    assert "positive cash outflow" in text
    assert "minority interest" in text
    capex = [fact for fact in result.actuals if fact.metric_key.startswith("purchase_")]
    assert all(fact.amount < 0 for fact in capex)
