#!/usr/bin/env bash
set -euo pipefail

mkdir -p logs
LOG_FILE="logs/derived-dataset-backfill.log"

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

log "derived dataset backfill started"

# Derived-first policy:
# - Do not expand raw source_documents coverage by default.
# - Use already cached/externalized raw documents to refresh normalized tables.
# - Fill structured endpoint data that is compact and directly used by MCP tools.

for year in 2021 2022 2023 2024 2025; do
  run_step "rerun business-report extractors ${year} from cached/externalized raw documents" \
    .venv/bin/kreports run-document-extractors --year "$year" --source-type business_report

  run_step "rerun audit-report extractors ${year} from cached/externalized raw documents" \
    .venv/bin/kreports run-document-extractors --year "$year" --source-type audit_report
done

run_step "trim normalized evidence documents" \
  .venv/bin/kreports trim-evidence-documents --year-from 2024 --year-to 2025 --max-text-chars 12000

run_step "rebuild normalized evidence documents 2024-2025" \
  .venv/bin/kreports rebuild-evidence-documents --year-from 2024 --year-to 2025 --max-text-chars 12000

run_step "financials 2021-2025" \
  .venv/bin/kreports collect-all --year-from 2021 --year-to 2025

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

run_step "auditor feature readiness" \
  .venv/bin/kreports auditor-feature-readiness --year 2025

run_step "auditor readiness" \
  .venv/bin/kreports dataset-auditor-readiness --year 2025 --years-back 5

log "derived dataset backfill finished"
