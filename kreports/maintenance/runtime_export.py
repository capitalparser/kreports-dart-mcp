from __future__ import annotations

from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile

from kreports.db import engine as engine_module


COMPACT_EXCLUDED_TABLES = {
    "financial_facts",
    "extraction_runs",
    "fetch_log",
}

COMPACT_TABLE_WHERE = {
    # User-keyed ad-hoc disclosure bodies are session/on-demand cache, not
    # preloaded runtime data. The disclosure list and title event index remain.
    "source_documents": "source_type <> 'event_disclosure'",
}

COPY_BATCH_SIZE = 1000


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _compact_select_expression(table: str, column: str) -> str:
    if table == "source_documents" and column == "raw_content":
        return (
            "CASE WHEN content_type='derived_report_sections' "
            "THEN raw_content ELSE '' END AS raw_content"
        )
    return _quote_ident(column)


def export_runtime_db(
    *,
    output_path: str | Path,
    year_from: int,
    year_to: int,
    profile: str = "compact",
    vacuum: bool = True,
) -> dict:
    if profile != "compact":
        raise ValueError("only compact profile is supported")

    dest = Path(output_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()

    copied: list[str] = []
    dest_conn = sqlite3.connect(dest)
    try:
        with engine_module.engine.connect() as src_conn:
            tables = [
                row[0]
                for row in src_conn.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).all()
            ]
            for table in tables:
                if table in COMPACT_EXCLUDED_TABLES:
                    continue
                schema_row = src_conn.exec_driver_sql(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()
                if not schema_row or not schema_row[0]:
                    continue

                dest_conn.execute(schema_row[0])
                columns = [
                    row[1]
                    for row in src_conn.exec_driver_sql(f"PRAGMA table_info({_quote_ident(table)})").all()
                ]
                if not columns:
                    copied.append(table)
                    continue
                col_csv = ", ".join(_quote_ident(col) for col in columns)
                select_csv = ", ".join(_compact_select_expression(table, col) for col in columns)
                where = COMPACT_TABLE_WHERE.get(table)
                query = f"SELECT {select_csv} FROM {_quote_ident(table)}"
                if where:
                    query += f" WHERE {where}"
                result = src_conn.exec_driver_sql(query)
                placeholders = ", ".join(["?"] * len(columns))
                insert_sql = f"INSERT INTO {_quote_ident(table)} ({col_csv}) VALUES ({placeholders})"
                while True:
                    rows = result.fetchmany(COPY_BATCH_SIZE)
                    if not rows:
                        break
                    dest_conn.executemany(insert_sql, rows)
                copied.append(table)

            indexes = src_conn.exec_driver_sql(
                "SELECT name, tbl_name, sql FROM sqlite_master "
                "WHERE type='index' AND sql IS NOT NULL AND tbl_name NOT IN "
                f"({', '.join('?' for _ in COMPACT_EXCLUDED_TABLES)})",
                tuple(COMPACT_EXCLUDED_TABLES),
            ).all()
            for _name, _tbl_name, idx_sql in indexes:
                try:
                    dest_conn.execute(idx_sql)
                except sqlite3.OperationalError:
                    pass

        dest_conn.commit()
        if vacuum:
            dest_conn.execute("VACUUM")
    finally:
        dest_conn.close()

    return {
        "ok": True,
        "output_path": str(dest),
        "profile": profile,
        "year_from": int(year_from),
        "year_to": int(year_to),
        "copied_tables": copied,
        "excluded_tables": sorted(COMPACT_EXCLUDED_TABLES),
        "table_filters": COMPACT_TABLE_WHERE,
        "vacuum": bool(vacuum),
        "bytes": dest.stat().st_size,
    }


def build_runtime_db_manifest(*, db_path: str | Path, profile: str, year_from: int, year_to: int) -> dict:
    path = Path(db_path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "profile": profile,
        "year_from": int(year_from),
        "year_to": int(year_to),
        "bytes": path.stat().st_size,
        "sha256": digest,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def upload_runtime_db_artifact(
    *,
    db_path: str | Path,
    bucket: str,
    prefix: str = "runtime-db",
    profile: str = "compact",
    year_from: int,
    year_to: int,
) -> dict:
    from google.cloud import storage

    path = Path(db_path)
    manifest = build_runtime_db_manifest(
        db_path=path,
        profile=profile,
        year_from=year_from,
        year_to=year_to,
    )
    client = storage.Client()
    db_object = f"{prefix.strip('/')}/kreports-{profile}-{year_from}-{year_to}.db.gz"
    manifest_object = f"{prefix.strip('/')}/kreports-{profile}-{year_from}-{year_to}.manifest.json"
    bucket_obj = client.bucket(bucket)

    with tempfile.NamedTemporaryFile(suffix=".db.gz") as tmp:
        with path.open("rb") as src, gzip.open(tmp.name, "wb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
        compressed_bytes = Path(tmp.name).stat().st_size
        db_blob = bucket_obj.blob(db_object)
        db_blob.chunk_size = 8 * 1024 * 1024
        db_blob.upload_from_filename(tmp.name, content_type="application/gzip", timeout=600)

    manifest_blob = bucket_obj.blob(manifest_object)
    manifest_blob.upload_from_string(
        json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        content_type="application/json",
        timeout=120,
    )
    return {
        "ok": True,
        "db_uri": f"gs://{bucket}/{db_object}",
        "manifest_uri": f"gs://{bucket}/{manifest_object}",
        "manifest": manifest,
        "compressed_bytes": compressed_bytes,
    }
