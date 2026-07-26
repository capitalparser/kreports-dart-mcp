import hashlib

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DatabaseError


def test_schema_contract_tables_exist(temp_engine):
    tables = set(inspect(temp_engine).get_table_names())
    assert {"schema_migrations", "dataset_manifest"}.issubset(tables)


def test_schema_migrations_are_idempotent(temp_engine):
    from kreports.db.migrations import apply_schema_migrations

    with temp_engine.begin() as conn:
        first = apply_schema_migrations(conn)
        second = apply_schema_migrations(conn)

    assert first == [
        "20260711_01_quality_contract",
        "20260711_02_company_year_quality",
        "20260711_03_backfill_run_lifecycle",
        "20260711_04_backfill_owner_identity",
    ]
    assert second == []


def test_schema_migration_records_stable_sha256_and_current_version(temp_engine):
    from kreports.db.migrations import (
        MIGRATIONS,
        apply_schema_migrations,
        current_schema_version,
    )

    migration = MIGRATIONS[0]
    expected_checksum = hashlib.sha256(
        "\n".join(
            (migration.revision, migration.description, *migration.statements)
        ).encode("utf-8")
    ).hexdigest()

    with temp_engine.begin() as conn:
        assert apply_schema_migrations(conn) == [
            item.revision for item in MIGRATIONS
        ]
        row = conn.execute(
            text(
                "SELECT revision, checksum, description, applied_at "
                "FROM schema_migrations WHERE revision=:revision"
            ),
            {"revision": migration.revision},
        ).mappings().one()
        assert current_schema_version(conn) == MIGRATIONS[-1].revision

    assert row["revision"] == "20260711_01_quality_contract"
    assert row["checksum"] == expected_checksum
    assert row["description"] == "Add schema, dataset, and quality contract tables"
    assert row["applied_at"] is not None


def test_schema_migration_checksum_drift_fails_without_rewriting_record(temp_engine):
    from kreports.db.migrations import (
        MIGRATIONS,
        SchemaDriftError,
        apply_schema_migrations,
    )

    revision = MIGRATIONS[0].revision
    with temp_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO schema_migrations "
                "(revision, checksum, description, applied_at) "
                "VALUES (:revision, :checksum, :description, CURRENT_TIMESTAMP)"
            ),
            {
                "revision": revision,
                "checksum": "0" * 64,
                "description": "tampered",
            },
        )

    with pytest.raises(SchemaDriftError, match=revision):
        with temp_engine.begin() as conn:
            apply_schema_migrations(conn)

    with temp_engine.connect() as conn:
        assert (
            conn.execute(
                text(
                    "SELECT checksum FROM schema_migrations "
                    "WHERE revision = :revision"
                ),
                {"revision": revision},
            ).scalar_one()
            == "0" * 64
        )


def test_schema_migration_rolls_back_all_statements_after_mid_revision_failure(
    temp_engine,
    monkeypatch,
):
    import kreports.db.migrations as migrations_module
    from kreports.db.migrations import Migration, apply_schema_migrations

    broken = Migration(
        revision="20260711_test_atomic",
        description="Exercise atomic rollback",
        statements=(
            "CREATE TABLE migration_atomic_probe (value INTEGER NOT NULL)",
            "INSERT INTO migration_atomic_probe (value) VALUES (1)",
            "THIS IS NOT VALID SQL",
        ),
    )
    monkeypatch.setattr(migrations_module, "MIGRATIONS", (broken,))

    with pytest.raises(DatabaseError, match="syntax"):
        with temp_engine.begin() as conn:
            apply_schema_migrations(conn)

    assert "migration_atomic_probe" not in inspect(temp_engine).get_table_names()
    with temp_engine.connect() as conn:
        recorded = conn.execute(
            text(
                "SELECT revision FROM schema_migrations "
                "WHERE revision = :revision"
            ),
            {"revision": broken.revision},
        ).scalar_one_or_none()
    assert recorded is None


def test_schema_migrations_apply_in_declared_order(temp_engine, monkeypatch):
    import kreports.db.migrations as migrations_module
    from kreports.db.migrations import Migration, apply_schema_migrations

    first = Migration(
        revision="20260711_test_01",
        description="Create ordered probe",
        statements=(
            "CREATE TABLE migration_order_probe "
            "(position INTEGER PRIMARY KEY)",
            "INSERT INTO migration_order_probe (position) VALUES (1)",
        ),
    )
    second = Migration(
        revision="20260711_test_02",
        description="Append ordered probe",
        statements=(
            "INSERT INTO migration_order_probe (position) VALUES (2)",
        ),
    )
    monkeypatch.setattr(
        migrations_module,
        "MIGRATIONS",
        (first, second),
    )

    with temp_engine.begin() as conn:
        applied = apply_schema_migrations(conn)
        positions = conn.execute(
            text(
                "SELECT position FROM migration_order_probe "
                "ORDER BY position"
            )
        ).scalars().all()

    assert applied == [first.revision, second.revision]
    assert positions == [1, 2]


