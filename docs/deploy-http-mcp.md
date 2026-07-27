# Deploy KReports Remote MCP

KReports runs best as two separate processes:

1. **Remote MCP endpoint**: read-only, no DART API key, serves `/mcp`.
2. **Collector/backfill worker**: private, has `DART_API_KEY`, writes the same dataset.

The MCP endpoint reads `kreports.db` on every tool call, so newly backfilled rows
are visible without redeploying the endpoint. Keep the endpoint read-only and run
collection elsewhere.

For public pilot deployment, prefer a compact runtime DB artifact over the full
maintainer DB. The maintainer DB can retain full warehouse tables and extraction
logs, while the runtime artifact excludes heavy tables such as `financial_facts`,
`extraction_runs`, and `fetch_log`.

## Required Runtime Contract

Endpoint environment:

```env
KREPORTS_RUNTIME_MODE=readonly
DB_URL=sqlite:////data/kreports.db
KREPORTS_MCP_TOKEN=<long-random-token>
```

Collector environment:

```env
KREPORTS_RUNTIME_MODE=collector
DART_API_KEY=<opendart-key>
DB_URL=sqlite:////path/to/writable/kreports.db
RAW_STORAGE_BACKEND=gcs
RAW_STORAGE_BUCKET=<gcs-bucket-name>
RAW_STORAGE_PREFIX=dart/raw
RAW_STORAGE_KEEP_INLINE=false
```

Do not put `DART_API_KEY` in the MCP endpoint environment.
Do not put `RAW_STORAGE_BACKEND=inline` on the collector unless the intent is to
grow the SQLite DB with full raw XML/HTML bodies.

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

## On-Demand Disclosure Fetch Contract

The hosted endpoint is cache-first by default. If a public MCP user requests an
uncached ad-hoc disclosure, the endpoint may support on-demand DART fetch only
under this contract:

- the user supplies their own OpenDART API key for that request,
- the server-side collector `DART_API_KEY` is never used by the public endpoint,
- the user key is not persisted, logged, or echoed,
- fetched documents are cached and subsequent reads use the cache,
- responses disclose whether the answer came from cache or a user-keyed fetch.

## Local Production-Like Run

```bash
export KREPORTS_MCP_TOKEN="$(openssl rand -hex 32)"
docker compose -f docker-compose.deploy.yml up --build
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

For Claude Web or other remote clients, put this behind HTTPS and connect to:

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

## Backfill While Serving

Use a separate shell or worker:

```bash
export DART_API_KEY=<opendart-key>
export KREPORTS_RUNTIME_MODE=collector
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

Compact runtime artifact flow:

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
kreports export-runtime-db \
  --output artifacts/kreports-runtime-2021-2025.db \
  --year-from 2021 \
  --year-to 2025 \
  --profile compact
kreports upload-runtime-db-artifact \
  --db artifacts/kreports-runtime-2021-2025.db \
  --bucket <gcs-bucket-name> \
  --prefix runtime-db \
  --profile compact \
  --year-from 2021 \
  --year-to 2025
kreports build-release-manifest \
  --db artifacts/kreports-runtime-2021-2025.db
kreports verify-release-artifact \
  --db artifacts/kreports-runtime-2021-2025.db
```

The build command is evidence-producing: it writes atomically and may record
`release_gate.passed=false` with named blockers. The verify command is
deployment-gating: it recomputes the DB size/hash, schema and required indexes,
dataset manifest, inline raw count, current release gate, 32-tool wire contract,
isolated real-dispatch catalog smoke, and the approved packaged golden contract
hash. The user-keyed network fetch is checked in its no-key fail-closed state.
Verify exits non-zero on drift or any current blocker.

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
- only one collector writes at a time,
- `dataset-audit` shows no duplicate unique-key groups.

Move to Postgres once public traffic and continuous backfill run at the same
time for more than a small pilot.

## Deployment Checklist

- `uv run pytest -q`
- `kreports mcp-doctor`
- `kreports mcp-smoke --company 005930`
- `kreports dataset-audit --top 20`
- `kreports dataset-completeness --year 2025 --years-back 5 --sample-size 100`
- `curl /healthz` returns `ok: true`
- `kreports verify-release-artifact --db <runtime.db>` exits zero
- `curl /readyz` returns HTTP 200 with `ok: true` and no required failures
- Endpoint has `KREPORTS_MCP_TOKEN`
- Endpoint does not have `DART_API_KEY`
