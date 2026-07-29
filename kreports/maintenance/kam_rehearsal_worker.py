"""Fresh-process, bounded KAM schema-rehearsal actions.

This module deliberately imports only the standard library at module load.
The command-line boundary validates its action before importing settings or an
engine, so an accidental parent ``DB_URL`` can never be bound first.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import stat as stat_module
from typing import Any
from urllib.parse import unquote


YEARS = (2021, 2022, 2023, 2024, 2025)
CANONICAL_STATUSES = {"usable", "limited", "missing", "error"}
KAM_GATED_TOOLS = {
    "build_audit_acceptance_pack",
    "get_audit_report_sections",
    "get_kam_lifecycle",
    "compare_peer_kam_topics",
}
PROFESSIONAL_REHEARSAL_TOOLS: tuple[tuple[str, dict[str, object]], ...] = (
    ("prepare_standard_audit_hours_inputs", {"company": "005930", "year": 2025}),
    ("compare_peer_audit_fees", {"company": "005930", "year": 2025}),
    ("build_audit_acceptance_pack", {"company": "005930", "year": 2025}),
    ("compare_peer_risk_profile", {"company": "005930", "year": 2025}),
    ("get_audit_history", {"company": "005930"}),
    ("get_audit_report_sections", {"company": "005930", "year": 2025}),
    ("search_audit_report_matters", {"company": "005930", "year": 2025}),
    ("compare_peer_audit_report_matters", {"company": "005930", "year": 2025}),
    ("get_kam_lifecycle", {"company": "005930", "start_year": 2021, "end_year": 2025}),
    ("compare_peer_kam_topics", {"company": "005930", "year": 2025}),
    ("get_financial_snapshot", {"company": "005930", "years": 5}),
    ("compare_to_industry_multi", {"company": "005930", "years_back": 5}),
    ("get_investor_signals", {"company": "005930", "years": 5}),
    ("search_disclosure_events", {"company": "005930"}),
    ("get_quality_of_earnings_pack", {"company": "005930", "start_year": 2021, "end_year": 2025}),
    ("get_dcf_input_candidates", {"company": "005930", "start_year": 2021, "end_year": 2025}),
    ("build_dcf_model_pack", {"company": "005930", "base_year": 2025}),
)

_ACTIONS = {
    "migrate",
    "kam-dry-run",
    "kam-rebuild",
    "procedure-index",
    "semantic-snapshot",
    "mcp-validate",
}
_FORBIDDEN_SCHEMA_LITERALS = (
    "no such table",
    "no such column",
    "operationalerror",
    "kam_items",
    "kam_item_id",
    "audit_procedure_items",
)
_PATH_TEXT = re.compile(r"(?:[A-Za-z]:)?/(?:[^\s:'\"]+/)+[^\s:'\"]*")
_MARKER_SCHEMA = "kam-schema-backfill-rehearsal-marker.v1"
_MARKER_FIELDS = {
    "schema_version",
    "run_id",
    "database_path",
    "database_inode",
    "database_device",
    "source_path",
    "source_inode",
    "source_device",
    "source_sha256",
    "clone_initial_sha256",
    "repository_root",
    "rehearsal_dir",
    "filesystem_type",
    "min_free_bytes",
    "hmac_sha256",
}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MIN_FREE_BYTES = 10 * 1024**3
_ACTIVE_DBAPI_CONNECTION: sqlite3.Connection | None = None

KAM_SNAPSHOT_COLUMNS = (
    "id", "rcept_no", "dcm_no", "corp_code", "bsns_year", "source_type",
    "ordinal", "title", "normalized_topic", "reason_text", "audit_response_text",
    "related_note_references_json", "full_body_hash", "full_body_length",
    "source_basis", "parser_version", "quality_status",
)
PROCEDURE_SNAPSHOT_COLUMNS = (
    "id", "rcept_no", "dcm_no", "corp_code", "bsns_year", "source_type",
    "kam_item_id", "kam_topic", "method", "procedure_type", "procedure_text",
    "procedure_hash", "procedure_length", "assertion_hints_json",
    "linked_metric_keys_json", "linked_note_keys_json", "linked_event_keys_json",
    "parser_version", "quality_status", "section_ordinal", "procedure_ordinal",
)


class WorkerActionError(RuntimeError):
    """A stable public failure code for the parent rehearsal orchestrator."""

    def __init__(self, code: str, message: object) -> None:
        self.code = code
        super().__init__(_bounded_message(message))


@dataclass(frozen=True)
class RehearsalBinding:
    """Resolved file identity authenticated by the signed clone receipt."""

    database_path: Path
    database_inode: int
    database_device: int


def _bounded_message(value: object) -> str:
    message = _PATH_TEXT.sub("[path]", str(value).replace("\n", " ").replace("\r", " "))
    return message[:500] or "worker action failed"


def _assert_no_symlink_components(path: Path) -> None:
    if not path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise WorkerActionError(
            "rehearsal_binding_required",
            "database path must be absolute and normalized",
        )
    current = Path(path.anchor)
    try:
        for part in path.parts[1:]:
            current /= part
            if stat_module.S_ISLNK(os.lstat(current).st_mode):
                raise WorkerActionError(
                    "rehearsal_binding_required",
                    "database path must not contain symlinks",
                )
    except OSError as exc:
        raise WorkerActionError(
            "database_unavailable",
            "configured SQLite database is unavailable",
        ) from exc


def _configured_database_path() -> Path:
    url = os.environ.get("DB_URL", "")
    if not url.startswith("sqlite:///"):
        raise WorkerActionError("invalid_database_url", "DB_URL must be an absolute SQLite URL")
    raw_path = unquote(url[len("sqlite:///"):])
    configured_path = Path(raw_path)
    if not configured_path.is_absolute():
        raise WorkerActionError("invalid_database_url", "DB_URL must name an absolute SQLite file")
    _assert_no_symlink_components(configured_path)
    try:
        path = configured_path.resolve(strict=True)
    except OSError as exc:
        raise WorkerActionError("database_unavailable", "configured SQLite database is unavailable") from exc
    path_stat = os.lstat(path)
    if not stat_module.S_ISREG(path_stat.st_mode):
        raise WorkerActionError("database_unavailable", "configured SQLite database is unavailable")
    return path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _require_rehearsal_binding(
    *,
    require_initial_digest: bool,
) -> RehearsalBinding:
    """Validate the pre-import clone capability for every database action."""
    database = _configured_database_path()
    raw_marker = os.environ.get("KREPORTS_REHEARSAL_MARKER", "")
    capability = os.environ.get("KREPORTS_REHEARSAL_CAPABILITY", "")
    if not raw_marker or not _SHA256.fullmatch(capability):
        raise WorkerActionError(
            "rehearsal_binding_required",
            "signed rehearsal capability is required",
        )
    marker = Path(raw_marker)
    if not marker.is_absolute() or marker.is_symlink() or not marker.is_file():
        raise WorkerActionError("rehearsal_binding_required", "rehearsal marker must be a regular absolute file")
    try:
        marker = marker.resolve(strict=True)
        if marker.parent != database.parent or marker.stat().st_size > 8192:
            raise ValueError("marker path or size is invalid")
        payload = json.loads(marker.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or set(payload) != _MARKER_FIELDS:
            raise ValueError("marker schema is invalid")
        signature = payload["hmac_sha256"]
        if not isinstance(signature, str) or not _SHA256.fullmatch(signature):
            raise ValueError("marker signature is invalid")
        signed_fields = {
            field: value
            for field, value in payload.items()
            if field != "hmac_sha256"
        }
        canonical = json.dumps(
            signed_fields,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        expected_signature = hmac.new(
            bytes.fromhex(capability),
            canonical,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected_signature):
            raise ValueError("marker signature mismatch")
        if not isinstance(payload["database_path"], str):
            raise ValueError("marker database path is invalid")
        marker_database = Path(payload["database_path"])
        if not marker_database.is_absolute() or marker_database.resolve(strict=True) != database:
            raise ValueError("marker database path is invalid")
        if payload["schema_version"] != _MARKER_SCHEMA:
            raise ValueError("marker version is invalid")
        if not isinstance(payload["run_id"], str) or not 1 <= len(payload["run_id"]) <= 128:
            raise ValueError("marker run id is invalid")
        stat = database.stat()
        if payload["database_inode"] != stat.st_ino or payload["database_device"] != stat.st_dev:
            raise ValueError("marker database identity is invalid")
        if not isinstance(payload["source_path"], str):
            raise ValueError("marker source path is invalid")
        source = Path(payload["source_path"])
        if not source.is_absolute() or source.is_symlink() or not source.is_file():
            raise ValueError("marker source path is invalid")
        source = source.resolve(strict=True)
        source_stat = source.stat()
        if (
            payload["source_inode"] != source_stat.st_ino
            or payload["source_device"] != source_stat.st_dev
        ):
            raise ValueError("marker source identity is invalid")
        if database == source or (stat.st_ino, stat.st_dev) == (
            source_stat.st_ino,
            source_stat.st_dev,
        ):
            raise ValueError("source database cannot be the rehearsal clone")
        repository = Path(str(payload["repository_root"]))
        rehearsal_dir = Path(str(payload["rehearsal_dir"]))
        if (
            not repository.is_absolute()
            or not repository.is_dir()
            or not rehearsal_dir.is_absolute()
            or not rehearsal_dir.is_dir()
        ):
            raise ValueError("marker protected paths are invalid")
        repository = repository.resolve(strict=True)
        rehearsal_dir = rehearsal_dir.resolve(strict=True)
        if rehearsal_dir != database.parent:
            raise ValueError("marker rehearsal directory is invalid")
        if _is_within(database, source.parent) or _is_within(database, repository):
            raise ValueError("rehearsal database is inside a protected root")
        if payload["filesystem_type"] != "apfs":
            raise ValueError("marker filesystem type is invalid")
        min_free_bytes = payload["min_free_bytes"]
        if (
            not isinstance(min_free_bytes, int)
            or isinstance(min_free_bytes, bool)
            or min_free_bytes < _MIN_FREE_BYTES
        ):
            raise ValueError("marker free-space floor is invalid")
        for field in ("source_sha256", "clone_initial_sha256"):
            if not isinstance(payload[field], str) or not _SHA256.fullmatch(payload[field]):
                raise ValueError("marker digest is invalid")
        if require_initial_digest and _sha256_file(database) != payload["clone_initial_sha256"]:
            raise ValueError("rehearsal clone changed before migration")
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise WorkerActionError("rehearsal_binding_required", "rehearsal marker does not bind this database") from exc
    return RehearsalBinding(
        database_path=database,
        database_inode=stat.st_ino,
        database_device=stat.st_dev,
    )


def _verify_binding_identity(
    binding: RehearsalBinding,
    file_descriptor: int,
) -> None:
    _assert_no_symlink_components(binding.database_path)
    path_stat = os.lstat(binding.database_path)
    descriptor_stat = os.fstat(file_descriptor)
    expected = (binding.database_inode, binding.database_device)
    if (
        not stat_module.S_ISREG(path_stat.st_mode)
        or (path_stat.st_ino, path_stat.st_dev) != expected
        or (descriptor_stat.st_ino, descriptor_stat.st_dev) != expected
    ):
        raise WorkerActionError(
            "rehearsal_binding_required",
            "rehearsal database identity changed",
        )


@contextmanager
def _open_pinned_database(
    binding: RehearsalBinding,
    *,
    collector: bool,
):
    """Hold and verify one file identity while opening the action DBAPI handle."""
    if not hasattr(os, "O_NOFOLLOW"):
        raise WorkerActionError(
            "rehearsal_binding_required",
            "no-follow database open is unavailable",
        )
    flags = (os.O_RDWR if collector else os.O_RDONLY) | os.O_NOFOLLOW
    flags |= getattr(os, "O_CLOEXEC", 0)
    file_descriptor = -1
    connection: sqlite3.Connection | None = None
    try:
        file_descriptor = os.open(binding.database_path, flags)
        lock_kind = fcntl.LOCK_EX if collector else fcntl.LOCK_SH
        fcntl.flock(file_descriptor, lock_kind | fcntl.LOCK_NB)
        _verify_binding_identity(binding, file_descriptor)
        # On APFS, the required non-blocking flock and SQLite's default POSIX
        # locks conflict even within one process.  The signed marker already
        # restricts this worker to APFS, and the held exclusive flock provides
        # the action-wide writer lock, so the one pinned collector connection
        # must not attempt a second, conflicting lock protocol.
        query = "mode=rw&vfs=unix-none" if collector else "mode=ro&immutable=1"
        connection = sqlite3.connect(
            f"{binding.database_path.as_uri()}?{query}",
            uri=True,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(
            "PRAGMA foreign_keys=ON" if collector else "PRAGMA query_only=ON"
        )
        _verify_binding_identity(binding, file_descriptor)
        try:
            yield connection
        finally:
            # Re-check after the action as well as around the DBAPI open.  If
            # an attacker renamed the authenticated path while the pinned
            # connection was in use, replace any lower-level SQLite/SQLAlchemy
            # error with the bounded public binding failure.
            _verify_binding_identity(binding, file_descriptor)
    except WorkerActionError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise WorkerActionError(
            "rehearsal_binding_required",
            "could not pin the rehearsal database identity",
        ) from exc
    finally:
        if connection is not None:
            connection.close()
        if file_descriptor >= 0:
            try:
                fcntl.flock(file_descriptor, fcntl.LOCK_UN)
            finally:
                os.close(file_descriptor)


@contextmanager
def _bound_database_runtime(
    binding: RehearsalBinding,
    *,
    collector: bool,
):
    """Bind every KReports database consumer to one already-open DBAPI handle."""
    global _ACTIVE_DBAPI_CONNECTION

    with _open_pinned_database(binding, collector=collector) as connection:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        from kreports.config import settings
        import kreports.db.engine as engine_module

        original_engine = engine_module.engine
        original_session_local = engine_module.SessionLocal
        original_db_url = settings.db_url
        safe_engine = create_engine(
            "sqlite://",
            creator=lambda: connection,
            poolclass=StaticPool,
            pool_reset_on_return=None,
        )
        engine_module.engine = safe_engine
        engine_module.SessionLocal = sessionmaker(
            bind=safe_engine,
            autocommit=False,
            autoflush=False,
        )
        settings.db_url = f"sqlite:///{binding.database_path}"
        _ACTIVE_DBAPI_CONNECTION = connection
        try:
            yield connection
        finally:
            _ACTIVE_DBAPI_CONNECTION = None
            engine_module.engine = original_engine
            engine_module.SessionLocal = original_session_local
            settings.db_url = original_db_url
            safe_engine.dispose()


def _open_readonly_database() -> sqlite3.Connection:
    if _ACTIVE_DBAPI_CONNECTION is not None:
        return _ACTIVE_DBAPI_CONNECTION
    path = _configured_database_path()
    connection = sqlite3.connect(
        f"{path.as_uri()}?mode=ro&immutable=1",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _table_indexes(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in connection.execute(f"PRAGMA index_list({table})")}


def migration_state() -> dict[str, object]:
    """Inspect the configured SQLite database without changing it."""
    from kreports.db.migrations import MIGRATIONS, _checksum

    required_columns = {
        "kam_items": {"id", "rcept_no", "dcm_no", "corp_code", "bsns_year", "source_type", "ordinal", "related_note_references_json", "full_body_hash", "source_basis", "quality_status"},
        "audit_procedure_items": {"id", "rcept_no", "dcm_no", "corp_code", "bsns_year", "source_type", "kam_item_id", "method", "assertion_hints_json", "linked_metric_keys_json", "linked_note_keys_json", "linked_event_keys_json", "parser_version", "quality_status"},
        "audit_fees": {"contract_fee_m", "contract_hours", "actual_fee_m", "actual_hours", "source_class", "source_rcept_no", "source_period", "availability_status", "quality_status", "compatibility_basis", "conflict_status", "source_observations_json"},
    }
    required_tables = set(required_columns) | {"schema_migrations", "group_entities", "group_relationships", "group_component_metrics"}
    required_indexes = {
        "idx_kam_item_corp_year", "idx_kam_item_quality_year", "idx_kam_item_receipt",
        "idx_audit_procedure_kam_item", "idx_audit_procedure_method_year",
        "idx_audit_fee_availability_year", "idx_group_entity_parent_year",
        "idx_group_relationship_parent_year", "idx_group_metric_parent_year",
    }
    connection = _open_readonly_database()
    try:
        tables = {str(row["name"]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        recorded: dict[str, str] = {}
        if "schema_migrations" in tables:
            recorded = {
                str(row["revision"]): str(row["checksum"])
                for row in connection.execute("SELECT revision, checksum FROM schema_migrations ORDER BY revision")
            }
        expected = {migration.revision: _checksum(migration) for migration in MIGRATIONS}
        checksum_mismatches = [revision for revision, checksum in recorded.items() if expected.get(revision) != checksum]
        missing_tables = sorted(required_tables - tables)
        missing_columns = {
            table: sorted(columns - _table_columns(connection, table))
            for table, columns in required_columns.items() if table in tables and columns - _table_columns(connection, table)
        }
        existing_indexes = set().union(*(_table_indexes(connection, table) for table in tables)) if tables else set()
        foreign_keys = [dict(row) for row in connection.execute("PRAGMA foreign_key_check")]
        quick_check = [str(row[0]) for row in connection.execute("PRAGMA quick_check")]
        pending = [migration.revision for migration in MIGRATIONS if migration.revision not in recorded]
        return {
            "recorded_revisions": [migration.revision for migration in MIGRATIONS if migration.revision in recorded],
            "pending_revisions": pending,
            "checksum_mismatches": checksum_mismatches,
            "schema_complete": not (missing_tables or missing_columns or required_indexes - existing_indexes),
            "missing_tables": missing_tables,
            "missing_columns": missing_columns,
            "missing_indexes": sorted(required_indexes - existing_indexes),
            "quick_check": quick_check,
            "foreign_key_violations": foreign_keys,
        }
    finally:
        if connection is not _ACTIVE_DBAPI_CONNECTION:
            connection.close()


def _require_mode(expected: str, action: str) -> None:
    actual = os.environ.get("KREPORTS_RUNTIME_MODE", "readonly").strip().lower()
    if actual != expected:
        raise WorkerActionError("invalid_runtime_mode", f"{action} requires {expected} runtime mode")


def _bounded_rebuild_result(result: dict[str, Any]) -> dict[str, object]:
    bounded: dict[str, object] = dict(result)
    for field in ("receipts", "errors", "limitations"):
        value = result.get(field)
        if isinstance(value, list):
            bounded[field] = value[:20]
    return bounded


def _validate_state_after_migration(state: dict[str, object]) -> None:
    if (
        state["checksum_mismatches"]
        or state["pending_revisions"]
        or not state["schema_complete"]
        or state["quick_check"] != ["ok"]
        or state["foreign_key_violations"]
    ):
        raise WorkerActionError("migration_failed", "schema migration verification failed")


def _canonical_json_text(value: object) -> object:
    if not isinstance(value, str):
        return value
    try:
        return json.dumps(json.loads(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return value


def _snapshot_rows(connection: sqlite3.Connection, table: str, columns: tuple[str, ...]) -> list[dict[str, object]]:
    present = _table_columns(connection, table)
    missing = set(columns) - present
    if missing:
        raise WorkerActionError("snapshot_failed", f"required snapshot columns are missing from {table}")
    rows: list[dict[str, object]] = []
    query = f"SELECT {', '.join(columns)} FROM {table} ORDER BY {', '.join(columns)}"
    json_columns = {column for column in columns if column.endswith("_json")}
    for row in connection.execute(query):
        rows.append({column: _canonical_json_text(row[column]) if column in json_columns else row[column] for column in columns})
    return rows


def _quality_distribution(rows: list[dict[str, object]]) -> dict[str, dict[str, int]]:
    distribution: dict[str, dict[str, int]] = {}
    for row in rows:
        year = str(row.get("bsns_year"))
        status = str(row.get("quality_status") or "missing")
        distribution.setdefault(year, {})[status] = distribution.setdefault(year, {}).get(status, 0) + 1
    return distribution


def semantic_snapshot() -> dict[str, object]:
    """Return a deterministic, typed KAM/procedure semantic identity snapshot."""
    connection = _open_readonly_database()
    try:
        kam_rows = _snapshot_rows(connection, "kam_items", KAM_SNAPSHOT_COLUMNS)
        procedure_rows = _snapshot_rows(connection, "audit_procedure_items", PROCEDURE_SNAPSHOT_COLUMNS)
        duplicate_logical_identities = [
            dict(row) for row in connection.execute(
                "SELECT rcept_no, source_type, ordinal, COUNT(*) AS count FROM kam_items GROUP BY rcept_no, source_type, ordinal HAVING COUNT(*) > 1"
            )
        ]
        integrity = {
            "orphan_procedure_count": int(connection.execute("SELECT COUNT(*) FROM audit_procedure_items p LEFT JOIN kam_items k ON k.id=p.kam_item_id WHERE p.kam_item_id IS NOT NULL AND k.id IS NULL").fetchone()[0]),
            "cross_receipt_source_ordinal_link_count": int(connection.execute("SELECT COUNT(*) FROM audit_procedure_items p JOIN kam_items k ON k.id=p.kam_item_id WHERE p.rcept_no != k.rcept_no OR p.source_type != k.source_type OR p.section_ordinal != k.ordinal").fetchone()[0]),
            "usable_response_without_procedure_count": int(connection.execute("SELECT COUNT(*) FROM kam_items k WHERE k.quality_status='usable' AND trim(COALESCE(k.audit_response_text, '')) != '' AND NOT EXISTS (SELECT 1 FROM audit_procedure_items p WHERE p.kam_item_id=k.id)").fetchone()[0]),
        }
        payload = {"kam_items": kam_rows, "audit_procedure_items": procedure_rows}
        digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return {
            "kam_count": len(kam_rows),
            "procedure_count": len(procedure_rows),
            "kam_quality_by_year": _quality_distribution(kam_rows),
            "procedure_quality_by_year": _quality_distribution(procedure_rows),
            "duplicate_logical_identities": duplicate_logical_identities,
            "integrity": integrity,
            "semantic_sha256": digest,
        }
    finally:
        if connection is not _ACTIVE_DBAPI_CONNECTION:
            connection.close()


def _public_schema_leak(value: object) -> bool:
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).lower()
    return any(token in rendered for token in _FORBIDDEN_SCHEMA_LITERALS)


def validate_professional_result(result: dict[str, object]) -> dict[str, object]:
    """Validate the public legacy result without inspecting private diagnostics."""
    answer = result.get("answer")
    quality = result.get("data_quality")
    pack = result.get("answer_pack")
    if not isinstance(answer, str) or not isinstance(quality, dict):
        raise WorkerActionError("mcp_invalid_result", "MCP result lacks a public answer or status")
    status = quality.get("status")
    if status not in CANONICAL_STATUSES:
        raise WorkerActionError("mcp_invalid_status", "MCP result has a non-canonical status")
    if _public_schema_leak(result):
        raise WorkerActionError("mcp_schema_not_closed", "public MCP output contains an internal schema error")
    if isinstance(pack, dict):
        pack_quality = pack.get("data_quality")
        if isinstance(pack_quality, dict) and pack_quality.get("status") not in {None, status}:
            raise WorkerActionError("mcp_boundary_mismatch", "answer pack status differs from result status")
    return {"status": str(status), "pack": pack if isinstance(pack, dict) else {}}


def _first_paragraph(answer: str) -> str:
    return answer.split("\n\n", 1)[0][:500]


def validate_professional_mcp() -> dict[str, object]:
    """Exercise the same professional calls through legacy, envelope, and stdio."""
    from kreports.mcp.dispatch import dispatch_tool
    from kreports.mcp.resources import read_resource, render_resource
    from kreports.mcp.server import handle_call_tool
    from kreports.mcp.tools import call_tool

    matrix: list[dict[str, object]] = []
    for name, arguments in PROFESSIONAL_REHEARSAL_TOOLS:
        legacy = json.loads(call_tool(name, arguments))
        envelope = dispatch_tool(name, arguments).model_dump(mode="json")
        stdio_result = asyncio.run(handle_call_tool(name, arguments))
        if not (isinstance(stdio_result, tuple) and len(stdio_result) == 2):
            raise WorkerActionError("mcp_boundary_mismatch", f"{name} returned an invalid stdio shape")
        contents, stdio = stdio_result
        legacy_pack = legacy.get("answer_pack")
        envelope_pack = envelope.get("answer_pack")
        stdio_pack = stdio.get("answer_pack") if isinstance(stdio, dict) else None
        if any(
            _public_schema_leak(value)
            for value in (legacy, envelope, stdio, legacy_pack, envelope_pack, stdio_pack)
        ):
            raise WorkerActionError("mcp_schema_not_closed", f"{name} public result leaks schema text")
        if stdio != envelope:
            raise WorkerActionError("mcp_boundary_mismatch", f"{name} stdio envelope differs")
        for field in ("answer", "answer_pack", "domain_verdict"):
            if legacy.get(field) != envelope.get(field):
                raise WorkerActionError("mcp_boundary_mismatch", f"{name} {field} differs across boundaries")
        legacy_quality = legacy.get("data_quality")
        envelope_quality = envelope.get("data_quality")
        if not isinstance(legacy_quality, dict) or not isinstance(envelope_quality, dict):
            raise WorkerActionError("mcp_boundary_mismatch", f"{name} lacks a boundary quality object")
        if (
            legacy_quality.get("status") != envelope_quality.get("status")
            or legacy_quality.get("section_statuses") != envelope_quality.get("section_statuses")
        ):
            raise WorkerActionError("mcp_boundary_mismatch", f"{name} quality differs across boundaries")
        if not isinstance(contents, list) or not contents or getattr(contents[0], "text", None) != envelope.get("answer"):
            raise WorkerActionError("mcp_boundary_mismatch", f"{name} stdio text differs")
        validation = validate_professional_result(legacy)
        status = validation["status"]
        if name in KAM_GATED_TOOLS and status == "error":
            raise WorkerActionError("mcp_kam_gate_failed", f"{name} returned error")
        pack = validation["pack"]
        resource_checked = False
        if isinstance(pack, dict) and isinstance(pack.get("resource_uri"), str):
            uri = str(pack["resource_uri"])
            resource = read_resource(uri)
            rendered = render_resource(uri)
            if _public_schema_leak({"resource": resource, "rendered": rendered}):
                raise WorkerActionError("mcp_schema_not_closed", f"{name} resource leaks schema text")
            if str(status) not in rendered:
                raise WorkerActionError("mcp_resource_mismatch", f"{name} resource lacks canonical status")
            for source in pack.get("sources") or []:
                if isinstance(source, dict) and source.get("rcept_no") and str(source["rcept_no"]) not in rendered:
                    raise WorkerActionError("mcp_resource_mismatch", f"{name} resource lacks material receipt")
            resource_checked = True
        quality = legacy.get("data_quality") if isinstance(legacy.get("data_quality"), dict) else {}
        matrix.append({
            "tool": name,
            "status": status,
            "domain_verdict": legacy.get("domain_verdict"),
            "fact_count": len(legacy.get("confirmed_facts") or []),
            "evidence_count": len(envelope.get("evidence") or []),
            "pack_status": (pack.get("data_quality") or {}).get("status") if isinstance(pack, dict) else None,
            "table_ids": [table.get("id") for table in pack.get("tables") or [] if isinstance(table, dict)] if isinstance(pack, dict) else [],
            "source_count": len(pack.get("sources") or []) if isinstance(pack, dict) else 0,
            "resource_checked": resource_checked,
            "first_answer_paragraph": _first_paragraph(str(legacy.get("answer") or "")),
            "limitation_count": len(quality.get("limitations") or []),
        })
    return {"tool_count": len(matrix), "schema_error_closed": True, "all_boundary_parity": True, "matrix": matrix}


def execute_action(action: str, *, year: int | None = None) -> dict[str, object]:
    """Execute one validated action after the child process has bound its DB."""
    if action not in _ACTIONS:
        raise WorkerActionError("invalid_action", "unsupported worker action")
    if action in {"kam-dry-run", "kam-rebuild", "procedure-index"}:
        if year not in YEARS:
            raise WorkerActionError("invalid_year", "year must be one of 2021..2025")
    elif year is not None:
        raise WorkerActionError("invalid_action_arguments", f"{action} does not accept --year")

    collector = action in {
        "migrate",
        "kam-dry-run",
        "kam-rebuild",
        "procedure-index",
    }
    if collector:
        _require_mode("collector", action)
    else:
        _require_mode("readonly", action)
    binding = _require_rehearsal_binding(
        require_initial_digest=action == "migrate",
    )

    with _bound_database_runtime(binding, collector=collector):
        if action == "migrate":
            before = migration_state()
            try:
                from kreports.db.engine import init_db
                init_db()
            except Exception as exc:
                raise WorkerActionError("migration_failed", exc) from exc
            after = migration_state()
            _validate_state_after_migration(after)
            before_revisions = set(before["recorded_revisions"])
            return {
                "before": before,
                "applied_revisions": [
                    revision
                    for revision in after["recorded_revisions"]
                    if revision not in before_revisions
                ],
                "after": after,
            }
        if action in {"kam-dry-run", "kam-rebuild"}:
            from kreports.collector.report_document_collector import (
                rebuild_kam_items,
            )
            result = _bounded_rebuild_result(
                rebuild_kam_items(
                    year=int(year),
                    dry_run=action == "kam-dry-run",
                )
            )
            if int(result.get("error") or 0) or int(
                result.get("failed") or 0
            ):
                raise WorkerActionError(
                    "backfill_failed",
                    "KAM rebuild reported receipt errors",
                )
            return result
        if action == "procedure-index":
            from kreports.collector.report_document_collector import (
                index_audit_procedures_from_sections,
            )
            result = _bounded_rebuild_result(
                index_audit_procedures_from_sections(year=int(year))
            )
            if int(result.get("error") or 0) or int(
                result.get("failed") or 0
            ):
                raise WorkerActionError(
                    "backfill_failed",
                    "procedure index reported failures",
                )
            return result
        if action == "semantic-snapshot":
            return semantic_snapshot()
        return validate_professional_mcp()


def _parse_arguments(argv: list[str] | None) -> tuple[str, int | None]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("action", nargs="?")
    parser.add_argument("--year", type=int)
    namespace, unknown = parser.parse_known_args(argv)
    if unknown or namespace.action is None:
        raise WorkerActionError("invalid_action", "expected one supported action and optional --year")
    return str(namespace.action), namespace.year


def _write_json(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")), flush=True)


def main(argv: list[str] | None = None) -> int:
    action = "unknown"
    try:
        action, year = _parse_arguments(argv)
        _write_json(execute_action(action, year=year))
        return 0
    except WorkerActionError as exc:
        _write_json({"ok": False, "action": action, "error": {"code": exc.code, "message": _bounded_message(exc)}})
        return 2
    except Exception as exc:  # pragma: no cover - final containment boundary
        _write_json({"ok": False, "action": action, "error": {"code": "worker_failed", "message": _bounded_message(exc)}})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
