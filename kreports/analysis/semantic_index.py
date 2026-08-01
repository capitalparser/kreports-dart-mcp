"""Read-only composition of existing filing evidence into a company context.

This module intentionally does not persist an index.  It is a bounded query
adapter over the runtime tables so MCP reads never create parser artifacts,
caches, or SQLite sidecars.
"""
from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import text

import kreports.db.engine as _engine_module
from kreports.processor.semantic_contracts import normalize_note_topic


TOPIC_ALIASES: dict[str, set[str]] = {
    "business_overview": {"business_overview", "business_description"},
    "major_shareholders_board": {"major_shareholders_board"},
    "risks": {"risk_management"},
    "raw_materials": {"business_description"},
    "facilities": {"business_description"},
    "contracts": {"key_contracts"},
    "accounting_policies": {"policy"},
    "kam": {"kam"},
    "audit_opinion": {"audit_opinion", "basis_for_opinion"},
    "subsequent_events": {"other_note"},
}

_BUCKETS = (
    "business_report",
    "audit_report",
    "notes",
    "evidence_documents",
    "disclosures",
    "financials",
)


def _resolve_company(conn, company: str) -> dict | None:
    row = conn.execute(
        text(
            """
            SELECT corp_code, stock_code, corp_name, market, induty_code
            FROM companies
            WHERE corp_code=:company OR stock_code=:company OR corp_name=:company
            ORDER BY CASE WHEN corp_code=:company THEN 0
                          WHEN stock_code=:company THEN 1 ELSE 2 END
            LIMIT 1
            """
        ),
        {"company": company},
    ).mappings().first()
    return dict(row) if row else None


def _requested_section_keys(topics: Iterable[str] | None, source_type: str) -> set[str] | None:
    if topics is None:
        return None
    keys: set[str] = set()
    for topic in topics:
        keys.update(TOPIC_ALIASES.get(topic, {topic}))
    if source_type == "business_report":
        return keys & {
            "business_overview",
            "business_description",
            "risk_management",
            "management_plan",
            "rd_activities",
            "key_contracts",
            "major_shareholders_board",
        }
    return keys & {"kam", "audit_opinion", "basis_for_opinion", "emphasis", "other_matter", "going_concern"}


def _section_rows(conn, *, corp_code: str, year: int, source_type: str, topics: list[str] | None) -> list[dict]:
    requested_keys = _requested_section_keys(topics, source_type)
    if requested_keys == set():
        return []
    rows = conn.execute(
        text(
            """
            SELECT rs.id, rs.rcept_no, rs.dcm_no, rs.source_type, rs.section_key,
                   rs.section_title, rs.body_text, rs.body_length, sd.id AS source_document_id,
                   sd.doc_hash
            FROM report_sections rs
            LEFT JOIN source_documents sd
              ON sd.rcept_no=rs.rcept_no AND sd.source_type=rs.source_type
            WHERE rs.corp_code=:corp_code AND rs.bsns_year=:year
              AND rs.source_type=:source_type
            ORDER BY rs.ordinal, rs.section_key
            """
        ),
        {"corp_code": corp_code, "year": year, "source_type": source_type},
    ).mappings().all()
    result: list[dict] = []
    for row in rows:
        item = dict(row)
        if requested_keys is not None and item["section_key"] not in requested_keys:
            continue
        item.update(
            source_locator=f"report_sections:{item['rcept_no']}:{item['section_key']}",
            availability="available",
            parser_version="semantic-v1",
            extraction_method="normalized_report_section",
            excerpt=item.pop("body_text"),
        )
        result.append(item)
    return result


def _note_rows(conn, *, corp_code: str, year: int, topics: list[str] | None) -> list[dict]:
    rows = conn.execute(
        text(
            """
            SELECT anc.id, anc.rcept_no, anc.dcm_no, anc.fs_div, anc.note_no,
                   anc.note_title, anc.section_type, anc.body, anc.body_length,
                   sd.id AS source_document_id
            FROM accounting_note_chapters anc
            LEFT JOIN source_documents sd
              ON sd.rcept_no=anc.rcept_no AND sd.source_type=anc.source_type
            WHERE anc.corp_code=:corp_code AND anc.bsns_year=:year
            ORDER BY anc.fs_div, anc.note_no, anc.section_type
            """
        ),
        {"corp_code": corp_code, "year": year},
    ).mappings().all()
    result: list[dict] = []
    for row in rows:
        item = dict(row)
        topic = normalize_note_topic(item["note_title"] or "", item["body"] or "")
        if topics is not None and not (set(topics) & {topic, "accounting_policies"}):
            continue
        item.update(
            topic=topic,
            section_key=item["section_type"],
            source_locator=f"accounting_note_chapters:{item['id']}",
            availability="available",
            parser_version="semantic-v1",
            extraction_method="normalized_note_chapter",
            excerpt=item.pop("body"),
        )
        result.append(item)
    return result


