# Task 5 — policy chapter schema contract

## Scope

- Added `kreports.db.schema_contract` as the single release/rehearsal source of truth for required runtime tables, columns, and exact SQLite index definitions.
- Added `accounting_note_chapters` to the runtime contract, including its policy-comparison inputs and named deterministic identity index.
- Changed the ORM identity from an anonymous SQLite unique constraint to the named unique index `uq_accounting_note_chapter_identity`. The identity deliberately excludes receipt/body/version fields: a corrected annual filing updates one logical company/year/FS/note/section slot rather than leaving policy comparison with two unordered candidates.
- Added migration `20260731_12_accounting_note_chapter_contract`. It creates a missing table, adds the named indexes idempotently, preserves existing rows, and fails transactionally when existing duplicate identities make a unique index unsafe.
- Made rehearsal use the same exact full index definitions as release (table, ordered columns, unique flag, and partial predicate) and expose `invalid_indexes` separately from `missing_indexes`.
- Added an `accounting_policy_changes` readiness feature/count/rate distinct from `accounting_policy_items`.

## Changed files and commits

- `8cc90f3 Harden policy chapter schema contracts`
  - `kreports/db/schema_contract.py`
  - `kreports/db/models.py`, `kreports/db/migrations.py`
  - `kreports/release_artifact.py`, `kreports/maintenance/kam_rehearsal_worker.py`
  - `kreports/analysis/readiness.py`
  - focused schema, migration, release, rehearsal, and readiness tests
- `c512ccd test: cover policy chapter contract blockers`
  - literal release and rehearsal assertions for a missing `body_hash` column,
    a wrong named unique index, and a missing policy chapter table.
- `5804ef1 Address DB schema contract review`
  - moved all 12 `audit_fees` readiness columns into the shared contract;
  - made partial-index WHERE validation exact after whitespace normalization;
  - added revision `20260731_13_accounting_note_chapter_storage_contract` so a
    migration-created table reaches the full ORM storage schema and indexes;
  - added bounded SQLite `busy_timeout` + WAL retry/verification +
    `BEGIN IMMEDIATE` migration serialization (the pinned rehearsal MEMORY
    journal remains explicitly preserved and separately verified);
  - required two receipt-proven, latest-annual-filing comparable years for
    `accounting_policy_changes`, with unproven/non-comparable exclusions;
  - added literal two-connection repeated migration, exact-predicate,
    release/rehearsal parity, ORM-schema, and latest-receipt adversarial tests.
- `4065db4 Require requested year for policy readiness`
  - a historically comparable key now qualifies only when its proven years
    include the requested readiness year;
  - exposes `policy_change_excluded_missing_requested_year` so a proven old
    pair with no current-year chapter is inspectable rather than silently
    reported usable.

## Strict TDD

RED:

- `tests/test_db_schema_contract.py` initially failed with `ModuleNotFoundError: kreports.db.schema_contract`.
- New migration regression tests then exposed stale expected revision lists and legacy migration paths without `accounting_note_chapters`; the migration was made additive by creating the table first.
- Review remediation RED: `tests/test_db_schema_contract_review.py` failed all
  five adversarial cases on the reviewed revision: release/rehearsal audit-fee
  drift, a `WHERE ... AND 0` partial index, incomplete migration-created note
  schema, concurrent SQLite `database is locked`, and one unproven current
  policy row being reported usable.
- Final semantic RED: proven latest annual chapters for 2022 and 2023, with no
  2025 chapter, produced `accounting_policy_changes=usable` for `year=2025`.

GREEN:

- `tests/test_db_schema_contract.py tests/test_schema_migrations.py tests/test_accounting_note_chapters.py tests/test_release_artifact.py tests/test_auditor_readiness.py tests/test_kam_rehearsal_integration.py tests/test_kam_backfill_rehearsal.py -q` passed.
- `tests/test_policy_changes.py tests/test_mcp_contracts.py tests/test_mcp_answer_pack.py tests/test_accounting_note_mcp_contract.py tests/test_accounting_note_answer_surface.py -q`: `80 passed`.
- Final focused release/readiness/schema check: `88 passed`.
- Ruff on every changed Python file and `git diff --check`: passed.
- Final focused suite spanning schema contract/review, migration, release,
  rehearsal, readiness, policy, accounting-note, and MCP contract/answer-pack
  tests passed; Ruff and `git diff --check` passed again.
- Requested-year correction: readiness/policy adversarial suite `46 passed`;
  related MCP contract/answer-pack surfaces `73 passed`; Ruff and diff check
  passed.

## Migration/data risk

- A pre-existing duplicate `(corp_code, bsns_year, fs_div, note_no, section_type)` is intentionally a fail-closed migration blocker; the migration neither deletes nor chooses a row. Deduplicate with a separately reviewed evidence/backfill operation before applying the new revision.
- Revision 13 is additive. If a pre-existing note table is missing a storage
  column, it adds that nullable metadata column; if logical chapter identities
  are duplicated, revision 12 remains the blocking precondition.
- No live `kreports.db` was opened or modified.
