"""Read-only, side-by-side accounting-note comparison over one peer cohort."""
from __future__ import annotations

import html
import hashlib
import json
import re
import unicodedata

from sqlalchemy import bindparam, text

import kreports.db.engine as _engine_module
from kreports.analysis.peer_benchmarks import select_peer_group
from kreports.processor.semantic_contracts import normalize_note_topic


NOTE_TOPICS = (
    "revenue",
    "leases",
    "financial_instruments",
    "related_parties",
    "provisions_contingencies",
    "impairment",
    "subsidiaries",
    "subsequent_events",
    "accounting_policies",
)
_STANDARD_FS_DIVS = ("CFS", "OFS")
_MAX_RAW_TEXT_OUTPUT_CHARS = 12_000
_MAX_COMPARISON_TEXT_OUTPUT_CHARS = 4_000
MAX_NOTE_COMPARISON_OUTPUT_BYTES = 100_000


def _availability(row: dict) -> str:
    body = str(row.get("body") or "")
    full_length = row.get("full_text_length")
    status = str(row.get("full_text_storage_status") or "").lower()
    if (
        row.get("full_text_uri")
        or status in {"externalized", "truncated", "compressed"}
        or (isinstance(full_length, int) and full_length > len(body))
    ):
        return "summary_only"
    return "available"


def _note_topic(row: dict) -> str:
    if row.get("section_type") == "policy" and "회계정책" in str(row.get("note_title") or ""):
        return "accounting_policies"
    return normalize_note_topic(str(row.get("note_title") or ""), str(row.get("body") or ""))


def _raw_text_format(value: str) -> str:
    has_table = any("|" in line for line in value.splitlines())
    has_html = bool(re.search(r"<[^>\n]+>", value))
    if has_table and has_html:
        return "markdown_table+html"
    if has_table:
        return "markdown_table"
    if has_html:
        return "html"
    return "plain_text"


def _comparison_text(value: str) -> str:
    cleaned = "".join(
        " " if unicodedata.category(char).startswith(("C", "Zl", "Zp")) else char
        for char in value
    )
    return " ".join(cleaned.split())


def _table_safe_display(value: str) -> tuple[str, dict[str, bool]]:
    rendered: list[str] = []
    metadata = {
        "line_breaks_escaped": False,
        "markdown_table_escaped": False,
        "markdown_link_or_image_escaped": False,
        "html_escaped": False,
        "control_characters_escaped": False,
        "unicode_separators_escaped": False,
    }
    for char in value:
        if char == "\n":
            rendered.append("\\n")
            metadata["line_breaks_escaped"] = True
        elif char == "\r":
            rendered.append("\\r")
            metadata["line_breaks_escaped"] = True
        elif char == "\t":
            rendered.append("\\t")
            metadata["control_characters_escaped"] = True
        elif char in {"\u2028", "\u2029"}:
            rendered.append(f"\\u{ord(char):04x}")
            metadata["unicode_separators_escaped"] = True
        elif unicodedata.category(char).startswith("C"):
            rendered.append(f"\\u{ord(char):04x}")
            metadata["control_characters_escaped"] = True
        elif char == "|":
            rendered.append("\\|")
            metadata["markdown_table_escaped"] = True
        elif char == "\\":
            rendered.append("\\\\")
        elif char in "![]()":
            rendered.append(f"\\{char}")
            metadata["markdown_link_or_image_escaped"] = True
        else:
            escaped = html.escape(char, quote=False)
            if escaped != char:
                metadata["html_escaped"] = True
            rendered.append(escaped)
    return "".join(rendered), metadata


