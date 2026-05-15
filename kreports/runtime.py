from __future__ import annotations

import os
from typing import Any

READONLY = "readonly"
COLLECTOR = "collector"


def runtime_mode() -> str:
    raw = os.environ.get("KREPORTS_RUNTIME_MODE", READONLY).strip().lower()
    return raw if raw in {READONLY, COLLECTOR} else READONLY


def is_readonly_mode() -> bool:
    return runtime_mode() == READONLY


def require_collector_mode(operation: str) -> None:
    if is_readonly_mode():
        raise RuntimeError(
            f"{operation} requires collector mode. Set "
            "KREPORTS_RUNTIME_MODE=collector on the maintainer machine."
        )


def readonly_cache_miss(dataset: str, company: str | None = None, year: Any = None) -> str:
    parts = [f"{dataset} is not available in the pre-built DB"]
    if company:
        parts.append(f"company={company}")
    if year is not None:
        parts.append(f"year={year}")
    parts.append("refresh the maintainer dataset and redeploy the DB artifact")
    return "; ".join(parts)
