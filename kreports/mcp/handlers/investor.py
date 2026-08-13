"""Investor-quality and disclosure-event handlers."""
from __future__ import annotations

from kreports.analysis.financial_analysis import (
    build_dcf_model_pack,
    detect_restatement,
    get_dcf_input_candidates,
    get_investor_signals,
    get_quality_of_earnings_pack,
    score_going_concern,
    search_disclosure_events,
)
from kreports.mcp.dispatch import resolve_company
from kreports.mcp.input_models import (
    BuildDcfModelPackInput,
    DetectRestatementInput,
    GetDcfInputCandidatesInput,
    GetInvestorSignalsInput,
    GetQualityOfEarningsPackInput,
    ScoreGoingConcernInput,
    SearchDisclosureEventsInput,
)


def handle_score_going_concern(args: ScoreGoingConcernInput) -> dict:
    return score_going_concern(resolve_company(args.company))


def handle_detect_restatement(args: DetectRestatementInput) -> dict:
    return detect_restatement(
        resolve_company(args.company),
        threshold_pct=args.threshold_pct,
        top_n=args.top_n,
    )


def handle_get_investor_signals(args: GetInvestorSignalsInput) -> dict:
    return get_investor_signals(
        resolve_company(args.company),
        years=args.years,
        window_days=args.window_days,
        event_limit=args.event_limit,
    )


def handle_get_quality_of_earnings_pack(args: GetQualityOfEarningsPackInput) -> dict:
    return get_quality_of_earnings_pack(
        company=resolve_company(args.company),
        start_year=args.start_year,
        end_year=args.end_year,
        fs_div=args.fs_div,
    )


def handle_get_dcf_input_candidates(args: GetDcfInputCandidatesInput) -> dict:
    return get_dcf_input_candidates(
        company=resolve_company(args.company),
        start_year=args.start_year,
        end_year=args.end_year,
        fs_div=args.fs_div,
    )


def handle_build_dcf_model_pack(args: BuildDcfModelPackInput) -> dict:
    return build_dcf_model_pack(
        company=args.company,
        base_year=args.base_year,
        fs_div=args.fs_div,
        forecast_years=args.forecast_years,
        revenue_growth=args.revenue_growth,
        operating_margin=args.operating_margin,
        tax_rate=args.tax_rate,
        da_to_revenue=args.da_to_revenue,
        capex_to_revenue=args.capex_to_revenue,
        nwc_to_revenue=args.nwc_to_revenue,
        wacc=args.wacc,
        terminal_growth=args.terminal_growth,
        normalized_revenue=args.normalized_revenue,
        normalized_operating_profit=args.normalized_operating_profit,
        normalization_reason=args.normalization_reason,
    )


def handle_search_disclosure_events(args: SearchDisclosureEventsInput) -> dict:
    return search_disclosure_events(
        company=args.company,
        start_date=args.start_date,
        end_date=args.end_date,
        event_types=args.event_types,
        market=args.market,
        limit=args.limit,
    )
