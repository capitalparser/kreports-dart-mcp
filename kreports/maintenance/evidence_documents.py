"""Build compact MCP evidence documents from normalized report tables."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import re

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy import text

from kreports.db.engine import get_session
from kreports.db.models import (
    AccountingNoteChapter,
    AccountingPolicyItem,
    AuditProcedureItem,
    EvidenceDocument,
    ReportSection,
)


def _sha1(value: str) -> str:
    return hashlib.sha1((value or "").encode("utf-8")).hexdigest()


def _clean_text(value: str | None) -> str:
    text = value or ""
    text = text.replace("&cr;", "\n").replace("&#13;", "\n")
    text = text.replace("&nbsp;", " ").replace("&#160;", " ")
    text = text.replace("\r", "\n")
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def _section_heading(label: str, title: str | None = None) -> str:
    title = _clean_text(title)
    return f"## {label}: {title}" if title else f"## {label}"


@dataclass(frozen=True)
class EvidenceBuildResult:
    documents: int
    rows_used: int
    skipped: int


@dataclass(frozen=True)
class EvidenceBlock:
    heading: str
    body: str
    priority: int

    def render(self) -> str:
        return f"{self.heading}\n{self.body}".strip()


_AUDIT_REPORT_PRIORITY_SECTIONS = {
    "kam": 0,
    "emphasis": 1,
    "other_matter": 1,
    "going_concern": 1,
    "basis_for_opinion": 2,
    "audit_opinion": 3,
}


def _render_evidence_bundle(
    *,
    header: list[str],
    blocks: list[EvidenceBlock],
    max_text_chars: int | None,
) -> str:
    if not blocks:
        return ""
    ordered_blocks = blocks
    if max_text_chars:
        rendered_full = "\n".join(header + ["", *[block.render() for block in ordered_blocks]]).strip()
        if len(rendered_full) <= int(max_text_chars):
            return rendered_full
        ordered_blocks = sorted(
            blocks,
            key=lambda block: (block.priority, blocks.index(block)),
        )

    rendered_parts = header + [""]
    truncated = False
    for block in ordered_blocks:
        candidate_parts = rendered_parts + [block.render()]
        candidate = "\n".join(candidate_parts).strip()
        if not max_text_chars or len(candidate) <= int(max_text_chars):
            rendered_parts.append(block.render())
            continue
        truncated = True
        remaining = int(max_text_chars) - len("\n".join(rendered_parts).strip()) - len("\n... (truncated)") - 2
        if remaining > 80 and not any(part.startswith(block.heading) for part in rendered_parts):
            rendered_parts.append(f"{block.heading}\n{block.body[:remaining].rstrip()}")
        break

    evidence = "\n".join(rendered_parts).strip()
    if truncated:
        evidence = evidence.rstrip() + "\n... (truncated)"
    return evidence


def build_evidence_text(
    *,
    corp_code: str,
    bsns_year: int,
    source_type: str,
    rcept_no: str,
    dcm_no: str | None = None,
    max_text_chars: int | None = 12000,
) -> tuple[str, int]:
    """Return a markdown-like evidence bundle and the number of rows used."""
    blocks: list[EvidenceBlock] = []
    rows_used = 0

    with get_session() as session:
        sections = (
            session.query(ReportSection)
            .filter_by(
                corp_code=corp_code,
                bsns_year=bsns_year,
                source_type=source_type,
                rcept_no=rcept_no,
            )
            .order_by(ReportSection.ordinal.asc(), ReportSection.section_key.asc())
            .all()
        )
        for section in sections:
            body = _clean_text(section.body_text)
            if not body:
                continue
            priority = _AUDIT_REPORT_PRIORITY_SECTIONS.get(section.section_key, 10) if source_type == "audit_report" else 10
            blocks.append(EvidenceBlock(
                heading=_section_heading(f"report_section/{section.section_key}", section.section_title),
                body=body,
                priority=priority,
            ))
            rows_used += 1

        if source_type == "business_report":
            chapters = (
                session.query(AccountingNoteChapter)
                .filter_by(corp_code=corp_code, bsns_year=bsns_year, rcept_no=rcept_no)
                .order_by(AccountingNoteChapter.fs_div.asc(), AccountingNoteChapter.note_no.asc())
                .all()
            )
            for chapter in chapters:
                body = _clean_text(chapter.body)
                if not body:
                    continue
                label = f"accounting_note/{chapter.fs_div}/{chapter.note_no}/{chapter.section_type}"
                blocks.append(EvidenceBlock(
                    heading=_section_heading(label, chapter.note_title),
                    body=body,
                    priority=5 if chapter.section_type == "policy" else 8,
                ))
                rows_used += 1

            policies = (
                session.query(AccountingPolicyItem)
                .filter_by(corp_code=corp_code, bsns_year=bsns_year, rcept_no=rcept_no)
                .order_by(AccountingPolicyItem.fs_div.asc(), AccountingPolicyItem.item_key.asc())
                .all()
            )
            for policy in policies:
                body = _clean_text(policy.body)
                if not body:
                    continue
                label = f"accounting_policy/{policy.fs_div}/{policy.item_key}"
                blocks.append(EvidenceBlock(
                    heading=_section_heading(label, policy.heading),
                    body=body,
                    priority=5,
                ))
                rows_used += 1

        procedures = (
            session.query(AuditProcedureItem)
            .filter_by(
                corp_code=corp_code,
                bsns_year=bsns_year,
                source_type=source_type,
                rcept_no=rcept_no,
            )
            .order_by(AuditProcedureItem.section_ordinal.asc(), AuditProcedureItem.procedure_ordinal.asc())
            .all()
        )
        for item in procedures:
            body = _clean_text(item.procedure_text)
            if not body:
                continue
            topic = item.kam_topic or "unknown_topic"
            label = f"audit_procedure/{topic}/{item.procedure_type}"
            blocks.append(EvidenceBlock(
                heading=_section_heading(label),
                body=body,
                priority=0 if source_type == "audit_report" else 6,
            ))
            rows_used += 1

    if not blocks:
        return "", 0

    header = [
        f"# Evidence document",
        f"- corp_code: {corp_code}",
        f"- bsns_year: {bsns_year}",
        f"- source_type: {source_type}",
        f"- rcept_no: {rcept_no}",
    ]
    if dcm_no:
        header.append(f"- dcm_no: {dcm_no}")
    evidence = _render_evidence_bundle(
        header=header,
        blocks=blocks,
        max_text_chars=max_text_chars,
    )
    return evidence, rows_used


def rebuild_evidence_documents(
    *,
    year: int | None = None,
    corp_code: str | None = None,
    source_type: str | None = None,
    limit: int | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    max_text_chars: int | None = 12000,
) -> dict:
    """Rebuild evidence_documents from already parsed local evidence tables."""
    where = []
    params: dict[str, object] = {}
    if year is not None:
        where.append("bsns_year=:year")
        params["year"] = int(year)
    if year_from is not None:
        where.append("bsns_year>=:year_from")
        params["year_from"] = int(year_from)
    if year_to is not None:
        where.append("bsns_year<=:year_to")
        params["year_to"] = int(year_to)
    if corp_code is not None:
        where.append("corp_code=:corp_code")
        params["corp_code"] = corp_code
    if source_type is not None:
        where.append("source_type=:source_type")
        params["source_type"] = source_type
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    limit_sql = "LIMIT :limit" if limit is not None else ""
    if limit is not None:
        params["limit"] = int(limit)

    with get_session() as session:
        targets = session.execute(text(f"""
            SELECT corp_code, bsns_year, source_type, rcept_no, max(dcm_no) AS dcm_no
            FROM (
                SELECT corp_code, bsns_year, source_type, rcept_no, dcm_no FROM report_sections
                UNION ALL
                SELECT corp_code, bsns_year, source_type, rcept_no, dcm_no FROM accounting_note_chapters
                UNION ALL
                SELECT corp_code, bsns_year, source_type, rcept_no, dcm_no FROM audit_procedure_items
            )
            {where_sql}
            GROUP BY corp_code, bsns_year, source_type, rcept_no
            ORDER BY bsns_year DESC, corp_code ASC, source_type ASC
            {limit_sql}
        """), params).mappings().all()

    documents = 0
    rows_used_total = 0
    skipped = 0
    now = datetime.utcnow()

    for target in targets:
        evidence_text, rows_used = build_evidence_text(
            corp_code=target["corp_code"],
            bsns_year=target["bsns_year"],
            source_type=target["source_type"],
            rcept_no=target["rcept_no"],
            dcm_no=target["dcm_no"],
            max_text_chars=max_text_chars,
        )
        if not evidence_text:
            skipped += 1
            continue
        row = {
            "corp_code": target["corp_code"],
            "bsns_year": target["bsns_year"],
            "source_type": target["source_type"],
            "rcept_no": target["rcept_no"],
            "dcm_no": target["dcm_no"],
            "evidence_scope": "auditor_view",
            "title": f"{target['bsns_year']} {target['source_type']} evidence",
            "normalized_text": evidence_text,
            "text_hash": _sha1(evidence_text),
            "text_length": len(evidence_text),
            "source_count": rows_used,
            "generated_at": now,
        }
        with get_session() as session:
            stmt = sqlite_insert(EvidenceDocument).values(row)
            stmt = stmt.on_conflict_do_update(
                index_elements=["corp_code", "bsns_year", "source_type", "rcept_no", "evidence_scope"],
                set_={
                    "dcm_no": stmt.excluded.dcm_no,
                    "title": stmt.excluded.title,
                    "normalized_text": stmt.excluded.normalized_text,
                    "text_hash": stmt.excluded.text_hash,
                    "text_length": stmt.excluded.text_length,
                    "source_count": stmt.excluded.source_count,
                    "generated_at": stmt.excluded.generated_at,
                },
            )
            session.execute(stmt)
        documents += 1
        rows_used_total += rows_used

    return {
        "documents": documents,
        "rows_used": rows_used_total,
        "skipped": skipped,
        "targets": len(targets),
    }


def trim_evidence_documents(
    *,
    year_from: int | None = None,
    year_to: int | None = None,
    max_text_chars: int = 12000,
) -> dict:
    """Prune or truncate evidence_documents to keep the runtime DB compact."""
    result = {"deleted": 0, "trimmed": 0, "trimmed_bytes": 0}
    with get_session() as session:
        delete_filters = []
        params: dict[str, object] = {}
        if year_from is not None:
            delete_filters.append("bsns_year < :year_from")
            params["year_from"] = int(year_from)
        if year_to is not None:
            delete_filters.append("bsns_year > :year_to")
            params["year_to"] = int(year_to)
        if delete_filters:
            delete_result = session.execute(
                text(f"DELETE FROM evidence_documents WHERE {' OR '.join(delete_filters)}"),
                params,
            )
            result["deleted"] = int(delete_result.rowcount or 0)

        rows = (
            session.query(EvidenceDocument)
            .filter(EvidenceDocument.text_length > int(max_text_chars))
            .all()
        )
        for doc in rows:
            original = doc.normalized_text or ""
            if len(original) <= int(max_text_chars):
                continue
            trimmed = original[: int(max_text_chars)].rstrip() + "\n... (truncated)"
            result["trimmed_bytes"] += max(0, len(original.encode("utf-8")) - len(trimmed.encode("utf-8")))
            doc.normalized_text = trimmed
            doc.text_hash = _sha1(trimmed)
            doc.text_length = len(trimmed)
            result["trimmed"] += 1
        session.flush()
    return result


def evidence_document_readiness() -> dict:
    with get_session() as session:
        total = session.query(EvidenceDocument).count()
        companies = session.query(EvidenceDocument.corp_code).distinct().count()
        years = session.query(EvidenceDocument.bsns_year).distinct().count()
        audit = session.query(EvidenceDocument).filter_by(source_type="audit_report").count()
        business = session.query(EvidenceDocument).filter_by(source_type="business_report").count()
    return {
        "total": total,
        "companies": companies,
        "years": years,
        "audit_report": audit,
        "business_report": business,
    }