def _raw_text_fields(value: object) -> dict:
    raw_text = str(value or "")
    normalized_text = _comparison_text(raw_text)
    raw_text_truncated = len(raw_text) > _MAX_RAW_TEXT_OUTPUT_CHARS
    output_text = raw_text[:_MAX_RAW_TEXT_OUTPUT_CHARS]
    comparison_text_truncated = len(normalized_text) > _MAX_COMPARISON_TEXT_OUTPUT_CHARS
    display_text, rendering = _table_safe_display(output_text)
    result = {
        "raw_text": output_text,
        "raw_text_length": len(raw_text),
        "raw_text_truncated": raw_text_truncated,
        "raw_text_format": _raw_text_format(raw_text),
        "comparison_text": normalized_text[:_MAX_COMPARISON_TEXT_OUTPUT_CHARS],
        "comparison_text_length": len(normalized_text),
        "comparison_text_hash": hashlib.sha256(normalized_text.encode("utf-8")).hexdigest(),
        "comparison_text_truncated": comparison_text_truncated,
        "display": {
            "text": display_text,
            **rendering,
        },
    }
    return result


def _output_bytes(value: dict) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _compact_row_for_output_budget(row: dict) -> None:
    """Keep provenance while removing repeated large text payloads."""
    raw_text = row.get("raw_text")
    if raw_text is not None:
        compact_raw = str(raw_text)[:360]
        compact_comparison = str(row.get("comparison_text") or "")[:240]
        display_text, rendering = _table_safe_display(compact_raw)
        row.update({
            "raw_text": compact_raw,
            "raw_text_truncated": row.get("raw_text_length", 0) > len(compact_raw),
            "comparison_text": compact_comparison,
            "comparison_text_truncated": (
                row.get("comparison_text_length", 0) > len(compact_comparison)
            ),
            "value_or_excerpt": compact_comparison,
            "display": {"text": display_text, **rendering},
        })
    original_evidence_documents = list(row.get("evidence_documents") or [])
    evidence_documents = original_evidence_documents[:3]
    row["evidence_documents"] = [
        {
            "source_locator": str(item.get("source_locator") or "")[:160],
            "availability": str(item.get("availability") or "")[:40],
            "rcept_no": str(item.get("rcept_no") or "")[:80],
            "source_type": str(item.get("source_type") or "")[:80],
            "evidence_scope": str(item.get("evidence_scope") or "")[:120],
            "title": str(item.get("title") or "")[:240],
            "full_text_uri": str(item.get("full_text_uri") or "")[:500],
            "full_text_hash": item.get("full_text_hash"),
            "full_text_length": item.get("full_text_length"),
            "full_text_storage_status": str(item.get("full_text_storage_status") or "")[:80],
        }
        for item in evidence_documents
        if isinstance(item, dict)
    ]
    row["evidence_documents_truncated"] = len(original_evidence_documents) > 3
    row["output_budget_truncated"] = True


def _emergency_budget_result(result: dict) -> dict:
    """Last-resort, deterministic response that remains valid for hostile metadata."""
    topics = []
    for topic_result in result["topics"]:
        subject_row = topic_result["rows"][0] if topic_result["rows"] else None
        topics.append({
            "topic": str(topic_result["topic"])[:120],
            "subject": (
                {
                    "corp_code": str(subject_row["company"]["corp_code"])[:80],
                    "availability": subject_row["availability"],
                    "source_locator": str(subject_row["source_locator"] or "")[:160],
                    "rcept_no": str(subject_row["rcept_no"] or "")[:80],
                    "full_text_hash": subject_row["full_text_hash"],
                }
                if subject_row is not None
                else None
            ),
            "omitted_peer_rows": max(0, len(topic_result["rows"]) - 1)
            + topic_result.get("omitted_peer_rows", 0),
        })
    compact = {
        "subject": {"corp_code": str((result.get("subject") or {}).get("corp_code") or "")[:80]},
        "year": result.get("year"),
        "pagination": result["pagination"],
        "truncation": {
            "applied": True,
            "reason": "note_comparison_output_budget",
            "output_budget_applied": True,
            "max_output_bytes": MAX_NOTE_COMPARISON_OUTPUT_BYTES,
        },
        "topics": topics,
        "read_only": True,
        "limitations": ["note_comparison_output_truncated"],
    }
    compact["truncation"]["output_bytes"] = _output_bytes(compact)
    return compact


