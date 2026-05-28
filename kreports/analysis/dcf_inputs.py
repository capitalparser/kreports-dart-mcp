"""DCF input candidates from DART-derived historical facts."""
from __future__ import annotations

from statistics import median

from kreports.analysis.investor_quality import _financial_series, _safe_div


def _growth(current: int | float | None, previous: int | float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return round((float(current) / float(previous)) - 1.0, 4)


def _median(values: list[float | None]) -> float | None:
    clean = [float(v) for v in values if v is not None]
    return round(median(clean), 4) if clean else None


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
    previous_revenue = None
    for row in series:
        revenue = row.get("revenue")
        op = row.get("operating_profit")
        ni = row.get("net_income")
        ocf = row.get("operating_cf")
        revenue_growth = _growth(revenue, previous_revenue)
        operating_margin = _safe_div(op, revenue)
        cash_conversion = _safe_div(ocf, ni)
        actuals.append({
            "year": row["bsns_year"],
            "revenue": revenue,
            "operating_profit": op,
            "net_income": ni,
            "operating_cf": ocf,
            "revenue_growth": revenue_growth,
            "operating_margin": operating_margin,
            "cash_conversion": cash_conversion,
        })
        revenue_growths.append(revenue_growth)
        operating_margins.append(operating_margin)
        cash_conversions.append(cash_conversion)
        previous_revenue = revenue

    missing_inputs = []
    if all(row.get("operating_profit") is None for row in series):
        missing_inputs.append("operating_profit")
    if all(row.get("operating_cf") is None for row in series):
        missing_inputs.append("operating_cash_flow")
    missing_inputs.extend(["tax_rate", "capex", "working_capital_delta", "wacc"])

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
        },
        "missing_inputs": missing_inputs,
        "evidence_notes": [
            "Historical values come from annual compact financial facts.",
            "Assumption candidates are not valuation conclusions; they are starting points for analyst review.",
        ],
        "data_quality": {
            "status": "usable" if len(series) >= 3 else "limited",
            "source": "financial_facts_compact",
            "year_count": len(series),
        },
        "limitations": [
            "DCF requires analyst-selected forecast period, terminal value method, discount rate, tax rate, capex, and working capital assumptions.",
            "The tool separates observed historical values from assumptions to avoid overstating precision.",
        ],
    }
