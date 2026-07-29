"""Black-box and real-SQLite contracts for the KAM rehearsal worker.

Each test names the production break it catches: accepting an unbounded action,
missing a checked-out migration, or silently losing typed KAM linkage.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import socket
import subprocess
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

from kreports.db.migrations import MIGRATIONS, _checksum


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MARKER_SCHEMA = "kam-schema-backfill-rehearsal-marker.v1"
EXPECTED_KAM_GATED_TOOLS = {
    "build_audit_acceptance_pack",
    "get_audit_report_sections",
    "get_kam_lifecycle",
    "compare_peer_kam_topics",
}
EXPECTED_PROFESSIONAL_REHEARSAL_TOOLS = (
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


def write_marker(database: Path, *, database_path: Path | None = None, inode: int | None = None) -> Path:
    """Create the explicit, non-secret rehearsal capability next to a real DB."""
    stat = database.stat()
    marker = database.parent / "kam-rehearsal-marker.json"
    marker.write_text(json.dumps({
        "schema_version": MARKER_SCHEMA,
        "run_id": "test-run-20260729",
        "database_path": str((database_path or database).resolve()),
        "database_inode": stat.st_ino if inode is None else inode,
        "database_device": stat.st_dev,
        "source_sha256": "a" * 64,
        "clone_initial_sha256": "b" * 64,
    }), encoding="utf-8")
    return marker


def _child_env(database: Path, *, runtime_mode: str, marker: Path | None = None) -> dict[str, str]:
    """Bind only an explicit temporary database in the fresh child process."""
    env = os.environ.copy()
    for name in ("DB_URL", "DART_API_KEY", "KREPORTS_RUNTIME_MODE", "KREPORTS_REHEARSAL_MARKER"):
        env.pop(name, None)
    env.update(
        {
            "DB_URL": f"sqlite:///{database}",
            "DART_API_KEY": "",
            "KREPORTS_RUNTIME_MODE": runtime_mode,
            "PYTHONPATH": str(REPOSITORY_ROOT),
        }
    )
    if marker is not None:
        env["KREPORTS_REHEARSAL_MARKER"] = str(marker)
    return env


def run_worker_process(
    database: Path, action: str, *arguments: str, runtime_mode: str = "readonly", marker: Path | None = None,
    with_marker: bool = True,
) -> subprocess.CompletedProcess[str]:
    if marker is None and with_marker:
        marker = write_marker(database)
    return subprocess.run(
        [sys.executable, "-m", "kreports.maintenance.kam_rehearsal_worker", action, *arguments],
        cwd=REPOSITORY_ROOT,
        env=_child_env(database, runtime_mode=runtime_mode, marker=marker),
        text=True,
        capture_output=True,
        check=False,
    )


def run_worker(
    database: Path, action: str, *arguments: str, runtime_mode: str = "readonly", marker: Path | None = None,
) -> dict[str, object]:
    result = run_worker_process(database, action, *arguments, runtime_mode=runtime_mode, marker=marker)
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("\n") == 1
    return json.loads(result.stdout)


@pytest.fixture
def empty_database(tmp_path: Path) -> Path:
    path = tmp_path / "worker.db"
    sqlite3.connect(path).close()
    return path


def test_worker_rejects_year_for_migrate_before_database_import(empty_database: Path) -> None:
    """Catch a change that accepts --year and can accidentally widen migration work."""
    result = run_worker_process(empty_database, "migrate", "--year", "2025", runtime_mode="collector")
    payload = json.loads(result.stdout)
    assert result.returncode == 2
    assert payload["error"]["code"] == "invalid_action_arguments"


def test_worker_rejects_year_outside_rehearsal_range(empty_database: Path) -> None:
    """Catch a change that lets a KAM rebuild run outside the approved years."""
    result = run_worker_process(empty_database, "kam-rebuild", "--year", "2020", runtime_mode="collector")
    payload = json.loads(result.stdout)
    assert result.returncode == 2
    assert payload["error"]["code"] == "invalid_year"


@pytest.mark.parametrize("action,runtime_mode", [("migrate", "collector"), ("semantic-snapshot", "readonly")])
def test_worker_requires_matching_rehearsal_marker_before_any_database_action(
    empty_database: Path, action: str, runtime_mode: str,
) -> None:
    """Catch an arbitrary DB URL reaching migration or readonly inspection without a clone capability."""
    result = run_worker_process(
        empty_database, action, runtime_mode=runtime_mode, with_marker=False,
    )
    payload = json.loads(result.stdout)
    assert result.returncode == 2
    assert payload["error"]["code"] == "rehearsal_binding_required"
    with sqlite3.connect(empty_database) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []


def test_worker_rejects_marker_inode_mismatch_before_writable_init(empty_database: Path) -> None:
    """Catch a marker copied from a different clone being accepted for a writable migration."""
    marker = write_marker(empty_database, inode=empty_database.stat().st_ino + 1)
    result = run_worker_process(
        empty_database, "migrate", runtime_mode="collector", marker=marker,
    )
    payload = json.loads(result.stdout)
    assert result.returncode == 2
    assert payload["error"]["code"] == "rehearsal_binding_required"
    with sqlite3.connect(empty_database) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []


@pytest.fixture
def legacy_database(tmp_path: Path) -> Path:
    """A real revision-04 SQLite database with the three pre-KAM legacy tables."""
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE schema_migrations (revision TEXT PRIMARY KEY, checksum TEXT NOT NULL, description TEXT NOT NULL, applied_at TEXT NOT NULL);
            CREATE TABLE backfill_runs (id INTEGER PRIMARY KEY, task_type TEXT NOT NULL, year INTEGER, market TEXT, status TEXT NOT NULL, pid INTEGER, params_json TEXT, summary_json TEXT, error_msg TEXT, started_at TEXT NOT NULL, finished_at TEXT, owner_token TEXT, heartbeat_at TEXT, checkpoint_json TEXT NOT NULL DEFAULT '{}', attempted_count INTEGER NOT NULL DEFAULT 0, saved_count INTEGER NOT NULL DEFAULT 0, no_data_count INTEGER NOT NULL DEFAULT 0, error_count INTEGER NOT NULL DEFAULT 0, lease_key TEXT, owner_host TEXT, owner_process_start TEXT);
            CREATE TABLE audit_procedure_items (id INTEGER PRIMARY KEY, rcept_no TEXT NOT NULL, dcm_no TEXT, corp_code TEXT NOT NULL, bsns_year INTEGER NOT NULL, source_type TEXT NOT NULL, kam_topic TEXT, procedure_type TEXT NOT NULL, procedure_text TEXT NOT NULL, procedure_hash TEXT, procedure_length INTEGER, section_ordinal INTEGER NOT NULL DEFAULT 0, procedure_ordinal INTEGER NOT NULL DEFAULT 0, fetched_at TEXT NOT NULL);
            CREATE TABLE audit_fees (id INTEGER PRIMARY KEY, corp_code TEXT NOT NULL, bsns_year INTEGER NOT NULL, auditor_nm TEXT, audit_fee_m INTEGER, audit_hours INTEGER, non_audit_fee_m INTEGER, non_audit_hours INTEGER, nas_ratio REAL, independence_risk_flag INTEGER, fetched_at TEXT NOT NULL);
            """
        )
        for migration in MIGRATIONS[:4]:
            connection.execute(
                "INSERT INTO schema_migrations VALUES (?, ?, ?, ?)",
                (migration.revision, _checksum(migration), migration.description, "2026-07-29T00:00:00Z"),
            )
        connection.commit()
    finally:
        connection.close()
    return path


