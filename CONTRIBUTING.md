# Contributing to KReports

KReports turns Korean DART filings into read-only MCP tools for investors,
auditors, and accounting practitioners. Contributions are welcome when they
improve correctness, provenance, maintainability, or user-facing answer quality.

## Good first contribution areas

- Parser fixtures for DART XML/HTML edge cases.
- Tests for audit report sections, KAM extraction, accounting policy notes, and
  disclosure event classification.
- Documentation for MCP client setup, remote deployment, and dataset readiness.
- Performance improvements for SQLite runtime queries and compact export.
- Safer failure handling for DART API limits, stale backfill records, and
  partial datasets.

## Development setup

```bash
git clone https://github.com/capitalparser/kreports-dart-mcp.git
cd kreports-dart-mcp
uv sync --extra dev
uv run pytest -q
```

Self-hosted collection requires a DART OpenAPI key. Do not commit API keys,
database files, raw filings, or customer/client data.

```bash
mkdir -p ~/.config/kreports
$EDITOR ~/.config/kreports/collector.env
```

Expected private collector env:

```env
DART_API_KEY=<opendart-key>
KREPORTS_RUNTIME_MODE=collector
```

## Pull request expectations

- Keep changes narrowly scoped.
- Add or update tests for parser, MCP, and data-quality behavior changes.
- Preserve read-only MCP behavior unless the change is explicitly about
  maintainer-side collection.
- Include source/provenance handling when adding new analysis output.
- Avoid storing large raw source documents in Git. Use fixtures only when they
  are small and legally safe to redistribute.

## Data and provenance principles

KReports distinguishes three layers:

- `source_documents`: original DART document evidence, stored inline or in
  external object storage with hash and URI metadata.
- `evidence_documents`: normalized text used for MCP search and narrative
  answers.
- structured tables: financial facts, auditor history, audit fees, accounting
  policies, KAM/procedure indexes, and disclosure events.

New features should prefer structured tables for deterministic results and
evidence documents for source-grounded narrative answers.

## Tests

Run the full test suite before submitting:

```bash
uv run pytest -q
```

For focused changes, run the relevant test file first, then the full suite.
