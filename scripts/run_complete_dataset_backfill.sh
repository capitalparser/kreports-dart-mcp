#!/usr/bin/env bash
set -euo pipefail

mkdir -p logs
LOG_FILE="logs/complete-dataset-backfill.log"

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

api_exit=0

run_api_step() {
  local name="$1"
  shift
  if (( api_exit != 0 )); then
    log "$name skipped after API failure exit_code=$api_exit"
    return 0
  fi
  if run_step "$name" "$@"; then
    return 0
  else
    local code=$?
    api_exit=$code
    return 0
  fi
}

export KREPORTS_RUNTIME_MODE="${KREPORTS_RUNTIME_MODE:-collector}"

log "complete dataset backfill started"

# 1. Disclosure list and event index. Event bodies remain on-demand with caller DART keys.
for market in KOSPI KOSDAQ; do
  run_api_step "disclosure list 2021-2026 ${market}" \
    .venv/bin/kreports collect-disclosures --market "$market" --start-date 20210101 --end-date 20261231
done

for year in 2021 2022 2023 2024 2025 2026; do
  for market in KOSPI KOSDAQ; do
    run_step "disclosure event index ${year} ${market}" \
      .venv/bin/kreports rebuild-disclosure-events --year "$year" --market "$market"
  done
done

# 2. Structured financials and compact runtime metrics.
# Even when DART quota stops collect-all, rebuild compact facts from rows already saved.
run_api_step "financial facts 2021-2025" \
  .venv/bin/kreports collect-all --year-from 2021 --year-to 2025

run_step "rebuild compact financial facts 2021-2025" \
  .venv/bin/kreports rebuild-financial-facts-compact --year-from 2021 --year-to 2025

# 3. Annual business reports and attached audit-report bodies.
for year in 2021 2022 2023 2024 2025; do
  for market in KOSPI KOSDAQ; do
    run_api_step "business report sections ${year} ${market}" \
      .venv/bin/kreports collect-business-report-sections --year "$year" --market "$market"

    run_api_step "audit report sections ${year} ${market}" \
      .venv/bin/kreports collect-audit-report-sections --year "$year" --market "$market"

    run_api_step "business-report attached audit reports ${year} ${market}" \
      .venv/bin/python scripts/backfill_business_report_audit_attachments.py --start-year "$year" --end-year "$year" --market "$market"

    run_api_step "audit-submission sections ${year} ${market}" \
      .venv/bin/python scripts/backfill_audit_submission_sections.py --start-year "$year" --end-year "$year" --market "$market"
  done
done

# 4. Derived tables from the collected documents. These steps do not require more DART API calls.
for year in 2021 2022 2023 2024 2025; do
  run_step "rerun business-report extractors ${year}" \
    .venv/bin/kreports run-document-extractors --year "$year" --source-type business_report

  run_step "rerun audit-report extractors ${year}" \
    .venv/bin/kreports run-document-extractors --year "$year" --source-type audit_report
done

run_step "rebuild audit matters" \
  .venv/bin/kreports rebuild-audit-matter-items

run_step "rebuild audit procedures" \
  .venv/bin/kreports index-audit-procedures

run_step "rebuild normalized evidence documents 2021-2025" \
  .venv/bin/kreports rebuild-evidence-documents --year-from 2021 --year-to 2025 --max-text-chars 12000

# 5. Auditor and audit fee structured data.
run_api_step "auditors all" \
  .venv/bin/kreports collect-auditors

for market in KOSPI KOSDAQ; do
  run_api_step "audit fees 2021-2025 ${market}" \
    .venv/bin/kreports collect-audit-fees --year-from 2021 --year-to 2025 --market "$market"
done

# 6. Final diagnostics.
run_step "raw annual report coverage" \
  .venv/bin/kreports raw-annual-report-coverage --start-filing-year 2022 --end-filing-year 2026

run_step "evidence document readiness" \
  .venv/bin/kreports evidence-document-readiness

run_step "investor dataset readiness" \
  .venv/bin/kreports investor-dataset-readiness --year 2025 --years-back 5

run_step "auditor feature readiness" \
  .venv/bin/kreports auditor-feature-readiness --year 2025

run_step "auditor dataset readiness" \
  .venv/bin/kreports dataset-auditor-readiness --year 2025 --years-back 5

run_step "dataset audit" \
  .venv/bin/kreports dataset-audit --top 20

if (( api_exit != 0 )); then
  log "complete dataset backfill finished with API failure exit_code=$api_exit"
  exit "$api_exit"
fi

log "complete dataset backfill finished"