def test_migrate_applies_every_pending_checked_out_revision(legacy_database: Path) -> None:
    """Catch a worker that reports success while omitting a checked-out migration."""
    first = run_worker(legacy_database, "migrate", runtime_mode="collector")
    second = run_worker(legacy_database, "migrate", runtime_mode="collector")
    assert first["before"]["recorded_revisions"] == [item.revision for item in MIGRATIONS[:4]]
    assert first["applied_revisions"] == [item.revision for item in MIGRATIONS[4:]]
    assert first["after"]["recorded_revisions"] == [item.revision for item in MIGRATIONS]
    assert first["after"]["schema_complete"] is True
    assert first["after"]["pending_revisions"] == []
    assert first["after"]["checksum_mismatches"] == []
    assert first["after"]["missing_tables"] == []
    assert first["after"]["missing_columns"] == {}
    assert first["after"]["missing_indexes"] == []
    assert first["after"]["quick_check"] == ["ok"]
    assert first["after"]["foreign_key_violations"] == []
    assert second["applied_revisions"] == []
    with sqlite3.connect(legacy_database) as connection:
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"kam_items", "audit_procedure_items", "audit_fees", "group_entities", "group_relationships", "group_component_metrics"} <= tables
        procedure_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(audit_procedure_items)")
        }
        assert {"kam_item_id", "method", "assertion_hints_json", "linked_metric_keys_json", "linked_note_keys_json", "linked_event_keys_json", "parser_version", "quality_status"} <= procedure_columns
        fee_columns = {row[1] for row in connection.execute("PRAGMA table_info(audit_fees)")}
        assert {"contract_fee_m", "actual_fee_m", "availability_status", "quality_status", "source_observations_json"} <= fee_columns
        indexes = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        assert {"idx_kam_item_corp_year", "idx_audit_procedure_kam_item", "idx_audit_fee_availability_year", "idx_group_entity_parent_year", "idx_group_relationship_parent_year", "idx_group_metric_parent_year"} <= indexes


