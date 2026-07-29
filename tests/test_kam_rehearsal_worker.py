"""Black-box and real-SQLite contracts for the KAM rehearsal worker.

Each test names the production break it catches: accepting an unbounded action,
missing a checked-out migration, or silently losing typed KAM linkage.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import socket
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from kreports.db.migrations import MIGRATIONS, _checksum

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MARKER_SCHEMA = "kam-schema-backfill-rehearsal-marker.v1"
MIN_FREE_BYTES = 10 * 1024**3
TEST_CAPABILITY = "c" * 64
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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_marker(
    database: Path,
    *,
    database_path: Path | None = None,
    inode: int | None = None,
    source_path: Path | None = None,
    repository_root: Path | None = None,
    clone_initial_sha256: str | None = None,
    capability: str = TEST_CAPABILITY,
    valid_hmac: bool = True,
) -> Path:
    """Create a signed Task1/Task3 clone receipt next to a real DB."""
    database = database.resolve()
    source = source_path or (
        database.parent.parent
        / f"{database.parent.name}-{database.stem}-source"
        / "source.db"
    )
    source.parent.mkdir(parents=True, exist_ok=True)
    if not source.exists():
        sqlite3.connect(source).close()
    source = source.resolve()
    repository = repository_root or (
        database.parent.parent / f"{database.parent.name}-{database.stem}-repo"
    )
    repository.mkdir(parents=True, exist_ok=True)
    repository = repository.resolve()
    stat = database.stat()
    source_stat = source.stat()
    marker = database.parent / "kam-rehearsal-marker.json"
    signed_fields = {
        "schema_version": MARKER_SCHEMA,
        "run_id": "test-run-20260729",
        "database_path": str((database_path or database).resolve()),
        "database_inode": stat.st_ino if inode is None else inode,
        "database_device": stat.st_dev,
        "source_path": str(source),
        "source_inode": source_stat.st_ino,
        "source_device": source_stat.st_dev,
        "source_sha256": _sha256_file(source),
        "clone_initial_sha256": clone_initial_sha256 or _sha256_file(database),
        "repository_root": str(repository),
        "rehearsal_dir": str(database.parent),
        "filesystem_type": "apfs",
        "min_free_bytes": MIN_FREE_BYTES,
    }
    canonical = json.dumps(
        signed_fields,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    signature = hmac.new(
        bytes.fromhex(capability),
        canonical,
        hashlib.sha256,
    ).hexdigest()
    marker.write_text(
        json.dumps({**signed_fields, "hmac_sha256": signature if valid_hmac else "0" * 64}),
        encoding="utf-8",
    )
    return marker


def write_unsigned_self_asserted_marker(database: Path) -> Path:
    """Create the forgeable round-1 marker to prove it is no longer trusted."""
    stat = database.stat()
    marker = database.parent / "kam-rehearsal-marker.json"
    marker.write_text(json.dumps({
        "schema_version": MARKER_SCHEMA,
        "run_id": "forged-marker",
        "database_path": str(database.resolve()),
        "database_inode": stat.st_ino,
        "database_device": stat.st_dev,
        "source_sha256": "a" * 64,
        "clone_initial_sha256": _sha256_file(database),
    }), encoding="utf-8")
    return marker


def _child_env(
    database: Path,
    *,
    runtime_mode: str,
    marker: Path | None = None,
    capability: str | None = None,
) -> dict[str, str]:
    """Bind only an explicit temporary database in the fresh child process."""
    env = os.environ.copy()
    for name in (
        "DB_URL",
        "DART_API_KEY",
        "KREPORTS_RUNTIME_MODE",
        "KREPORTS_REHEARSAL_MARKER",
        "KREPORTS_REHEARSAL_CAPABILITY",
    ):
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
        env["KREPORTS_REHEARSAL_CAPABILITY"] = capability or TEST_CAPABILITY
    return env


def run_worker_process(
    database: Path, action: str, *arguments: str, runtime_mode: str = "readonly", marker: Path | None = None,
    with_marker: bool = True, capability: str | None = None,
) -> subprocess.CompletedProcess[str]:
    if marker is None and with_marker:
        marker = write_marker(database)
    return subprocess.run(
        [sys.executable, "-m", "kreports.maintenance.kam_rehearsal_worker", action, *arguments],
        cwd=REPOSITORY_ROOT,
        env=_child_env(
            database,
            runtime_mode=runtime_mode,
            marker=marker,
            capability=capability,
        ),
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


@pytest.fixture
def revision08_evidence_database(tmp_path: Path) -> Path:
    """A revision-08 clone with the pre-foundation evidence table shapes."""
    from sqlalchemy import create_engine

    from kreports.db.models import Base

    path = tmp_path / "revision-08-evidence.db"
    engine = create_engine(f"sqlite:///{path}")
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            DROP TABLE audit_fee_observations;
            DROP TABLE financial_facts_compact;
            DROP TABLE company_year_quality;
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
            );
            CREATE INDEX idx_fin_compact_corp_year
              ON financial_facts_compact (corp_code, bsns_year);
            CREATE INDEX idx_fin_compact_metric
              ON financial_facts_compact (metric_key);
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
            );
            CREATE TABLE IF NOT EXISTS schema_migrations (
              revision TEXT PRIMARY KEY,
              checksum TEXT NOT NULL,
              description TEXT NOT NULL,
              applied_at TEXT NOT NULL
            );
            DELETE FROM schema_migrations;
            """
        )
        connection.executemany(
            "INSERT INTO schema_migrations VALUES (?, ?, ?, ?)",
            [
                (
                    migration.revision,
                    _checksum(migration),
                    migration.description,
                    "2026-07-29T00:00:00Z",
                )
                for migration in MIGRATIONS[:8]
            ],
        )
    return path


