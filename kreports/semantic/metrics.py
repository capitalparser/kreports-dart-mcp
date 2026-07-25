"""Canonical financial metric semantics used by compact and analysis paths."""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping


@dataclass(frozen=True)
class MetricDefinition:
    key: str
    label_ko: str
    unit: Literal["KRW", "ratio", "count", "hours", "text"]
    period_type: Literal["instant", "duration", "event"]
    preferred_fs_div: Literal["CFS", "OFS", "either"]
    source_tables: tuple[str, ...]
    source_account_ids: tuple[str, ...]
    aggregation: Literal["last", "sum", "average", "none"]
    null_meaning: Literal["missing", "not_applicable", "unknown"]
    source_unit: Literal["KRW", "million_KRW", "hours", "text"]
    source_multiplier: int
    source_account_groups: tuple[tuple[str, ...], ...]
    statement_division_preference: tuple[str, ...]


_FINANCIAL_FACT_SOURCES = ("financial_facts", "financial_facts_compact")
_BALANCE_SHEET_STATEMENTS = ("BS", "CIS", "IS", "CF", "SCE", "")
_INCOME_STATEMENTS = ("CIS", "IS", "CF", "BS", "SCE", "")
_CASH_FLOW_STATEMENTS = ("CF", "CIS", "IS", "BS", "SCE", "")


def _metric(
    key: str,
    label_ko: str,
    unit: Literal["KRW", "ratio", "count", "hours", "text"],
    period_type: Literal["instant", "duration", "event"],
    preferred_fs_div: Literal["CFS", "OFS", "either"],
    source_tables: tuple[str, ...],
    source_account_groups: tuple[tuple[str, ...], ...],
    aggregation: Literal["last", "sum", "average", "none"],
    null_meaning: Literal["missing", "not_applicable", "unknown"],
    source_unit: Literal["KRW", "million_KRW", "hours", "text"] = "KRW",
    source_multiplier: int = 1,
    statement_division_preference: tuple[str, ...] | None = None,
) -> MetricDefinition:
    """Build a definition without duplicating its ordered account IDs."""
    source_account_ids = tuple(
        dict.fromkeys(account_id for group in source_account_groups for account_id in group)
    )
    if statement_division_preference is None:
        statement_division_preference = (
            _BALANCE_SHEET_STATEMENTS if period_type == "instant" else _INCOME_STATEMENTS
        )
    return MetricDefinition(
        key=key,
        label_ko=label_ko,
        unit=unit,
        period_type=period_type,
        preferred_fs_div=preferred_fs_div,
        source_tables=source_tables,
        source_account_ids=source_account_ids,
        aggregation=aggregation,
        null_meaning=null_meaning,
        source_unit=source_unit,
        source_multiplier=source_multiplier,
        source_account_groups=source_account_groups,
        statement_division_preference=statement_division_preference,
    )


