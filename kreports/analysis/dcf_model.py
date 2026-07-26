"""Deterministic, reviewable DCF domain model.

This module owns validation and Decimal arithmetic only. Database access and
MCP presentation remain in their dedicated adapters.
"""
from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from decimal import Decimal, DecimalException, InvalidOperation, ROUND_HALF_UP
from typing import Any, Literal


MONEY_QUANTUM = Decimal("0.01")
RATE_QUANTUM = Decimal("0.0000000001")
UFCF_FORMULA = "EBIT * (1-tax) + D&A - capex - change_in_NWC"
TERMINAL_FORMULA = "final_UFCF * (1+g) / (wacc-g)"
ROUNDING_POLICY = (
    "Decimal arithmetic; monetary values ROUND_HALF_UP to 0.01 KRW and "
    "discount factors ROUND_HALF_UP to 10 decimal places."
)
_MAX_DECIMAL_TEXT = 128
_MAX_DECIMAL_DIGITS = 38
_MAX_DECIMAL_EXPONENT = 18
_MAX_DECIMAL_ADJUSTED = 30
_MAX_PUBLIC_TEXT = 1_000
_ASSUMPTION_KEYS = (
    "revenue_growth",
    "operating_margin",
    "tax_rate",
    "da_to_revenue",
    "capex_to_revenue",
    "nwc_to_revenue",
    "wacc",
    "terminal_growth",
)
_ACTUAL_METRIC_KEYS = {
    "revenue",
    "operating_profit",
    "depreciation_amortization",
    "purchase_ppe",
    "purchase_intangible_assets",
    "trade_receivables",
    "inventories",
    "trade_payables",
    "cash_and_equivalents",
    "interest_bearing_debt",
}


def _decimal(value: Any, field_name: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError(f"{field_name} must be a finite decimal, not bool")
    rendered = str(value)
    if len(rendered) > _MAX_DECIMAL_TEXT:
        raise ValueError(f"{field_name} exceeds decimal text limit")
    try:
        converted = Decimal(rendered)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{field_name} must be a finite decimal") from exc
    if not converted.is_finite():
        raise ValueError(f"{field_name} must be finite")
    decimal_tuple = converted.as_tuple()
    if (
        len(decimal_tuple.digits) > _MAX_DECIMAL_DIGITS
        or abs(decimal_tuple.exponent) > _MAX_DECIMAL_EXPONENT
        or abs(converted.adjusted()) > _MAX_DECIMAL_ADJUSTED
    ):
        raise ValueError(f"{field_name} exceeds decimal precision bounds")
    return converted


def _required_decimal(value: Any, field_name: str) -> Decimal:
    converted = _decimal(value, field_name)
    if converted is None:
        raise ValueError(f"{field_name} is required")
    return converted


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _rate(value: Decimal) -> Decimal:
    return value.quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)


def _text(
    value: Any,
    field_name: str,
    *,
    optional: bool = False,
) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must be nonblank")
    if len(normalized) > _MAX_PUBLIC_TEXT:
        raise ValueError(f"{field_name} exceeds text limit")
    return normalized


