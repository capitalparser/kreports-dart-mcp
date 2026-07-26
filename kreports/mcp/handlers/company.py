"""Company identity, profile, and financial snapshot handlers."""
from __future__ import annotations

from kreports.analysis.api import (
    get_business_overview,
    get_financial_snapshot,
    search_company,
)
from kreports.mcp.dispatch import resolve_company
from kreports.mcp.input_models import (
    GetBusinessOverviewInput,
    GetFinancialSnapshotInput,
    SearchCompanyInput,
)


def handle_search_company(args: SearchCompanyInput) -> dict:
    results = search_company(args.query, limit=args.limit)
    return {"query": args.query, "count": len(results), "results": results}


def handle_get_financial_snapshot(args: GetFinancialSnapshotInput) -> dict:
    return get_financial_snapshot(
        resolve_company(args.company),
        fs_div=args.fs_div,
        years=args.years,
        annual_only=True,
    )


def handle_get_business_overview(args: GetBusinessOverviewInput) -> dict:
    return get_business_overview(
        resolve_company(args.company),
        bsns_year=args.bsns_year,
    )
