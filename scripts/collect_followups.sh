#!/usr/bin/env bash
# collect_followups.sh
#
# collect-all (PID via --wait-pid 또는 자동감지) 종료를 기다린 후
# audit-fees → auditors 순으로 전체 배치 수집을 돌린다.
# (collect-policies는 회사별 disclosure 의존이라 별도 수동 실행)
#
# Requires: DART_API_KEY env var.
# Usage:
#   DART_API_KEY=xxx nohup bash scripts/collect_followups.sh > logs/collect_followups.log 2>&1 &
#   DART_API_KEY=xxx nohup bash scripts/collect_followups.sh --wait-pid 32764 > logs/collect_followups.log 2>&1 &

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ENV_FILE="${KREPORTS_COLLECTOR_ENV:-$HOME/.config/kreports/collector.env}"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

if [[ -z "${DART_API_KEY:-}" ]]; then
  echo "[ERROR] DART_API_KEY env var required" >&2
  exit 1
fi

PYTHON_BIN="${KREPORTS_PYTHON:-/Users/kjun/.pyenv/versions/3.12.7/bin/python}"

WAIT_PID=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --wait-pid) WAIT_PID="$2"; shift 2 ;;
    *) echo "[ERROR] unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$WAIT_PID" ]]; then
  WAIT_PID="$(pgrep -f 'kreports.cli.main collect-all' | head -1 || true)"
fi

log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

if [[ -n "$WAIT_PID" ]] && kill -0 "$WAIT_PID" 2>/dev/null; then
  log "waiting for collect-all PID=$WAIT_PID to exit..."
  while kill -0 "$WAIT_PID" 2>/dev/null; do
    sleep 60
  done
  log "collect-all PID=$WAIT_PID exited."
else
  log "no active collect-all detected; proceeding immediately."
fi

run_step() {
  local label="$1"; shift
  log "=== START: $label ==="
  if "$PYTHON_BIN" -m kreports.cli.main "$@"; then
    log "=== OK: $label ==="
  else
    local rc=$?
    log "=== FAIL ($rc): $label === (continuing to next step)"
  fi
}

run_python_script() {
  local label="$1"; shift
  log "=== START: $label ==="
  if "$PYTHON_BIN" "$@"; then
    log "=== OK: $label ==="
  else
    local rc=$?
    log "=== FAIL ($rc): $label === (continuing to next step)"
  fi
}

run_step "collect-audit-fees (full)"  collect-audit-fees --year-from 2021 --year-to 2025
run_python_script "retry financial errors KOSPI" scripts/backfill_error_financials.py --market KOSPI --year-from 2021 --year-to 2025
run_python_script "retry financial errors KOSDAQ" scripts/backfill_error_financials.py --market KOSDAQ --year-from 2021 --year-to 2025
run_step "collect-auditors (full)"    collect-auditors

log "=== ALL DONE ==="
"$PYTHON_BIN" -m kreports.cli.main dataset-health || true
