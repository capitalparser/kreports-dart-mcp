"""CLI contract tests for the bounded investor-core runner."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from typer.testing import CliRunner


def test_run_investor_core_backfill_defaults_to_json_dry_run(
    tmp_path: Path,
    monkeypatch,
):
    from kreports.cli.main import app
    from kreports.maintenance import investor_core_backfill_runner as runner

    database = tmp_path / "runner.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE marker (value TEXT NOT NULL)")
    monkeypatch.setattr(runner, "run_investor_core_backfill", lambda *args, **kwargs: {
        "schema": "investor_core_backfill_report",
        "version": 1,
        "dry_run": not kwargs["execute"],
        "execute": kwargs["execute"],
        "completed": True,
    })

    result = CliRunner().invoke(
        app,
        ["run-investor-core-backfill", "--db", str(database)],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "schema": "investor_core_backfill_report",
        "version": 1,
        "dry_run": True,
        "execute": False,
        "completed": True,
    }


def test_run_investor_core_backfill_emits_stable_json_error_and_nonzero_exit(
    tmp_path: Path,
    monkeypatch,
):
    from kreports.cli.main import app
    from kreports.maintenance import investor_core_backfill_runner as runner

    database = tmp_path / "runner.db"
    database.touch()
    secret = "must-not-appear"

    def fail(*args, **kwargs):
        del args, kwargs
        raise runner.InvestorCoreBackfillError(
            "expected_db_sha256_mismatch",
            f"database SHA-256 mismatch: {secret}",
        )

    monkeypatch.setattr(runner, "run_investor_core_backfill", fail)
    result = CliRunner().invoke(
        app,
        ["run-investor-core-backfill", "--db", str(database)],
    )

    assert result.exit_code == 2
    assert json.loads(result.stdout) == {
        "error": {
            "code": "expected_db_sha256_mismatch",
            "message": "database SHA-256 does not match expected value",
        }
    }
    assert secret not in result.stdout


def test_run_investor_core_backfill_incomplete_evidence_report_exits_three_without_secrets(
    tmp_path: Path,
    monkeypatch,
):
    from kreports.cli.main import app
    from kreports.maintenance import investor_core_backfill_runner as runner

    database = tmp_path / "runner.db"
    database.touch()
    secret = "fixture-secret-response"
    monkeypatch.setattr(runner, "run_investor_core_backfill", lambda *args, **kwargs: {
        "schema": "investor_core_backfill_report",
        "completed": False,
        "stop_reason": "evidence_collection_failed",
        "stop_message": "post-run evidence could not be collected",
        "target_outcomes": {"counts": {"success": 1}},
    })

    result = CliRunner().invoke(
        app,
        ["run-investor-core-backfill", "--db", str(database)],
    )

    assert result.exit_code == 3
    assert json.loads(result.stdout)["stop_reason"] == "evidence_collection_failed"
    assert secret not in result.stdout