def _create_snapshot_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE kam_items (id INTEGER PRIMARY KEY, rcept_no TEXT NOT NULL, dcm_no TEXT, corp_code TEXT NOT NULL, bsns_year INTEGER NOT NULL, source_type TEXT NOT NULL, ordinal INTEGER NOT NULL, title TEXT, normalized_topic TEXT, reason_text TEXT, audit_response_text TEXT, related_note_references_json TEXT NOT NULL, full_body_hash TEXT NOT NULL, full_body_length INTEGER NOT NULL, source_basis TEXT NOT NULL, parser_version TEXT NOT NULL, quality_status TEXT NOT NULL);
            CREATE TABLE audit_procedure_items (id INTEGER PRIMARY KEY, rcept_no TEXT NOT NULL, dcm_no TEXT, corp_code TEXT NOT NULL, bsns_year INTEGER NOT NULL, source_type TEXT NOT NULL, kam_item_id INTEGER, kam_topic TEXT, method TEXT, procedure_type TEXT NOT NULL, procedure_text TEXT NOT NULL, procedure_hash TEXT, procedure_length INTEGER, assertion_hints_json TEXT, linked_metric_keys_json TEXT, linked_note_keys_json TEXT, linked_event_keys_json TEXT, parser_version TEXT, quality_status TEXT, section_ordinal INTEGER NOT NULL, procedure_ordinal INTEGER NOT NULL);
            """
        )
        connection.execute("INSERT INTO kam_items VALUES (1, '20260310000001', '1', '00126380', 2025, 'audit_report', 1, '수익', 'revenue', '추정', '검증', '{\"b\":2,\"a\":1}', 'abc', 10, 'full_body', 'v1', 'usable')")
        connection.execute("INSERT INTO audit_procedure_items VALUES (1, '20260310000001', '1', '00126380', 2025, 'audit_report', 1, 'revenue', 'inspection', 'substantive', '증빙 검토', 'def', 5, '[\"existence\"]', '[\"revenue\"]', '[]', '[]', 'v1', 'usable', 1, 1)")
        connection.commit()
    finally:
        connection.close()


def test_semantic_snapshot_binds_stable_ids_and_typed_linkage(tmp_path: Path) -> None:
    """Catch a snapshot that omits procedure method, IDs, or KAM foreign keys."""
    path = tmp_path / "snapshot.db"
    _create_snapshot_database(path)
    before = run_worker(path, "semantic-snapshot")
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE audit_procedure_items SET method='inquiry'")
    changed = run_worker(path, "semantic-snapshot")
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE audit_procedure_items SET method='inspection'")
    restored = run_worker(path, "semantic-snapshot")
    assert changed["semantic_sha256"] != before["semantic_sha256"]
    assert restored["semantic_sha256"] == before["semantic_sha256"]
    assert before["integrity"]["orphan_procedure_count"] == 0


def test_kam_rebuild_and_procedure_index_use_local_evidence_only(
    temp_engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catch an action changed to fetch a filing instead of using persisted evidence."""
    import httpx
    from kreports.db.engine import get_session
    from kreports.db.models import Company, KamItem, SourceDocument
    from kreports.maintenance.kam_rehearsal_worker import execute_action

    def blocked_network(*_args, **_kwargs):
        raise AssertionError("KAM rehearsal must not make a network request")

    monkeypatch.setattr(socket.socket, "connect", blocked_network)
    monkeypatch.setattr(httpx.Client, "send", blocked_network)
    _bind_direct_rehearsal_marker(monkeypatch, tmp_path)
    with get_session() as session:
        session.add(Company(corp_code="00126380", stock_code="005930", corp_name="삼성전자", market="KOSPI"))
        session.add(SourceDocument(
            rcept_no="20260310000001", dcm_no="1", corp_code="00126380",
            bsns_year=2025, source_type="audit_report", report_nm="감사보고서",
            content_type="xml", raw_content=(
                "핵심감사사항\n수익인식\n핵심감사사항으로 선정한 이유\n"
                "추정\n감사에서 다루어진 방법\n계약서를 검사하였습니다."
            ), doc_hash="a" * 40,
            storage_status="inline", fetched_at=datetime(2026, 3, 10),
        ))
        session.add(KamItem(
            rcept_no="20260310000001", dcm_no="1", corp_code="00126380",
            bsns_year=2025, source_type="audit_report", ordinal=1, title="수익인식",
            normalized_topic="revenue", reason_text="추정", audit_response_text="계약서를 검사하였습니다.",
            related_note_references_json="[]", full_body_hash="b" * 40,
            full_body_length=50, source_basis="source_documents.raw_body",
            parser_version="v1", quality_status="usable", fetched_at=datetime(2026, 3, 10),
        ))

    dry = execute_action("kam-dry-run", year=2025)
    rebuilt = execute_action("kam-rebuild", year=2025)
    indexed = execute_action("procedure-index", year=2025)
    assert dry["database_status"] == "available"
    assert dry["rows_written"] == 0
    assert rebuilt["receipt_counts"]["full_body"] == 1
    assert indexed["failed"] == 0
    assert indexed["rows_written"] >= 1


