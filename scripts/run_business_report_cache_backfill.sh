#!/usr/bin/env bash
set -euo pipefail

cd /Users/kjun/vault/01_Projects/kreports_dart_mcp || exit 1
mkdir -p logs
source scripts/raw_backfill_guard.sh
require_external_raw_backfill "business report cache backfill"

LOG_FILE="logs/business-report-cache-backfill.log"
echo "===== cache business report backfill restarted $(date) =====" >> "$LOG_FILE"

for y in 2021 2022 2023 2024 2025; do
  echo "===== collect-business-report-sections year=$y started $(date) =====" >> "$LOG_FILE"
  KREPORTS_RUNTIME_MODE=collector .venv/bin/kreports collect-business-report-sections --year "$y" >> "$LOG_FILE" 2>&1
  echo "===== collect-business-report-sections year=$y finished $(date) =====" >> "$LOG_FILE"
done
