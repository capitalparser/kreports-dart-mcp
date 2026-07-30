# Task 5 — policy chapter schema contract

## Scope

- Added `kreports.db.schema_contract` as the single release/rehearsal source of truth for required runtime tables, columns, and exact SQLite index definitions.
- Added `accounting_note_chapters` to the runtime contract, including its policy-comparison inputs and named deterministic identity index.
- Changed the ORM identity from an anonymous SQLite unique constraint to the named unique index `uq_accounting_note_chapter_identity`. The identity deliberately excludes receipt/body/version fields: a corrected annual filing updates one logical company/year/FS/note/section slot rather than leaving policy comparison with two unordered candidates.
- Added migration `20260731_12_accounting_note_chapter_contract`. It creates a missing table, adds the named indexes idempotently, preserves existing rows, and fails transactionally when existing duplicate identities make a unique index unsafe.
- Made rehearsal use the same exact full index definitions as release (table, ordered columns, unique flag, and partial predicate) and expose `invalid_indexes` separately from `missing_indexes`.
- Added an `accounting_policy_changes` readiness feature/count/rate distinct from `accounting_policy_items`.

## Strict TDD

RED:

- `tests/test_db_schema_contract.py` initially failed with `ModuleNotFoundError: kreports.db.schema_contract`.
- New migration regression tests then exposed stale expected revision lists and legacy migration paths without `accounting_note_chapters`; the migration was made additive by creating the table first.

GREEN:

- `tests/test_db_schema_contract.py tests/test_schema_migrations.py tests/test_accounting_note_chapters.py tests/test_release_artifact.py tests/test_auditor_readiness.py tests/test_kam_rehearsal_integration.py tests/test_kam_backfill_rehearsal.py -q` passed.
- `tests/test_policy_changes.py tests/test_mcp_contracts.py tests/test_mcp_answer_pack.py tests/test_accounting_note_mcp_contract.py tests/test_accounting_note_answer_surface.py -q`: `80 passed`.
- Final focused release/readiness/schema check: `88 passed`.
- Ruff on every changed Python file and `git diff --check`: passed.

## Migration/data risk

- A pre-existing duplicate `(corp_code, bsns_year, fs_div, note_no, section_type)` is intentionally a fail-closed migration blocker; the migration neither deletes nor chooses a row. Deduplicate with a separately reviewed evidence/backfill operation before applying the new revision.
- No live `kreports.db` was opened or modified.
