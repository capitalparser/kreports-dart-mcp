#!/usr/bin/env bash
set -euo pipefail

# Publish a secret-free, immutable worker bundle to the shared Drive pipeline
# area.  The input must be a committed source revision: collaborators must not
# be handed an untracked/dirty checkout that cannot later be reproduced.

PROJECT_DIR="${KREPORTS_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ALLOW_DIRTY_SNAPSHOT=0
if [[ "${1:-}" == "--allow-dirty-snapshot" ]]; then
  ALLOW_DIRTY_SNAPSHOT=1
  shift
fi
REVISION="${1:-HEAD}"
PIPELINE_REMOTE="${KREPORTS_SOURCE_ARCHIVE_PIPELINE_REMOTE:-${RAW_STORAGE_DRIVE_REMOTE:-}}"
PIPELINE_PREFIX="${KREPORTS_SOURCE_ARCHIVE_PIPELINE_PREFIX:-KReports Data Lake/source-archive-v3-all-annual-issuers/00_PIPELINE}"
RCLONE_CONFIG_PATH="${RAW_STORAGE_RCLONE_CONFIG:-}"

if [[ -z "$PIPELINE_REMOTE" ]]; then
  echo "set KREPORTS_SOURCE_ARCHIVE_PIPELINE_REMOTE or RAW_STORAGE_DRIVE_REMOTE" >&2
  exit 64
fi
if [[ -n "$RCLONE_CONFIG_PATH" && ! -f "$RCLONE_CONFIG_PATH" ]]; then
  echo "RAW_STORAGE_RCLONE_CONFIG must name an existing rclone config" >&2
  exit 64
fi

cd "$PROJECT_DIR"
DIRTY=0
if ! git diff --quiet || ! git diff --cached --quiet || [[ -n "$(git ls-files --others --exclude-standard)" ]]; then
  DIRTY=1
fi
if [[ "$DIRTY" == 1 && "$ALLOW_DIRTY_SNAPSHOT" != 1 ]]; then
  echo "refusing to publish a dirty worker bundle; commit the reviewed pipeline first" >&2
  exit 65
fi

COMMIT="$(git rev-parse --verify "${REVISION}^{commit}")"
SHORT_COMMIT="${COMMIT:0:12}"
STAGE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/kreports-source-archive-worker.XXXXXX")"
trap 'rm -rf "$STAGE_DIR"' EXIT

if [[ "$DIRTY" == 1 ]]; then
  BUNDLE_KIND="snapshot-$(date -u '+%Y%m%dT%H%M%SZ')"
else
  BUNDLE_KIND="release"
fi
BUNDLE_NAME="kreports-source-archive-worker-${SHORT_COMMIT}-${BUNDLE_KIND}.tar.gz"
BUNDLE_PATH="$STAGE_DIR/$BUNDLE_NAME"
CHECKSUM_PATH="$STAGE_DIR/$BUNDLE_NAME.sha256"

if [[ "$DIRTY" == 1 ]]; then
  # Git-tracked and non-ignored worktree files include the exact current source
  # snapshot while excluding private .env credentials through .gitignore.
  git ls-files --cached --others --exclude-standard -z \
    | tar --null --files-from=- --create --file - \
        --exclude='*.db' --exclude='*.pem' --exclude='*.key' --exclude='*.token' \
        --exclude='__pycache__' --exclude='*.pyc' \
    | gzip -n > "$BUNDLE_PATH"
else
  git archive --format=tar --prefix="kreports-source-archive-worker-${SHORT_COMMIT}/" "$COMMIT" \
    | gzip -n > "$BUNDLE_PATH"
fi
(cd "$STAGE_DIR" && shasum -a 256 "$BUNDLE_NAME" > "$(basename "$CHECKSUM_PATH")")

RCLONE_ARGS=(rclone)
if [[ -n "$RCLONE_CONFIG_PATH" ]]; then
  RCLONE_ARGS+=(--config "$RCLONE_CONFIG_PATH")
fi
TARGET_ROOT="${PIPELINE_REMOTE%:}:${PIPELINE_PREFIX%/}/worker-bundles"
CHECKSUM_ROOT="${PIPELINE_REMOTE%:}:${PIPELINE_PREFIX%/}/checksums"

"${RCLONE_ARGS[@]}" copyto "$BUNDLE_PATH" "$TARGET_ROOT/$BUNDLE_NAME" --immutable
"${RCLONE_ARGS[@]}" copyto "$CHECKSUM_PATH" "$CHECKSUM_ROOT/$(basename "$CHECKSUM_PATH")" --immutable

printf 'published worker bundle commit=%s sha256=%s\n' \
  "$COMMIT" "$(cut -d ' ' -f 1 "$CHECKSUM_PATH")"
