#!/usr/bin/env bash
set -euo pipefail

backfill_free_kb() {
  local path="${1:-.}"
  if [[ -n "${KREPORTS_BACKFILL_FREE_KB_OVERRIDE:-}" ]]; then
    printf '%s\n' "$KREPORTS_BACKFILL_FREE_KB_OVERRIDE"
    return 0
  fi

  df -Pk "$path" | awk 'NR == 2 { print $4 }'
}

require_backfill_free_space() {
  local operation="${1:-backfill}"
  local path="${KREPORTS_BACKFILL_SPACE_PATH:-.}"
  local min_kb="${KREPORTS_MIN_FREE_KB:-10485760}"
  local free_kb
  free_kb="$(backfill_free_kb "$path")"

  if [[ ! "$free_kb" =~ ^[0-9]+$ ]] || [[ ! "$min_kb" =~ ^[0-9]+$ ]]; then
    echo "ERROR: ${operation} could not determine free disk space at ${path}." >&2
    return 70
  fi

  if (( free_kb < min_kb )); then
    echo "ERROR: ${operation} requires at least ${min_kb} KB free; found ${free_kb} KB at ${path}." >&2
    return 70
  fi
}
