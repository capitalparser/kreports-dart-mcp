#!/usr/bin/env bash
set -euo pipefail

mkdir -p logs
LOG_FILE="logs/2023-expansion-backfill.log"

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

log "2023 expansion backfill started"

# 2023 expansion policy:
# - Continue from the compact 2024-2025 dataset.
# - Fill 2023 structured data first; raw report fetches are only for evidence extraction.
# - Stop immediately when a step detects DART quota exhaustion.

run_step "financials 2023 KOSDAQ resume" \
  .venv/bin/kreports collect-all --year-from 2023 --year-to 2023 --market KOSDAQ --force

run_step "auditors all years refresh" \
  .venv/bin/kreports collect-auditors --force

run_step "audit fees 2023 KOSPI" \
  .venv/bin/kreports collect-audit-fees --year-from 2023 --year-to 2023 --market KOSPI --force

run_step "audit fees 2023 KOSDAQ" \
  .venv/bin/kreports collect-audit-fees --year-from 2023 --year-to 2023 --market KOSDAQ --force

run_step "business report sections 2023 KOSPI" \
  .venv/bin/kreports collect-business-report-sections --year 2023 --market KOSPI --force

run_step "business report sections 2023 KOSDAQ" \
  .venv/bin/kreports collect-business-report-sections --year 2023 --market KOSDAQ --force

run_step "extract 2023 business report derived data" \
  .venv/bin/kreports run-document-extractors --year 2023 --source-type business_report

run_step "extract 2023 audit report derived data" \
  .venv/bin/kreports run-document-extractors --year 2023 --source-type audit_report

run_step "rebuild 2023 evidence documents" \
  .venv/bin/kreports rebuild-evidence-documents --year-from 2023 --year-to 2023 --max-text-chars 12000

run_step "clear 2023 derived raw content dry run" \
  .venv/bin/kreports clear-cold-derived-raw-content --year-to 2023 --limit 1000

run_step "auditor readiness 3-year" \
  .venv/bin/kreports dataset-auditor-readiness --year 2025 --years-back 3

log "2023 expansion backfill finished"
