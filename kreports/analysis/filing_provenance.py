"""Resolve structured annual facts to proven DART filing sources."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any, TypeAlias

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

CompactCitationScope: TypeAlias = tuple[str, int, str]

_VALID_RECEIPT_GLOB = "*[0-9][0-9][0-9][0-9][0-9][0-9][0-9]" \
    "[0-9][0-9][0-9][0-9][0-9][0-9][0-9]*"


def valid_annual_filing_receipt(
    receipt: object,
    bsns_year: object,
) -> str | None:
    """Return a canonical receipt only when its filing date is plausible."""
    canonical = parent_rcept_no(str(receipt or ""))
    if canonical is None or len(canonical) != 14 or not canonical.isdigit():
        return None
    try:
        receipt_date = datetime.strptime(canonical[:8], "%Y%m%d").date()
        normalized_year = int(bsns_year)
    except (TypeError, ValueError):
        return None
    if not normalized_year <= receipt_date.year <= normalized_year + 10:
        return None
    return canonical


def compact_citation_anchors(
    scopes: Iterable[CompactCitationScope], *, batch_size: int = 100
) -> dict[CompactCitationScope, dict[str, Any]]:
    """Resolve annual-filing anchors for known compact scopes in bounded batches.

    A returned receipt is a company/year annual filing match, never direct
    endpoint lineage for the compact financial value.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")

    normalized_scopes: set[CompactCitationScope] = set()
    for corp_code, bsns_year, fs_div in scopes:
        normalized_corp_code = str(corp_code or "").strip()
        normalized_fs_div = str(fs_div or "").strip()
        try:
            normalized_year = int(bsns_year)
        except (TypeError, ValueError):
            continue
        if normalized_corp_code and normalized_fs_div and normalized_year > 0:
            normalized_scopes.add(
                (normalized_corp_code, normalized_year, normalized_fs_div)
            )
    ordered_scopes = sorted(normalized_scopes)
    if not ordered_scopes:
        return {}

    anchors: dict[CompactCitationScope, dict[str, Any]] = {}
    for start in range(0, len(ordered_scopes), batch_size):
        requested_scopes = ordered_scopes[start:start + batch_size]
        params: dict[str, Any] = {}
        requested_values: list[str] = []
        for index, (corp_code, bsns_year, fs_div) in enumerate(requested_scopes):
            params.update({
                f"corp_{index}": corp_code,
                f"year_{index}": bsns_year,
                f"fs_{index}": fs_div,
            })
            requested_values.append(
                f"(:corp_{index}, :year_{index}, :fs_{index})"
            )
        query = text(f"""
            WITH requested(corp_code, bsns_year, fs_div) AS (
                VALUES {", ".join(requested_values)}
            ),
            ranked AS (
                SELECT
                    requested.corp_code,
                    requested.bsns_year,
                    requested.fs_div,
                    d.rcept_no,
                    d.report_nm,
                    ROW_NUMBER() OVER (
                        PARTITION BY
                            requested.corp_code,
                            requested.bsns_year,
                            requested.fs_div
                        ORDER BY d.disc_date DESC, d.rcept_no DESC
                    ) AS source_rank
                FROM requested
                JOIN disclosures AS d ON d.corp_code = requested.corp_code
                WHERE d.report_nm LIKE
                      ('%사업보고서 (' || requested.bsns_year || '.%')
                  AND d.rcept_no GLOB '{_VALID_RECEIPT_GLOB}'
                  AND LENGTH(d.rcept_no) >= 14
                  AND SUBSTR(d.rcept_no, 1, 14) NOT GLOB '*[^0-9]*'
                  AND SUBSTR(d.rcept_no, 1, 8) = REPLACE(
                      SUBSTR(CAST(d.disc_date AS TEXT), 1, 10),
                      '-',
                      ''
                  )
            )
            SELECT corp_code, bsns_year, fs_div, rcept_no, report_nm
            FROM ranked
            WHERE source_rank = 1
        """)
        with _engine_module.engine.connect() as conn:
            rows = conn.execute(query, params).mappings().all()
        for row in rows:
            scope = (str(row["corp_code"]), int(row["bsns_year"]), str(row["fs_div"]))
            receipt = valid_annual_filing_receipt(
                row["rcept_no"],
                scope[1],
            )
            if receipt is None:
                continue
            anchors[scope] = {
                "corp_code": scope[0],
                "bsns_year": scope[1],
                "fs_div": scope[2],
                "rcept_no": receipt,
                "report_nm": row["report_nm"],
                "citation_basis": "company_year_annual_filing_match",
            }
    return anchors


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
              AND LENGTH(d.rcept_no) >= 14
              AND SUBSTR(d.rcept_no, 1, 14) NOT GLOB '*[^0-9]*'
              AND SUBSTR(d.rcept_no, 1, 8) = REPLACE(
                  SUBSTR(CAST(d.disc_date AS TEXT), 1, 10),
                  '-',
                  ''
              )
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
        normalized_year = int(disclosure_row["bsns_year"])
        resolved_rcept_no = valid_annual_filing_receipt(
            disclosure_row.get("rcept_no"),
            normalized_year,
        )
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
