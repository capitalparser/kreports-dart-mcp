# Full-suite DB Lifecycle Hardening Report

## Scope

- Isolated worktree: `codex/full-suite-db-lifecycle-hardening`
- No live database, external endpoint, or network action was used.

## Diagnosed failures

1. The company-year test froze the migration sequence at revision 11. It
   failed when the checked-out append-only policy/schema revisions 12--14
   were correctly present.
2. A readonly MCP process used the ordinary SQLite engine URL. Opening and
   disposing that connection against a WAL database changed the main database
   file. A plain `mode=ro` reader makes committed WAL data visible, but SQLite
   updates its shared-memory reader state, so the SHM checksum changes.
3. `immutable=1` avoids those writes but must never be used while WAL contains
   uncheckpointed frames because that would serve an incomplete snapshot.
4. The same risk applies to a non-empty rollback journal: immutable mode does
   not perform recovery, so it could expose a recovery-required main file.

## Changes

- The migration test now pins the historic revision-1--11 prefix, the current
  12--14 append-only tail, lexical ordering, unique revision identifiers, and
  revision-name syntax. It still executes and requires every revision.
- A readonly file-backed runtime now opens a fresh `NullPool` DBAPI connection
  through a guarded immutable URI. It first rejects a non-empty WAL with the
  stable `runtime_db_unavailable:uncheckpointed_wal` error and a non-empty
  rollback journal with `runtime_db_unavailable:hot_rollback_journal`, rather
  than reading an incomplete snapshot or modifying SQLite sidecars.
- SQLite file URIs are decoded then reconstructed with `Path.as_uri()`, so
  paths with spaces, `#`, `?`, and `%` retain their identity. A configured
  `file:` URI cannot override readonly mode; unknown query parameters fail
  closed rather than being silently dropped.

## Verification

- RED: stale revision list, ordinary readonly MCP main-DB SHA mutation, and
  `mode=ro` SHM checksum mutation were reproduced.
- Checkpointed lifecycle probe: readonly MCP reads the committed row, exits on
  SIGTERM, disposes its engine, and leaves main/WAL/SHM checksums unchanged.
- Adversarial WAL probe: a committed non-empty WAL produces the bounded
  uncheckpointed-WAL failure and leaves main/WAL/SHM unchanged.
- Adversarial rollback-journal probe: a non-empty journal produces the bounded
  hot-rollback-journal failure and leaves main/WAL/SHM/journal checksums
  unchanged.
- Configured percent-encoded `file:` URI with a forced `mode=rw` remains
  readonly; write attempts fail and all SQLite file checksums remain unchanged.

## Deployment assumption

The sidecar check is a pre-open snapshot gate, not an interprocess lock. A
readonly deployment therefore assumes the database is checkpointed and has no
concurrent writer. A WAL or rollback journal created after the check is an
external time-of-check/time-of-use breach; immutable mode does not make that
later state authoritative.
