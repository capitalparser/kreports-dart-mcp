from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner


@pytest.fixture(autouse=True)
def _matching_runtime_build_sha(monkeypatch):
    monkeypatch.setenv("KREPORTS_BUILD_SHA", "a" * 40)


def _minimal_manifest_payload() -> dict:
    from kreports.release_artifact import FROZEN_TOOL_COUNT, FROZEN_TOOL_WIRE_SHA256

    return {
        "artifact_version": "1.0",
        "generated_at": "2026-07-27T00:00:00Z",
        "database": {
            "file_name": "runtime.db",
            "byte_count": 4096,
            "sha256": "a" * 64,
        },
        "schema": {
            "version": "20260711_08_group_audit_graph",
            "required_tables": ["companies"],
            "required_indexes": ["idx_company_year_quality_year_market"],
        },
        "dataset": {
            "version": "fixture-v1",
            "manifest_state": {"manifest_id": "fixture-v1"},
        },
        "tool_contract": {
            "version": "1.3",
            "tool_count": FROZEN_TOOL_COUNT,
            "wire_sha256": FROZEN_TOOL_WIRE_SHA256,
        },
        "release_gate": {
            "profile": "public_runtime",
            "passed": False,
            "blockers": ["investor_core_coverage"],
            "degraded_features": ["audit_procedure"],
            "coverage_year": 2025,
            "feature_coverage": {},
            "feature_grades": {},
        },
        "inline_raw_count": 0,
        "contracts": {
            "all_tools": {"passed": True, "checks": FROZEN_TOOL_COUNT},
            "golden_contract_sha256": "b" * 64,
            "golden_contract_passed": True,
        },
    }


def test_release_manifest_rejects_missing_unknown_and_malformed_fields():
    from kreports.release_artifact import ReleaseManifest

    payload = _minimal_manifest_payload()
    ReleaseManifest.model_validate(payload)

    missing = json.loads(json.dumps(payload))
    del missing["database"]["sha256"]
    with pytest.raises(ValidationError):
        ReleaseManifest.model_validate(missing)

    unknown = json.loads(json.dumps(payload))
    unknown["release_gate"]["trusted_stored_pass"] = True
    with pytest.raises(ValidationError):
        ReleaseManifest.model_validate(unknown)

    malformed = json.loads(json.dumps(payload))
    malformed["database"]["sha256"] = "not-a-digest"
    with pytest.raises(ValidationError):
        ReleaseManifest.model_validate(malformed)


def test_release_manifest_v11_binds_a_full_source_commit_sha():
    """A released DB must be bound to the exact code build that serves it."""
    from kreports.release_artifact import ReleaseManifest

    payload = _minimal_manifest_payload()
    payload["artifact_version"] = "1.1"
    payload["code"] = {"source_commit_sha": "a" * 40}

    parsed = ReleaseManifest.model_validate(payload)

    assert parsed.code.source_commit_sha == "a" * 40

    del payload["code"]
    with pytest.raises(ValidationError, match="code evidence is required"):
        ReleaseManifest.model_validate(payload)


def test_release_build_rejects_an_unbound_database(tmp_path, monkeypatch):
    """Removing the code SHA must prevent a deployable proof from being made."""
    from kreports import release_artifact

    db_path = tmp_path / "runtime.db"
    _create_contract_db(db_path)
    monkeypatch.setattr(
        release_artifact,
        "_collect_current_evidence",
        lambda _db, _profile: _minimal_manifest_payload(),
    )
    monkeypatch.delenv("KREPORTS_BUILD_SHA", raising=False)

    with pytest.raises(ValueError, match="source_commit_sha is required"):
        release_artifact.build_release_manifest(db_path)


def test_release_build_uses_the_configured_runtime_build_sha(
    tmp_path, monkeypatch
):
    """A deployment build may supply its immutable SHA through its environment."""
    from kreports import release_artifact

    db_path = tmp_path / "runtime.db"
    _create_contract_db(db_path)
    monkeypatch.setattr(
        release_artifact,
        "_collect_current_evidence",
        lambda _db, _profile: _minimal_manifest_payload(),
    )
    monkeypatch.setenv("KREPORTS_BUILD_SHA", "a" * 40)

    manifest_path = release_artifact.build_release_manifest(db_path)

    stored = json.loads(manifest_path.read_text())
    assert stored["code"] == {"source_commit_sha": "a" * 40}


def test_release_verification_rejects_a_different_runtime_build(
    tmp_path, monkeypatch
):
    """Serving a valid DB from another commit must fail closed."""
    from kreports import release_artifact

    db_path = tmp_path / "runtime.db"
    _create_contract_db(db_path)
    payload = _minimal_manifest_payload()
    payload["release_gate"] = {
        **payload["release_gate"],
        "passed": True,
        "blockers": [],
    }
    payload["contracts"]["golden_contract_sha256"] = (
        release_artifact.APPROVED_GOLDEN_CONTRACT_SHA256
    )
    monkeypatch.setattr(
        release_artifact,
        "_collect_current_evidence",
        lambda _db, _profile: json.loads(json.dumps(payload)),
    )
    monkeypatch.setenv("KREPORTS_BUILD_SHA", "b" * 40)

    manifest_path = release_artifact.build_release_manifest(
        db_path,
        source_commit_sha="a" * 40,
    )
    result = release_artifact.verify_release_artifact(db_path, manifest_path)

    assert "runtime_build_sha_mismatch" in result.failures


def test_release_manifest_preserves_materiality_coverage_metadata():
    """Catch a release proof that drops the materiality denominator policy."""
    from kreports.release_artifact import ReleaseManifest

    payload = _minimal_manifest_payload()
    payload["release_gate"]["feature_coverage"] = {
        "materiality_benchmark": {
            "numerator": 19,
            "denominator": 20,
            "coverage_pct": 95.0,
            "threshold_pct": 95.0,
        }
    }
    payload["release_gate"]["coverage_metadata"] = {
        "materiality_benchmark": {
            "window_years": 3,
            "receipt_policy": "exact_canonical_company_year_annual_filing",
        }
    }
    payload["release_gate"]["coverage_denominators"] = {
        "materiality_benchmark": 20
    }
    payload["release_gate"]["coverage_exclusions"] = {
        "materiality_benchmark": {
            "two_proven_years": 1,
            "zero_proven_years": 0,
        }
    }

    parsed = ReleaseManifest.model_validate(payload)

    assert parsed.release_gate.coverage_metadata == {
        "materiality_benchmark": {
            "window_years": 3,
            "receipt_policy": "exact_canonical_company_year_annual_filing",
        }
    }
    assert parsed.release_gate.coverage_denominators == {
        "materiality_benchmark": 20
    }
    assert parsed.release_gate.coverage_exclusions == {
        "materiality_benchmark": {
            "two_proven_years": 1,
            "zero_proven_years": 0,
        }
    }


def test_release_manifest_binds_legacy_investor_key_to_named_three_year_window():
    """Catch a release proof that keeps an ambiguous investor_core alias."""
    from kreports.release_artifact import ReleaseManifest

    payload = _minimal_manifest_payload()
    coverage = {
        "numerator": 19,
        "denominator": 20,
        "coverage_pct": 95.0,
        "threshold_pct": 95.0,
    }
    payload["release_gate"]["feature_coverage"] = {
        "investor_core_3y": coverage,
        "investor_timeseries_5y": {**coverage, "numerator": 10, "coverage_pct": 50.0},
        "investor_core": coverage,
    }
    payload["release_gate"]["coverage_metadata"] = {
        "investor_core_3y": {"window_years": 5, "minimum_available_years": 3},
        "investor_timeseries_5y": {"window_years": 5, "minimum_available_years": 5},
    }

    with pytest.raises(ValidationError, match="compatibility alias"):
        ReleaseManifest.model_validate(payload)

    payload["release_gate"]["coverage_metadata"]["investor_core"] = {
        "compatibility_alias_for": "investor_core_3y"
    }
    parsed = ReleaseManifest.model_validate(payload)

    assert parsed.release_gate.feature_coverage["investor_core"] == coverage


