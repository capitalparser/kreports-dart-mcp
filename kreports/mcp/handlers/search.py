"""Dataset, peer-selection, industry, and on-demand retrieval handlers."""
from __future__ import annotations

from kreports.analysis.financial_analysis import _annual_report_source
from kreports.analysis.note_search import (
    search_note_disclosing_companies,
)
from kreports.analysis.note_source_projection import (
    project_note_search_sources,
)
from kreports.analysis.peer_benchmarks import (
    compare_to_industry,
    get_industry_audit_landscape,
)
from kreports.analysis.peer_quality import (
    compare_custom_peer_financials,
    resolve_statistical_peer_population,
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


def handle_compare_to_industry(
    args: CompareToIndustryInput,
) -> dict:
    return compare_to_industry(
        company=(
            resolve_company(args.company)
            if args.company
            else None
        ),
        induty_code=args.induty_code,
        metric=args.metric,
        year=args.year,
        fs_div=args.fs_div,
        prefix_len=args.prefix_len,
        include_peers=args.include_peers,
        peer_limit=args.peer_limit,
    )


def handle_compare_to_industry_multi(
    args: CompareToIndustryMultiInput,
) -> dict:
    return compare_custom_peer_financials(
        company=resolve_company(args.company),
        year=getattr(args, "year", None),
        metrics=args.metrics,
        years_back=args.years_back,
        peer_criteria=getattr(args, "peer_criteria", None),
        peer_limit=getattr(args, "peer_limit", 50),
        fs_strategy=args.fs_strategy,
        prefix_len_start=args.prefix_len_start,
        size_bucket_decade=args.size_bucket_decade,
        exclude_other_sectors=args.exclude_other_sectors,
    )


def handle_select_peer_group(
    args: SelectPeerGroupInput,
) -> dict:
    population = resolve_statistical_peer_population(
        company=resolve_company(args.company),
        year=getattr(args, "year", None),
        peer_criteria=args.peer_criteria or args.criteria,
        peer_limit=args.peer_limit,
        fs_strategy=args.fs_strategy,
        prefix_len_start=args.prefix_len_start,
        size_bucket_decade=args.size_bucket_decade,
        exclude_other_sectors=args.exclude_other_sectors,
    )
    if "error" in population:
        return population

    result = population["peer_group"]
    result["cohort_snapshot"] = population[
        "cohort_snapshot"
    ]
    result["statistical_member_count"] = population[
        "statistical_member_count"
    ]
    result["returned_peer_count"] = population[
        "returned_member_count"
    ]
    result["presentation_truncated"] = (
        population["returned_member_count"]
        < population["statistical_member_count"]
    )

    limitations: list[str] = []
    if population["statistical_universe_truncated"]:
        limitations.append(
            "statistical_universe_exceeded_internal_safety_bound"
        )
    if population["statistical_member_count"] < 5:
        limitations.append(
            "statistical_peer_count_below_5"
        )
    if result["presentation_truncated"]:
        limitations.append(
            "chatbot_peer_table_is_truncated"
        )
    status = (
        "missing"
        if population["statistical_member_count"] == 0
        else "limited"
        if limitations[:2]
        else "usable"
    )
    result["data_quality"] = {
        "status": status,
        "dataset_version": population[
            "cohort_snapshot"
        ]["dataset_version"],
        "schema_version": population[
            "cohort_snapshot"
        ]["schema_version"],
        "limitations": limitations,
        "statistical_member_count": population[
            "statistical_member_count"
        ],
        "returned_member_count": population[
            "returned_member_count"
        ],
    }
    result["next_checks"] = [
        "후속 peer 분석에 cohort_id를 함께 기록해 동일 모집단 재사용 여부를 확인하세요.",
        "사용자 강제 포함 기업은 경제적 유사성을 의미하지 않으므로 포함 사유를 별도 검토하세요.",
    ]

    subject = result.get("subject") or {}
    policy = result.get("selection_policy") or {}
    corp_code = subject.get("corp_code")
    if corp_code:
        resolved_year = policy.get("resolved_year")
        result["confirmed_facts"] = [{
            "statement": (
                "선정 정책에 따라 통계 대상 비교기업 "
                f"{population['statistical_member_count']}개를 구성했고, "
                f"챗봇 표에는 {population['returned_member_count']}개를 표시합니다."
            ),
            "source": _annual_report_source(
                str(corp_code),
                subject,
                (
                    int(resolved_year)
                    if resolved_year
                    else None
                ),
                section_title="재무제표",
                source_table="peer_cohort",
            ),
            "excerpt": (
                f"cohort_id={population['cohort_snapshot']['cohort_id']}, "
                f"requested_year={policy.get('requested_year')}, "
                f"resolved_year={resolved_year}, "
                f"fs_div={policy.get('fs_div_used')}"
            ),
        }]
    return result


def handle_search_dataset(
    args: SearchDatasetInput,
) -> dict:
    if (
        args.dataset == "accounting_note_chapters"
        and args.keyword
        and args.company is None
        and args.source_type in {None, "business_report"}
    ):
        result = search_note_disclosing_companies(
            args.keyword,
            year=args.year,
            market=args.market,
            induty_prefix=args.induty_prefix,
            fs_div=args.fs_div,
            section_type=args.section_type,
            limit=args.limit,
            offset=getattr(args, "offset", 0),
            include_excerpt=args.include_excerpt,
            search_mode=getattr(args, "search_mode", "exact"),
            synonyms=getattr(args, "synonyms", None),
        )
        return project_note_search_sources(result)
    payload = args.model_dump()
    for extension_field in (
        "offset",
        "search_mode",
        "synonyms",
    ):
        payload.pop(extension_field, None)
    return search_dataset(**payload)


def handle_fetch_disclosure_on_demand(
    args: FetchDisclosureOnDemandInput,
) -> dict:
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
        company=(
            resolve_company(args.company)
            if args.company
            else None
        ),
        induty_code=args.induty_code,
        years_back=args.years_back,
        fs_div=args.fs_div,
        prefix_len_start=args.prefix_len_start,
        top_n=args.top_n,
        exclude_other_sectors=args.exclude_other_sectors,
    )
