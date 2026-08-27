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

## Five-year source archive campaign (Google Drive)

The source archive is a separate, local-collector workflow for preserving the
original DART annual-report assets and a generic document structure. It is not
part of the public MCP request path: the public server reads only a separately
prepared, read-only database artifact and does not have Google Drive access.

Use an explicit, read-only *candidate/collector* SQLite DB as the source of the
frozen company-year target list. Never point these commands at the active MCP
runtime DB. A company receives the same deterministic shard in every selected
year, so operators can resume a shard without changing its membership.

First perform a no-network, no-write preflight and create a target preview:

```bash
uv run kreports source-archive-preflight \
  --db /path/to/candidate.db \
  --year 2021 --year 2022 --year 2023 --year 2024 --year 2025

uv run kreports source-archive-plan \
  --db /path/to/candidate.db \
  --state-dir ~/.local/share/kreports/source-archive-2021-2025 \
  --year 2021 --year 2022 --year 2023 --year 2024 --year 2025
```

`TARGET.preview.json` is a local planning preview. On the first `--apply`, the
runner archives the complete target list to Drive *before any DART request*,
then writes the immutable Drive object URI and SHA-256 into `TARGET.json`.
`TARGET.json` is the frozen denominator. It records both canonical annual
filing targets and explicit `no_source_metadata` gaps, from verified
year-specific KOSPI/KOSDAQ membership evidence; a missing anchor is not
evidence that a filing has no disclosure. A current `companies` row alone is
not eligibility. Do not replace a manifest after a run has started. Create a
new campaign directory when source-selection rules or target years change.

Previewing a shard performs no DART request, Drive request, local source-state
write, or raw-file creation:

```bash
uv run kreports source-archive-run \
  --db /path/to/candidate.db \
  --state-dir ~/.local/share/kreports/source-archive-2021-2025 \
  --shard 7 \
  --year 2021 --year 2022 --year 2023 --year 2024 --year 2025
```

Only after preflight proves the local spool, DART credentials/quota, and Drive
configuration should an operator opt in to a real shard. The command fails
closed unless collector mode and raw retention are explicitly enabled:

```bash
export KREPORTS_RUNTIME_MODE=collector
export KREPORTS_ENABLE_RAW_BACKFILL=1
export RAW_STORAGE_BACKEND=drive
export RAW_STORAGE_DRIVE_REMOTE=vault:
export RAW_STORAGE_PREFIX='KReports Data Lake'
export RAW_STORAGE_SPOOL_DIR="$HOME/.cache/kreports/source-archive-spool"

uv run kreports source-archive-run \
  --db /path/to/candidate.db \
  --state-dir ~/.local/share/kreports/source-archive-2021-2025 \
  --shard 7 --apply --max-dart-calls 100 \
  --year 2021 --year 2022 --year 2023 --year 2024 --year 2025
```

`--max-dart-calls` is mandatory with `--apply`; the runner consumes one unit
before every physical DART HTTP attempt, including retry attempts, attachment
viewer requests, and PDF fallback. It stops with a resumable
`dart_budget_exhausted` outcome when it reaches zero. Each business report
retains the original DART ZIP response/container first; indexed XML members
carry its SHA-256, Drive URI, and member-name lineage. XML/HTML structure is
the primary parse path, while PDF is an original-byte audit fallback only. The runner handles one source asset at a time:
the original ZIP entry, viewer response, or PDF bytes are hashed, immutably
archived and read-back verified *before* any parser decoding; the generic parse
package is then archived. There is no replacement-decoding path for retained
raw bytes.

If DART returns a direct XML document instead of a ZIP, it is retained as an
XML raw-response container with distinct media metadata and archive version;
it is never mislabeled as a ZIP.

Both report families are required: the business-report assets and the selected
primary audit-report package (viewer with official-PDF fallback). A
business-only result, missing audit attachment, unreadable audit source, or
parser-review requirement is `partial_source`, never a completed company-year.
Each asset also creates an immutable Drive-side document manifest containing
company-year, report kind, receipt, source locator/filename, content type, raw
and parse object URIs/hashes, parser version, and status. Append-only
Drive-side campaign events plus these document manifests are the reconstruction
evidence; local `outcomes.jsonl` is a cache/checkpoint.

`COMMITTED.json` appears only when every company-year in the shard reaches
`structurally_complete`. It binds the frozen target digest, shard number, and
the current outcomes checksum; `source-archive-run` and
`source-archive-verify` fail closed if this marker or the local outcome cache is
tampered with. It is deliberately absent for partial results.

Inspect local campaign progress without contacting DART or Drive:

```bash
uv run kreports source-archive-verify \
  --state-dir ~/.local/share/kreports/source-archive-2021-2025 --shard 7
```

This workflow archives evidence; it does not modify the candidate DB, active
runtime DB, or Lightsail artifact, and it does not promote a release. A later,
separately verified candidate-artifact build consumes the archived parse
objects.

Uninstall:

```bash
scripts/uninstall_launchd_backfill.sh
```
