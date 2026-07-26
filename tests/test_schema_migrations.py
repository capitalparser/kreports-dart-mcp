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
