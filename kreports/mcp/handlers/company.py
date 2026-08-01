"""Company identity, profile, and financial snapshot handlers."""
from __future__ import annotations

from kreports.analysis.company_profile import (
    get_business_overview,
    search_company,
)
from kreports.analysis.semantic_index import build_company_context
from kreports.analysis.financial_analysis import (
    _annual_report_source,
    get_financial_snapshot,
)
from kreports.mcp.dispatch import resolve_company
from kreports.mcp.input_models import (
    GetBusinessOverviewInput,
    GetFinancialSnapshotInput,
    GetSemanticCompanyContextInput,
    SearchCompanyInput,
)


def handle_search_company(args: SearchCompanyInput) -> dict:
    results = search_company(args.query, limit=args.limit)
    return {"query": args.query, "count": len(results), "results": results}


def handle_get_financial_snapshot(args: GetFinancialSnapshotInput) -> dict:
    corp_code = resolve_company(args.company)
    result = get_financial_snapshot(
        corp_code,
        fs_div=args.fs_div,
        years=args.years,
        annual_only=True,
    )
    rows = result.get("rows") or []
    data_quality = result.get("data_quality") or {}
    source_table = data_quality.get("source")
    if source_table not in {"financials", "financial_facts_compact"}:
        source_table = "financials"
    latest_year = max(
        (
            int(row["연도"])
            for row in rows
            if row.get("연도") is not None
        ),
        default=None,
    )
    if rows:
        result["confirmed_facts"] = [{
            "statement": (
                f"{latest_year}년까지 {result.get('fs_div')} 기준 "
                f"재무 스냅샷 {len(rows)}개 연도를 조회했습니다."
            ),
            "source": _annual_report_source(
                corp_code,
                None,
                latest_year,
                section_title="재무제표",
                source_table=source_table,
            ),
            "excerpt": (
                f"years={len(rows)}, fs_div={result.get('fs_div')}"
            ),
        }]
    return result


def handle_get_business_overview(args: GetBusinessOverviewInput) -> dict:
    return get_business_overview(
        resolve_company(args.company),
        bsns_year=args.bsns_year,
    )


def handle_get_semantic_company_context(
    args: GetSemanticCompanyContextInput,
) -> dict:
    """Expose existing cached filing evidence without collection or writes."""
    return build_company_context(
        resolve_company(args.company),
        args.year,
        topics=args.topics,
    )
