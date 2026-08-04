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
    latest = max(rows, key=lambda row: int(row.get("연도") or 0), default={})
    latest_year = int(latest["연도"]) if latest.get("연도") is not None else None
    if rows:
        annual_sources: list[dict] = []
        for row in rows:
            if isinstance(row.get("source"), dict):
                annual_sources.append(dict(row["source"]))
            derived = row.get("derived_sources")
            if not isinstance(derived, dict):
                continue
            for source in derived.get("매출성장률") or []:
                if isinstance(source, dict):
                    annual_sources.append(dict(source))
        annual_sources = list({
            str(source.get("rcept_no") or source): source
            for source in annual_sources
        }.values())
        confirmed_facts = [{
            "statement": (
                f"{latest_year}년까지 {result.get('fs_div')} 기준 "
                f"재무 스냅샷 {len(rows)}개 연도를 조회했습니다."
            ),
            "source": latest.get("source") or _annual_report_source(
                corp_code, None, latest_year, section_title="재무제표",
                source_table=source_table,
            ),
            "sources": annual_sources,
            "excerpt": (
                f"years={len(rows)}, fs_div={result.get('fs_div')}"
            ),
        }]
        growth_sources = (
            (latest.get("derived_sources") or {}).get("매출성장률")
            if isinstance(latest.get("derived_sources"), dict)
            else None
        )
        if (
            latest.get("매출성장률") is not None
            and isinstance(growth_sources, list)
            and len(growth_sources) == 2
            and all(isinstance(source, dict) for source in growth_sources)
        ):
            input_years = [
                source.get("bsns_year") for source in growth_sources
            ]
            confirmed_facts.append({
                "statement": (
                    f"{latest_year}년 매출성장률 "
                    f"{latest.get('매출성장률')}%는 "
                    f"{input_years[0]}년과 {input_years[1]}년 "
                    "매출액으로 계산했습니다."
                ),
                "sources": [
                    dict(source) for source in growth_sources
                ],
                "excerpt": (
                    f"revenue_growth={latest.get('매출성장률')}, "
                    f"input_years={input_years[0]},{input_years[1]}"
                ),
            })
        result["confirmed_facts"] = confirmed_facts
    return result


def handle_get_business_overview(args: GetBusinessOverviewInput) -> dict:
    result = get_business_overview(
        resolve_company(args.company),
        bsns_year=args.bsns_year,
    )
    if not args.include_semantic_context or not isinstance(result, dict):
        return result
    year = result.get("bsns_year")
    if not isinstance(year, int):
        result["semantic_context"] = {
            "read_only": True,
            "availability": "unavailable",
            "limitations": ["business_overview_year_unavailable"],
        }
        return result
    result["semantic_context"] = build_company_context(
        resolve_company(args.company),
        year,
        topics=args.semantic_topics,
        note_topics=args.note_topics,
    )
    return result
