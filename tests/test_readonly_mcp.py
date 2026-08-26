import os
import sqlite3
import subprocess

from kreports.runtime import (
    is_readonly_mode,
    readonly_cache_miss,
    require_collector_mode,
    require_raw_backfill_mode,
)


def test_readonly_mode_defaults_to_true_for_mcp(monkeypatch):
    monkeypatch.delenv("KREPORTS_RUNTIME_MODE", raising=False)
    assert is_readonly_mode() is True


def test_collector_mode_can_be_enabled(monkeypatch):
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    assert is_readonly_mode() is False


def test_require_collector_mode_blocks_readonly(monkeypatch):
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")
    try:
        require_collector_mode("collect-policies")
    except RuntimeError as exc:
        assert "collect-policies requires collector mode" in str(exc)
    else:
        raise AssertionError("collector guard did not raise")


def test_require_raw_backfill_mode_blocks_without_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    monkeypatch.delenv("KREPORTS_ENABLE_RAW_BACKFILL", raising=False)

    try:
        require_raw_backfill_mode(
            "collect-business-report-sections",
            raw_storage_backend="gcs",
            raw_storage_keep_inline=False,
        )
    except RuntimeError as exc:
        assert "KREPORTS_ENABLE_RAW_BACKFILL=1" in str(exc)
    else:
        raise AssertionError("raw backfill guard did not raise")


def test_require_raw_backfill_mode_blocks_inline_storage(monkeypatch):
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    monkeypatch.setenv("KREPORTS_ENABLE_RAW_BACKFILL", "1")

    try:
        require_raw_backfill_mode(
            "collect-business-report-sections",
            raw_storage_backend="inline",
            raw_storage_keep_inline=False,
        )
    except RuntimeError as exc:
        assert "RAW_STORAGE_BACKEND=file or gcs" in str(exc)
    else:
        raise AssertionError("inline raw storage guard did not raise")


def test_require_raw_backfill_mode_blocks_keep_inline(monkeypatch):
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    monkeypatch.setenv("KREPORTS_ENABLE_RAW_BACKFILL", "1")

    try:
        require_raw_backfill_mode(
            "collect-business-report-sections",
            raw_storage_backend="gcs",
            raw_storage_keep_inline=True,
        )
    except RuntimeError as exc:
        assert "RAW_STORAGE_KEEP_INLINE=false" in str(exc)
    else:
        raise AssertionError("keep-inline raw storage guard did not raise")


def test_require_raw_backfill_mode_allows_external_storage(monkeypatch):
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    monkeypatch.setenv("KREPORTS_ENABLE_RAW_BACKFILL", "1")
    monkeypatch.setenv("RAW_STORAGE_BUCKET", "kreports-raw-documents")

    require_raw_backfill_mode(
        "collect-business-report-sections",
        raw_storage_backend="gcs",
        raw_storage_keep_inline=False,
    )


def test_readonly_cache_miss_message_does_not_request_dart_key():
    msg = readonly_cache_miss("accounting_policy", "00126380", 2025)
    assert "pre-built DB" in msg
    assert "DART_API_KEY" not in msg


def test_mcp_smoke_cli_fails_closed_without_seeded_data_or_dart_key(tmp_path):
    empty_database = tmp_path / "empty-runtime.db"
    sqlite3.connect(empty_database).close()
    environment = {
        "PATH": os.environ["PATH"],
        "KREPORTS_RUNTIME_MODE": "readonly",
        "DART_API_KEY": "",
        "DB_URL": f"sqlite:///{empty_database}",
    }
    proc = subprocess.run(
        [".venv/bin/kreports", "mcp-smoke", "--company", "005930"],
        text=True,
        capture_output=True,
        env=environment,
    )
    assert proc.returncode == 1
    assert "RESULT: CHECK REQUIRED" in proc.stdout
    assert "DART_API_KEY" not in proc.stdout
    assert "DART_API_KEY" not in proc.stderr