def _seed_local_database_evidence(database: Path) -> None:
    from datetime import date

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from kreports.collector.audit_fee_sources import (
        AuditFeeObservation,
        observations_json,
    )
    from kreports.db.models import (
        AuditFee,
        Company,
        Disclosure,
        FinancialFact,
    )

    observation = AuditFeeObservation(
        corp_code="00126380",
        bsns_year=2025,
        source_class="cached_business_report",
        actual_fee_m=100,
        actual_hours=1_000,
        source_rcept_no="20260310000001",
        source_period="2025",
        raw_values={"fee": "100", "hours": "1000"},
    )
    engine = create_engine(f"sqlite:///{database}")
    try:
        with Session(engine) as session:
            session.add_all([
                Company(
                    corp_code="00126380",
                    stock_code="005930",
                    corp_name="삼성전자",
                    market="KOSPI",
                ),
                Disclosure(
                    rcept_no="20260310000001",
                    corp_code="00126380",
                    corp_name="삼성전자",
                    disc_date=date(2026, 3, 10),
                    disc_type="A",
                    report_nm="사업보고서 (2025.12)",
                    flr_nm="삼성전자",
                ),
                FinancialFact(
                    corp_code="00126380",
                    bsns_year=2025,
                    reprt_code="11011",
                    fs_div="CFS",
                    sj_div="IS",
                    account_id="ifrs-full_Revenue",
                    account_nm="매출액",
                    ord=1,
                    thstrm_amount=100_000_000,
                ),
                AuditFee(
                    corp_code="00126380",
                    bsns_year=2025,
                    auditor_nm="삼일회계법인",
                    audit_fee_m=100,
                    audit_hours=1_000,
                    actual_fee_m=100,
                    actual_hours=1_000,
                    source_observations_json=observations_json([observation]),
                ),
            ])
            session.commit()
    finally:
        engine.dispose()


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
        kam_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(kam_items)")
        }
        assert {
            "id", "rcept_no", "dcm_no", "corp_code", "bsns_year",
            "source_type", "ordinal", "title", "normalized_topic",
            "reason_text", "audit_response_text",
            "related_note_references_json", "full_body_hash",
            "full_body_length", "source_basis", "parser_version",
            "quality_status", "fetched_at",
        } <= kam_columns
        assert {"kam_item_id", "method", "assertion_hints_json", "linked_metric_keys_json", "linked_note_keys_json", "linked_event_keys_json", "parser_version", "quality_status"} <= procedure_columns
        fee_columns = {row[1] for row in connection.execute("PRAGMA table_info(audit_fees)")}
        assert {"contract_fee_m", "actual_fee_m", "availability_status", "quality_status", "source_observations_json"} <= fee_columns
        indexes = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        assert {"idx_kam_item_corp_year", "idx_audit_procedure_kam_item", "idx_audit_fee_availability_year", "idx_group_entity_parent_year", "idx_group_relationship_parent_year", "idx_group_metric_parent_year"} <= indexes


def test_migrate_fails_closed_on_recorded_checksum_mismatch(
    legacy_database: Path,
) -> None:
    """Catch a checked-out migration ledger whose recorded checksum was mutated."""
    with sqlite3.connect(legacy_database) as connection:
        connection.execute(
            "UPDATE schema_migrations SET checksum=? WHERE revision=?",
            ("0" * 64, MIGRATIONS[0].revision),
        )
    result = run_worker_process(
        legacy_database,
        "migrate",
        runtime_mode="collector",
    )
    payload = json.loads(result.stdout)
    assert result.returncode == 2
    assert payload["error"]["code"] == "migration_failed"
    with sqlite3.connect(legacy_database) as connection:
        recorded = dict(
            connection.execute(
                "SELECT revision, checksum FROM schema_migrations ORDER BY revision"
            )
        )
    assert set(recorded) == {migration.revision for migration in MIGRATIONS[:4]}
    assert recorded[MIGRATIONS[0].revision] == "0" * 64


