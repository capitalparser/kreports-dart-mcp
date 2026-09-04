#!/usr/bin/env bash
set -euo pipefail

# Copy legacy source-archive objects referenced by local outcomes into the
# human-browsable annual layout.  It never deletes the old hash-layout object
# or rewrites a checkpoint; URI cutover is a later, explicit operation.

if [[ "${1:-}" != "--apply" ]]; then
  echo "usage: $0 --apply <campaign-state-dir>" >&2
  exit 64
fi
CAMPAIGN_DIR="${2:-}"
if [[ -z "$CAMPAIGN_DIR" || ! -d "$CAMPAIGN_DIR" ]]; then
  echo "campaign state directory is required" >&2
  exit 64
fi
: "${RAW_STORAGE_DRIVE_REMOTE:?set RAW_STORAGE_DRIVE_REMOTE}"
: "${RAW_STORAGE_PREFIX:?set RAW_STORAGE_PREFIX}"
RCLONE_CONFIG_PATH="${RAW_STORAGE_RCLONE_CONFIG:-}"
if [[ -n "$RCLONE_CONFIG_PATH" && ! -f "$RCLONE_CONFIG_PATH" ]]; then
  echo "RAW_STORAGE_RCLONE_CONFIG must name an existing rclone config" >&2
  exit 64
fi

MIGRATION_DIR="$CAMPAIGN_DIR/layout-migration"
mkdir -p "$MIGRATION_DIR"
MAPPING_FILE="$MIGRATION_DIR/year-layout.tsv"
touch "$MAPPING_FILE"

RCLONE=(rclone)
if [[ -n "$RCLONE_CONFIG_PATH" ]]; then
  RCLONE+=(--config "$RCLONE_CONFIG_PATH")
fi

while IFS=$'\t' read -r year corp_code receipt report_kind role source_uri; do
  [[ -n "$source_uri" ]] || continue
  filename="${source_uri##*/}"
  source_path="${source_uri#*:}"
  source_for_copy="${RAW_STORAGE_DRIVE_REMOTE%:}:${source_path}"
  destination="${RAW_STORAGE_DRIVE_REMOTE%:}:${RAW_STORAGE_PREFIX%/}/${year}/${corp_code}/${receipt}/${report_kind}/${role}/${filename}"
  if grep -Fqx "${source_uri}"$'\t'"${destination}" "$MAPPING_FILE"; then
    continue
  fi
  "${RCLONE[@]}" copyto "$source_for_copy" "$destination" --ignore-existing
  printf '%s\t%s\n' "$source_uri" "$destination" >> "$MAPPING_FILE"
done < <(
  jq -r '
    select(.report_kind == "business_report" or .report_kind == "audit_report")
    | select((.bsns_year | type) == "number" and (.corp_code | type) == "string")
    | . as $row
    | [
        ["raw", ($row.raw_object.storage_uri // empty)],
        ["container", ($row.raw_container.storage_uri // empty)],
        ["parsed", ($row.parsed_object.storage_uri // empty)],
        ["manifest", ($row.document_manifest.storage_uri // empty)]
      ]
    | .[]
    | select(.[1] != "")
    | [$row.bsns_year, $row.corp_code, ($row.source_receipt // ""), $row.report_kind, .[0], .[1]]
    | @tsv
  ' "$CAMPAIGN_DIR"/shard-*/outcomes.jsonl 2>/dev/null | sort -u
)

printf 'legacy objects copied; mapping=%s\n' "$MAPPING_FILE"
