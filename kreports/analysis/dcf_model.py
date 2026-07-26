"""Deterministic, reviewable DCF domain model.

This module owns validation and Decimal arithmetic only. Database access and
MCP presentation remain in their dedicated adapters.
"""
from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Literal


MONEY_QUANTUM = Decimal("0.01")
RATE_QUANTUM = Decimal("0.0000000001")
GRID_QUANTUM = Decimal("0.0001")
UFCF_FORMULA = "EBIT * (1-tax) + D&A - capex - change_in_NWC"
TERMINAL_FORMULA = "final_UFCF * (1+g) / (wacc-g)"
ROUNDING_POLICY = (
    "Decimal arithmetic; monetary values ROUND_HALF_UP to 0.01 KRW and "
    "discount factors ROUND_HALF_UP to 10 decimal places."
)


def _decimal(value: Any, field_name: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError(f"{field_name} must be a finite decimal, not bool")
    try:
        converted = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{field_name} must be a finite decimal") from exc
    if not converted.is_finite():
        raise ValueError(f"{field_name} must be finite")
    return converted


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _rate(value: Decimal) -> Decimal:
    return value.quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class DcfActualFact:
    metric_key: str
    amount: Decimal
    unit: str
    year: int
    fs_div: Literal["CFS", "OFS"]
    source_account_id: str | None
    source_account_name: str | None
    source_table: str
    fetched_at: str | None

    def __post_init__(self) -> None:
        amount = _decimal(self.amount, f"{self.metric_key}.amount")
        if not self.metric_key or amount is None:
            raise ValueError("actual fact requires metric_key and amount")
        if self.fs_div not in {"CFS", "OFS"}:
            raise ValueError("actual fact fs_div must be CFS or OFS")
        object.__setattr__(self, "amount", amount)


@dataclass(frozen=True)
class DcfScenarioInput:
    company: str
    base_year: int
    fs_div: Literal["CFS", "OFS"]
    forecast_years: int = 5
    revenue_growth: Decimal | None = None
    operating_margin: Decimal | None = None
    tax_rate: Decimal | None = None
    da_to_revenue: Decimal | None = None
    capex_to_revenue: Decimal | None = None
    nwc_to_revenue: Decimal | None = None
    wacc: Decimal | None = None
    terminal_growth: Decimal | None = None
    normalized_revenue: Decimal | None = None
    normalized_operating_profit: Decimal | None = None
    normalization_reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.company, str) or not self.company.strip():
            raise ValueError("company must be nonblank")
        if isinstance(self.base_year, bool) or not isinstance(self.base_year, int):
            raise TypeError("base_year must be an integer")
        if self.fs_div not in {"CFS", "OFS"}:
            raise ValueError("fs_div must be CFS or OFS")
        if isinstance(self.forecast_years, bool) or not 1 <= self.forecast_years <= 10:
            raise ValueError("forecast_years must be between 1 and 10")

        decimal_fields = (
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
        )
        for field_name in decimal_fields:
            object.__setattr__(
                self,
                field_name,
                _decimal(getattr(self, field_name), field_name),
            )
        if self.revenue_growth is not None and self.revenue_growth <= -1:
            raise ValueError("revenue_growth must be greater than -1")
        if self.operating_margin is not None and self.operating_margin <= -1:
            raise ValueError("operating_margin must be greater than -1")
        if self.tax_rate is not None and not 0 <= self.tax_rate <= 1:
            raise ValueError("tax_rate must be between 0 and 1")
        for field_name in ("da_to_revenue", "capex_to_revenue", "nwc_to_revenue"):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                raise ValueError(f"{field_name} must be non-negative")
        if self.wacc is not None and self.wacc <= 0:
            raise ValueError("wacc must be greater than zero")
        if self.terminal_growth is not None and self.terminal_growth <= -1:
            raise ValueError("terminal_growth must be greater than -1")
        if (
            self.wacc is not None
            and self.terminal_growth is not None
            and self.terminal_growth >= self.wacc
        ):
            raise ValueError("terminal_growth must be less than wacc")
        if self.normalized_revenue is not None and self.normalized_revenue <= 0:
            raise ValueError("normalized_revenue must be greater than zero")
        if (
            self.normalized_revenue is not None
            or self.normalized_operating_profit is not None
        ) and not str(self.normalization_reason or "").strip():
            raise ValueError("normalization_reason is required for an override")
        object.__setattr__(self, "company", self.company.strip())
        object.__setattr__(
            self,
            "normalization_reason",
            str(self.normalization_reason).strip()
            if self.normalization_reason is not None
            else None,
        )


