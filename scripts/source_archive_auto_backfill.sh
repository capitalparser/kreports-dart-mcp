#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${KREPORTS_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
COLLECTOR_ENV="${KREPORTS_SOURCE_ARCHIVE_COLLECTOR_ENV:-$PROJECT_DIR/.env.collector}"
DRIVE_ENV="${KREPORTS_SOURCE_ARCHIVE_DRIVE_ENV:-$PROJECT_DIR/.env.drive}"

load_private_env() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    echo "missing private environment file: $path" >&2
    exit 64
  fi
  set -a
  # shellcheck disable=SC1090
  source "$path"
  set +a
}

load_private_env "$COLLECTOR_ENV"
load_private_env "$DRIVE_ENV"

: "${KREPORTS_SOURCE_ARCHIVE_DB:?set KREPORTS_SOURCE_ARCHIVE_DB}"
: "${KREPORTS_SOURCE_ARCHIVE_STATE_DIR:?set KREPORTS_SOURCE_ARCHIVE_STATE_DIR}"
: "${DART_API_KEY:?set DART_API_KEY in the collector env}"

AUTH_BLOCK="${KREPORTS_SOURCE_ARCHIVE_AUTH_BLOCK:-$KREPORTS_SOURCE_ARCHIVE_STATE_DIR/AUTH_BLOCKED}"
LOCK_DIR="${KREPORTS_SOURCE_ARCHIVE_LOCK_DIR:-$KREPORTS_SOURCE_ARCHIVE_STATE_DIR/.supervisor.lock}"
MAX_DART_CALLS="${KREPORTS_SOURCE_ARCHIVE_MAX_DART_CALLS:-100}"
PARTIAL_RETRY_SECONDS="${KREPORTS_SOURCE_ARCHIVE_PARTIAL_RETRY_SECONDS:-86400}"
UNIVERSE="${KREPORTS_SOURCE_ARCHIVE_UNIVERSE:-all-annual-issuers}"
SHARD_COUNT="${KREPORTS_SOURCE_ARCHIVE_SHARD_COUNT:-64}"
YEARS="${KREPORTS_SOURCE_ARCHIVE_YEARS:-2021 2022 2023 2024 2025}"

if [[ -f "$AUTH_BLOCK" ]]; then
  echo "source archive is auth-blocked; fix credentials and remove $AUTH_BLOCK" >&2
  exit 0
fi

mkdir -p "$KREPORTS_SOURCE_ARCHIVE_STATE_DIR"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  if [[ -f "$LOCK_DIR/pid" ]] && kill -0 "$(<"$LOCK_DIR/pid")" 2>/dev/null; then
    echo "source archive supervisor already running" >&2
    exit 0
  fi
  rm -rf "$LOCK_DIR"
  mkdir "$LOCK_DIR"
fi
trap 'rm -rf "$LOCK_DIR"' EXIT
echo "$$" > "$LOCK_DIR/pid"

args=(
  source-archive-auto-run
  --db "$KREPORTS_SOURCE_ARCHIVE_DB"
  --state-dir "$KREPORTS_SOURCE_ARCHIVE_STATE_DIR"
  --universe "$UNIVERSE"
  --shard-count "$SHARD_COUNT"
  --max-dart-calls "$MAX_DART_CALLS"
  --partial-retry-after-seconds "$PARTIAL_RETRY_SECONDS"
)
for year in $YEARS; do
  args+=(--year "$year")
done

cd "$PROJECT_DIR"
caffeinate -i -w $$ &
set +e
.venv/bin/python -m kreports.cli.main "${args[@]}"
code=$?
set -e
if [[ "$code" == "78" ]]; then
  printf 'DART authentication failed at %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" > "$AUTH_BLOCK"
  chmod 600 "$AUTH_BLOCK"
  echo "DART authentication failed; created $AUTH_BLOCK" >&2
  exit 0
fi
exit "$code"
