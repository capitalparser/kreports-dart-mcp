"""Build structured audit matter rows from normalized audit report sections."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from kreports.analysis.api import _classify_audit_matter
from kreports.db.engine import get_session, init_db
from kreports.db.models import AuditMatterItem, ReportSection

MATTER_KEYS = ("other_matter", "emphasis", "going_concern", "basis_for_opinion")


def _sha1(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()


def rebuild_audit_matter_items(*, year: int | None = None, limit: int | None = None) -> dict:
    """Rebuild structured audit matter rows from cached report sections."""
    init_db()
    with get_session() as session:
        query = session.query(ReportSection).filter(
            ReportSection.source_type == "audit_report",
            ReportSection.section_key.in_(MATTER_KEYS),
        )
        if year is not None:
            query = query.filter(ReportSection.bsns_year == int(year))
        query = query.order_by(
            ReportSection.bsns_year.desc(),
            ReportSection.corp_code,
            ReportSection.rcept_no,
            ReportSection.ordinal,
        )
        if limit:
            query = query.limit(int(limit))
        rows = query.all()

        inserted = 0
        for row in rows:
            body = row.body_text or ""
            classified = _classify_audit_matter(body, row.section_key)
            values = {
                "rcept_no": row.rcept_no,
                "dcm_no": row.dcm_no,
                "corp_code": row.corp_code,
                "bsns_year": row.bsns_year,
                "matter_type": row.section_key,
                "matter_title": row.section_title,
                "matter_text": body,
                "matter_hash": _sha1(body),
                "matter_length": len(body),
                "topic_tags": json.dumps(classified["topic_tags"], ensure_ascii=False),
                "severity_hint": classified["severity_hint"],
                "source_type": row.source_type,
                "section_ordinal": row.ordinal,
                "fetched_at": datetime.utcnow(),
            }
            stmt = sqlite_insert(AuditMatterItem).values(values)
            stmt = stmt.on_conflict_do_update(
                index_elements=["rcept_no", "matter_type", "section_ordinal"],
                set_={
                    "dcm_no": stmt.excluded.dcm_no,
                    "corp_code": stmt.excluded.corp_code,
                    "bsns_year": stmt.excluded.bsns_year,
                    "matter_title": stmt.excluded.matter_title,
                    "matter_text": stmt.excluded.matter_text,
                    "matter_hash": stmt.excluded.matter_hash,
                    "matter_length": stmt.excluded.matter_length,
                    "topic_tags": stmt.excluded.topic_tags,
                    "severity_hint": stmt.excluded.severity_hint,
                    "source_type": stmt.excluded.source_type,
                    "fetched_at": stmt.excluded.fetched_at,
                },
            )
            session.execute(stmt)
            inserted += 1
    return {"scanned": len(rows), "inserted": inserted, "year": year}
