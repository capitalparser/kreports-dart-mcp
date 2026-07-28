"""Resolve structured annual facts to proven DART filing sources."""
from __future__ import annotations

from typing import Any

from sqlalchemy import text

import kreports.db.engine as _engine_module
from kreports.analysis.evidence import parent_rcept_no


_SOURCE_FACT_TABLES = {
    "financial_facts_compact": ("financial_facts_compact", "bsns_year", ""),
    "financial_facts": (
        "financial_facts",
        "bsns_year",
        "AND f.reprt_code='11011'",
    ),
    "financials": ("financials", "year", "AND f.quarter=4"),
}


def annual_filing_sources(
    corp_code: str,
    bsns_years: list[int],
    *,
    source_table: str,
    fs_div: str | None = None,
) -> dict[int, dict[str, Any]]:
    """Resolve annual filings in one query bound to matching fact identities.

    A local fact table does not itself prove a filing citation.  The fact and
    the annual disclosure must independently agree on company and business
    year; unsupported source tables remain uncitable.
    """
    fact_table = _SOURCE_FACT_TABLES.get(source_table)
    if fact_table is None:
        return {}

    normalized_corp_code = str(corp_code or "").strip()
    normalized_years: list[int] = []
    for value in bsns_years:
        try:
            normalized_year = int(value)
        except (TypeError, ValueError):
            continue
        if normalized_year > 0 and normalized_year not in normalized_years:
            normalized_years.append(normalized_year)
    if not normalized_corp_code or not normalized_years:
        return {}

    params: dict[str, Any] = {"corp_code": normalized_corp_code}
    fact_year_clauses: list[str] = []
    disclosure_year_clauses: list[str] = []
    for index, normalized_year in enumerate(normalized_years[:20]):
        params[f"bsns_year_{index}"] = normalized_year
        params[f"annual_year_pattern_{index}"] = (
            f"%사업보고서 ({normalized_year}.%"
        )
        fact_year_clauses.append(
            f"f.{fact_table[1]}=:bsns_year_{index}"
        )
        disclosure_year_clauses.append(
            f"(fact.bsns_year=:bsns_year_{index} "
            f"AND d.report_nm LIKE :annual_year_pattern_{index})"
        )
    fs_div_clause = ""
    if fs_div:
        params["fs_div"] = str(fs_div)
        fs_div_clause = "AND f.fs_div=:fs_div"

    valid_receipt_pattern = "*[0-9][0-9][0-9][0-9][0-9][0-9][0-9]" \
        "[0-9][0-9][0-9][0-9][0-9][0-9][0-9]*"
    query = text(f"""
        WITH fact_identities AS (
            SELECT DISTINCT f.{fact_table[1]} AS bsns_year, f.fs_div
            FROM {fact_table[0]} AS f
            WHERE f.corp_code=:corp_code
              {fact_table[2]}
              {fs_div_clause}
              AND ({" OR ".join(fact_year_clauses)})
        ),
        ranked_disclosures AS (
            SELECT fact.bsns_year, fact.fs_div,
                   d.rcept_no, d.corp_code, d.corp_name, d.report_nm,
                   ROW_NUMBER() OVER (
                       PARTITION BY fact.bsns_year, fact.fs_div
                       ORDER BY d.disc_date DESC, d.rcept_no DESC
                   ) AS source_rank
            FROM fact_identities AS fact
            JOIN disclosures AS d ON d.corp_code=:corp_code
            WHERE ({" OR ".join(disclosure_year_clauses)})
              AND d.rcept_no GLOB '{valid_receipt_pattern}'
        )
        SELECT bsns_year, fs_div, rcept_no, corp_code, corp_name, report_nm
        FROM ranked_disclosures
        WHERE source_rank=1
        ORDER BY bsns_year DESC, fs_div ASC
    """)
    with _engine_module.engine.connect() as conn:
        disclosure_rows = conn.execute(query, params).mappings().all()

    sources: dict[int, dict[str, Any]] = {}
    for disclosure_row in disclosure_rows:
        resolved_rcept_no = parent_rcept_no(disclosure_row.get("rcept_no"))
        normalized_year = int(disclosure_row["bsns_year"])
        if not resolved_rcept_no or normalized_year in sources:
            continue
        sources[normalized_year] = {
            "corp_code": normalized_corp_code,
            "corp_name": disclosure_row.get("corp_name")
            or normalized_corp_code,
            "report_nm": disclosure_row.get("report_nm"),
            "bsns_year": normalized_year,
            "rcept_no": resolved_rcept_no,
            "section_title": "재무제표",
            "source_table": source_table,
            "fs_div": disclosure_row.get("fs_div"),
        }
    return sources


def annual_filing_source(
    corp_code: str,
    bsns_year: int,
    *,
    source_table: str,
    fs_div: str | None = None,
) -> dict[str, Any] | None:
    """Resolve one annual filing through the fact-bound batch resolver."""
    try:
        normalized_year = int(bsns_year)
    except (TypeError, ValueError):
        return None
    return annual_filing_sources(
        corp_code,
        [normalized_year],
        source_table=source_table,
        fs_div=fs_div,
    ).get(normalized_year)
