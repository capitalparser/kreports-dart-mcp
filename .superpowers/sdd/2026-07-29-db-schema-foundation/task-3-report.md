# Task 3 Report: Revision 11 Quality Freshness Schema

## Scope and base

- Worktree: `/Users/kjun/vault/01_Projects/kreports_dart_mcp/.worktrees/db-schema-foundation`
- Branch: `codex/db-schema-foundation`
- Required base and starting HEAD: `22225ab747f966675027732f6f2b528bc8b921cb`
- Scope completed: append-only revision 11, additive `CompanyYearQuality` ORM
  fields, and schema/migration tests only. No data backfill was added.

## RED

Command:

```bash
uv run pytest tests/test_schema_migrations.py::test_company_year_quality_freshness_migration_is_additive tests/test_company_year_quality.py::test_company_year_quality_schema_is_versioned_append_only -q
```

Result: `2 failed` as expected. The registry had no `MIGRATIONS[10]`, and the
exact versioned schema list ended at revision 10.

## GREEN

Commands:

```bash
uv run pytest tests/test_schema_migrations.py::test_company_year_quality_freshness_migration_is_additive tests/test_company_year_quality.py::test_company_year_quality_schema_is_versioned_append_only -q
uv run pytest tests/test_schema_migrations.py::test_company_year_quality_freshness_migration_upgrades_revision_10_row -q
uv run pytest tests/test_schema_migrations.py tests/test_company_year_quality.py -q
git diff --check
```

Results:

- Requested focused contract tests: `2 passed`.
- File-backed revision-10 to revision-11 upgrade test: `1 passed`.
- Full Task 3 focused suites: `42 passed`; 227 existing deprecation warnings.
- `git diff --check`: passed.

## Files changed

- `kreports/db/migrations.py`
  - Appends `20260711_11_company_year_quality_freshness` with two schema-only
    `ALTER TABLE ... ADD COLUMN` statements and safe defaults.
- `kreports/db/models.py`
  - Adds `input_fingerprint` and `evidence_summary_json` to
    `CompanyYearQuality` with matching Python and server defaults.
- `tests/test_schema_migrations.py`
  - Covers revision 11 shape and a file-backed revision-10 database upgrade.
    The upgrade case seeds a quality row, records checksums through revision
    10, verifies only revision 11 applies, verifies a second run is empty,
    preserves the seeded row and composite primary key, and checks defaults.
  - Isolates the pre-existing revision-10-only compact test to its intended
    registry cutoff; its synthetic fixture does not contain an unrelated
    quality table required by revision 11.
- `tests/test_company_year_quality.py`
  - Extends the exact append-only revision and column contracts through
    revision 11.

## Self-review

- Revision 01 through 10 migration text and order were not changed.
- Revision 11 contains no `INSERT`, `UPDATE`, `DELETE`, `SELECT`, or backfill.
- The upgrade test uses a temporary file-backed SQLite database, not
  `Base.metadata.create_all`, and proves the additive path over a revision-10
  ledger with a retained row and key contract.
- No live `kreports.db`, WAL/SHM sidecar, network, remote Git state, SDD
  progress ledger, or production database was touched.

## Concerns

- Running Ruff on all four Task 3 files reports 11 pre-existing style rules in
  existing imports and datetime usages. The reported locations do not include
  the revision-11 additions; no unrelated lint cleanup was made in this task.
