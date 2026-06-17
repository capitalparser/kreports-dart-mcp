#!/usr/bin/env bash
set -euo pipefail

mkdir -p logs
LOG_FILE="logs/full-dataset-backfill.log"
source scripts/raw_backfill_guard.sh

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

log "full dataset backfill started"

# Canonical document body collection is not part of default full backfill.
# It can add tens of GB if run inline, so it requires explicit hot-raw opt-in
# plus external storage. Default runs continue from existing cached/externalized
# documents and derived tables only.
if raw_backfill_enabled; then
  require_external_raw_backfill "full dataset annual report source documents"
  for year in 2021 2022 2023 2024 2025; do
    run_step "business report source documents ${year} KOSPI" \
      .venv/bin/kreports collect-business-report-sections --year "$year" --market KOSPI

    run_step "business report source documents ${year} KOSDAQ" \
      .venv/bin/kreports collect-business-report-sections --year "$year" --market KOSDAQ
  done
else
  log "business report source documents skipped by raw retention policy"
fi

run_step "rerun document extractors from cached source documents" \
  .venv/bin/kreports run-document-extractors --source-type business_report

for year in 2021 2022 2023 2024 2025; do
  run_step "policies ${year} KOSPI" \
    .venv/bin/kreports collect-policies --market KOSPI --year "$year" --limit 10000

  run_step "policies ${year} KOSDAQ" \
    .venv/bin/kreports collect-policies --market KOSDAQ --year "$year" --limit 10000
done

run_step "financials 2021-2024" \
  .venv/bin/kreports collect-all --year-from 2021 --year-to 2024

run_step "auditors all" \
  .venv/bin/kreports collect-auditors

run_step "audit fees 2021-2025 KOSPI" \
  .venv/bin/kreports collect-audit-fees --year-from 2021 --year-to 2025 --market KOSPI

run_step "audit fees 2021-2025 KOSDAQ" \
  .venv/bin/kreports collect-audit-fees --year-from 2021 --year-to 2025 --market KOSDAQ

run_step "dataset audit" \
  .venv/bin/kreports dataset-audit --top 20

run_step "dataset completeness" \
  .venv/bin/kreports dataset-completeness --year 2025 --years-back 5 --sample-size 100

run_step "auditor readiness" \
  .venv/bin/kreports dataset-auditor-readiness --year 2025 --years-back 5

log "full dataset backfill finished"