def _year(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    if not 1900 <= value <= 2200:
        raise ValueError(f"{field_name} is outside supported range")
    return value


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
        metric_key = _text(self.metric_key, "metric_key")
        if metric_key not in _ACTUAL_METRIC_KEYS:
            raise ValueError("metric_key is not a DCF actual metric")
        amount = _required_decimal(self.amount, f"{metric_key}.amount")
        if self.fs_div not in {"CFS", "OFS"}:
            raise ValueError("actual fact fs_div must be CFS or OFS")
        object.__setattr__(self, "metric_key", metric_key)
        object.__setattr__(self, "amount", amount)
        object.__setattr__(self, "unit", _text(self.unit, "unit"))
        object.__setattr__(self, "year", _year(self.year, "year"))
        object.__setattr__(
            self,
            "source_account_id",
            _text(
                self.source_account_id,
                "source_account_id",
                optional=True,
            ),
        )
        object.__setattr__(
            self,
            "source_account_name",
            _text(
                self.source_account_name,
                "source_account_name",
                optional=True,
            ),
        )
        object.__setattr__(
            self,
            "source_table",
            _text(self.source_table, "source_table"),
        )
        object.__setattr__(
            self,
            "fetched_at",
            _text(self.fetched_at, "fetched_at", optional=True),
        )


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
        company = _text(self.company, "company")
        object.__setattr__(self, "base_year", _year(self.base_year, "base_year"))
        if self.fs_div not in {"CFS", "OFS"}:
            raise ValueError("fs_div must be CFS or OFS")
        if (
            isinstance(self.forecast_years, bool)
            or not isinstance(self.forecast_years, int)
            or not 1 <= self.forecast_years <= 10
        ):
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
        if self.tax_rate is not None and not 0 <= self.tax_rate <= 1:
            raise ValueError("tax_rate must be between 0 and 1")
        for field_name in ("da_to_revenue", "capex_to_revenue"):
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
        normalization_reason = _text(
            self.normalization_reason,
            "normalization_reason",
            optional=True,
        )
        if (
            self.normalized_revenue is not None
            or self.normalized_operating_profit is not None
        ) and normalization_reason is None:
            raise ValueError("normalization_reason is required for an override")
        object.__setattr__(self, "company", company)
        object.__setattr__(
            self,
            "normalization_reason",
            normalization_reason,
        )


@dataclass(frozen=True)
class DcfNormalizedMetric:
    metric_key: str
    original_actual: Decimal | None
    normalized_amount: Decimal | None
    basis: Literal["actual_unchanged", "analyst_override"]
    reason: str | None

    def __post_init__(self) -> None:
        metric_key = _text(self.metric_key, "normalization.metric_key")
        if metric_key not in {"revenue", "operating_profit"}:
            raise ValueError("normalization.metric_key is unsupported")
        if self.basis not in {"actual_unchanged", "analyst_override"}:
            raise ValueError("normalization.basis is invalid")
        original = _decimal(
            self.original_actual,
            f"normalization.{metric_key}.original_actual",
        )
        normalized = _decimal(
            self.normalized_amount,
            f"normalization.{metric_key}.normalized_amount",
        )
        reason = _text(
            self.reason,
            f"normalization.{metric_key}.reason",
            optional=True,
        )
        if self.basis == "analyst_override" and reason is None:
            raise ValueError("normalization override reason is required")
        if self.basis == "analyst_override" and normalized is None:
            raise ValueError("normalization override amount is required")
        if self.basis == "actual_unchanged" and original != normalized:
            raise ValueError("actual_unchanged normalization must preserve actual")
        object.__setattr__(self, "metric_key", metric_key)
        object.__setattr__(self, "original_actual", original)
        object.__setattr__(self, "normalized_amount", normalized)
        object.__setattr__(self, "reason", reason)


@dataclass(frozen=True)
class DcfNormalization:
    revenue: DcfNormalizedMetric
    operating_profit: DcfNormalizedMetric

    def __post_init__(self) -> None:
        if (
            not isinstance(self.revenue, DcfNormalizedMetric)
            or self.revenue.metric_key != "revenue"
        ):
            raise TypeError("normalization.revenue is invalid")
        if (
            not isinstance(self.operating_profit, DcfNormalizedMetric)
            or self.operating_profit.metric_key != "operating_profit"
        ):
            raise TypeError("normalization.operating_profit is invalid")


@dataclass(frozen=True)
class DcfAssumption:
    key: str
    value: Decimal | None
    basis: Literal["analyst_input"]

    def __post_init__(self) -> None:
        key = _text(self.key, "assumption.key")
        if key not in _ASSUMPTION_KEYS:
            raise ValueError("assumption.key is unsupported")
        if self.basis != "analyst_input":
            raise ValueError("assumption.basis is invalid")
        object.__setattr__(self, "key", key)
        object.__setattr__(
            self,
            "value",
            _decimal(self.value, f"assumption.{key}.value"),
        )


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

    def __post_init__(self) -> None:
        object.__setattr__(self, "year", _year(self.year, "projection.year"))
        for field_name in (
            "revenue",
            "ebit",
            "tax_rate",
            "after_tax_ebit",
            "depreciation_amortization",
            "capex",
            "nwc_balance",
            "nwc_change",
            "ufcf",
            "discount_factor",
            "present_value",
        ):
            object.__setattr__(
                self,
                field_name,
                _required_decimal(
                    getattr(self, field_name),
                    f"projection.{field_name}",
                ),
            )
        if self.formula != UFCF_FORMULA:
            raise ValueError("projection.formula is invalid")
        if not 0 <= self.tax_rate <= 1:
            raise ValueError("projection.tax_rate is invalid")
        if self.capex < 0:
            raise ValueError("projection.capex must be a positive outflow")
        if self.discount_factor <= 0:
            raise ValueError("projection.discount_factor must be positive")
        if self.after_tax_ebit != _money(
            self.ebit * (Decimal(1) - self.tax_rate)
        ):
            raise ValueError("projection after-tax EBIT does not reconcile")
        if self.ufcf != _money(
            self.after_tax_ebit
            + self.depreciation_amortization
            - self.capex
            - self.nwc_change
        ):
            raise ValueError("projection UFCF does not reconcile")
        if self.present_value != _money(
            self.ufcf * self.discount_factor
        ):
            raise ValueError("projection present value does not reconcile")


@dataclass(frozen=True)
class DcfSensitivityCell:
    wacc: Decimal
    terminal_growth: Decimal
    status: Literal["valid", "invalid_rate_pair"]
    enterprise_value: Decimal | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "wacc",
            _required_decimal(self.wacc, "sensitivity.wacc"),
        )
        object.__setattr__(
            self,
            "terminal_growth",
            _required_decimal(
                self.terminal_growth,
                "sensitivity.terminal_growth",
            ),
        )
        object.__setattr__(
            self,
            "enterprise_value",
            _decimal(
                self.enterprise_value,
                "sensitivity.enterprise_value",
            ),
        )
        if self.status not in {"valid", "invalid_rate_pair"}:
            raise ValueError("sensitivity.status is invalid")
        valid_pair = (
            self.wacc > 0
            and self.terminal_growth > -1
            and self.terminal_growth < self.wacc
        )
        if self.status == "valid" and (
            not valid_pair or self.enterprise_value is None
        ):
            raise ValueError("sensitivity.status valid does not reconcile")
        if self.status == "invalid_rate_pair" and (
            valid_pair or self.enterprise_value is not None
        ):
            raise ValueError(
                "sensitivity.status invalid_rate_pair does not reconcile"
            )


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

    def __post_init__(self) -> None:
        company = _text(self.company, "result.company")
        base_year = _year(self.base_year, "result.base_year")
        if self.fs_div not in {"CFS", "OFS"}:
            raise ValueError("result.fs_div is invalid")
        if (
            isinstance(self.forecast_years, bool)
            or not isinstance(self.forecast_years, int)
            or not 1 <= self.forecast_years <= 10
        ):
            raise ValueError("result.forecast_years is invalid")
        if self.status not in {
            "complete_model",
            "partial_model",
            "invalid_model",
        }:
            raise ValueError("result.status is invalid")
        if self.confidence not in {
            "complete_equity",
            "enterprise_complete_equity_partial",
            "partial",
            "invalid",
        }:
            raise ValueError("result.confidence is invalid")
        if self.rounding_policy != ROUNDING_POLICY:
            raise ValueError("result.rounding_policy is invalid")

        tuple_fields = (
            ("actuals", DcfActualFact),
            ("assumptions", DcfAssumption),
            ("projections", DcfProjection),
            ("sensitivity", DcfSensitivityCell),
        )
        for field_name, expected_type in tuple_fields:
            values = tuple(getattr(self, field_name))
            if not all(isinstance(value, expected_type) for value in values):
                raise TypeError(f"result.{field_name} contains invalid values")
            object.__setattr__(self, field_name, values)
        if not isinstance(self.normalization, DcfNormalization):
            raise TypeError("result.normalization is invalid")
        if tuple(item.key for item in self.assumptions) != _ASSUMPTION_KEYS:
            raise ValueError("result assumptions do not match the DCF contract")
        for fact in self.actuals:
            if fact.year != base_year or fact.fs_div != self.fs_div:
                raise ValueError("result actual basis does not reconcile")

        for field_name in (
            "forecast_period_present_value",
            "terminal_value",
            "terminal_value_present_value",
            "enterprise_value",
            "cash",
            "debt",
            "net_debt",
            "equity_value",
        ):
            object.__setattr__(
                self,
                field_name,
                _decimal(getattr(self, field_name), f"result.{field_name}"),
            )
        for field_name in ("missing_inputs", "limitations"):
            values = tuple(
                _text(value, f"result.{field_name}")
                for value in tuple(getattr(self, field_name))
            )
            if len(values) > 32:
                raise ValueError(f"result.{field_name} exceeds item limit")
            object.__setattr__(self, field_name, values)
        object.__setattr__(self, "company", company)
        object.__setattr__(self, "base_year", base_year)

        if self.status == "complete_model":
            if (
                len(self.projections) != self.forecast_years
                or len(self.sensitivity) != 25
                or self.forecast_period_present_value is None
                or self.terminal_value is None
                or self.terminal_value_present_value is None
                or self.enterprise_value is None
            ):
                raise ValueError("complete result shape does not reconcile")
            if self.confidence not in {
                "complete_equity",
                "enterprise_complete_equity_partial",
            }:
                raise ValueError("complete result confidence does not reconcile")
            if any(assumption.value is None for assumption in self.assumptions):
                raise ValueError("complete result assumptions are incomplete")
            expected_years = tuple(
                range(base_year + 1, base_year + self.forecast_years + 1)
            )
            if tuple(row.year for row in self.projections) != expected_years:
                raise ValueError("projection years do not reconcile")
            if self.forecast_period_present_value != _money(
                sum(row.present_value for row in self.projections)
            ):
                raise ValueError("forecast present value does not reconcile")
            if self.enterprise_value != _money(
                self.forecast_period_present_value
                + self.terminal_value_present_value
            ):
                raise ValueError("enterprise value does not reconcile")
            assumptions = {
                assumption.key: assumption.value
                for assumption in self.assumptions
            }
            wacc = assumptions["wacc"]
            terminal_growth = assumptions["terminal_growth"]
            assert wacc is not None and terminal_growth is not None
            if self.terminal_value != _money(
                self.projections[-1].ufcf
                * (Decimal(1) + terminal_growth)
                / (wacc - terminal_growth)
            ):
                raise ValueError("terminal value does not reconcile")
            if self.terminal_value_present_value != _money(
                self.terminal_value
                * self.projections[-1].discount_factor
            ):
                raise ValueError(
                    "terminal present value does not reconcile"
                )
            center = self.sensitivity[12]
            if (
                center.wacc != wacc
                or center.terminal_growth != terminal_growth
                or center.enterprise_value != self.enterprise_value
            ):
                raise ValueError("sensitivity center does not reconcile")
            offsets = (
                Decimal("-0.02"),
                Decimal("-0.01"),
                Decimal("0"),
                Decimal("0.01"),
                Decimal("0.02"),
            )
            expected_pairs = tuple(
                (wacc + wacc_offset, terminal_growth + growth_offset)
                for wacc_offset in offsets
                for growth_offset in offsets
            )
            if tuple(
                (cell.wacc, cell.terminal_growth)
                for cell in self.sensitivity
            ) != expected_pairs:
                raise ValueError("sensitivity axes do not reconcile")
            for cell in self.sensitivity:
                if cell.status != "valid":
                    continue
                expected_enterprise_value = _enterprise_value_for_rates(
                    self.projections,
                    cell.wacc,
                    cell.terminal_growth,
                )[2]
                if cell.enterprise_value != expected_enterprise_value:
                    raise ValueError("sensitivity value does not reconcile")
        else:
            expected_confidence = (
                "partial" if self.status == "partial_model" else "invalid"
            )
            if self.confidence != expected_confidence:
                raise ValueError(
                    "partial or invalid result confidence does not reconcile"
                )
            if any((
                self.projections,
                self.sensitivity,
                self.forecast_period_present_value is not None,
                self.terminal_value is not None,
                self.terminal_value_present_value is not None,
                self.enterprise_value is not None,
                self.equity_value is not None,
            )):
                raise ValueError("partial or invalid result does not reconcile")

        if self.cash is not None and self.debt is not None:
            expected_net_debt = _money(self.debt - self.cash)
            if self.net_debt != expected_net_debt:
                raise ValueError("net debt does not reconcile")
            if (
                self.enterprise_value is not None
                and self.equity_value
                != _money(self.enterprise_value - self.debt + self.cash)
            ):
                raise ValueError("equity value does not reconcile")
        elif self.net_debt is not None or self.equity_value is not None:
            raise ValueError("equity bridge does not reconcile")