def _create_contract_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE companies (
                corp_code TEXT PRIMARY KEY,
                stock_code TEXT,
                corp_name TEXT NOT NULL,
                market TEXT
            );
            CREATE TABLE disclosures (rcept_no TEXT PRIMARY KEY);
            CREATE TABLE financials (id INTEGER PRIMARY KEY, year INTEGER);
            CREATE TABLE financial_facts_compact (id INTEGER PRIMARY KEY);
            CREATE TABLE report_sections (id INTEGER PRIMARY KEY);
            CREATE TABLE evidence_documents (id INTEGER PRIMARY KEY);
            CREATE TABLE backfill_runs (
                id INTEGER PRIMARY KEY,
                status TEXT,
                started_at TEXT
            );
            CREATE TABLE company_year_quality (
                corp_code TEXT NOT NULL,
                bsns_year INTEGER NOT NULL,
                market TEXT,
                investor_grade TEXT NOT NULL,
                auditor_grade TEXT NOT NULL,
                group_audit_grade TEXT NOT NULL,
                policy_status TEXT NOT NULL,
                audit_procedure_status TEXT NOT NULL,
                PRIMARY KEY (corp_code, bsns_year)
            );
            CREATE INDEX idx_company_year_quality_year_market
                ON company_year_quality (bsns_year, market);
            CREATE TABLE schema_migrations (
                revision TEXT PRIMARY KEY,
                checksum TEXT NOT NULL,
                description TEXT NOT NULL,
                applied_at TEXT NOT NULL
            );
            CREATE TABLE dataset_manifest (
                manifest_id TEXT PRIMARY KEY,
                schema_version TEXT NOT NULL,
                dataset_version TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                year_from INTEGER,
                year_to INTEGER,
                company_count INTEGER NOT NULL,
                disclosure_count INTEGER NOT NULL,
                evidence_document_count INTEGER NOT NULL,
                quality_snapshot_json TEXT NOT NULL,
                notes TEXT
            );
            CREATE TABLE source_documents (
                id INTEGER PRIMARY KEY,
                raw_content TEXT
            );
            """
        )
        connection.commit()
    finally:
        connection.close()


def test_build_is_atomic_preserves_db_and_records_named_gate_blockers(
    tmp_path,
    monkeypatch,
):
    from kreports import release_artifact

    db_path = tmp_path / "runtime.db"
    _create_contract_db(db_path)
    before = hashlib.sha256(db_path.read_bytes()).hexdigest()
    monkeypatch.setattr(
        release_artifact,
        "_collect_current_evidence",
        lambda _db, _profile: _minimal_manifest_payload(),
    )

    output = release_artifact.build_release_manifest(db_path)

    assert output == db_path.with_suffix(".db.release.json")
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == before
    stored = json.loads(output.read_text())
    assert stored["release_gate"]["passed"] is False
    assert stored["release_gate"]["blockers"] == ["investor_core_coverage"]
    assert not list(tmp_path.glob(".*.tmp"))


def test_default_path_and_two_builds_are_deterministic_except_generated_at(
    tmp_path,
    monkeypatch,
):
    from kreports import release_artifact

    db_path = tmp_path / "runtime.db"
    _create_contract_db(db_path)
    payloads = []
    for generated_at in (
        "2026-07-27T00:00:00Z",
        "2026-07-27T00:00:01Z",
    ):
        payload = _minimal_manifest_payload()
        payload["generated_at"] = generated_at
        payloads.append(payload)
    calls = iter(payloads)
    monkeypatch.setattr(
        release_artifact,
        "_collect_current_evidence",
        lambda _db, _profile: json.loads(json.dumps(next(calls))),
    )

    first_path = release_artifact.build_release_manifest(db_path)
    first = json.loads(first_path.read_text())
    second_path = release_artifact.build_release_manifest(db_path)
    second = json.loads(second_path.read_text())

    assert first_path == second_path == db_path.with_suffix(
        ".db.release.json"
    )
    first.pop("generated_at")
    second.pop("generated_at")
    assert first == second


def test_verify_recomputes_db_digest_and_reports_named_drift(tmp_path, monkeypatch):
    from kreports import release_artifact

    db_path = tmp_path / "runtime.db"
    _create_contract_db(db_path)
    payload = _minimal_manifest_payload()
    payload["artifact_version"] = "1.1"
    payload["code"] = {"source_commit_sha": "a" * 40}
    payload["database"] = {
        "file_name": db_path.name,
        "byte_count": db_path.stat().st_size,
        "sha256": hashlib.sha256(db_path.read_bytes()).hexdigest(),
    }
    payload["contracts"]["golden_contract_sha256"] = (
        release_artifact.APPROVED_GOLDEN_CONTRACT_SHA256
    )
    monkeypatch.setattr(
        release_artifact,
        "_collect_current_evidence",
        lambda _db, _profile: json.loads(json.dumps(payload)),
    )
    manifest_path = release_artifact.build_release_manifest(db_path)

    with db_path.open("ab") as handle:
        handle.write(b"drift")

    result = release_artifact.verify_release_artifact(
        db_path,
        manifest_path,
    )

    assert result.ok is False
    assert "database_size_mismatch" in result.failures
    assert "database_sha256_mismatch" in result.failures


def test_verify_recomputes_current_gate_and_ignores_tampered_pass(
    tmp_path,
    monkeypatch,
):
    from kreports import release_artifact

    db_path = tmp_path / "runtime.db"
    _create_contract_db(db_path)
    stored_ready = _minimal_manifest_payload()
    stored_ready["release_gate"] = {
        **stored_ready["release_gate"],
        "passed": True,
        "blockers": [],
    }
    current_blocked = _minimal_manifest_payload()
    calls = iter((stored_ready, current_blocked))
    monkeypatch.setattr(
        release_artifact,
        "_collect_current_evidence",
        lambda _db, _profile: json.loads(json.dumps(next(calls))),
    )

    manifest_path = release_artifact.build_release_manifest(db_path)
    result = release_artifact.verify_release_artifact(db_path, manifest_path)

    assert result.ok is False
    assert (
        "release_gate_blocked:investor_core_coverage"
        in result.failures
    )
    assert "release_gate_evidence_mismatch" in result.failures


def test_verify_explains_stale_32_tool_proof_against_current_33_tool_contract(
    tmp_path,
    monkeypatch,
):
    from kreports import release_artifact

    db_path = tmp_path / "runtime.db"
    _create_contract_db(db_path)
    stored = _minimal_manifest_payload()
    stored["database"] = {
        "file_name": db_path.name,
        "byte_count": db_path.stat().st_size,
        "sha256": hashlib.sha256(db_path.read_bytes()).hexdigest(),
    }
    stored["release_gate"] = {
        **stored["release_gate"],
        "passed": True,
        "blockers": [],
    }
    stored["tool_contract"] = {
        **stored["tool_contract"],
        "tool_count": 32,
        "wire_sha256": "0" * 64,
    }
    stored["contracts"] = {
        **stored["contracts"],
        "all_tools": {"passed": True, "checks": 32},
    }
    current = _minimal_manifest_payload()
    current["database"] = dict(stored["database"])
    current["release_gate"] = dict(stored["release_gate"])
    calls = iter((stored, current))
    monkeypatch.setattr(
        release_artifact,
        "_collect_current_evidence",
        lambda _db, _profile: json.loads(json.dumps(next(calls))),
    )

    manifest_path = release_artifact.build_release_manifest(db_path)
    result = release_artifact.verify_release_artifact(db_path, manifest_path)

    assert result.ok is False
    assert "tool_contract_evidence_mismatch" in result.failures
    diagnostics = {
        item.failure: item.model_dump() for item in result.diagnostics
    }
    assert diagnostics["tool_contract_evidence_mismatch"] == {
        "failure": "tool_contract_evidence_mismatch",
        "owner": "dataset_release_maintainer",
        "action": "rebuild the release manifest from the current approved 33-tool public catalog",
    }


def test_explicit_db_path_cannot_mix_with_global_engine(tmp_path, monkeypatch):
    import importlib
    import sys

    from sqlalchemy.orm import sessionmaker

    from kreports import release_artifact
    import kreports.db.engine as global_engine

    db_path = tmp_path / "explicit-runtime.db"
    _create_contract_db(db_path)

    class GlobalEngineSentinel:
        def connect(self):
            raise AssertionError("explicit DB proof touched the global engine")

    sentinel = GlobalEngineSentinel()
    lazy_modules = (
        "kreports.collector.on_demand",
        "kreports.quality.release_gate",
    )
    for module_name in lazy_modules:
        sys.modules.pop(module_name, None)
    monkeypatch.setattr(global_engine, "engine", sentinel)
    monkeypatch.setattr(
        global_engine,
        "SessionLocal",
        sessionmaker(bind=sentinel),
    )

    try:
        evidence = release_artifact._collect_current_evidence(
            db_path,
            "public_runtime",
        )
    finally:
        monkeypatch.undo()

    assert evidence["database"]["file_name"] == db_path.name
    assert evidence["release_gate"]["passed"] is False
    assert evidence["release_gate"]["blockers"]
    for module_name in lazy_modules:
        module = importlib.import_module(module_name)
        assert module.get_session is global_engine.get_session


def test_explicit_runtime_serializes_concurrent_database_bindings(tmp_path):
    """Two concurrent explicit contexts must never observe one another's DB."""
    import threading
    import time

    from kreports import release_artifact
    import kreports.db.engine as engine_module

    def create_marker_db(path, marker):
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE marker (value TEXT NOT NULL)")
            connection.execute("INSERT INTO marker(value) VALUES (?)", (marker,))

    first_db = tmp_path / "first.db"
    second_db = tmp_path / "second.db"
    create_marker_db(first_db, "first")
    create_marker_db(second_db, "second")
    original_engine = engine_module.engine
    start = threading.Barrier(2)
    failures: list[str] = []

    def worker(path, expected):
        try:
            start.wait(timeout=5)
            with release_artifact._bound_explicit_runtime(path):
                for _ in range(30):
                    with engine_module.engine.connect() as connection:
                        actual = connection.exec_driver_sql(
                            "SELECT value FROM marker"
                        ).scalar_one()
                    if actual != expected:
                        failures.append(f"{expected}:{actual}")
                    time.sleep(0.002)
        except Exception as exc:
            failures.append(type(exc).__name__)

    threads = [
        threading.Thread(target=worker, args=(first_db, "first")),
        threading.Thread(target=worker, args=(second_db, "second")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not any(thread.is_alive() for thread in threads)
    assert failures == []
    assert engine_module.engine is original_engine


def test_explicit_runtime_restores_nested_and_exceptional_bindings(tmp_path):
    """Re-entrant and exceptional contexts must restore the prior binding."""
    from kreports import release_artifact
    import kreports.db.engine as engine_module

    outer_db = tmp_path / "outer.db"
    inner_db = tmp_path / "inner.db"
    _create_contract_db(outer_db)
    _create_contract_db(inner_db)
    original_engine = engine_module.engine

    with release_artifact._bound_explicit_runtime(outer_db):
        outer_engine = engine_module.engine
        with release_artifact._bound_explicit_runtime(inner_db):
            assert engine_module.engine is not outer_engine
        assert engine_module.engine is outer_engine

    with pytest.raises(RuntimeError, match="test failure"):
        with release_artifact._bound_explicit_runtime(outer_db):
            raise RuntimeError("test failure")

    assert engine_module.engine is original_engine


def test_explicit_runtime_binds_analysis_queries_and_restores_central_engine(
    tmp_path,
    monkeypatch,
):
    """Removing any legacy engine rebinding must not reach the default database."""
    from datetime import UTC, date, datetime

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import kreports.db.engine as engine_module

    from kreports import release_artifact
    from kreports.analysis import (
        disclosure_events,
        investor_quality,
        kam_lifecycle,
        peer,
        policy_changes,
        raw_coverage,
        readiness,
    )
    from kreports.analysis.financial_timeseries import (
        get_financial_timeseries_quality,
    )
    from kreports.db.models import (
        AccountingNoteChapter,
        AccountingPolicyItem,
        AuditFee,
        Auditor,
        Base,
        Company,
        Disclosure,
        DisclosureEvent,
        Financial,
        FinancialFactCompact,
        ReportSection,
        SourceDocument,
    )
    db_path = tmp_path / "explicit-runtime.db"
    fixture_engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=fixture_engine)
    fixture_session = sessionmaker(bind=fixture_engine)()
    try:
        fixture_session.add_all(
            [
                Company(
                    corp_code="900001",
                    stock_code="900001",
                    corp_name="Explicit Runtime Co",
                    market="KOSPI",
                    induty_code="10101",
                ),
                Financial(
                    corp_code="900001",
                    year=2025,
                    quarter=4,
                    fs_div="CFS",
                    revenue=100,
                    operating_profit=10,
                    net_income=8,
                    total_assets=200,
                    total_debt=50,
                    total_equity=150,
                    operating_cf=12,
                ),
                Disclosure(
                    rcept_no="20250331000001",
                    corp_code="900001",
                    corp_name="Explicit Runtime Co",
                    disc_date=date(2025, 3, 31),
                    disc_type="A",
                    report_nm="사업보고서",
                ),
                DisclosureEvent(
                    rcept_no="20250331000001",
                    corp_code="900001",
                    event_date=datetime(2025, 3, 31, tzinfo=UTC),
                    event_type="audit",
                    event_title="synthetic event",
                    source_report_nm="사업보고서",
                ),
                FinancialFactCompact(
                    corp_code="900001",
                    bsns_year=2025,
                    fs_div="CFS",
                    metric_key="revenue",
                    metric_name="매출액",
                    amount=100,
                ),
                AuditFee(corp_code="900001", bsns_year=2025),
                Auditor(
                    corp_code="900001",
                    bsns_year=2025,
                    fs_div="CFS",
                    auditor_nm="Synthetic Auditor",
                ),
                SourceDocument(
                    rcept_no="20250331000001",
                    corp_code="900001",
                    bsns_year=2025,
                    source_type="business_report",
                    report_nm="사업보고서",
                    raw_content="synthetic",
                    doc_hash="a" * 40,
                ),
                AccountingPolicyItem(
                    corp_code="900001",
                    bsns_year=2025,
                    fs_div="CFS",
                    rcept_no="20250331000001",
                    item_key="revenue",
                    body="synthetic policy",
                ),
                ReportSection(
                    rcept_no="20250331000001",
                    corp_code="900001",
                    bsns_year=2025,
                    source_type="audit_report",
                    section_key="kam",
                    section_title="KAM",
                    body_text="핵심감사사항으로 결정 감사절차를 수행하였습니다",
                ),
                AccountingNoteChapter(
                    corp_code="900001",
                    bsns_year=2025,
                    fs_div="CFS",
                    rcept_no="20250331000001",
                    note_no="2",
                    section_type="policy",
                    body="synthetic policy",
                ),
            ]
        )
        fixture_session.commit()
    finally:
        fixture_session.close()
        fixture_engine.dispose()

    class RefusingEngine:
        def connect(self):
            raise AssertionError("explicit runtime touched a previous engine")

    stale_engine = RefusingEngine()
    original_engine = engine_module.engine
    original_sessions = engine_module.SessionLocal
    stale_sessions = sessionmaker(bind=stale_engine)
    monkeypatch.setattr(engine_module, "engine", stale_engine)
    monkeypatch.setattr(engine_module, "SessionLocal", stale_sessions)

    with release_artifact._bound_explicit_runtime(db_path):
        assert kam_lifecycle.kam_lifecycle_for_company(
            "900001", start_year=2025, end_year=2025
        )["event_count"] == 1
        assert disclosure_events.search_disclosure_events(company="900001")[
            "total_events"
        ] == 1
        assert peer.resolve_fs_div_for_company("900001", 2025) == "CFS"
        assert readiness.auditor_readiness_snapshot(2025, 1)["markets"]["KOSPI"][
            "listed"
        ] == 1
        assert raw_coverage.raw_annual_report_coverage(
            start_filing_year=2025, end_filing_year=2025
        )["totals"]["latest_reports"] == 1
        assert investor_quality.quality_of_earnings_pack(
            "900001", start_year=2025, end_year=2025
        )["company"] == "900001"
        assert policy_changes.accounting_policy_changes(
            "900001", start_year=2025, end_year=2025
        )["changes"]
        assert get_financial_timeseries_quality("900001", year=2025, years_back=1)[
            "rows"
        ]

    assert engine_module.engine is stale_engine
    assert engine_module.SessionLocal is stale_sessions
    monkeypatch.undo()
    assert engine_module.engine is original_engine
    assert engine_module.SessionLocal is original_sessions


def test_explicit_runtime_restores_a_lazily_imported_collector_engine(tmp_path):
    """A module first imported in the context must not retain its disposed engine."""
    import importlib
    import sys

    from sqlalchemy import text

    from kreports import release_artifact
    import kreports.db.engine as engine_module

    db_path = tmp_path / "explicit-runtime.db"
    _create_contract_db(db_path)
    module_name = "kreports.collector.report_document_collector"
    original_engine = engine_module.engine
    sys.modules.pop(module_name, None)
    try:
        with release_artifact._bound_explicit_runtime(db_path):
            collector = importlib.import_module(module_name)
            assert collector._engine_module.engine is not original_engine
            with collector.get_session() as session:
                assert session.execute(text("SELECT 1")).scalar() == 1

        assert collector._engine_module.engine is original_engine
    finally:
        sys.modules.pop(module_name, None)


def test_runtime_bound_modules_have_no_static_engine_aliases():
    """Adding a static engine import would bypass the explicit runtime boundary."""
    import ast

    package_root = Path(__file__).resolve().parents[1] / "kreports"
    static_engine_imports: list[str] = []
    for source_path in package_root.rglob("*.py"):
        tree = ast.parse(source_path.read_text(), filename=str(source_path))
        for node in tree.body:
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module != "kreports.db.engine":
                continue
            if any(alias.name == "engine" for alias in node.names):
                static_engine_imports.append(
                    str(source_path.relative_to(package_root))
                )

    assert static_engine_imports == []


def test_db_swap_or_nonempty_wal_during_proof_fails_without_replacing_manifest(
    tmp_path,
    monkeypatch,
):
    from kreports import release_artifact

    db_path = tmp_path / "runtime.db"
    _create_contract_db(db_path)
    manifest_path = tmp_path / "proof.json"
    manifest_path.write_text("preserve-existing-proof\n")
    db_path.with_name(f"{db_path.name}-wal").write_bytes(b"pending-wal")
    monkeypatch.setattr(
        release_artifact,
        "_collect_current_evidence",
        lambda _db, _profile: _minimal_manifest_payload(),
    )

    with pytest.raises(
        release_artifact.ReleaseArtifactError,
        match="nonempty_wal",
    ):
        release_artifact.build_release_manifest(
            db_path,
            manifest_path,
        )

    assert manifest_path.read_text() == "preserve-existing-proof\n"


def test_missing_table_index_inline_raw_and_duplicate_keys_are_named_blockers(
    tmp_path,
):
    from kreports import release_artifact

    db_path = tmp_path / "runtime.db"
    _create_contract_db(db_path)
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("DROP TABLE report_sections")
        connection.execute(
            "INSERT INTO source_documents(id, raw_content) VALUES (1, '<xml/>')"
        )
        connection.execute(
            "ALTER TABLE financial_facts_compact ADD COLUMN corp_code TEXT"
        )
        connection.execute(
            "ALTER TABLE financial_facts_compact ADD COLUMN bsns_year INTEGER"
        )
        connection.execute(
            "ALTER TABLE financial_facts_compact ADD COLUMN fs_div TEXT"
        )
        connection.execute(
            "ALTER TABLE financial_facts_compact ADD COLUMN metric_key TEXT"
        )
        connection.executemany(
            "INSERT INTO financial_facts_compact"
            "(id, corp_code, bsns_year, fs_div, metric_key) "
            "VALUES (?, '00126380', 2025, 'CFS', 'revenue')",
            [(1,), (2,)],
        )
        connection.commit()
    finally:
        connection.close()

    evidence = release_artifact._collect_current_evidence(
        db_path,
        "public_runtime",
    )

    assert evidence["inline_raw_count"] == 1
    blockers = evidence["release_gate"]["blockers"]
    assert "missing_required_table:report_sections" in blockers
    assert any(
        blocker.startswith("missing_required_index:")
        for blocker in blockers
    )
    assert "duplicate_key:financial_facts_compact" in blockers


def test_missing_index_target_tables_block_release(tmp_path, monkeypatch):
    from kreports import release_artifact

    db_path = tmp_path / "runtime.db"
    _create_contract_db(db_path)
    monkeypatch.setattr(
        release_artifact,
        "run_all_tool_contract",
        lambda _db: {"passed": True, "checks": 0},
    )
    monkeypatch.setattr(
        release_artifact,
        "golden_contract_result",
        lambda: {"passed": True, "sha256": "a" * 64, "cases": 1},
    )
    monkeypatch.setattr(
        release_artifact,
        "_tool_wire_sha256",
        lambda: "b" * 64,
    )

    evidence = release_artifact._collect_current_evidence(
        db_path,
        "public_runtime",
    )

    blockers = set(evidence["release_gate"]["blockers"])
    assert {
        "missing_required_table:audit_fees",
        "missing_required_table:group_entities",
        "missing_required_table:group_relationships",
        "missing_required_table:group_component_metrics",
        "missing_required_index:idx_audit_fee_availability_year",
        "missing_required_index:idx_group_entity_parent_year",
        "missing_required_index:idx_group_relationship_parent_year",
        "missing_required_index:idx_group_metric_parent_year",
    } <= blockers


@pytest.mark.parametrize(
    "missing_column",
    [
        "corp_code",
        "bsns_year",
        "fs_div",
        "metric_key",
        "amount",
        "source_account_id",
        "source_table",
        "unit",
        "period_type",
        "citation_rcept_no",
        "citation_report_nm",
        "citation_basis",
        "quality_status",
    ],
)
def test_missing_compact_provenance_column_blocks_release(
    tmp_path,
    missing_column,
):
    from kreports import release_artifact

    db_path = tmp_path / "runtime.db"
    _create_contract_db(db_path)
    all_columns = [
        "corp_code",
        "bsns_year",
        "fs_div",
        "metric_key",
        "amount",
        "source_account_id",
        "source_table",
        "unit",
        "period_type",
        "citation_rcept_no",
        "citation_report_nm",
        "citation_basis",
        "quality_status",
    ]
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("DROP TABLE financial_facts_compact")
        columns = ", ".join(
            f'"{column}" TEXT'
            for column in all_columns
            if column != missing_column
        )
        connection.execute(
            "CREATE TABLE financial_facts_compact "
            f"(id INTEGER PRIMARY KEY, {columns})"
        )
        connection.commit()
    finally:
        connection.close()

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        blockers = release_artifact._column_contract_blockers(
            connection,
            {"financial_facts_compact"},
        )
    finally:
        connection.close()

    assert (
        f"missing_required_column:financial_facts_compact.{missing_column}"
        in blockers
    )


def test_missing_kam_runtime_tables_and_linkage_columns_block_release(tmp_path):
    """Catches a revision-04 artifact passing despite revision-05/06 KAM needs."""
    from kreports import release_artifact

    db_path = tmp_path / "runtime.db"
    _create_contract_db(db_path)
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "CREATE TABLE audit_procedure_items ("
            "id INTEGER PRIMARY KEY, corp_code TEXT, bsns_year INTEGER)"
        )
        connection.commit()
    finally:
        connection.close()

    evidence = release_artifact._collect_current_evidence(
        db_path,
        "public_runtime",
    )

    blockers = evidence["release_gate"]["blockers"]
    assert "missing_required_table:kam_items" in blockers
    assert (
        "missing_required_column:audit_procedure_items.kam_item_id"
        in blockers
    )
    assert (
        "missing_required_column:audit_procedure_items.method"
        in blockers
    )


