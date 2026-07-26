"""Deterministic, reviewable DCF domain model.

This module owns validation and Decimal arithmetic only. Database access and
MCP presentation remain in their dedicated adapters.
"""
from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from decimal import Decimal, DecimalException, InvalidOperation, ROUND_HALF_UP
from typing import Any, Literal

from kreports.semantic.metrics import DCF_MODEL_METRICS


MONEY_QUANTUM = Decimal("0.01")
RATE_QUANTUM = Decimal("0.0000000001")
UFCF_FORMULA = "EBIT * (1-tax) + D&A - capex - change_in_NWC"
TERMINAL_FORMULA = "final_UFCF * (1+g) / (wacc-g)"
ROUNDING_POLICY = (
    "Decimal arithmetic; monetary values ROUND_HALF_UP to 0.01 KRW and "
    "discount factors ROUND_HALF_UP to 10 decimal places."
)
MAX_REVENUE_GROWTH = Decimal("10")
MAX_ABS_OPERATING_RATIO = Decimal("10")
MAX_WACC = Decimal("1")
MAX_TERMINAL_GROWTH = Decimal("1")
MAX_ABS_BASE_AMOUNT = Decimal("1E+24")
MAX_COMPANY_LENGTH = 200
MAX_NORMALIZATION_REASON_LENGTH = 1_000
MIN_DECIMAL_ADJUSTED = -100
_MAX_DECIMAL_TEXT = 128
_MAX_DECIMAL_DIGITS = 38
_MAX_DECIMAL_ADJUSTED = 30
_MAX_JSON_DECIMAL_CHARS = 128
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
_ACTUAL_METRIC_KEYS = frozenset(DCF_MODEL_METRICS)
_NONNEGATIVE_ACTUAL_METRICS = frozenset({
    "depreciation_amortization",
    "trade_receivables",
    "inventories",
    "trade_payables",
    "cash_and_equivalents",
    "interest_bearing_debt",
})


class _DcfConstructionError(ValueError):
    """A bounded internal domain-construction failure."""


def dcf_decimal_fits_serialization(value: Decimal) -> bool:
    """Return whether fixed-point JSON rendering stays within its hard cap."""
    if not value.is_finite():
        return False
    decimal_tuple = value.as_tuple()
    digit_count = len(decimal_tuple.digits)
    exponent = decimal_tuple.exponent
    if not isinstance(exponent, int):
        return False
    sign_length = 1 if decimal_tuple.sign else 0
    if exponent >= 0:
        rendered_length = sign_length + digit_count + exponent + 3
    elif -exponent < digit_count:
        rendered_length = sign_length + digit_count + 1
        rendered_length += max(0, 2 + exponent)
    else:
        rendered_length = sign_length + 2 - exponent
        rendered_length += max(0, 2 + exponent)
    return rendered_length <= _MAX_JSON_DECIMAL_CHARS


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
    significant_digits = list(decimal_tuple.digits)
    while len(significant_digits) > 1 and significant_digits[-1] == 0:
        significant_digits.pop()
    if (
        len(significant_digits) > _MAX_DECIMAL_DIGITS
        or not dcf_decimal_fits_serialization(converted)
        or (
            converted != 0
            and not (
                MIN_DECIMAL_ADJUSTED
                <= converted.adjusted()
                <= _MAX_DECIMAL_ADJUSTED
            )
        )
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
    max_length: int = _MAX_PUBLIC_TEXT,
) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must be nonblank")
    if len(normalized) > max_length:
        raise ValueError(f"{field_name} exceeds text limit")
    return normalized


