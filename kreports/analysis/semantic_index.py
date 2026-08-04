"""Read-only composition of existing filing evidence into a company context.

This module intentionally does not persist an index.  It is a bounded query
adapter over the runtime tables so MCP reads never create parser artifacts,
caches, or SQLite sidecars.
"""
from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import text

import kreports.db.engine as _engine_module
from kreports.analysis.note_comparison import NOTE_TOPICS
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


def _excerpt_availability(row: dict, *, body_key: str) -> str:
    """Do not present an externalized or shortened body as complete evidence."""
    body = str(row.get(body_key) or "")
    full_length = row.get("full_text_length")
    storage_status = str(row.get("full_text_storage_status") or "").lower()
    if (
        row.get("full_text_uri")
        or storage_status in {"externalized", "truncated", "compressed"}
        or (isinstance(full_length, int) and full_length > len(body))
    ):
        return "summary_only"
    return "available"


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
                   rs.section_title, rs.body_text, rs.body_length, rs.ordinal,
                   rs.full_text_uri, rs.full_text_hash, rs.full_text_length,
                   rs.full_text_compressed_length, rs.full_text_storage_status,
                   sd.id AS source_document_id, sd.doc_hash AS source_doc_hash,
                   sd.storage_uri AS source_storage_uri,
                   sd.content_length AS source_content_length,
                   sd.compressed_length AS source_compressed_length,
                   sd.storage_status AS source_storage_status
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
            source_locator=(
                f"report_sections:{item['id']}:{item['rcept_no']}:"
                f"{item['section_key']}:{item['ordinal']}"
            ),
            availability=_excerpt_availability(item, body_key="body_text"),
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
                   anc.full_text_uri, anc.full_text_hash, anc.full_text_length,
                   anc.full_text_compressed_length, anc.full_text_storage_status,
                   sd.id AS source_document_id, sd.doc_hash AS source_doc_hash,
                   sd.storage_uri AS source_storage_uri,
                   sd.content_length AS source_content_length,
                   sd.compressed_length AS source_compressed_length,
                   sd.storage_status AS source_storage_status
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
        if topics is not None:
            requested = set(topics)
            policy_match = (
                "accounting_policies" in requested
                and (topic == "accounting_policies" or item["section_type"] == "policy")
            )
            if not (topic in requested or policy_match):
                continue
        item.update(
            topic=topic,
            section_key=item["section_type"],
            source_locator=f"accounting_note_chapters:{item['id']}",
            availability=_excerpt_availability(item, body_key="body"),
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
                   ed.title, ed.normalized_text, ed.text_hash, ed.text_length,
                   ed.full_text_uri, ed.full_text_hash, ed.full_text_length,
                   ed.full_text_compressed_length, ed.full_text_storage_status,
                   ed.source_count, sd.id AS source_document_id,
                   sd.doc_hash AS source_doc_hash, sd.storage_uri AS source_storage_uri,
                   sd.content_length AS source_content_length,
                   sd.compressed_length AS source_compressed_length,
                   sd.storage_status AS source_storage_status
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


def _local_note_comparison_summary(
    notes: list[dict],
    requested_topics: list[str] | None,
) -> dict:
    """Expose local note coverage without implying a peer comparison occurred."""

    def aggregate_availability(rows: list[dict]) -> str:
        statuses = {
            str(row.get("availability") or "unavailable")
            for row in rows
        }
        if not statuses or statuses == {"unavailable"}:
            return "unavailable"
        if statuses == {"available"}:
            return "available"
        if statuses == {"summary_only"}:
            return "summary_only"
        return "partial"

    topics = requested_topics or sorted({
        str(note.get("topic"))
        for note in notes
        if note.get("topic")
    })
    topic_coverage = []
    missing_evidence = []
    source_locators = []
    fs_div_selection = []
    for topic in topics:
        topic_notes = [note for note in notes if note.get("topic") == topic]
        availability = aggregate_availability(topic_notes)
        topic_coverage.append({
            "topic": topic,
            "subject_availability": availability,
            "peer_availability": "not_requested",
        })
        if not topic_notes:
            missing_evidence.append({
                "topic": topic,
                "reason": "no_cached_subject_note_for_exact_business_year",
            })
        for note in topic_notes:
            locator = note.get("source_locator")
            if locator:
                source_locators.append(str(locator))
            fs_div = note.get("fs_div")
            if fs_div:
                fs_div_selection.append({
                    "topic": topic,
                    "requested": None,
                    "used": str(fs_div),
                    "status": "local_context_only",
                })
    missing_evidence.append({
        "reason": "peer_note_comparison_not_requested",
    })
    return {
        "topic_coverage": topic_coverage,
        "subject_availability": (
            aggregate_availability(notes)
        ),
        "peer_availability": "not_requested",
        "differences": [],
        "fs_div_selection": fs_div_selection,
        "source_locators": sorted(set(source_locators)),
        "missing_evidence": missing_evidence,
    }


def build_company_context(
    company: str,
    year: int,
    topics: list[str] | None = None,
    *,
    note_topics: list[str] | None = None,
    read_engine=None,
) -> dict:
    """Build a local, read-only evidence context for one company-year.

    Empty buckets explicitly mean this runtime has no matching cached evidence;
    they never imply the filing or fact does not exist at DART.
    """
    active_engine = read_engine or _engine_module.engine
    normalized_topics = sorted(dict.fromkeys(topics)) if topics else None
    normalized_note_topics = (
        sorted(dict.fromkeys(note_topics))
        if note_topics is not None
        else normalized_topics
    )
    summary_note_topics = (
        normalized_note_topics
        if note_topics is not None
        else (
            [topic for topic in normalized_note_topics if topic in NOTE_TOPICS]
            if normalized_note_topics is not None
            else None
        )
    )
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
            "note_topics_requested": normalized_note_topics,
            "read_only": True,
            "business_report": _section_rows(conn, corp_code=corp_code, year=year, source_type="business_report", topics=normalized_topics),
            "audit_report": _section_rows(conn, corp_code=corp_code, year=year, source_type="audit_report", topics=normalized_topics),
            "notes": _note_rows(conn, corp_code=corp_code, year=year, topics=normalized_note_topics),
            "evidence_documents": _evidence_document_rows(conn, corp_code=corp_code, year=year),
            "disclosures": _disclosure_rows(conn, corp_code=corp_code, year=year),
            "financials": _financial_rows(conn, corp_code=corp_code, year=year),
        }
    result["availability"] = {
        bucket: "available" if result[bucket] else "unavailable"
        for bucket in _BUCKETS
    }
    result["note_comparison_summary"] = _local_note_comparison_summary(
        result["notes"],
        summary_note_topics,
    )
    result["interpretation"] = (
        "Local cached evidence only. unavailable means no matching cached row, not a claim that DART has no filing."
    )
    return result
