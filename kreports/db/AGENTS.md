# KReports Database Implementation And Mutation Rules

This file applies to work under `kreports/db/`. The repository-root `AGENTS.md`
and `kreports/AGENTS.md` remain authoritative for architecture, source
precedence, read-only runtime behavior, validation, and user-facing evidence.

## Shared Database Ownership

KReports uses one canonical data model, but not every contributor or agent may
mutate every physical database.

- Treat maintainer databases, team-query databases, release-candidate databases,
  and runtime databases as controlled artifacts.
- Shared databases are read-only unless the user explicitly authorizes the
  current task to mutate that exact database.
- Only the designated database maintainer may apply shared schema migrations,
  run shared backfills, regenerate shared datasets, export runtime databases, or
  build and verify release artifacts.
- Access to a database path, environment variable, credential, or writable file
  does not itself grant permission to mutate it.
- Contributors may read the full approved dataset needed for their work, but
  development writes must use disposable databases, fixtures, or explicitly
  approved private copies.

## No Manual Data Repair

Never repair a shared data defect by manually editing, inserting, or deleting a
row in the database.

The required path is:

```text
defect identified
→ source filing and affected row recorded
→ canonical collector/parser/transformation/analysis defect found
→ code fixed
→ regression test added
→ Pull Request reviewed and merged
→ database maintainer rebuilds or migrates the shared artifact
→ result and release evidence reverified
```

A manual data patch hides the reproducibility defect and will usually be lost or
reintroduced during the next collection or rebuild.

## Disposable Development Databases

- Use an isolated disposable SQLite database for migrations, schema tests,
  parser tests, and write-path validation.
- The database path must clearly identify that it is disposable, for example
  `.codex-validation/kreports.db` or a temporary directory.
- Do not point test commands at a maintainer, team-query, or runtime database.
- Remove disposable database files after validation and keep them untracked.
- Test fixtures must be small, legally safe, and free of secrets or confidential
  client data.

## Schema And Migration Changes

Before changing models or migrations, record:

1. the user or product need;
2. the canonical table and field owner;
3. existing tables, indexes, migrations, and callers affected;
4. forward migration behavior;
5. existing-data and rollback implications;
6. runtime export and release-manifest implications;
7. tests proving both a new database and an existing compatible database behave
   correctly.

Rules:

- Do not edit the meaning of an already-applied migration silently.
- Add a new migration for a new schema change.
- Keep model declarations and migration statements aligned.
- Do not weaken uniqueness, provenance, source identity, or read-only guarantees
  merely to make a migration pass.
- A schema change is incomplete until required indexes, data-quality contracts,
  runtime export, and release verification are considered.

## Preflight Before Any Authorized Shared Write

Before executing a command that may mutate a non-disposable database, state and
verify:

- exact database path or identifier;
- database role: maintainer, team-query, release candidate, or runtime;
- command to be executed;
- tables or artifacts expected to change;
- credential and runtime mode involved;
- backup or rollback path;
- disk-space and WAL/file-state preflight when relevant;
- explicit user authorization for that operation.

Stop when the target, impact, or authorization is unclear.

## Runtime Read-Only Contract

- Runtime MCP access must remain read-only unless a separately approved feature
  explicitly defines a bounded request-scoped write path.
- A runtime response must not trigger migrations, backfills, collector writes,
  silent cache persistence, or raw-document storage.
- Read-only failures must fail closed and report a coverage or readiness gap;
  they must not initialize a new empty database or switch to an unrelated file.
- `/healthz`, successful SQL access, or successful tool execution does not prove
  release readiness. Use the verified release artifact and `/readyz` contract.

## Pull-Request Evidence For Database Work

A database-related PR must include:

- exact changed models, migrations, indexes, and affected callers;
- disposable database setup and exact validation commands;
- pass/fail/error/skipped counts;
- expected shared-data regeneration or backfill work;
- runtime export and release-verification impact;
- confirmation that no shared database was manually edited;
- confirmation that no database file, WAL, SHM, raw filing, cache, or secret was
  committed.