# Deploy KReports Remote MCP

KReports runs as two role-separated processes and databases:

1. **Remote MCP endpoint**: read-only, no DART API key, serves `/mcp` from an
   immutable compact runtime DB.
2. **Collector/backfill worker**: private, has `DART_API_KEY`, and writes a
   separate writable maintainer DB.

The collector never writes the mounted public runtime DB. Publishing collector
changes requires a compact export from the maintainer DB, release-manifest build
and verification, atomic deployment of the DB and matching release JSON pair,
and an MCP service restart or reload. Until that promotion completes, the
running endpoint continues to serve its previously verified release.

The endpoint exposes **34 public tools**. Its database and the matching
`kreports.db.release.json` are an inseparable deployment pair: mount both
read-only at `/data/` from the same verified release. `/readyz` remains the
release health gate and cannot report ready without matching artifact proof.

For public pilot deployment, prefer a compact runtime DB artifact over the full
maintainer DB. The maintainer DB can retain full warehouse tables and extraction
logs, while the runtime artifact excludes heavy tables such as `financial_facts`,
`extraction_runs`, and `fetch_log`.

## Required Runtime Contract

Use the role-separated examples in [`deploy/`](../deploy/):

- [`public-mcp.env.example`](../deploy/public-mcp.env.example) contains only
  the public bearer token, read-only DB URL, and readonly runtime mode.
- [`private-collector.env.example`](../deploy/private-collector.env.example)
  is private; it contains collector-only DART and external raw-storage
  placeholders, with raw backfill disabled by default.

Public endpoint environment:

```env
KREPORTS_RUNTIME_MODE=readonly
DB_URL=sqlite:////data/kreports.db
KREPORTS_MCP_TOKEN=<long-random-token>
```

Private collector environment:

```env
KREPORTS_RUNTIME_MODE=collector
DART_API_KEY=<opendart-key>
DB_URL=sqlite:////path/to/writable/kreports.db
RAW_STORAGE_BACKEND=gcs
RAW_STORAGE_BUCKET=<gcs-bucket-name>
RAW_STORAGE_PREFIX=dart/raw
RAW_STORAGE_KEEP_INLINE=false
KREPORTS_ENABLE_RAW_BACKFILL=0
```

Do not put `DART_API_KEY` in the MCP endpoint environment.
Do not put `RAW_STORAGE_BACKEND=inline` on the collector unless the intent is to
grow the SQLite DB with full raw XML/HTML bodies.
Raw backfill is disabled by default. Only the exact value `1` opts in through
`KREPORTS_ENABLE_RAW_BACKFILL=1`; unset, `0`, and every other value remain off.

## User-Facing Response Contract

MCP tools should not expose raw JSON as the primary answer. Internal handlers may
produce structured dictionaries, but the MCP-facing response should be readable
Korean prose:

- verdict first,
- short evidence paragraphs,
- source references such as receipt number, filing year, section title,
- explicit data-quality and coverage notes.

Structured JSON can remain available for tests, API clients, and future UI
renderers, but public MCP users should receive narrative output that can be used
directly in audit, investment, or disclosure review workflows.

Every professional answer begins with `판정:` and its canonical availability is
one of `usable`, `limited`, `missing`, or `error`. This is distinct from a
domain verdict: for example, DCF input candidates can be available while a
valuation is blocked, and `산출 불가` is not a valuation result. Standard-audit-
hour input preparation likewise has a `not_assessed` boundary and must not be
presented as a calculated standard-hour conclusion.

Clients should display the concise chatbot `answer` first, then the complete
`answer_pack`, then its detailed visualization resource. Keep release readiness
separate from question usability: a release warning cannot downgrade or upgrade
the evidence status of a particular answer. Also distinguish cache absence from
filing absence; an uncached local result does not prove that DART has no filing.

## On-Demand Disclosure Fetch Contract

The hosted endpoint is cache-first for release data. If a public MCP user
requests an uncached ad-hoc disclosure, the endpoint may support on-demand DART
fetch only under this contract:

- the user supplies their own OpenDART API key for that request,
- the server-side collector `DART_API_KEY` is never used by the public endpoint,
- the user key is not persisted, logged, or echoed,
- the public user-keyed fetch is ephemeral and does not persist or cache the
  response or source document,
- responses disclose whether the answer came from cache or a user-keyed fetch.

Persisting a fetched document is collector work and requires an explicit
collector and external raw-storage policy; it is not an endpoint capability.

