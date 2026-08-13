#!/usr/bin/env python3
"""Small deployment wrapper for KReports release proof build/verification."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from kreports.release_artifact import (
    ReleaseManifest,
    build_release_manifest,
    verify_release_artifact,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--profile", default="public_runtime")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()

    if args.verify:
        result = verify_release_artifact(
            args.db,
            args.manifest,
            profile=args.profile,
        )
        print(json.dumps(result.model_dump(mode="json"), sort_keys=True))
        return 0 if result.ok else 1

    output = build_release_manifest(
        args.db,
        args.manifest,
        profile=args.profile,
    )
    manifest = ReleaseManifest.model_validate_json(output.read_text())
    print(
        json.dumps(
            {
                "artifact": str(output),
                "ready": manifest.release_gate.passed,
                "blockers": manifest.release_gate.blockers,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
