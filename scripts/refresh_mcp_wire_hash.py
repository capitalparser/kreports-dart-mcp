#!/usr/bin/env python3
"""Refresh the approved MCP wire hash after an intentional schema change."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
TARGETS = (
    ROOT / "kreports" / "release_artifact.py",
    ROOT / "tests" / "test_all_tools_contract.py",
    ROOT / "tests" / "test_mcp_catalog.py",
)


def _current_approved_hash() -> str:
    path = TARGETS[0]
    text = path.read_text(encoding="utf-8")
    match = re.search(
        r"FROZEN_TOOL_WIRE_SHA256\s*=\s*\(\s*"
        r"\"([0-9a-f]{64})\"",
        text,
    )
    if match is None:
        raise RuntimeError(
            "FROZEN_TOOL_WIRE_SHA256 not found"
        )
    return match.group(1)


def _computed_hash() -> str:
    from kreports.mcp.catalog_extensions import (
        install_catalog_extensions,
    )

    install_catalog_extensions()
    from kreports.release_artifact import (
        _tool_wire_sha256,
    )

    return _tool_wire_sha256()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail when committed hashes do not match the current wire schema.",
    )
    args = parser.parse_args()

    approved = _current_approved_hash()
    computed = _computed_hash()
    if args.check:
        mismatches = []
        for path in TARGETS:
            text = path.read_text(encoding="utf-8")
            if computed not in text:
                mismatches.append(
                    str(path.relative_to(ROOT))
                )
        if approved != computed or mismatches:
            print(
                f"MCP wire hash mismatch: approved={approved} computed={computed}",
                file=sys.stderr,
            )
            if mismatches:
                print(
                    "missing from: " + ", ".join(mismatches),
                    file=sys.stderr,
                )
            return 1
        print(f"MCP wire hash OK: {computed}")
        return 0

    changed = []
    for path in TARGETS:
        text = path.read_text(encoding="utf-8")
        occurrences = text.count(approved)
        if occurrences < 1:
            raise RuntimeError(
                f"approved hash not found in {path.relative_to(ROOT)}"
            )
        updated = text.replace(approved, computed)
        path.write_text(updated, encoding="utf-8")
        changed.append(
            f"{path.relative_to(ROOT)} ({occurrences})"
        )

    print(f"old={approved}")
    print(f"new={computed}")
    print("updated:")
    for item in changed:
        print(f"- {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
