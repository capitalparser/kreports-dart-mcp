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


def test_run_investor_core_backfill_emits_explicit_korean_safety_messages(
    tmp_path: Path,
    monkeypatch,
):
    from kreports.cli.main import app
    from kreports.maintenance import investor_core_backfill_runner as runner

    database = tmp_path / "runner.db"
    database.touch()
    expected_messages = {
        "backfill_already_running": "다른 투자자 핵심 백필이 이미 실행 중입니다",
        "single_writer_guard_unavailable": "단일 실행 잠금을 확보할 수 없어 투자자 핵심 백필을 시작하지 않았습니다",
        "database_hardlink_rejected": "데이터베이스 파일은 하드링크가 하나만 허용됩니다",
        "database_identity_changed": "실행 중 데이터베이스 파일 식별자가 변경되었습니다",
        "database_connection_identity_mismatch": "데이터베이스 연결이 요청한 파일과 일치하지 않습니다",
        "durability_checkpoint_failed": "SQLite WAL 내구성 체크포인트를 완료하지 못했습니다",
    }

    for code, message in expected_messages.items():
        def fail(*args, _code=code, **kwargs):
            del args, kwargs
            raise runner.InvestorCoreBackfillError(_code, "untrusted fixture detail")

        monkeypatch.setattr(runner, "run_investor_core_backfill", fail)
        result = CliRunner().invoke(
            app,
            ["run-investor-core-backfill", "--db", str(database)],
        )

        assert result.exit_code == 2
        assert json.loads(result.stdout) == {
            "error": {"code": code, "message": message},
        }
        assert "untrusted fixture detail" not in result.stdout


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
