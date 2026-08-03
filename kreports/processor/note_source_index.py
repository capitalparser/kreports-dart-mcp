"""Read-only, provenance-preserving accounting-note source indexing.

This module deliberately creates no ``AccountingNoteChapter`` rows.  It is the
dry-run boundary for a later, explicitly approved backfill: callers can inspect
the deterministic candidates and coverage before any retained database is
mutated.
"""
from __future__ import annotations

import html
import hashlib
import os
import re
from collections import Counter
from collections.abc import Callable

from sqlalchemy import text

import kreports.db.engine as _engine_module
from kreports.storage.raw_documents import RawDocumentStore


NOTE_TOPICS = (
    "leases",
    "financial_instruments",
    "related_parties",
    "impairment",
    "provisions_contingencies",
    "subsidiaries",
    "subsequent_events",
    "accounting_policies",
)
PARSER_VERSION = "note-source-index-v1"

_HEADING_ELEMENT_RE = re.compile(
    r"<(?P<tag>p|h[1-6]|title)\b[^>]*>(?P<inner>.*?)</(?P=tag)\s*>",
    re.IGNORECASE | re.DOTALL,
)
_NUMBERED_HEADING_RE = re.compile(
    r"^\s*(?:주석\s*)?(?P<number>\d{1,3})\s*[\.．\)]\s*(?P<title>[^\n]{1,200}?)\s*$",
    re.IGNORECASE,
)
_TOPIC_KEYWORDS = {
    "financial_instruments": ("금융상품", "금융자산", "금융부채", "금융위험"),
    "related_parties": ("특수관계자", "관계기업과의 거래"),
    "impairment": ("손상", "손상차손", "손상검사"),
    "provisions_contingencies": ("충당부채", "우발부채", "우발상황", "우발채무"),
    "subsidiaries": ("종속기업", "연결대상", "연결범위"),
    "subsequent_events": ("보고기간후", "후발사건", "후속사건"),
    "accounting_policies": ("회계정책", "회계처리방침", "재무제표 작성기준"),
}
_LEASE_STANDALONE_RE = re.compile(r"(?<![가-힣])리\s*스(?![가-힣])")
_LEASE_COMPOUND_KEYWORDS = ("사용권자산", "리스부채", "리스계약", "운용리스", "금융리스")


