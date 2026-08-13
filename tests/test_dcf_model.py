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


@pytest.mark.parametrize(
    ("metric_key", "amount"),
    [
        ("revenue", Decimal("0")),
        ("revenue", Decimal("-1")),
        ("revenue", Decimal("1000000000000000000000001")),
        (
            "operating_profit",
            Decimal("1000000000000000000000001"),
        ),
        (
            "operating_profit",
            Decimal("-1000000000000000000000001"),
        ),
    ],
)
def test_normalized_metric_enforces_metric_specific_amount_domains(
    metric_key,
    amount,
):
    from kreports.analysis.dcf_model import DcfNormalizedMetric

    with pytest.raises(ValueError, match=metric_key):
        DcfNormalizedMetric(
            metric_key=metric_key,
            original_actual=amount,
            normalized_amount=amount,
            basis="actual_unchanged",
            reason=None,
        )

    signed_operating_profit = DcfNormalizedMetric(
        metric_key="operating_profit",
        original_actual=Decimal("-100"),
        normalized_amount=Decimal("-120"),
        basis="analyst_override",
        reason="영업손실 정상화",
    )
    assert signed_operating_profit.normalized_amount == Decimal("-120")


def test_normalized_metric_basis_and_reason_must_reconcile():
    from kreports.analysis.dcf_model import DcfNormalizedMetric

    with pytest.raises(ValueError, match="reason"):
        DcfNormalizedMetric(
            metric_key="revenue",
            original_actual=Decimal("1000"),
            normalized_amount=Decimal("1000"),
            basis="actual_unchanged",
            reason="사유가 있으면 안 됨",
        )
    with pytest.raises(ValueError, match="reason"):
        DcfNormalizedMetric(
            metric_key="revenue",
            original_actual=Decimal("1000"),
            normalized_amount=Decimal("1100"),
            basis="analyst_override",
            reason=" ",
        )


@pytest.mark.parametrize("status", ["complete", "partial", "invalid"])
def test_result_revalidates_normalization_for_every_status(status):
    from kreports.analysis.dcf_model import build_dcf_valuation

    if status == "complete":
        result = build_dcf_valuation(_scenario(), _facts())
    elif status == "partial":
        result = build_dcf_valuation(_scenario(wacc=None), _facts())
    else:
        facts = tuple(
            replace(fact, amount=Decimal("0.001"))
            if fact.metric_key == "revenue"
            else fact
            for fact in _facts()
        )
        result = build_dcf_valuation(_scenario(), facts)
    object.__setattr__(
        result.normalization.revenue,
        "reason",
        "변조된 actual_unchanged 사유",
    )

    with pytest.raises(ValueError, match="reason"):
        replace(result)


def test_result_revalidates_shared_normalization_override_reason():
    from kreports.analysis.dcf_model import build_dcf_valuation

    result = build_dcf_valuation(
        _scenario(
            normalized_revenue=Decimal("1100"),
            normalized_operating_profit=Decimal("120"),
            normalization_reason="동일한 검토 사유",
        ),
        _facts(),
    )
    object.__setattr__(
        result.normalization.operating_profit,
        "reason",
        "서로 다른 검토 사유",
    )

    with pytest.raises(ValueError, match="normalization"):
        replace(result)


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


def test_builder_revalidates_mutated_nonpositive_base_revenue_fact():
    from kreports.analysis.dcf_model import build_dcf_valuation

    facts = list(_facts())
    revenue = next(
        fact for fact in facts if fact.metric_key == "revenue"
    )
    object.__setattr__(revenue, "amount", Decimal("-1"))

    with pytest.raises(ValueError, match="revenue.amount"):
        build_dcf_valuation(_scenario(), tuple(facts))


