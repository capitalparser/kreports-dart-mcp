#!/usr/bin/env bash
set -euo pipefail

# Maintainer-only lifecycle runner.  It never touches the public MCP runtime
# and only asks the CLI to prune files already verified in the append-only ledger.
PROJECT_DIR="${KREPORTS_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ENV_FILE="${KREPORTS_COLLECTOR_ENV:-$HOME/.config/kreports/collector.env}"
STATE_DIR="${KREPORTS_DB_ARCHIVE_STATE_DIR:-$HOME/Library/Application Support/kreports/archive-ledger}"
CANDIDATE_ROOT="${KREPORTS_DB_ARCHIVE_CANDIDATE_ROOT:-$HOME/Library/Application Support/kreports/candidates}"
PROTECTED_DB="${KREPORTS_RUNTIME_DB_PATH:-$HOME/Library/Application Support/kreports/releases/kreports-compact-2021-2025-v2.db}"
LEDGER="${KREPORTS_DB_ARCHIVE_LEDGER:-$STATE_DIR/db-archive.jsonl}"
LOCK_DIR="${KREPORTS_DB_ARCHIVE_LOCK_DIR:-$STATE_DIR/run.lock}"
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/db-archive-lifecycle.log"

mkdir -p "$STATE_DIR" "$LOG_DIR"

log() {
  printf '===== %s %s =====\n' "$*" "$(date '+%Y-%m-%d %H:%M:%S %Z')" >> "$LOG_FILE"
}

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  log "skip: db archive lifecycle already running"
  exit 0
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

export KREPORTS_RUNTIME_MODE="${KREPORTS_RUNTIME_MODE:-collector}"
if [[ "${KREPORTS_ENABLE_DB_ARCHIVE:-0}" != "1" ]]; then
  log "skip: KREPORTS_ENABLE_DB_ARCHIVE=1 is not set"
  exit 0
fi

cd "$PROJECT_DIR"
log "db archive lifecycle started"
.venv/bin/kreports db-archive-run \
  --candidate-root "$CANDIDATE_ROOT" \
  --protect "$PROTECTED_DB" \
  --ledger "$LEDGER" \
  --grace-days "${KREPORTS_DB_ARCHIVE_GRACE_DAYS:-7}" \
  --min-free-gib "${KREPORTS_DB_ARCHIVE_MIN_FREE_GIB:-20}" \
  --target-free-gib "${KREPORTS_DB_ARCHIVE_TARGET_FREE_GIB:-25}" \
  --apply --prune >> "$LOG_FILE" 2>&1
log "db archive lifecycle finished"