def _year(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    if not 1900 <= value <= 2200:
        raise ValueError(f"{field_name} is outside supported range")
    return value


def parse_dcf_timestamp(
    value: Any,
    field_name: str = "fetched_at",
) -> datetime | None:
    """Parse the bounded ISO timestamp contract used for DCF provenance."""
    rendered = _text(value, field_name, optional=True)
    if rendered is None:
        return None
    if "T" not in rendered and " " not in rendered:
        raise ValueError(f"{field_name} must be an ISO timestamp")
    candidate = (
        f"{rendered[:-1]}+00:00"
        if rendered.endswith("Z")
        else rendered
    )
    try:
        return datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO timestamp") from exc


@dataclass(frozen=True)
class DcfActualFact:
    metric_key: str
    amount: Decimal
    unit: str
    year: int
    fs_div: Literal["CFS", "OFS"]
    source_account_id: str
    source_account_name: str
    source_table: str
    fetched_at: str | None

    def __post_init__(self) -> None:
        metric_key = _text(self.metric_key, "metric_key")
        if metric_key not in _ACTUAL_METRIC_KEYS:
            raise ValueError("metric_key is not a DCF actual metric")
        amount = _required_decimal(self.amount, f"{metric_key}.amount")
        if abs(amount) > MAX_ABS_BASE_AMOUNT:
            raise ValueError(
                f"{metric_key}.amount exceeds the supported bound"
            )
        if metric_key == "revenue" and amount <= 0:
            raise ValueError("revenue.amount must be positive")
        if (
            metric_key in _NONNEGATIVE_ACTUAL_METRICS
            and amount < 0
        ):
            raise ValueError(f"{metric_key}.amount must be non-negative")
        if self.fs_div not in {"CFS", "OFS"}:
            raise ValueError("actual fact fs_div must be CFS or OFS")
        unit = _text(self.unit, "unit")
        if unit != "KRW":
            raise ValueError("unit must be canonical KRW")
        source_table = _text(self.source_table, "source_table")
        if source_table != "financial_facts_compact":
            raise ValueError(
                "source_table must be financial_facts_compact"
            )
        object.__setattr__(self, "metric_key", metric_key)
        object.__setattr__(self, "amount", amount)
        object.__setattr__(self, "unit", unit)
        object.__setattr__(self, "year", _year(self.year, "year"))
        object.__setattr__(
            self,
            "source_account_id",
            _text(self.source_account_id, "source_account_id"),
        )
        object.__setattr__(
            self,
            "source_account_name",
            _text(self.source_account_name, "source_account_name"),
        )
        object.__setattr__(
            self,
            "source_table",
            source_table,
        )
        fetched_at = _text(self.fetched_at, "fetched_at", optional=True)
        parse_dcf_timestamp(fetched_at)
        object.__setattr__(self, "fetched_at", fetched_at)


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
        company = _text(
            self.company,
            "company",
            max_length=MAX_COMPANY_LENGTH,
        )
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
        if (
            self.revenue_growth is not None
            and self.revenue_growth > MAX_REVENUE_GROWTH
        ):
            raise ValueError(
                f"revenue_growth must be at most {MAX_REVENUE_GROWTH}"
            )
        if (
            self.operating_margin is not None
            and abs(self.operating_margin) > MAX_ABS_OPERATING_RATIO
        ):
            raise ValueError(
                "operating_margin exceeds the supported absolute bound"
            )
        if self.tax_rate is not None and not 0 <= self.tax_rate <= 1:
            raise ValueError("tax_rate must be between 0 and 1")
        for field_name in ("da_to_revenue", "capex_to_revenue"):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                raise ValueError(f"{field_name} must be non-negative")
            if value is not None and value > MAX_ABS_OPERATING_RATIO:
                raise ValueError(f"{field_name} exceeds the supported bound")
        if (
            self.nwc_to_revenue is not None
            and abs(self.nwc_to_revenue) > MAX_ABS_OPERATING_RATIO
        ):
            raise ValueError(
                "nwc_to_revenue exceeds the supported absolute bound"
            )
        if self.wacc is not None and self.wacc <= 0:
            raise ValueError("wacc must be greater than zero")
        if self.wacc is not None and self.wacc > MAX_WACC:
            raise ValueError(f"wacc must be at most {MAX_WACC}")
        if self.terminal_growth is not None and self.terminal_growth <= -1:
            raise ValueError("terminal_growth must be greater than -1")
        if (
            self.terminal_growth is not None
            and self.terminal_growth > MAX_TERMINAL_GROWTH
        ):
            raise ValueError(
                f"terminal_growth must be at most {MAX_TERMINAL_GROWTH}"
            )
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
            and self.normalized_revenue > MAX_ABS_BASE_AMOUNT
        ):
            raise ValueError(
                "normalized_revenue exceeds the supported bound"
            )
        if (
            self.normalized_operating_profit is not None
            and abs(self.normalized_operating_profit)
            > MAX_ABS_BASE_AMOUNT
        ):
            raise ValueError(
                "normalized_operating_profit exceeds the supported bound"
            )
        normalization_reason = _text(
            self.normalization_reason,
            "normalization_reason",
            optional=True,
            max_length=MAX_NORMALIZATION_REASON_LENGTH,
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
        for field_name, amount in (
            ("original_actual", original),
            ("normalized_amount", normalized),
        ):
            if amount is None:
                continue
            if abs(amount) > MAX_ABS_BASE_AMOUNT:
                raise ValueError(
                    f"normalization.{metric_key}.{field_name} "
                    "exceeds the supported bound"
                )
            if metric_key == "revenue" and amount <= 0:
                raise ValueError(
                    f"normalization.revenue.{field_name} must be positive"
                )
        if self.basis == "actual_unchanged" and reason is not None:
            raise ValueError(
                "actual_unchanged normalization reason must be None"
            )
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
        if self.revenue <= 0:
            raise ValueError("projection.revenue must be positive")
        if not 0 <= self.tax_rate <= 1:
            raise ValueError("projection.tax_rate is invalid")
        if self.depreciation_amortization < 0:
            raise ValueError(
                "projection.depreciation_amortization must be non-negative"
            )
        if self.capex < 0:
            raise ValueError("projection.capex must be a positive outflow")
        if not 0 < self.discount_factor <= 1:
            raise ValueError(
                "projection.discount_factor must be within (0, 1]"
            )
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
        normalization = DcfNormalization(
            revenue=DcfNormalizedMetric(**{
                field.name: getattr(
                    self.normalization.revenue,
                    field.name,
                )
                for field in fields(DcfNormalizedMetric)
            }),
            operating_profit=DcfNormalizedMetric(**{
                field.name: getattr(
                    self.normalization.operating_profit,
                    field.name,
                )
                for field in fields(DcfNormalizedMetric)
            }),
        )
        object.__setattr__(self, "normalization", normalization)
        if tuple(item.key for item in self.assumptions) != _ASSUMPTION_KEYS:
            raise ValueError("result assumptions do not match the DCF contract")
        assumption_values = {
            assumption.key: assumption.value
            for assumption in self.assumptions
        }
        override_reasons = {
            metric.reason
            for metric in (
                normalization.revenue,
                normalization.operating_profit,
            )
            if metric.basis == "analyst_override"
        }
        if len(override_reasons) > 1:
            raise ValueError(
                "normalization override reasons do not reconcile"
            )
        normalization_reason = next(iter(override_reasons), None)
        DcfScenarioInput(
            company=company,
            base_year=base_year,
            fs_div=self.fs_div,
            forecast_years=self.forecast_years,
            normalized_revenue=(
                normalization.revenue.normalized_amount
                if normalization.revenue.basis == "analyst_override"
                else None
            ),
            normalized_operating_profit=(
                normalization.operating_profit.normalized_amount
                if normalization.operating_profit.basis
                == "analyst_override"
                else None
            ),
            normalization_reason=normalization_reason,
            **assumption_values,
        )
        actuals_by_metric: dict[str, list[DcfActualFact]] = {}
        for fact in self.actuals:
            if fact.year != base_year or fact.fs_div != self.fs_div:
                raise ValueError("result actual basis does not reconcile")
            actuals_by_metric.setdefault(fact.metric_key, []).append(fact)
        for normalized_metric in (
            normalization.revenue,
            normalization.operating_profit,
        ):
            source_facts = actuals_by_metric.get(
                normalized_metric.metric_key,
                [],
            )
            expected_original = (
                source_facts[0].amount if len(source_facts) == 1 else None
            )
            if normalized_metric.original_actual != expected_original:
                raise ValueError(
                    f"normalization {normalized_metric.metric_key} "
                    "does not reconcile"
                )

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
        if (
            self.limitations[:len(_LIMITATIONS)] != _LIMITATIONS
            or len(set(self.limitations)) != len(self.limitations)
        ):
            raise ValueError(
                "result.limitations must preserve the required "
                "ordered disclosures with deduped source additions"
            )
        object.__setattr__(self, "company", company)
        object.__setattr__(self, "base_year", base_year)

        expected_cash = (
            actuals_by_metric["cash_and_equivalents"][0].amount
            if len(actuals_by_metric.get("cash_and_equivalents", [])) == 1
            else None
        )
        expected_debt = (
            actuals_by_metric["interest_bearing_debt"][0].amount
            if len(actuals_by_metric.get("interest_bearing_debt", [])) == 1
            else None
        )
        if self.cash != expected_cash or self.debt != expected_debt:
            raise ValueError("result bridge facts do not reconcile")

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
            expected_bridge_missing = {
                key
                for key, value in (
                    ("cash_and_equivalents", expected_cash),
                    ("interest_bearing_debt", expected_debt),
                )
                if value is None
            }
            expected_confidence = (
                "complete_equity"
                if not expected_bridge_missing
                else "enterprise_complete_equity_partial"
            )
            if (
                self.confidence != expected_confidence
                or set(self.missing_inputs) != expected_bridge_missing
            ):
                raise ValueError(
                    "complete result bridge semantics do not reconcile"
                )
            if any(assumption.value is None for assumption in self.assumptions):
                raise ValueError("complete result assumptions are incomplete")
            expected_years = tuple(
                range(base_year + 1, base_year + self.forecast_years + 1)
            )
            if tuple(row.year for row in self.projections) != expected_years:
                raise ValueError("projection years do not reconcile")
            assumptions = {
                assumption.key: assumption.value
                for assumption in self.assumptions
            }
            revenue_growth = assumptions["revenue_growth"]
            operating_margin = assumptions["operating_margin"]
            tax_rate = assumptions["tax_rate"]
            da_to_revenue = assumptions["da_to_revenue"]
            capex_to_revenue = assumptions["capex_to_revenue"]
            nwc_to_revenue = assumptions["nwc_to_revenue"]
            wacc = assumptions["wacc"]
            terminal_growth = assumptions["terminal_growth"]
            assert all(
                value is not None
                for value in (
                    revenue_growth,
                    operating_margin,
                    tax_rate,
                    da_to_revenue,
                    capex_to_revenue,
                    nwc_to_revenue,
                    wacc,
                    terminal_growth,
                )
            )
            normalized_revenue = (
                self.normalization.revenue.normalized_amount
            )
            if normalized_revenue is None or normalized_revenue <= 0:
                raise ValueError("normalized base revenue does not reconcile")
            for metric_key in _ENTERPRISE_ACTUALS:
                if len(actuals_by_metric.get(metric_key, [])) != 1:
                    raise ValueError(
                        f"complete result actual {metric_key} "
                        "does not reconcile"
                    )
            previous_revenue = normalized_revenue
            previous_nwc = _money(
                actuals_by_metric["trade_receivables"][0].amount
                + actuals_by_metric["inventories"][0].amount
                - actuals_by_metric["trade_payables"][0].amount
            )
            for index, projection in enumerate(self.projections, 1):
                expected_revenue = _money(
                    previous_revenue
                    * (Decimal(1) + revenue_growth)
                )
                expected_ebit = _money(
                    expected_revenue * operating_margin
                )
                expected_after_tax = _money(
                    expected_ebit * (Decimal(1) - tax_rate)
                )
                expected_da = _money(
                    expected_revenue * da_to_revenue
                )
                expected_capex = _money(
                    expected_revenue * capex_to_revenue
                )
                expected_nwc = _money(
                    expected_revenue * nwc_to_revenue
                )
                expected_nwc_change = _money(
                    expected_nwc - previous_nwc
                )
                expected_ufcf = _money(
                    expected_after_tax
                    + expected_da
                    - expected_capex
                    - expected_nwc_change
                )
                expected_discount_factor = _rate(
                    Decimal(1)
                    / ((Decimal(1) + wacc) ** index)
                )
                expected_present_value = _money(
                    expected_ufcf * expected_discount_factor
                )
                expected_values = {
                    "revenue": expected_revenue,
                    "ebit": expected_ebit,
                    "tax_rate": tax_rate,
                    "after_tax_ebit": expected_after_tax,
                    "depreciation_amortization": expected_da,
                    "capex": expected_capex,
                    "nwc_balance": expected_nwc,
                    "nwc_change": expected_nwc_change,
                    "ufcf": expected_ufcf,
                    "discount_factor": expected_discount_factor,
                    "present_value": expected_present_value,
                }
                for field_name, expected_value in expected_values.items():
                    if getattr(projection, field_name) != expected_value:
                        raise ValueError(
                            f"projection {field_name} does not reconcile"
                        )
                previous_revenue = expected_revenue
                previous_nwc = expected_nwc
            if self.forecast_period_present_value != _money(
                sum(row.present_value for row in self.projections)
            ):
                raise ValueError("forecast present value does not reconcile")
            if self.enterprise_value != _money(
                self.forecast_period_present_value
                + self.terminal_value_present_value
            ):
                raise ValueError("enterprise value does not reconcile")
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
            normalized_revenue = (
                self.normalization.revenue.normalized_amount
            )
            base_revenue_invalid = False
            if normalized_revenue is not None:
                try:
                    rounded_revenue = _money(normalized_revenue)
                except DecimalException:
                    pass
                else:
                    base_revenue_invalid = (
                        normalized_revenue <= 0
                        or rounded_revenue <= 0
                    )
            invalid_reason = None
            if self.status == "invalid_model":
                invalid_reason = (
                    "base_revenue_nonpositive"
                    if base_revenue_invalid
                    else "arithmetic_invalid"
                )
            expected_missing = _expected_missing_inputs(
                assumption_values,
                actuals_by_metric,
                invalid_reason=invalid_reason,
            )
            has_enterprise_gap = any(
                assumption_values[key] is None
                for key in _ASSUMPTION_KEYS
            ) or any(
                len(actuals_by_metric.get(key, ())) != 1
                for key in _ENTERPRISE_ACTUALS
            )
            if (
                tuple(self.missing_inputs) != expected_missing
                or (
                    self.status == "partial_model"
                    and (
                        not has_enterprise_gap
                        or base_revenue_invalid
                    )
                )
                or (
                    self.status == "invalid_model"
                    and invalid_reason == "arithmetic_invalid"
                    and has_enterprise_gap
                )
            ):
                raise ValueError(
                    "partial or invalid result missing inputs and "
                    "status semantics "
                    "do not reconcile"
                )

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
_BRIDGE_ACTUALS = (
    "cash_and_equivalents",
    "interest_bearing_debt",
)
_LIMITATIONS = (
    "Operating NWC is limited to receivables + inventory - payables; taxes, provisions, and other operating balances are excluded.",
    "Capex is modeled as a positive cash outflow; negative source cash-flow signs are normalized with the source amounts preserved in actuals.",
    "The equity bridge includes only interest-bearing debt and cash; minority interest, associates, options, and other non-operating assets are excluded unless separately sourced.",
    "This is a reviewable model, not investment advice, a fairness opinion, an approved forecast, or an audit conclusion.",
)


def _expected_missing_inputs(
    assumption_values: dict[str, Decimal | None],
    actuals_by_metric: dict[str, list[DcfActualFact]],
    *,
    invalid_reason: str | None = None,
) -> tuple[str, ...]:
    missing = [
        key
        for key in _ASSUMPTION_KEYS
        if assumption_values[key] is None
    ]
    missing.extend(
        key
        for key in (*_ENTERPRISE_ACTUALS, *_BRIDGE_ACTUALS)
        if len(actuals_by_metric.get(key, ())) != 1
    )
    if invalid_reason is not None:
        missing.append(invalid_reason)
    return tuple(missing)


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
    assumption_values = {
        key: getattr(scenario, key)
        for key in _ASSUMPTION_FIELDS
    }
    actuals_by_metric = {
        key: [fact]
        for key, fact in by_metric.items()
    }
    missing = list(_expected_missing_inputs(
        assumption_values,
        actuals_by_metric,
    ))

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
        and (
            normalization.revenue.normalized_amount <= 0
            or _money(normalization.revenue.normalized_amount) <= 0
        )
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
    if any(
        key in missing
        for key in (*_ASSUMPTION_FIELDS, *_ENTERPRISE_ACTUALS)
    ):
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
        if revenue <= 0:
            raise _DcfConstructionError(
                "projection_revenue_nonpositive"
            )
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
        try:
            projection = DcfProjection(
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
            )
        except ValueError as exc:
            raise _DcfConstructionError(
                "projection_contract_invalid"
            ) from exc
        projections.append(projection)
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
        missing_inputs=tuple(missing),
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
    if not isinstance(scenario, DcfScenarioInput):
        raise TypeError("scenario must be DcfScenarioInput")
    immutable_actuals = tuple(actuals)
    if not all(isinstance(fact, DcfActualFact) for fact in immutable_actuals):
        raise TypeError("actuals must contain only DcfActualFact")
    scenario = DcfScenarioInput(**{
        field.name: getattr(scenario, field.name)
        for field in fields(DcfScenarioInput)
    })
    immutable_actuals = tuple(
        DcfActualFact(**{
            field.name: getattr(fact, field.name)
            for field in fields(DcfActualFact)
        })
        for fact in immutable_actuals
    )
    try:
        return _build_dcf_valuation_unchecked(
            scenario,
            immutable_actuals,
            source_missing=tuple(source_missing),
            source_limitations=tuple(source_limitations),
        )
    except (DecimalException, _DcfConstructionError) as exc:
        by_metric = _actual_map(scenario, immutable_actuals)
        assumptions = _assumptions(scenario)
        assumption_values = {
            assumption.key: assumption.value
            for assumption in assumptions
        }
        actuals_by_metric = {
            key: [fact]
            for key, fact in by_metric.items()
        }
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
            assumptions=assumptions,
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
            missing_inputs=_expected_missing_inputs(
                assumption_values,
                actuals_by_metric,
                invalid_reason="arithmetic_invalid",
            ),
            limitations=tuple(dict.fromkeys((
                *_LIMITATIONS,
                *source_limitations,
                "arithmetic_invalid:"
                + (
                    str(exc)
                    if isinstance(exc, _DcfConstructionError)
                    else type(exc).__name__
                ),
            ))),
        )


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        if not dcf_decimal_fits_serialization(value) or (
            value != 0
            and value.adjusted() < MIN_DECIMAL_ADJUSTED
        ):
            raise ValueError(
                "decimal exceeds fixed serialization bounds"
            )
        rendered = format(value, "f")
        if "." not in rendered:
            rendered = f"{rendered}.00"
        else:
            whole, fractional = rendered.split(".", 1)
            rendered = f"{whole}.{fractional.ljust(2, '0')}"
        if len(rendered) > _MAX_JSON_DECIMAL_CHARS:
            raise ValueError(
                "decimal exceeds fixed serialization bounds"
            )
        return rendered
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