@pytest.mark.parametrize("source", ["actual", "normalization"])
def test_sub_cent_positive_base_revenue_fails_typed_after_money_rounding(source):
    from kreports.analysis.dcf_model import build_dcf_valuation

    facts = _facts()
    scenario = _scenario()
    if source == "actual":
        facts = tuple(
            replace(fact, amount=Decimal("0.001"))
            if fact.metric_key == "revenue"
            else fact
            for fact in facts
        )
    else:
        scenario = _scenario(
            normalized_revenue=Decimal("0.001"),
            normalization_reason="KRW 반올림 경계 재현",
        )

    result = build_dcf_valuation(scenario, facts)

    assert result.status == "invalid_model"
    assert result.enterprise_value is None
    assert result.projections == ()
    assert result.missing_inputs == ("base_revenue_nonpositive",)


def test_positive_revenue_that_rounds_to_zero_in_projection_fails_typed():
    from kreports.analysis.dcf_model import build_dcf_valuation

    facts = tuple(
        replace(fact, amount=Decimal("0.01"))
        if fact.metric_key == "revenue"
        else fact
        for fact in _facts()
    )

    result = build_dcf_valuation(
        _scenario(revenue_growth=Decimal("-0.9")),
        facts,
    )

    assert result.status == "invalid_model"
    assert result.enterprise_value is None
    assert result.projections == ()
    assert result.missing_inputs == ("arithmetic_invalid",)
    assert any(
        item == "arithmetic_invalid:projection_revenue_nonpositive"
        for item in result.limitations
    )


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
        replace(fact, amount=Decimal("1E+24"))
        if fact.metric_key == "revenue"
        else fact
        for fact in _facts()
    )
    result = build_dcf_valuation(
        _scenario(
            forecast_years=10,
            revenue_growth=Decimal("10"),
        ),
        facts,
    )
    assert result.status == "invalid_model"
    assert result.enterprise_value is None
    assert "arithmetic_invalid" in result.missing_inputs


def test_decimal_bounds_use_normalized_significance_and_allow_tiny_wacc():
    from kreports.analysis.dcf_model import build_dcf_valuation

    trailing = build_dcf_valuation(
        _scenario(wacc=Decimal("0.1" + ("0" * 40))),
        _facts(),
    )
    tiny = build_dcf_valuation(
        _scenario(
            wacc=Decimal("1E-19"),
            terminal_growth=Decimal("-0.01"),
        ),
        _facts(),
    )

    assert trailing.status == "complete_model"
    assert trailing.assumptions[6].value == Decimal("0.1")
    assert tiny.status == "complete_model"
    assert tiny.sensitivity[12].wacc == Decimal("1E-19")


@pytest.mark.parametrize(
    "field",
    [
        "revenue_growth",
        "operating_margin",
        "tax_rate",
        "da_to_revenue",
        "capex_to_revenue",
        "nwc_to_revenue",
        "wacc",
        "terminal_growth",
        "normalized_revenue",
        "normalized_operating_profit",
    ],
)
def test_decimal_domain_has_no_unpublished_tiny_positive_exponent_floor(field):
    overrides = {field: Decimal("1E-31")}
    if field == "wacc":
        overrides["terminal_growth"] = Decimal("-0.01")
    if field in {"normalized_revenue", "normalized_operating_profit"}:
        overrides["normalization_reason"] = "초소형 값 경계 검토"

    scenario = _scenario(**overrides)

    assert getattr(scenario, field) == Decimal("1E-31")


def test_one_e_minus_31_wacc_fails_typed_when_sensitivity_is_unsafe():
    from kreports.analysis.dcf_model import build_dcf_valuation

    result = build_dcf_valuation(
        _scenario(
            wacc=Decimal("1E-31"),
            terminal_growth=Decimal("-0.01"),
        ),
        _facts(),
    )

    assert result.status == "invalid_model"
    assert result.enterprise_value is None
    assert result.missing_inputs == ("arithmetic_invalid",)
    assert any(
        item == "arithmetic_invalid:InvalidOperation"
        for item in result.limitations
    )


