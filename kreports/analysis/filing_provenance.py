"""Resolve structured annual facts to proven DART filing sources."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any, TypeAlias

from sqlalchemy import text

import kreports.db.engine as _engine_module
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


def _is_exact_annual_report_name(value: object, bsns_year: int) -> bool:
    return str(value or "").strip().startswith(f"사업보고서 ({bsns_year}.")

def valid_annual_filing_receipt(
    receipt: object,
    bsns_year: object,
) -> str | None:
    """Return a receipt only when the original stored value is exact and plausible.

    This provenance boundary intentionally does not use ``parent_rcept_no``:
    attachment/document ids and whitespace-wrapped values are not filing
    receipts.  Callers that need a parent identifier for document navigation
    must keep that weaker normalization separate from annual-filing proof.
    """
    raw_receipt = str(receipt or "")
    if len(raw_receipt) != 14 or not raw_receipt.isdigit():
        return None
    try:
        receipt_date = datetime.strptime(raw_receipt[:8], "%Y%m%d").date()
        normalized_year = int(bsns_year)
    except (TypeError, ValueError):
        return None
    if not normalized_year <= receipt_date.year <= normalized_year + 10:
        return None
    return raw_receipt


def canonical_annual_filing_source_binding(
    row: dict[str, Any],
    *,
    corp_code: object,
    bsns_year: object,
) -> str | None:
    """Return a receipt only for one exact company/year annual source binding.

    Cached excerpts are not sufficient DART evidence by themselves.  The
    receipt must bind the cached row to a same-company, same-business-year
    source document and to the exact dated annual-report disclosure.
    """
    receipt = valid_annual_filing_receipt(row.get("rcept_no"), bsns_year)
    if receipt is None:
        return None
    try:
        normalized_year = int(bsns_year)
    except (TypeError, ValueError):
        return None
    if (
        row.get("source_document_id") is None
        or str(row.get("source_document_rcept_no") or "") != receipt
        or str(row.get("source_document_corp_code") or "") != str(corp_code)
        or row.get("source_document_bsns_year") != normalized_year
        or not _is_exact_annual_report_name(
            row.get("source_document_report_nm"), normalized_year,
        )
        or str(row.get("disclosure_rcept_no") or "") != receipt
        or str(row.get("disclosure_corp_code") or "") != str(corp_code)
        or not _is_exact_annual_report_name(
            row.get("disclosure_report_nm"), normalized_year,
        )
    ):
        return None
    return _exact_receipt_matches_disclosure_date(
        receipt,
        normalized_year,
        row.get("disclosure_disc_date"),
    )


def canonical_annual_filing_source_receipt(
    *,
    corp_code: object,
    bsns_year: object,
    rcept_no: object,
    source_document_id: object,
    source_type: object,
    read_engine=None,
) -> str | None:
    """Re-query one canonical annual filing source at a public boundary.

    Public rows may retain cache metadata for inspection, but their serialized
    provenance flags are never authority to emit a DART source or link.
    """
    if (
        type(source_document_id) is not int
        or source_document_id <= 0
        or not str(source_type or "")
    ):
        return None
    return _canonical_annual_filing_source_receipt(
        corp_code=corp_code,
        bsns_year=bsns_year,
        rcept_no=rcept_no,
        source_document_id=source_document_id,
        source_type=source_type,
        read_engine=read_engine,
    )


def canonical_business_report_source_receipt(
    *,
    corp_code: object,
    bsns_year: object,
    rcept_no: object,
    read_engine=None,
) -> str | None:
    """Verify a policy row through an explicit fixed business-report path."""
    return _canonical_annual_filing_source_receipt(
        corp_code=corp_code,
        bsns_year=bsns_year,
        rcept_no=rcept_no,
        source_document_id=None,
        source_type="business_report",
        read_engine=read_engine,
    )


def _canonical_annual_filing_source_receipt(
    *,
    corp_code: object,
    bsns_year: object,
    rcept_no: object,
    source_document_id: object | None,
    source_type: object,
    read_engine=None,
) -> str | None:
    receipt = valid_annual_filing_receipt(rcept_no, bsns_year)
    try:
        normalized_year = int(bsns_year)
    except (TypeError, ValueError):
        return None
    normalized_corp_code = str(corp_code or "")
    normalized_source_type = str(source_type or "")
    if not receipt or not normalized_corp_code or not normalized_source_type:
        return None
    conditions = [
        "sd.rcept_no=:rcept_no",
        "sd.corp_code=:corp_code",
        "sd.bsns_year=:bsns_year",
        "d.rcept_no=sd.rcept_no",
        "d.corp_code=sd.corp_code",
    ]
    params: dict[str, Any] = {
        "rcept_no": receipt,
        "corp_code": normalized_corp_code,
        "bsns_year": normalized_year,
    }
    if source_document_id is not None:
        try:
            params["source_document_id"] = int(source_document_id)
        except (TypeError, ValueError):
            return None
        conditions.append("sd.id=:source_document_id")
    params["source_type"] = normalized_source_type
    conditions.append("sd.source_type=:source_type")
    stmt = text(f"""
        SELECT sd.id AS source_document_id,
               sd.rcept_no AS source_document_rcept_no,
               sd.corp_code AS source_document_corp_code,
               sd.bsns_year AS source_document_bsns_year,
               sd.report_nm AS source_document_report_nm,
               d.rcept_no AS disclosure_rcept_no,
               d.corp_code AS disclosure_corp_code,
               d.disc_date AS disclosure_disc_date,
               d.report_nm AS disclosure_report_nm
        FROM source_documents sd
        JOIN disclosures d ON d.rcept_no=sd.rcept_no AND d.corp_code=sd.corp_code
        WHERE {' AND '.join(conditions)}
        ORDER BY sd.id
        LIMIT 1
    """)
    active_engine = read_engine or _engine_module.engine
    try:
        with active_engine.connect() as conn:
            row = conn.execute(stmt, params).mappings().first()
    except Exception:
        return None
    if row is None:
        return None
    return canonical_annual_filing_source_binding(
        {"rcept_no": receipt, **dict(row)},
        corp_code=normalized_corp_code,
        bsns_year=normalized_year,
    )


def _exact_receipt_matches_disclosure_date(
    receipt: object,
    bsns_year: object,
    disclosure_date: object,
) -> str | None:
    """Prove an unmodified annual receipt against its recorded disclosure day."""
    raw_receipt = str(receipt or "")
    resolved_receipt = valid_annual_filing_receipt(raw_receipt, bsns_year)
    normalized_disclosure_date = str(disclosure_date or "")[:10].replace("-", "")
    if (
        resolved_receipt is None
        or resolved_receipt != raw_receipt
        or resolved_receipt[:8] != normalized_disclosure_date
    ):
        return None
    return resolved_receipt


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
                    d.disc_date,
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
            )
            SELECT corp_code, bsns_year, fs_div, rcept_no, disc_date, report_nm
            FROM ranked
            WHERE source_rank = 1
        """)
        with _engine_module.engine.connect() as conn:
            rows = conn.execute(query, params).mappings().all()
        for row in rows:
            scope = (str(row["corp_code"]), int(row["bsns_year"]), str(row["fs_div"]))
            receipt = _exact_receipt_matches_disclosure_date(
                row["rcept_no"],
                scope[1],
                row["disc_date"],
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
                   d.rcept_no, d.disc_date, d.corp_code, d.corp_name, d.report_nm,
                   ROW_NUMBER() OVER (
                       PARTITION BY fact.bsns_year, fact.fs_div
                       ORDER BY d.disc_date DESC, d.rcept_no DESC
                   ) AS source_rank
            FROM fact_identities AS fact
            JOIN disclosures AS d ON d.corp_code=:corp_code
            WHERE ({" OR ".join(disclosure_year_clauses)})
        )
        SELECT bsns_year, fs_div, rcept_no, disc_date, corp_code, corp_name, report_nm
        FROM ranked_disclosures
        WHERE source_rank=1
        ORDER BY bsns_year DESC, fs_div ASC
    """)
    with _engine_module.engine.connect() as conn:
        disclosure_rows = conn.execute(query, params).mappings().all()

    sources: dict[int, dict[str, Any]] = {}
    for disclosure_row in disclosure_rows:
        normalized_year = int(disclosure_row["bsns_year"])
        resolved_rcept_no = _exact_receipt_matches_disclosure_date(
            disclosure_row.get("rcept_no"),
            normalized_year,
            disclosure_row.get("disc_date"),
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