@dataclass(frozen=True)
class DcfNormalizedMetric:
    metric_key: str
    original_actual: Decimal | None
    normalized_amount: Decimal | None
    basis: Literal["actual_unchanged", "analyst_override"]
    reason: str | None


@dataclass(frozen=True)
class DcfNormalization:
    revenue: DcfNormalizedMetric
    operating_profit: DcfNormalizedMetric


@dataclass(frozen=True)
class DcfAssumption:
    key: str
    value: Decimal | None
    basis: Literal["analyst_input"]


@dataclass(frozen=True)
class DcfProjection:
    year: int
    revenue: Decimal
    ebit: Decimal
    tax_rate: Decimal
    after_tax_ebit: Decimal
    depreciation_amortization: Decimal
    capex: Decimal
    nwc_balance: Decimal
    nwc_change: Decimal
    ufcf: Decimal
    discount_factor: Decimal
    present_value: Decimal
    formula: str = UFCF_FORMULA


@dataclass(frozen=True)
class DcfSensitivityCell:
    wacc: Decimal
    terminal_growth: Decimal
    status: Literal["valid", "invalid_rate_pair"]
    enterprise_value: Decimal | None


@dataclass(frozen=True)
class DcfValuationResult:
    company: str
    base_year: int
    fs_div: Literal["CFS", "OFS"]
    forecast_years: int
    status: Literal["complete_model", "partial_model", "invalid_model"]
    confidence: str
    actuals: tuple[DcfActualFact, ...]
    normalization: DcfNormalization
    assumptions: tuple[DcfAssumption, ...]
    projections: tuple[DcfProjection, ...]
    forecast_period_present_value: Decimal | None
    terminal_value: Decimal | None
    terminal_value_present_value: Decimal | None
    enterprise_value: Decimal | None
    cash: Decimal | None
    debt: Decimal | None
    net_debt: Decimal | None
    equity_value: Decimal | None
    sensitivity: tuple[DcfSensitivityCell, ...]
    missing_inputs: tuple[str, ...]
    limitations: tuple[str, ...]
    rounding_policy: str = ROUNDING_POLICY


_ASSUMPTION_FIELDS = (
    "revenue_growth",
    "operating_margin",
    "tax_rate",
    "da_to_revenue",
    "capex_to_revenue",
    "nwc_to_revenue",
    "wacc",
    "terminal_growth",
)
_ENTERPRISE_ACTUALS = (
    "revenue",
    "operating_profit",
    "depreciation_amortization",
    "purchase_ppe",
    "purchase_intangible_assets",
    "trade_receivables",
    "inventories",
    "trade_payables",
)
_LIMITATIONS = (
    "Operating NWC is limited to receivables + inventory - payables; taxes, provisions, and other operating balances are excluded.",
    "Capex is modeled as a positive cash outflow; negative source cash-flow signs are normalized with the source amounts preserved in actuals.",
    "The equity bridge includes only interest-bearing debt and cash; minority interest, associates, options, and other non-operating assets are excluded unless separately sourced.",
    "This is a reviewable model, not investment advice, a fairness opinion, an approved forecast, or an audit conclusion.",
)


