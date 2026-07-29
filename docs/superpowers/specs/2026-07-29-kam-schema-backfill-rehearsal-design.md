# KAM Schema and Backfill Rehearsal Design

## Approval and Purpose

The live professional MCP validation found a structural data-readiness gap:
the operational database has schema revisions `20260711_01` through
`20260711_04`, while the current application requires revisions
`20260711_05` through `20260711_08`. In particular, the database lacks
`kam_items` and the KAM linkage columns on `audit_procedure_items`.

The user selected the recommended rollout boundary: prove the complete
migration, derived-data rebuild, and MCP behavior on an APFS copy-on-write
clone while keeping the live database immutable. This document defines that
rehearsal. It does not authorize modification of the live database.

## Goals

- Exercise the official append-only migration path on a byte-identical clone.
- Reconstruct local KAM matter rows for business years 2021 through 2025.
- Reconcile procedure rows to the reconstructed KAM identities.
- Prove idempotency, relational integrity, and source traceability.
- Re-run the professional MCP surface against the rehearsed clone and remove
  raw schema failures from chatbot, structured pack, and resource output.
- Produce an operator-readable, bounded report that supports a later
  production migration decision.
- Prove that the live database digest did not change.

## Non-Goals

- No write, migration, checkpoint, vacuum, or sidecar creation on the live
  database.
- No DART API call, remote collection, or raw-document repair.
- No claim that locally missing filing evidence is absent from DART.
- No promotion of `limited` or `missing` to `usable` merely because the schema
  exists.
- No modification of parser semantics, MCP answer contracts, or professional
  status rules in this slice.
- No deletion of the rehearsal clone before the user has received its path,
  size, result, and cleanup choice.
- No push, pull request, merge, or production rollout.

## Safety Boundary

The implementation accepts an explicit source database path and an explicit
rehearsal directory. It must not infer either from the current directory.
Before copying, it resolves both paths and fails closed unless all of the
following are true:

- the source exists, is a regular file, and is not a symbolic link;
- the rehearsal directory exists on the same APFS volume and is not the
  source directory, repository root, home directory, or filesystem root;
- the target database does not already exist;
- source and target paths are distinct after resolution;
- no non-empty `-wal` or `-shm` sidecar exists beside the source;
- the source passes immutable read-only `PRAGMA quick_check`;
- at least 10 GiB remains available before the clone and at every mutation
  phase;
- no collector or backfill lease is active against the source database.

The runner records the source path only in the local run report, never in
committed documentation or fixtures. It records source size, modification
metadata, inode, and SHA-256 before cloning.

The APFS clone is created with clonefile semantics. A normal full copy is not
an automatic fallback: unsupported copy-on-write behavior is a
`preflight_blocked` result. Immediately after cloning, the runner computes the
clone SHA-256 and a second source SHA-256. The rehearsal proceeds only when
both source digests equal the initial digest. The target is then the only
database opened in collector mode.

## Architecture

### Rehearsal Orchestrator

A dedicated maintenance module owns the state machine, path validation,
subprocess environment, phase results, and report. A thin CLI command exposes
it to operators. The orchestration uses these explicit phases:

1. `source_preflight`
2. `clone_created`
3. `schema_migrated`
4. `kam_dry_run_complete`
5. `kam_rebuild_complete`
6. `procedure_reconcile_complete`
7. `idempotency_verified`
8. `mcp_validation_complete`
9. `live_immutability_verified`

Every phase is append-only in the report. A failed phase stops all later
mutating phases. Re-running against an existing clone is rejected; resumption
must use an explicit, previously emitted run identifier and must verify the
recorded clone identity first.

### Process Isolation

Database configuration is import-time state in the current application.
Therefore each schema, rebuild, and MCP phase runs in a fresh subprocess with
an explicit environment:

```text
DB_URL=sqlite:////absolute/path/to/rehearsal.db
KREPORTS_RUNTIME_MODE=collector   # migration and derived-data rebuild only
KREPORTS_RUNTIME_MODE=readonly    # MCP validation only
DART_API_KEY=                     # deliberately unavailable
```

The environment contains only the values required for the phase. The
orchestrator never imports a database-bound application module before binding
the clone. This prevents an import cache from silently retaining the live
database engine.

