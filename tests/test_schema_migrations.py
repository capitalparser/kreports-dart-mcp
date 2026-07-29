import hashlib

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DatabaseError, IntegrityError

REVISION_08_CHECKSUMS = {
    "20260711_01_quality_contract": (
        "f538065ca8ca190f28ba13b436a3dd8e1135591bcef1ac05d2e5e77464dfd6aa"
    ),
    "20260711_02_company_year_quality": (
        "797837a4df0a92135542e376db48a9a7078bd9da3abd7a950b71243fda6348eb"
    ),
    "20260711_03_backfill_run_lifecycle": (
        "b5a958e21c751e72e4243b5f4a35b03ff41f313a87e5e058e7a9623bfaf4f324"
    ),
    "20260711_04_backfill_owner_identity": (
        "021162b6c422573f7741f8cf271c2b83f55df4d1909b2f424216b8aea428b24b"
    ),
    "20260711_05_kam_items": (
        "0fa52c82a3c4807885b757734417f5069e04698c954cabb414d67b7e4ac84d06"
    ),
    "20260711_06_audit_procedure_linkage": (
        "d35015b9c185fcf69b62fcf74224cc21b2607ea61047a0505b588bbc1e8cd637"
    ),
    "20260711_07_audit_fee_availability": (
        "05a32077fd271047be3c5ef964208aad731c6a3dee7a9b9686b06478abb0256c"
    ),
    "20260711_08_group_audit_graph": (
        "2064e12d09a4d1376b25f244813204357db7f0ca6462f164d3ab7f090cd46df8"
    ),
}


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
        "20260711_05_kam_items",
        "20260711_06_audit_procedure_linkage",
        "20260711_07_audit_fee_availability",
        "20260711_08_group_audit_graph",
        "20260711_09_audit_fee_observations",
        "20260711_10_financial_compact_provenance",
        "20260711_11_company_year_quality_freshness",
    ]
    assert second == []