def _create_snapshot_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE kam_items (id INTEGER PRIMARY KEY, rcept_no TEXT NOT NULL, dcm_no TEXT, corp_code TEXT NOT NULL, bsns_year INTEGER NOT NULL, source_type TEXT NOT NULL, ordinal INTEGER NOT NULL, title TEXT, normalized_topic TEXT, reason_text TEXT, audit_response_text TEXT, related_note_references_json TEXT NOT NULL, full_body_hash TEXT NOT NULL, full_body_length INTEGER NOT NULL, source_basis TEXT NOT NULL, parser_version TEXT NOT NULL, quality_status TEXT NOT NULL);
            CREATE TABLE audit_procedure_items (id INTEGER PRIMARY KEY, rcept_no TEXT NOT NULL, dcm_no TEXT, corp_code TEXT NOT NULL, bsns_year INTEGER NOT NULL, source_type TEXT NOT NULL, kam_item_id INTEGER, kam_topic TEXT, method TEXT, procedure_type TEXT NOT NULL, procedure_text TEXT NOT NULL, procedure_hash TEXT, procedure_length INTEGER, assertion_hints_json TEXT, linked_metric_keys_json TEXT, linked_note_keys_json TEXT, linked_event_keys_json TEXT, parser_version TEXT, quality_status TEXT, section_ordinal INTEGER NOT NULL, procedure_ordinal INTEGER NOT NULL);
            CREATE TABLE audit_fee_observations (observation_hash TEXT PRIMARY KEY, source_slot_hash TEXT NOT NULL, corp_code TEXT NOT NULL, bsns_year INTEGER NOT NULL, source_class TEXT NOT NULL, source_rcept_no TEXT, source_period TEXT, contract_fee_m INTEGER, contract_hours INTEGER, actual_fee_m INTEGER, actual_hours INTEGER, availability_status TEXT NOT NULL, quality_status TEXT NOT NULL, parser_version TEXT NOT NULL, is_current BOOLEAN NOT NULL, supersedes_hash TEXT, observed_at DATETIME NOT NULL);
            CREATE TABLE financial_facts_compact (corp_code TEXT NOT NULL, bsns_year INTEGER NOT NULL, fs_div TEXT NOT NULL, metric_key TEXT NOT NULL, amount INTEGER, source_account_id TEXT, source_table TEXT, unit TEXT, period_type TEXT, citation_rcept_no TEXT, citation_report_nm TEXT, citation_basis TEXT NOT NULL, quality_status TEXT NOT NULL, fetched_at DATETIME NOT NULL);
            CREATE TABLE company_year_quality (corp_code TEXT NOT NULL, bsns_year INTEGER NOT NULL, input_fingerprint TEXT NOT NULL, evidence_summary_json TEXT NOT NULL, quality_version TEXT NOT NULL, updated_at DATETIME NOT NULL);
            """
        )
        connection.execute("INSERT INTO kam_items VALUES (1, '20260310000001', '1', '00126380', 2025, 'audit_report', 1, '수익', 'revenue', '추정', '검증', '{\"b\":2,\"a\":1}', 'abc', 10, 'full_body', 'v1', 'usable')")
        connection.execute("INSERT INTO audit_procedure_items VALUES (1, '20260310000001', '1', '00126380', 2025, 'audit_report', 1, 'revenue', 'inspection', 'substantive', '증빙 검토', 'def', 5, '[\"existence\"]', '[\"revenue\"]', '[]', '[]', 'v1', 'usable', 1, 1)")
        connection.commit()
    finally:
        connection.close()


def test_valid_signed_marker_allows_readonly_snapshot(tmp_path: Path) -> None:
    """Catch a verifier that rejects the Task1/Task3 signed receipt interface."""
    database = tmp_path / "rehearsal" / "snapshot.db"
    database.parent.mkdir()
    _create_snapshot_database(database)
    marker = write_marker(database)
    result = run_worker_process(database, "semantic-snapshot", marker=marker)
    assert result.returncode == 0, result.stdout


def test_unsigned_self_asserted_marker_is_rejected(tmp_path: Path) -> None:
    """Catch a forgeable marker whose identity fields are not authenticated."""
    database = tmp_path / "rehearsal" / "snapshot.db"
    database.parent.mkdir()
    _create_snapshot_database(database)
    marker = write_unsigned_self_asserted_marker(database)
    result = run_worker_process(database, "semantic-snapshot", marker=marker)
    assert result.returncode == 2
    assert json.loads(result.stdout)["error"]["code"] == "rehearsal_binding_required"


def test_signed_marker_rejects_database_equal_to_source(tmp_path: Path) -> None:
    """Catch a signed receipt that relabels the live source itself as the clone."""
    database = tmp_path / "rehearsal" / "snapshot.db"
    database.parent.mkdir()
    _create_snapshot_database(database)
    control = write_marker(database)
    assert run_worker_process(database, "semantic-snapshot", marker=control).returncode == 0
    marker = write_marker(database, source_path=database)
    result = run_worker_process(database, "semantic-snapshot", marker=marker)
    assert result.returncode == 2
    assert json.loads(result.stdout)["error"]["code"] == "rehearsal_binding_required"


def test_signed_marker_rejects_database_inside_protected_repository(tmp_path: Path) -> None:
    """Catch a signed receipt that puts the mutable clone under a protected repository root."""
    database = tmp_path / "rehearsal" / "snapshot.db"
    database.parent.mkdir()
    _create_snapshot_database(database)
    control = write_marker(database)
    assert run_worker_process(database, "semantic-snapshot", marker=control).returncode == 0
    marker = write_marker(database, repository_root=database.parent)
    result = run_worker_process(database, "semantic-snapshot", marker=marker)
    assert result.returncode == 2
    assert json.loads(result.stdout)["error"]["code"] == "rehearsal_binding_required"


def test_migrate_rejects_clone_digest_changed_after_receipt(
    legacy_database: Path,
) -> None:
    """Catch writable migration starting after the signed clone bytes changed."""
    marker = write_marker(legacy_database)
    with sqlite3.connect(legacy_database) as connection:
        connection.execute("CREATE TABLE post_clone_mutation (id INTEGER PRIMARY KEY)")
    result = run_worker_process(
        legacy_database,
        "migrate",
        runtime_mode="collector",
        marker=marker,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["error"]["code"] == "rehearsal_binding_required"
    with sqlite3.connect(legacy_database) as connection:
        revisions = {
            row[0] for row in connection.execute("SELECT revision FROM schema_migrations")
        }
    assert revisions == {migration.revision for migration in MIGRATIONS[:4]}


def test_rehearsal_capability_is_redacted_from_failure_output(tmp_path: Path) -> None:
    """Catch the HMAC capability leaking through bounded stdout or diagnostics."""
    database = tmp_path / "rehearsal" / "snapshot.db"
    database.parent.mkdir()
    _create_snapshot_database(database)
    marker = write_unsigned_self_asserted_marker(database)
    secret = "d" * 64
    result = run_worker_process(
        database,
        "semantic-snapshot",
        marker=marker,
        capability=secret,
    )
    assert result.returncode == 2
    assert secret not in result.stdout
    assert secret not in result.stderr


@pytest.mark.parametrize("symlink_kind", ["final", "component"])
def test_worker_rejects_symlink_in_raw_database_path(
    tmp_path: Path,
    symlink_kind: str,
) -> None:
    """Catch raw DB_URL symlinks being normalized into an apparently safe path."""
    real_dir = tmp_path / "real-rehearsal"
    real_dir.mkdir()
    database = real_dir / "snapshot.db"
    _create_snapshot_database(database)
    marker = write_marker(database)
    if symlink_kind == "final":
        raw_database = tmp_path / "snapshot-link.db"
        raw_database.symlink_to(database)
    else:
        alias = tmp_path / "rehearsal-alias"
        alias.symlink_to(real_dir, target_is_directory=True)
        raw_database = alias / database.name
    result = run_worker_process(
        raw_database,
        "semantic-snapshot",
        marker=marker,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["error"]["code"] == "rehearsal_binding_required"
    assert str(raw_database) not in result.stdout


def _create_probe_database(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE action_probe (id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO action_probe (id, value) VALUES (1, ?)",
            (value,),
        )


def _probe_value(path: Path) -> str:
    with sqlite3.connect(path) as connection:
        return str(
            connection.execute(
                "SELECT value FROM action_probe WHERE id=1"
            ).fetchone()[0]
        )


def test_writable_open_rejects_path_swap_between_receipt_and_sqlite_connect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catch a replacement file winning the race between validation and mode=rw open."""
    import kreports.maintenance.kam_rehearsal_worker as worker

    database = tmp_path / "rehearsal" / "clone.db"
    replacement = tmp_path / "replacement.db"
    moved_original = tmp_path / "original-held.db"
    _create_probe_database(database, "original")
    _create_probe_database(replacement, "replacement")
    replacement_sha = _sha256_file(replacement)
    marker = write_marker(database)
    monkeypatch.setenv("DB_URL", f"sqlite:///{database}")
    monkeypatch.setenv("KREPORTS_REHEARSAL_MARKER", str(marker))
    monkeypatch.setenv("KREPORTS_REHEARSAL_CAPABILITY", TEST_CAPABILITY)
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    binding = worker._require_rehearsal_binding(require_initial_digest=False)
    real_connect = worker.sqlite3.connect
    swapped = False

    def swapping_connect(*args, **kwargs):
        nonlocal swapped
        if not swapped:
            swapped = True
            os.replace(database, moved_original)
            os.replace(replacement, database)
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(worker.sqlite3, "connect", swapping_connect)
    with (
        pytest.raises(worker.WorkerActionError) as caught,
        worker._open_pinned_database(binding, collector=True),
    ):
        pytest.fail("a swapped path must not reach the action")
    assert caught.value.code == "rehearsal_binding_required"
    assert _sha256_file(database) == replacement_sha
    assert _probe_value(database) == "replacement"


