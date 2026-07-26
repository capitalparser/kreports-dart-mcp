from dataclasses import FrozenInstanceError, replace
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


def test_negative_operating_margin_and_nwc_ratio_are_valid_economic_inputs():
    from kreports.analysis.dcf_model import build_dcf_valuation

    result = build_dcf_valuation(
        _scenario(
            operating_margin=Decimal("-1.50"),
            nwc_to_revenue=Decimal("-0.10"),
        ),
        _facts(),
    )

    assert result.status == "complete_model"
    assert result.projections[0].nwc_balance == Decimal("-110.00")
    assert result.projections[0].nwc_change == Decimal("-260.00")
    assert result.projections[0].ufcf == Decimal("-1049.00")


def test_sensitivity_preserves_unrounded_center_and_tiny_positive_wacc():
    from kreports.analysis.dcf_model import build_dcf_valuation

    precise = build_dcf_valuation(
        _scenario(
            wacc=Decimal("0.103712345678"),
            terminal_growth=Decimal("0.021112345678"),
        ),
        _facts(),
    )
    center = precise.sensitivity[12]
    assert center.wacc == Decimal("0.103712345678")
    assert center.terminal_growth == Decimal("0.021112345678")
    assert center.enterprise_value == precise.enterprise_value

    tiny = build_dcf_valuation(
        _scenario(
            wacc=Decimal("0.000000000001"),
            terminal_growth=Decimal("-0.01"),
        ),
        _facts(),
    )
    tiny_center = tiny.sensitivity[12]
    assert tiny_center.status == "valid"
    assert tiny_center.wacc == Decimal("0.000000000001")
    assert tiny_center.enterprise_value == tiny.enterprise_value


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("base_year", True),
        ("base_year", 2024.0),
        ("forecast_years", True),
        ("forecast_years", 2.5),
    ],
)
def test_scenario_rejects_bool_and_noninteger_year_fields(field, value):
    with pytest.raises((TypeError, ValueError), match=field):
        _scenario(**{field: value})


def test_public_dcf_dataclasses_validate_nested_contracts_and_defensively_copy():
    from kreports.analysis.dcf_model import (
        DcfProjection,
        DcfSensitivityCell,
        build_dcf_valuation,
    )

    result = build_dcf_valuation(_scenario(), _facts())
    copied = replace(
        result,
        actuals=list(result.actuals),
        assumptions=list(result.assumptions),
        projections=list(result.projections),
        sensitivity=list(result.sensitivity),
        missing_inputs=[],
        limitations=list(result.limitations),
    )
    assert isinstance(copied.actuals, tuple)
    assert isinstance(copied.projections, tuple)
    assert isinstance(copied.limitations, tuple)

    with pytest.raises(ValueError, match="formula"):
        replace(result.projections[0], formula="EBIT + D&A")
    with pytest.raises(ValueError, match="reconcile"):
        replace(result, enterprise_value=result.enterprise_value + Decimal("1"))
    with pytest.raises(ValueError, match="status"):
        DcfSensitivityCell(
            wacc=Decimal("0.10"),
            terminal_growth=Decimal("0.20"),
            status="valid",
            enterprise_value=Decimal("1"),
        )
    with pytest.raises((TypeError, ValueError), match="year"):
        DcfProjection(
            year=True,
            revenue=Decimal("1"),
            ebit=Decimal("1"),
            tax_rate=Decimal("0.2"),
            after_tax_ebit=Decimal("0.8"),
            depreciation_amortization=Decimal("0"),
            capex=Decimal("0"),
            nwc_balance=Decimal("0"),
            nwc_change=Decimal("0"),
            ufcf=Decimal("0.8"),
            discount_factor=Decimal("0.9"),
            present_value=Decimal("0.72"),
        )


def test_decimal_bounds_reject_pathological_values_and_arithmetic_fails_typed():
    from kreports.analysis.dcf_model import build_dcf_valuation

    with pytest.raises(ValueError, match="wacc"):
        _scenario(wacc=Decimal("1E+999999"))

    facts = tuple(
        replace(fact, amount=Decimal("900000000000000000000"))
        if fact.metric_key == "revenue"
        else fact
        for fact in _facts()
    )
    result = build_dcf_valuation(
        _scenario(revenue_growth=Decimal("999999999999999999")),
        facts,
    )
    assert result.status == "invalid_model"
    assert result.enterprise_value is None
    assert "arithmetic_invalid" in result.missing_inputs


def test_dcf_discloses_nwc_definition_capex_sign_and_bridge_exclusions():
    from kreports.analysis.dcf_model import build_dcf_valuation

    result = build_dcf_valuation(_scenario(), _facts())
    text = " ".join(result.limitations)

    assert "receivables + inventory - payables" in text
    assert "positive cash outflow" in text
    assert "minority interest" in text
    capex = [fact for fact in result.actuals if fact.metric_key.startswith("purchase_")]
    assert all(fact.amount < 0 for fact in capex)
