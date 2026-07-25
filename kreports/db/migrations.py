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
                connection.execute(text(statement))
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