def test_decimal_negative_exponent_and_fixed_json_width_are_bounded():
    from kreports.analysis.dcf_model import (
        build_dcf_valuation,
        dcf_result_to_dict,
    )

    accepted = build_dcf_valuation(
        _scenario(
            wacc=Decimal("1E-31"),
            terminal_growth=Decimal("-0.01"),
        ),
        _facts(),
    )
    payload = dcf_result_to_dict(accepted)
    assert payload["assumptions"][6]["value"] == (
        "0.0000000000000000000000000000001"
    )
    assert len(payload["assumptions"][6]["value"]) < 128

    with pytest.raises(ValueError, match="precision bounds"):
        _scenario(
            wacc=Decimal("1E-10000"),
            terminal_growth=Decimal("-0.01"),
        )
    with pytest.raises(ValueError, match="precision bounds"):
        _scenario(operating_margin=Decimal("0E-10000"))

    object.__setattr__(
        accepted.assumptions[6],
        "value",
        Decimal("1E-10000"),
    )
    with pytest.raises(ValueError, match="serialization bounds"):
        dcf_result_to_dict(accepted)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("revenue_growth", Decimal("10.0001")),
        ("operating_margin", Decimal("10.0001")),
        ("operating_margin", Decimal("-10.0001")),
        ("da_to_revenue", Decimal("10.0001")),
        ("capex_to_revenue", Decimal("10.0001")),
        ("nwc_to_revenue", Decimal("10.0001")),
        ("nwc_to_revenue", Decimal("-10.0001")),
        ("wacc", Decimal("1.0001")),
        ("terminal_growth", Decimal("1.0001")),
    ],
)
def test_dcf_domain_rejects_unsupported_economic_rate_bounds(field, value):
    with pytest.raises(ValueError, match=field):
        _scenario(**{field: value})


def test_result_recomputes_projection_revenue_from_normalized_base():
    from kreports.analysis.dcf_model import build_dcf_valuation

    result = build_dcf_valuation(_scenario(), _facts())
    contradictory = replace(
        result.projections[0],
        revenue=result.projections[0].revenue + Decimal("1"),
    )

    with pytest.raises(ValueError, match="projection revenue"):
        replace(
            result,
            projections=(contradictory, *result.projections[1:]),
        )


@pytest.mark.parametrize(
    ("field_name", "changes"),
    [
        ("ebit", {"ebit": Decimal("111")}),
        ("tax_rate", {"tax_rate": Decimal("0.21")}),
        (
            "depreciation_amortization",
            {"depreciation_amortization": Decimal("56")},
        ),
        ("capex", {"capex": Decimal("45")}),
        (
            "nwc_balance",
            {
                "nwc_balance": Decimal("221"),
                "nwc_change": Decimal("71"),
            },
        ),
        ("nwc_change", {"nwc_change": Decimal("71")}),
        ("discount_factor", {"discount_factor": Decimal("0.90")}),
    ],
)
def test_result_recomputes_projection_drivers_from_assumptions(
    field_name,
    changes,
):
    from kreports.analysis.dcf_model import build_dcf_valuation

    def money(value):
        return value.quantize(Decimal("0.01"))

    result = build_dcf_valuation(_scenario(), _facts())
    row = result.projections[0]
    values = {
        "ebit": row.ebit,
        "tax_rate": row.tax_rate,
        "depreciation_amortization": row.depreciation_amortization,
        "capex": row.capex,
        "nwc_balance": row.nwc_balance,
        "nwc_change": row.nwc_change,
        "discount_factor": row.discount_factor,
        **changes,
    }
    values["after_tax_ebit"] = money(
        values["ebit"] * (Decimal(1) - values["tax_rate"])
    )
    values["ufcf"] = money(
        values["after_tax_ebit"]
        + values["depreciation_amortization"]
        - values["capex"]
        - values["nwc_change"]
    )
    values["present_value"] = money(
        values["ufcf"] * values["discount_factor"]
    )
    contradictory = replace(row, **values)

    with pytest.raises(ValueError, match=f"projection {field_name}"):
        replace(
            result,
            projections=(contradictory, *result.projections[1:]),
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"confidence": "enterprise_complete_equity_partial"},
        {"missing_inputs": ("cash_and_equivalents",)},
    ],
)
def test_complete_result_confidence_and_missing_bridge_semantics(changes):
    from kreports.analysis.dcf_model import build_dcf_valuation

    result = build_dcf_valuation(_scenario(), _facts())

    with pytest.raises(ValueError, match="bridge semantics"):
        replace(result, **changes)


