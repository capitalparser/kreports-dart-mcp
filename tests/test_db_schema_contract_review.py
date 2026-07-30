"""Adversarial regressions for the policy schema-contract review findings."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
import sqlite3

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


def _full_contract_database(tmp_path, name: str) -> sqlite3.Connection:
    from kreports.db.migrations import apply_schema_migrations
    from kreports.db.models import Base

    database = tmp_path / name
    engine = create_engine(f"sqlite:///{database}")
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            apply_schema_migrations(connection)
    finally:
        engine.dispose()
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    return connection


def test_release_and_rehearsal_share_audit_fee_column_blockers(tmp_path):
    """A release must not admit an audit-fee shape rehearsal rejects."""
    from kreports import release_artifact
    import kreports.maintenance.kam_rehearsal_worker as worker

    connection = _full_contract_database(tmp_path, "audit-fee-parity.db")
    try:
        connection.execute("ALTER TABLE audit_fees DROP COLUMN source_observations_json")
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_schema WHERE type='table'")
        }
        release_blockers = release_artifact._column_contract_blockers(connection, tables)
        worker._ACTIVE_DBAPI_CONNECTION = connection
        rehearsal_state = worker.migration_state()
    finally:
        worker._ACTIVE_DBAPI_CONNECTION = None
        connection.close()

    blocker = "missing_required_column:audit_fees.source_observations_json"
    assert blocker in release_blockers
    assert "source_observations_json" in rehearsal_state["missing_columns"]["audit_fees"]
    assert rehearsal_state["schema_complete"] is False


def test_audit_fee_contract_has_every_rehearsal_required_field():
    from kreports.db.schema_contract import REQUIRED_COLUMN_SPECS

    assert REQUIRED_COLUMN_SPECS["audit_fees"] == (
        "contract_fee_m", "contract_hours", "actual_fee_m", "actual_hours",
        "source_class", "source_rcept_no", "source_period",
        "availability_status", "quality_status", "compatibility_basis",
        "conflict_status", "source_observations_json",
    )


def test_partial_index_predicate_must_be_exact_not_a_substring():
    from kreports.db.schema_contract import index_contract_blockers

    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript("""
            CREATE TABLE backfill_runs (lease_key TEXT, status TEXT);
            CREATE UNIQUE INDEX uq_backfill_runs_active_lease
              ON backfill_runs (lease_key)
              WHERE status = 'running' AND 0;
        """)
        blockers = index_contract_blockers(connection, {"backfill_runs"})
    finally:
        connection.close()

    assert "invalid_required_index:uq_backfill_runs_active_lease" in blockers


def test_accounting_note_migration_creates_full_orm_schema_and_indexes(tmp_path):
    from kreports.db.migrations import MIGRATIONS, _checksum, apply_schema_migrations

    database = tmp_path / "bare-note-contract.db"
    engine = create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.exec_driver_sql("""
            CREATE TABLE schema_migrations (
              revision TEXT PRIMARY KEY, checksum TEXT NOT NULL,
              description TEXT NOT NULL, applied_at TEXT NOT NULL
            )
        """)
        for migration in MIGRATIONS[:11]:
            connection.execute(
                text(
                    "INSERT INTO schema_migrations "
                    "(revision, checksum, description, applied_at) "
                    "VALUES (:revision, :checksum, :description, CURRENT_TIMESTAMP)"
                ),
                {
                    "revision": migration.revision,
                    "checksum": _checksum(migration),
                    "description": migration.description,
                },
            )
    with engine.begin() as connection:
        assert apply_schema_migrations(connection) == [
            migration.revision for migration in MIGRATIONS[11:]
        ]
    try:
        from sqlalchemy import inspect
        from kreports.db.models import AccountingNoteChapter

        columns = {
            item["name"] for item in inspect(engine).get_columns("accounting_note_chapters")
        }
        assert {
            column.name for column in AccountingNoteChapter.__table__.columns
        }.issubset(columns)
        indexes = {
            item["name"] for item in inspect(engine).get_indexes("accounting_note_chapters")
        }
        assert {item.name for item in AccountingNoteChapter.__table__.indexes}.issubset(indexes)
    finally:
        engine.dispose()


@pytest.mark.parametrize("initial_journal_mode", ("delete", "memory"))
def test_concurrent_file_migrations_keep_wal_and_one_checksum_ledger(
    tmp_path,
    initial_journal_mode: str,
):
    """Concurrent schema installers serialize DDL without disabling WAL."""
    from kreports.db.migrations import MIGRATIONS, apply_schema_migrations
    from kreports.db.models import Base

    for run in range(8):
        database = tmp_path / f"concurrent-{run}.db"
        bootstrap = create_engine(f"sqlite:///{database}")
        try:
            Base.metadata.create_all(bootstrap)
            with bootstrap.connect() as connection:
                assert (
                    connection.exec_driver_sql(
                        f"PRAGMA journal_mode={initial_journal_mode}"
                    ).scalar_one()
                    == initial_journal_mode
                )
                connection.commit()
        finally:
            bootstrap.dispose()

        def migrate_once() -> list[str]:
            engine = create_engine(f"sqlite:///{database}", connect_args={"timeout": 0.05})
            try:
                with engine.begin() as connection:
                    return apply_schema_migrations(connection)
            finally:
                engine.dispose()

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _value: migrate_once(), range(2)))

        assert sum(bool(outcome) for outcome in outcomes) == 1
        connection = sqlite3.connect(database)
        try:
            assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)
            assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
            assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone() == (
                len(MIGRATIONS),
            )
            assert [
                row[0]
                for row in connection.execute(
                    "SELECT revision FROM schema_migrations ORDER BY revision"
                )
            ] == [migration.revision for migration in MIGRATIONS]
        finally:
            connection.close()


def test_file_backed_memory_journal_is_promoted_to_wal_before_migration(tmp_path):
    """Catch a file-backed MEMORY journal bypassing the WAL migration contract."""
    from kreports.db.migrations import apply_schema_migrations
    from kreports.db.models import Base

    database = tmp_path / "memory-before-migration.db"
    engine = create_engine(f"sqlite:///{database}")
    try:
        Base.metadata.create_all(engine)
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA journal_mode=MEMORY").scalar_one() == "memory"
            connection.commit()
            assert apply_schema_migrations(connection)
            connection.commit()
    finally:
        engine.dispose()

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)


def test_contract_repair_migration_restores_ledger_claimed_required_indexes(tmp_path):
    """Catch migration success when an older ledger claims an absent contract index."""
    from kreports.db.migrations import apply_schema_migrations
    from kreports.db.models import Base

    database = tmp_path / "ledger-claimed-index-missing.db"
    engine = create_engine(f"sqlite:///{database}")
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            assert apply_schema_migrations(connection)
            connection.exec_driver_sql("DROP INDEX uq_backfill_runs_active_lease")
            connection.exec_driver_sql(
                "DELETE FROM schema_migrations "
                "WHERE revision='20260731_14_schema_contract_repair'"
            )
        with engine.begin() as connection:
            applied = apply_schema_migrations(connection)
    finally:
        engine.dispose()

    assert applied[-1] == "20260731_14_schema_contract_repair"
    with sqlite3.connect(database) as connection:
        indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(backfill_runs)")
        }
    assert "uq_backfill_runs_active_lease" in indexes


def test_policy_item_contract_is_shared_by_release_and_rehearsal(tmp_path):
    """Catch a peer-policy runtime table omitted from either schema gate."""
    from kreports import release_artifact
    import kreports.maintenance.kam_rehearsal_worker as worker

    connection = _full_contract_database(tmp_path, "policy-item-parity.db")
    try:
        connection.execute("DROP TABLE accounting_policy_items")
        worker._ACTIVE_DBAPI_CONNECTION = connection
        rehearsal_state = worker.migration_state()
    finally:
        worker._ACTIVE_DBAPI_CONNECTION = None
        connection.close()

    assert "accounting_policy_items" in rehearsal_state["missing_tables"]
    assert rehearsal_state["schema_complete"] is False
    assert "accounting_policy_items" in release_artifact.REQUIRED_TABLES


def test_policy_item_contract_blocks_missing_column_and_index_everywhere(tmp_path):
    """Catch peer-policy comparisons accepting a partial cache table contract."""
    from kreports import release_artifact
    import kreports.maintenance.kam_rehearsal_worker as worker

    connection = _full_contract_database(tmp_path, "policy-item-shape-parity.db")
    try:
        connection.execute("ALTER TABLE accounting_policy_items DROP COLUMN body_hash")
        connection.execute("DROP INDEX idx_policy_item_key")
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_schema WHERE type='table'")
        }
        release_columns = release_artifact._column_contract_blockers(connection, tables)
        release_indexes = release_artifact._index_contract_blockers(connection, tables)
        worker._ACTIVE_DBAPI_CONNECTION = connection
        rehearsal_state = worker.migration_state()
    finally:
        worker._ACTIVE_DBAPI_CONNECTION = None
        connection.close()

    assert "missing_required_column:accounting_policy_items.body_hash" in release_columns
    assert "missing_required_index:idx_policy_item_key" in release_indexes
    assert rehearsal_state["missing_columns"]["accounting_policy_items"] == ["body_hash"]
    assert "idx_policy_item_key" in rehearsal_state["missing_indexes"]
    assert rehearsal_state["schema_complete"] is False


def test_policy_item_repair_migration_matches_orm_schema_and_indexes(tmp_path):
    """Catch a repair migration that creates a peer-policy table unlike the ORM."""
    from kreports.db.migrations import MIGRATIONS, _checksum, apply_schema_migrations
    from kreports.db.models import AccountingPolicyItem

    database = tmp_path / "bare-policy-item.db"
    engine = create_engine(f"sqlite:///{database}")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE schema_migrations (revision TEXT PRIMARY KEY, "
                "checksum TEXT NOT NULL, description TEXT NOT NULL, applied_at TEXT NOT NULL)"
            )
            for migration in MIGRATIONS[:-1]:
                connection.execute(
                    text(
                        "INSERT INTO schema_migrations "
                        "(revision, checksum, description, applied_at) "
                        "VALUES (:revision, :checksum, :description, CURRENT_TIMESTAMP)"
                    ),
                    {
                        "revision": migration.revision,
                        "checksum": _checksum(migration),
                        "description": migration.description,
                    },
                )
        with engine.begin() as connection:
            assert apply_schema_migrations(connection) == [MIGRATIONS[-1].revision]
            columns = {
                row[1]
                for row in connection.exec_driver_sql(
                    "PRAGMA table_info(accounting_policy_items)"
                )
            }
            indexes = {
                row[1]
                for row in connection.exec_driver_sql(
                    "PRAGMA index_list(accounting_policy_items)"
                )
            }
    finally:
        engine.dispose()

    assert {column.name for column in AccountingPolicyItem.__table__.columns} <= columns
    assert {"uq_policy_item", "idx_policy_item_corp_year", "idx_policy_item_key"} <= indexes


def test_policy_change_readiness_requires_two_proven_comparable_annual_years(temp_engine):
    """One unproven current chapter is not policy-change readiness."""
    import kreports.analysis.readiness as readiness
    from kreports.db.models import AccountingNoteChapter, Company

    Session = sessionmaker(bind=temp_engine)
    with Session() as session:
        session.add(Company(corp_code="00126380", corp_name="A", stock_code="005930", market="KOSPI"))
        session.add(AccountingNoteChapter(
            corp_code="00126380", bsns_year=2025, fs_div="CFS",
            rcept_no="20260301000001", source_type="business_report",
            note_no="2", section_type="policy", body="current",
        ))
        session.commit()

    snapshot = readiness.auditor_feature_readiness_snapshot(year=2025, market="KOSPI")

    assert snapshot["feature_status"]["accounting_policy_changes"] != "usable"
    assert snapshot["counts"]["policy_change_excluded_unproven"] == 1
    assert snapshot["counts"]["policy_change_comparable_companies"] == 0


def test_policy_change_readiness_rejects_nonlatest_annual_receipt(temp_engine):
    import kreports.analysis.readiness as readiness
    from kreports.db.models import AccountingNoteChapter, Company, Disclosure

    Session = sessionmaker(bind=temp_engine)
    with Session() as session:
        session.add(Company(corp_code="00126380", corp_name="A", stock_code="005930", market="KOSPI"))
        session.add_all([
            Disclosure(
                rcept_no="20250301000001", corp_code="00126380", corp_name="A",
                disc_date=date(2025, 3, 1), disc_type="A", report_nm="사업보고서 (2024.12)",
            ),
            Disclosure(
                rcept_no="20250401000001", corp_code="00126380", corp_name="A",
                disc_date=date(2025, 4, 1), disc_type="A", report_nm="사업보고서 (2024.12) [정정]",
            ),
            Disclosure(
                rcept_no="20260301000001", corp_code="00126380", corp_name="A",
                disc_date=date(2026, 3, 1), disc_type="A", report_nm="사업보고서 (2025.12)",
            ),
            AccountingNoteChapter(
                corp_code="00126380", bsns_year=2024, fs_div="CFS",
                rcept_no="20250301000001", source_type="business_report",
                note_no="2", section_type="policy", body="before",
            ),
            AccountingNoteChapter(
                corp_code="00126380", bsns_year=2025, fs_div="CFS",
                rcept_no="20260301000001", source_type="business_report",
                note_no="2", section_type="policy", body="after",
            ),
        ])
        session.commit()

    snapshot = readiness.auditor_feature_readiness_snapshot(year=2025, market="KOSPI")

    assert snapshot["feature_status"]["accounting_policy_changes"] != "usable"
    assert snapshot["counts"]["policy_change_excluded_unproven"] == 1
    assert snapshot["counts"]["policy_change_comparable_companies"] == 0


def test_policy_change_readiness_rejects_whitespace_wrapped_current_annual_receipt(
    temp_engine,
):
    """A two-year chapter pair needs exact raw receipts in both years."""
    import kreports.analysis.readiness as readiness
    from kreports.db.models import AccountingNoteChapter, Company, Disclosure

    Session = sessionmaker(bind=temp_engine)
    with Session() as session:
        session.add(Company(corp_code="00126380", corp_name="A", stock_code="005930", market="KOSPI"))
        session.add_all([
            Disclosure(
                rcept_no="20250301000001", corp_code="00126380", corp_name="A",
                disc_date=date(2025, 3, 1), disc_type="A", report_nm="사업보고서 (2024.12)",
            ),
            Disclosure(
                rcept_no=" 20260301000001 ", corp_code="00126380", corp_name="A",
                disc_date=date(2026, 3, 1), disc_type="A", report_nm="사업보고서 (2025.12)",
            ),
            AccountingNoteChapter(
                corp_code="00126380", bsns_year=2024, fs_div="CFS", rcept_no="20250301000001",
                source_type="business_report", note_no="2", section_type="policy", body="before",
            ),
            AccountingNoteChapter(
                corp_code="00126380", bsns_year=2025, fs_div="CFS", rcept_no=" 20260301000001 ",
                source_type="business_report", note_no="2", section_type="policy", body="after",
            ),
        ])
        session.commit()

    snapshot = readiness.auditor_feature_readiness_snapshot(year=2025, market="KOSPI")

    assert snapshot["feature_status"]["accounting_policy_changes"] != "usable"
    assert snapshot["counts"]["policy_change_excluded_unproven"] == 1
    assert snapshot["counts"]["policy_change_comparable_companies"] == 0


def test_policy_change_readiness_rejects_historical_pair_without_requested_year(
    temp_engine,
):
    """A proven 2022/2023 pair cannot satisfy requested-year 2025 readiness."""
    import kreports.analysis.readiness as readiness
    from kreports.db.models import AccountingNoteChapter, Company, Disclosure

    Session = sessionmaker(bind=temp_engine)
    with Session() as session:
        session.add(Company(
            corp_code="00126380", corp_name="A", stock_code="005930",
            market="KOSPI",
        ))
        session.add_all([
            Disclosure(
                rcept_no="20230301000001", corp_code="00126380", corp_name="A",
                disc_date=date(2023, 3, 1), disc_type="A",
                report_nm="사업보고서 (2022.12)",
            ),
            Disclosure(
                rcept_no="20240301000001", corp_code="00126380", corp_name="A",
                disc_date=date(2024, 3, 1), disc_type="A",
                report_nm="사업보고서 (2023.12)",
            ),
            AccountingNoteChapter(
                corp_code="00126380", bsns_year=2022, fs_div="CFS",
                rcept_no="20230301000001", source_type="business_report",
                note_no="2", section_type="policy", body="2022 policy",
            ),
            AccountingNoteChapter(
                corp_code="00126380", bsns_year=2023, fs_div="CFS",
                rcept_no="20240301000001", source_type="business_report",
                note_no="2", section_type="policy", body="2023 policy",
            ),
        ])
        session.commit()

    snapshot = readiness.auditor_feature_readiness_snapshot(
        year=2025,
        market="KOSPI",
    )

    assert snapshot["feature_status"]["accounting_policy_changes"] != "usable"
    assert snapshot["counts"]["accounting_policy_change_chapters"] == 0
    assert snapshot["counts"]["policy_change_comparable_companies"] == 0
    assert snapshot["counts"]["policy_change_excluded_missing_requested_year"] == 1