_ASSUMPTION_FIELDS = _ASSUMPTION_KEYS
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
        wacc = center_wacc + wacc_offset
        for growth_offset in offsets:
            growth = center_growth + growth_offset
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


def _build_dcf_valuation_unchecked(
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
        invalid_cash = (
            by_metric.get("cash_and_equivalents").amount
            if by_metric.get("cash_and_equivalents") else None
        )
        invalid_debt = (
            by_metric.get("interest_bearing_debt").amount
            if by_metric.get("interest_bearing_debt") else None
        )
        return DcfValuationResult(
            status="invalid_model",
            confidence="invalid",
            projections=(),
            forecast_period_present_value=None,
            terminal_value=None,
            terminal_value_present_value=None,
            enterprise_value=None,
            cash=invalid_cash,
            debt=invalid_debt,
            net_debt=(
                _money(invalid_debt - invalid_cash)
                if invalid_debt is not None and invalid_cash is not None
                else None
            ),
            equity_value=None,
            sensitivity=(),
            missing_inputs=invalid_missing,
            **{key: value for key, value in common.items() if key != "missing_inputs"},
        )
    if any(key in missing for key in (*_ASSUMPTION_FIELDS, *_ENTERPRISE_ACTUALS)):
        partial_cash = (
            by_metric.get("cash_and_equivalents").amount
            if by_metric.get("cash_and_equivalents") else None
        )
        partial_debt = (
            by_metric.get("interest_bearing_debt").amount
            if by_metric.get("interest_bearing_debt") else None
        )
        return DcfValuationResult(
            status="partial_model",
            confidence="partial",
            projections=(),
            forecast_period_present_value=None,
            terminal_value=None,
            terminal_value_present_value=None,
            enterprise_value=None,
            cash=partial_cash,
            debt=partial_debt,
            net_debt=(
                _money(partial_debt - partial_cash)
                if partial_debt is not None and partial_cash is not None
                else None
            ),
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


def build_dcf_valuation(
    scenario: DcfScenarioInput,
    actuals: tuple[DcfActualFact, ...],
    *,
    source_missing: tuple[str, ...] = (),
    source_limitations: tuple[str, ...] = (),
) -> DcfValuationResult:
    """Build a DCF result and fail typed on bounded Decimal arithmetic errors."""
    immutable_actuals = tuple(actuals)
    if not isinstance(scenario, DcfScenarioInput):
        raise TypeError("scenario must be DcfScenarioInput")
    if not all(isinstance(fact, DcfActualFact) for fact in immutable_actuals):
        raise TypeError("actuals must contain only DcfActualFact")
    try:
        return _build_dcf_valuation_unchecked(
            scenario,
            immutable_actuals,
            source_missing=tuple(source_missing),
            source_limitations=tuple(source_limitations),
        )
    except DecimalException as exc:
        by_metric = _actual_map(scenario, immutable_actuals)
        cash = (
            by_metric["cash_and_equivalents"].amount
            if "cash_and_equivalents" in by_metric else None
        )
        debt = (
            by_metric["interest_bearing_debt"].amount
            if "interest_bearing_debt" in by_metric else None
        )
        try:
            net_debt = (
                _money(debt - cash)
                if debt is not None and cash is not None else None
            )
        except DecimalException:
            cash = None
            debt = None
            net_debt = None
        return DcfValuationResult(
            company=scenario.company,
            base_year=scenario.base_year,
            fs_div=scenario.fs_div,
            forecast_years=scenario.forecast_years,
            status="invalid_model",
            confidence="invalid",
            actuals=immutable_actuals,
            normalization=_normalization(scenario, by_metric),
            assumptions=_assumptions(scenario),
            projections=(),
            forecast_period_present_value=None,
            terminal_value=None,
            terminal_value_present_value=None,
            enterprise_value=None,
            cash=cash,
            debt=debt,
            net_debt=net_debt,
            equity_value=None,
            sensitivity=(),
            missing_inputs=tuple(dict.fromkeys(
                (*source_missing, "arithmetic_invalid")
            )),
            limitations=tuple(dict.fromkeys((
                *_LIMITATIONS,
                *source_limitations,
                f"arithmetic_invalid:{type(exc).__name__}",
            ))),
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
        "gordon_growth_formula": TERMINAL_FORMULA,
        "final_year_discount_factor": (
            payload["projections"][-1]["discount_factor"]
            if payload["projections"] else None
        ),
        "enterprise_value_formula": (
            "enterprise_value = forecast_period_present_value + "
            "terminal_value_present_value"
        ),
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
