from __future__ import annotations

from sqlalchemy import text

from kreports.db.engine import get_session
from kreports.storage.evidence_blobs import EvidenceBlobStore, sha1_text


TABLE_CONFIG = {
    "accounting_note_chapters": {
        "text_column": "body",
    },
    "evidence_documents": {
        "text_column": "normalized_text",
    },
    "report_sections": {
        "text_column": "body_text",
    },
}


def _config(table_name: str, text_column: str | None = None) -> dict:
    if table_name not in TABLE_CONFIG:
        raise ValueError(f"unsupported evidence table: {table_name}")
    cfg = dict(TABLE_CONFIG[table_name])
    if text_column:
        if text_column != cfg["text_column"]:
            raise ValueError(f"unsupported text column for {table_name}: {text_column}")
        cfg["text_column"] = text_column
    return cfg


def externalize_long_evidence_text(
    *,
    table_name: str,
    text_column: str | None = None,
    excerpt_chars: int = 2000,
    min_text_chars: int = 4000,
    limit: int | None = None,
    backend: str = "file",
    bucket: str | None = None,
    prefix: str = "evidence/full-text",
) -> dict:
    cfg = _config(table_name, text_column)
    col = cfg["text_column"]
    limit_sql = " LIMIT :limit" if limit else ""
    params = {"min_text_chars": int(min_text_chars)}
    if limit:
        params["limit"] = int(limit)

    select_sql = text(f"""
        SELECT id, corp_code, bsns_year, {col} AS full_text
        FROM {table_name}
        WHERE length(coalesce({col}, '')) >= :min_text_chars
          AND (full_text_uri IS NULL OR full_text_uri='')
        ORDER BY bsns_year DESC, id
        {limit_sql}
    """)
    store = EvidenceBlobStore(backend=backend, bucket=bucket, prefix=prefix)
    externalized = skipped = failed = 0
    errors: list[dict] = []

    with get_session() as session:
        rows = session.execute(select_sql, params).mappings().all()
        for row in rows:
            full_text = row["full_text"] or ""
            if not full_text:
                skipped += 1
                continue
            try:
                saved = store.write(
                    table_name=table_name,
                    row_id=int(row["id"]),
                    corp_code=row["corp_code"],
                    bsns_year=int(row["bsns_year"]),
                    content=full_text,
                )
                excerpt = full_text[: int(excerpt_chars)]
                session.execute(text(f"""
                    UPDATE {table_name}
                    SET {col}=:excerpt,
                        full_text_uri=:uri,
                        full_text_hash=:hash,
                        full_text_length=:content_length,
                        full_text_compressed_length=:compressed_length,
                        full_text_storage_status='externalized'
                    WHERE id=:id
                """), {
                    "excerpt": excerpt,
                    "uri": saved.storage_uri,
                    "hash": sha1_text(full_text),
                    "content_length": saved.content_length,
                    "compressed_length": saved.compressed_length,
                    "id": row["id"],
                })
                externalized += 1
            except Exception as exc:
                failed += 1
                if len(errors) < 20:
                    errors.append({"id": row["id"], "error": str(exc)})
        session.commit()

    return {
        "table": table_name,
        "total": len(rows),
        "externalized": externalized,
        "skipped": skipped,
        "failed": failed,
        "errors": errors,
    }
