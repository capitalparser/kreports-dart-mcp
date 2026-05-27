# Raw Document Retention Policy

## Verdict

The deployable MCP dataset is derived-data-first. Full DART XML/HTML raw
documents are retained only when they improve re-parsing, source verification,
or hot-year audit evidence review.

## Dataset Tiers

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

## Operational Rules

- Do not run raw source-document backfill as the unattended default.
- Default automated backfill is `scripts/run_derived_dataset_backfill.sh`.
- Use `scripts/run_source_documents_backfill.sh` only for explicit hot-raw
  archive expansion.
- Set `RAW_STORAGE_BACKEND=file` or `RAW_STORAGE_BACKEND=gcs` before collecting
  new hot raw documents. Otherwise the collector stores raw XML/HTML inline in
  `source_documents.raw_content` and the SQLite DB will keep growing.
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