_METRICS = {
    "revenue": _metric(
        "revenue", "매출액", "KRW", "duration", "CFS", _FINANCIAL_FACT_SOURCES,
        (
            ("ifrs-full_Revenue",),
            ("ifrs-full_RevenueFromContractsWithCustomers",),
            ("dart_Revenue",),
            ("ifrs-full_RevenueFromRenderingOfServices",),
            ("ifrs-full_RevenueFromSaleOfGoods",),
            ("dart_TotalRevenue",),
        ), "last", "missing",
    ),
    "operating_profit": _metric(
        "operating_profit", "영업손익", "KRW", "duration", "CFS", _FINANCIAL_FACT_SOURCES,
        (
            ("ifrs-full_OperatingProfitLoss",),
            ("dart_OperatingIncomeLoss",),
            ("ifrs-full_ProfitLossFromOperatingActivities",),
        ), "last", "missing",
    ),
    "profit_loss": _metric(
        "profit_loss", "당기순손익", "KRW", "duration", "CFS", _FINANCIAL_FACT_SOURCES,
        (
            ("ifrs-full_ProfitLoss",),
            ("ifrs-full_ProfitLossAttributableToOwnersOfParent",),
        ), "last", "missing",
    ),
    "operating_cash_flow": _metric(
        "operating_cash_flow", "영업활동현금흐름", "KRW", "duration", "CFS",
        _FINANCIAL_FACT_SOURCES,
        (("ifrs-full_CashFlowsFromUsedInOperatingActivities",),), "last", "missing",
        statement_division_preference=_CASH_FLOW_STATEMENTS,
    ),
    "assets": _metric(
        "assets", "자산총계", "KRW", "instant", "CFS", _FINANCIAL_FACT_SOURCES,
        (("ifrs-full_Assets",),), "last", "missing",
    ),
    "liabilities": _metric(
        "liabilities", "부채총계", "KRW", "instant", "CFS", _FINANCIAL_FACT_SOURCES,
        (("ifrs-full_Liabilities",),), "last", "missing",
    ),
    "equity": _metric(
        "equity", "자본총계", "KRW", "instant", "CFS", _FINANCIAL_FACT_SOURCES,
        (("ifrs-full_Equity",), ("ifrs-full_EquityAttributableToOwnersOfParent",)),
        "last", "missing",
    ),
    "cash_and_equivalents": _metric(
        "cash_and_equivalents", "현금및현금성자산", "KRW", "instant", "CFS",
        _FINANCIAL_FACT_SOURCES, (("ifrs-full_CashAndCashEquivalents",),), "last", "missing",
    ),
    "interest_bearing_debt": _metric(
        "interest_bearing_debt", "이자부부채", "KRW", "instant", "CFS",
        _FINANCIAL_FACT_SOURCES,
        (
            (
                "ifrs-full_CurrentBorrowings",
                "ifrs-full_NoncurrentBorrowings",
                "ifrs-full_CurrentBondsIssued",
                "ifrs-full_NoncurrentBondsIssued",
            ),
            ("ifrs-full_Borrowings",),
            (
                "ifrs-full_CurrentPortionOfLongtermBorrowings",
                "ifrs-full_NoncurrentPortionOfLongtermBorrowings",
            ),
            ("dart_CurrentBorrowings", "dart_LongTermBorrowings"),
        ), "sum", "missing",
    ),
    "depreciation_amortization": _metric(
        "depreciation_amortization", "감가상각비및무형자산상각비", "KRW", "duration", "CFS",
        _FINANCIAL_FACT_SOURCES,
        (
            ("ifrs-full_DepreciationAndAmortisationExpense",),
            ("dart_DepreciationAndAmortization",),
            ("ifrs-full_DepreciationAmortisationAndImpairmentLoss",),
            ("ifrs-full_DepreciationDepletionAndAmortization",),
        ), "last", "missing",
    ),
    "purchase_ppe": _metric(
        "purchase_ppe", "유형자산 취득", "KRW", "duration", "CFS", _FINANCIAL_FACT_SOURCES,
        (("ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",),),
        "last", "missing", statement_division_preference=_CASH_FLOW_STATEMENTS,
    ),
    "purchase_intangible_assets": _metric(
        "purchase_intangible_assets", "무형자산 취득", "KRW", "duration", "CFS",
        _FINANCIAL_FACT_SOURCES,
        (("ifrs-full_PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities",),),
        "last", "missing", statement_division_preference=_CASH_FLOW_STATEMENTS,
    ),
    "trade_receivables": _metric(
        "trade_receivables", "매출채권", "KRW", "instant", "CFS", _FINANCIAL_FACT_SOURCES,
        (
            ("ifrs-full_TradeAndOtherCurrentReceivables",),
            ("ifrs-full_CurrentTradeReceivables",),
            ("dart_TradeAndOtherReceivables",),
            ("ifrs-full_TradeReceivables",),
        ), "last", "missing",
    ),
    "inventories": _metric(
        "inventories", "재고자산", "KRW", "instant", "CFS", _FINANCIAL_FACT_SOURCES,
        (("ifrs-full_Inventories",),), "last", "missing",
    ),
    "trade_payables": _metric(
        "trade_payables", "매입채무", "KRW", "instant", "CFS", _FINANCIAL_FACT_SOURCES,
        (("ifrs-full_TradePayables",),), "last", "missing",
    ),
    "tax_expense": _metric(
        "tax_expense", "법인세비용", "KRW", "duration", "CFS", _FINANCIAL_FACT_SOURCES,
        (("ifrs-full_IncomeTaxExpenseContinuingOperations",),), "last", "missing",
    ),
    "investing_cash_flow": _metric(
        "investing_cash_flow", "투자활동현금흐름", "KRW", "duration", "CFS",
        _FINANCIAL_FACT_SOURCES,
        (("ifrs-full_CashFlowsFromUsedInInvestingActivities",),), "last", "missing",
        statement_division_preference=_CASH_FLOW_STATEMENTS,
    ),
    "financing_cash_flow": _metric(
        "financing_cash_flow", "재무활동현금흐름", "KRW", "duration", "CFS",
        _FINANCIAL_FACT_SOURCES,
        (("ifrs-full_CashFlowsFromUsedInFinancingActivities",),), "last", "missing",
        statement_division_preference=_CASH_FLOW_STATEMENTS,
    ),
    "audit_fee": _metric(
        "audit_fee", "감사보수", "KRW", "event", "either", ("audit_fees",), (),
        "last", "missing", source_unit="million_KRW", source_multiplier=1_000_000,
    ),
    "audit_hours": _metric(
        "audit_hours", "감사시간", "hours", "event", "either", ("audit_fees",), (),
        "last", "missing", source_unit="hours",
    ),
}

