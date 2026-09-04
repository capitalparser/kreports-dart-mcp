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

For the Drive-first source archive, configure a separate OAuth client and
conservative quota guard in the private collector environment. The client ID
value is never printed or sent as an archive metadata field.

```env
RAW_STORAGE_BACKEND=drive
RAW_STORAGE_DRIVE_REMOTE=<named-drive-remote>:
RAW_STORAGE_PREFIX=KReports Data Lake/source-archive
RAW_STORAGE_SPOOL_DIR=~/.cache/kreports/source-archive-spool
# Existing operator-owned Drive API/rclone pipeline; keep the file chmod 600.
RAW_STORAGE_RCLONE_CONFIG=~/.config/rclone/rclone.conf
RAW_STORAGE_RCLONE_TPSLIMIT=0.5
RAW_STORAGE_RCLONE_TPSLIMIT_BURST=1
RAW_STORAGE_DRIVE_RATE_LIMIT_RETRIES=2
RAW_STORAGE_DRIVE_RATE_LIMIT_COOLDOWN_SECONDS=60
RAW_STORAGE_DRIVE_RATE_LIMIT_MAX_COOLDOWN_SECONDS=900
KREPORTS_ENABLE_RAW_BACKFILL=1
```

The named rclone Drive remote must contain a non-empty `client_id` (configure
it with `rclone config`, then verify with `rclone config show <remote-name>`),
or the process may use an override that rclone itself consumes:
`RCLONE_CONFIG_<REMOTE_NAME>_CLIENT_ID` (upper-case, non-alphanumeric
characters replaced with `_`) or `RCLONE_DRIVE_CLIENT_ID`. An application-only
marker such as `RAW_STORAGE_DRIVE_CLIENT_ID` is ignored and cannot satisfy the
source-archive apply gate.

Every rclone command is sent through one quota-aware gateway and carries
`--tpslimit 0.5 --tpslimit-burst 1` by default. HTTP 429 and Drive 403 rate
markers (`rateLimitExceeded`, `userRateLimitExceeded`, or
`rate_limit_exceeded`) use a bounded truncated-exponential retry whose first
cooldown is at least 60 seconds. A permission 403 is a hard error; it is never
treated as a missing object. Only an explicit 404/missing response can select
the immutable upload path.

Only one source collector may write a named Drive remote at a time. The
collector takes a process lease in the spool control directory before the first
DART request. Company-year campaign events are durable local outbox bundles and
are deleted only after verified Drive archive; a quota stop leaves the bundle,
spool, and checkpoints for the next run.

The quota cooldown not-before timestamp is process-local and is not restored
after a collector restart. After a quota stop, wait for the external Drive
quota window before resuming; local outbox/checkpoint recovery remains durable.

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
but that legacy hot-raw path remains a manual operation for selected years or
companies. The frozen five-year source-archive campaign uses its own bounded
supervisor and launchd job described below; do not point the generic dataset
job at it.

Raw collection fails closed unless all of these are set:

```bash
export KREPORTS_ENABLE_RAW_BACKFILL=1
export RAW_STORAGE_BACKEND=gcs   # or file
export RAW_STORAGE_KEEP_INLINE=false
```

Do not run raw collection with `RAW_STORAGE_BACKEND=inline` or
`RAW_STORAGE_KEEP_INLINE=true`; that stores DART XML/HTML in SQLite and can grow
`kreports.db` by tens of GB.

## Local DB capacity lifecycle (Google Drive archive)

Raw-source retention and local SQLite capacity are separate workflows. The
maintainer workstation can archive **inactive candidate or historic release
DBs** to verified Google Drive objects, then prune the local copy only after a
grace period and only when the free-space threshold requires it. The current
runtime DB and every active campaign/checkpoint are protected; public MCP never
reads the Drive object.

See [Maintainer DB archive lifecycle](database-archive-lifecycle.md) for the
read-only plan, explicit apply/prune command, and daily macOS `launchd` job.

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
before every physical DART HTTP attempt, including retry attempts, separate
audit-filing discovery, and `document.xml` retrieval. It stops with a resumable
`dart_budget_exhausted` outcome when it reaches zero. Each business report
retains the original DART ZIP response/container first; indexed XML members
carry its SHA-256, Drive URI, and member-name lineage. Audit reports likewise
require an explicit audit-report XML member, either embedded in that ZIP or
from a separately filed audit-report receipt. The runner handles one source
asset at a time: the original ZIP/direct XML response and its XML member bytes
are hashed and immutably archived *before* any parser decoding; the generic
parse package is then archived. High-volume source collection may accept a
successful content-addressed `rclone copyto --ignore-existing` without an
immediate remote readback (`RAW_STORAGE_VERIFY_READBACK_ON_SUCCESS=0`). A
failed or timed-out copy retains its local spool and stops for resumable retry;
an explicit `source-archive-verify`/audit can perform the stricter readback.
There is no replacement-decoding path for retained raw bytes.

If DART returns a direct XML document instead of a ZIP, it is retained as an
XML raw-response container with distinct media metadata and archive version;
it is never mislabeled as a ZIP.

Both report families are required: business-report XML assets and a selected
audit-report XML package. A business-only result, unavailable audit XML,
unreadable audit source, or parser-review requirement is `partial_source`,
never a completed company-year. Viewer/PDF evidence may be retained for
diagnosis but never completes the audit-report family.
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

### All-issuer v3 source archive boundary

The existing listed workflow remains the v2 historical KOSPI/KOSDAQ universe.
To preserve raw sources for every canonical annual-report issuer, operators
must use `--universe all-annual-issuers` with a new v3 Drive prefix and a new
local state directory. Never reuse, replace, or resume a v2 target manifest as
a v3 checkpoint. Review the v3 preflight's cohort counts and target digest
before a dry run. Review one finite `--apply --max-dart-calls` shard first;
after that gate passes, `source-archive-auto-run` may traverse all frozen shards
while retaining the same finite budget per physical batch.

The added cohort is
`annual_report_issuer_outside_verified_markets`, and its default historic status
is `unclassified`. Missing KOSPI/KOSDAQ evidence is not proof of unlisted, so
raw archive inclusion is not a historic-listing conclusion. A dated official
KRX KOSPI/KOSDAQ/KONEX raw export and normalization manifest are required before
`not_krx_listed_verified`; `unlisted_confirmed` additionally requires a dated
issuer-status source. These later labels do not alter the frozen source target
or demonstrate full archive coverage.

The detailed commands, fresh-root requirement, and evidence limits are in
[Drive-first annual source archive backfill](source-archive-backfill.md).

The dedicated continuous runner treats `api_budget_exhausted` as a local batch
boundary and starts the next bounded batch after 30 seconds. It treats a real
DART quota response separately, probing again after 15 minutes without assuming
a daily reset time. Recent terminal `partial_source` targets wait 24 hours while
untouched company-years are processed first. A `partial_source` outcome created
before the current audit-XML resolver version is retried once immediately, then
returns to the normal 24-hour cadence:

Drive copy/readback timeouts and transient rclone command failures are recorded
as `drive_transport_failure`, preserving the local checkpoint and event outbox;
the same batch resumes after 60 seconds. This is separate from a Drive quota
stop, which observes its longer cooldown.

```bash
scripts/source_archive_auto_backfill.sh
scripts/install_launchd_source_archive.sh
```

This is separate from `com.kjun.kreports-dart-backfill`; install only the
dedicated source-archive job for this campaign. Its PID lock and Drive writer
lease prevent concurrent writers.

Uninstall:

```bash
scripts/uninstall_launchd_backfill.sh
```