def test_writable_open_fd_binding_survives_aba_swap_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catch SQLite reopening a replacement pathname during an ABA window."""
    import kreports.maintenance.kam_rehearsal_worker as worker

    database = tmp_path / "rehearsal" / "clone.db"
    replacement = tmp_path / "replacement.db"
    held_original = tmp_path / "held-original.db"
    _create_probe_database(database, "original")
    _create_probe_database(replacement, "replacement")
    marker = write_marker(database)
    monkeypatch.setenv("DB_URL", f"sqlite:///{database}")
    monkeypatch.setenv("KREPORTS_REHEARSAL_MARKER", str(marker))
    monkeypatch.setenv("KREPORTS_REHEARSAL_CAPABILITY", TEST_CAPABILITY)
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    binding = worker._require_rehearsal_binding(require_initial_digest=False)
    real_connect = worker.sqlite3.connect

    def aba_connect(*args, **kwargs):
        os.replace(database, held_original)
        os.replace(replacement, database)
        try:
            return real_connect(*args, **kwargs)
        finally:
            os.replace(database, replacement)
            os.replace(held_original, database)

    monkeypatch.setattr(worker.sqlite3, "connect", aba_connect)
    with worker._open_pinned_database(binding, collector=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("UPDATE action_probe SET value='written' WHERE id=1")
        connection.commit()

    monkeypatch.setattr(worker.sqlite3, "connect", real_connect)
    assert _probe_value(database) == "written"
    assert _probe_value(replacement) == "replacement"


def test_writable_open_uses_authenticated_fd_normal_vfs_and_memory_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catch a pathname/lockless DBAPI open or rollback/WAL sidecar policy."""
    import kreports.maintenance.kam_rehearsal_worker as worker

    database = tmp_path / "rehearsal" / "clone.db"
    _create_probe_database(database, "original")
    marker = write_marker(database)
    monkeypatch.setenv("DB_URL", f"sqlite:///{database}")
    monkeypatch.setenv("KREPORTS_REHEARSAL_MARKER", str(marker))
    monkeypatch.setenv("KREPORTS_REHEARSAL_CAPABILITY", TEST_CAPABILITY)
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    binding = worker._require_rehearsal_binding(require_initial_digest=False)
    real_connect = worker.sqlite3.connect
    opened_uris: list[str] = []

    def observing_connect(database_uri: str, *args, **kwargs):
        opened_uris.append(database_uri)
        return real_connect(database_uri, *args, **kwargs)

    monkeypatch.setattr(worker.sqlite3, "connect", observing_connect)
    with worker._open_pinned_database(binding, collector=True) as connection:
        main_path = str(
            connection.execute("PRAGMA database_list").fetchone()[2]
        )
        assert opened_uris == [f"file:/dev/fd/{main_path.rsplit('/', 1)[-1]}?mode=rw"]
        assert main_path.startswith("/dev/fd/")
        assert "vfs=unix-none" not in opened_uris[0]
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "memory"