METRICS: Mapping[str, MetricDefinition] = MappingProxyType(_METRICS)

# Beneish historically selected the DART revenue total before contract-specific
# alternatives. Keep that public-analysis contract in registry-owned metadata.
_CONSUMER_ACCOUNT_ORDERS: Mapping[tuple[str, str], tuple[str, ...]] = MappingProxyType({
    ("revenue", "beneish"): (
        "ifrs-full_Revenue",
        "dart_Revenue",
        "ifrs-full_RevenueFromContractsWithCustomers",
        "dart_TotalRevenue",
        "ifrs-full_RevenueFromRenderingOfServices",
        "ifrs-full_RevenueFromSaleOfGoods",
    ),
})

# QoE only queries the compact metrics it reads. DCF explicitly requests its
# broader support set below, so analyst inputs cannot disappear by coincidence.
CORE_FINANCIAL_METRICS = (
    "revenue",
    "operating_profit",
    "profit_loss",
    "operating_cash_flow",
    "assets",
    "liabilities",
    "equity",
)
DCF_REQUIRED_METRICS = (
    "revenue",
    "operating_profit",
    "profit_loss",
    "operating_cash_flow",
)
DCF_SUPPORT_METRICS = DCF_REQUIRED_METRICS + (
    "tax_expense",
    "purchase_ppe",
    "purchase_intangible_assets",
)

# Public QoE/DCF rows historically expose these two names. The compact table
# remains canonical so stored keys and existing public result keys do not move.
METRIC_OUTPUT_ALIASES: Mapping[str, str] = MappingProxyType({
    "profit_loss": "net_income",
    "operating_cash_flow": "operating_cf",
})

# financials is a legacy summary fallback, not a second semantic account map.
FINANCIAL_SUMMARY_FIELD_METRICS: Mapping[str, str] = MappingProxyType({
    "revenue": "revenue",
    "operating_profit": "operating_profit",
    "net_income": "profit_loss",
    "total_assets": "assets",
    "total_debt": "liabilities",
    "total_equity": "equity",
    "operating_cf": "operating_cash_flow",
})


def metric_definition(metric_key: str) -> MetricDefinition:
    """Return one registered metric or fail closed for an unknown key."""
    return METRICS[metric_key]


def metric_source_account_ids(metric_key: str, *, consumer: str | None = None) -> tuple[str, ...]:
    """Return a registry-owned account order, including stable consumer contracts."""
    definition = metric_definition(metric_key)
    if consumer is None:
        return definition.source_account_ids
    return _CONSUMER_ACCOUNT_ORDERS.get((metric_key, consumer), definition.source_account_ids)


def compact_metric_definitions() -> tuple[MetricDefinition, ...]:
    """Return metrics whose XBRL account IDs are eligible for compact rebuilds."""
    return tuple(
        definition
        for definition in METRICS.values()
        if "financial_facts" in definition.source_tables and definition.source_account_ids
    )


def financial_summary_account_map() -> dict[str, str]:
    """Derive legacy `financials` summary fields from registered XBRL accounts."""
    summary_field_by_metric = {
        metric_key: source_field
        for source_field, metric_key in FINANCIAL_SUMMARY_FIELD_METRICS.items()
    }
    return {
        account_id: summary_field_by_metric[definition.key]
        for definition in compact_metric_definitions()
        if definition.key in summary_field_by_metric
        for account_id in definition.source_account_ids
    }


def metric_output_key(metric_key: str) -> str:
    """Return the established analysis-row name for a canonical compact key."""
    metric_definition(metric_key)
    return METRIC_OUTPUT_ALIASES.get(metric_key, metric_key)


def canonical_amount(metric_key: str, amount: int | float | None) -> int | float | None:
    """Convert a source amount to the metric's canonical unit without rounding."""
    if amount is None:
        return None
    return amount * metric_definition(metric_key).source_multiplier
