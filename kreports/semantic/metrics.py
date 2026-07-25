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


_FINANCIAL_FACT_SOURCES = ("financial_facts", "financial_facts_compact")

_METRICS = {
    "revenue": MetricDefinition("revenue", "매출액", "KRW", "duration", "CFS", _FINANCIAL_FACT_SOURCES, ("ifrs-full_Revenue",), "last", "missing"),
    "operating_profit": MetricDefinition("operating_profit", "영업손익", "KRW", "duration", "CFS", _FINANCIAL_FACT_SOURCES, ("ifrs-full_OperatingProfitLoss", "dart_OperatingIncomeLoss"), "last", "missing"),
    "profit_loss": MetricDefinition("profit_loss", "당기순손익", "KRW", "duration", "CFS", _FINANCIAL_FACT_SOURCES, ("ifrs-full_ProfitLoss",), "last", "missing"),
    "operating_cash_flow": MetricDefinition("operating_cash_flow", "영업활동현금흐름", "KRW", "duration", "CFS", _FINANCIAL_FACT_SOURCES, ("ifrs-full_CashFlowsFromUsedInOperatingActivities",), "last", "missing"),
    "assets": MetricDefinition("assets", "자산총계", "KRW", "instant", "CFS", _FINANCIAL_FACT_SOURCES, ("ifrs-full_Assets",), "last", "missing"),
    "liabilities": MetricDefinition("liabilities", "부채총계", "KRW", "instant", "CFS", _FINANCIAL_FACT_SOURCES, ("ifrs-full_Liabilities",), "last", "missing"),
    "equity": MetricDefinition("equity", "자본총계", "KRW", "instant", "CFS", _FINANCIAL_FACT_SOURCES, ("ifrs-full_Equity",), "last", "missing"),
    "cash_and_equivalents": MetricDefinition("cash_and_equivalents", "현금및현금성자산", "KRW", "instant", "CFS", _FINANCIAL_FACT_SOURCES, ("ifrs-full_CashAndCashEquivalents",), "last", "missing"),
    "interest_bearing_debt": MetricDefinition("interest_bearing_debt", "이자부부채", "KRW", "instant", "CFS", _FINANCIAL_FACT_SOURCES, ("ifrs-full_Borrowings",), "last", "missing"),
    "depreciation_amortization": MetricDefinition("depreciation_amortization", "감가상각비및무형자산상각비", "KRW", "duration", "CFS", _FINANCIAL_FACT_SOURCES, ("ifrs-full_DepreciationDepletionAndAmortization",), "last", "missing"),
    "purchase_ppe": MetricDefinition("purchase_ppe", "유형자산 취득", "KRW", "duration", "CFS", _FINANCIAL_FACT_SOURCES, ("ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",), "last", "missing"),
    "purchase_intangible_assets": MetricDefinition("purchase_intangible_assets", "무형자산 취득", "KRW", "duration", "CFS", _FINANCIAL_FACT_SOURCES, ("ifrs-full_PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities",), "last", "missing"),
    "trade_receivables": MetricDefinition("trade_receivables", "매출채권", "KRW", "instant", "CFS", _FINANCIAL_FACT_SOURCES, ("ifrs-full_TradeReceivables",), "last", "missing"),
    "inventories": MetricDefinition("inventories", "재고자산", "KRW", "instant", "CFS", _FINANCIAL_FACT_SOURCES, ("ifrs-full_Inventories",), "last", "missing"),
    "trade_payables": MetricDefinition("trade_payables", "매입채무", "KRW", "instant", "CFS", _FINANCIAL_FACT_SOURCES, ("ifrs-full_TradePayables",), "last", "missing"),
    "tax_expense": MetricDefinition("tax_expense", "법인세비용", "KRW", "duration", "CFS", _FINANCIAL_FACT_SOURCES, ("ifrs-full_IncomeTaxExpenseContinuingOperations",), "last", "missing"),
    "investing_cash_flow": MetricDefinition("investing_cash_flow", "투자활동현금흐름", "KRW", "duration", "CFS", _FINANCIAL_FACT_SOURCES, ("ifrs-full_CashFlowsFromUsedInInvestingActivities",), "last", "missing"),
    "financing_cash_flow": MetricDefinition("financing_cash_flow", "재무활동현금흐름", "KRW", "duration", "CFS", _FINANCIAL_FACT_SOURCES, ("ifrs-full_CashFlowsFromUsedInFinancingActivities",), "last", "missing"),
    "audit_fee": MetricDefinition("audit_fee", "감사보수", "KRW", "event", "either", ("audit_fees",), (), "last", "missing"),
    "audit_hours": MetricDefinition("audit_hours", "감사시간", "hours", "event", "either", ("audit_fees",), (), "last", "missing"),
}

METRICS: Mapping[str, MetricDefinition] = MappingProxyType(_METRICS)

# These compact-source keys are the QoE series contract.  Keep canonical
# storage keys here and adapt legacy output names through METRIC_OUTPUT_ALIASES.
CORE_FINANCIAL_METRICS = (
    "revenue",
    "operating_profit",
    "profit_loss",
    "operating_cash_flow",
    "tax_expense",
    "purchase_ppe",
    "purchase_intangible_assets",
    "assets",
    "liabilities",
    "equity",
)
DCF_SUPPORT_METRICS = (
    "revenue",
    "operating_profit",
    "profit_loss",
    "operating_cash_flow",
    "tax_expense",
    "purchase_ppe",
    "purchase_intangible_assets",
)

# Public QoE/DCF rows historically expose these two names.  The compact table
# remains canonical so that stored keys do not change.
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


def compact_metric_definitions() -> tuple[MetricDefinition, ...]:
    """Return metrics whose XBRL account IDs are eligible for compact rebuilds."""
    return tuple(
        definition
        for definition in METRICS.values()
        if "financial_facts" in definition.source_tables and definition.source_account_ids
    )


def metric_output_key(metric_key: str) -> str:
    """Return the established analysis-row name for a canonical compact key."""
    metric_definition(metric_key)
    return METRIC_OUTPUT_ALIASES.get(metric_key, metric_key)
