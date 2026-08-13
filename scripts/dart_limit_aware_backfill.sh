#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${KREPORTS_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ENV_FILE="${KREPORTS_COLLECTOR_ENV:-$HOME/.config/kreports/collector.env}"
LOCK_DIR="${KREPORTS_BACKFILL_LOCK_DIR:-$PROJECT_DIR/.dart-backfill.lock}"
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/dart-limit-aware-backfill.log"
# KREPORTS_BACKFILL_SCRIPT previously defaulted to
# scripts/run_complete_dataset_backfill.sh. Task 5 retires executable shell
# delegation in favor of the single Python orchestration command below.

mkdir -p "$LOG_DIR"
source "$PROJECT_DIR/scripts/backfill_preflight.sh"

log() {
  printf '===== %s %s =====\n' "$*" "$(date '+%Y-%m-%d %H:%M:%S %Z')" >> "$LOG_FILE"
}

load_env() {
  if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
  fi
  export KREPORTS_RUNTIME_MODE="${KREPORTS_RUNTIME_MODE:-collector}"
}

acquire_lock() {
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    trap 'rm -rf "$LOCK_DIR"' EXIT
    return 0
  fi

  local existing_pid=""
  if [[ -f "$LOCK_DIR/pid" ]]; then
    existing_pid="$(<"$LOCK_DIR/pid")"
  fi
  if [[ -n "$existing_pid" ]] && kill -0 "$existing_pid" 2>/dev/null; then
    log "skip: wrapper already running pid=$existing_pid"
    exit 0
  fi

  rm -rf "$LOCK_DIR"
  mkdir "$LOCK_DIR"
  trap 'rm -rf "$LOCK_DIR"' EXIT
}

main() {
  cd "$PROJECT_DIR"
  acquire_lock
  echo "$$" > "$LOCK_DIR/pid"
  load_env

  if [[ -z "${DART_API_KEY:-}" ]]; then
    log "skip: DART_API_KEY missing; configure $ENV_FILE"
    exit 64
  fi

  require_backfill_free_space "DART backfill wrapper"

  log "probe started"
  set +e
  .venv/bin/python scripts/probe_dart_api.py >> "$LOG_FILE" 2>&1
  code=$?
  set -e
  if [[ "$code" != "0" ]]; then
    if [[ "$code" == "75" ]]; then
      log "skip: DART API limit still unavailable"
      exit 0
    fi
    log "probe failed exit_code=$code"
    exit "$code"
  fi

  log "backfill orchestration started"
  if .venv/bin/kreports orchestrate-complete-backfill >> "$LOG_FILE" 2>&1; then
    log "backfill orchestration finished"
  else
    code=$?
    log "backfill orchestration failed exit_code=$code"
    exit "$code"
  fi
}

main "$@"
