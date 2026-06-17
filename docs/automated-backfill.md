# Automated DART-Limit-Aware Backfill

The collector should run on a private maintainer machine. The public MCP
endpoint remains read-only and must not have `DART_API_KEY`.

## Runtime Contract

- `scripts/dart_limit_aware_backfill.sh` is idempotent.
- It exits when another live backfill is already running.
- It closes stale `backfill_runs` records whose PID no longer exists.
- It probes DART once before starting the configured backfill script.
- The default script is `scripts/run_complete_dataset_backfill.sh`.
- If DART returns usage-limit status `020`, it logs and waits for the next
  scheduled run instead of failing loudly.

## Private Collector Env

Store collector secrets outside the repository:

```bash
mkdir -p ~/.config/kreports
chmod 700 ~/.config/kreports
$EDITOR ~/.config/kreports/collector.env
chmod 600 ~/.config/kreports/collector.env
```

Required content:

```env
DART_API_KEY=<opendart-key>
KREPORTS_RUNTIME_MODE=collector
```

If the collector should stop growing SQLite with full XML/HTML bodies, also set
raw storage explicitly:

```env
RAW_STORAGE_BACKEND=gcs
RAW_STORAGE_BUCKET=<gcs-bucket-name>
RAW_STORAGE_PREFIX=dart/raw
RAW_STORAGE_KEEP_INLINE=false
```

For a local gzip archive instead of GCS:

```env
RAW_STORAGE_BACKEND=file
RAW_STORAGE_PREFIX=dart/raw
RAW_STORAGE_KEEP_INLINE=false
```

Before a scheduled collector run, verify what will happen to newly collected raw
documents:

```bash
uv run kreports raw-storage-config
uv run kreports raw-storage-smoke --backend file --prefix smoke-test
uv run kreports raw-storage-smoke --backend gcs --bucket <gcs-bucket-name> --prefix smoke-test
```

`raw-storage-config` is the guardrail: if it says
`inline_raw_will_grow_db`, new source documents will still be stored inside
SQLite.

## Install macOS launchd Job

```bash
scripts/install_launchd_backfill.sh
```

The job runs at 00:20, 06:20, 12:20, and 18:20 local time, plus once at load.
This intentionally does not assume the exact DART reset time. The probe is
cheap, and the wrapper will skip while the API limit is still exhausted.

Status:

```bash
launchctl print gui/$(id -u)/com.kjun.kreports-dart-backfill
tail -f logs/dart-limit-aware-backfill.log
tail -f logs/derived-dataset-backfill.log
```

## Backfill Policy

Default unattended runs target the complete runtime dataset:

- collect listed-company disclosure lists;
- rebuild disclosure event indexes;
- collect compact structured data such as financials, auditors, audit fees, and
  audit hours;
- rerun document extractors only from already cached or externalized source
  documents;
- rebuild `evidence_documents`, KAM matters, and audit procedure indexes.

Raw source collection remains available through
`scripts/run_source_documents_backfill.sh` or by setting
`KREPORTS_ENABLE_RAW_BACKFILL=1` for `scripts/run_complete_dataset_backfill.sh`,
but it should be treated as a manual hot-raw archive operation for selected years
or companies. Do not use it as the default launchd target while local/Drive
capacity is constrained.

Raw collection fails closed unless all of these are set:

```bash
export KREPORTS_ENABLE_RAW_BACKFILL=1
export RAW_STORAGE_BACKEND=gcs   # or file
export RAW_STORAGE_KEEP_INLINE=false
```

Do not run raw collection with `RAW_STORAGE_BACKEND=inline` or
`RAW_STORAGE_KEEP_INLINE=true`; that stores DART XML/HTML in SQLite and can grow
`kreports.db` by tens of GB.

Uninstall:

```bash
scripts/uninstall_launchd_backfill.sh
```
