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


def runtime_write_allowed(operation: str) -> bool:
    """Whether this runtime may mutate its local DB or object storage.

    ``operation`` is deliberately accepted for call-site readability and future
    auditing; the policy is mode-wide and fails closed for unknown modes.
    """
    del operation
    return runtime_mode() == COLLECTOR


def require_runtime_write(operation: str) -> None:
    """Reject a DB/object-store write outside a maintainer collector runtime."""
    if not runtime_write_allowed(operation):
        raise RuntimeError(
            f"{operation} requires collector mode. Set "
            "KREPORTS_RUNTIME_MODE=collector on the maintainer machine."
        )


def raw_backfill_enabled() -> bool:
    return os.environ.get("KREPORTS_ENABLE_RAW_BACKFILL", "").strip() == "1"


def raw_storage_policy() -> tuple[str, bool, str]:
    """Read raw storage policy at call time so deploy settings fail closed."""
    from kreports.config import settings

    backend = os.environ.get("RAW_STORAGE_BACKEND", settings.raw_storage_backend).strip().lower()
    keep_inline = os.environ.get("RAW_STORAGE_KEEP_INLINE")
    if keep_inline is None:
        keep_inline_value = bool(settings.raw_storage_keep_inline)
    else:
        keep_inline_value = keep_inline.strip().lower() in {"1", "true", "yes", "on"}
    bucket = os.environ.get("RAW_STORAGE_BUCKET", settings.raw_storage_bucket).strip()
    return backend, keep_inline_value, bucket


def raw_persistence_allowed(*, backend: str | None = None, bucket: str | None = None) -> bool:
    """Return true only for explicit external, non-inline raw retention."""
    configured_backend, keep_inline, configured_bucket = raw_storage_policy()
    effective_backend = (backend or configured_backend).strip().lower()
    effective_bucket = (bucket if bucket is not None else configured_bucket).strip()
    return (
        runtime_write_allowed("raw persistence")
        and raw_backfill_enabled()
        and effective_backend in {"file", "gcs"}
        and not keep_inline
        and (effective_backend != "gcs" or bool(effective_bucket))
    )


def require_raw_backfill_mode(
    operation: str,
    *,
    raw_storage_backend: str | None,
    raw_storage_keep_inline: bool,
) -> None:
    """Guard DART document body collection behind explicit external storage.

    Annual report body collection can expand source_documents by tens of GB.
    The default maintainer workflow is derived-data-first, so raw collection
    requires both an explicit operator opt-in and non-inline storage.
    """
    require_runtime_write(operation)
    if not raw_backfill_enabled():
        raise RuntimeError(
            f"{operation} is blocked by the raw retention policy. Set "
            "KREPORTS_ENABLE_RAW_BACKFILL=1 only for an explicit hot-raw "
            "archive operation."
        )
    backend = (raw_storage_backend or "").strip().lower()
    if backend not in {"file", "gcs"}:
        raise RuntimeError(
            f"{operation} must use external raw storage. Set "
            "RAW_STORAGE_BACKEND=file or gcs; inline/db storage is not allowed."
        )
    if raw_storage_keep_inline:
        raise RuntimeError(
            f"{operation} must not keep raw bodies inline. Set "
            "RAW_STORAGE_KEEP_INLINE=false."
        )
    if backend == "gcs":
        _, _, bucket = raw_storage_policy()
        if not bucket:
            raise RuntimeError(
                f"{operation} must set RAW_STORAGE_BUCKET for gcs raw storage."
            )


def require_gcs_raw_backfill_mode(operation: str) -> None:
    """Require the stricter GCS-only raw boundary for durable DART recovery."""
    backend, keep_inline, bucket = raw_storage_policy()
    if backend != "gcs":
        raise RuntimeError(
            f"{operation} must use GCS raw storage; set RAW_STORAGE_BACKEND=gcs."
        )
    if not bucket:
        raise RuntimeError(
            f"{operation} must set RAW_STORAGE_BUCKET for GCS raw storage."
        )
    require_raw_backfill_mode(
        operation,
        raw_storage_backend=backend,
        raw_storage_keep_inline=keep_inline,
    )


def readonly_cache_miss(dataset: str, company: str | None = None, year: Any = None) -> str:
    parts = [f"{dataset} is not available in the pre-built DB"]
    if company:
        parts.append(f"company={company}")
    if year is not None:
        parts.append(f"year={year}")
    parts.append("refresh the maintainer dataset and redeploy the DB artifact")
    return "; ".join(parts)
