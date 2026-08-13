#!/usr/bin/env python3
"""Probe whether the configured DART API key can make a live request.

Exit codes:
  0  available
  64 missing configuration
  70 unexpected DART/network error
  75 temporary unavailable, including DART daily usage limit
"""

from __future__ import annotations

from datetime import date
import os
import sys


def main() -> int:
    if not os.environ.get("DART_API_KEY"):
        print("DART_API_KEY is not configured", file=sys.stderr)
        return 64

    # Import after env validation so pydantic settings sees sourced env files.
    from kreports.collector.fetcher import fetch_disclosure_list

    probe_date = os.environ.get("KREPORTS_DART_PROBE_DATE") or date.today().strftime("%Y%m%d")
    try:
        fetch_disclosure_list(None, probe_date, probe_date, disc_type="A")
    except Exception as exc:  # noqa: BLE001 - probe must classify all failures.
        msg = str(exc)
        if "status=020" in msg or "사용한도" in msg:
            print(f"DART API unavailable: usage limit exceeded ({probe_date})", file=sys.stderr)
            return 75
        print(f"DART API probe failed: {msg}", file=sys.stderr)
        return 70

    print(f"DART API available ({probe_date})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