## Schema Migration

The migration phase invokes the supported `init_db()` path, not ad hoc DDL.
The source currently records revisions `01` through `04`; current HEAD
contains revisions through `08`. The expected newly recorded revisions are:

| Revision | Rehearsal effect |
|---|---|
| `20260711_05_kam_items` | create matter-level KAM storage and indexes |
| `20260711_06_audit_procedure_linkage` | add KAM linkage and typed procedure metadata |
| `20260711_07_audit_fee_availability` | add typed fee/hour availability provenance |
| `20260711_08_group_audit_graph` | create the current group-audit graph schema |

The runner derives this pending set from the checked-out migration registry
and the clone ledger. It does not hard-code `08` as permanently latest.
After migration it verifies:

- every checked-out revision is present exactly once;
- every recorded checksum matches the checked-out migration;
- the KAM table, procedure linkage columns, fee availability columns, group
  graph tables, declared indexes, unique constraints, and foreign keys exist;
- a second `init_db()` call applies zero revisions;
- `PRAGMA foreign_key_check` and `PRAGMA quick_check` return no issue.

Any checksum drift, unexpected pending revision, partial schema, or integrity
failure is `migration_failed`.

## KAM Rebuild

### Dry Run

For each year 2021 through 2025, the runner first executes
`rebuild-kam-items --dry-run`. It captures bounded aggregate counts for:

- receipts by `full_body`, `summary_only`, `missing`, and `error`;
- items by the same quality states;
- source-basis distribution;
- reason and audit-response coverage;
- a capped sample of receipt-level limitations.

Dry-run output must contain no write and must not create database sidecars.
Any receipt-level `error` is reported and blocks the write pass unless the
error is explicitly classified as a known, reviewed parser limitation.

### Write Pass

The runner then executes `rebuild-kam-items` in ascending year order,
2021 through 2025. The command reconstructs rows solely from evidence already
present in the clone. It must not call DART or repair source documents.

`rows_written` is treated as the number of reconciled rows, not the number of
physical changes. Completion therefore depends on post-write database facts,
not on a zero `rows_written` value.

The year-level gate records:

- target receipt count and persisted KAM row count;
- KAM quality and source-basis distributions;
- distinct companies and receipts;
- title, normalized topic, reason, and audit-response coverage;
- duplicate logical identities;
- rows whose filing receipt, company, year, or source type disagrees with the
  source evidence.

No year may silently borrow another company, year, filing receipt, or
standalone summary to create apparent full-body coverage.

## Procedure Reconciliation

After all KAM years complete, the runner executes
`index-audit-procedures` for 2021 through 2025 in ascending order. It records:

- processed KAM identities;
- successful and failed identities;
- reconciled procedure rows;
- non-null `kam_item_id`, method, parser version, and quality coverage;
- orphaned procedure links;
- KAM items with an audit response but no extracted procedure;
- procedure rows linked across a different receipt, source type, or ordinal.

Zero procedures can be a valid data result only when the corresponding KAM
does not contain a usable audit-response body. It cannot hide an indexer
failure.

## Idempotency and Determinism

Before the first write pass, after the first complete pass, and after a second
complete pass, the runner stores a deterministic semantic snapshot. Volatile
database fields such as page layout, file modification time, and run timestamp
are excluded. The snapshot includes:

- KAM primary IDs and logical identity keys;
- normalized semantic fields, body hashes, source basis, parser version, and
  quality status;
- procedure primary IDs, KAM foreign keys, ordinals, hashes, methods, typed
  link fields, parser version, and quality status;
- per-year counts and integrity-query results.

The second KAM rebuild and procedure reconciliation may report reconciled
rows, but must produce the same semantic snapshot and stable primary/foreign
key identities. New duplicates, ID churn, orphan creation, or changed
semantics is `backfill_failed`.

## MCP Validation

MCP validation binds the rehearsed database through the existing immutable
read-only live-test harness and exercises the same 17 professional calls used
by the prior validation. It compares the enriched tool result, Korean chatbot
answer, `answer_pack`, and detailed resource where available.

The following KAM-dependent tools receive explicit regression gates:

- `build_audit_acceptance_pack`
- `get_audit_report_sections`
- `get_kam_lifecycle`
- `compare_peer_kam_topics`