def test_writable_transaction_swap_creates_no_sidecars_next_to_either_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catch a pinned write that leaves a journal or WAL beside a replacement."""
    import kreports.maintenance.kam_rehearsal_worker as worker

    database = tmp_path / "rehearsal" / "clone.db"
    replacement = tmp_path / "replacement.db"
    held_original = tmp_path / "held-original.db"
    _create_probe_database(database, "original")
    _create_probe_database(replacement, "replacement")
    marker = write_marker(database)
    monkeypatch.setenv("DB_URL", f"sqlite:///{database}")
    monkeypatch.setenv("KREPORTS_REHEARSAL_MARKER", str(marker))
    monkeypatch.setenv("KREPORTS_REHEARSAL_CAPABILITY", TEST_CAPABILITY)
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    binding = worker._require_rehearsal_binding(require_initial_digest=False)

    with (
        pytest.raises(worker.WorkerActionError) as caught,
        worker._open_pinned_database(binding, collector=True) as connection,
    ):
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "memory"
        os.replace(database, held_original)
        os.replace(replacement, database)
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("UPDATE action_probe SET value='written' WHERE id=1")
        for path in (database, replacement, held_original):
            assert not Path(f"{path}-journal").exists()
            assert not Path(f"{path}-wal").exists()
        connection.commit()
    assert caught.value.code == "rehearsal_binding_required"
    assert _probe_value(database) == "replacement"
    for path in (database, replacement, held_original):
        assert not Path(f"{path}-journal").exists()
        assert not Path(f"{path}-wal").exists()


def test_writable_action_never_reconnects_to_replacement_after_safe_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catch SQLAlchemy reconnecting by path after the safe DBAPI connection opened."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    import kreports.collector.report_document_collector as collector_module
    import kreports.db.engine as engine_module
    from kreports.maintenance.kam_rehearsal_worker import (
        WorkerActionError,
        execute_action,
    )

    database = tmp_path / "rehearsal" / "clone.db"
    replacement = tmp_path / "replacement.db"
    moved_original = tmp_path / "original-held.db"
    _create_probe_database(database, "original")
    _create_probe_database(replacement, "replacement")
    marker = write_marker(database)
    monkeypatch.setenv("DB_URL", f"sqlite:///{database}")
    monkeypatch.setenv("KREPORTS_REHEARSAL_MARKER", str(marker))
    monkeypatch.setenv("KREPORTS_REHEARSAL_CAPABILITY", TEST_CAPABILITY)
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    raw_engine = create_engine(f"sqlite:///{database}")
    monkeypatch.setattr(engine_module, "engine", raw_engine)
    monkeypatch.setattr(
        engine_module,
        "SessionLocal",
        sessionmaker(bind=raw_engine, autocommit=False, autoflush=False),
    )

    def swap_then_write(**_kwargs):
        os.replace(database, moved_original)
        os.replace(replacement, database)
        with engine_module.get_session() as session:
            session.execute(
                text("UPDATE action_probe SET value='written' WHERE id=1")
            )
        return {"error": 0, "failed": 0, "receipts": []}

    monkeypatch.setattr(
        collector_module,
        "rebuild_kam_items",
        swap_then_write,
    )
    try:
        with pytest.raises(WorkerActionError) as caught:
            execute_action("kam-rebuild", year=2025)
    finally:
        raw_engine.dispose()
    assert caught.value.code == "rehearsal_binding_required"
    assert str(database) not in str(caught.value)
    assert _probe_value(database) == "replacement"
    assert _probe_value(moved_original) in {"original", "written"}


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


def test_semantic_snapshot_covers_evidence_semantics_but_not_timestamps(
    revision08_evidence_database: Path,
) -> None:
    """Catch provenance or freshness changes hidden by an incomplete digest."""
    migrated = run_worker(
        revision08_evidence_database,
        "migrate",
        runtime_mode="collector",
    )
    assert migrated["ok"] is True
    _seed_local_database_evidence(revision08_evidence_database)
    marker = write_marker(revision08_evidence_database)
    for action in (
        "audit-fee-observation-backfill",
        "financial-compact-rebuild",
        "company-year-quality-rebuild",
    ):
        result = run_worker(
            revision08_evidence_database,
            action,
            "--year",
            "2025",
            runtime_mode="collector",
            marker=marker,
        )
        assert result["ok"] is True

    first = run_worker(
        revision08_evidence_database,
        "semantic-snapshot",
        marker=marker,
    )
    second = run_worker(
        revision08_evidence_database,
        "semantic-snapshot",
        marker=marker,
    )

    assert first["semantic_sha256"] == second["semantic_sha256"]
    assert first["audit_fee_observations"]["current_count"] >= 1
    assert first["audit_fee_observations"]["historical_count"] >= 0
    assert first["financial_compact_provenance"]["uncitable_count"] >= 0
    assert first["company_year_quality_freshness"]["blank_fingerprint_count"] == 0

    with sqlite3.connect(revision08_evidence_database) as connection:
        connection.execute(
            "UPDATE audit_fee_observations SET actual_fee_m=actual_fee_m + 1"
        )
    changed_audit = run_worker(
        revision08_evidence_database,
        "semantic-snapshot",
        marker=marker,
    )
    assert changed_audit["semantic_sha256"] != first["semantic_sha256"]
    with sqlite3.connect(revision08_evidence_database) as connection:
        connection.execute(
            "UPDATE audit_fee_observations SET actual_fee_m=actual_fee_m - 1"
        )
        original_basis = connection.execute(
            "SELECT citation_basis FROM financial_facts_compact LIMIT 1"
        ).fetchone()[0]
        connection.execute(
            "UPDATE financial_facts_compact SET citation_basis='changed-basis'"
        )
    changed_financial = run_worker(
        revision08_evidence_database,
        "semantic-snapshot",
        marker=marker,
    )
    assert changed_financial["semantic_sha256"] != first["semantic_sha256"]
    with sqlite3.connect(revision08_evidence_database) as connection:
        connection.execute(
            "UPDATE financial_facts_compact SET citation_basis=?",
            (original_basis,),
        )
        original_fingerprint = connection.execute(
            "SELECT input_fingerprint FROM company_year_quality LIMIT 1"
        ).fetchone()[0]
        connection.execute(
            "UPDATE company_year_quality SET input_fingerprint=?",
            ("0" * 64,),
        )
    changed_quality = run_worker(
        revision08_evidence_database,
        "semantic-snapshot",
        marker=marker,
    )
    assert changed_quality["semantic_sha256"] != first["semantic_sha256"]
    with sqlite3.connect(revision08_evidence_database) as connection:
        connection.execute(
            "UPDATE company_year_quality SET input_fingerprint=?",
            (original_fingerprint,),
        )
        connection.execute(
            "UPDATE audit_fee_observations SET observed_at='2099-01-01'"
        )
        connection.execute(
            "UPDATE financial_facts_compact SET fetched_at='2099-01-01'"
        )
        connection.execute(
            "UPDATE company_year_quality SET updated_at='2099-01-01'"
        )
    timestamp_only = run_worker(
        revision08_evidence_database,
        "semantic-snapshot",
        marker=marker,
    )
    assert timestamp_only["semantic_sha256"] == first["semantic_sha256"]


def test_kam_rebuild_and_procedure_index_use_local_evidence_only(
    temp_engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catch an action changed to fetch a filing instead of using persisted evidence."""
    import httpx
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import kreports.db.engine as engine_module
    from kreports.db.engine import get_session
    from kreports.db.models import Base, Company, KamItem, SourceDocument
    from kreports.maintenance.kam_rehearsal_worker import execute_action

    def blocked_network(*_args, **_kwargs):
        raise AssertionError("KAM rehearsal must not make a network request")

    monkeypatch.setattr(socket.socket, "connect", blocked_network)
    monkeypatch.setattr(httpx.Client, "send", blocked_network)
    database = _bind_direct_rehearsal_marker(monkeypatch, tmp_path)
    clone_engine = create_engine(f"sqlite:///{database}")
    Base.metadata.create_all(bind=clone_engine)
    monkeypatch.setattr(engine_module, "engine", clone_engine)
    monkeypatch.setattr(
        engine_module,
        "SessionLocal",
        sessionmaker(bind=clone_engine, autocommit=False, autoflush=False),
    )
    with get_session() as session:
        session.add(Company(corp_code="00126380", stock_code="005930", corp_name="삼성전자", market="KOSPI"))
        session.add(SourceDocument(
            rcept_no="20260310000001", dcm_no="1", corp_code="00126380",
            bsns_year=2025, source_type="audit_report", report_nm="감사보고서",
            content_type="xml", raw_content=(
                "핵심감사사항\n수익인식\n핵심감사사항으로 선정한 이유\n"
                "추정\n감사에서 다루어진 방법\n계약서를 검사하였습니다."
            ), doc_hash="a" * 40,
            storage_status="inline", fetched_at=datetime(2026, 3, 10, tzinfo=UTC),
        ))
        session.add(KamItem(
            rcept_no="20260310000001", dcm_no="1", corp_code="00126380",
            bsns_year=2025, source_type="audit_report", ordinal=1, title="수익인식",
            normalized_topic="revenue", reason_text="추정", audit_response_text="계약서를 검사하였습니다.",
            related_note_references_json="[]", full_body_hash="b" * 40,
            full_body_length=50, source_basis="source_documents.raw_body",
            parser_version="v1", quality_status="usable",
            fetched_at=datetime(2026, 3, 10, tzinfo=UTC),
        ))

    try:
        dry = execute_action("kam-dry-run", year=2025)
        rebuilt = execute_action("kam-rebuild", year=2025)
        indexed = execute_action("procedure-index", year=2025)
    finally:
        clone_engine.dispose()
    assert dry["database_status"] == "available"
    assert dry["rows_written"] == 0
    assert rebuilt["receipt_counts"]["full_body"] == 1
    assert indexed["failed"] == 0
    assert indexed["rows_written"] >= 1