def test_inline_raw_count_excludes_derived_sections_and_blocks_original_bodies(
    tmp_path,
):
    from kreports import release_artifact

    db_path = tmp_path / "runtime.db"
    _create_contract_db(db_path)
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "ALTER TABLE source_documents ADD COLUMN content_type TEXT"
        )
        connection.execute(
            "ALTER TABLE source_documents ADD COLUMN report_nm TEXT"
        )
        connection.execute(
            "ALTER TABLE source_documents ADD COLUMN doc_hash TEXT"
        )
        for definition in (
            "rcept_no TEXT",
            "corp_code TEXT",
            "bsns_year INTEGER",
            "source_type TEXT",
        ):
            connection.execute(
                f"ALTER TABLE source_documents ADD COLUMN {definition}"
            )
        for definition in (
            "rcept_no TEXT",
            "corp_code TEXT",
            "bsns_year INTEGER",
            "source_type TEXT",
            "section_key TEXT",
            "section_title TEXT",
            "body_text TEXT",
            "ordinal INTEGER",
        ):
            connection.execute(
                f"ALTER TABLE report_sections ADD COLUMN {definition}"
            )
        connection.execute(
            "INSERT INTO report_sections "
            "(id, rcept_no, corp_code, bsns_year, source_type, "
            "section_key, section_title, body_text, ordinal) "
            "VALUES (1, '20250101000001', '00126380', 2025, "
            "'audit_report', 'kam', '핵심감사사항', "
            "'수익인식 감사절차', 0)"
        )
        derived_body = (
            "DERIVED FROM report_sections\n"
            "This is not the original DART filing body. It is a legacy "
            "evidence bundle reconstructed from cached extracted sections.\n"
            "\n"
            "## kam | 핵심감사사항\n"
            "rcept_no=20250101000001 source_type=audit_report ordinal=0\n"
            "수익인식 감사절차"
        )
        connection.executemany(
            "INSERT INTO source_documents"
            "(id, raw_content, content_type, report_nm, doc_hash, "
            "rcept_no, corp_code, bsns_year, source_type) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    1,
                    "<original/>",
                    "xml",
                    None,
                    None,
                    "20250101000002",
                    "00126380",
                    2025,
                    "audit_report",
                ),
                (
                    2,
                    derived_body,
                    "derived_report_sections",
                    "derived from report_sections",
                    hashlib.sha1(derived_body.encode()).hexdigest(),
                    "20250101000001",
                    "00126380",
                    2025,
                    "audit_report",
                ),
            ],
        )
        connection.commit()
    finally:
        connection.close()

    evidence = release_artifact._collect_current_evidence(
        db_path,
        "public_runtime",
    )

    assert evidence["inline_raw_count"] == 1
    assert (
        "inline_raw_bodies_present"
        in evidence["release_gate"]["blockers"]
    )
    assert evidence["release_gate"]["passed"] is False


