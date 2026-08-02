"""Read-only, side-by-side accounting-note comparison over one peer cohort."""
from __future__ import annotations

import html
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
        " " if unicodedata.category(char).startswith("C") else char
        for char in value
    )
    return " ".join(cleaned.split())


def _table_safe_display(value: str) -> tuple[str, dict[str, bool]]:
    rendered: list[str] = []
    metadata = {
        "line_breaks_escaped": False,
        "markdown_table_escaped": False,
        "html_escaped": False,
        "control_characters_escaped": False,
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
        elif unicodedata.category(char).startswith("C"):
            rendered.append(f"\\u{ord(char):04x}")
            metadata["control_characters_escaped"] = True
        elif char == "|":
            rendered.append("\\|")
            metadata["markdown_table_escaped"] = True
        elif char == "\\":
            rendered.append("\\\\")
        else:
            escaped = html.escape(char, quote=False)
            if escaped != char:
                metadata["html_escaped"] = True
            rendered.append(escaped)
    return "".join(rendered), metadata


def _raw_text_fields(value: object) -> dict:
    raw_text = str(value or "")
    raw_text_truncated = len(raw_text) > _MAX_RAW_TEXT_OUTPUT_CHARS
    output_text = raw_text[:_MAX_RAW_TEXT_OUTPUT_CHARS]
    display_text, rendering = _table_safe_display(output_text)
    return {
        "raw_text": output_text,
        "raw_text_length": len(raw_text),
        "raw_text_truncated": raw_text_truncated,
        "raw_text_format": _raw_text_format(raw_text),
        "comparison_text": _comparison_text(output_text),
        "display": {
            "text": display_text,
            **rendering,
        },
    }


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
    fs_strategy: str = "auto",
    peer_criteria: list[str] | dict | None = None,
    _peer_group: dict | None = None,
    _read_engine=None,
) -> dict:
    """Compare cached note excerpts for one exact business year only."""
    if not 1 <= peer_limit <= 200:
        raise ValueError("peer_limit must be between 1 and 200")
    requested_topics = list(dict.fromkeys(topics or NOTE_TOPICS))
    unknown = set(requested_topics) - set(NOTE_TOPICS)
    if unknown:
        raise ValueError(f"unsupported note topics: {sorted(unknown)}")
    peer_group = _peer_group or _resolve_peer_group(
        company, year, peer_limit, fs_strategy, peer_criteria
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
    peer_rows = all_peer_rows[:peer_limit]
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
                    "value_or_excerpt": raw_fields["raw_text"],
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
                    "display": {
                        "text": None,
                        "line_breaks_escaped": False,
                        "markdown_table_escaped": False,
                        "html_escaped": False,
                        "control_characters_escaped": False,
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
                subject_row["comparison_text"] is not None
                and peer_row["comparison_text"] is not None
                and subject_row["comparison_text"] != peer_row["comparison_text"]
            ):
                differences.append({
                    "topic": topic,
                    "subject_corp_code": str(subject_code),
                    "peer_corp_code": peer_row["company"]["corp_code"],
                    "status": "different_normalized_text",
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
    truncated = len(all_peer_rows) > len(peer_rows)
    return {
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
            "offset": 0,
            "peer_limit": peer_limit,
            "available_peer_count": len(all_peer_rows),
            "returned_peer_count": len(peer_rows),
            "has_more": truncated,
        },
        "truncation": {
            "applied": truncated,
            "reason": "peer_limit" if truncated else None,
            "peer_limit": peer_limit,
        },
        "differences": differences,
        "topics": topic_results,
        "read_only": True,
        "limitations": [
            "Only cached accounting_note_chapters for the exact requested business year are compared.",
            "Rows without the cohort fs_div use the documented CFS, then OFS fallback order and expose fs_div_selection.",
        ],
    }