def _refresh_coverage_matrix(result: dict) -> None:
    matrix_topics: list[dict] = []
    for topic_result in result["topics"]:
        cells = [
            {
                "corp_code": row["company"]["corp_code"],
                "availability": row["availability"],
                "source_locator": row["source_locator"],
                "rcept_no": row["rcept_no"],
                "full_text_hash": row["full_text_hash"],
                "fs_div_selection": row["fs_div_selection"],
            }
            for row in topic_result["rows"]
        ]
        matrix_topics.append({
            "topic": topic_result["topic"],
            "coverage": {
                status: sum(cell["availability"] == status for cell in cells)
                for status in ("available", "summary_only", "unavailable")
            },
            "cells": cells,
            "omitted_peer_rows": topic_result.get("omitted_peer_rows", 0),
        })
    result["coverage_matrix"]["topics"] = matrix_topics


def _apply_output_budget(result: dict) -> dict:
    truncation = result["truncation"]
    truncation.update({
        "output_budget_applied": False,
        "max_output_bytes": MAX_NOTE_COMPARISON_OUTPUT_BYTES,
        "output_bytes": 0,
    })
    truncation["output_bytes"] = _output_bytes(result)
    if _output_bytes(result) <= MAX_NOTE_COMPARISON_OUTPUT_BYTES:
        return result

    for topic_result in result["topics"]:
        for row in topic_result["rows"]:
            _compact_row_for_output_budget(row)
    truncation["applied"] = True
    if truncation["reason"] is None:
        truncation["reason"] = "note_comparison_output_budget"
    truncation["output_budget_applied"] = True
    truncation["output_budget_reason"] = "note_comparison_output_budget"
    _refresh_coverage_matrix(result)

    while _output_bytes(result) > MAX_NOTE_COMPARISON_OUTPUT_BYTES:
        candidates = [topic for topic in result["topics"] if len(topic["rows"]) > 1]
        if not candidates:
            break
        topic_result = max(candidates, key=lambda item: len(item["rows"]))
        omitted = topic_result["rows"].pop()
        topic_result["omitted_peer_rows"] = topic_result.get("omitted_peer_rows", 0) + 1
        peer_code = omitted["company"]["corp_code"]
        topic_result["differences"] = [
            item for item in topic_result["differences"]
            if item["peer_corp_code"] != peer_code
        ]
        result["differences"] = [
            item for item in result["differences"]
            if not (
                item["topic"] == topic_result["topic"]
                and item["peer_corp_code"] == peer_code
            )
        ]
    _refresh_coverage_matrix(result)
    truncation["output_bytes"] = _output_bytes(result)
    while (
        _output_bytes(result) > MAX_NOTE_COMPARISON_OUTPUT_BYTES
        and result["cohort"]["peers"]
    ):
        result["cohort"]["peers"].pop()
        result["coverage_matrix"]["companies"].pop()
        truncation["cohort_metadata_omitted"] = truncation.get("cohort_metadata_omitted", 0) + 1
        truncation["output_bytes"] = _output_bytes(result)
    return (
        result
        if _output_bytes(result) <= MAX_NOTE_COMPARISON_OUTPUT_BYTES
        else _emergency_budget_result(result)
    )


