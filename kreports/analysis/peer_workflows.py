"""High-level read-only workflows for customizable peer and note analysis."""
from __future__ import annotations

from typing import Any

from kreports.analysis.note_comparison import compare_peer_accounting_notes
from kreports.analysis.peer_benchmarks import (
    _METRIC_SQL,
    _METRIC_UNIT,
    _fetch_metric_values,
    _quantile,
    compare_peer_accounting_policies,
    compare_peer_audit_fees,
    compare_peer_audit_procedures,
    compare_peer_audit_report_matters,
    compare_peer_kam_topics,
    compare_peer_risk_profile,
    select_peer_group,
)
from kreports.analysis.peer_criteria import PeerCriteriaProfile
from kreports.analysis.search_adapter import search_dataset
import kreports.db.engine as _engine_module


_DEFAULT_FINANCIAL_METRICS = [
    "영업이익률",
    "순이익률",
    "부채비율",
    "ROE",
    "ROA",
    "자기자본비율",
    "매출성장률",
    "Beneish_M",
]


def build_custom_peer_group(
    company: str,
    *,
    year: int | None = None,
    peer_criteria: PeerCriteriaProfile | dict | list[str] | None = None,
    peer_limit: int = 30,
    fs_strategy: str = "auto",
    prefix_len_start: int = 3,
    size_bucket_decade: float | None = None,
    exclude_other_sectors: bool = True,
) -> dict[str, Any]:
    """Resolve one reproducible peer cohort with explicit selection evidence."""
    return select_peer_group(
        company=company,
        criteria=peer_criteria,
        peer_limit=peer_limit,
        fs_strategy=fs_strategy,
        prefix_len_start=prefix_len_start,
        size_bucket_decade=size_bucket_decade,
        exclude_other_sectors=exclude_other_sectors,
        year=year,
    )


def compare_custom_peer_financials(
    company: str,
    *,
    year: int | None = None,
    metrics: list[str] | None = None,
    years_back: int = 5,
    peer_criteria: PeerCriteriaProfile | dict | list[str] | None = None,
    peer_limit: int = 50,
    fs_strategy: str = "auto",
    prefix_len_start: int = 3,
    size_bucket_decade: float | None = None,
    exclude_other_sectors: bool = True,
) -> dict[str, Any]:
    """Compare financial metrics using one user-controlled, reproducible cohort."""
    selected_metrics = list(metrics or _DEFAULT_FINANCIAL_METRICS)
    invalid = [metric for metric in selected_metrics if metric not in _METRIC_SQL]
    if invalid:
        return {"error": f"지원하지 않는 metric: {invalid}", "allowed": sorted(_METRIC_SQL)}
    if years_back < 1 or years_back > 10:
        return {"error": "years_back must be between 1 and 10"}

    peer_group = build_custom_peer_group(
        company,
        year=year,
        peer_criteria=peer_criteria,
        peer_limit=peer_limit,
        fs_strategy=fs_strategy,
        prefix_len_start=prefix_len_start,
        size_bucket_decade=size_bucket_decade,
        exclude_other_sectors=exclude_other_sectors,
    )
    if "error" in peer_group:
        return peer_group

    subject = peer_group.get("subject") or {}
    policy = peer_group.get("selection_policy") or {}
    corp_code = subject.get("corp_code")
    fs_div = policy.get("fs_div_used") or "CFS"
    resolved_year = policy.get("resolved_year")
    if not corp_code or resolved_year is None:
        return {
            "subject": subject,
            "peer_group": peer_group,
            "years": [],
            "metrics": selected_metrics,
            "results": {},
            "data_quality": {
                "status": "missing",
                "limitations": ["resolved_peer_year_unavailable"],
            },
        }

    latest_year = int(resolved_year)
    years = list(range(latest_year - years_back + 1, latest_year + 1))
    peer_codes = [
        str(peer.get("corp_code"))
        for peer in peer_group.get("peers") or []
        if peer.get("corp_code")
    ]
    results: dict[int, dict[str, dict[str, Any]]] = {}
    with _engine_module.engine.connect() as conn:
        for current_year in years:
            year_result: dict[str, dict[str, Any]] = {}
            for metric in selected_metrics:
                peer_rows = _fetch_metric_values(
                    conn,
                    peer_codes,
                    _METRIC_SQL[metric],
                    current_year,
                    fs_div,
                )
                values = sorted(float(row[3]) for row in peer_rows if row[3] is not None)
                subject_rows = _fetch_metric_values(
                    conn,
                    [str(corp_code)],
                    _METRIC_SQL[metric],
                    current_year,
                    fs_div,
                )
                subject_value = (
                    float(subject_rows[0][3])
                    if subject_rows and subject_rows[0][3] is not None
                    else None
                )
                n = len(values)
                percentile = None
                if subject_value is not None and n:
                    percentile = round(
                        100.0 * sum(1 for value in values if value < subject_value) / n,
                        1,
                    )
                year_result[metric] = {
                    "p25": round(_quantile(values, 0.25), 2) if n >= 5 else None,
                    "p50": round(_quantile(values, 0.50), 2) if n else None,
                    "p75": round(_quantile(values, 0.75), 2) if n >= 5 else None,
                    "n": n,
                    "subject_value": round(subject_value, 2) if subject_value is not None else None,
                    "percentile": percentile,
                    "unit": _METRIC_UNIT.get(metric),
                }
            results[current_year] = year_result

    nonempty = sum(
        1
        for year_result in results.values()
        for metric_result in year_result.values()
        if metric_result["n"] > 0
    )
    return {
        "subject": subject,
        "year": year,
        "resolved_year": latest_year,
        "fs_div_used": fs_div,
        "peer_count": len(peer_codes),
        "peer_group": peer_group,
        "years": years,
        "metrics": selected_metrics,
        "results": results,
        "data_quality": {
            "status": "usable" if nonempty else "missing",
            "nonempty_metric_year_cells": nonempty,
            "interpretation": (
                "Percentiles and quantiles use only the explicitly resolved peer cohort. "
                "n is reported per metric-year because financial availability can vary by year."
            ),
        },
    }