def _actual_map(
    scenario: DcfScenarioInput,
    actuals: tuple[DcfActualFact, ...],
) -> dict[str, DcfActualFact]:
    selected: dict[str, DcfActualFact] = {}
    duplicate_keys: set[str] = set()
    for fact in actuals:
        if fact.year != scenario.base_year or fact.fs_div != scenario.fs_div:
            continue
        if fact.metric_key in selected:
            duplicate_keys.add(fact.metric_key)
        selected[fact.metric_key] = fact
    for key in duplicate_keys:
        selected.pop(key, None)
    return selected


def _normalization(
    scenario: DcfScenarioInput,
    by_metric: dict[str, DcfActualFact],
) -> DcfNormalization:
    revenue_actual = by_metric.get("revenue")
    operating_actual = by_metric.get("operating_profit")
    revenue_override = scenario.normalized_revenue
    operating_override = scenario.normalized_operating_profit
    return DcfNormalization(
        revenue=DcfNormalizedMetric(
            metric_key="revenue",
            original_actual=revenue_actual.amount if revenue_actual else None,
            normalized_amount=(
                revenue_override
                if revenue_override is not None
                else revenue_actual.amount if revenue_actual else None
            ),
            basis="analyst_override" if revenue_override is not None else "actual_unchanged",
            reason=scenario.normalization_reason if revenue_override is not None else None,
        ),
        operating_profit=DcfNormalizedMetric(
            metric_key="operating_profit",
            original_actual=operating_actual.amount if operating_actual else None,
            normalized_amount=(
                operating_override
                if operating_override is not None
                else operating_actual.amount if operating_actual else None
            ),
            basis="analyst_override" if operating_override is not None else "actual_unchanged",
            reason=scenario.normalization_reason if operating_override is not None else None,
        ),
    )


def _assumptions(scenario: DcfScenarioInput) -> tuple[DcfAssumption, ...]:
    return tuple(
        DcfAssumption(
            key=key,
            value=getattr(scenario, key),
            basis="analyst_input",
        )
        for key in _ASSUMPTION_FIELDS
    )


def _enterprise_value_for_rates(
    projections: tuple[DcfProjection, ...],
    wacc: Decimal,
    terminal_growth: Decimal,
) -> tuple[Decimal, Decimal, Decimal]:
    discount_factors = tuple(
        _rate(Decimal(1) / ((Decimal(1) + wacc) ** index))
        for index in range(1, len(projections) + 1)
    )
    forecast_pv = _money(sum(
        _money(projection.ufcf * discount_factor)
        for projection, discount_factor in zip(
            projections,
            discount_factors,
            strict=True,
        )
    ))
    terminal = _money(
        projections[-1].ufcf
        * (Decimal(1) + terminal_growth)
        / (wacc - terminal_growth)
    )
    terminal_pv = _money(
        terminal
        * discount_factors[-1]
    )
    return forecast_pv, terminal, _money(forecast_pv + terminal_pv)


def _sensitivity(
    projections: tuple[DcfProjection, ...],
    center_wacc: Decimal,
    center_growth: Decimal,
) -> tuple[DcfSensitivityCell, ...]:
    offsets = (
        Decimal("-0.02"),
        Decimal("-0.01"),
        Decimal("0"),
        Decimal("0.01"),
        Decimal("0.02"),
    )
    cells: list[DcfSensitivityCell] = []
    for wacc_offset in offsets:
        wacc = (center_wacc + wacc_offset).quantize(GRID_QUANTUM)
        for growth_offset in offsets:
            growth = (center_growth + growth_offset).quantize(GRID_QUANTUM)
            if wacc <= 0 or growth <= -1 or growth >= wacc:
                cells.append(DcfSensitivityCell(
                    wacc=wacc,
                    terminal_growth=growth,
                    status="invalid_rate_pair",
                    enterprise_value=None,
                ))
                continue
            _, _, enterprise_value = _enterprise_value_for_rates(
                projections,
                wacc,
                growth,
            )
            cells.append(DcfSensitivityCell(
                wacc=wacc,
                terminal_growth=growth,
                status="valid",
                enterprise_value=enterprise_value,
            ))
    return tuple(cells)


