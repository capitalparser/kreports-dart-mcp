#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${KREPORTS_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ENV_FILE="${KREPORTS_COLLECTOR_ENV:-$HOME/.config/kreports/collector.env}"
LOCK_DIR="${KREPORTS_BACKFILL_LOCK_DIR:-$PROJECT_DIR/.dart-backfill.lock}"
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/dart-limit-aware-backfill.log"
BACKFILL_SCRIPT="${KREPORTS_BACKFILL_SCRIPT:-scripts/run_derived_dataset_backfill.sh}"

mkdir -p "$LOG_DIR"

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
    existing_pid="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
  fi
  if [[ -n "$existing_pid" ]] && kill -0 "$existing_pid" 2>/dev/null; then
    log "skip: wrapper already running pid=$existing_pid"
    exit 0
  fi

  rm -rf "$LOCK_DIR"
  mkdir "$LOCK_DIR"
  trap 'rm -rf "$LOCK_DIR"' EXIT
}

running_live_backfill_pids() {
  if [[ ! -f "$PROJECT_DIR/kreports.db" ]]; then
    return 0
  fi
  sqlite3 "$PROJECT_DIR/kreports.db" \
    "select pid from backfill_runs where status='running' and pid is not null;" \
    2>/dev/null || true
}

has_live_backfill() {
  local pid
  while IFS= read -r pid; do
    [[ -z "$pid" ]] && continue
    if kill -0 "$pid" 2>/dev/null; then
      log "skip: backfill already running pid=$pid"
      return 0
    fi
  done < <(running_live_backfill_pids)
  return 1
}

mark_stale_backfills() {
  if [[ ! -f "$PROJECT_DIR/kreports.db" ]]; then
    return 0
  fi

  local stale_ids=()
  local row id pid
  while IFS='|' read -r id pid; do
    [[ -z "$id" ]] && continue
    if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
      stale_ids+=("$id")
    fi
  done < <(sqlite3 "$PROJECT_DIR/kreports.db" \
    "select id, coalesce(pid,'') from backfill_runs where status='running';" \
    2>/dev/null || true)

  if (( ${#stale_ids[@]} == 0 )); then
    return 0
  fi

  local ids_csv
  ids_csv="$(IFS=,; echo "${stale_ids[*]}")"
  sqlite3 "$PROJECT_DIR/kreports.db" \
    "update backfill_runs set status='error', finished_at=datetime('now'), error_msg='stale running record closed by dart_limit_aware_backfill.sh' where id in ($ids_csv);"
  log "closed stale backfill_runs ids=$ids_csv"
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

  mark_stale_backfills
  if has_live_backfill; then
    exit 0
  fi

  log "probe started"
  set +e
  .venv/bin/python scripts/probe_dart_api.py >> "$LOG_FILE" 2>&1
  code=$?
  set -e
  if [[ "$code" != "0" ]]; then
    if [[ "$code" == "75" ]]; then
      log "skip: DART API limit still unavailable"
      exit 0
    else
      log "probe failed exit_code=$code"
      exit "$code"
    fi
  fi

  log "backfill started script=$BACKFILL_SCRIPT"
  if "$BACKFILL_SCRIPT" >> "$LOG_FILE" 2>&1; then
    log "backfill finished"
  else
    code=$?
    log "backfill failed exit_code=$code"
    exit "$code"
  fi
}

main "$@"