## Local build, configuration render, and smoke

These commands validate local configuration only. They do not prove release
readiness or live-data coverage; verify the DB artifact before starting the
service.

```bash
docker compose --env-file deploy/public-mcp.env.example -f docker-compose.deploy.yml build
docker compose --env-file deploy/public-mcp.env.example -f docker-compose.deploy.yml config
```

The example env file contains only a placeholder token. Inspect the config in
the terminal; do not render or persist Compose config after exporting a live
token.

After a verified DB and matching release JSON are mounted, start the service and
perform the authenticated HTTP smoke:

```bash
export KREPORTS_MCP_TOKEN="$(openssl rand -hex 32)"
docker compose -f docker-compose.deploy.yml up -d --force-recreate
curl -fsS http://127.0.0.1:8765/healthz
curl -fsS -H "Authorization: Bearer ${KREPORTS_MCP_TOKEN}" http://127.0.0.1:8765/readyz
```

Verify the artifact from the mounted pair before interpreting an HTTP result as
deployment readiness:

```bash
kreports verify-release-artifact --db ./kreports.db
```

Health:

```bash
curl http://127.0.0.1:8765/healthz
curl http://127.0.0.1:8765/readyz
```

`/readyz` is ready only when the public-runtime report has `ok: true` and
`required_failures` is empty. A `usable` tool response or HTTP transport success
does not override a release blocker.

MCP URL:

```text
http://127.0.0.1:8765/mcp
```

The Compose port is intentionally bound to host loopback only. For Claude Web
or other remote clients, terminate TLS in a reverse proxy on the same host and
connect to:

```text
https://<host>/mcp
```

The endpoint requires:

```http
Authorization: Bearer <KREPORTS_MCP_TOKEN>
```

For a short-lived tunnel test only:

```bash
kreports serve-http --host 127.0.0.1 --port 8765 --allow-unauthenticated
```

## Collector and immutable release promotion

Run collection only against a separate writable maintainer DB. This process must
never point `DB_URL` at `./kreports.db` or the mounted public runtime artifact:

```bash
export DART_API_KEY=<opendart-key>
export KREPORTS_RUNTIME_MODE=collector
export DB_URL=sqlite:////absolute/path/to/kreports-maintainer.db
scripts/run_derived_dataset_backfill.sh
```

For unattended local collection that resumes after DART daily-limit resets, use
the macOS launchd wrapper in [automated-backfill.md](automated-backfill.md).

Before collecting new hot raw documents, check the collector storage mode:

```bash
kreports raw-storage-config
kreports raw-storage-smoke --backend gcs --bucket <gcs-bucket-name> --prefix smoke-test
```

If `raw-storage-config` reports `inline_raw_will_grow_db`, newly collected raw
documents will still be written into SQLite rather than GCS/file storage.

Backfill is derived-data-first by default. The collector should answer MCP tools
from compact tables such as `financials`, `auditors`, `audit_fees`,
`report_sections`, `accounting_note_chapters`, `audit_procedure_items`, and
`evidence_documents`. Raw business/audit report bodies in `source_documents` are
a hot/cold archive for re-parsing and source verification, not the primary
runtime dataset. When a new extractor is added, rerun it from the local or
externalized source cache instead of downloading the same DART document again:

```bash
kreports run-document-extractors --source-type business_report
kreports run-document-extractors --year 2025 --source-type business_report --extractor auditors
```

This command does not require `DART_API_KEY`; it only reads cached or
externalized `source_documents` and writes derived rows.

Use `scripts/run_source_documents_backfill.sh` only for explicitly selected raw
archive expansion. On capacity-constrained machines, prefer rebuilding
`evidence_documents` and structured facts over collecting more full XML bodies.

Complete any maintainer-DB compaction and externalization before exporting a
public release:

```bash
kreports rebuild-financial-facts-compact --year-from 2021 --year-to 2025
kreports externalize-long-evidence-text \
  --table accounting_note_chapters \
  --min-text-chars 8000 \
  --excerpt-chars 2000 \
  --limit 100 \
  --backend gcs \
  --bucket <gcs-bucket-name> \
  --prefix evidence/full-text
```

Compact runtime artifact flow from that maintainer DB. The staging DB is created
under its final basename, `kreports.db`, so the adjacent
`kreports.db.release.json` remains filename-bound through promotion. The
fail-fast block stops before service shutdown if staged verification fails and
leaves the service stopped if final-path verification fails:

