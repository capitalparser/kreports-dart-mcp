"""Accounting policy and estimate-judgment change analysis."""
from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import text

import kreports.db.engine as _engine_module
from kreports.analysis.filing_provenance import valid_annual_filing_receipt


def _similarity(a: str, b: str) -> float:
    return round(SequenceMatcher(None, a or "", b or "").ratio(), 4)


def _annual_note_filing_sources(
    corp_code: str,
    years: set[int],
) -> dict[tuple[int, str], dict[str, Any]]:
    """Return exact company-year annual filings usable by cached note chapters.

    ``annual_filing_sources`` deliberately admits only structured financial
    fact tables.  Note chapters have a different source-table contract, so
    their receipt must be proven directly against the same company's annual
    disclosure for the chapter's own business year.  This helper never picks
    an alternative receipt for a chapter.
    """
    normalized_years = sorted(year for year in years if year > 0)[:20]
    if not normalized_years:
        return {}

    params: dict[str, object] = {"corp_code": str(corp_code or "").strip()}
    annual_clauses: list[str] = []
    for index, year in enumerate(normalized_years):
        params[f"annual_year_{index}"] = f"%사업보고서 ({year}.%"
        annual_clauses.append(f"d.report_nm LIKE :annual_year_{index}")
    stmt = text(f"""
        SELECT d.rcept_no, d.corp_code, d.corp_name, d.disc_date, d.report_nm
        FROM disclosures AS d
        WHERE d.corp_code=:corp_code
          AND ({" OR ".join(annual_clauses)})
        ORDER BY d.disc_date DESC, d.rcept_no DESC
    """)
    with _engine_module.engine.connect() as conn:
        rows = conn.execute(stmt, params).mappings().all()

    sources: dict[tuple[int, str], dict[str, Any]] = {}
    for year in normalized_years:
        for row in rows:
            report_nm = str(row.get("report_nm") or "")
            if f"사업보고서 ({year}." not in report_nm:
                continue
            raw_receipt = str(row.get("rcept_no") or "").strip()
            receipt = valid_annual_filing_receipt(raw_receipt, year)
            disclosure_date = str(row.get("disc_date") or "")[:10].replace("-", "")
            if (
                receipt is None
                or raw_receipt != receipt
                or receipt[:8] != disclosure_date
                or (year, receipt) in sources
            ):
                continue
            sources[(year, receipt)] = {
                "corp_code": str(row.get("corp_code") or corp_code),
                "corp_name": row.get("corp_name") or corp_code,
                "bsns_year": year,
                "rcept_no": receipt,
                "report_nm": report_nm,
                "source_table": "accounting_note_chapters",
            }
    return sources


def _chapter_provenance(
    row: dict[str, Any],
    annual_sources: dict[tuple[int, str], dict[str, Any]],
) -> tuple[str, str, dict[str, Any] | None]:
    """Classify a cached chapter without substituting a different filing."""
    year = int(row["bsns_year"])
    raw_receipt = str(row.get("rcept_no") or "").strip()
    receipt = valid_annual_filing_receipt(raw_receipt, year)
    if receipt is None or raw_receipt != receipt:
        return raw_receipt, "invalid_receipt", None

    annual_source = annual_sources.get((year, receipt))
    if annual_source is None:
        return receipt, "unproven_annual_filing", None

    note_no = str(row.get("note_no") or "").strip()
    note_title = str(row.get("note_title") or "회계정책 주석").strip()
    return receipt, "proven_annual_filing", {
        **annual_source,
        "fs_div": str(row.get("fs_div") or ""),
        "section_title": f"주석 {note_no} {note_title}".strip(),
    }


def accounting_policy_changes(
    company: str,
    *,
    start_year: int,
    end_year: int,
    fs_div: str | None = None,
) -> dict:
    """Compare note 2/3/4 policy and estimate chapters across years."""
    where = [
        "anc.corp_code=:corp_code",
        "anc.bsns_year BETWEEN :start_year AND :end_year",
        "anc.note_no IN ('2', '3', '4')",
        "anc.section_type IN ('basis', 'policy', 'estimate_judgment')",
    ]
    params: dict[str, object] = {
        "corp_code": company,
        "start_year": int(start_year),
        "end_year": int(end_year),
    }
    if fs_div:
        where.append("anc.fs_div=:fs_div")
        params["fs_div"] = fs_div
    stmt = text(f"""
        SELECT anc.corp_code, c.corp_name, anc.bsns_year, anc.fs_div, anc.rcept_no,
               anc.note_no, anc.note_title, anc.section_type, anc.body,
               anc.body_hash, anc.body_length
        FROM accounting_note_chapters anc
        JOIN companies c ON c.corp_code=anc.corp_code
        WHERE {" AND ".join(where)}
        ORDER BY anc.fs_div, anc.note_no, anc.section_type, anc.bsns_year
    """)
    with _engine_module.engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(stmt, params).mappings()]

    annual_sources = _annual_note_filing_sources(
        company,
        {int(row["bsns_year"]) for row in rows},
    )

    previous_by_key: dict[tuple[str, str, str], dict] = {}
    changes: list[dict] = []
    for row in rows:
        key = (row["fs_div"], row["note_no"], row["section_type"])
        previous = previous_by_key.get(key)
        body = row.get("body") or ""
        if previous is None:
            change_type = "new"
            sim = None
        else:
            same_hash = previous.get("body_hash") and previous.get("body_hash") == row.get("body_hash")
            sim = _similarity(previous.get("body") or "", body)
            change_type = "stable" if same_hash or sim >= 0.98 else "changed"
        receipt, provenance_status, filing_source = _chapter_provenance(
            row,
            annual_sources,
        )
        changes.append({
            "year": row["bsns_year"],
            "fs_div": row["fs_div"],
            "rcept_no": receipt,
            "note_no": row["note_no"],
            "note_title": row.get("note_title"),
            "section_type": row["section_type"],
            "change_type": change_type,
            "similarity_to_previous": sim,
            "body_length": row.get("body_length"),
            "body_excerpt": body[:900],
            "provenance_status": provenance_status,
            "filing_source": filing_source,
        })
        previous_by_key[key] = row

    changed = [row for row in changes if row["change_type"] == "changed"]
    unproven_changes = [
        row for row in changes
        if row["provenance_status"] != "proven_annual_filing"
    ]
    limitations = [
        "Policy changes are text-difference hints from cached note chapters; they require professional review before concluding that accounting policy changed.",
    ]
    if unproven_changes:
        limitations.append(
            f"주석 행 {len(unproven_changes)}건의 접수번호가 요청 회사·사업연도 사업보고서로 검증되지 않아 원문 근거로 사용할 수 없습니다."
        )
    return {
        "company": company,
        "start_year": int(start_year),
        "end_year": int(end_year),
        "fs_div": fs_div,
        "change_count": len(changed),
        "changes": changes,
        "changed_items": changed,
        "data_quality": {
            "status": (
                "missing" if not changes
                else "limited" if unproven_changes
                else "usable"
            ),
            "source": "accounting_note_chapters",
            "interpretation": limitations[0],
            "limitations": limitations,
        },
    }