def test_inline_raw_count_rejects_self_attested_unlinked_derived_body(
    tmp_path,
):
    from kreports import release_artifact

    db_path = tmp_path / "runtime.db"
    _create_contract_db(db_path)
    connection = sqlite3.connect(db_path)
    try:
        for definition in (
            "content_type TEXT",
            "report_nm TEXT",
            "doc_hash TEXT",
            "rcept_no TEXT",
            "corp_code TEXT",
            "bsns_year INTEGER",
            "source_type TEXT",
        ):
            connection.execute(
                f"ALTER TABLE source_documents ADD COLUMN {definition}"
            )
        body = "DERIVED FROM report_sections\n<original-filing/>"
        connection.execute(
            "INSERT INTO source_documents "
            "(id, raw_content, content_type, report_nm, doc_hash, "
            "rcept_no, corp_code, bsns_year, source_type) "
            "VALUES (1, ?, 'derived_report_sections', "
            "'derived from report_sections', ?, '20250101009999', "
            "'00126380', 2025, 'audit_report')",
            (body, hashlib.sha1(body.encode()).hexdigest()),
        )
        connection.commit()
    finally:
        connection.close()

    evidence = release_artifact._collect_current_evidence(
        db_path,
        "public_runtime",
    )

    assert evidence["inline_raw_count"] == 1
    assert "inline_raw_bodies_present" in evidence["release_gate"][
        "blockers"
    ]


