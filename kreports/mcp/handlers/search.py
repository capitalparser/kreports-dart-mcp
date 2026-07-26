"""Dataset, peer-selection, industry, and on-demand retrieval handlers."""
from __future__ import annotations

from kreports.analysis.api import (
    compare_to_industry,
    compare_to_industry_multi,
    get_industry_audit_landscape,
    search_dataset,
    select_peer_group,
)
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
    return select_peer_group(
        company=resolve_company(args.company),
        criteria=args.criteria,
        peer_limit=args.peer_limit,
        fs_strategy=args.fs_strategy,
        prefix_len_start=args.prefix_len_start,
        size_bucket_decade=args.size_bucket_decade,
        exclude_other_sectors=args.exclude_other_sectors,
    )


def handle_search_dataset(args: SearchDatasetInput) -> dict:
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
