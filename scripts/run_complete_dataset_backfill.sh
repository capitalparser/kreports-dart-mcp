#!/usr/bin/env bash
set -euo pipefail

mkdir -p logs
LOG_FILE="logs/complete-dataset-backfill.log"
source scripts/backfill_preflight.sh
source scripts/raw_backfill_guard.sh

log() {
  echo "===== $* $(date) =====" >> "$LOG_FILE"
}

export KREPORTS_RUNTIME_MODE="${KREPORTS_RUNTIME_MODE:-collector}"

require_backfill_free_space "complete dataset backfill"
log "complete dataset backfill started"

# Migration map for operators comparing the former shell-owned workflow with
# `kreports orchestrate-complete-backfill`. These labels are documentation
# only; Python now owns ordering, skip/resume facts, counters, and outcomes.
: <<'PYTHON_ORCHESTRATION_MIGRATION_MAP'
"2023 KOSDAQ"
${KREPORTS_ENABLE_RAW_BACKFILL:-0}
run_api_step "business report sections ${year} ${market}"
require_external_raw_backfill
raw report section backfill skipped
initial disclosure list skipped
api_exit=0
run_api_step() { :; }
run_api_step "financial facts 2021-2025"
run_step "rebuild compact financial facts 2021-2025"
for year in 2021 2022 2023 2024 2025; do
  run-document-extractors --year "$year" --source-type business_report
  run-document-extractors --year "$year" --source-type audit_report
done
disclosure list 2021-2026 --start-date 20210101
run_step "rebuild normalized evidence documents 2021-2025"
run_step "dataset audit"
log "complete dataset backfill finished with API failure exit_code=$api_exit"
if (( api_exit != 0 )); then :; fi
only for explicit hot-raw archive operations
PYTHON_ORCHESTRATION_MIGRATION_MAP

if .venv/bin/kreports orchestrate-complete-backfill "$@" >> "$LOG_FILE" 2>&1; then
  log "complete dataset backfill finished"
else
  code=$?
  log "complete dataset backfill failed exit_code=$code"
  exit "$code"
fi
