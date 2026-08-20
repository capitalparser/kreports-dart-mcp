"""Dataset, peer-selection, industry, and on-demand retrieval handlers."""
from __future__ import annotations

from kreports.analysis.peer_benchmarks import (
    compare_to_industry,
    compare_to_industry_multi,
    get_industry_audit_landscape,
    select_peer_group,
)
from kreports.analysis.financial_analysis import _annual_report_source
from kreports.analysis.peer_workflows import (
    compare_custom_peer_financials,
    search_note_disclosing_companies,
)
from kreports.analysis.search_adapter import search_dataset
from kreports.collector.on_demand import fetch_disclosure_on_demand
from kreports.mcp.dispatch import resolve_company
from kreports.mcp.input_models import (
    CompareToIndustryInput,
    CompareToIndustryMultiInput,
    FetchDisclosureOnDemandInput,
    GetIndustryAuditLandscapeInput,
    SearchDatasetInput,
    SelectPeerGroupInput,
)


def handle_compare_to_industry(args: CompareToIndustryInput) -> dict:
    return compare_to_industry(
        company=resolve_company(args.company) if args.company else None,
        induty_code=args.induty_code,
        metric=args.metric,
        year=args.year,
        fs_div=args.fs_div,
        prefix_len=args.prefix_len,
        include_peers=args.include_peers,
        peer_limit=args.peer_limit,
    )


def handle_compare_to_industry_multi(args: CompareToIndustryMultiInput) -> dict:
    peer_criteria = getattr(args, "peer_criteria", None)
    requested_year = getattr(args, "year", None)
    if peer_criteria is not None or requested_year is not None:
        return compare_custom_peer_financials(
            company=resolve_company(args.company),
            year=requested_year,
            metrics=args.metrics,
            years_back=args.years_back,
            peer_criteria=peer_criteria,
            fs_strategy=args.fs_strategy,
            prefix_len_start=args.prefix_len_start,
            size_bucket_decade=args.size_bucket_decade,
            exclude_other_sectors=args.exclude_other_sectors,
        )
    return compare_to_industry_multi(
        company=resolve_company(args.company),
        metrics=args.metrics,
        years_back=args.years_back,
        fs_div=args.fs_div,
        fs_strategy=args.fs_strategy,
        prefix_len_start=args.prefix_len_start,
        exclude_other_sectors=args.exclude_other_sectors,
        size_bucket_decade=args.size_bucket_decade,
    )


def handle_select_peer_group(args: SelectPeerGroupInput) -> dict:
    result = select_peer_group(
        company=resolve_company(args.company),
        criteria=args.peer_criteria or args.criteria,
        peer_limit=args.peer_limit,
        fs_strategy=args.fs_strategy,
        prefix_len_start=args.prefix_len_start,
        size_bucket_decade=args.size_bucket_decade,
        exclude_other_sectors=args.exclude_other_sectors,
        year=getattr(args, "year", None),
    )
    subject = result.get("subject") or {}
    policy = result.get("selection_policy") or {}
    corp_code = subject.get("corp_code")
    if corp_code:
        resolved_year = policy.get("resolved_year")
        result["confirmed_facts"] = [{
            "statement": (
                f"선정 정책에 따라 비교기업 "
                f"{result.get('returned_peer_count', len(result.get('peers') or []))}"
                "개를 구성했습니다."
            ),
            "source": _annual_report_source(
                str(corp_code),
                subject,
                int(resolved_year) if resolved_year else None,
                section_title="재무제표",
                source_table="peer_cohort",
            ),
            "excerpt": (
                f"requested_year={policy.get('requested_year')}, "
                f"resolved_year={resolved_year}, "
                f"fs_div={policy.get('fs_div_used')}"
            ),
        }]
    return result


def handle_search_dataset(args: SearchDatasetInput) -> dict:
    if (
        args.dataset == "accounting_note_chapters"
        and args.keyword
        and args.company is None
        and args.source_type in {None, "business_report"}
    ):
        return search_note_disclosing_companies(
            args.keyword,
            year=args.year,
            market=args.market,
            induty_prefix=args.induty_prefix,
            fs_div=args.fs_div,
            section_type=args.section_type,
            limit=args.limit,
            include_excerpt=args.include_excerpt,
        )
    return search_dataset(**args.model_dump())


def handle_fetch_disclosure_on_demand(args: FetchDisclosureOnDemandInput) -> dict:
    # Secret is unwrapped only at this ephemeral external-fetch boundary.
    user_key = (
        args.user_dart_api_key.get_secret_value()
        if args.user_dart_api_key is not None
        else None
    )
    return fetch_disclosure_on_demand(
        rcept_no=args.rcept_no,
        user_dart_api_key=user_key,
        cache_policy=args.cache_policy,
        corp_code=args.corp_code,
        year=args.year,
    )


def handle_get_industry_audit_landscape(
    args: GetIndustryAuditLandscapeInput,
) -> dict:
    return get_industry_audit_landscape(
        company=resolve_company(args.company) if args.company else None,
        induty_code=args.induty_code,
        years_back=args.years_back,
        fs_div=args.fs_div,
        prefix_len_start=args.prefix_len_start,
        top_n=args.top_n,
        exclude_other_sectors=args.exclude_other_sectors,
    )