def _evidence_document_rows(conn, *, corp_code: str, year: int) -> list[dict]:
    rows = conn.execute(
        text(
            """
            SELECT ed.id, ed.rcept_no, ed.dcm_no, ed.source_type, ed.evidence_scope,
                   ed.title, ed.normalized_text, ed.text_hash, ed.source_count,
                   sd.id AS source_document_id
            FROM evidence_documents ed
            LEFT JOIN source_documents sd
              ON sd.rcept_no=ed.rcept_no AND sd.source_type=ed.source_type
            WHERE ed.corp_code=:corp_code AND ed.bsns_year=:year
            ORDER BY ed.source_type, ed.id
            """
        ),
        {"corp_code": corp_code, "year": year},
    ).mappings().all()
    return [
        {
            **dict(row),
            "source_locator": f"evidence_documents:{row['id']}",
            "availability": "summary_only",
            "parser_version": "semantic-v1",
            "extraction_method": "derived_evidence_document",
            "excerpt": row["normalized_text"],
        }
        for row in rows
    ]


def _disclosure_rows(conn, *, corp_code: str, year: int) -> list[dict]:
    rows = conn.execute(
        text(
            """
            SELECT rcept_no, disc_date, disc_type, report_nm, flr_nm
            FROM disclosures
            WHERE corp_code=:corp_code AND disc_date BETWEEN :start_date AND :end_date
            ORDER BY disc_date DESC, rcept_no DESC
            """
        ),
        {"corp_code": corp_code, "start_date": f"{year}-01-01", "end_date": f"{year}-12-31"},
    ).mappings().all()
    return [
        {
            **dict(row),
            "disc_date": (
                row["disc_date"].isoformat()
                if hasattr(row["disc_date"], "isoformat")
                else row["disc_date"]
            ),
            "source_locator": f"disclosures:{row['rcept_no']}",
            "availability": "available",
            "extraction_method": "disclosure_ledger",
        }
        for row in rows
    ]


def _financial_rows(conn, *, corp_code: str, year: int) -> list[dict]:
    rows = conn.execute(
        text(
            """
            SELECT fs_div, revenue, operating_profit, net_income, total_assets,
                   total_debt, total_equity, source
            FROM financials
            WHERE corp_code=:corp_code AND year=:year AND quarter=4
            ORDER BY fs_div
            """
        ),
        {"corp_code": corp_code, "year": year},
    ).mappings().all()
    return [
        {
            **dict(row),
            "quarter": 4,
            "source_locator": f"financials:{corp_code}:{year}:{row['fs_div']}:Q4",
            "availability": "available",
            "extraction_method": "financial_snapshot",
        }
        for row in rows
    ]


def build_company_context(
    company: str,
    year: int,
    topics: list[str] | None = None,
    *,
    read_engine=None,
) -> dict:
    """Build a local, read-only evidence context for one company-year.

    Empty buckets explicitly mean this runtime has no matching cached evidence;
    they never imply the filing or fact does not exist at DART.
    """
    active_engine = read_engine or _engine_module.engine
    normalized_topics = sorted(dict.fromkeys(topics)) if topics else None
    with active_engine.connect() as conn:
        subject = _resolve_company(conn, company)
        if subject is None:
            return {
                "error": "company_not_found",
                "company": company,
                "year": year,
                "read_only": True,
            }
        corp_code = subject["corp_code"]
        result = {
            "subject": subject,
            "year": year,
            "topics_requested": normalized_topics,
            "read_only": True,
            "business_report": _section_rows(conn, corp_code=corp_code, year=year, source_type="business_report", topics=normalized_topics),
            "audit_report": _section_rows(conn, corp_code=corp_code, year=year, source_type="audit_report", topics=normalized_topics),
            "notes": _note_rows(conn, corp_code=corp_code, year=year, topics=normalized_topics),
            "evidence_documents": _evidence_document_rows(conn, corp_code=corp_code, year=year),
            "disclosures": _disclosure_rows(conn, corp_code=corp_code, year=year),
            "financials": _financial_rows(conn, corp_code=corp_code, year=year),
        }
    result["availability"] = {
        bucket: "available" if result[bucket] else "unavailable"
        for bucket in _BUCKETS
    }
    result["interpretation"] = (
        "Local cached evidence only. unavailable means no matching cached row, not a claim that DART has no filing."
    )
    return result
