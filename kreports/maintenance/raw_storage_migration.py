from __future__ import annotations

from sqlalchemy import text

from kreports.config import settings
from kreports.db.engine import get_session
from kreports.db.models import SourceDocument
from kreports.runtime import runtime_mode
from kreports.storage.raw_documents import RawDocumentStore, sha1_text


def raw_storage_config_status() -> dict:
    """Report whether the collector will inline or externalize new raw documents."""
    backend = (settings.raw_storage_backend or "inline").strip().lower()
    bucket = (settings.raw_storage_bucket or "").strip()
    prefix = (settings.raw_storage_prefix or "").strip("/")
    keep_inline = bool(settings.raw_storage_keep_inline)

    if backend in {"", "inline", "db"}:
        mode = "inline"
        ready = True
        verdict = "inline_raw_will_grow_db"
        notes = [
            "new source_documents will store full raw_content inside SQLite",
            "use RAW_STORAGE_BACKEND=file or RAW_STORAGE_BACKEND=gcs to externalize new raw documents",
        ]
    elif backend == "file":
        mode = "externalized"
        ready = True
        verdict = "file_storage_ready"
        notes = ["new raw documents will be written as gzip files and referenced by storage_uri"]
    elif backend == "gcs":
        mode = "externalized"
        ready = bool(bucket)
        verdict = "gcs_storage_ready" if ready else "gcs_bucket_missing"
        notes = ["new raw documents will be written to GCS and referenced by gs:// storage_uri"]
        if not bucket:
            notes.append("RAW_STORAGE_BUCKET is required when RAW_STORAGE_BACKEND=gcs")
    else:
        mode = "unknown"
        ready = False
        verdict = "unsupported_backend"
        notes = [f"unsupported RAW_STORAGE_BACKEND={backend!r}; expected inline, file, or gcs"]

    return {
        "ready": ready,
        "verdict": verdict,
        "runtime_mode": runtime_mode(),
        "backend": backend or "inline",
        "mode": mode,
        "bucket": bucket or None,
        "prefix": prefix,
        "keep_inline": keep_inline,
        "will_store_inline_raw_content": mode == "inline" or keep_inline,
        "will_write_storage_uri": mode == "externalized",
        "env": {
            "backend": "RAW_STORAGE_BACKEND",
            "bucket": "RAW_STORAGE_BUCKET",
            "prefix": "RAW_STORAGE_PREFIX",
            "keep_inline": "RAW_STORAGE_KEEP_INLINE",
        },
        "notes": notes,
    }


def raw_storage_smoke(
    *,
    backend: str = "file",
    bucket: str | None = None,
    prefix: str = "",
    content: str | None = None,
) -> dict:
    """Write and read one tiny raw document through the configured raw store."""
    smoke_content = content or "<DOCUMENT><TITLE>raw storage smoke</TITLE><P>ok</P></DOCUMENT>"
    store = RawDocumentStore(backend=backend, bucket=bucket, prefix=prefix)
    saved = store.write(
        corp_code="00000000",
        bsns_year=2099,
        source_type="smoke_test",
        rcept_no="raw_storage_smoke",
        content_type="xml",
        content=smoke_content,
    )
    read_back = store.read(saved.storage_uri, expected_hash=saved.doc_hash)
    ok = read_back == smoke_content
    return {
        "ok": ok,
        "backend": backend,
        "storage_uri": saved.storage_uri,
        "doc_hash": saved.doc_hash,
        "content_length": saved.content_length,
        "compressed_length": saved.compressed_length,
        "roundtrip_bytes": len(read_back.encode("utf-8")),
    }


