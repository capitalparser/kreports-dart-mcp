"""Release and rehearsal schema-contract regression tests."""
from __future__ import annotations

import sqlite3

from sqlalchemy import create_engine


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    return connection


def test_accounting_note_chapter_contract_names_missing_columns_and_indexes():
    from kreports.db.schema_contract import (
        REQUIRED_COLUMN_SPECS,
        REQUIRED_INDEX_SPECS,
        REQUIRED_TABLES,
        schema_contract_blockers,
    )

    assert "accounting_note_chapters" in REQUIRED_TABLES
    assert {
        "corp_code", "bsns_year", "fs_div", "rcept_no", "note_no",
        "section_type", "body", "body_hash", "body_length",
    }.issubset(REQUIRED_COLUMN_SPECS["accounting_note_chapters"])
    assert REQUIRED_INDEX_SPECS["uq_accounting_note_chapter_identity"] == (
        "accounting_note_chapters",
        ("corp_code", "bsns_year", "fs_div", "note_no", "section_type"),
        True,
        None,
    )

    connection = _connection()
    try:
        connection.execute("CREATE TABLE accounting_note_chapters (id INTEGER PRIMARY KEY)")
        blockers = schema_contract_blockers(connection)
    finally:
        connection.close()

    assert "missing_required_column:accounting_note_chapters.corp_code" in blockers
    assert "missing_required_index:uq_accounting_note_chapter_identity" in blockers
    assert "missing_required_index:idx_note_chapter_corp_year" in blockers


def test_schema_contract_rejects_wrong_named_unique_or_index_definition():
    from kreports.db.schema_contract import schema_contract_blockers

    connection = _connection()
    try:
        connection.executescript("""
            CREATE TABLE accounting_note_chapters (
              id INTEGER PRIMARY KEY,
              corp_code TEXT NOT NULL,
              bsns_year INTEGER NOT NULL,
              fs_div TEXT NOT NULL,
              rcept_no TEXT NOT NULL,
              dcm_no TEXT,
              source_type TEXT NOT NULL,
              note_no TEXT NOT NULL,
              note_title TEXT,
              section_type TEXT NOT NULL,
              body TEXT NOT NULL,
              body_hash TEXT,
              body_length INTEGER,
              fetched_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX uq_accounting_note_chapter_identity
              ON accounting_note_chapters (corp_code, bsns_year, fs_div, note_no);
            CREATE INDEX idx_note_chapter_corp_year
              ON accounting_note_chapters (corp_code, bsns_year);
            CREATE INDEX idx_note_chapter_section_type
              ON accounting_note_chapters (note_no);
        """)
        blockers = schema_contract_blockers(connection)
    finally:
        connection.close()

    assert "invalid_required_index:uq_accounting_note_chapter_identity" in blockers
    assert "invalid_required_index:idx_note_chapter_corp_year" in blockers
    assert "invalid_required_index:idx_note_chapter_section_type" in blockers


def test_rehearsal_migration_state_rejects_a_release_contract_index_mismatch(
    tmp_path,
):
    """Rehearsal must use release's exact table/column/index definitions."""
    from kreports.db.migrations import apply_schema_migrations
    from kreports.db.models import Base
    import kreports.maintenance.kam_rehearsal_worker as worker

    database = tmp_path / "rehearsal-contract.db"
    engine = create_engine(f"sqlite:///{database}")
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            apply_schema_migrations(connection)
    finally:
        engine.dispose()

    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript("""
            DROP INDEX uq_accounting_note_chapter_identity;
            CREATE UNIQUE INDEX uq_accounting_note_chapter_identity
              ON accounting_note_chapters
              (corp_code, bsns_year, fs_div, note_no);
        """)
        worker._ACTIVE_DBAPI_CONNECTION = connection
        state = worker.migration_state()
    finally:
        worker._ACTIVE_DBAPI_CONNECTION = None
        connection.close()

    assert state["schema_complete"] is False
    assert "uq_accounting_note_chapter_identity" in state["invalid_indexes"]


def test_rehearsal_state_names_missing_policy_chapter_table():
    import kreports.maintenance.kam_rehearsal_worker as worker

    connection = _connection()
    try:
        worker._ACTIVE_DBAPI_CONNECTION = connection
        state = worker.migration_state()
    finally:
        worker._ACTIVE_DBAPI_CONNECTION = None
        connection.close()

    assert state["schema_complete"] is False
    assert "accounting_note_chapters" in state["missing_tables"]
