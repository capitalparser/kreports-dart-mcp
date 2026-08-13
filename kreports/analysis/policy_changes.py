"""Accounting policy and estimate-judgment change analysis."""
from __future__ import annotations

from difflib import SequenceMatcher

from sqlalchemy import text

from kreports.db.engine import engine


def _similarity(a: str, b: str) -> float:
    return round(SequenceMatcher(None, a or "", b or "").ratio(), 4)


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
    with engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(stmt, params).mappings()]

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
        changes.append({
            "year": row["bsns_year"],
            "fs_div": row["fs_div"],
            "rcept_no": row["rcept_no"],
            "note_no": row["note_no"],
            "note_title": row.get("note_title"),
            "section_type": row["section_type"],
            "change_type": change_type,
            "similarity_to_previous": sim,
            "body_length": row.get("body_length"),
            "body_excerpt": body[:900],
        })
        previous_by_key[key] = row

    changed = [row for row in changes if row["change_type"] == "changed"]
    return {
        "company": company,
        "start_year": int(start_year),
        "end_year": int(end_year),
        "fs_div": fs_div,
        "change_count": len(changed),
        "changes": changes,
        "changed_items": changed,
        "data_quality": {
            "status": "usable" if changes else "missing",
            "source": "accounting_note_chapters",
            "interpretation": "Policy changes are text-difference hints from cached note chapters; they require professional review before concluding that accounting policy changed.",
        },
    }
