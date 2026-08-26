"""Container healthcheck for the authenticated public-runtime readiness gate."""
from __future__ import annotations

import os
from urllib import error, request


READY_URL = "http://127.0.0.1:8765/readyz"
READY_TIMEOUT_SECONDS = 20


def main() -> int:
    """Return non-zero when the authenticated release gate is not ready."""
    token = os.environ.get("KREPORTS_MCP_TOKEN", "")
    if not token:
        return 1
    ready_request = request.Request(
        READY_URL,
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with request.urlopen(
            ready_request,
            timeout=READY_TIMEOUT_SECONDS,
        ) as response:
            return 0 if response.status == 200 else 1
    except (error.HTTPError, error.URLError, TimeoutError):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
