#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${DART_API_KEY:-}" ]]; then
  echo "DART_API_KEY is required" >&2
  exit 1
fi

export KREPORTS_RUNTIME_MODE="${KREPORTS_RUNTIME_MODE:-collector}"

mkdir -p logs/disclosure-audit

run_disclosure_audit() {
  local kind="$1"
  local year="$2"
  local keyword="$3"
  shift 3
  local out="logs/disclosure-audit/${kind}-${year}.json"
  .venv/bin/kreports audit-disclosure-window \
    --start-date "${year}0101" \
    --end-date "${year}1231" \
    --report-keyword "$keyword" \
    "$@" \
    --json > "$out"
  .venv/bin/python - "$out" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text())
print(
    path.name,
    data["verdict"],
    "target", data["target_rows"],
    "local", data["local_rows"],
    "missing", data["missing_rows"],
    "errors", len(data["errors"]),
    "coverage", data["coverage_pct"],
)
PY
}

for year in 2022 2023 2024 2025 2026; do
  run_disclosure_audit business "$year" 사업보고서 \
    --exclude-keyword 제출기한연장 \
    --exclude-keyword 해외증권
  run_disclosure_audit audit "$year" 감사보고서
done

.venv/bin/kreports dataset-audit --top 10
.venv/bin/kreports dataset-completeness --year 2025 --years-back 5 --sample-size 100
.venv/bin/kreports dataset-auditor-readiness --year 2025 --years-back 5
KREPORTS_RUNTIME_MODE=readonly .venv/bin/python scripts/evaluate_current_mcp_quality.py
