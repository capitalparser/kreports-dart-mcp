from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection


_logger = logging.getLogger(__name__)


class SchemaDriftError(RuntimeError):
    """Raised when an applied revision no longer matches its source."""


@dataclass(frozen=True)
class Migration:
    revision: str
    description: str
    statements: tuple[str, ...]


MIGRATIONS = (
    Migration(
        revision="20260711_01_quality_contract",
        description="Add schema, dataset, and quality contract tables",
        statements=(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
              revision VARCHAR(40) PRIMARY KEY,
              checksum VARCHAR(64) NOT NULL,
              description VARCHAR(300) NOT NULL,
              applied_at DATETIME NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS dataset_manifest (
              manifest_id VARCHAR(80) PRIMARY KEY,
              schema_version VARCHAR(40) NOT NULL,
              dataset_version VARCHAR(80) NOT NULL,
              generated_at DATETIME NOT NULL,
              year_from SMALLINT,
              year_to SMALLINT,
              company_count INTEGER NOT NULL,
              disclosure_count INTEGER NOT NULL,
              evidence_document_count INTEGER NOT NULL,
              quality_snapshot_json TEXT NOT NULL DEFAULT '{}',
              notes TEXT
            )
            """,
        ),
    ),
    Migration(
        revision="20260711_02_company_year_quality",
        description="Add company-year feature quality ledger",
        statements=(
            """
            CREATE TABLE IF NOT EXISTS company_year_quality (
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
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_company_year_quality_year_market
            ON company_year_quality (bsns_year, market)
            """,
        ),
    ),
    Migration(
        revision="20260711_03_backfill_run_lifecycle",
        description="Add resumable backfill lease and checkpoint columns",
        statements=(
            "ALTER TABLE backfill_runs ADD COLUMN owner_token VARCHAR(64)",
            "ALTER TABLE backfill_runs ADD COLUMN heartbeat_at DATETIME",
            "ALTER TABLE backfill_runs ADD COLUMN checkpoint_json TEXT NOT NULL DEFAULT '{}'",
            "ALTER TABLE backfill_runs ADD COLUMN attempted_count INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE backfill_runs ADD COLUMN saved_count INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE backfill_runs ADD COLUMN no_data_count INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE backfill_runs ADD COLUMN error_count INTEGER NOT NULL DEFAULT 0",
        ),
    ),
    Migration(
        revision="20260711_04_backfill_owner_identity",
        description="Enforce one active backfill owner and process identity",
        statements=(
            "ALTER TABLE backfill_runs ADD COLUMN lease_key VARCHAR(160)",
            "ALTER TABLE backfill_runs ADD COLUMN owner_host VARCHAR(255)",
            "ALTER TABLE backfill_runs ADD COLUMN owner_process_start VARCHAR(100)",
            """
            UPDATE backfill_runs
            SET lease_key = printf(
              '%s|%s|%s',
              task_type,
              COALESCE(CAST(year AS TEXT), ''),
              COALESCE(market, '')
            )
            WHERE lease_key IS NULL
            """,
            """
            WITH ranked_active_leases AS (
              SELECT
                id,
                ROW_NUMBER() OVER (
                  PARTITION BY lease_key
                  ORDER BY
                    CASE WHEN heartbeat_at IS NULL THEN 1 ELSE 0 END,
                    heartbeat_at DESC,
                    id DESC
                ) AS active_rank
              FROM backfill_runs
              WHERE status = 'running'
            )
            UPDATE backfill_runs
            SET
              status = 'stale_failed',
              finished_at = COALESCE(
                heartbeat_at,
                started_at,
                CURRENT_TIMESTAMP
              ),
              error_msg = (
                'superseded duplicate active lease during 20260711_04 migration'
              )
            WHERE id IN (
              SELECT id
              FROM ranked_active_leases
              WHERE active_rank > 1
            )
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_backfill_runs_active_lease
            ON backfill_runs (lease_key)
            WHERE status = 'running'
            """,
        ),
    ),
    Migration(
        revision="20260711_05_kam_items",
        description="Add reconstructed key audit matter items",
        statements=(
            """
            CREATE TABLE IF NOT EXISTS kam_items (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              rcept_no VARCHAR(80) NOT NULL,
              dcm_no VARCHAR(20),
              corp_code VARCHAR(8) NOT NULL,
              bsns_year SMALLINT NOT NULL,
              source_type VARCHAR(30) NOT NULL,
              ordinal SMALLINT NOT NULL,
              title VARCHAR(500),
              normalized_topic VARCHAR(80),
              reason_text TEXT,
              audit_response_text TEXT,
              related_note_references_json TEXT NOT NULL DEFAULT '[]',
              full_body_hash VARCHAR(40) NOT NULL,
              full_body_length INTEGER NOT NULL DEFAULT 0,
              source_basis VARCHAR(80) NOT NULL,
              parser_version VARCHAR(30) NOT NULL DEFAULT 'v1',
              quality_status VARCHAR(20) NOT NULL,
              fetched_at DATETIME NOT NULL,
              CONSTRAINT uq_kam_item_source_ordinal_body
                UNIQUE (rcept_no, source_type, ordinal, full_body_hash)
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_kam_item_corp_year
            ON kam_items (corp_code, bsns_year)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_kam_item_quality_year
            ON kam_items (bsns_year, quality_status)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_kam_item_receipt
            ON kam_items (rcept_no, source_type)
            """,
        ),
    ),
    Migration(
        revision="20260711_06_audit_procedure_linkage",
        description="Link structured audit procedures to KAM and evidence",
        statements=(
            (
                "ALTER TABLE audit_procedure_items "
                "ADD COLUMN kam_item_id INTEGER REFERENCES kam_items(id)"
            ),
            "ALTER TABLE audit_procedure_items ADD COLUMN method VARCHAR(50)",
            (
                "ALTER TABLE audit_procedure_items "
                "ADD COLUMN assertion_hints_json TEXT"
            ),
            (
                "ALTER TABLE audit_procedure_items "
                "ADD COLUMN linked_metric_keys_json TEXT"
            ),
            (
                "ALTER TABLE audit_procedure_items "
                "ADD COLUMN linked_note_keys_json TEXT"
            ),
            (
                "ALTER TABLE audit_procedure_items "
                "ADD COLUMN linked_event_keys_json TEXT"
            ),
            (
                "ALTER TABLE audit_procedure_items "
                "ADD COLUMN parser_version VARCHAR(30)"
            ),
            (
                "ALTER TABLE audit_procedure_items "
                "ADD COLUMN quality_status VARCHAR(20)"
            ),
            (
                "CREATE INDEX IF NOT EXISTS idx_audit_procedure_kam_item "
                "ON audit_procedure_items (kam_item_id)"
            ),
            (
                "CREATE INDEX IF NOT EXISTS idx_audit_procedure_method_year "
                "ON audit_procedure_items (method, bsns_year)"
            ),
        ),
    ),
    Migration(
        revision="20260711_07_audit_fee_availability",
        description="Add typed audit fee and hour availability provenance",
        statements=(
            "ALTER TABLE audit_fees ADD COLUMN contract_fee_m INTEGER",
            "ALTER TABLE audit_fees ADD COLUMN contract_hours INTEGER",
            "ALTER TABLE audit_fees ADD COLUMN actual_fee_m INTEGER",
            "ALTER TABLE audit_fees ADD COLUMN actual_hours INTEGER",
            "ALTER TABLE audit_fees ADD COLUMN source_class VARCHAR(40)",
            "ALTER TABLE audit_fees ADD COLUMN source_rcept_no VARCHAR(80)",
            "ALTER TABLE audit_fees ADD COLUMN source_period VARCHAR(80)",
            "ALTER TABLE audit_fees ADD COLUMN availability_status VARCHAR(40)",
            "ALTER TABLE audit_fees ADD COLUMN quality_status VARCHAR(24)",
            "ALTER TABLE audit_fees ADD COLUMN compatibility_basis VARCHAR(40)",
            "ALTER TABLE audit_fees ADD COLUMN conflict_status VARCHAR(24)",
            "ALTER TABLE audit_fees ADD COLUMN source_observations_json TEXT",
            (
                "CREATE INDEX IF NOT EXISTS idx_audit_fee_availability_year "
                "ON audit_fees (bsns_year, availability_status)"
            ),
        ),
    ),
)


def _checksum(migration: Migration) -> str:
    payload = "\n".join(
        (migration.revision, migration.description, *migration.statements)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _recorded_checksum(connection: Connection, revision: str) -> str | None:
    if "schema_migrations" not in inspect(connection).get_table_names():
        return None
    return connection.execute(
        text(
            "SELECT checksum FROM schema_migrations "
            "WHERE revision = :revision"
        ),
        {"revision": revision},
    ).scalar_one_or_none()


def apply_schema_migrations(connection: Connection) -> list[str]:
    """Apply pending revisions in order and return newly applied revisions."""
    applied: list[str] = []
    for migration in MIGRATIONS:
        transaction = (
            connection.begin_nested()
            if connection.in_transaction()
            else connection.begin()
        )
        with transaction:
            expected_checksum = _checksum(migration)
            recorded_checksum = _recorded_checksum(
                connection,
                migration.revision,
            )
            if recorded_checksum is not None:
                if recorded_checksum != expected_checksum:
                    raise SchemaDriftError(
                        "schema migration checksum drift for "
                        f"{migration.revision}: expected {expected_checksum}, "
                        f"recorded {recorded_checksum}"
                    )
                _logger.debug(
                    "Schema migration already applied: %s",
                    migration.revision,
                )
                continue

            for statement in migration.statements:
                _execute_statement(connection, statement)
            connection.execute(
                text(
                    "INSERT INTO schema_migrations "
                    "(revision, checksum, description, applied_at) "
                    "VALUES (:revision, :checksum, :description, :applied_at)"
                ),
                {
                    "revision": migration.revision,
                    "checksum": expected_checksum,
                    "description": migration.description,
                    "applied_at": datetime.now(timezone.utc),
                },
            )
            applied.append(migration.revision)
            _logger.info(
                "Applied schema migration %s: %s",
                migration.revision,
                migration.description,
            )
    return applied


def _execute_statement(connection: Connection, statement: str) -> None:
    """Execute one statement, tolerating columns already created by metadata.

    Test and fresh-database bootstraps create the current ORM schema before
    replaying the append-only migration ledger. SQLite has no portable
    ``ADD COLUMN IF NOT EXISTS``, so detect that narrow case without changing
    the checksummed migration text.
    """
    normalized = " ".join(statement.split())
    tokens = normalized.split()
    if (
        len(tokens) >= 6
        and tokens[:2] == ["ALTER", "TABLE"]
        and tokens[3:5] == ["ADD", "COLUMN"]
    ):
        table_name = tokens[2]
        column_name = tokens[5]
        existing = {
            column["name"]
            for column in inspect(connection).get_columns(table_name)
        }
        if column_name in existing:
            return
    connection.execute(text(statement))


def current_schema_version(connection: Connection) -> str:
    """Return the newest recorded revision, or an empty string if unversioned."""
    if "schema_migrations" not in inspect(connection).get_table_names():
        return ""
    revision = connection.execute(
        text(
            "SELECT revision FROM schema_migrations "
            "WHERE trim(revision) != '' "
            "ORDER BY revision DESC LIMIT 1"
        )
    ).scalar_one_or_none()
    return str(revision) if revision is not None else ""
