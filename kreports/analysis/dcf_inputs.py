"""DCF input candidates from DART-derived historical facts."""
from __future__ import annotations

import math
from statistics import median

from kreports.analysis.investor_quality import _financial_series, _safe_div
from kreports.semantic.metrics import DCF_REQUIRED_METRICS, DCF_SUPPORT_METRICS, METRIC_OUTPUT_ALIASES


def _growth(current: int | float | None, previous: int | float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return round((float(current) / float(previous)) - 1.0, 4)


def _median(values: list[float | None]) -> float | None:
    clean = [
        float(v)
        for v in values
        if v is not None and math.isfinite(float(v))
    ]
    return round(median(clean), 4) if clean else None


def _valuation_blocker(
    field: str,
    *,
    kind: str,
    impact: str,
    owner: str,
    next_action: str,
) -> dict[str, str]:
    """Describe one missing DCF prerequisite without changing history coverage."""
    return {
        "field": field,
        "kind": kind,
        "impact": impact,
        "owner": owner,
        "next_action": next_action,
    }


def _sum_optional(*values: int | float | None) -> int | float | None:
    clean = [v for v in values if v is not None]
    if not clean:
        return None
    return sum(clean)


def _source_blocker(
    field: str,
    *,
    fs_div: str,
    impact: str,
) -> dict[str, str]:
    return _valuation_blocker(
        field,
        kind="source_fact_missing",
        impact=impact,
        owner="filing_data",
        next_action=(
            f"요청 기간 {fs_div} 재무제표에서 {field} 실제값과 "
            "사업연도별 출처를 확인하세요."
        ),
    )


def dcf_input_candidates(
    company: str,
    *,
    start_year: int,
    end_year: int,
    fs_div: str = "CFS",
) -> dict:
    """Return evidence-backed DCF assumption candidates without valuing the company."""
    series = _financial_series(
        company, start_year, end_year, fs_div=fs_div, metric_keys=DCF_SUPPORT_METRICS,
    )
    if not series:
        source_blockers = [
            _source_blocker(
                field,
                fs_div=fs_div,
                impact="과거 실적 기반 DCF 입력 후보 산정 불가",
            )
            for field in (
                *DCF_REQUIRED_METRICS,
                "tax_rate",
                "capex",
                "working_capital_delta",
            )
        ]
        return {
            "company": company,
            "start_year": int(start_year),
            "end_year": int(end_year),
            "fs_div": fs_div,
            "historical_actuals": [],
            "candidate_assumptions": {},
            "candidate_status": "missing",
            "valuation_readiness": "blocked",
            "valuation_blockers": [
                *source_blockers,
                _valuation_blocker(
                    "wacc",
                    kind="analyst_input_missing",
                    impact="기업가치 할인 계산 불가",
                    owner="analyst",
                    next_action="자본구조·무위험수익률·베타·시장위험프리미엄으로 WACC를 산정하세요.",
                ),
                _valuation_blocker(
                    "terminal_growth",
                    kind="analyst_input_missing",
                    impact="터미널가치 계산 불가",
                    owner="analyst",
                    next_action="장기 거시성장률과 사업 지속가능성을 근거로 영구성장률을 정하세요.",
                ),
            ],
            "missing_inputs": ["financial_facts_compact"],
            "data_quality": {"status": "missing", "source": "financial_facts_compact"},
            "limitations": ["No compact financial facts are available for the requested range."],
        }

    actuals: list[dict] = []
    revenue_growths: list[float | None] = []
    operating_margins: list[float | None] = []
    cash_conversions: list[float | None] = []
    tax_rates: list[float | None] = []
    tax_observations: list[dict] = []
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
        raw_tax_rate = _safe_div(
            tax_expense,
            (ni + tax_expense)
            if ni is not None and tax_expense is not None
            else None,
        )
        tax_nonfinite = (
            raw_tax_rate is not None
            and not math.isfinite(float(raw_tax_rate))
        )
        tax_rate = None if tax_nonfinite else raw_tax_rate
        capex_to_revenue = _safe_div(capex, revenue)
        actuals.append({
            "year": row["bsns_year"],
            "revenue": revenue,
            "operating_profit": op,
            "net_income": ni,
            "operating_cf": ocf,
            "tax_expense": (
                tax_expense
                if tax_expense is None
                or math.isfinite(float(tax_expense))
                else None
            ),
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
        tax_outlier = tax_nonfinite or (
            tax_rate is not None and (tax_rate < 0 or tax_rate > 1)
        )
        tax_observation = {
            "year": row["bsns_year"],
            "value": tax_rate,
            "outlier": tax_outlier,
        }
        if tax_nonfinite:
            tax_observation.update({
                "exclusion_reason": "nonfinite",
                "raw_value_marker": "nonfinite",
            })
        elif tax_outlier:
            tax_observation["exclusion_reason"] = "outside_range"
        tax_observations.append(tax_observation)
        if tax_rate is not None and not tax_outlier:
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
    missing_inputs.extend(["working_capital_delta", "wacc", "terminal_growth"])

    missing_core_metrics = [
        metric
        for metric in DCF_REQUIRED_METRICS
        if all(row.get(METRIC_OUTPUT_ALIASES.get(metric, metric)) is None for row in series)
    ]
    if missing_core_metrics:
        candidate_status = "limited"
    elif len(series) >= 5 and "capex" not in missing_inputs and "tax_rate" not in missing_inputs:
        candidate_status = "usable"
    elif len(series) >= 3:
        candidate_status = "limited"
    else:
        candidate_status = "limited"

    valuation_blockers: list[dict[str, str]] = []
    for field in missing_core_metrics:
        valuation_blockers.append(_source_blocker(
            field,
            fs_div=fs_div,
            impact="과거 실적 기반 DCF 입력 후보 산정 불가",
        ))
    if "working_capital_delta" in missing_inputs:
        valuation_blockers.append(_valuation_blocker(
            "working_capital_delta",
            kind="source_fact_missing",
            impact="운전자본 증감에 따른 UFCF 계산 불가",
            owner="filing_data",
            next_action="기준연도 CFS 운전자본 관련 계정의 전년 대비 증감을 확인하세요.",
        ))
    for field, impact, action in (
        (
            "tax_rate",
            "세후 EBIT 계산 불가",
            "요청 기간 CFS 법인세비용과 세전손익 실제값을 확인하세요.",
        ),
        (
            "capex",
            "CAPEX 현금유출 계산 불가",
            "요청 기간 CFS 유형·무형자산 취득 실제값을 확인하세요.",
        ),
    ):
        if field in missing_inputs:
            valuation_blockers.append(_valuation_blocker(
                field,
                kind="source_fact_missing",
                impact=impact,
                owner="filing_data",
                next_action=action,
            ))
    if "wacc" in missing_inputs:
        valuation_blockers.append(_valuation_blocker(
            "wacc",
            kind="analyst_input_missing",
            impact="기업가치 할인 계산 불가",
            owner="analyst",
            next_action="자본구조·무위험수익률·베타·시장위험프리미엄으로 WACC를 산정하세요.",
        ))
    if "terminal_growth" in missing_inputs:
        valuation_blockers.append(_valuation_blocker(
            "terminal_growth",
            kind="analyst_input_missing",
            impact="터미널가치 계산 불가",
            owner="analyst",
            next_action="장기 거시성장률과 사업 지속가능성을 근거로 영구성장률을 정하세요.",
        ))
    valuation_readiness = "ready" if not valuation_blockers else "blocked"

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
                "observations": tax_observations,
                "included_observation_count": len(tax_rates),
                "excluded_observation_count": sum(
                    1 for observation in tax_observations
                    if observation["outlier"]
                ),
                "outlier_policy": "negative_or_greater_than_one_excluded_from_median",
            },
            "capex_to_revenue": {
                "basis": "historical_median",
                "value": _median(capex_to_revenues),
                "observations": [v for v in capex_to_revenues if v is not None],
            },
        },
        "candidate_status": candidate_status,
        "valuation_readiness": valuation_readiness,
        "valuation_blockers": valuation_blockers,
        "missing_inputs": missing_inputs,
        "evidence_notes": [
            "Historical values come from annual compact financial facts.",
            "Assumption candidates are not valuation conclusions; they are starting points for analyst review.",
        ],
        "data_quality": {
            "status": candidate_status,
            "candidate_status": candidate_status,
            "valuation_readiness": valuation_readiness,
            "source": "financial_facts_compact",
            "year_count": len(series),
            "missing_core_metrics": missing_core_metrics,
        },
        "limitations": [
            "DCF requires analyst-selected forecast period, terminal value method, discount rate, tax rate, capex, and working capital assumptions.",
            "The tool separates observed historical values from assumptions to avoid overstating precision.",
        ],
    }
