"""Resolve structured annual facts to proven DART filing sources."""
from __future__ import annotations

from typing import Any

from sqlalchemy import text

import kreports.db.engine as _engine_module
from kreports.analysis.evidence import parent_rcept_no


_SOURCE_FACT_QUERIES = {
    "financial_facts_compact": """
        SELECT fs_div
        FROM financial_facts_compact
        WHERE corp_code=:corp_code AND bsns_year=:bsns_year
        {fs_div_clause}
        ORDER BY fs_div ASC
        LIMIT 1
    """,
    "financial_facts": """
        SELECT fs_div
        FROM financial_facts
        WHERE corp_code=:corp_code AND bsns_year=:bsns_year
          AND reprt_code='11011'
        {fs_div_clause}
        ORDER BY fs_div ASC
        LIMIT 1
    """,
    "financials": """
        SELECT fs_div
        FROM financials
        WHERE corp_code=:corp_code AND year=:bsns_year AND quarter=4
        {fs_div_clause}
        ORDER BY fs_div ASC
        LIMIT 1
    """,
}


def annual_filing_source(
    corp_code: str,
    bsns_year: int,
    *,
    source_table: str,
    fs_div: str | None = None,
) -> dict[str, Any] | None:
    """Resolve the latest valid same-company, same-year annual filing.

    A local fact table does not itself prove a filing citation.  The fact and
    the annual disclosure must independently agree on company and business
    year; unsupported source tables remain uncitable.
    """
    query_template = _SOURCE_FACT_QUERIES.get(source_table)
    if query_template is None:
        return None

    normalized_corp_code = str(corp_code or "").strip()
    try:
        normalized_year = int(bsns_year)
    except (TypeError, ValueError):
        return None
    if not normalized_corp_code or normalized_year < 1:
        return None

    params: dict[str, Any] = {
        "corp_code": normalized_corp_code,
        "bsns_year": normalized_year,
    }
    fs_div_clause = ""
    if fs_div:
        params["fs_div"] = str(fs_div)
        fs_div_clause = "AND fs_div=:fs_div"

    fact_query = query_template.format(fs_div_clause=fs_div_clause)
    annual_year_pattern = f"%사업보고서 ({normalized_year}.%"
    with _engine_module.engine.connect() as conn:
        fact_row = conn.execute(text(fact_query), params).mappings().first()
        if not fact_row:
            return None
        disclosure_rows = conn.execute(
            text("""
                SELECT rcept_no, corp_code, corp_name, report_nm
                FROM disclosures
                WHERE corp_code=:corp_code
                  AND report_nm LIKE :annual_year_pattern
                ORDER BY disc_date DESC, rcept_no DESC
            """),
            {
                "corp_code": normalized_corp_code,
                "annual_year_pattern": annual_year_pattern,
            },
        ).mappings().all()

    for disclosure_row in disclosure_rows:
        resolved_rcept_no = parent_rcept_no(disclosure_row.get("rcept_no"))
        if resolved_rcept_no:
            return {
                "corp_code": normalized_corp_code,
                "corp_name": disclosure_row.get("corp_name") or normalized_corp_code,
                "report_nm": disclosure_row.get("report_nm"),
                "bsns_year": normalized_year,
                "rcept_no": resolved_rcept_no,
                "section_title": "재무제표",
                "source_table": source_table,
                "fs_div": fact_row.get("fs_div"),
            }

    return None