def _select_note_row(
    rows: list[dict],
    requested_fs_div: str | None,
) -> tuple[dict, dict] | tuple[None, dict]:
    """Select one note row without mixing CFS/OFS implicitly."""
    if not rows:
        return None, {
            "requested": requested_fs_div,
            "used": None,
            "status": "unavailable_no_cached_note",
        }
    by_fs_div: dict[str, list[dict]] = {}
    for row in rows:
        fs_div = str(row.get("fs_div") or "unknown")
        by_fs_div.setdefault(fs_div, []).append(row)
    if requested_fs_div and by_fs_div.get(requested_fs_div):
        return by_fs_div[requested_fs_div][0], {
            "requested": requested_fs_div,
            "used": requested_fs_div,
            "status": "exact",
        }
    fallback_order = [
        fs_div for fs_div in _STANDARD_FS_DIVS if fs_div in by_fs_div
    ] + sorted(fs_div for fs_div in by_fs_div if fs_div not in _STANDARD_FS_DIVS)
    used_fs_div = fallback_order[0]
    return by_fs_div[used_fs_div][0], {
        "requested": requested_fs_div,
        "used": used_fs_div,
        "status": (
            "fallback_requested_fs_div_unavailable"
            if requested_fs_div
            else "fallback_no_cohort_fs_div"
        ),
    }


def _resolve_peer_group(
    company: str,
    year: int,
    peer_limit: int,
    fs_strategy: str,
    peer_criteria: list[str] | dict | None,
) -> dict:
    return select_peer_group(
        company,
        criteria=peer_criteria,
        peer_limit=peer_limit,
        fs_strategy=fs_strategy,
        year=year,
    )