def test_manifest_rejects_nonfinite_unbounded_and_unsupported_versions():
    from kreports.release_artifact import ReleaseManifest

    nonfinite = _minimal_manifest_payload()
    nonfinite["release_gate"]["feature_coverage"] = {
        "investor_core": {"coverage_pct": float("nan")}
    }
    with pytest.raises(ValidationError):
        ReleaseManifest.model_validate(nonfinite)

    unbounded = _minimal_manifest_payload()
    unbounded["inline_raw_count"] = 10**15
    with pytest.raises(ValidationError):
        ReleaseManifest.model_validate(unbounded)

    unsupported = _minimal_manifest_payload()
    unsupported["tool_contract"]["version"] = "2.0"
    with pytest.raises(ValidationError):
        ReleaseManifest.model_validate(unsupported)


def test_verify_rejects_duplicate_json_keys_and_oversized_manifest(
    tmp_path,
    monkeypatch,
):
    from kreports import release_artifact

    db_path = tmp_path / "runtime.db"
    _create_contract_db(db_path)
    payload = _minimal_manifest_payload()
    payload["database"] = {
        "file_name": db_path.name,
        "byte_count": db_path.stat().st_size,
        "sha256": hashlib.sha256(db_path.read_bytes()).hexdigest(),
    }
    monkeypatch.setattr(
        release_artifact,
        "_collect_current_evidence",
        lambda _db, _profile: json.loads(json.dumps(payload)),
    )
    manifest_path = release_artifact.build_release_manifest(db_path)
    original = manifest_path.read_text()
    manifest_path.write_text(
        original.replace(
            '"artifact_version":',
            '"artifact_version": "1.0", "artifact_version":',
            1,
        )
    )

    duplicate = release_artifact.verify_release_artifact(
        db_path,
        manifest_path,
    )

    assert duplicate.ok is False
    assert (
        "duplicate_manifest_key:artifact_version"
        in duplicate.failures
    )

    manifest_path.write_bytes(
        b"{" + b" " * (release_artifact.MAX_MANIFEST_BYTES + 1)
    )
    oversized = release_artifact.verify_release_artifact(
        db_path,
        manifest_path,
    )
    assert oversized.failures == ["manifest_too_large"]