For each tool:

- the canonical status is one of `usable`, `limited`, `missing`, or `error`;
- answer, pack, and resource agree on status and core facts;
- no raw SQLite error, missing-table message, or missing-column message reaches
  the user;
- filing-backed facts retain valid receipt provenance;
- limitations identify evidence insufficiency rather than implementation
  internals;
- tables remain bounded and answer the professional question directly.

Samsung Electronics (`005930`) is the materiality probe because the clone
contains multi-year KAM evidence for that company. A schema error is a hard
failure. `limited` remains acceptable when topic, reason, audit response,
procedure, or source coverage is genuinely incomplete. `usable` is accepted
only when the existing professional contract proves all required evidence.

The other professional tools are regression probes for migration side effects,
especially audit-fee availability and group-audit schema additions. The
rehearsal does not interpret schema presence as data readiness.

## Live Immutability Proof

The runner never constructs a writable application URL for the source. It
re-checks the source SHA-256 and file identity:

- after clone creation;
- after schema migration;
- after KAM and procedure writes;
- after MCP validation;
- immediately before reporting completion.

Any source digest, inode, size, or unexpected sidecar change is
`live_digest_changed`, a critical stop condition. The final report must show
the initial and final source digests and an explicit equality result.

## Report and Retention

The run emits:

- a machine-readable JSON report with schema version and phase outcomes;
- a concise Markdown report for an auditor/operator;
- bounded command logs with secrets and absolute source paths excluded from
  committed artifacts;
- the retained clone path, logical size, allocated size, and remaining disk
  space in the local handoff only.

The report distinguishes:

- `preflight_blocked`
- `migration_failed`
- `backfill_failed`
- `data_quality_limited`
- `mcp_schema_closed`
- `live_digest_changed`
- `complete`

The clone is retained after handoff so the result can be inspected. Cleanup is
a separate, explicit user-approved action. If free space falls below the
10 GiB reserve, the run stops before the next mutation and reports the retained
clone rather than deleting it automatically.

## Verification Strategy

Implementation follows test-driven development:

1. path, symlink, sidecar, disk-reserve, same-volume, and source-stability
   tests;
2. clone identity and no-full-copy-fallback tests;
3. fresh-process environment and import-order tests;
4. pending-migration discovery, checksum, schema, and second-run idempotency
   tests;
5. bounded year-order, stop-on-error, and no-network rebuild tests;
6. semantic snapshot and stable-ID tests;
7. MCP schema-error closure and cross-layer parity tests;
8. a full rehearsal on an APFS clone of the live database;
9. final independent review of code, tests, run artifacts, and live digest.

Mocks may prove orchestration boundaries, but completion requires the real
migration registry, real SQLite clone, real rebuild commands, and real MCP
dispatch path.

## Acceptance Criteria

The slice is complete only when all of the following are true:

- safety tests fail closed for every invalid source or target condition;
- an APFS clone begins byte-identical to the stable live source;
- every pending checked-out migration applies through `08` and a second
  migration pass applies none;
- 2021–2025 KAM dry runs and write passes finish without unreviewed errors;
- procedure reconciliation finishes without orphan or cross-receipt links;
- the second full pass preserves the semantic digest and row identities;
- SQLite quick check, foreign-key check, uniqueness checks, and schema checks
  pass;
- all 17 professional MCP calls complete against the clone;
- the four KAM-dependent calls expose no missing-table or missing-column
  failure in raw result, chatbot answer, pack, or resource;
- professional statuses remain evidence-grounded and information limitations
  remain explicit;
- the initial and final live database SHA-256 are identical;
- focused tests, related regression tests, full-suite comparison, Ruff, and
  independent review provide no new Critical or Important issue;
- the report gives a clear production recommendation without modifying the
  live database.

## Production Decision Boundary

A successful rehearsal proves that the checked-out migration and local
derived-data path can close the observed schema failure on a representative
clone. It does not authorize live migration. A production change requires a
separate user decision after reviewing:

- migration duration and allocated-space growth;
- per-year KAM quality distributions;
- procedure linkage coverage;
- MCP before/after evidence;
- any remaining auditor or investor information gaps;
- rollback and backup readiness.
