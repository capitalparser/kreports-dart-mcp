from __future__ import annotations

from sqlalchemy import text

from kreports.db.engine import get_session
from kreports.db.models import SourceDocument
from kreports.storage.raw_documents import RawDocumentStore, sha1_text


def migrate_raw_documents_to_storage(*, limit: int | None = None, clear_inline: bool = False) -> dict:
    totals = {"scanned": 0, "migrated": 0, "skipped": 0, "errors": []}
    with get_session() as session:
        query = (
            session.query(SourceDocument)
            .filter(SourceDocument.content_type != "derived_report_sections")
            .filter(SourceDocument.storage_status != "externalized")
            .filter(SourceDocument.raw_content != "")
            .order_by(SourceDocument.bsns_year, SourceDocument.source_type, SourceDocument.rcept_no)
        )
        if limit:
            query = query.limit(int(limit))
        rows = query.all()

        store = RawDocumentStore()
        for doc in rows:
            totals["scanned"] += 1
            content = doc.raw_content or ""
            if not content:
                totals["skipped"] += 1
                continue
            actual_hash = sha1_text(content)
            if doc.doc_hash and actual_hash != doc.doc_hash:
                totals["errors"].append({"rcept_no": doc.rcept_no, "error": "hash mismatch before migration"})
                continue
            saved = store.write(
                corp_code=doc.corp_code,
                bsns_year=doc.bsns_year,
                source_type=doc.source_type,
                rcept_no=doc.rcept_no,
                content_type=doc.content_type,
                content=content,
            )
            doc.storage_uri = saved.storage_uri
            doc.doc_hash = saved.doc_hash
            doc.content_length = saved.content_length
            doc.compressed_length = saved.compressed_length
            doc.storage_status = "externalized"
            if clear_inline:
                doc.raw_content = ""
            totals["migrated"] += 1
        session.flush()
    return totals


def raw_storage_readiness() -> dict:
    with get_session() as session:
        row = session.execute(text(
            """
            SELECT
              COUNT(*) total,
              SUM(CASE WHEN content_type='derived_report_sections' THEN 1 ELSE 0 END) derived,
              SUM(CASE WHEN storage_status='externalized' THEN 1 ELSE 0 END) externalized,
              SUM(CASE WHEN storage_uri IS NULL OR storage_uri='' THEN 1 ELSE 0 END) missing_uri,
              SUM(CASE WHEN raw_content!='' THEN 1 ELSE 0 END) inline_present
            FROM source_documents
            """
        )).mappings().one()
    return dict(row)


def verify_raw_storage(*, limit: int | None = None) -> dict:
    totals = {"checked": 0, "ok": 0, "failed": 0, "errors": []}
    with get_session() as session:
        query = (
            session.query(SourceDocument)
            .with_entities(
                SourceDocument.rcept_no,
                SourceDocument.storage_uri,
                SourceDocument.doc_hash,
                SourceDocument.content_length,
            )
            .filter(SourceDocument.storage_status == "externalized")
            .filter(SourceDocument.storage_uri.isnot(None))
            .order_by(SourceDocument.bsns_year, SourceDocument.rcept_no)
        )
        if limit:
            query = query.limit(int(limit))
        rows = query.all()

    store = RawDocumentStore()
    for doc in rows:
        totals["checked"] += 1
        try:
            content = store.read(doc.storage_uri, expected_hash=doc.doc_hash)
            if doc.content_length is not None and len(content.encode("utf-8")) != doc.content_length:
                raise ValueError("content length mismatch")
            totals["ok"] += 1
        except Exception as exc:
            totals["failed"] += 1
            if len(totals["errors"]) < 20:
                totals["errors"].append({"rcept_no": doc.rcept_no, "error": str(exc)})
    return totals
