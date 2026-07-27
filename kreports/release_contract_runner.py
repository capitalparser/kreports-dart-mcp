"""Isolated process boundary for explicit-DB release contract dispatches."""
from __future__ import annotations

from pathlib import Path
import sys

from kreports.release_artifact import (
    _CONTRACT_RUNNER_MARKER,
    _run_catalog_dispatch_contract,
)


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    passed = _run_catalog_dispatch_contract(Path(sys.argv[1]))
    print(f"{_CONTRACT_RUNNER_MARKER}{int(passed)}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