def _bind_direct_rehearsal_marker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    database = tmp_path / "direct-worker.db"
    sqlite3.connect(database).close()
    marker = write_marker(database)
    monkeypatch.setenv("DB_URL", f"sqlite:///{database}")
    monkeypatch.setenv("KREPORTS_REHEARSAL_MARKER", str(marker))
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")


def test_kam_rebuild_fails_closed_on_failed_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catch a KAM rebuild that reports failed receipts but still exits successfully."""
    import kreports.collector.report_document_collector as collector_module
    from kreports.maintenance.kam_rehearsal_worker import WorkerActionError, execute_action

    _bind_direct_rehearsal_marker(monkeypatch, tmp_path)
    monkeypatch.setattr(
        collector_module, "rebuild_kam_items", lambda **_kwargs: {"error": 0, "failed": 1},
    )
    with pytest.raises(WorkerActionError) as caught:
        execute_action("kam-rebuild", year=2025)
    assert caught.value.code == "backfill_failed"


def test_procedure_index_fails_closed_on_error_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catch a procedure indexer that reports normalized errors but still exits successfully."""
    import kreports.collector.report_document_collector as collector_module
    from kreports.maintenance.kam_rehearsal_worker import WorkerActionError, execute_action

    _bind_direct_rehearsal_marker(monkeypatch, tmp_path)
    monkeypatch.setattr(
        collector_module, "index_audit_procedures_from_sections", lambda **_kwargs: {"error": 1, "failed": 0},
    )
    with pytest.raises(WorkerActionError) as caught:
        execute_action("procedure-index", year=2025)
    assert caught.value.code == "backfill_failed"


def test_mcp_validator_rejects_schema_text_at_every_layer() -> None:
    """Catch public schema leakage even when the private diagnostic fields are clean."""
    from kreports.maintenance.kam_rehearsal_worker import WorkerActionError, validate_professional_result

    leaked = {
        "answer": "판정: error\nno such table: kam_items",
        "data_quality": {"status": "error", "section_statuses": {}},
        "answer_pack": {"data_quality": {"status": "error"}, "tables": [], "sources": []},
    }
    with pytest.raises(WorkerActionError) as caught:
        validate_professional_result(leaked)
    assert caught.value.code == "mcp_schema_not_closed"


