"""Read-only, side-by-side accounting-note comparison over one peer cohort."""
from __future__ import annotations

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
    requested_topics = list(dict.fromkeys(topics or NOTE_TOPICS))
    unknown = set(requested_topics) - set(NOTE_TOPICS)
    if unknown:
        raise ValueError(f"unsupported note topics: {sorted(unknown)}")
    peer_group = _peer_group or _resolve_peer_group(
        company, year, peer_limit, fs_strategy, peer_criteria
    )
    if "error" in peer_group:
        return peer_group
    subject = dict(peer_group.get("subject") or {})
    subject_code = subject.get("corp_code")
    if not subject_code:
        return {"error": "peer_subject_unavailable"}
    peer_rows = peer_group.get("peers") or []
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
    for topic in requested_topics:
        comparison_rows: list[dict] = []
        for code in codes:
            rows = notes_by_key.get((code, topic), [])
            if rows:
                row = rows[0]
                comparison_rows.append({
                    "company": {"corp_code": code, "corp_name": names.get(code)},
                    "value_or_excerpt": row["body"],
                    "availability": _availability(row),
                    "source_locator": f"accounting_note_chapters:{row['id']}",
                    "source_document_id": row.get("source_document_id"),
                    "rcept_no": row["rcept_no"],
                    "note_no": row["note_no"],
                    "note_title": row["note_title"],
                    "fs_div": row["fs_div"],
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
                    "availability": "unavailable",
                    "source_locator": None,
                    "evidence_documents": evidence_by_code.get(code, []),
                    "comparison_note": "no_cached_note_for_exact_business_year",
                })
        topic_results.append({
            "topic": topic,
            "rows": comparison_rows,
            "coverage": sum(row["availability"] != "unavailable" for row in comparison_rows),
            "comparison_note": "Text excerpts are filing evidence; difference interpretation is not inferred.",
        })
    return {
        "subject": subject,
        "year": year,
        "peer_selection": dict(peer_group.get("selection_policy") or {}),
        "peer_confidence": peer_group.get("confidence"),
        "topics": topic_results,
        "read_only": True,
        "limitations": ["Only cached accounting_note_chapters for the exact requested business year are compared."],
    }