def _plain_text(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", value or "")
    return " ".join(html.unescape(without_tags).split())


def _heading_hits(content: str) -> list[tuple[int, int, str, str]]:
    """Return monotonically ordered numbered heading spans from XML or HTML."""
    hits: list[tuple[int, int, str, str]] = []
    for match in _HEADING_ELEMENT_RE.finditer(content):
        heading = _plain_text(match.group("inner"))
        numbered = _NUMBERED_HEADING_RE.match(heading)
        if numbered is None:
            continue
        hits.append((
            match.start(),
            match.end(),
            numbered.group("number"),
            numbered.group("title").strip(),
        ))
    if hits:
        return hits

    # Some recovered legacy bodies are plain text rather than tag-complete XML.
    for match in re.finditer(
        r"(?m)^\s*(?:주석\s*)?(\d{1,3})\s*[\.．\)]\s*([^\n]{1,200})$",
        content,
    ):
        hits.append((match.start(), match.end(), match.group(1), match.group(2).strip()))
    return hits


def _fs_div_for_offset(content: str, offset: int, source_type: str | None) -> str:
    marker_text = re.sub(r"\s+", "", _plain_text(content[:offset]))
    cfs_marker = marker_text.rfind("연결재무제표주석")
    ofs_marker = marker_text.rfind("별도재무제표주석")
    if cfs_marker < 0 and ofs_marker < 0:
        return "OFS" if source_type == "audit_report" else "CFS"
    return "CFS" if cfs_marker > ofs_marker else "OFS"


def _topics_for(value: str) -> list[str]:
    compact = re.sub(r"\s+", "", value)
    topics = []
    if (
        _LEASE_STANDALONE_RE.search(value)
        or any(keyword in compact for keyword in _LEASE_COMPOUND_KEYWORDS)
    ):
        topics.append("leases")
    topics.extend(
        topic
        for topic in NOTE_TOPICS
        if topic != "leases" and any(
            keyword in compact for keyword in _TOPIC_KEYWORDS[topic]
        )
    )
    return topics


def _section_type(topics: list[str]) -> str:
    return "policy" if "accounting_policies" in topics else "other_note"


def parse_note_source_document(metadata: dict, content: str | None) -> dict:
    """Parse one cached source body without writes or network calls."""
    raw_content = content or ""
    if not raw_content.strip():
        return {
            "status": "unavailable",
            "chapters": [],
            "parser_version": PARSER_VERSION,
        }
    headings = _heading_hits(raw_content)
    if not headings:
        return {"status": "malformed", "chapters": [], "parser_version": PARSER_VERSION}

    chapters: list[dict] = []
    for index, (start, _heading_end, note_no, note_title) in enumerate(headings):
        end = headings[index + 1][0] if index + 1 < len(headings) else len(raw_content)
        raw_body = raw_content[start:end].strip()
        body_text = _plain_text(raw_body)
        topics = _topics_for(f"{note_title} {body_text}")
        if not topics or not body_text:
            continue
        chapters.append({
            "source_document_id": metadata.get("id"),
            "corp_code": metadata.get("corp_code"),
            "bsns_year": metadata.get("bsns_year"),
            "source_type": metadata.get("source_type"),
            "rcept_no": metadata.get("rcept_no"),
            "dcm_no": metadata.get("dcm_no"),
            "fs_div": _fs_div_for_offset(
                raw_content, start, metadata.get("source_type")
            ),
            "note_no": note_no,
            "note_title": note_title,
            "section_type": _section_type(topics),
            "topics": topics,
            "raw_body": raw_body,
            "raw_start": start,
            "raw_end": end,
            "raw_span_locator": (
                f"source_documents:{metadata.get('id')}#chars={start}-{end}"
            ),
            "raw_body_hash": hashlib.sha1(raw_body.encode("utf-8")).hexdigest(),
            "body_text": body_text,
            "full_text_uri": metadata.get("storage_uri"),
            "full_text_hash": metadata.get("doc_hash"),
            "full_text_length": metadata.get("content_length") or len(raw_content),
            "full_text_compressed_length": metadata.get("compressed_length"),
            "full_text_storage_status": metadata.get("storage_status") or "inline",
            "parser_version": PARSER_VERSION,
        })
    return {
        "status": "available" if chapters else "malformed",
        "chapters": chapters,
        "parser_version": PARSER_VERSION,
    }


def _default_content_loader(row: dict) -> str:
    if str(row.get("raw_content") or "").strip():
        return str(row["raw_content"])
    storage_uri = row.get("storage_uri")
    if storage_uri:
        return RawDocumentStore().read(storage_uri, expected_hash=row.get("doc_hash"))
    return ""


def build_note_source_index(
    *,
    year: int | None = None,
    source_type: str | None = None,
    limit: int | None = None,
    include_chapters: bool = True,
    _read_engine=None,
    _content_loader: Callable[[dict], str] | None = None,
) -> dict:
    """Produce a deterministic, dry-run coverage report over cached source rows.

    ``AccountingNoteChapter`` persistence is intentionally out of scope.  A
    later backfill must explicitly map the returned ``raw_body`` candidates to
    storage and write policy under a separately approved write boundary.
    """
    if source_type is not None and source_type not in {"business_report", "audit_report"}:
        raise ValueError("source_type must be business_report or audit_report")
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")

    stmt = """
        SELECT id, rcept_no, dcm_no, corp_code, bsns_year, source_type, report_nm,
               content_type, raw_content, doc_hash, storage_uri, content_length,
               compressed_length, storage_status
        FROM source_documents
        WHERE source_type IN ('business_report', 'audit_report')
          AND content_type != 'derived_report_sections'
    """
    params: dict[str, object] = {}
    if year is not None:
        stmt += " AND bsns_year=:year"
        params["year"] = year
    if source_type is not None:
        stmt += " AND source_type=:source_type"
        params["source_type"] = source_type
    stmt += " ORDER BY bsns_year, source_type, rcept_no"
    if limit is not None:
        stmt += " LIMIT :limit"
        params["limit"] = limit

    active_engine = _read_engine or _engine_module.engine
    with active_engine.connect() as connection:
        rows = [dict(row) for row in connection.execute(text(stmt), params).mappings().all()]

    loader = _content_loader or _default_content_loader
    documents = Counter()
    topic_coverage = Counter()
    chapter_count = 0
    chapters: list[dict] = []
    limitations: list[str] = []
    coverage = Counter()
    for row in rows:
        documents["scanned"] += 1
        coverage[(row["source_type"], row.get("storage_status") or "inline")] += 1
        try:
            content = _default_content_loader(row) if str(row.get("raw_content") or "").strip() else loader(row)
        except Exception:
            documents["unavailable"] += 1
            limitations.append(f"raw_content_unavailable:{row['rcept_no']}")
            continue
        parsed = parse_note_source_document(row, content)
        status = parsed["status"]
        documents[status] += 1
        if status == "malformed":
            limitations.append(f"malformed_note_headings:{row['rcept_no']}")
        elif status == "unavailable":
            limitations.append(f"raw_content_unavailable:{row['rcept_no']}")
        for chapter in parsed["chapters"]:
            chapter_count += 1
            topic_coverage.update(chapter["topics"])
            if include_chapters:
                chapters.append(chapter)

    return {
        "mode": "dry_run_read_only",
        "parser_version": PARSER_VERSION,
        "write_boundary": {
            "writes_performed": False,
            "later_backfill_required": True,
            "target_table": "accounting_note_chapters",
            "raw_body_mapping": "candidate.raw_body requires separately approved materialization",
        },
        "documents": {
            key: documents[key]
            for key in ("scanned", "available", "summary_only", "malformed", "unavailable")
        },
        "coverage": [
            {"source_type": src_type, "storage_status": storage_status, "documents": count}
            for (src_type, storage_status), count in sorted(coverage.items())
        ],
        "topic_coverage": dict(sorted(topic_coverage.items())),
        "chapter_count": chapter_count,
        "chapters": chapters,
        "limitations": limitations,
    }


def _source_where(
    *,
    year: int | None,
    source_type: str | None,
) -> tuple[str, dict[str, object]]:
    if source_type is not None and source_type not in {"business_report", "audit_report"}:
        raise ValueError("source_type must be business_report or audit_report")
    where = """
        WHERE sd.source_type IN ('business_report', 'audit_report')
          AND sd.content_type != 'derived_report_sections'
    """
    params: dict[str, object] = {}
    if year is not None:
        where += " AND sd.bsns_year=:year"
        params["year"] = year
    if source_type is not None:
        where += " AND sd.source_type=:source_type"
        params["source_type"] = source_type
    return where, params


def _free_space_kb() -> int:
    stats = os.statvfs(".")
    return int(stats.f_bavail * stats.f_frsize // 1024)


def build_note_source_inventory(
    *,
    year: int | None = None,
    source_type: str | None = None,
    company_offset: int = 0,
    company_limit: int = 500,
    _read_engine=None,
    _free_kb: int | None = None,
) -> dict:
    """Inventory all cached note sources without reading external objects or writing.

    Inline raw bodies are parsed only to establish exact candidate topics.  A
    ``gs://``/``file://`` reference with no inline raw remains explicitly
    unverified: this all-company plan never turns URI metadata into a claim
    about a filing's note coverage.
    """
    if company_offset < 0:
        raise ValueError("company_offset must be zero or greater")
    if not 1 <= company_limit <= 1_000:
        raise ValueError("company_limit must be between 1 and 1000")

    where, params = _source_where(year=year, source_type=source_type)
    active_engine = _read_engine or _engine_module.engine
    summary_stmt = text(f"""
        SELECT
            COUNT(*) AS documents,
            COALESCE(SUM(CASE WHEN sd.raw_content IS NOT NULL AND sd.raw_content != '' THEN 1 ELSE 0 END), 0) AS inline_readable,
            COALESCE(SUM(CASE
                WHEN (sd.raw_content IS NULL OR sd.raw_content = '')
                 AND sd.storage_uri IS NOT NULL AND sd.storage_uri != '' THEN 1 ELSE 0 END), 0) AS external_uri_unverified,
            COALESCE(SUM(CASE
                WHEN (sd.raw_content IS NULL OR sd.raw_content = '')
                 AND (sd.storage_uri IS NULL OR sd.storage_uri = '') THEN 1 ELSE 0 END), 0) AS unavailable,
            COALESCE(SUM(CASE WHEN sd.raw_content IS NOT NULL AND sd.raw_content != ''
                THEN COALESCE(sd.content_length, length(sd.raw_content), 0) ELSE 0 END), 0) AS inline_bytes,
            COALESCE(SUM(CASE
                WHEN (sd.raw_content IS NULL OR sd.raw_content = '')
                 AND sd.storage_uri IS NOT NULL AND sd.storage_uri != ''
                THEN COALESCE(sd.content_length, 0) ELSE 0 END), 0) AS external_bytes
        FROM source_documents sd
        {where}
    """)
    group_count_stmt = text(f"""
        SELECT COUNT(*) FROM (
            SELECT sd.bsns_year, sd.source_type, sd.corp_code
            FROM source_documents sd
            {where}
            GROUP BY sd.bsns_year, sd.source_type, sd.corp_code
        )
    """)
    year_source_stmt = text(f"""
        SELECT sd.bsns_year, sd.source_type, COUNT(*) AS documents,
               COALESCE(SUM(CASE WHEN sd.raw_content IS NOT NULL AND sd.raw_content != '' THEN 1 ELSE 0 END), 0) AS inline_readable,
               COALESCE(SUM(CASE
                   WHEN (sd.raw_content IS NULL OR sd.raw_content = '')
                    AND sd.storage_uri IS NOT NULL AND sd.storage_uri != '' THEN 1 ELSE 0 END), 0) AS external_uri_unverified,
               COALESCE(SUM(CASE
                   WHEN (sd.raw_content IS NULL OR sd.raw_content = '')
                    AND (sd.storage_uri IS NULL OR sd.storage_uri = '') THEN 1 ELSE 0 END), 0) AS unavailable,
               COALESCE(SUM(CASE
                   WHEN sd.raw_content IS NOT NULL AND sd.raw_content != ''
                     OR ((sd.raw_content IS NULL OR sd.raw_content = '')
                         AND sd.storage_uri IS NOT NULL AND sd.storage_uri != '')
                   THEN COALESCE(sd.content_length, 0) ELSE 0 END), 0) AS estimated_raw_bytes
        FROM source_documents sd
        {where}
        GROUP BY sd.bsns_year, sd.source_type
        ORDER BY sd.bsns_year, sd.source_type
    """)
    group_page_stmt = text(f"""
        SELECT sd.bsns_year, sd.source_type, sd.corp_code, MAX(c.corp_name) AS corp_name,
               COUNT(*) AS documents,
               COALESCE(SUM(CASE WHEN sd.raw_content IS NOT NULL AND sd.raw_content != '' THEN 1 ELSE 0 END), 0) AS inline_readable,
               COALESCE(SUM(CASE
                   WHEN (sd.raw_content IS NULL OR sd.raw_content = '')
                    AND sd.storage_uri IS NOT NULL AND sd.storage_uri != '' THEN 1 ELSE 0 END), 0) AS external_uri_unverified,
               COALESCE(SUM(CASE
                   WHEN (sd.raw_content IS NULL OR sd.raw_content = '')
                    AND (sd.storage_uri IS NULL OR sd.storage_uri = '') THEN 1 ELSE 0 END), 0) AS unavailable,
               COALESCE(SUM(CASE
                   WHEN sd.raw_content IS NOT NULL AND sd.raw_content != ''
                     OR ((sd.raw_content IS NULL OR sd.raw_content = '')
                         AND sd.storage_uri IS NOT NULL AND sd.storage_uri != '')
                   THEN COALESCE(sd.content_length, 0) ELSE 0 END), 0) AS estimated_raw_bytes
        FROM source_documents sd
        LEFT JOIN companies c ON c.corp_code = sd.corp_code
        {where}
        GROUP BY sd.bsns_year, sd.source_type, sd.corp_code
        ORDER BY sd.bsns_year, sd.source_type, sd.corp_code
        LIMIT :company_limit OFFSET :company_offset
    """)
    inline_stmt = text(f"""
        SELECT sd.id, sd.rcept_no, sd.dcm_no, sd.corp_code, sd.bsns_year, sd.source_type,
               sd.content_type, sd.raw_content, sd.doc_hash, sd.storage_uri,
               sd.content_length, sd.compressed_length, sd.storage_status
        FROM source_documents sd
        {where}
          AND sd.raw_content IS NOT NULL AND sd.raw_content != ''
        ORDER BY sd.bsns_year, sd.source_type, sd.corp_code, sd.rcept_no, sd.id
    """)
    page_params = {**params, "company_limit": company_limit, "company_offset": company_offset}
    with active_engine.connect() as connection:
        summary = dict(connection.execute(summary_stmt, params).mappings().one())
        total_groups = int(connection.execute(group_count_stmt, params).scalar_one())
        year_source_rows = [
            dict(row) for row in connection.execute(year_source_stmt, params).mappings().all()
        ]
        page_rows = [dict(row) for row in connection.execute(group_page_stmt, page_params).mappings().all()]
        inline_rows = [dict(row) for row in connection.execute(inline_stmt, params).mappings().all()]

    inline_topics = Counter()
    inline_candidate_documents = 0
    inline_candidate_rows = 0
    for row in inline_rows:
        parsed = parse_note_source_document(row, row["raw_content"])
        if parsed["status"] == "available":
            inline_candidate_documents += 1
        for chapter in parsed["chapters"]:
            inline_candidate_rows += 1
            inline_topics.update(chapter["topics"])

    external_documents = int(summary["external_uri_unverified"])
    total_estimate = inline_candidate_rows + external_documents * len(NOTE_TOPICS)
    free_kb = _free_space_kb() if _free_kb is None else int(_free_kb)
    minimum_free_kb = int(os.environ.get("KREPORTS_MIN_FREE_KB", "10485760"))
    estimated_write_bytes = total_estimate * 2_000
    return {
        "mode": "dry_run_read_only_inventory",
        "parser_version": PARSER_VERSION,
        "documents": int(summary["documents"]),
        "raw_availability": {
            "inline_readable": int(summary["inline_readable"]),
            "external_uri_unverified": external_documents,
            "unavailable": int(summary["unavailable"]),
        },
        "parser_eligibility": {
            "inline_parse_attempted": int(summary["inline_readable"]),
            "inline_candidate_documents": inline_candidate_documents,
            "external_read_preflight_required": external_documents,
            "unavailable": int(summary["unavailable"]),
        },
        "topic_coverage_candidates": {
            "inline_parsed": dict(sorted(inline_topics.items())),
            "external_unverified_documents": external_documents,
            "not_inferred_from_metadata": True,
        },
        "estimated_note_rows": {
            "inline_parsed_candidates": inline_candidate_rows,
            "external_topic_heuristic": external_documents * len(NOTE_TOPICS),
            "total_planning_estimate": total_estimate,
            "external_estimate_method": "eight supported topics per external document; verify raw before write",
        },
        "estimated_raw_bytes": {
            "inline": int(summary["inline_bytes"]),
            "external": int(summary["external_bytes"]),
            "total": int(summary["inline_bytes"] + summary["external_bytes"]),
        },
        "year_source_counts": year_source_rows,
        "company_year_source_page": {
            "offset": company_offset,
            "limit": company_limit,
            "total_groups": total_groups,
            "has_more": company_offset + len(page_rows) < total_groups,
            "rows": page_rows,
        },
        "write_preflight": {
            "writes_performed": False,
            "free_kb": free_kb,
            "minimum_free_kb": minimum_free_kb,
            "estimated_db_write_bytes": estimated_write_bytes,
            "safe_to_write": free_kb >= minimum_free_kb,
            "status": "blocked_disk_preflight" if free_kb < minimum_free_kb else "requires_scope_approval",
        },
        "idempotent_batch_plan": {
            "source_key": ["rcept_no", "source_type", "doc_hash"],
            "target_key": ["corp_code", "bsns_year", "fs_div", "note_no", "section_type"],
            "batch_cursor": ["bsns_year", "source_type", "corp_code", "rcept_no", "id"],
            "write_rule": "upsert only after raw read/hash verification and an approved bounded batch",
        },
        "peer_query_behavior": {
            "index_scope": "all_available_note_chapters_global",
            "cohort_resolution": "query_time_customizable",
            "criteria_input": "peer_criteria",
            "persistence": "no_precomputed_peer_membership",
        },
    }