def test_manifest_cannot_be_db_symlink_or_hardlink(tmp_path, monkeypatch):
    from kreports import release_artifact

    db_path = tmp_path / "runtime.db"
    _create_contract_db(db_path)
    monkeypatch.setattr(
        release_artifact,
        "_collect_current_evidence",
        lambda _db, _profile: _minimal_manifest_payload(),
    )

    with pytest.raises(ValueError, match="overwrite"):
        release_artifact.build_release_manifest(db_path, db_path)

    symlink = tmp_path / "symlink.json"
    symlink.symlink_to(db_path)
    with pytest.raises(ValueError, match="symlink"):
        release_artifact.build_release_manifest(db_path, symlink)

    hardlink = tmp_path / "hardlink.json"
    os.link(db_path, hardlink)
    with pytest.raises(ValueError, match="hardlink|alias"):
        release_artifact.build_release_manifest(db_path, hardlink)


def test_atomic_failure_or_db_swap_preserves_previous_manifest(
    tmp_path,
    monkeypatch,
):
    from kreports import release_artifact

    db_path = tmp_path / "runtime.db"
    _create_contract_db(db_path)
    manifest_path = tmp_path / "proof.json"
    manifest_path.write_text("old-proof\n")

    def mutate_during_collection(database, _profile):
        payload = _minimal_manifest_payload()
        with database.open("ab") as handle:
            handle.write(b"swap")
        return payload

    monkeypatch.setattr(
        release_artifact,
        "_collect_current_evidence",
        mutate_during_collection,
    )
    with pytest.raises(
        release_artifact.ReleaseArtifactError,
        match="database_changed_during_proof",
    ):
        release_artifact.build_release_manifest(db_path, manifest_path)
    assert manifest_path.read_text() == "old-proof\n"
    assert not list(tmp_path.glob(".*.tmp"))

    monkeypatch.setattr(
        release_artifact,
        "_collect_current_evidence",
        lambda _db, _profile: _minimal_manifest_payload(),
    )

    def replace_failure(*_args, **_kwargs):
        raise OSError("replace failed")

    monkeypatch.setattr(release_artifact.os, "replace", replace_failure)
    with pytest.raises(OSError, match="replace failed"):
        release_artifact.build_release_manifest(db_path, manifest_path)
    assert manifest_path.read_text() == "old-proof\n"
    assert not list(tmp_path.glob(".*.tmp"))


def test_release_gate_readiness_predicate_requires_ok_and_no_failures():
    from kreports.release_artifact import release_gate_is_ready

    ambiguous = {
        "ok": False,
        "profile": "public_runtime",
        "schema_version": "fixture",
        "dataset_version": "fixture",
        "required_failures": [],
        "degraded_features": [],
        "tool_count": 33,
    }
    assert release_gate_is_ready(ambiguous) is False


def test_runtime_readiness_uses_deployment_artifact_without_full_recompute(
    tmp_path,
    monkeypatch,
):
    from kreports import release_artifact

    db_path = tmp_path / "runtime.db"
    _create_contract_db(db_path)
    payload = _minimal_manifest_payload()
    payload["artifact_version"] = "1.1"
    payload["code"] = {"source_commit_sha": "a" * 40}
    payload["database"] = {
        "file_name": db_path.name,
        "byte_count": db_path.stat().st_size,
        "sha256": hashlib.sha256(db_path.read_bytes()).hexdigest(),
    }
    payload["contracts"]["golden_contract_sha256"] = (
        release_artifact.APPROVED_GOLDEN_CONTRACT_SHA256
    )
    db_path.with_suffix(".db.release.json").write_text(
        release_artifact.ReleaseManifest.model_validate(
            payload
        ).model_dump_json(by_alias=True)
    )
    monkeypatch.setattr(
        release_artifact,
        "_collect_current_evidence",
        lambda *_args, **_kwargs: pytest.fail(
            "runtime readiness must not recompute full artifact evidence"
        ),
    )

    report = release_artifact.evaluate_artifact_readiness(db_path)

    assert report["ok"] is False
    assert report["required_failures"] == ["investor_core_coverage"]


def test_runtime_readiness_rejects_nonempty_wal(
    tmp_path,
):
    from kreports import release_artifact

    db_path = tmp_path / "runtime.db"
    _create_contract_db(db_path)
    payload = _minimal_manifest_payload()
    payload["database"] = {
        "file_name": db_path.name,
        "byte_count": db_path.stat().st_size,
        "sha256": hashlib.sha256(db_path.read_bytes()).hexdigest(),
    }
    db_path.with_suffix(".db.release.json").write_text(
        release_artifact.ReleaseManifest.model_validate(
            payload
        ).model_dump_json(by_alias=True)
    )
    db_path.with_name(f"{db_path.name}-wal").write_bytes(b"pending")

    report = release_artifact.evaluate_artifact_readiness(db_path)

    assert report["ok"] is False
    assert "nonempty_wal" in report["required_failures"]


def test_runtime_readiness_rejects_large_wal_before_any_hash(
    tmp_path,
    monkeypatch,
):
    from kreports import release_artifact

    db_path = tmp_path / "runtime.db"
    _create_contract_db(db_path)
    db_path.with_suffix(".db.release.json").write_text(
        release_artifact.ReleaseManifest.model_validate(
            _minimal_manifest_payload()
        ).model_dump_json(by_alias=True)
    )
    db_path.with_name(f"{db_path.name}-wal").write_bytes(b"x" * 1_000_000)

    monkeypatch.setattr(
        release_artifact,
        "_sha256_file",
        lambda _path: pytest.fail("non-empty WAL must fail before hashing"),
    )

    report = release_artifact.evaluate_artifact_readiness(db_path)

    assert report["ok"] is False
    assert "nonempty_wal" in report["required_failures"]


def test_runtime_readiness_hashes_once_per_file_identity(
    tmp_path,
    monkeypatch,
):
    from kreports import release_artifact

    db_path = tmp_path / "runtime.db"
    _create_contract_db(db_path)
    payload = _minimal_manifest_payload()
    payload["database"] = {
        "file_name": db_path.name,
        "byte_count": db_path.stat().st_size,
        "sha256": hashlib.sha256(db_path.read_bytes()).hexdigest(),
    }
    payload["contracts"]["golden_contract_sha256"] = (
        release_artifact.APPROVED_GOLDEN_CONTRACT_SHA256
    )
    db_path.with_suffix(".db.release.json").write_text(
        release_artifact.ReleaseManifest.model_validate(
            payload
        ).model_dump_json(by_alias=True)
    )
    release_artifact._RUNTIME_DIGEST_CACHE.clear()
    real_sha256 = release_artifact._sha256_file
    calls = 0

    def counted(path):
        nonlocal calls
        calls += 1
        return real_sha256(path)

    monkeypatch.setattr(release_artifact, "_sha256_file", counted)

    release_artifact.evaluate_artifact_readiness(db_path)
    release_artifact.evaluate_artifact_readiness(db_path)

    assert calls == 1


