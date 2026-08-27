"""Lightweight source-first projection over canonical note evidence.

This module is an application projection, not a second note-evidence engine. It
reuses ``note_evidence`` for deterministic references and text-completeness
classification, then attaches only the fields needed by the default chatbot
answer and lazy drill-down actions. It deliberately skips disclosure grading
and optional facet extraction on the ordinary search/comparison path.
"""
from __future__ import annotations

from contextlib import contextmanager
import re
from typing import Any, Iterable, Iterator, Sequence

from sqlalchemy.orm import Session

from kreports.analysis.note_evidence import (
    NOTE_EVIDENCE_VERSION,
    build_note_ref,
    load_note_text,
    note_resource_uris,
)
from kreports.db.engine import get_session
from kreports.db.models import AccountingNoteChapter


SOURCE_PROJECTION_VERSION = "note_source_projection.v1"
_MAX_RELATED_TEXT = 1_600


@contextmanager
def _session_scope(session: Session | None = None) -> Iterator[Session]:
    if session is not None:
        yield session
        return
    with get_session() as managed:
        yield managed


def _chunks(values: Sequence[int], size: int = 500) -> Iterator[list[int]]:
    for index in range(0, len(values), size):
        yield list(values[index:index + size])


def _load_rows(
    note_ids: Iterable[int],
    *,
    session: Session,
) -> dict[int, AccountingNoteChapter]:
    ids = sorted({int(value) for value in note_ids if value is not None})
    rows: dict[int, AccountingNoteChapter] = {}
    for chunk in _chunks(ids):
        for row in (
            session.query(AccountingNoteChapter)
            .filter(AccountingNoteChapter.id.in_(chunk))
            .all()
        ):
            rows[int(row.id)] = row
    return rows


def _source_locator_id(value: Any) -> int | None:
    match = re.fullmatch(
        r"accounting_note_chapters:([0-9]+)",
        str(value or ""),
        re.ASCII,
    )
    return int(match.group(1)) if match else None


def _bounded_source_text(value: Any) -> str | None:
    if not value:
        return None
    normalized = " ".join(str(value).split())
    if len(normalized) <= _MAX_RELATED_TEXT:
        return normalized
    return normalized[:_MAX_RELATED_TEXT].rstrip() + " …"


def _normalized_source_text(record: dict[str, Any]) -> str | None:
    for key in (
        "body_excerpt",
        "related_paragraph",
        "raw_text",
        "value_or_excerpt",
        "comparison_text",
    ):
        bounded = _bounded_source_text(record.get(key))
        if bounded:
            return bounded
    return None


def _canonical_source_url(receipt: Any) -> str | None:
    value = str(receipt or "")
    if re.fullmatch(r"[0-9]{14}", value, re.ASCII):
        return (
            "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="
            f"{value}"
        )
    return None


def _attach_source_projection(
    record: dict[str, Any],
    row: AccountingNoteChapter,
) -> str:
    """Attach reference/scope fields without reading external text or grading."""
    note_ref = build_note_ref(row)
    uris = note_resource_uris(note_ref)
    note_text = load_note_text(row, include_external=False)
    record.update({
        "note_ref": note_ref,
        "note_resource_uri": uris["summary"],
        "paragraph_resource_uri": uris["paragraph"],
        "full_note_resource_uri": uris["full_page"],
        "text_completeness": note_text.completeness,
        "text_source_basis": note_text.source_basis,
        "related_paragraph": (
            _normalized_source_text(record)
            or _bounded_source_text(note_text.text)
        ),
        "source_url": (
            record.get("source_url")
            or _canonical_source_url(row.rcept_no)
        ),
    })
    return note_text.completeness


def project_note_search_sources(
    result: dict[str, Any],
    *,
    session: Session | None = None,
) -> dict[str, Any]:
    """Attach lazy note actions to search results without default grading."""
    if not isinstance(result, dict) or "error" in result:
        return result

    note_ids: list[int] = []
    for company in result.get("companies") or []:
        if not isinstance(company, dict):
            continue
        for record in company.get("records") or []:
            if isinstance(record, dict) and record.get("id") is not None:
                note_ids.append(int(record["id"]))

    projected = 0
    partial = 0
    with _session_scope(session) as active:
        rows = _load_rows(note_ids, session=active)
        for company in result.get("companies") or []:
            if not isinstance(company, dict):
                continue
            for record in company.get("records") or []:
                if not isinstance(record, dict):
                    continue
                row = rows.get(int(record.get("id") or 0))
                if row is None:
                    continue
                completeness = _attach_source_projection(record, row)
                projected += 1
                if completeness != "complete":
                    partial += 1

    enriched = dict(result)
    enriched["note_evidence"] = {
        "version": NOTE_EVIDENCE_VERSION,
        "projection_version": SOURCE_PROJECTION_VERSION,
        "projection": "source_first",
        "projected_record_count": projected,
        "partial_text_record_count": partial,
        "optional_facet_assessment_performed": False,
        "full_note_loaded_lazily": True,
    }
    quality = dict(enriched.get("data_quality") or {})
    limitations = list(quality.get("limitations") or [])
    if partial:
        limitations.append(
            "some_source_excerpts_require_full_note_confirmation"
        )
    quality["limitations"] = list(dict.fromkeys(limitations))
    enriched["data_quality"] = quality
    return enriched


def project_note_comparison_sources(
    result: dict[str, Any],
    *,
    session: Session | None = None,
) -> dict[str, Any]:
    """Attach source-first drill-down fields to existing comparison rows."""
    if not isinstance(result, dict) or "error" in result:
        return result

    note_ids: list[int] = []
    for topic in result.get("topics") or []:
        if not isinstance(topic, dict):
            continue
        for record in topic.get("rows") or []:
            if not isinstance(record, dict):
                continue
            note_id = _source_locator_id(record.get("source_locator"))
            if note_id is not None:
                note_ids.append(note_id)

    projected = 0
    partial = 0
    with _session_scope(session) as active:
        rows = _load_rows(note_ids, session=active)
        for topic in result.get("topics") or []:
            if not isinstance(topic, dict):
                continue
            for record in topic.get("rows") or []:
                if not isinstance(record, dict):
                    continue
                note_id = _source_locator_id(record.get("source_locator"))
                row = rows.get(note_id or 0)
                if row is None:
                    continue
                completeness = _attach_source_projection(record, row)
                projected += 1
                if completeness != "complete":
                    partial += 1

    enriched = dict(result)
    enriched.pop("disclosure_depth_by_company", None)
    enriched["note_evidence"] = {
        "version": NOTE_EVIDENCE_VERSION,
        "projection_version": SOURCE_PROJECTION_VERSION,
        "projection": "source_first",
        "projected_row_count": projected,
        "partial_text_row_count": partial,
        "optional_facet_assessment_performed": False,
        "full_note_loaded_lazily": True,
    }
    quality = dict(enriched.get("data_quality") or {})
    limitations = list(quality.get("limitations") or [])
    if partial:
        limitations.append(
            "some_source_excerpts_require_full_note_confirmation"
        )
    quality["limitations"] = list(dict.fromkeys(limitations))
    enriched["data_quality"] = quality
    return enriched


__all__ = [
    "SOURCE_PROJECTION_VERSION",
    "project_note_comparison_sources",
    "project_note_search_sources",
]