def test_evidence_rebuild_actions_are_bounded_to_local_year_and_never_network(
    revision08_evidence_database: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catch an evidence worker widening its year or constructing a DART client."""
    import httpx

    import kreports.collector.fetcher as fetcher_module
    from kreports.maintenance.kam_rehearsal_worker import execute_action

    migrated = run_worker(
        revision08_evidence_database,
        "migrate",
        runtime_mode="collector",
    )
    assert migrated["applied_revisions"] == [
        migration.revision for migration in MIGRATIONS[8:11]
    ]
    _seed_local_database_evidence(revision08_evidence_database)
    marker = write_marker(revision08_evidence_database)

    audit = run_worker(
        revision08_evidence_database,
        "audit-fee-observation-backfill",
        "--year",
        "2025",
        runtime_mode="collector",
        marker=marker,
    )
    financial = run_worker(
        revision08_evidence_database,
        "financial-compact-rebuild",
        "--year",
        "2025",
        runtime_mode="collector",
        marker=marker,
    )
    quality = run_worker(
        revision08_evidence_database,
        "company-year-quality-rebuild",
        "--year",
        "2025",
        runtime_mode="collector",
        marker=marker,
    )

    assert audit["ok"] is True
    assert audit["inserted_observations"] >= 1
    assert financial["ok"] is True
    assert financial["total_inserted_or_updated"] >= 1
    assert quality["ok"] is True
    assert quality["rows_written"] >= 1

    monkeypatch.setenv(
        "DB_URL",
        f"sqlite:///{revision08_evidence_database}",
    )
    monkeypatch.setenv("KREPORTS_REHEARSAL_MARKER", str(marker))
    monkeypatch.setenv("KREPORTS_REHEARSAL_CAPABILITY", TEST_CAPABILITY)
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")

    def blocked_network(*_args, **_kwargs):
        raise AssertionError("database evidence rehearsal must stay local")

    monkeypatch.setattr(socket.socket, "connect", blocked_network)
    monkeypatch.setattr(httpx, "Client", blocked_network)
    monkeypatch.setattr(fetcher_module, "_get_client", blocked_network)

    local_audit = execute_action(
        "audit-fee-observation-backfill",
        year=2025,
    )
    local_financial = execute_action("financial-compact-rebuild", year=2025)
    local_quality = execute_action("company-year-quality-rebuild", year=2025)

    assert local_audit["failed_company_years"] == 0
    assert local_financial["total_inserted_or_updated"] >= 1
    assert local_quality["rows_written"] >= 1


def _bind_direct_rehearsal_marker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Path:
    database = tmp_path / "direct-worker.db"
    sqlite3.connect(database).close()
    marker = write_marker(database)
    monkeypatch.setenv("DB_URL", f"sqlite:///{database}")
    monkeypatch.setenv("KREPORTS_REHEARSAL_MARKER", str(marker))
    monkeypatch.setenv("KREPORTS_REHEARSAL_CAPABILITY", TEST_CAPABILITY)
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    return database


def test_kam_rebuild_fails_closed_on_failed_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catch a KAM rebuild that reports failed receipts but still exits successfully."""
    import kreports.collector.report_document_collector as collector_module
    from kreports.maintenance.kam_rehearsal_worker import (
        WorkerActionError,
        execute_action,
    )

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
    from kreports.maintenance.kam_rehearsal_worker import (
        WorkerActionError,
        execute_action,
    )

    _bind_direct_rehearsal_marker(monkeypatch, tmp_path)
    monkeypatch.setattr(
        collector_module, "index_audit_procedures_from_sections", lambda **_kwargs: {"error": 1, "failed": 0},
    )
    with pytest.raises(WorkerActionError) as caught:
        execute_action("procedure-index", year=2025)
    assert caught.value.code == "backfill_failed"


def test_mcp_validator_rejects_schema_text_at_every_layer() -> None:
    """Catch public schema leakage even when the private diagnostic fields are clean."""
    from kreports.maintenance.kam_rehearsal_worker import (
        WorkerActionError,
        validate_professional_result,
    )

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
    from kreports.maintenance.kam_rehearsal_worker import (
        WorkerActionError,
        validate_professional_mcp,
    )

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
