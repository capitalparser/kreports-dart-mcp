#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export DART_HEADLESS=1
export KREPORTS_HEADLESS=1

cd "$ROOT"
exec "${KREPORTS_PYTHON:-python}" -m kreports.mcp "$@"
