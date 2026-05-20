#!/usr/bin/env bash
set -euo pipefail

mkdir -p logs
LOG_FILE="logs/document-first-backfill.log"

log() {
  echo "===== $* $(date) =====" >> "$LOG_FILE"
}

run_step() {
  local name="$1"
  shift
  log "$name started"
  if "$@" >> "$LOG_FILE" 2>&1; then
    log "$name finished"
  else
    local code=$?
    log "$name failed exit_code=$code"
    return "$code"
  fi
}

export KREPORTS_RUNTIME_MODE="${KREPORTS_RUNTIME_MODE:-collector}"

log "document-first backfill started"

for year in 2021 2022 2023 2024 2025; do
  run_step "business report source documents ${year} KOSPI" \
    .venv/bin/kreports collect-business-report-sections --year "$year" --market KOSPI

  run_step "business report source documents ${year} KOSDAQ" \
    .venv/bin/kreports collect-business-report-sections --year "$year" --market KOSDAQ
done

run_step "rerun business-report document extractors" \
  .venv/bin/kreports run-document-extractors --source-type business_report

run_step "rerun audit-report document extractors" \
  .venv/bin/kreports run-document-extractors --source-type audit_report

run_step "dataset audit" \
  .venv/bin/kreports dataset-audit --top 20

run_step "auditor readiness" \
  .venv/bin/kreports dataset-auditor-readiness --year 2025 --years-back 5

log "document-first backfill finished"