def migrate_raw_documents_to_storage(
    *,
    limit: int | None = None,
    clear_inline: bool = False,
    backend: str = "file",
    bucket: str | None = None,
    prefix: str = "",
) -> dict:
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

        store = RawDocumentStore(backend=backend, bucket=bucket, prefix=prefix)
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
              SUM(CASE WHEN raw_content!='' THEN 1 ELSE 0 END) inline_present,
              SUM(CASE
                    WHEN content_type!='derived_report_sections'
                     AND COALESCE(storage_status, 'inline')!='derived_only'
                     AND (
                       (raw_content IS NOT NULL AND raw_content!='')
                       OR (storage_uri IS NOT NULL AND storage_uri!='')
                     )
                    THEN 1 ELSE 0
                  END) raw_extractable,
              SUM(CASE
                    WHEN content_type!='derived_report_sections'
                     AND (
                       COALESCE(storage_status, '')='derived_only'
                       OR (
                         (raw_content IS NULL OR raw_content='')
                         AND (storage_uri IS NULL OR storage_uri='')
                       )
                     )
                    THEN 1 ELSE 0
                  END) derived_placeholders,
              SUM(CASE
                    WHEN source_type='business_report'
                     AND content_type!='derived_report_sections'
                     AND COALESCE(storage_status, 'inline')!='derived_only'
                     AND (
                       (raw_content IS NOT NULL AND raw_content!='')
                       OR (storage_uri IS NOT NULL AND storage_uri!='')
                     )
                    THEN 1 ELSE 0
                  END) raw_business_extractable,
              SUM(CASE
                    WHEN source_type='audit_report'
                     AND content_type!='derived_report_sections'
                     AND COALESCE(storage_status, 'inline')!='derived_only'
                     AND (
                       (raw_content IS NOT NULL AND raw_content!='')
                       OR (storage_uri IS NOT NULL AND storage_uri!='')
                     )
                    THEN 1 ELSE 0
                  END) raw_audit_extractable,
              SUM(CASE
                    WHEN source_type='business_report'
                     AND content_type!='derived_report_sections'
                     AND (
                       COALESCE(storage_status, '')='derived_only'
                       OR (
                         (raw_content IS NULL OR raw_content='')
                         AND (storage_uri IS NULL OR storage_uri='')
                       )
                     )
                    THEN 1 ELSE 0
                  END) derived_business_placeholders,
              SUM(CASE
                    WHEN source_type='audit_report'
                     AND content_type!='derived_report_sections'
                     AND (
                       COALESCE(storage_status, '')='derived_only'
                       OR (
                         (raw_content IS NULL OR raw_content='')
                         AND (storage_uri IS NULL OR storage_uri='')
                       )
                     )
                    THEN 1 ELSE 0
                  END) derived_audit_placeholders
            FROM source_documents
            """
        )).mappings().one()
    out = {key: int(value or 0) for key, value in dict(row).items()}
    out["parser_repair_ready"] = out["raw_extractable"] > 0
    out["status_note"] = (
        "raw documents available for parser repair"
        if out["parser_repair_ready"]
        else "no raw documents available; only derived placeholders cannot support parser repair"
    )
    return out


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


def clear_externalized_inline_content(*, limit: int | None = None) -> dict:
    """Clear DB inline raw_content only after external storage verification.

    This does not shrink the SQLite file by itself. It makes pages reusable; an
    eventual VACUUM/checkpoint strategy is needed to reduce the physical file.
    """
    totals = {"checked": 0, "cleared": 0, "failed": 0, "cleared_bytes": 0, "errors": []}
    store = RawDocumentStore()
    with get_session() as session:
        query = (
            session.query(SourceDocument)
            .filter(SourceDocument.storage_status == "externalized")
            .filter(SourceDocument.storage_uri.isnot(None))
            .filter(SourceDocument.raw_content != "")
            .order_by(SourceDocument.bsns_year, SourceDocument.rcept_no)
        )
        if limit:
            query = query.limit(int(limit))
        rows = query.all()

        for doc in rows:
            totals["checked"] += 1
            try:
                content = store.read(doc.storage_uri, expected_hash=doc.doc_hash)
                if doc.content_length is not None and len(content.encode("utf-8")) != doc.content_length:
                    raise ValueError("content length mismatch")
                inline = doc.raw_content or ""
                inline_hash = sha1_text(inline)
                if doc.doc_hash and inline_hash != doc.doc_hash:
                    raise ValueError("inline hash mismatch")
                totals["cleared_bytes"] += len(inline.encode("utf-8"))
                doc.raw_content = ""
                totals["cleared"] += 1
            except Exception as exc:
                totals["failed"] += 1
                if len(totals["errors"]) < 20:
                    totals["errors"].append({"rcept_no": doc.rcept_no, "error": str(exc)})
        session.flush()
    return totals


def clear_cold_derived_inline_content(
    *,
    year_to: int,
    limit: int | None = None,
    dry_run: bool = True,
) -> dict:
    """Clear cold raw_content when derived evidence/facts already exist.

    This is the fallback when there is not enough space to externalize every raw
    filing. It intentionally sacrifices raw re-parsing for old documents, while
    preserving the derived rows MCP tools use for answers.
    """
    params: dict[str, object] = {"year_to": int(year_to)}
    limit_sql = "LIMIT :limit" if limit else ""
    if limit:
        params["limit"] = int(limit)

    candidate_sql = f"""
        SELECT sd.id, sd.rcept_no, sd.bsns_year, sd.source_type,
               length(sd.raw_content) AS raw_bytes
        FROM source_documents sd
        WHERE sd.raw_content!=''
          AND sd.content_type!='derived_report_sections'
          AND sd.storage_status!='externalized'
          AND sd.bsns_year<=:year_to
          AND (
            EXISTS (
              SELECT 1 FROM report_sections rs
              WHERE rs.rcept_no=sd.rcept_no
                AND rs.source_type=sd.source_type
                AND rs.corp_code=sd.corp_code
                AND rs.bsns_year=sd.bsns_year
            )
            OR EXISTS (
              SELECT 1 FROM accounting_note_chapters anc
              WHERE anc.rcept_no=sd.rcept_no
                AND anc.source_type=sd.source_type
                AND anc.corp_code=sd.corp_code
                AND anc.bsns_year=sd.bsns_year
            )
            OR EXISTS (
              SELECT 1 FROM audit_procedure_items api
              WHERE api.rcept_no=sd.rcept_no
                AND api.source_type=sd.source_type
                AND api.corp_code=sd.corp_code
                AND api.bsns_year=sd.bsns_year
            )
            OR EXISTS (
              SELECT 1 FROM evidence_documents ed
              WHERE ed.rcept_no=sd.rcept_no
                AND ed.source_type=sd.source_type
                AND ed.corp_code=sd.corp_code
                AND ed.bsns_year=sd.bsns_year
            )
          )
        ORDER BY sd.bsns_year ASC, sd.source_type ASC, sd.rcept_no ASC
        {limit_sql}
    """
    with get_session() as session:
        rows = session.execute(text(candidate_sql), params).mappings().all()
        result = {
            "dry_run": bool(dry_run),
            "year_to": int(year_to),
            "checked": len(rows),
            "cleared": 0,
            "cleared_bytes": int(sum(int(row["raw_bytes"] or 0) for row in rows)),
            "status": "would_clear" if dry_run else "cleared",
            "sample": [
                {
                    "rcept_no": row["rcept_no"],
                    "bsns_year": row["bsns_year"],
                    "source_type": row["source_type"],
                    "raw_bytes": row["raw_bytes"],
                }
                for row in rows[:10]
            ],
        }
        if dry_run or not rows:
            return result

        for row in rows:
            session.execute(
                text(
                    """
                    UPDATE source_documents
                    SET content_length=COALESCE(content_length, :raw_bytes),
                        raw_content='',
                        storage_status='derived_only'
                    WHERE id=:id
                    """
                ),
                {"id": row["id"], "raw_bytes": int(row["raw_bytes"] or 0)},
            )
            result["cleared"] += 1
        session.flush()
    return result
