"""Read-only audit of derived report extraction coverage.

The audit deliberately treats a raw object URI as *unverified metadata*, not
as a readable filing.  It therefore never fetches raw storage and never calls
an extractor.  A missing note chapter is reported only for an actual chapter
candidate found by the inline note parser; report-section absences have no
equivalent source parser and remain ``unverified``.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from sqlalchemy import text

import kreports.db.engine as _engine_module
from kreports.processor.note_source_index import (
    NOTE_TOPICS,
    PARSER_VERSION,
    parse_note_source_document,
)
from kreports.processor.semantic_contracts import normalize_note_topic


REQUIRED_REPORT_SECTIONS: dict[str, dict[str, tuple[str, ...]]] = {
    "business_report": {
        "business_overview": ("business_overview", "business_description"),
        "risks": ("risk_management",),
        "shareholders_board": ("major_shareholders_board",),
    },
    "audit_report": {
        "audit_opinion": ("audit_opinion", "basis_for_opinion"),
        "kam": ("kam",),
    },
}

_NOTE_GAP_STATUSES = ("available", "missing_derived")
_SECTION_GAP_STATUSES = ("available", "unverified")


def _where(*, year: int | None, source_type: str | None) -> tuple[str, dict[str, object]]:
    if source_type is not None and source_type not in REQUIRED_REPORT_SECTIONS:
        raise ValueError("source_type must be business_report or audit_report")
    where = """
        WHERE source_type IN ('business_report', 'audit_report')
          AND content_type != 'derived_report_sections'
    """
    params: dict[str, object] = {}
    if year is not None:
        where += " AND bsns_year=:year"
        params["year"] = year
    if source_type is not None:
        where += " AND source_type=:source_type"
        params["source_type"] = source_type
    return where, params


def _raw_status(row: dict) -> str:
    if str(row.get("raw_content") or "").strip():
        return "inline_readable"
    if str(row.get("storage_uri") or "").strip():
        return "external_raw_unverified"
    return "raw_unavailable"


def _chapter_topic(row: dict) -> str:
    if row.get("section_type") == "policy":
        return "accounting_policies"
    return normalize_note_topic(str(row.get("note_title") or ""), str(row.get("body") or ""))


def _counter_payload(counter: Counter[str], statuses: tuple[str, ...]) -> dict[str, int]:
    return {status: int(counter[status]) for status in statuses if counter[status]}


def _source_key(row: dict) -> tuple[str, int, str, str]:
    """Return the complete logical filing identity used in every cross-table map."""
    return (
        str(row["corp_code"]),
        int(row["bsns_year"]),
        str(row["rcept_no"]),
        str(row["source_type"]),
    )


def build_extraction_gap_audit(
    *,
    year: int | None = None,
    source_type: str | None = None,
    company_offset: int = 0,
    company_limit: int = 200,
    _read_engine=None,
) -> dict:
    """Compare cached source/derived tables without fetching or persisting data.

    Output is intentionally bounded to a page of receipt-level samples.  The
    aggregate breakdowns remain complete for the selected filters, while each
    missing status remains fail-closed about unavailable external raw content.
    """
    if company_offset < 0:
        raise ValueError("company_offset must be zero or greater")
    if not 1 <= company_limit <= 1_000:
        raise ValueError("company_limit must be between 1 and 1000")

    where, params = _where(year=year, source_type=source_type)
    active_engine = _read_engine or _engine_module.engine
    source_stmt = text(f"""
        SELECT sd.id, sd.rcept_no, sd.dcm_no, sd.corp_code, MAX(c.corp_name) AS corp_name,
               sd.bsns_year, sd.source_type, sd.report_nm, sd.content_type,
               sd.raw_content, sd.doc_hash, sd.storage_uri, sd.content_length,
               sd.compressed_length, sd.storage_status
        FROM source_documents sd
        LEFT JOIN companies c ON c.corp_code = sd.corp_code
        {where}
        GROUP BY sd.id
        ORDER BY sd.bsns_year, sd.source_type, sd.corp_code, sd.rcept_no, sd.id
    """)
    section_stmt = text(f"""
        SELECT corp_code, bsns_year, rcept_no, source_type, section_key
        FROM report_sections
        WHERE source_type IN ('business_report', 'audit_report')
          {"AND bsns_year=:year" if year is not None else ""}
          {"AND source_type=:source_type" if source_type is not None else ""}
    """)
    chapter_stmt = text(f"""
        SELECT corp_code, bsns_year, rcept_no, source_type, fs_div, note_title, section_type, body
        FROM accounting_note_chapters
        WHERE source_type IN ('business_report', 'audit_report')
          {"AND bsns_year=:year" if year is not None else ""}
          {"AND source_type=:source_type" if source_type is not None else ""}
    """)
    evidence_stmt = text(f"""
        SELECT corp_code, bsns_year, rcept_no, source_type
        FROM evidence_documents
        WHERE source_type IN ('business_report', 'audit_report')
          {"AND bsns_year=:year" if year is not None else ""}
          {"AND source_type=:source_type" if source_type is not None else ""}
    """)
    with active_engine.connect() as connection:
        sources = [dict(row) for row in connection.execute(source_stmt, params).mappings().all()]
        sections = [dict(row) for row in connection.execute(section_stmt, params).mappings().all()]
        chapters = [dict(row) for row in connection.execute(chapter_stmt, params).mappings().all()]
        evidence = [dict(row) for row in connection.execute(evidence_stmt, params).mappings().all()]

    section_keys: dict[tuple[str, int, str, str], set[str]] = defaultdict(set)
    for row in sections:
        section_keys[_source_key(row)].add(str(row["section_key"]))
    chapter_topics: dict[tuple[str, int, str, str], set[tuple[str, str]]] = defaultdict(set)
    for row in chapters:
        topic = _chapter_topic(row)
        if topic in NOTE_TOPICS:
            chapter_topics[_source_key(row)].add((str(row["fs_div"]), topic))
    evidence_keys = {_source_key(row) for row in evidence}

    raw_counts: Counter[str] = Counter()
    parser_counts: Counter[str] = Counter()
    note_counts: dict[str, Counter[str]] = {topic: Counter() for topic in NOTE_TOPICS}
    note_breakdown: dict[tuple[str, str, str], Counter[str]] = defaultdict(Counter)
    section_counts: dict[str, Counter[str]] = {
        name: Counter()
        for requirements in REQUIRED_REPORT_SECTIONS.values()
        for name in requirements
    }
    section_breakdown: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    evidence_counts: Counter[str] = Counter()
    rows: list[dict] = []

    for source in sources:
        receipt_key = _source_key(source)
        raw_status = _raw_status(source)
        raw_counts[raw_status] += 1
        parser_status: str | None = None
        if raw_status == "inline_readable":
            parsed = parse_note_source_document(source, str(source["raw_content"]))
            parser_status = str(parsed["status"])
            parser_counts[parser_status] += 1
        present_topics = chapter_topics[receipt_key]
        parsed_candidates: set[tuple[str, str]] = set()
        missing_topics: list[str] = []
        if raw_status == "inline_readable" and parser_status == "available":
            for chapter in parsed["chapters"]:
                for topic in chapter["topics"]:
                    if topic in NOTE_TOPICS:
                        parsed_candidates.add((str(chapter["fs_div"]), topic))
        for fs_div, topic in sorted(parsed_candidates):
            status = "available" if (fs_div, topic) in present_topics else "missing_derived"
            note_counts[topic][status] += 1
            note_breakdown[(str(source["source_type"]), fs_div, topic)][status] += 1
            if status == "missing_derived":
                missing_topics.append(topic)
        # Existing derived rows are useful coverage even if the corresponding
        # raw document is no longer inline; do not turn them into a synthetic
        # missing-topic matrix.
        for fs_div, topic in sorted(present_topics - parsed_candidates):
            note_counts[topic]["available"] += 1
            note_breakdown[(str(source["source_type"]), fs_div, topic)]["available"] += 1

        present_keys = section_keys[receipt_key]
        unverified_sections: list[str] = []
        for name, aliases in REQUIRED_REPORT_SECTIONS[str(source["source_type"])].items():
            status = "available" if present_keys.intersection(aliases) else "unverified"
            section_counts[name][status] += 1
            section_breakdown[(str(source["source_type"]), name)][status] += 1
            if status == "unverified":
                unverified_sections.append(name)

        evidence_status = "available" if receipt_key in evidence_keys else "missing_derived"
        evidence_counts[evidence_status] += 1
        rows.append({
            "corp_code": source["corp_code"],
            "corp_name": source["corp_name"],
            "bsns_year": source["bsns_year"],
            "source_type": source["source_type"],
            "fs_divs": sorted({fs_div for fs_div, _topic in present_topics | parsed_candidates}),
            "rcept_no": source["rcept_no"],
            "report_nm": source["report_nm"],
            "raw_status": raw_status,
            "parser_status": parser_status,
            "parsed_note_candidates": [
                {"fs_div": fs_div, "topic": topic}
                for fs_div, topic in sorted(parsed_candidates)
            ],
            "present_note_topics": [
                {"fs_div": fs_div, "topic": topic}
                for fs_div, topic in sorted(present_topics)
            ],
            "missing_note_topics": sorted(missing_topics),
            "present_report_section_keys": sorted(present_keys),
            "unverified_report_sections": sorted(unverified_sections),
            "evidence_document_status": evidence_status,
        })

    total = len(rows)
    page = rows[company_offset:company_offset + company_limit]
    return {
        "mode": "read_only_extraction_gap_audit",
        "parser_version": PARSER_VERSION,
        "scope": {
            "year": year,
            "source_type": source_type,
            "source_tables": [
                "source_documents", "report_sections", "accounting_note_chapters", "evidence_documents",
            ],
            "external_raw_access": "not_attempted",
        },
        "parser_coverage": {
            "source_documents": total,
            "raw_availability": {
                "inline_readable": int(raw_counts["inline_readable"]),
                "external_uri_unverified": int(raw_counts["external_raw_unverified"]),
                "unavailable": int(raw_counts["raw_unavailable"]),
            },
            "inline_note_parser": {
                "attempted": int(raw_counts["inline_readable"]),
                "available": int(parser_counts["available"]),
                "malformed": int(parser_counts["malformed"]),
                "unavailable": int(parser_counts["unavailable"]),
            },
            "derived_rows": {
                "report_sections": len(sections),
                "accounting_note_chapters": len(chapters),
                "evidence_documents": len(evidence),
            },
        },
        "note_topic_gaps": {
            topic: _counter_payload(note_counts[topic], _NOTE_GAP_STATUSES)
            for topic in NOTE_TOPICS
        },
        "note_topic_gap_breakdown": [
            {
                "source_type": source,
                "fs_div": fs_div,
                "topic": topic,
                **_counter_payload(counts, _NOTE_GAP_STATUSES),
            }
            for (source, fs_div, topic), counts in sorted(note_breakdown.items())
        ],
        "report_section_gaps": {
            name: _counter_payload(section_counts[name], _SECTION_GAP_STATUSES)
            for name in sorted(section_counts)
        },
        "report_section_gap_breakdown": [
            {
                "source_type": source,
                "section": section,
                **_counter_payload(counts, _SECTION_GAP_STATUSES),
            }
            for (source, section), counts in sorted(section_breakdown.items())
        ],
        "evidence_document_gaps": {
            "available": int(evidence_counts["available"]),
            "missing_derived": int(evidence_counts["missing_derived"]),
        },
        "company_year_source_page": {
            "offset": company_offset,
            "limit": company_limit,
            "total_source_documents": total,
            "has_more": company_offset + len(page) < total,
            "rows": page,
        },
        "write_boundary": {
            "writes_performed": False,
            "database_writes": "none; SELECT-only connections to four retained tables",
            "raw_storage_reads": "none; storage_uri is classified only as external_raw_unverified",
            "network_calls": "none",
            "manifest_or_cache_writes": "none",
            "later_remediation": "requires explicit scoped approval after this audit",
        },
    }
