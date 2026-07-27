from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner


def _minimal_manifest_payload() -> dict:
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
            "version": "1.0",
            "tool_count": 32,
            "wire_sha256": (
                "055f54993bf45f2e4a1388642871d09c1e2f45fc0b5fde1e83228bb910b38339"
            ),
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
            "all_tools": {"passed": True, "checks": 32},
            "golden_contract_sha256": "b" * 64,
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


def test_explicit_db_path_cannot_mix_with_global_engine(tmp_path, monkeypatch):
    from kreports import release_artifact
    import kreports.db.engine as global_engine

    db_path = tmp_path / "explicit-runtime.db"
    _create_contract_db(db_path)

    def global_engine_sentinel(*_args, **_kwargs):
        raise AssertionError("explicit DB proof touched the global engine")

    monkeypatch.setattr(global_engine, "get_session", global_engine_sentinel)

    evidence = release_artifact._collect_current_evidence(
        db_path,
        "public_runtime",
    )

    assert evidence["database"]["file_name"] == db_path.name
    assert evidence["release_gate"]["passed"] is False
    assert evidence["release_gate"]["blockers"]


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
        connection.executemany(
            "INSERT INTO source_documents(id, raw_content, content_type) "
            "VALUES (?, ?, ?)",
            [
                (1, "<original/>", "xml"),
                (2, "# reconstructed evidence", "derived_report_sections"),
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
    with pytest.raises(ValueError, match="alias"):
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
        "tool_count": 32,
    }
    assert release_gate_is_ready(ambiguous) is False


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
        "tool_count": 32,
    }
    monkeypatch.setattr(
        http_server,
        "evaluate_release_gate",
        lambda _profile: ambiguous,
    )
    app = http_server.create_app(token="secret")
    with TestClient(app) as client:
        response = client.get("/readyz")
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

    def fake_build(_db, output, *, profile):
        assert profile == "public_runtime"
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