def test_partial_and_invalid_result_missing_status_semantics():
    from kreports.analysis.dcf_model import build_dcf_valuation

    partial = build_dcf_valuation(_scenario(wacc=None), _facts())
    sub_cent_revenue = tuple(
        replace(fact, amount=Decimal("0.001"))
        if fact.metric_key == "revenue"
        else fact
        for fact in _facts()
    )
    invalid = build_dcf_valuation(_scenario(), sub_cent_revenue)

    with pytest.raises(ValueError, match="status semantics"):
        replace(partial, missing_inputs=())
    with pytest.raises(ValueError, match="status semantics"):
        replace(invalid, missing_inputs=())


@pytest.mark.parametrize(
    ("key", "invalid_value"),
    [
        ("revenue_growth", Decimal("-1")),
        ("operating_margin", Decimal("10.1")),
        ("tax_rate", Decimal("1.1")),
        ("da_to_revenue", Decimal("-0.1")),
        ("capex_to_revenue", Decimal("-0.1")),
        ("nwc_to_revenue", Decimal("10.1")),
        ("wacc", Decimal("0")),
        ("terminal_growth", Decimal("-1")),
    ],
)
def test_partial_result_revalidates_every_assumption_domain(
    key,
    invalid_value,
):
    from kreports.analysis.dcf_model import build_dcf_valuation

    facts = tuple(
        fact
        for fact in _facts()
        if fact.metric_key != "depreciation_amortization"
    )
    partial = build_dcf_valuation(_scenario(), facts)
    assumptions = tuple(
        replace(assumption, value=invalid_value)
        if assumption.key == key
        else assumption
        for assumption in partial.assumptions
    )

    with pytest.raises(ValueError, match=key):
        replace(partial, assumptions=assumptions)


def test_partial_result_revalidates_relational_assumption_domain():
    from kreports.analysis.dcf_model import build_dcf_valuation

    facts = tuple(
        fact
        for fact in _facts()
        if fact.metric_key != "depreciation_amortization"
    )
    partial = build_dcf_valuation(_scenario(), facts)
    assumptions = tuple(
        replace(assumption, value=Decimal("0.10"))
        if assumption.key == "terminal_growth"
        else assumption
        for assumption in partial.assumptions
    )

    with pytest.raises(ValueError, match="terminal_growth"):
        replace(partial, assumptions=assumptions)


def test_missing_inputs_are_exact_ordered_assumption_enterprise_bridge_gaps():
    from kreports.analysis.dcf_model import build_dcf_valuation

    facts = tuple(
        fact
        for fact in _facts()
        if fact.metric_key
        not in {"depreciation_amortization", "cash_and_equivalents"}
    )
    partial = build_dcf_valuation(
        _scenario(wacc=None),
        facts,
        source_missing=(
            "cash_and_equivalents",
            "depreciation_amortization",
        ),
    )

    assert partial.status == "partial_model"
    assert partial.missing_inputs == (
        "wacc",
        "depreciation_amortization",
        "cash_and_equivalents",
    )
    for contradictory in (
        partial.missing_inputs[:-1],
        (*partial.missing_inputs, "stale_gap"),
        tuple(reversed(partial.missing_inputs)),
    ):
        with pytest.raises(ValueError, match="missing inputs"):
            replace(partial, missing_inputs=contradictory)


def test_invalid_result_missing_inputs_include_exact_bridge_and_reason_gaps():
    from kreports.analysis.dcf_model import build_dcf_valuation

    facts = tuple(
        replace(fact, amount=Decimal("0.001"))
        if fact.metric_key == "revenue"
        else fact
        for fact in _facts(include_cash=False)
    )

    invalid = build_dcf_valuation(_scenario(), facts)

    assert invalid.status == "invalid_model"
    assert invalid.missing_inputs == (
        "cash_and_equivalents",
        "base_revenue_nonpositive",
    )
    with pytest.raises(ValueError, match="missing inputs"):
        replace(
            invalid,
            missing_inputs=("base_revenue_nonpositive",),
        )