def test_revision_08_database_upgrades_to_foundation_without_rewriting_rows(
    tmp_path,
):
    """Catch an additive foundation migration that loses revision-08 evidence."""
    from kreports.db.migrations import (
        MIGRATIONS,
        _checksum,
        apply_schema_migrations,
    )

    assert {item.revision: _checksum(item) for item in MIGRATIONS[:8]} == (
        REVISION_08_CHECKSUMS
    )
    legacy = create_engine(f"sqlite:///{tmp_path / 'revision-08-foundation.db'}")
    with legacy.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys = ON")
        connection.execute(text("""
            CREATE TABLE schema_migrations (
              revision VARCHAR(40) PRIMARY KEY,
              checksum VARCHAR(64) NOT NULL,
              description VARCHAR(300) NOT NULL,
              applied_at DATETIME NOT NULL
            )
        """))
        connection.execute(text("""
            CREATE TABLE audit_fees (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              corp_code VARCHAR(8) NOT NULL,
              bsns_year SMALLINT NOT NULL,
              auditor_nm VARCHAR(100),
              audit_fee_m INTEGER,
              audit_hours INTEGER,
              non_audit_fee_m INTEGER,
              non_audit_hours INTEGER,
              nas_ratio FLOAT,
              independence_risk_flag BOOLEAN,
              fetched_at DATETIME NOT NULL,
              contract_fee_m INTEGER,
              contract_hours INTEGER,
              actual_fee_m INTEGER,
              actual_hours INTEGER,
              source_class VARCHAR(40),
              source_rcept_no VARCHAR(80),
              source_period VARCHAR(80),
              availability_status VARCHAR(40),
              quality_status VARCHAR(24),
              compatibility_basis VARCHAR(40),
              conflict_status VARCHAR(24),
              source_observations_json TEXT
            )
        """))
        connection.execute(text("""
            CREATE TABLE financial_facts_compact (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              corp_code VARCHAR(8) NOT NULL,
              bsns_year SMALLINT NOT NULL,
              fs_div VARCHAR(3) NOT NULL,
              metric_key VARCHAR(50) NOT NULL,
              metric_name VARCHAR(200) NOT NULL,
              amount BIGINT,
              source_account_id VARCHAR(200),
              source_account_nm VARCHAR(300),
              fetched_at DATETIME NOT NULL,
              CONSTRAINT uq_financial_facts_compact
                UNIQUE (corp_code, bsns_year, fs_div, metric_key)
            )
        """))
        connection.execute(text("""
            CREATE TABLE company_year_quality (
              corp_code VARCHAR(8) NOT NULL,
              bsns_year SMALLINT NOT NULL,
              market VARCHAR(10),
              financial_core_status VARCHAR(24) NOT NULL,
              auditor_status VARCHAR(24) NOT NULL,
              audit_fee_status VARCHAR(24) NOT NULL,
              policy_status VARCHAR(24) NOT NULL,
              kam_status VARCHAR(24) NOT NULL,
              audit_procedure_status VARCHAR(24) NOT NULL,
              group_audit_status VARCHAR(24) NOT NULL,
              investor_grade VARCHAR(1) NOT NULL,
              auditor_grade VARCHAR(1) NOT NULL,
              group_audit_grade VARCHAR(1) NOT NULL,
              blockers_json TEXT NOT NULL DEFAULT '[]',
              quality_version VARCHAR(20) NOT NULL DEFAULT 'v1',
              updated_at DATETIME NOT NULL,
              PRIMARY KEY (corp_code, bsns_year)
            )
        """))
        connection.execute(text("""
            INSERT INTO audit_fees (
              corp_code, bsns_year, fetched_at, contract_fee_m, actual_fee_m
            ) VALUES ('00126380', 2025, '2026-07-29 00:00:00', 1000, 2000)
        """))
        connection.execute(text("""
            INSERT INTO financial_facts_compact (
              corp_code, bsns_year, fs_div, metric_key, metric_name, amount,
              fetched_at
            ) VALUES (
              '00126380', 2025, 'CFS', 'revenue', 'Revenue', 333000,
              '2026-07-29 00:00:00'
            )
        """))
        connection.execute(text("""
            INSERT INTO company_year_quality (
              corp_code, bsns_year, financial_core_status, auditor_status,
              audit_fee_status, policy_status, kam_status,
              audit_procedure_status, group_audit_status, investor_grade,
              auditor_grade, group_audit_grade, updated_at
            ) VALUES (
              '00126380', 2025, 'available', 'available', 'available',
              'available', 'available', 'available', 'available', 'A', 'B',
              'C', '2026-07-29 00:00:00'
            )
        """))
        for migration in MIGRATIONS[:8]:
            connection.execute(
                text("""
                    INSERT INTO schema_migrations
                    (revision, checksum, description, applied_at)
                    VALUES (:revision, :checksum, :description, CURRENT_TIMESTAMP)
                """),
                {
                    "revision": migration.revision,
                    "checksum": REVISION_08_CHECKSUMS[migration.revision],
                    "description": migration.description,
                },
            )

    with legacy.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys = ON")
        applied = apply_schema_migrations(connection)
        second_applied = apply_schema_migrations(connection)
        seeded_audit_fee = connection.execute(text("""
            SELECT corp_code, bsns_year, contract_fee_m, actual_fee_m
            FROM audit_fees
        """)).one()
        seeded_compact = connection.execute(text("""
            SELECT corp_code, bsns_year, metric_key, amount
            FROM financial_facts_compact
        """)).one()
        seeded_quality = connection.execute(text("""
            SELECT corp_code, bsns_year, investor_grade, input_fingerprint
            FROM company_year_quality
        """)).one()
        recorded_ledger = dict(connection.execute(text("""
            SELECT revision, checksum
            FROM schema_migrations
        """)).all())
        revision_11 = connection.execute(text("""
            SELECT revision FROM schema_migrations
            WHERE revision = :revision
        """), {"revision": MIGRATIONS[10].revision}).scalar_one()
        foreign_key_check = connection.exec_driver_sql(
            "PRAGMA foreign_key_check"
        ).fetchall()
        quick_check = connection.exec_driver_sql("PRAGMA quick_check").scalar_one()

    assert applied == [
        "20260711_09_audit_fee_observations",
        "20260711_10_financial_compact_provenance",
        "20260711_11_company_year_quality_freshness",
    ]
    assert second_applied == []
    assert seeded_audit_fee == ("00126380", 2025, 1000, 2000)
    assert seeded_compact == ("00126380", 2025, "revenue", 333_000)
    assert seeded_quality == ("00126380", 2025, "A", "")
    assert {
        item.revision: recorded_ledger[item.revision] for item in MIGRATIONS[:8]
    } == REVISION_08_CHECKSUMS
    # SQLite preserves the full value despite the legacy VARCHAR(40) affinity.
    assert revision_11 == "20260711_11_company_year_quality_freshness"
    assert len(revision_11) == 42
    assert foreign_key_check == []
    assert quick_check == "ok"


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
    assert _checksum(MIGRATIONS[4]) == (
        "0fa52c82a3c4807885b757734417f5069e04698c954cabb414d67b7e4ac84d06"
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


def test_kam_item_migration_appends_matter_level_provenance(temp_engine):
    from kreports.db.migrations import MIGRATIONS, _checksum

    assert [migration.revision for migration in MIGRATIONS[:4]] == [
        "20260711_01_quality_contract",
        "20260711_02_company_year_quality",
        "20260711_03_backfill_run_lifecycle",
        "20260711_04_backfill_owner_identity",
    ]
    assert _checksum(MIGRATIONS[2]) == (
        "b5a958e21c751e72e4243b5f4a35b03ff41f313a87e5e058e7a9623bfaf4f324"
    )
    assert _checksum(MIGRATIONS[3]) == (
        "021162b6c422573f7741f8cf271c2b83f55df4d1909b2f424216b8aea428b24b"
    )
    columns = {
        column["name"]
        for column in inspect(temp_engine).get_columns("kam_items")
    }
    assert {
        "rcept_no",
        "dcm_no",
        "corp_code",
        "bsns_year",
        "source_type",
        "ordinal",
        "title",
        "normalized_topic",
        "reason_text",
        "audit_response_text",
        "related_note_references_json",
        "full_body_hash",
        "full_body_length",
        "source_basis",
        "parser_version",
        "quality_status",
        "fetched_at",
    }.issubset(columns)


def test_audit_procedure_linkage_migration_is_append_only_and_nullable(temp_engine):
    from kreports.db.migrations import MIGRATIONS, _checksum

    assert _checksum(MIGRATIONS[2]) == (
        "b5a958e21c751e72e4243b5f4a35b03ff41f313a87e5e058e7a9623bfaf4f324"
    )
    assert _checksum(MIGRATIONS[3]) == (
        "021162b6c422573f7741f8cf271c2b83f55df4d1909b2f424216b8aea428b24b"
    )
    assert MIGRATIONS[5].revision == "20260711_06_audit_procedure_linkage"
    columns = {
        column["name"]: column
        for column in inspect(temp_engine).get_columns("audit_procedure_items")
    }
    assert {
        "kam_item_id",
        "method",
        "assertion_hints_json",
        "linked_metric_keys_json",
        "linked_note_keys_json",
        "linked_event_keys_json",
        "parser_version",
        "quality_status",
    }.issubset(columns)
    assert all(
        columns[name]["nullable"]
        for name in {
            "kam_item_id",
            "method",
            "assertion_hints_json",
            "linked_metric_keys_json",
            "linked_note_keys_json",
            "linked_event_keys_json",
            "parser_version",
            "quality_status",
        }
    )


def test_audit_fee_availability_migration_is_append_only_and_nullable(temp_engine):
    from kreports.db.migrations import MIGRATIONS, _checksum

    assert _checksum(MIGRATIONS[2]) == (
        "b5a958e21c751e72e4243b5f4a35b03ff41f313a87e5e058e7a9623bfaf4f324"
    )
    assert _checksum(MIGRATIONS[3]) == (
        "021162b6c422573f7741f8cf271c2b83f55df4d1909b2f424216b8aea428b24b"
    )
    assert _checksum(MIGRATIONS[4]) == (
        "0fa52c82a3c4807885b757734417f5069e04698c954cabb414d67b7e4ac84d06"
    )
    assert _checksum(MIGRATIONS[5]) == (
        "d35015b9c185fcf69b62fcf74224cc21b2607ea61047a0505b588bbc1e8cd637"
    )
    assert MIGRATIONS[6].revision == "20260711_07_audit_fee_availability"
    columns = {
        column["name"]: column
        for column in inspect(temp_engine).get_columns("audit_fees")
    }
    expected = {
        "contract_fee_m",
        "contract_hours",
        "actual_fee_m",
        "actual_hours",
        "source_class",
        "source_rcept_no",
        "source_period",
        "availability_status",
        "quality_status",
        "compatibility_basis",
        "conflict_status",
        "source_observations_json",
    }
    assert expected.issubset(columns)
    assert all(columns[name]["nullable"] for name in expected)


def test_audit_fee_observation_migration_adds_immutable_claim_store(temp_engine):
    from kreports.db.migrations import MIGRATIONS

    assert MIGRATIONS[8].revision == "20260711_09_audit_fee_observations"
    columns = {
        item["name"]: item
        for item in inspect(temp_engine).get_columns("audit_fee_observations")
    }
    assert {
        "observation_hash", "source_slot_hash", "corp_code", "bsns_year",
        "source_class", "source_rcept_no", "source_period",
        "contract_fee_m", "contract_hours", "actual_fee_m", "actual_hours",
        "auditor_nm", "availability_status", "quality_status",
        "displayed_unit", "raw_values_json", "source_status",
        "source_message", "source_eligibility", "limitations_json",
        "parser_version", "is_current", "supersedes_hash", "observed_at",
    } == set(columns)
    foreign_keys = inspect(temp_engine).get_foreign_keys("audit_fee_observations")
    assert foreign_keys == [
        {
            "name": None,
            "constrained_columns": ["supersedes_hash"],
            "referred_schema": None,
            "referred_table": "audit_fee_observations",
            "referred_columns": ["observation_hash"],
            "options": {},
        }
    ]
    indexes = {
        item["name"]: item
        for item in inspect(temp_engine).get_indexes("audit_fee_observations")
    }
    assert indexes["uq_audit_fee_observation_current_slot"]["unique"] == 1
    assert (
        indexes["uq_audit_fee_observation_current_slot"]["dialect_options"][
            "sqlite_where"
        ].text
        == "is_current = 1"
    )
    assert {
        "idx_audit_fee_observation_corp_year",
        "idx_audit_fee_observation_receipt",
        "idx_audit_fee_observation_year_quality",
    }.issubset(indexes)
    assert indexes["idx_audit_fee_observation_corp_year"]["column_names"] == [
        "corp_code",
        "bsns_year",
    ]
    assert indexes["idx_audit_fee_observation_receipt"]["column_names"] == [
        "source_rcept_no"
    ]
    assert indexes["idx_audit_fee_observation_year_quality"]["column_names"] == [
        "bsns_year",
        "quality_status",
    ]


def test_financial_compact_provenance_migration_is_additive(temp_engine):
    from kreports.db.migrations import MIGRATIONS

    assert MIGRATIONS[9].revision == "20260711_10_financial_compact_provenance"
    columns = {
        item["name"]: item
        for item in inspect(temp_engine).get_columns("financial_facts_compact")
    }
    assert {
        "source_table", "unit", "period_type", "citation_rcept_no",
        "citation_report_nm", "citation_basis", "quality_status",
    }.issubset(columns)
    assert columns["citation_basis"]["default"].strip("'") == "uncitable"
    assert columns["quality_status"]["default"].strip("'") == "limited"


def test_financial_compact_provenance_migration_upgrades_legacy_table(
    tmp_path,
    monkeypatch,
):
    import kreports.db.migrations as migrations_module
    from kreports.db.migrations import (
        MIGRATIONS,
        _checksum,
        apply_schema_migrations,
    )

    legacy = create_engine(f"sqlite:///{tmp_path / 'legacy-compact.db'}")
    with legacy.begin() as conn:
        conn.execute(text("""
            CREATE TABLE financial_facts_compact (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              corp_code VARCHAR(8) NOT NULL,
              bsns_year SMALLINT NOT NULL,
              fs_div VARCHAR(3) NOT NULL,
              metric_key VARCHAR(50) NOT NULL,
              metric_name VARCHAR(200) NOT NULL,
              amount BIGINT,
              source_account_id VARCHAR(200),
              source_account_nm VARCHAR(300),
              fetched_at DATETIME NOT NULL,
              CONSTRAINT uq_financial_facts_compact
                UNIQUE (corp_code, bsns_year, fs_div, metric_key)
            )
        """))
        conn.execute(text("""
            CREATE TABLE schema_migrations (
              revision VARCHAR(40) PRIMARY KEY,
              checksum VARCHAR(64) NOT NULL,
              description VARCHAR(300) NOT NULL,
              applied_at DATETIME NOT NULL
            )
        """))
        for migration in MIGRATIONS[:9]:
            conn.execute(
                text("""
                    INSERT INTO schema_migrations
                    (revision, checksum, description, applied_at)
                    VALUES (:revision, :checksum, :description, CURRENT_TIMESTAMP)
                """),
                {
                    "revision": migration.revision,
                    "checksum": _checksum(migration),
                    "description": migration.description,
                },
            )

    monkeypatch.setattr(migrations_module, "MIGRATIONS", MIGRATIONS[:10])
    with legacy.begin() as conn:
        assert apply_schema_migrations(conn) == [MIGRATIONS[9].revision]

    columns = {
        item["name"]: item
        for item in inspect(legacy).get_columns("financial_facts_compact")
    }
    assert {
        "source_table", "unit", "period_type", "citation_rcept_no",
        "citation_report_nm", "citation_basis", "quality_status",
    }.issubset(columns)
    assert columns["citation_basis"]["default"].strip("'") == "uncitable"
    assert columns["quality_status"]["default"].strip("'") == "limited"
    assert (
        "corp_code",
        "bsns_year",
        "fs_div",
        "metric_key",
    ) in {
        tuple(item["column_names"])
        for item in inspect(legacy).get_unique_constraints("financial_facts_compact")
    }


def test_company_year_quality_freshness_migration_is_additive(temp_engine):
    from kreports.db.migrations import MIGRATIONS

    assert MIGRATIONS[10].revision == (
        "20260711_11_company_year_quality_freshness"
    )
    columns = {
        item["name"]: item
        for item in inspect(temp_engine).get_columns("company_year_quality")
    }
    assert columns["input_fingerprint"]["nullable"] is False
    assert columns["evidence_summary_json"]["nullable"] is False


def test_company_year_quality_freshness_migration_upgrades_revision_10_row(
    tmp_path,
):
    from kreports.db.migrations import (
        MIGRATIONS,
        _checksum,
        apply_schema_migrations,
    )

    legacy = create_engine(f"sqlite:///{tmp_path / 'revision-10-quality.db'}")
    with legacy.begin() as conn:
        conn.execute(text("""
            CREATE TABLE company_year_quality (
              corp_code VARCHAR(8) NOT NULL,
              bsns_year SMALLINT NOT NULL,
              market VARCHAR(10),
              financial_core_status VARCHAR(24) NOT NULL,
              auditor_status VARCHAR(24) NOT NULL,
              audit_fee_status VARCHAR(24) NOT NULL,
              policy_status VARCHAR(24) NOT NULL,
              kam_status VARCHAR(24) NOT NULL,
              audit_procedure_status VARCHAR(24) NOT NULL,
              group_audit_status VARCHAR(24) NOT NULL,
              investor_grade VARCHAR(1) NOT NULL,
              auditor_grade VARCHAR(1) NOT NULL,
              group_audit_grade VARCHAR(1) NOT NULL,
              blockers_json TEXT NOT NULL DEFAULT '[]',
              quality_version VARCHAR(20) NOT NULL DEFAULT 'v1',
              updated_at DATETIME NOT NULL,
              PRIMARY KEY (corp_code, bsns_year)
            )
        """))
        conn.execute(text("""
            CREATE TABLE schema_migrations (
              revision VARCHAR(40) PRIMARY KEY,
              checksum VARCHAR(64) NOT NULL,
              description VARCHAR(300) NOT NULL,
              applied_at DATETIME NOT NULL
            )
        """))
        conn.execute(text("""
            INSERT INTO company_year_quality (
              corp_code, bsns_year, financial_core_status, auditor_status,
              audit_fee_status, policy_status, kam_status,
              audit_procedure_status, group_audit_status, investor_grade,
              auditor_grade, group_audit_grade, updated_at
            ) VALUES (
              '00126380', 2025, 'available', 'available', 'available',
              'available', 'available', 'available', 'available', 'A', 'B',
              'C', '2026-07-29 00:00:00'
            )
        """))
        for migration in MIGRATIONS[:10]:
            conn.execute(
                text("""
                    INSERT INTO schema_migrations
                    (revision, checksum, description, applied_at)
                    VALUES (:revision, :checksum, :description, CURRENT_TIMESTAMP)
                """),
                {
                    "revision": migration.revision,
                    "checksum": _checksum(migration),
                    "description": migration.description,
                },
            )

    with legacy.begin() as conn:
        assert apply_schema_migrations(conn) == [MIGRATIONS[10].revision]
        assert apply_schema_migrations(conn) == []
        upgraded = conn.execute(
            text("""
                SELECT corp_code, bsns_year, investor_grade,
                       input_fingerprint, evidence_summary_json
                FROM company_year_quality
            """)
        ).one()

    columns = {
        item["name"]: item
        for item in inspect(legacy).get_columns("company_year_quality")
    }
    assert upgraded == ("00126380", 2025, "A", "", "{}")
    assert columns["input_fingerprint"]["nullable"] is False
    assert columns["input_fingerprint"]["default"].strip("'") == ""
    assert columns["evidence_summary_json"]["nullable"] is False
    assert columns["evidence_summary_json"]["default"].strip("'") == "{}"
    assert tuple(
        inspect(legacy)
        .get_pk_constraint("company_year_quality")["constrained_columns"]
    ) == ("corp_code", "bsns_year")


def test_audit_fee_observation_current_slot_allows_one_current_claim(
    temp_engine,
):
    observation = "audit_fee_observations"
    current_claim = {
        "observation_hash": "a" * 64,
        "source_slot_hash": "slot" * 16,
        "corp_code": "00126380",
        "bsns_year": 2025,
        "source_class": "audit_report",
        "availability_status": "available",
        "quality_status": "verified",
        "raw_values_json": "{}",
        "source_eligibility": "eligible",
        "limitations_json": "[]",
        "parser_version": "v1",
        "is_current": True,
        "observed_at": "2026-07-29 00:00:00",
    }
    duplicate_current_claim = {
        **current_claim,
        "observation_hash": "b" * 64,
    }
    historical_claim = {
        **current_claim,
        "observation_hash": "c" * 64,
        "is_current": False,
    }
    insert = text(
        """
        INSERT INTO audit_fee_observations (
          observation_hash, source_slot_hash, corp_code, bsns_year,
          source_class, availability_status, quality_status, raw_values_json,
          source_eligibility, limitations_json, parser_version, is_current,
          observed_at
        ) VALUES (
          :observation_hash, :source_slot_hash, :corp_code, :bsns_year,
          :source_class, :availability_status, :quality_status, :raw_values_json,
          :source_eligibility, :limitations_json, :parser_version, :is_current,
          :observed_at
        )
        """
    )

    with temp_engine.begin() as conn:
        conn.execute(insert, historical_claim)
        conn.execute(insert, current_claim)

    with pytest.raises(IntegrityError), temp_engine.begin() as conn:
        conn.execute(insert, duplicate_current_claim)

    with temp_engine.connect() as conn:
        assert conn.execute(
            text(
                f"SELECT COUNT(*) FROM {observation} "
                "WHERE source_slot_hash = :source_slot_hash"
            ),
            {"source_slot_hash": current_claim["source_slot_hash"]},
        ).scalar_one() == 2


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
