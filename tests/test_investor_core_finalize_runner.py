"""Safety tests for scoped post-backfill investor derived-data finalization."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import sqlite3
from types import SimpleNamespace

import pytest


GIB = 1024**3


def _create_database(database):
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE financial_facts_compact "
            "(corp_code TEXT NOT NULL, bsns_year INTEGER NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE company_year_quality "
            "(corp_code TEXT NOT NULL, bsns_year INTEGER NOT NULL)"
        )
        connection.execute("CREATE TABLE dataset_manifest (dataset_version TEXT)")


def _sha256(database):
    return hashlib.sha256(database.read_bytes()).hexdigest()


def _settings(database):
    return SimpleNamespace(db_url=f"sqlite:///{database}")


def _run(database, **overrides):
    from kreports.maintenance.investor_core_finalize_runner import (
        run_investor_core_finalize,
    )

    options = {
        "corp_codes": ["00000002", "00000001"],
        "year_from": 2021,
        "year_to": 2025,
        "quality_year": 2025,
        "dataset_version": "investor-core-test-v1",
        "disk_probe": lambda path: 20 * GIB,
        "settings_obj": _settings(database),
    }
    options.update(overrides)
    return run_investor_core_finalize(database, **options)


def test_finalize_dry_run_does_not_call_writers_or_change_database(tmp_path, monkeypatch):
    """Accidentally running default mode must neither bind nor mutate the DB."""
    from kreports.maintenance import investor_core_finalize_runner as runner

    database = tmp_path / "finalize.db"
    _create_database(database)
    monkeypatch.setenv("DB_URL", f"sqlite:///{database}")
    before = database.read_bytes()

    def unexpected_writer(*args, **kwargs):
        raise AssertionError("dry-run invoked a database writer")

    monkeypatch.setattr(runner, "rebuild_financial_facts_compact", unexpected_writer)
    monkeypatch.setattr(runner, "rebuild_company_year_quality", unexpected_writer)
    monkeypatch.setattr(runner, "write_dataset_manifest", unexpected_writer)

    report = _run(database)

    assert database.read_bytes() == before
    assert report["completed"] is True
    assert report["dry_run"] is True
    assert report["release_ready"] is False
    assert report["phases"] == {
        "compact": {"status": "planned", "result": None},
        "quality": {"status": "planned", "result": None},
        "manifest": {"status": "planned", "result": None},
    }


def test_finalize_rejects_manifest_id_collision_before_derived_writes(tmp_path, monkeypatch):
    """A tampered manifest identity must fail before compact/quality writes."""
    monkeypatch.delenv("DB_URL", raising=False)
    database = tmp_path / "manifest-id-collision.db"
    _create_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE dataset_manifest")
        connection.execute(
            "CREATE TABLE dataset_manifest (manifest_id TEXT PRIMARY KEY, dataset_version TEXT)"
        )
        connection.execute(
            "INSERT INTO dataset_manifest VALUES (?, ?)",
            ("investor-core-test-v1", "different-version"),
        )
    from kreports.maintenance import investor_core_finalize_runner as runner

    monkeypatch.setattr(
        runner, "rebuild_financial_facts_compact",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("writer reached")),
    )
    report = _run(database, execute=False)
    assert report["completed"] is False
    assert report["stop_reason"] == "dataset_version_exists"
    assert report["phases"]["manifest"]["status"] == "blocked"


@pytest.mark.parametrize(
    "corp_codes",
    [[], ["0000000"], [" 00000001"], "00000001", ["00000001", 2]],
)
def test_finalize_rejects_nonexact_company_scope_before_opening_database(corp_codes):
    """A malformed scope must never fall back to a broad derived rebuild."""
    from kreports.maintenance.investor_core_backfill_runner import (
        InvestorCoreBackfillError,
    )

    with pytest.raises(InvestorCoreBackfillError) as caught:
        _run("does-not-need-to-exist.db", corp_codes=corp_codes)

    assert caught.value.code == "invalid_corp_codes"


def test_finalize_execute_requires_expected_hash_and_collector_runtime(tmp_path, monkeypatch):
    """Execute must not reach a writer without both operator confirmations."""
    from kreports.maintenance.investor_core_backfill_runner import (
        InvestorCoreBackfillError,
    )

    database = tmp_path / "finalize.db"
    _create_database(database)
    monkeypatch.setenv("DB_URL", f"sqlite:///{database}")

    with pytest.raises(InvestorCoreBackfillError) as missing_hash:
        _run(database, execute=True)
    assert missing_hash.value.code == "expected_db_sha256_required"

    with pytest.raises(InvestorCoreBackfillError) as missing_runtime:
        _run(database, execute=True, expected_db_sha256=_sha256(database))
    assert missing_runtime.value.code == "collector_mode_required"


def test_finalize_execute_reports_disk_guard_without_writer_calls(tmp_path, monkeypatch):
    """The 10 GiB reserve must stop finalization before derived writes begin."""
    from kreports.maintenance import investor_core_finalize_runner as runner

    database = tmp_path / "finalize.db"
    _create_database(database)
    monkeypatch.setenv("DB_URL", f"sqlite:///{database}")
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")

    def unexpected_writer(*args, **kwargs):
        raise AssertionError("disk-guarded finalization invoked a writer")

    monkeypatch.setattr(runner, "rebuild_financial_facts_compact", unexpected_writer)

    report = _run(
        database,
        execute=True,
        expected_db_sha256=_sha256(database),
        disk_probe=lambda path: 9 * GIB,
    )

    assert report["completed"] is False
    assert report["stop_reason"] == "insufficient_free_space"
    assert report["phases"]["compact"]["status"] == "not_run"


def test_finalize_orders_scoped_writes_and_holds_lock_for_checkpoint_and_evidence(
    tmp_path,
    monkeypatch,
):
    """Manifest evidence must describe the same locked, scoped mutation."""
    from kreports.maintenance import investor_core_finalize_runner as runner

    database = tmp_path / "finalize.db"
    _create_database(database)
    monkeypatch.setenv("DB_URL", f"sqlite:///{database}")
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    calls = []
    lock_active = False
    evidence_calls = 0

    @contextmanager
    def tracked_guard(identity):
        nonlocal lock_active
        del identity
        lock_active = True
        try:
            yield
        finally:
            lock_active = False

    @contextmanager
    def bound_writer(identity):
        del identity
        assert lock_active is True
        yield None

    def compact(**kwargs):
        assert lock_active is True
        calls.append(("compact", kwargs))
        return {"rows_written": 3}

    def quality(*args, **kwargs):
        assert lock_active is True
        calls.append(("quality", args, kwargs))
        return {"rows_written": 2}

    def manifest(dataset_version, notes=None):
        assert lock_active is True
        calls.append(("manifest", dataset_version, notes))
        return {"dataset_version": dataset_version}

    def checkpoint(identity):
        del identity
        assert lock_active is True
        calls.append(("checkpoint",))
        return True

    original_counts = runner._scoped_row_counts

    def tracked_counts(path, scope):
        nonlocal evidence_calls
        evidence_calls += 1
        if evidence_calls == 2:
            assert lock_active is True
        return original_counts(path, scope)

    monkeypatch.setattr(runner, "_exclusive_execution_guard", tracked_guard)
    monkeypatch.setattr(runner, "_bound_financial_writer", bound_writer)
    monkeypatch.setattr(runner, "rebuild_financial_facts_compact", compact)
    monkeypatch.setattr(runner, "rebuild_company_year_quality", quality)
    monkeypatch.setattr(runner, "write_dataset_manifest", manifest)
    monkeypatch.setattr(runner, "_checkpoint_wal", checkpoint)
    monkeypatch.setattr(runner, "_scoped_row_counts", tracked_counts)

    report = _run(
        database,
        execute=True,
        expected_db_sha256=_sha256(database),
    )

    assert report["completed"] is True
    assert evidence_calls == 2
    assert [call[0] for call in calls] == [
        "compact", "quality", "manifest", "checkpoint",
    ]
    assert calls[0][1] == {
        "corp_codes": ("00000001", "00000002"),
        "year_from": 2021,
        "year_to": 2025,
    }
    assert calls[1][1] == (2025, 2025)
    assert calls[1][2] == {"corp_codes": ("00000001", "00000002")}
    assert calls[2][1] == "investor-core-test-v1"
    assert report["wal_checkpointed"] is True


def test_finalize_manifest_failure_returns_stable_incomplete_report(tmp_path, monkeypatch):
    """A manifest error after derived work must not escape with internal details."""
    from kreports.maintenance import investor_core_finalize_runner as runner

    database = tmp_path / "finalize.db"
    _create_database(database)
    monkeypatch.setenv("DB_URL", f"sqlite:///{database}")
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")

    @contextmanager
    def bound_writer(identity):
        del identity
        yield None

    monkeypatch.setattr(runner, "_bound_financial_writer", bound_writer)
    monkeypatch.setattr(runner, "rebuild_financial_facts_compact", lambda **kwargs: {})
    monkeypatch.setattr(runner, "rebuild_company_year_quality", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        runner,
        "write_dataset_manifest",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("token=secret")),
    )
    monkeypatch.setattr(runner, "_checkpoint_wal", lambda identity: True)

    report = _run(
        database,
        execute=True,
        expected_db_sha256=_sha256(database),
    )

    assert report["completed"] is False
    assert report["release_ready"] is False
    assert report["stop_reason"] == "manifest_failed"
    assert report["stop_message"] == "dataset manifest could not be written"
    assert report["phases"]["compact"]["status"] == "completed"
    assert report["phases"]["quality"]["status"] == "completed"
    assert report["phases"]["manifest"]["status"] == "failed"
    assert report["wal_checkpointed"] is True


def test_finalize_rechecks_disk_between_derived_phases(tmp_path, monkeypatch):
    """A compact write must not proceed to quality after crossing the reserve gate."""
    from kreports.maintenance import investor_core_finalize_runner as runner

    database = tmp_path / "finalize.db"
    _create_database(database)
    monkeypatch.setenv("DB_URL", f"sqlite:///{database}")
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    monkeypatch.setattr(
        runner,
        "rebuild_financial_facts_compact",
        lambda **kwargs: {"rows_written": 1},
    )
    monkeypatch.setattr(
        runner,
        "rebuild_company_year_quality",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("quality phase ran below disk reserve")
        ),
    )
    probes = iter([20 * GIB, 9 * GIB, 20 * GIB])

    report = _run(
        database,
        execute=True,
        expected_db_sha256=_sha256(database),
        disk_probe=lambda path: next(probes),
    )

    assert report["completed"] is False
    assert report["stop_reason"] == "insufficient_free_space"
    assert report["phases"]["compact"]["status"] == "completed"
    assert report["phases"]["quality"]["status"] == "not_run"