def test_backfill_owner_migration_is_append_only_and_enforces_active_lease(
    temp_engine,
):
    from kreports.db.migrations import MIGRATIONS, _checksum

    assert _checksum(MIGRATIONS[2]) == (
        "b5a958e21c751e72e4243b5f4a35b03ff41f313a87e5e058e7a9623bfaf4f324"
    )
    assert _checksum(MIGRATIONS[3]) == (
        "021162b6c422573f7741f8cf271c2b83f55df4d1909b2f424216b8aea428b24b"
    )
    indexes = {
        item["name"]: item
        for item in inspect(temp_engine).get_indexes("backfill_runs")
    }
    assert indexes["uq_backfill_runs_active_lease"]["unique"] == 1
    assert (
        indexes["uq_backfill_runs_active_lease"]["dialect_options"][
            "sqlite_where"
        ].text
        == "status = 'running'"
    )


def test_backfill_owner_migration_repairs_duplicate_running_leases_atomically(
    tmp_path,
    monkeypatch,
):
    import kreports.db.migrations as migrations_module
    from kreports.db.migrations import MIGRATIONS, apply_schema_migrations

    legacy = create_engine(f"sqlite:///{tmp_path / 'duplicate-leases.db'}")
    with legacy.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE schema_migrations (
                  revision VARCHAR(40) PRIMARY KEY,
                  checksum VARCHAR(64) NOT NULL,
                  description VARCHAR(300) NOT NULL,
                  applied_at DATETIME NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE backfill_runs (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  task_type VARCHAR(50) NOT NULL,
                  year SMALLINT,
                  market VARCHAR(10),
                  status VARCHAR(20) NOT NULL,
                  pid INTEGER,
                  owner_token VARCHAR(64),
                  heartbeat_at DATETIME,
                  checkpoint_json TEXT NOT NULL DEFAULT '{}',
                  attempted_count INTEGER NOT NULL DEFAULT 0,
                  saved_count INTEGER NOT NULL DEFAULT 0,
                  no_data_count INTEGER NOT NULL DEFAULT 0,
                  error_count INTEGER NOT NULL DEFAULT 0,
                  params_json TEXT,
                  summary_json TEXT,
                  error_msg TEXT,
                  started_at DATETIME NOT NULL,
                  finished_at DATETIME
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO backfill_runs
                  (id, task_type, year, market, status, heartbeat_at, started_at)
                VALUES
                  (1, 'financials', 2024, 'KOSPI', 'running',
                   '2026-07-26 09:00:00', '2026-07-26 08:00:00'),
                  (2, 'financials', 2024, 'KOSPI', 'running',
                   '2026-07-26 10:00:00', '2026-07-26 08:00:00'),
                  (3, 'financials', 2024, 'KOSPI', 'running',
                   '2026-07-26 10:00:00', '2026-07-26 08:00:00'),
                  (4, 'financials', 2024, 'KOSDAQ', 'running',
                   '2026-07-26 09:00:00', '2026-07-26 08:00:00')
                """
            )
        )

    monkeypatch.setattr(migrations_module, "MIGRATIONS", (MIGRATIONS[3],))
    with legacy.begin() as conn:
        assert apply_schema_migrations(conn) == [MIGRATIONS[3].revision]
        assert apply_schema_migrations(conn) == []
        rows = conn.execute(
            text(
                """
                SELECT id, lease_key, status, finished_at, error_msg
                FROM backfill_runs
                ORDER BY id
                """
            )
        ).mappings().all()

    # Newest heartbeat wins; newest id breaks heartbeat ties.
    assert [row["id"] for row in rows if row["status"] == "running"] == [3, 4]
    for row in rows[:2]:
        assert row["status"] == "stale_failed"
        assert row["finished_at"] is not None
        assert "duplicate active lease" in row["error_msg"]
    assert rows[2]["lease_key"] == "financials|2024|KOSPI"
    assert rows[3]["lease_key"] == "financials|2024|KOSDAQ"


def test_init_db_stops_on_schema_drift(tmp_path, monkeypatch):
    import kreports.db.engine as engine_module
    from kreports.db.migrations import MIGRATIONS, SchemaDriftError
    from kreports.db.models import Base

    isolated = create_engine(f"sqlite:///{tmp_path / 'drift.db'}")
    Base.metadata.create_all(isolated)
    with isolated.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO schema_migrations "
                "(revision, checksum, description, applied_at) "
                "VALUES (:revision, :checksum, 'tampered', CURRENT_TIMESTAMP)"
            ),
            {"revision": MIGRATIONS[0].revision, "checksum": "f" * 64},
        )

    monkeypatch.setattr(engine_module, "engine", isolated)
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")

    with pytest.raises(SchemaDriftError):
        engine_module.init_db()
