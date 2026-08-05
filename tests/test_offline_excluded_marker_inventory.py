"""Keep every offline-lane exclusion marker at an explicit, reviewed scope."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOCKET_GUARD = PROJECT_ROOT / "scripts" / "offline_test_guard"

EXPECTED_OFFLINE_EXCLUDED_NODES = {
    "live": frozenset(
        {
            "tests/test_professional_mcp_live.py::test_live_database_requires_explicit_absolute_regular_file",
            "tests/test_professional_mcp_live.py::test_samsung_fy2025_professional_public_result_matrix",
        }
    ),
    "apfs_real": frozenset(
        {
            "tests/test_kam_rehearsal_integration.py::test_real_rehearsal_migrates_rebuilds_and_preserves_source",
            "tests/test_rehearsal_safety.py::test_create_apfs_clone_with_real_cp_on_apfs",
        }
    ),
}


def _collect_marker_nodes(marker: str) -> frozenset[str]:
    """Collect marker nodes without executing their real DB or network work."""
    existing_pythonpath = os.environ.get("PYTHONPATH")
    environment = os.environ | {
        "DART_API_KEY": "",
        "DB_URL": "sqlite:///:memory:",
        "KREPORTS_LIVE_DB": "",
        "KREPORTS_RUN_LIVE_DB_TESTS": "0",
        "KREPORTS_OFFLINE_NETWORK_BLOCK": "1",
        "PYTHONPATH": (
            f"{SOCKET_GUARD}:{existing_pythonpath}"
            if existing_pythonpath
            else str(SOCKET_GUARD)
        ),
    }
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-m", marker, "tests"],
        cwd=PROJECT_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )
    return frozenset(
        line for line in completed.stdout.splitlines() if line.startswith("tests/")
    )


def test_live_and_apfs_real_marker_inventories_are_exact():
    """Catch a new offline exclusion being added without explicit review."""
    for marker, expected_nodes in EXPECTED_OFFLINE_EXCLUDED_NODES.items():
        assert _collect_marker_nodes(marker) == expected_nodes
