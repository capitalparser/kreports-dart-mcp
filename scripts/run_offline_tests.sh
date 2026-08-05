#!/usr/bin/env bash
# Reproducible release-evidence lane: no DART key, no live DB, no external TCP.
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
guard_dir="${project_root}/scripts/offline_test_guard"
python_bin="${project_root}/.venv/bin/python"

if [[ ! -x "${python_bin}" ]]; then
  python_bin="${PYTHON_BIN:-python3}"
fi

export DART_API_KEY=""
export DB_URL="sqlite:///:memory:"
export KREPORTS_RUNTIME_MODE="readonly"
export KREPORTS_LIVE_DB=""
export KREPORTS_RUN_LIVE_DB_TESTS="0"
export KREPORTS_OFFLINE_NETWORK_BLOCK="1"
export PYTHONPATH="${guard_dir}${PYTHONPATH:+:${PYTHONPATH}}"

cd "${project_root}"
exec "${python_bin}" -m pytest -q "$@" \
  -m "not live and not live_data and not apfs_real"