def build_dcf_valuation(
    scenario: DcfScenarioInput,
    actuals: tuple[DcfActualFact, ...],
    *,
    source_missing: tuple[str, ...] = (),
    source_limitations: tuple[str, ...] = (),
) -> DcfValuationResult:
    """Build a DCF result without filling missing assumptions or source facts."""
    immutable_actuals = tuple(actuals)
    by_metric = _actual_map(scenario, immutable_actuals)
    normalization = _normalization(scenario, by_metric)
    assumptions = _assumptions(scenario)
    missing = [
        key for key in _ASSUMPTION_FIELDS if getattr(scenario, key) is None
    ]
    missing.extend(
        key for key in _ENTERPRISE_ACTUALS if key not in by_metric
    )
    missing.extend(source_missing)
    missing = list(dict.fromkeys(missing))[:32]

    common = {
        "company": scenario.company,
        "base_year": scenario.base_year,
        "fs_div": scenario.fs_div,
        "forecast_years": scenario.forecast_years,
        "actuals": immutable_actuals,
        "normalization": normalization,
        "assumptions": assumptions,
        "missing_inputs": tuple(missing),
        "limitations": tuple(dict.fromkeys((*_LIMITATIONS, *source_limitations)))[:32],
    }
    if (
        normalization.revenue.normalized_amount is not None
        and normalization.revenue.normalized_amount <= 0
    ):
        invalid_missing = tuple(
            dict.fromkeys((*missing, "base_revenue_nonpositive"))
        )
        return DcfValuationResult(
            status="invalid_model",
            confidence="invalid",
            projections=(),
            forecast_period_present_value=None,
            terminal_value=None,
            terminal_value_present_value=None,
            enterprise_value=None,
            cash=by_metric.get("cash_and_equivalents").amount
            if by_metric.get("cash_and_equivalents") else None,
            debt=by_metric.get("interest_bearing_debt").amount
            if by_metric.get("interest_bearing_debt") else None,
            net_debt=None,
            equity_value=None,
            sensitivity=(),
            missing_inputs=invalid_missing,
            **{key: value for key, value in common.items() if key != "missing_inputs"},
        )
    if any(key in missing for key in (*_ASSUMPTION_FIELDS, *_ENTERPRISE_ACTUALS)):
        return DcfValuationResult(
            status="partial_model",
            confidence="partial",
            projections=(),
            forecast_period_present_value=None,
            terminal_value=None,
            terminal_value_present_value=None,
            enterprise_value=None,
            cash=by_metric.get("cash_and_equivalents").amount
            if by_metric.get("cash_and_equivalents") else None,
            debt=by_metric.get("interest_bearing_debt").amount
            if by_metric.get("interest_bearing_debt") else None,
            net_debt=None,
            equity_value=None,
            sensitivity=(),
            **common,
        )

    revenue = normalization.revenue.normalized_amount
    assert revenue is not None
    base_nwc = _money(
        by_metric["trade_receivables"].amount
        + by_metric["inventories"].amount
        - by_metric["trade_payables"].amount
    )
    projections: list[DcfProjection] = []
    for index in range(1, scenario.forecast_years + 1):
        revenue = _money(revenue * (Decimal(1) + scenario.revenue_growth))
        ebit = _money(revenue * scenario.operating_margin)
        after_tax_ebit = _money(ebit * (Decimal(1) - scenario.tax_rate))
        da = _money(revenue * scenario.da_to_revenue)
        capex = _money(revenue * scenario.capex_to_revenue)
        nwc_balance = _money(revenue * scenario.nwc_to_revenue)
        nwc_change = _money(nwc_balance - base_nwc)
        ufcf = _money(after_tax_ebit + da - capex - nwc_change)
        discount_factor = _rate(
            Decimal(1) / ((Decimal(1) + scenario.wacc) ** index)
        )
        present_value = _money(ufcf * discount_factor)
        projections.append(DcfProjection(
            year=scenario.base_year + index,
            revenue=revenue,
            ebit=ebit,
            tax_rate=scenario.tax_rate,
            after_tax_ebit=after_tax_ebit,
            depreciation_amortization=da,
            capex=capex,
            nwc_balance=nwc_balance,
            nwc_change=nwc_change,
            ufcf=ufcf,
            discount_factor=discount_factor,
            present_value=present_value,
        ))
        base_nwc = nwc_balance

    immutable_projections = tuple(projections)
    forecast_pv = _money(sum(row.present_value for row in immutable_projections))
    terminal_value = _money(
        immutable_projections[-1].ufcf
        * (Decimal(1) + scenario.terminal_growth)
        / (scenario.wacc - scenario.terminal_growth)
    )
    terminal_pv = _money(
        terminal_value * immutable_projections[-1].discount_factor
    )
    enterprise_value = _money(forecast_pv + terminal_pv)
    cash_fact = by_metric.get("cash_and_equivalents")
    debt_fact = by_metric.get("interest_bearing_debt")
    cash = cash_fact.amount if cash_fact else None
    debt = debt_fact.amount if debt_fact else None
    net_debt = _money(debt - cash) if debt is not None and cash is not None else None
    equity_value = (
        _money(enterprise_value - debt + cash)
        if debt is not None and cash is not None
        else None
    )
    bridge_missing = []
    if cash is None:
        bridge_missing.append("cash_and_equivalents")
    if debt is None:
        bridge_missing.append("interest_bearing_debt")
    all_missing = tuple(dict.fromkeys((*missing, *bridge_missing)))
    return DcfValuationResult(
        status="complete_model",
        confidence=(
            "complete_equity"
            if equity_value is not None
            else "enterprise_complete_equity_partial"
        ),
        projections=immutable_projections,
        forecast_period_present_value=forecast_pv,
        terminal_value=terminal_value,
        terminal_value_present_value=terminal_pv,
        enterprise_value=enterprise_value,
        cash=cash,
        debt=debt,
        net_debt=net_debt,
        equity_value=equity_value,
        sensitivity=_sensitivity(
            immutable_projections,
            scenario.wacc,
            scenario.terminal_growth,
        ),
        missing_inputs=all_missing,
        **{key: value for key, value in common.items() if key != "missing_inputs"},
    )


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        rendered = format(value, "f")
        if "." not in rendered:
            return f"{rendered}.00"
        whole, fractional = rendered.split(".", 1)
        return f"{whole}.{fractional.ljust(2, '0')}"
    if is_dataclass(value):
        return {
            field.name: _json_value(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


def dcf_result_to_dict(result: DcfValuationResult) -> dict[str, Any]:
    """Serialize the immutable model with Decimals represented exactly as strings."""
    payload = _json_value(result)
    normalization = payload.pop("normalization")
    assumptions = payload.pop("assumptions")
    payload["normalization"] = [
        normalization["revenue"],
        normalization["operating_profit"],
    ]
    payload["assumptions"] = assumptions
    payload["valuation_bridge"] = {
        "forecast_period_present_value": payload["forecast_period_present_value"],
        "terminal_value": payload["terminal_value"],
        "terminal_value_present_value": payload["terminal_value_present_value"],
        "enterprise_value": payload["enterprise_value"],
        "debt": payload["debt"],
        "cash": payload["cash"],
        "net_debt": payload["net_debt"],
        "equity_value": payload["equity_value"],
        "formula": "equity_value = enterprise_value - debt + cash",
    }
    payload["formulas"] = {
        "ufcf": UFCF_FORMULA,
        "terminal_value": TERMINAL_FORMULA,
    }
    payload["disclaimer"] = (
        "Reviewable model only; not investment advice, a fairness opinion, "
        "an approved forecast, or an audit conclusion."
    )
    return payload
