"""Read-only, provenance-preserving accounting-note source indexing.

This module deliberately creates no ``AccountingNoteChapter`` rows.  It is the
dry-run boundary for a later, explicitly approved backfill: callers can inspect
the deterministic candidates and coverage before any retained database is
mutated.
"""
from __future__ import annotations

import html
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
_FS_DIV_MARKER_RE = re.compile(
    r"연결\s*재무제표\s*주석|별도\s*재무제표\s*주석",
    re.IGNORECASE,
)
_TOPIC_KEYWORDS = {
    "leases": ("리스", "사용권자산", "리스부채"),
    "financial_instruments": ("금융상품", "금융자산", "금융부채", "금융위험"),
    "related_parties": ("특수관계자", "관계기업과의 거래"),
    "impairment": ("손상", "손상차손", "손상검사"),
    "provisions_contingencies": ("충당부채", "우발부채", "우발상황", "우발채무"),
    "subsidiaries": ("종속기업", "연결대상", "연결범위"),
    "subsequent_events": ("보고기간후", "후발사건", "후속사건"),
    "accounting_policies": ("회계정책", "회계처리방침", "재무제표 작성기준"),
}


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


def _fs_div_for_offset(content: str, offset: int) -> str:
    markers = [match for match in _FS_DIV_MARKER_RE.finditer(content) if match.start() <= offset]
    if not markers:
        return "CFS"
    return "CFS" if "연결" in markers[-1].group(0) else "OFS"


def _topics_for(value: str) -> list[str]:
    return [
        topic
        for topic in NOTE_TOPICS
        if any(keyword in value for keyword in _TOPIC_KEYWORDS[topic])
    ]


def _section_type(topics: list[str]) -> str:
    return "policy" if "accounting_policies" in topics else "other_note"


def parse_note_source_document(metadata: dict, content: str | None) -> dict:
    """Parse one cached source body without writes or network calls."""
    raw_content = content or ""
    if not raw_content.strip():
        return {
            "status": "summary_only" if metadata.get("storage_uri") else "unavailable",
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
            "fs_div": _fs_div_for_offset(raw_content, start),
            "note_no": note_no,
            "note_title": note_title,
            "section_type": _section_type(topics),
            "topics": topics,
            "raw_body": raw_body,
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
            documents["summary_only"] += 1
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
