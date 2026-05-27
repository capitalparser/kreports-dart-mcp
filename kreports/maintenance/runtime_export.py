from __future__ import annotations

from pathlib import Path
import sqlite3

from kreports.db import engine as engine_module


COMPACT_EXCLUDED_TABLES = {
    "financial_facts",
    "extraction_runs",
    "fetch_log",
}


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def export_runtime_db(
    *,
    output_path: str | Path,
    year_from: int,
    year_to: int,
    profile: str = "compact",
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
                rows = src_conn.exec_driver_sql(f"SELECT {col_csv} FROM {_quote_ident(table)}").fetchall()
                if rows:
                    placeholders = ", ".join(["?"] * len(columns))
                    dest_conn.executemany(
                        f"INSERT INTO {_quote_ident(table)} ({col_csv}) VALUES ({placeholders})",
                        rows,
                    )
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
        "bytes": dest.stat().st_size,
    }