def test_arithmetic_invalid_requires_complete_projection_inputs():
    from kreports.analysis.dcf_model import build_dcf_valuation

    partial = build_dcf_valuation(_scenario(wacc=None), _facts())
    assert partial.status == "partial_model"
    assert partial.missing_inputs == ("wacc",)

    with pytest.raises(ValueError, match="status semantics"):
        replace(
            partial,
            status="invalid_model",
            confidence="invalid",
            missing_inputs=("wacc", "arithmetic_invalid"),
        )

    overflow = build_dcf_valuation(
        _scenario(forecast_years=10, revenue_growth=Decimal("10")),
        tuple(
            replace(fact, amount=Decimal("1E+24"))
            if fact.metric_key == "revenue"
            else fact
            for fact in _facts()
        ),
    )
    without_da = tuple(
        fact
        for fact in overflow.actuals
        if fact.metric_key != "depreciation_amortization"
    )
    with pytest.raises(ValueError, match="status semantics"):
        replace(
            overflow,
            actuals=without_da,
            missing_inputs=(
                "depreciation_amortization",
                "arithmetic_invalid",
            ),
        )


def test_base_revenue_invalid_coexists_with_exact_projection_and_bridge_gaps():
    from kreports.analysis.dcf_model import build_dcf_valuation

    facts = tuple(
        replace(fact, amount=Decimal("0.001"))
        if fact.metric_key == "revenue"
        else fact
        for fact in _facts(include_cash=False)
    )

    invalid = build_dcf_valuation(
        _scenario(wacc=None),
        facts,
    )

    assert invalid.status == "invalid_model"
    assert invalid.missing_inputs == (
        "wacc",
        "cash_and_equivalents",
        "base_revenue_nonpositive",
    )
    with pytest.raises(ValueError, match="status semantics"):
        replace(
            invalid,
            status="partial_model",
            confidence="partial",
            missing_inputs=("wacc", "cash_and_equivalents"),
        )


def test_projection_direct_contract_rejects_impossible_domains():
    from kreports.analysis.dcf_model import build_dcf_valuation

    row = build_dcf_valuation(_scenario(), _facts()).projections[0]

    with pytest.raises(ValueError, match="revenue"):
        replace(row, revenue=Decimal("0"))
    with pytest.raises(ValueError, match="depreciation"):
        replace(
            row,
            depreciation_amortization=Decimal("-1"),
            ufcf=row.ufcf - row.depreciation_amortization - Decimal("1"),
            present_value=(
                (
                    row.ufcf
                    - row.depreciation_amortization
                    - Decimal("1")
                )
                * row.discount_factor
            ).quantize(Decimal("0.01")),
        )
    with pytest.raises(ValueError, match="discount_factor"):
        replace(
            row,
            discount_factor=Decimal("1.01"),
            present_value=(row.ufcf * Decimal("1.01")).quantize(
                Decimal("0.01")
            ),
        )


@pytest.mark.parametrize(
    ("changes", "field_name"),
    [
        ({"unit": "million_KRW"}, "unit"),
        ({"source_account_id": None}, "source_account_id"),
        ({"source_account_name": " "}, "source_account_name"),
        ({"source_table": "financials"}, "source_table"),
        ({"fetched_at": "not-an-iso-timestamp"}, "fetched_at"),
    ],
)
def test_actual_fact_direct_contract_requires_canonical_traceability(
    changes,
    field_name,
):
    fact = _facts()[0]

    with pytest.raises((TypeError, ValueError), match=field_name):
        replace(fact, **changes)

    synthetic = replace(
        fact,
        source_account_id="financials.revenue",
        source_account_name="매출액",
    )
    assert synthetic.source_account_id == "financials.revenue"


@pytest.mark.parametrize(
    ("metric_key", "message"),
    [
        ("revenue", "positive"),
        ("depreciation_amortization", "non-negative"),
        ("trade_receivables", "non-negative"),
        ("inventories", "non-negative"),
        ("trade_payables", "non-negative"),
        ("cash_and_equivalents", "non-negative"),
        ("interest_bearing_debt", "non-negative"),
    ],
)
def test_actual_fact_rejects_metric_specific_impossible_negative_signs(
    metric_key,
    message,
):
    fact = next(
        item for item in _facts() if item.metric_key == metric_key
    )

    with pytest.raises(ValueError, match=message):
        replace(fact, amount=Decimal("-0.01"))


