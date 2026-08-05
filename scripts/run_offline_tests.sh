#!/usr/bin/env bash
# Reproducible release-evidence lane: no DART key, no live DB, Python socket API network block.
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
guard_dir="${project_root}/scripts/offline_test_guard"
python_bin="${project_root}/.venv/bin/python"

if [[ ! -x "${python_bin}" ]]; then
  python_bin="${PYTHON_BIN:-python3}"
fi

export DART_API_KEY=""
export KREPORTS_LIVE_DB=""
export KREPORTS_RUN_LIVE_DB_TESTS="0"
export KREPORTS_OFFLINE_NETWORK_BLOCK="1"
export PYTHONPATH="${guard_dir}${PYTHONPATH:+:${PYTHONPATH}}"

tmp_base="${TMPDIR:-/tmp}"
tmp_base="${tmp_base%/}"
offline_dir="$(mktemp -d "${tmp_base}/kreports-offline.XXXXXX")"
offline_db="${offline_dir}/kreports.db"
cleanup() {
  rm -rf "${offline_dir}"
}
trap cleanup EXIT

export KREPORTS_OFFLINE_DB_PATH="${offline_db}"
export DB_URL="sqlite:///${offline_db}"
export KREPORTS_RUNTIME_MODE="collector"

cd "${project_root}"
"${python_bin}" -c "from kreports.db.engine import init_db; init_db()"

export KREPORTS_RUNTIME_MODE="readonly"
if "${python_bin}" -m pytest -q "$@" \
  -m "not live and not live_data and not apfs_real"; then
  exit 0
else
  exit $?
fi
