# Raw Document Retention Policy

## Verdict

The deployable MCP dataset is derived-data-first. Full DART XML/HTML raw
documents are retained only when they improve re-parsing, source verification,
or hot-year audit evidence review.

## Dataset Tiers

Every tier is part of one evidence chain:

1. **Raw layer**: source XML/HTML retained only under the policy below.
2. **Evidence layer**: receipt-bound sections, excerpts, hashes, and DART links.
3. **Structured layer**: financial, audit, peer, group, and quality facts used
   by MCP tools.

The release artifact binds the selected SQLite DB hash and inline raw count.
`verify-release-artifact` recomputes both, so retained raw content cannot drift
silently between build and deployment.

### Tier A: Always-on Runtime DB

Keep these compact tables available for public MCP responses:

- `financials`
- `financial_facts_compact`
- `auditors`
- `audit_fees`
- `report_sections`
- `accounting_note_chapters`
- `accounting_policy_items`
- `audit_procedure_items`
- `evidence_documents`
- `disclosures`

Completeness is judged against these derived tables, not against full raw XML
coverage.

Full warehouse tables such as `financial_facts` can remain in the maintainer DB,
but deployable runtime artifacts should use `financial_facts_compact` unless a
workflow explicitly needs every XBRL line item.

### Tier B: Hot Raw Archive

Keep raw XML/HTML only for selected hot coverage:

- current and prior annual report years;
- large-cap or frequently queried peer universes;
- documents needed to debug parser gaps.

Hot raw files should be compressed and addressed through `storage_uri`.
Long derived evidence text can also be externalized. In that case the runtime DB
keeps the excerpt plus `full_text_uri`, `full_text_hash`, `full_text_length`, and
`full_text_compressed_length`.

### Tier C: Cold / On-Demand Raw

Older or rarely queried raw filings can be fetched on demand with the user's
DART API key or restored from external storage. Public MCP runtime should report
the cache status instead of pretending the raw filing is locally available.
The caller key is request-scoped: it must not be stored, logged, echoed, or
included in structured, legacy, stdio, error, or release-manifest surfaces.

## Operational Rules

- Do not run raw source-document backfill as the unattended default.
- Default automated backfill must not collect new `source_documents.raw_content`.
  `scripts/run_complete_dataset_backfill.sh` skips raw report section expansion
  unless `KREPORTS_ENABLE_RAW_BACKFILL=1` is explicitly set.
- Raw report collection commands are also blocked at the CLI guard unless all
  of the following are true:
  - `KREPORTS_ENABLE_RAW_BACKFILL=1`
  - `RAW_STORAGE_BACKEND=file` or `RAW_STORAGE_BACKEND=gcs`
  - `RAW_STORAGE_KEEP_INLINE=false`
- Use `scripts/run_source_documents_backfill.sh` only for explicit hot-raw
  archive expansion.
- Legacy raw scripts source `scripts/raw_backfill_guard.sh`; they either skip
  raw collection in default dataset backfill or fail closed for raw-only jobs.
- Confirm the effective collector behavior with `kreports raw-storage-config`.
- Confirm storage write/read/hash behavior with `kreports raw-storage-smoke`.
- Before clearing inline raw XML, verify externalized storage with
  `kreports verify-raw-storage`.
- Clearing inline XML creates SQLite reusable pages, but physical file shrinkage
  requires a separate `VACUUM` or `VACUUM INTO` run with enough free space.

## Capacity Notes

Recent measured compression:

- 824.95MB raw XML
- 96.31MB gzip
- approximately 88% reduction

Recent compact runtime artifact smoke:

- Maintainer DB: 2.1GB
- Compact runtime SQLite artifact: 729MB
- Compact runtime artifact gzip uploaded to GCS: 162.8MB
- `financial_facts_compact`: 68,770 rows
- Externalized long accounting note chapters: 100 rows
- Runtime DB artifact URI:
  `gs://kreports-raw-documents-gen-lang-client-0171998581/runtime-db/kreports-compact-2021-2025.db.gz`

This is still additive until inline SQLite raw content is cleared and the DB is
rebuilt. On a nearly full disk, pause raw expansion and continue only derived
table backfill.
