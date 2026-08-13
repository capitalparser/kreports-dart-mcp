"""KAM lifecycle analysis across multiple years."""
from __future__ import annotations

from difflib import SequenceMatcher

from sqlalchemy import text

from kreports.db.engine import engine
from kreports.processor.audit_report_parser import classify_kam_topics, summarize_kam_body


def _similarity(a: str, b: str) -> float:
    return round(SequenceMatcher(None, a or "", b or "").ratio(), 4)


def kam_lifecycle_for_company(company: str, *, start_year: int, end_year: int) -> dict:
    """Return year-by-year KAM topic continuity and wording-change hints."""
    stmt = text("""
        SELECT rs.corp_code, c.corp_name, rs.bsns_year, rs.rcept_no, rs.dcm_no,
               rs.section_title, rs.body_text, rs.body_length, rs.ordinal
        FROM report_sections rs
        JOIN companies c ON c.corp_code=rs.corp_code
        WHERE rs.corp_code=:corp_code
          AND rs.source_type='audit_report'
          AND rs.section_key='kam'
          AND rs.bsns_year BETWEEN :start_year AND :end_year
        ORDER BY rs.bsns_year, rs.ordinal
    """)
    with engine.connect() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                stmt,
                {
                    "corp_code": company,
                    "start_year": int(start_year),
                    "end_year": int(end_year),
                },
            ).mappings()
        ]

    previous_by_topic: dict[str, dict] = {}
    events: list[dict] = []
    for row in rows:
        body = row.get("body_text") or ""
        topics = classify_kam_topics(body) or ["unknown"]
        summary = summarize_kam_body(body)
        for topic in topics:
            previous = previous_by_topic.get(topic)
            sim = _similarity(previous.get("body_text") or "", body) if previous else None
            status = "new" if previous is None else ("repeated_changed" if sim is not None and sim < 0.9 else "repeated_stable")
            events.append({
                "corp_code": row["corp_code"],
                "corp_name": row.get("corp_name"),
                "year": row["bsns_year"],
                "rcept_no": row["rcept_no"],
                "dcm_no": row.get("dcm_no"),
                "topic": topic,
                "title": row.get("section_title"),
                "status": status,
                "similarity_to_previous": sim,
                "has_reason_hint": summary.get("has_reason_hint"),
                "has_procedure_hint": summary.get("has_procedure_hint"),
                "body_length": row.get("body_length"),
                "body_excerpt": body[:900],
            })
            previous_by_topic[topic] = row
    return {
        "company": company,
        "start_year": int(start_year),
        "end_year": int(end_year),
        "event_count": len(events),
        "events": events,
        "data_quality": {
            "status": "usable" if events else "missing",
            "source": "report_sections.audit_report",
            "interpretation": "KAM lifecycle uses cached audit-report KAM sections; missing events indicate missing local KAM cache, not absence in DART.",
        },
    }
