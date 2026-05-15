import json
import os
import subprocess

from kreports.runtime import is_readonly_mode, readonly_cache_miss, require_collector_mode


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


def test_readonly_cache_miss_message_does_not_request_dart_key():
    msg = readonly_cache_miss("accounting_policy", "00126380", 2025)
    assert "pre-built DB" in msg
    assert "DART_API_KEY" not in msg


def test_mcp_smoke_cli_works_without_dart_key():
    proc = subprocess.run(
        [".venv/bin/kreports", "mcp-smoke", "--company", "005930"],
        text=True,
        capture_output=True,
        env={"PATH": os.environ["PATH"], "KREPORTS_RUNTIME_MODE": "readonly"},
    )
    assert proc.returncode == 0
    assert "RESULT: OK" in proc.stdout
    assert "DART_API_KEY" not in proc.stdout
    assert "DART_API_KEY" not in proc.stderr