def compare_peer_accounting_notes(
    company: str,
    year: int,
    *,
    topics: list[str] | None = None,
    peer_limit: int = 30,
    peer_offset: int = 0,
    page_size: int | None = None,
    fs_strategy: str = "auto",
    peer_criteria: list[str] | dict | None = None,
    _peer_group: dict | None = None,
    _read_engine=None,
) -> dict:
    """Compare cached note excerpts for one exact business year only."""
    if not 1 <= peer_limit <= 200:
        raise ValueError("peer_limit must be between 1 and 200")
    if peer_offset < 0:
        raise ValueError("peer_offset must be zero or greater")
    resolved_page_size = page_size if page_size is not None else peer_limit
    if not 1 <= resolved_page_size <= 200:
        raise ValueError("page_size must be between 1 and 200")
    requested_topics = list(dict.fromkeys(topics or NOTE_TOPICS))
    unknown = set(requested_topics) - set(NOTE_TOPICS)
    if unknown:
        raise ValueError(f"unsupported note topics: {sorted(unknown)}")
    peer_group = _peer_group or _resolve_peer_group(
        company,
        year,
        peer_offset + resolved_page_size,
        fs_strategy,
        peer_criteria,
    )
    if "error" in peer_group:
        return peer_group
    peer_selection = dict(peer_group.get("selection_policy") or {})
    cohort_fs_div = peer_selection.get("fs_div_used")
    requested_fs_div = cohort_fs_div if cohort_fs_div in _STANDARD_FS_DIVS else None
    subject = dict(peer_group.get("subject") or {})
    subject_code = subject.get("corp_code")
    if not subject_code:
        return {"error": "peer_subject_unavailable"}
    all_peer_rows = [
        dict(row) for row in (peer_group.get("peers") or [])
        if isinstance(row, dict) and row.get("corp_code")
    ]
    peer_rows = all_peer_rows[peer_offset:peer_offset + resolved_page_size]
    codes = [str(subject_code)] + [str(row["corp_code"]) for row in peer_rows if row.get("corp_code")]
    names = {str(subject_code): subject.get("corp_name")}
    names.update({str(row["corp_code"]): row.get("corp_name") for row in peer_rows if row.get("corp_code")})
    active_engine = _read_engine or _engine_module.engine
    note_stmt = text(
        """
        SELECT anc.id, anc.corp_code, anc.rcept_no, anc.dcm_no, anc.fs_div,
               anc.note_no, anc.note_title, anc.section_type, anc.body,
               anc.full_text_uri, anc.full_text_hash, anc.full_text_length,
               anc.full_text_compressed_length, anc.full_text_storage_status,
               sd.id AS source_document_id
        FROM accounting_note_chapters anc
        LEFT JOIN source_documents sd
          ON sd.rcept_no=anc.rcept_no AND sd.source_type=anc.source_type
        WHERE anc.corp_code IN :corp_codes AND anc.bsns_year=:year
        ORDER BY anc.corp_code, anc.fs_div, anc.note_no, anc.id
        """
    ).bindparams(bindparam("corp_codes", expanding=True))
    evidence_stmt = text(
        """
        SELECT id, corp_code, rcept_no, source_type, evidence_scope, title,
               full_text_uri, full_text_hash, full_text_length, full_text_storage_status
        FROM evidence_documents
        WHERE corp_code IN :corp_codes AND bsns_year=:year
        ORDER BY corp_code, id
        """
    ).bindparams(bindparam("corp_codes", expanding=True))
    with active_engine.connect() as conn:
        note_rows = [dict(row) for row in conn.execute(note_stmt, {"corp_codes": codes, "year": year}).mappings().all()]
        evidence_rows = [dict(row) for row in conn.execute(evidence_stmt, {"corp_codes": codes, "year": year}).mappings().all()]
    notes_by_key: dict[tuple[str, str], list[dict]] = {}
    for row in note_rows:
        topic = _note_topic(row)
        if topic not in requested_topics:
            continue
        row["topic"] = topic
        notes_by_key.setdefault((str(row["corp_code"]), topic), []).append(row)
    evidence_by_code: dict[str, list[dict]] = {}
    for row in evidence_rows:
        evidence_by_code.setdefault(str(row["corp_code"]), []).append({
            **row,
            "source_locator": f"evidence_documents:{row['id']}",
            "availability": "summary_only",
        })
    topic_results: list[dict] = []
    differences: list[dict] = []
    for topic in requested_topics:
        comparison_rows: list[dict] = []
        for code in codes:
            rows = notes_by_key.get((code, topic), [])
            row, fs_div_selection = _select_note_row(rows, requested_fs_div)
            if row:
                raw_fields = _raw_text_fields(row["body"])
                comparison_rows.append({
                    "company": {"corp_code": code, "corp_name": names.get(code)},
                    "value_or_excerpt": raw_fields["comparison_text"],
                    **raw_fields,
                    "availability": _availability(row),
                    "source_locator": f"accounting_note_chapters:{row['id']}",
                    "source_document_id": row.get("source_document_id"),
                    "rcept_no": row["rcept_no"],
                    "note_no": row["note_no"],
                    "note_title": row["note_title"],
                    "fs_div": row["fs_div"],
                    "fs_div_selection": fs_div_selection,
                    "full_text_uri": row["full_text_uri"],
                    "full_text_hash": row["full_text_hash"],
                    "full_text_length": row["full_text_length"],
                    "full_text_storage_status": row["full_text_storage_status"],
                    "evidence_documents": evidence_by_code.get(code, []),
                    "comparison_note": "cached_note_present",
                })
            else:
                comparison_rows.append({
                    "company": {"corp_code": code, "corp_name": names.get(code)},
                    "value_or_excerpt": None,
                    "raw_text": None,
                    "raw_text_length": 0,
                    "raw_text_truncated": False,
                    "raw_text_format": "unavailable",
                    "comparison_text": None,
                    "comparison_text_length": 0,
                    "comparison_text_hash": None,
                    "comparison_text_truncated": False,
                    "display": {
                        "text": None,
                        "line_breaks_escaped": False,
                        "markdown_table_escaped": False,
                        "markdown_link_or_image_escaped": False,
                        "html_escaped": False,
                        "control_characters_escaped": False,
                        "unicode_separators_escaped": False,
                    },
                    "availability": "unavailable",
                    "source_locator": None,
                    "source_document_id": None,
                    "rcept_no": None,
                    "full_text_hash": None,
                    "fs_div_selection": fs_div_selection,
                    "evidence_documents": evidence_by_code.get(code, []),
                    "comparison_note": "no_cached_note_for_exact_business_year",
                })
        subject_row = comparison_rows[0]
        for peer_row in comparison_rows[1:]:
            if (
                subject_row["comparison_text_hash"] is not None
                and peer_row["comparison_text_hash"] is not None
                and (
                    subject_row["comparison_text_hash"], subject_row["comparison_text_length"]
                ) != (
                    peer_row["comparison_text_hash"], peer_row["comparison_text_length"]
                )
            ):
                differences.append({
                    "topic": topic,
                    "subject_corp_code": str(subject_code),
                    "peer_corp_code": peer_row["company"]["corp_code"],
                    "status": (
                        "indeterminate_truncated"
                        if (
                            subject_row["comparison_text"] == peer_row["comparison_text"]
                            and (
                                subject_row["comparison_text_truncated"]
                                or peer_row["comparison_text_truncated"]
                            )
                        )
                        else "different_normalized_text"
                    ),
                    "subject_source_locator": subject_row["source_locator"],
                    "peer_source_locator": peer_row["source_locator"],
                })
        topic_results.append({
            "topic": topic,
            "rows": comparison_rows,
            "coverage": sum(row["availability"] != "unavailable" for row in comparison_rows),
            "differences": [
                item for item in differences if item["topic"] == topic
            ],
            "comparison_note": "Text excerpts are filing evidence; difference interpretation is not inferred.",
        })
    coverage_topics = []
    for topic_result in topic_results:
        cells = [
            {
                "corp_code": row["company"]["corp_code"],
                "availability": row["availability"],
                "source_locator": row["source_locator"],
                "rcept_no": row["rcept_no"],
                "full_text_hash": row["full_text_hash"],
                "fs_div_selection": row["fs_div_selection"],
            }
            for row in topic_result["rows"]
        ]
        coverage_topics.append({
            "topic": topic_result["topic"],
            "coverage": {
                status: sum(cell["availability"] == status for cell in cells)
                for status in ("available", "summary_only", "unavailable")
            },
            "cells": cells,
        })
    selector_peer_count = peer_group.get("peer_count")
    total_peer_count = (
        int(selector_peer_count)
        if isinstance(selector_peer_count, int) and selector_peer_count >= 0
        else len(all_peer_rows)
    )
    has_more = peer_offset + len(peer_rows) < total_peer_count
    result = {
        "subject": subject,
        "year": year,
        "peer_selection": peer_selection,
        "selection_policy": peer_selection,
        "peer_confidence": peer_group.get("confidence"),
        "cohort": {
            "subject": subject,
            "peers": [
                {"corp_code": str(row["corp_code"]), "corp_name": row.get("corp_name")}
                for row in peer_rows
            ],
        },
        "coverage_matrix": {
            "companies": [
                {"corp_code": str(subject_code), "corp_name": subject.get("corp_name")},
                *[
                    {"corp_code": str(row["corp_code"]), "corp_name": row.get("corp_name")}
                    for row in peer_rows
                ],
            ],
            "topics": coverage_topics,
        },
        "pagination": {
            "offset": peer_offset,
            "page_size": resolved_page_size,
            "peer_limit": peer_limit,
            "total_peer_count": total_peer_count,
            "available_peer_count": total_peer_count,
            "returned_peer_count": len(peer_rows),
            "has_more": has_more,
            "next_page_token": (
                f"offset:{peer_offset + len(peer_rows)}"
                if has_more and peer_rows
                else None
            ),
        },
        "truncation": {
            "applied": has_more,
            "reason": "peer_pagination" if has_more else None,
            "peer_limit": peer_limit,
            "peer_offset": peer_offset,
            "page_size": resolved_page_size,
        },
        "differences": differences,
        "topics": topic_results,
        "read_only": True,
        "limitations": [
            "Only cached accounting_note_chapters for the exact requested business year are compared.",
            "Rows without the cohort fs_div use the documented CFS, then OFS fallback order and expose fs_div_selection.",
        ],
    }
    return _apply_output_budget(result)