def test_runtime_readiness_rehashes_and_rejects_same_size_db_change(
    tmp_path,
):
    from kreports import release_artifact

    db_path = tmp_path / "runtime.db"
    _create_contract_db(db_path)
    payload = _minimal_manifest_payload()
    payload["artifact_version"] = "1.1"
    payload["code"] = {"source_commit_sha": "a" * 40}
    payload["release_gate"] = {
        **payload["release_gate"],
        "passed": True,
        "blockers": [],
    }
    payload["database"] = {
        "file_name": db_path.name,
        "byte_count": db_path.stat().st_size,
        "sha256": hashlib.sha256(db_path.read_bytes()).hexdigest(),
    }
    payload["contracts"]["golden_contract_sha256"] = (
        release_artifact.APPROVED_GOLDEN_CONTRACT_SHA256
    )
    db_path.with_suffix(".db.release.json").write_text(
        release_artifact.ReleaseManifest.model_validate(
            payload
        ).model_dump_json(by_alias=True)
    )
    release_artifact._RUNTIME_DIGEST_CACHE.clear()

    before = release_artifact.evaluate_artifact_readiness(db_path)
    changed = bytearray(db_path.read_bytes())
    changed[-1] ^= 1
    db_path.write_bytes(changed)
    after = release_artifact.evaluate_artifact_readiness(db_path)

    assert before["ok"] is True
    assert after["ok"] is False
    assert "database_sha256_mismatch" in after["required_failures"]


def test_readyz_and_manifest_share_the_same_fail_closed_predicate(monkeypatch):
    from starlette.testclient import TestClient
    from kreports.mcp import http_server

    ambiguous = {
        "ok": False,
        "profile": "public_runtime",
        "schema_version": "fixture",
        "dataset_version": "fixture",
        "required_failures": [],
        "degraded_features": [],
        "tool_count": 33,
    }
    monkeypatch.setattr(
        http_server,
        "evaluate_release_gate",
        lambda _profile: ambiguous,
    )
    app = http_server.create_app(token="secret")
    with TestClient(app) as client:
        response = client.get("/readyz", headers={"Authorization": "Bearer secret"})
    assert response.status_code == 503


def test_cli_build_writes_blocked_proof_with_zero_but_verify_exits_nonzero(
    tmp_path,
    monkeypatch,
):
    from kreports import release_artifact
    from kreports.cli.main import app

    db_path = tmp_path / "runtime.db"
    _create_contract_db(db_path)
    manifest_path = tmp_path / "proof.json"
    payload = _minimal_manifest_payload()
    payload["database"] = {
        "file_name": db_path.name,
        "byte_count": db_path.stat().st_size,
        "sha256": hashlib.sha256(db_path.read_bytes()).hexdigest(),
    }
    manifest = release_artifact.ReleaseManifest.model_validate(payload)
    real_build = release_artifact.build_release_manifest

    def fake_build(_db, output, *, profile, source_commit_sha):
        assert profile == "public_runtime"
        assert source_commit_sha == "a" * 40
        output.write_text(
            manifest.model_dump_json(by_alias=True),
        )
        return output

    monkeypatch.setattr(
        release_artifact,
        "build_release_manifest",
        fake_build,
    )
    runner = CliRunner()
    built = runner.invoke(
        app,
        [
            "build-release-manifest",
            "--db",
            str(db_path),
            "--manifest",
            str(manifest_path),
            "--source-commit-sha",
            "a" * 40,
            "--json",
        ],
    )
    assert built.exit_code == 0
    assert json.loads(built.stdout)["ready"] is False
    assert json.loads(built.stdout)["blockers"] == [
        "investor_core_coverage"
    ]

    monkeypatch.setattr(
        release_artifact,
        "verify_release_artifact",
        lambda *_args, **_kwargs: release_artifact.VerificationResult(
            ok=False,
            failures=["release_gate_blocked:investor_core_coverage"],
        ),
    )
    verified = runner.invoke(
        app,
        [
            "verify-release-artifact",
            "--db",
            str(db_path),
            "--manifest",
            str(manifest_path),
            "--json",
        ],
    )
    assert verified.exit_code == 1
    assert json.loads(verified.stdout)["ok"] is False

    monkeypatch.setattr(
        release_artifact,
        "build_release_manifest",
        real_build,
    )
    unsafe = runner.invoke(
        app,
        [
            "build-release-manifest",
            "--db",
            str(db_path),
            "--manifest",
            str(db_path),
        ],
    )
    assert unsafe.exit_code == 2


def test_wrong_index_definition_with_expected_name_is_blocked(tmp_path):
    from kreports import release_artifact

    db_path = tmp_path / "runtime.db"
    _create_contract_db(db_path)
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "DROP INDEX idx_company_year_quality_year_market"
        )
        connection.execute(
            "CREATE INDEX idx_company_year_quality_year_market "
            "ON company_year_quality (market, bsns_year)"
        )
        connection.commit()
    finally:
        connection.close()

    evidence = release_artifact._collect_current_evidence(
        db_path,
        "public_runtime",
    )
    assert (
        "invalid_required_index:idx_company_year_quality_year_market"
        in evidence["release_gate"]["blockers"]
    )


def test_policy_chapter_table_and_identity_index_are_release_contract_blockers(
    tmp_path,
    monkeypatch,
):
    """A policy comparison dependency cannot be omitted or reordered at release."""
    from sqlalchemy import create_engine

    from kreports import release_artifact
    from kreports.db.migrations import apply_schema_migrations
    from kreports.db.models import Base

    database = tmp_path / "policy-contract.db"
    engine = create_engine(f"sqlite:///{database}")
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            apply_schema_migrations(connection)
    finally:
        engine.dispose()
    monkeypatch.setattr(
        release_artifact,
        "run_all_tool_contract",
        lambda _db: {"passed": True, "checks": 0},
    )
    monkeypatch.setattr(
        release_artifact,
        "golden_contract_result",
        lambda: {"passed": True, "sha256": "a" * 64, "cases": 1},
    )
    monkeypatch.setattr(release_artifact, "_tool_wire_sha256", lambda: "b" * 64)

    connection = sqlite3.connect(database)
    try:
        connection.execute("ALTER TABLE accounting_note_chapters DROP COLUMN body_hash")
        connection.execute("DROP INDEX uq_accounting_note_chapter_identity")
        connection.execute(
            "CREATE UNIQUE INDEX uq_accounting_note_chapter_identity "
            "ON accounting_note_chapters (corp_code, bsns_year, fs_div, note_no)"
        )
        connection.commit()
    finally:
        connection.close()

    evidence = release_artifact._collect_current_evidence(database, "public_runtime")
    assert "missing_required_column:accounting_note_chapters.body_hash" in evidence[
        "release_gate"
    ]["blockers"]
    assert "invalid_required_index:uq_accounting_note_chapter_identity" in evidence[
        "release_gate"
    ]["blockers"]


