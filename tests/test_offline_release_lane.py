"""Contract for the reproducible, no-live-input release-evidence lane."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_offline_runner_overrides_ambient_inputs_and_propagates_guard_to_child(tmp_path):
    """Catch a runner that inherits a .env key, live DB, or runs live markers."""
    probe = tmp_path / "test_offline_environment.py"
    probe.write_text(
        """
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import pytest


def test_offline_environment_and_child_guard():
    assert os.environ["DART_API_KEY"] == ""
    db_path = Path(os.environ["KREPORTS_OFFLINE_DB_PATH"])
    assert db_path.is_file()
    assert os.environ["DB_URL"] == f"sqlite:///{db_path}"
    assert os.environ["KREPORTS_RUNTIME_MODE"] == "readonly"
    assert os.environ["KREPORTS_LIVE_DB"] == ""
    assert os.environ["KREPORTS_OFFLINE_NETWORK_BLOCK"] == "1"
    with sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True) as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='companies'"
        ).fetchone() == (1,)
    with pytest.raises(OSError, match="offline network disabled"):
        socket.create_connection(("example.com", 443), timeout=0.1)
    child = subprocess.run(
        [sys.executable, "-c", "import json, os, socket; socket.create_connection(('example.com', 443), timeout=0.1)"],
        text=True,
        capture_output=True,
    )
    assert child.returncode != 0
    assert "offline network disabled" in child.stderr


@pytest.mark.live
def test_live_marker_must_not_run():
    assert False


@pytest.mark.live_data
def test_live_data_marker_must_not_run():
    assert False


@pytest.mark.apfs_real
def test_apfs_real_marker_must_not_run():
    assert False
""",
        encoding="utf-8",
    )
    environment = os.environ | {
        "DART_API_KEY": "ambient-secret-must-not-win",
        "DB_URL": "sqlite:////ambient-live.db",
        "KREPORTS_RUNTIME_MODE": "collector",
        "KREPORTS_LIVE_DB": "/tmp/ambient-live.db",
    }

    completed = subprocess.run(
        [str(PROJECT_ROOT / "scripts" / "run_offline_tests.sh"), str(probe)],
        cwd=PROJECT_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "1 passed" in completed.stdout
    assert "3 deselected" in completed.stdout
