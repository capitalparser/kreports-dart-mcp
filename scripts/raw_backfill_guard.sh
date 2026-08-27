#!/usr/bin/env bash
set -euo pipefail

raw_backfill_enabled() {
  [[ "${KREPORTS_ENABLE_RAW_BACKFILL:-0}" == "1" ]]
}
require_external_raw_backfill() {
  local operation="${1:-raw backfill}"
  if ! raw_backfill_enabled; then
    echo "ERROR: ${operation} is blocked by raw retention policy." >&2
    echo "Set KREPORTS_ENABLE_RAW_BACKFILL=1 only for an explicit hot-raw archive operation." >&2
    return 2
  fi

  case "${RAW_STORAGE_BACKEND:-}" in
    file|gcs)
      ;;
    *)
      echo "ERROR: ${operation} requires RAW_STORAGE_BACKEND=file or gcs. inline/db raw storage is not allowed." >&2
      return 2
      ;;
  esac

  case "${RAW_STORAGE_KEEP_INLINE:-false}" in
    true|TRUE|1|yes|YES)
      echo "ERROR: ${operation} requires RAW_STORAGE_KEEP_INLINE=false." >&2
      return 2
      ;;
  esac
}