def compare_custom_peer_bundle(
    company: str,
    *,
    year: int,
    peer_criteria: PeerCriteriaProfile | dict | list[str] | None = None,
    peer_limit: int = 30,
    fs_strategy: str = "auto",
    fs_div: str = "CFS",
    note_topics: list[str] | None = None,
) -> dict[str, Any]:
    """Run major peer analyses against the exact same resolved cohort."""
    peer_group = build_custom_peer_group(
        company,
        year=year,
        peer_criteria=peer_criteria,
        peer_limit=peer_limit,
        fs_strategy=fs_strategy,
    )
    if "error" in peer_group:
        return peer_group

    peer_codes = [
        str(peer.get("corp_code"))
        for peer in peer_group.get("peers") or []
        if peer.get("corp_code")
    ]
    exact_note_criteria = {
        "mode": "strict",
        "industry_basis": "custom_codes",
        "included_corp_codes": peer_codes,
    }

    return {
        "subject": peer_group.get("subject"),
        "year": year,
        "peer_group": peer_group,
        "audit_fees": compare_peer_audit_fees(
            company=company,
            year=year,
            peer_limit=peer_limit,
            fs_strategy=fs_strategy,
            _peer_group=peer_group,
        ),
        "risk_profile": compare_peer_risk_profile(
            company=company,
            year=year,
            peer_limit=peer_limit,
            fs_strategy=fs_strategy,
            _peer_group=peer_group,
        ),
        "accounting_policies": compare_peer_accounting_policies(
            company=company,
            year=year,
            peer_limit=peer_limit,
            fs_div=fs_div,
            fs_strategy=fs_strategy,
            _peer_group=peer_group,
        ),
        "kam_topics": compare_peer_kam_topics(
            company=company,
            year=year,
            peer_limit=peer_limit,
            fs_strategy=fs_strategy,
            _peer_group=peer_group,
        ),
        "audit_report_matters": compare_peer_audit_report_matters(
            company=company,
            year=year,
            peer_limit=peer_limit,
            fs_strategy=fs_strategy,
            _peer_group=peer_group,
        ),
        "audit_procedures": compare_peer_audit_procedures(
            company=company,
            year=year,
            peer_limit=peer_limit,
            fs_strategy=fs_strategy,
            _peer_group=peer_group,
        ),
        "accounting_notes": compare_peer_accounting_notes(
            company=company,
            year=year,
            topics=note_topics,
            peer_limit=peer_limit,
            fs_strategy=fs_strategy,
            peer_criteria=exact_note_criteria,
        ),
        "data_quality": {
            "status": "usable",
            "interpretation": (
                "All benchmark children reuse one resolved peer group. "
                "Accounting-note comparison receives an exact custom-code cohort derived "
                "from that same resolved group."
            ),
        },
    }


def search_note_disclosing_companies(
    keyword: str,
    *,
    year: int | None = None,
    market: str | None = None,
    induty_prefix: str | None = None,
    fs_div: str | None = None,
    section_type: str | None = None,
    limit: int = 50,
    include_excerpt: bool = True,
) -> dict[str, Any]:
    """Find companies whose cached accounting notes disclose a keyword."""
    normalized_keyword = str(keyword or "").strip()
    if not normalized_keyword:
        return {
            "error": "keyword is required",
            "companies": [],
            "total_companies": 0,
            "total_records": 0,
        }

    result = search_dataset(
        dataset="accounting_note_chapters",
        year=year,
        market=market,
        induty_prefix=induty_prefix,
        keyword=normalized_keyword,
        section_type=section_type,
        fs_div=fs_div,
        limit=limit,
        include_excerpt=include_excerpt,
    )
    if "error" in result:
        return result

    companies = result.get("companies") or []
    return {
        "query": {
            "dataset": "accounting_note_chapters",
            "keyword": normalized_keyword,
            "year": year,
            "market": market,
            "induty_prefix": induty_prefix,
            "fs_div": fs_div,
            "section_type": section_type,
            "limit": limit,
            "include_excerpt": include_excerpt,
        },
        "total_companies": result.get("total_companies", len(companies)),
        "total_records": result.get("total_records", 0),
        "companies": companies,
        "data_quality": {
            **(result.get("data_quality") or {}),
            "search_scope": "cached_accounting_note_chapters",
            "limitations": [
                "cache_miss_is_not_disclosure_absence",
                "result_count_is_bounded_by_limit",
            ],
        },
    }