@pytest.mark.parametrize(
    "metric_key",
    [
        "operating_profit",
        "purchase_ppe",
        "purchase_intangible_assets",
    ],
)
def test_actual_fact_preserves_valid_loss_and_capex_source_signs(metric_key):
    fact = next(
        item for item in _facts() if item.metric_key == metric_key
    )

    negative = replace(fact, amount=Decimal("-0.01"))

    assert negative.amount == Decimal("-0.01")


def test_builder_revalidates_frozen_actual_fact_instances():
    from kreports.analysis.dcf_model import build_dcf_valuation

    facts = list(_facts())
    object.__setattr__(facts[0], "unit", "USD")

    with pytest.raises(ValueError, match="unit"):
        build_dcf_valuation(_scenario(), tuple(facts))


def test_dcf_domain_bounds_base_and_normalized_amounts():
    largest = Decimal("1E+24")
    assert replace(_facts()[0], amount=largest).amount == largest

    with pytest.raises(ValueError, match="amount"):
        replace(
            _facts()[0],
            amount=Decimal("1000000000000000000000001"),
        )
    with pytest.raises(ValueError, match="normalized_revenue"):
        _scenario(
            normalized_revenue=Decimal(
                "1000000000000000000000001"
            ),
            normalization_reason="상한 테스트",
        )
    with pytest.raises(ValueError, match="normalized_operating_profit"):
        _scenario(
            normalized_operating_profit=Decimal(
                "-1000000000000000000000001"
            ),
            normalization_reason="상한 테스트",
        )


def test_dcf_domain_bounds_company_and_normalization_reason_text():
    with pytest.raises(ValueError, match="company"):
        _scenario(company="회" * 201)
    with pytest.raises(ValueError, match="normalization_reason"):
        _scenario(
            normalized_revenue=Decimal("1000"),
            normalization_reason="근" * 1001,
        )


def test_dcf_discloses_nwc_definition_capex_sign_and_bridge_exclusions():
    from kreports.analysis.dcf_model import build_dcf_valuation

    result = build_dcf_valuation(_scenario(), _facts())
    text = " ".join(result.limitations)

    assert "receivables + inventory - payables" in text
    assert "positive cash outflow" in text
    assert "minority interest" in text
    capex = [fact for fact in result.actuals if fact.metric_key.startswith("purchase_")]
    assert all(fact.amount < 0 for fact in capex)


@pytest.mark.parametrize("status", ["complete", "partial", "invalid"])
def test_result_requires_exact_ordered_model_limitation_prefix(status):
    from kreports.analysis.dcf_model import build_dcf_valuation

    if status == "complete":
        result = build_dcf_valuation(_scenario(), _facts())
    elif status == "partial":
        result = build_dcf_valuation(_scenario(wacc=None), _facts())
    else:
        facts = tuple(
            replace(fact, amount=Decimal("0.001"))
            if fact.metric_key == "revenue"
            else fact
            for fact in _facts()
        )
        result = build_dcf_valuation(_scenario(), facts)
    required = result.limitations
    assert len(required) == 4

    for contradictory in (
        (),
        ("arbitrary", *required),
        (required[1], required[0], *required[2:]),
    ):
        with pytest.raises(ValueError, match="limitations"):
            replace(result, limitations=contradictory)


def test_result_allows_only_deduped_source_limitations_after_required_prefix():
    from kreports.analysis.dcf_model import build_dcf_valuation

    result = build_dcf_valuation(
        _scenario(wacc=None),
        _facts(),
        source_limitations=("source_partial", "source_partial"),
    )

    assert len(result.limitations) == 5
    assert result.limitations[-1] == "source_partial"
    copied = replace(result, limitations=result.limitations)
    assert copied.limitations == result.limitations
    with pytest.raises(ValueError, match="limitations"):
        replace(
            result,
            limitations=(*result.limitations, "source_partial"),
        )
