# Final Migration WAL and Schema Repair Report

## Scope

- Isolated worktree: `codex/final-migration-wal-fix`
- No live database, external endpoint, or network operation was used.

## Root causes

1. The retained-clone migration path set a file-backed connection to `MEMORY`
   journaling and intercepted subsequent WAL requests. This bypassed the
   file-backed SQLite migration contract and, when first adjusted, incorrectly
   enabled `query_only` for the writable migration path.
2. The shared schema contract newly verified
   `uq_backfill_runs_active_lease`, but a legacy ledger may already claim the
   original revision while the physical index is absent. No later migration
   repaired that state.
3. The peer policy comparison reads `accounting_policy_items`, which was not
   included in the shared release/rehearsal schema contract.
4. The first WAL allowance put `PRAGMA foreign_keys=ON` inside the
   MEMORY-only collector branch, leaving the writable retained-clone migrate
   connection at SQLite's default `foreign_keys=OFF`.

## Changes

- File-backed migrations now always set the bounded busy timeout, retry the
  WAL transition only for lock/busy errors, verify WAL, and then acquire
  `BEGIN IMMEDIATE`. In-memory SQLite remains the only no-WAL exception.
- Retained-clone `migrate` allows the verified file-backed WAL policy; all
  other collector rehearsal actions retain the in-memory-journal policy. Every
  collector connection enables foreign-key enforcement before either journal
  policy is selected.
- Added append-only revision `20260731_14_schema_contract_repair` for the
  policy item storage shape and named required indexes. The repair may skip an
  index only when its table is absent in a partial historical migration test;
  the shared release/rehearsal contract still fails closed for that absence.
- Added `accounting_policy_items` table, columns, identity, and query indexes
  to the shared contract used by release and rehearsal.

## Verification

- RED: file-backed `MEMORY` remained non-WAL; a ledger-claimed missing index
  was not repaired; peer policy table was absent from rehearsal validation.
- GREEN: `uv run --with pytest python -m pytest -q`
  `tests/test_kam_rehearsal_worker.py`
  `tests/test_db_schema_contract_review.py`
  `tests/test_schema_migrations.py`
  `tests/test_db_schema_contract.py`
  `tests/test_release_artifact.py`
  `tests/test_peer_note_presentation_comparison.py`
  `tests/test_all_tools_contract.py`
  `tests/test_mcp_catalog.py` -> `199 passed`.
- Focused file-backed `DELETE` and `MEMORY` two-connection migration probes
  both preserve WAL, serialize one applied ledger, and record every revision.
- RED/GREEN regression: the migrate-bound connection now proves
  `foreign_keys=1`, `query_only=0`, and `journal_mode=wal` after `init_db()`
  has applied migrations; the non-migrate collector memory-journal contract
  remains covered.
- Ruff and `git diff --check` pass.
