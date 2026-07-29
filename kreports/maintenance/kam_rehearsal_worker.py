"""Fresh-process, bounded KAM schema-rehearsal actions.

This module deliberately imports only the standard library at module load.
The command-line boundary validates its action before importing settings or an
engine, so an accidental parent ``DB_URL`` can never be bound first.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
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


def _bounded_message(value: object) -> str:
    message = _PATH_TEXT.sub("[path]", str(value).replace("\n", " ").replace("\r", " "))
    return message[:500] or "worker action failed"


def _configured_database_path() -> Path:
    url = os.environ.get("DB_URL", "")
    if not url.startswith("sqlite:///"):
        raise WorkerActionError("invalid_database_url", "DB_URL must be an absolute SQLite URL")
    raw_path = unquote(url[len("sqlite:///"):])
    path = Path(raw_path)
    if not path.is_absolute():
        raise WorkerActionError("invalid_database_url", "DB_URL must name an absolute SQLite file")
    if not path.is_file():
        raise WorkerActionError("database_unavailable", "configured SQLite database is unavailable")
    return path


def _open_readonly_database() -> sqlite3.Connection:
    path = _configured_database_path()
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
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

    if action in {"migrate", "kam-dry-run", "kam-rebuild", "procedure-index"}:
        _require_mode("collector", action)
    else:
        _require_mode("readonly", action)

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
        return {"before": before, "applied_revisions": [revision for revision in after["recorded_revisions"] if revision not in before_revisions], "after": after}
    if action in {"kam-dry-run", "kam-rebuild"}:
        from kreports.collector.report_document_collector import rebuild_kam_items
        result = _bounded_rebuild_result(rebuild_kam_items(year=int(year), dry_run=action == "kam-dry-run"))
        if int(result.get("error") or 0):
            raise WorkerActionError("backfill_failed", "KAM rebuild reported receipt errors")
        return result
    if action == "procedure-index":
        from kreports.collector.report_document_collector import index_audit_procedures_from_sections
        result = _bounded_rebuild_result(index_audit_procedures_from_sections(year=int(year)))
        if int(result.get("failed") or 0):
            raise WorkerActionError("backfill_failed", "procedure index reported failures")
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
