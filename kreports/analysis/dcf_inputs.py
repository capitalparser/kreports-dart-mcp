"""DCF input candidates from DART-derived historical facts."""
from __future__ import annotations

from statistics import median

from kreports.analysis.investor_quality import _financial_series, _safe_div
from kreports.semantic.metrics import DCF_SUPPORT_METRICS, METRIC_OUTPUT_ALIASES


def _growth(current: int | float | None, previous: int | float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return round((float(current) / float(previous)) - 1.0, 4)


def _median(values: list[float | None]) -> float | None:
    clean = [float(v) for v in values if v is not None]
    return round(median(clean), 4) if clean else None


def _sum_optional(*values: int | float | None) -> int | float | None:
    clean = [v for v in values if v is not None]
    if not clean:
        return None
    return sum(clean)


def dcf_input_candidates(
    company: str,
    *,
    start_year: int,
    end_year: int,
    fs_div: str = "CFS",
) -> dict:
    """Return evidence-backed DCF assumption candidates without valuing the company."""
    series = _financial_series(company, start_year, end_year, fs_div=fs_div)
    if not series:
        return {
            "company": company,
            "start_year": int(start_year),
            "end_year": int(end_year),
            "fs_div": fs_div,
            "historical_actuals": [],
            "candidate_assumptions": {},
            "missing_inputs": ["financial_facts_compact"],
            "data_quality": {"status": "missing", "source": "financial_facts_compact"},
            "limitations": ["No compact financial facts are available for the requested range."],
        }

    actuals: list[dict] = []
    revenue_growths: list[float | None] = []
    operating_margins: list[float | None] = []
    cash_conversions: list[float | None] = []
    tax_rates: list[float | None] = []
    capex_to_revenues: list[float | None] = []
    previous_revenue = None
    for row in series:
        revenue = row.get("revenue")
        op = row.get("operating_profit")
        ni = row.get("net_income")
        ocf = row.get("operating_cf")
        tax_expense = row.get("tax_expense")
        capex = _sum_optional(row.get("purchase_ppe"), row.get("purchase_intangible_assets"))
        revenue_growth = _growth(revenue, previous_revenue)
        operating_margin = _safe_div(op, revenue)
        cash_conversion = _safe_div(ocf, ni)
        tax_rate = _safe_div(tax_expense, (ni + tax_expense) if ni is not None and tax_expense is not None else None)
        capex_to_revenue = _safe_div(capex, revenue)
        actuals.append({
            "year": row["bsns_year"],
            "revenue": revenue,
            "operating_profit": op,
            "net_income": ni,
            "operating_cf": ocf,
            "tax_expense": tax_expense,
            "capex": capex,
            "revenue_growth": revenue_growth,
            "operating_margin": operating_margin,
            "cash_conversion": cash_conversion,
            "tax_rate": tax_rate,
            "capex_to_revenue": capex_to_revenue,
        })
        revenue_growths.append(revenue_growth)
        operating_margins.append(operating_margin)
        cash_conversions.append(cash_conversion)
        tax_rates.append(tax_rate)
        capex_to_revenues.append(capex_to_revenue)
        previous_revenue = revenue

    missing_inputs = []
    if all(row.get("operating_profit") is None for row in series):
        missing_inputs.append("operating_profit")
    if all(row.get("operating_cf") is None for row in series):
        missing_inputs.append("operating_cash_flow")
    if not any(v is not None for v in tax_rates):
        missing_inputs.append("tax_rate")
    if not any(v is not None for v in capex_to_revenues):
        missing_inputs.append("capex")
    missing_inputs.extend(["working_capital_delta", "wacc"])

    missing_core_metrics = [
        METRIC_OUTPUT_ALIASES.get(metric, metric)
        for metric in DCF_SUPPORT_METRICS[:4]
        if all(row.get(METRIC_OUTPUT_ALIASES.get(metric, metric)) is None for row in series)
    ]
    if missing_core_metrics:
        status = "incomplete_core_metrics"
        readiness = "partial"
    elif len(series) >= 5 and "capex" not in missing_inputs and "tax_rate" not in missing_inputs:
        status = "usable"
        readiness = "screen_grade"
    elif len(series) >= 3:
        status = "limited"
        readiness = "screen_grade"
    else:
        status = "limited"
        readiness = "partial"

    return {
        "company": company,
        "start_year": int(start_year),
        "end_year": int(end_year),
        "fs_div": fs_div,
        "historical_actuals": actuals,
        "candidate_assumptions": {
            "revenue_growth": {
                "basis": "historical_median",
                "value": _median(revenue_growths),
                "observations": [v for v in revenue_growths if v is not None],
            },
            "operating_margin": {
                "basis": "historical_median",
                "value": _median(operating_margins),
                "observations": [v for v in operating_margins if v is not None],
            },
            "cash_conversion": {
                "basis": "historical_median",
                "value": _median(cash_conversions),
                "observations": [v for v in cash_conversions if v is not None],
            },
            "tax_rate": {
                "basis": "historical_median",
                "value": _median(tax_rates),
                "observations": [v for v in tax_rates if v is not None],
            },
            "capex_to_revenue": {
                "basis": "historical_median",
                "value": _median(capex_to_revenues),
                "observations": [v for v in capex_to_revenues if v is not None],
            },
        },
        "missing_inputs": missing_inputs,
        "evidence_notes": [
            "Historical values come from annual compact financial facts.",
            "Assumption candidates are not valuation conclusions; they are starting points for analyst review.",
        ],
        "data_quality": {
            "status": status,
            "readiness": readiness,
            "source": "financial_facts_compact",
            "year_count": len(series),
            "missing_core_metrics": missing_core_metrics,
        },
        "limitations": [
            "DCF requires analyst-selected forecast period, terminal value method, discount rate, tax rate, capex, and working capital assumptions.",
            "The tool separates observed historical values from assumptions to avoid overstating precision.",
        ],
    }