```bash
set -euo pipefail

release_stage="$(mktemp -d "${TMPDIR:-/tmp}/kreports-release.XXXXXX")"
trap 'rm -rf "$release_stage"' EXIT

kreports export-runtime-db \
  --output "$release_stage/kreports.db" \
  --year-from 2021 \
  --year-to 2025 \
  --profile compact
kreports build-release-manifest \
  --db "$release_stage/kreports.db"
kreports verify-release-artifact \
  --db "$release_stage/kreports.db"

docker compose -f docker-compose.deploy.yml stop kreports-mcp
install -m 0444 "$release_stage/kreports.db" ./kreports.db
install -m 0444 "$release_stage/kreports.db.release.json" ./kreports.db.release.json
kreports verify-release-artifact --db ./kreports.db
docker compose -f docker-compose.deploy.yml up -d --force-recreate
```

The service is stopped before the verified pair is copied under the same two
basenames, so it cannot observe a partial replacement. `set -e` gates the
restart on successful final-path verification: do not manually resume serving
if that verification fails. Retain the old pair separately until the replacement
has passed `/readyz`, so rollback is another stopped-service pair promotion
rather than an in-place DB mutation.

The build command is evidence-producing: it writes atomically and may record
`release_gate.passed=false` with named blockers. The verify command is
deployment-gating: it recomputes the DB size/hash, schema and required indexes,
dataset manifest, inline raw count, current release gate, 34-tool wire contract,
isolated real-dispatch catalog smoke, and the approved packaged golden contract
hash. The user-keyed network fetch is forced to `refresh` and checked in its
no-key fail-closed state, so an existing cache row cannot satisfy that check.
Verify exits non-zero on drift or any current blocker. Runtime `/readyz` reads
this pre-verified artifact and performs cheap WAL/file/static-contract drift
checks. It hashes the DB once per process and file identity, rehashes after
device/inode/size/mtime/ctime drift, and never repeats handler smoke in a
health probe.

Do not substitute code-test success for live-data readiness. The immutable
artifact manifest is the source for current market/year coverage and feature
grades. Investor functions and conditional auditor functions must be presented
separately according to that evidence.

Measured smoke result:

- maintainer DB: 2.1GB
- compact runtime DB: 729MB
- uploaded gzip artifact: 162.8MB
- manifest:
  `gs://kreports-raw-documents-gen-lang-client-0171998581/runtime-db/kreports-compact-2021-2025.manifest.json`

Current long-running backfills are tracked in:

```bash
kreports dataset-audit --top 20
```

Completeness gate:

```bash
kreports dataset-completeness --year 2025 --years-back 5 --sample-size 100
kreports dataset-auditor-readiness --year 2025 --years-back 5
```

## SQLite Notes

The current dataset is several GB. Do not bake `kreports.db` into the Docker
image. Mount it as a volume.

SQLite is acceptable for a first hosted endpoint if:

- the MCP container mounts the DB read-only,
- only one collector writes the separate maintainer DB at a time,
- `dataset-audit` shows no duplicate unique-key groups.

Move the maintainer workflow to Postgres when collector write concurrency
requires it; continue deploying immutable, verified runtime releases to the
public endpoint.

## Deployment evidence checklist

Keep these four evidence streams separate:

| Evidence stream | What it establishes | What it does not establish |
| --- | --- | --- |
| Code-test success | Local code and deployment-contract tests pass. | Artifact validity or live coverage. |
| HTTP liveness | The started service answers `/healthz`. | Release readiness or data completeness. |
| Release readiness | `verify-release-artifact` accepts the host DB and matching JSON, and `/readyz` is 200 after service recreation. | Current market/year coverage beyond the artifact report. |
| Live-data coverage | Dataset audit and completeness/readiness commands report the selected release's coverage. | That a code change or HTTP probe is correct. |

- `uv run pytest -q`
- `kreports mcp-doctor`
- `kreports mcp-smoke --company 005930`
- `kreports verify-release-artifact --db ./kreports.db`
- `curl /healthz` returns `ok: true`
- `curl /readyz` returns HTTP 200 with `ok: true` and no required failures
- `kreports dataset-audit --top 20`
- `kreports dataset-completeness --year 2025 --years-back 5 --sample-size 100`
- `kreports dataset-auditor-readiness --year 2025 --years-back 5`
- Endpoint has `KREPORTS_MCP_TOKEN`
- Endpoint does not have `DART_API_KEY`