def test_verify_rejects_database_filename_mismatch(tmp_path, monkeypatch):
    from kreports import release_artifact

    db_path = tmp_path / "runtime.db"
    _create_contract_db(db_path)
    payload = _minimal_manifest_payload()
    monkeypatch.setattr(
        release_artifact,
        "_collect_current_evidence",
        lambda _db, _profile: json.loads(json.dumps(payload)),
    )
    manifest_path = release_artifact.build_release_manifest(db_path)
    stored = json.loads(manifest_path.read_text())
    stored["database"]["file_name"] = "different.db"
    manifest_path.write_text(json.dumps(stored))

    result = release_artifact.verify_release_artifact(
        db_path,
        manifest_path,
    )
    assert "database_filename_mismatch" in result.failures


def test_hardlinked_db_cannot_bypass_original_wal_state(tmp_path):
    from kreports import release_artifact

    original = tmp_path / "runtime.db"
    _create_contract_db(original)
    alias = tmp_path / "alias.db"
    os.link(original, alias)
    original.with_name(f"{original.name}-wal").write_bytes(b"pending")

    with pytest.raises(ValueError, match="hardlink"):
        release_artifact.build_release_manifest(alias)


def test_original_body_mislabeled_as_derived_remains_blocked(tmp_path):
    from kreports import release_artifact

    db_path = tmp_path / "runtime.db"
    _create_contract_db(db_path)
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "ALTER TABLE source_documents ADD COLUMN content_type TEXT"
        )
        connection.execute(
            "ALTER TABLE source_documents ADD COLUMN report_nm TEXT"
        )
        connection.execute(
            "ALTER TABLE source_documents ADD COLUMN doc_hash TEXT"
        )
        body = "<original-filing/>"
        connection.execute(
            "INSERT INTO source_documents"
            "(id, raw_content, content_type, report_nm, doc_hash) "
            "VALUES (1, ?, 'derived_report_sections', "
            "'original DART report', ?)",
            (body, hashlib.sha1(body.encode()).hexdigest()),
        )
        connection.commit()
    finally:
        connection.close()

    evidence = release_artifact._collect_current_evidence(
        db_path,
        "public_runtime",
    )
    assert evidence["inline_raw_count"] == 1
    assert "inline_raw_bodies_present" in evidence["release_gate"]["blockers"]


def test_manifest_redacts_or_rejects_secret_canaries_from_dataset_state(
    tmp_path,
):
    from kreports import release_artifact

    db_path = tmp_path / "runtime.db"
    _create_contract_db(db_path)
    secret = "task17-secret-canary"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "INSERT INTO dataset_manifest VALUES "
            "(?, 'schema', 'dataset', '2026-07-27', NULL, NULL, "
            "0, 0, 0, '{}', ?)",
            ("manifest", secret),
        )
        connection.commit()
    finally:
        connection.close()
    evidence = release_artifact._collect_current_evidence(
        db_path,
        "public_runtime",
    )
    serialized = json.dumps(evidence)
    assert secret not in serialized


def test_manifest_rejects_arbitrary_quality_snapshot_strings(
    tmp_path,
):
    from kreports import release_artifact

    db_path = tmp_path / "runtime.db"
    _create_contract_db(db_path)
    secret = "task17-quality-secret-canary"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "INSERT INTO dataset_manifest VALUES "
            "(?, 'schema', 'dataset', '2026-07-27', NULL, NULL, "
            "0, 0, 0, ?, NULL)",
            (
                "manifest",
                json.dumps({"debug": secret}),
            ),
        )
        connection.commit()
    finally:
        connection.close()

    evidence = release_artifact._collect_current_evidence(
        db_path,
        "public_runtime",
    )
    serialized = json.dumps(evidence)

    assert secret not in serialized
    assert evidence["dataset"]["manifest_state"]["quality_snapshot"] == {
        "status": "malformed"
    }


def test_feature_grades_include_only_release_coverage_year(tmp_path):
    from kreports import release_artifact

    db_path = tmp_path / "runtime.db"
    _create_contract_db(db_path)
    connection = sqlite3.connect(db_path)
    try:
        connection.executemany(
            "INSERT INTO company_year_quality "
            "(corp_code, bsns_year, market, investor_grade, auditor_grade, "
            "group_audit_grade, policy_status, audit_procedure_status) "
            "VALUES (?, ?, 'KOSPI', ?, ?, ?, 'full_body', 'available')",
            [
                ("00000001", 2024, "D", "D", "D"),
                ("00000001", 2025, "A", "B", "C"),
            ],
        )
        connection.commit()
    finally:
        connection.close()

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        grades = release_artifact._feature_grades(
            connection,
            {"company_year_quality"},
            coverage_year=2025,
        )
    finally:
        connection.close()
    assert grades == {
        "investor_core": {"A": 1},
        "auditor_full": {"B": 1},
        "group_audit": {"C": 1},
    }


def test_readyz_is_503_when_artifact_bound_index_or_contract_blocker_exists(
    monkeypatch,
):
    from starlette.testclient import TestClient
    from kreports.mcp import http_server

    monkeypatch.setattr(
        http_server,
        "evaluate_artifact_readiness",
        lambda *_args, **_kwargs: {
            "ok": False,
            "required_failures": [
                "invalid_required_index:idx_company_year_quality_year_market"
            ],
        },
        raising=False,
    )
    app = http_server.create_app(token="secret")
    with TestClient(app) as client:
        response = client.get("/readyz", headers={"Authorization": "Bearer secret"})
    assert response.status_code == 503


def test_explicit_db_cli_has_no_global_db_side_effects():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "import kreports.cli.main; "
                "print(int('kreports.db.engine' in sys.modules))"
            ),
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0"


def test_isolated_tool_contract_allows_slow_verified_runner(monkeypatch, tmp_path):
    """Release verification must accommodate a compact runtime on modest CPUs."""
    from kreports import release_artifact

    observed = {}

    def completed_runner(*args, **kwargs):
        observed["timeout"] = kwargs["timeout"]
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout="KREPORTS_RELEASE_CONTRACT=1\n",
            stderr="",
        )

    monkeypatch.setattr(release_artifact.subprocess, "run", completed_runner)

    assert release_artifact._isolated_catalog_dispatch_contract(
        tmp_path / "runtime.db"
    )
    assert observed["timeout"] == 300


def test_release_gate_evidence_excludes_host_local_listing_diagnostics():
    """A compact runtime proof must not depend on a builder's source-file cache."""
    from kreports import release_artifact

    builder_metadata = {
        "investor_core_3y": {"membership_status": "verified"},
        "listing_eligibility": {
            "verified_full_year": 2655,
            "normalized_artifact_available": 2655,
        },
    }
    runtime_metadata = {
        "investor_core_3y": {"membership_status": "verified"},
        "listing_eligibility": {
            "conflict": 2655,
            "normalized_artifact_unavailable": 2655,
        },
    }

    assert release_artifact._stable_release_gate_metadata(
        builder_metadata
    ) == release_artifact._stable_release_gate_metadata(runtime_metadata)
    assert release_artifact._stable_release_gate_metadata(
        builder_metadata
    ) == {"investor_core_3y": {"membership_status": "verified"}}


def test_wheel_contains_approved_golden_package_resource_and_hash(
    tmp_path,
):
    import subprocess
    import zipfile

    from kreports import release_artifact

    subprocess.run(
        [
            "uv",
            "build",
            "--wheel",
            "--out-dir",
            str(tmp_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(tmp_path.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        body = archive.read("kreports/data/golden_companies.json")
    assert hashlib.sha256(body).hexdigest() == (
        release_artifact.APPROVED_GOLDEN_CONTRACT_SHA256
    )
    assert release_artifact.golden_contract_result()["passed"] is True
