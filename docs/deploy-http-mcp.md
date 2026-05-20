# Deploy KReports Remote MCP

KReports runs best as two separate processes:

1. **Remote MCP endpoint**: read-only, no DART API key, serves `/mcp`.
2. **Collector/backfill worker**: private, has `DART_API_KEY`, writes the same dataset.

The MCP endpoint reads `kreports.db` on every tool call, so newly backfilled rows
are visible without redeploying the endpoint. Keep the endpoint read-only and run
collection elsewhere.

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
```

Do not put `DART_API_KEY` in the MCP endpoint environment.

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
scripts/run_full_dataset_backfill.sh
```

For unattended local collection that resumes after DART daily-limit resets, use
the macOS launchd wrapper in [automated-backfill.md](automated-backfill.md).

Backfill is document-first. The collector stores raw business/audit report bodies
in `source_documents`, then populates derived tables such as `report_sections`,
`auditors`, and `subsidiary_auditor_matrix`. When a new extractor is added, rerun
it from the local source cache instead of downloading the same DART document
again:

```bash
kreports run-document-extractors --source-type business_report
kreports run-document-extractors --year 2025 --source-type business_report --extractor auditors
```

This command does not require `DART_API_KEY`; it only reads cached
`source_documents` and writes derived rows.

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
- `curl /readyz` returns nonzero company count
- Endpoint has `KREPORTS_MCP_TOKEN`
- Endpoint does not have `DART_API_KEY`