def test_mcp_validation_rejects_envelope_only_schema_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catch a schema failure that appears only in typed envelope evidence, not legacy output."""
    import kreports.mcp.dispatch as dispatch_module
    import kreports.mcp.server as server_module
    import kreports.mcp.tools as tools_module
    from kreports.maintenance.kam_rehearsal_worker import WorkerActionError, validate_professional_mcp

    legacy = {
        "answer": "판정:\n- usable",
        "confirmed_facts": [],
        "domain_verdict": None,
        "data_quality": {"status": "usable", "limitations": [], "section_statuses": {}},
        "answer_pack": {"data_quality": {"status": "usable"}, "tables": [], "sources": []},
    }
    envelope = {**legacy, "evidence": [{"note": "no such table: kam_items"}]}

    class FakeEnvelope:
        def model_dump(self, **_kwargs):
            return envelope

    async def fake_stdio(_name, _arguments):
        return [SimpleNamespace(text=envelope["answer"])], envelope

    monkeypatch.setattr(tools_module, "call_tool", lambda _name, _arguments: json.dumps(legacy))
    monkeypatch.setattr(dispatch_module, "dispatch_tool", lambda _name, _arguments: FakeEnvelope())
    monkeypatch.setattr(server_module, "handle_call_tool", fake_stdio)
    with pytest.raises(WorkerActionError) as caught:
        validate_professional_mcp()
    assert caught.value.code == "mcp_schema_not_closed"


def test_mcp_validation_rejects_section_status_drift_across_all_17_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catch an envelope that reports a different section status than legacy/stdio."""
    import kreports.mcp.dispatch as dispatch_module
    import kreports.mcp.server as server_module
    import kreports.mcp.tools as tools_module
    from kreports.maintenance.kam_rehearsal_worker import (
        WorkerActionError,
        validate_professional_mcp,
    )

    legacy = {
        "answer": "판정:\n- usable",
        "confirmed_facts": [],
        "domain_verdict": None,
        "data_quality": {"status": "usable", "limitations": [], "section_statuses": {"kam": {"status": "usable"}}},
        "answer_pack": {"data_quality": {"status": "usable"}, "tables": [], "sources": []},
    }
    drifted = {**legacy, "data_quality": {"status": "usable", "limitations": [], "section_statuses": {"kam": {"status": "limited"}}}}

    class FakeEnvelope:
        def model_dump(self, **_kwargs):
            return drifted

    async def fake_stdio(_name, _arguments):
        return [SimpleNamespace(text=drifted["answer"])], drifted

    monkeypatch.setattr(tools_module, "call_tool", lambda _name, _arguments: json.dumps(legacy))
    monkeypatch.setattr(dispatch_module, "dispatch_tool", lambda _name, _arguments: FakeEnvelope())
    monkeypatch.setattr(server_module, "handle_call_tool", fake_stdio)
    with pytest.raises(WorkerActionError) as caught:
        validate_professional_mcp()
    assert caught.value.code == "mcp_boundary_mismatch"


def test_mcp_validation_uses_exact_ordered_samsung_catalog_and_kam_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catch a wrong tool argument/order or a missing KAM gate in the rehearsal boundary."""
    import kreports.mcp.dispatch as dispatch_module
    import kreports.mcp.server as server_module
    import kreports.mcp.tools as tools_module
    from kreports.maintenance.kam_rehearsal_worker import (
        KAM_GATED_TOOLS,
        PROFESSIONAL_REHEARSAL_TOOLS,
        validate_professional_mcp,
    )

    good = {
        "answer": "판정:\n- usable",
        "confirmed_facts": [],
        "domain_verdict": None,
        "data_quality": {"status": "usable", "limitations": [], "section_statuses": {}},
        "answer_pack": {"data_quality": {"status": "usable"}, "tables": [], "sources": []},
    }
    legacy_calls: list[tuple[str, dict[str, object]]] = []
    envelope_calls: list[tuple[str, dict[str, object]]] = []
    stdio_calls: list[tuple[str, dict[str, object]]] = []

    class FakeEnvelope:
        def model_dump(self, **_kwargs):
            return good

    async def fake_stdio(name, arguments):
        stdio_calls.append((name, arguments))
        return [SimpleNamespace(text=good["answer"])], good

    monkeypatch.setattr(
        tools_module,
        "call_tool",
        lambda name, arguments: (legacy_calls.append((name, arguments)), json.dumps(good))[1],
    )
    monkeypatch.setattr(
        dispatch_module,
        "dispatch_tool",
        lambda name, arguments: (envelope_calls.append((name, arguments)), FakeEnvelope())[1],
    )
    monkeypatch.setattr(server_module, "handle_call_tool", fake_stdio)
    result = validate_professional_mcp()
    assert PROFESSIONAL_REHEARSAL_TOOLS == EXPECTED_PROFESSIONAL_REHEARSAL_TOOLS
    assert KAM_GATED_TOOLS == EXPECTED_KAM_GATED_TOOLS
    assert legacy_calls == list(EXPECTED_PROFESSIONAL_REHEARSAL_TOOLS)
    assert envelope_calls == list(EXPECTED_PROFESSIONAL_REHEARSAL_TOOLS)
    assert stdio_calls == list(EXPECTED_PROFESSIONAL_REHEARSAL_TOOLS)
    assert result["tool_count"] == 17
