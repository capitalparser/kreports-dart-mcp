#!/bin/zsh
set -u

cd /Users/kjun/vault/01_Projects/kreports_dart_mcp || exit 1
mkdir -p logs

LOG_FILE="logs/business-report-cache-backfill.log"
echo "===== cache business report backfill restarted $(date) =====" >> "$LOG_FILE"

for y in 2021 2022 2023 2024 2025; do
  echo "===== collect-business-report-sections year=$y started $(date) =====" >> "$LOG_FILE"
  KREPORTS_RUNTIME_MODE=collector .venv/bin/kreports collect-business-report-sections --year "$y" >> "$LOG_FILE" 2>&1
  echo "===== collect-business-report-sections year=$y finished $(date) =====" >> "$LOG_FILE"
done
