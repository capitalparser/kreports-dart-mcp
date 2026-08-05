# Bounded Investor-Core Backfill Runner Design

## Goal

Add a fail-closed, bounded maintenance runner that consumes the existing
investor-core planner output and can execute only the planner's deterministic,
source-ready annual Q4 targets in an explicitly authorized collector session.

## Architecture

`kreports.maintenance.investor_core_backfill_runner` owns all preflight,
target extraction, digesting, database identity checks, disk checks, row-count
evidence, execution orchestration, and JSON-serializable reporting. It never
creates targets from company rows or disclosure metadata; it uses only
`selected_companies[*].selected_years` from
`plan_investor_core_backfill`.

`kreports.collector.fetcher` provides a scoped request budget. The budget is
consumed immediately before every actual HTTP attempt and records endpoint
counts. `fin_collector.collect_financial` re-raises bounded budget, transport,
authentication, and quota stop signals while preserving its existing broad
error-to-`"error"` behavior outside a bounded session.

The CLI command `run-investor-core-backfill` always emits JSON. Dry-run is the
default and performs only read-only preflight/planning. Execution requires
collector runtime mode, `--execute`, a positive `--max-api-calls`, an expected
database SHA-256, and source-ready-only targets.

## Safety invariants

1. `--db` must be an existing regular, checkpointed SQLite file with no
   symlink in the file path. Immutable dry-run readers never create WAL/SHM
   sidecars. Its resolved path must equal the resolved SQLite path from both
   the process `DB_URL` (when present) and `settings.db_url`.
2. Execute mode verifies `--expected-db-sha256` before the first write or
   network operation and rechecks the database digest immediately before
   execution starts.
3. Execute mode requires collector runtime mode and a positive request budget;
   dry-run makes zero collector/network/write calls.
4. The first runner accepts only `source_ready=true` candidates. An explicit
   request to include non-source-ready candidates is rejected in execute mode.
5. Every selected target is `(corp_code, stock_code, year)` and is sorted by
   those fields. Each uncached target calls `collect_financial(stock_code,
   year, quarter=4)` exactly once. A target is cached only when local summary
   or full-fact source rows can rebuild all seven annual core metrics; an
   incomplete `financials` row is not sufficient. Sufficient cached targets
   are recorded and do not consume a request.
6. The global 10 GiB free-space floor is checked before execution, before each
   target, and after each target. The probe is injectable for tests.
7. Bounded execution forces `settings.max_retries=1` and restores the original
setting even when execution stops. The report never contains the API key.
8. Facts-backed cache sufficiency requires only the canonical seven
   `CORE_FINANCIAL_METRICS` for one annual source scope, not every broader
   compact-projection metric.
9. The requested path must have `st_nlink == 1`. The runner captures its
   resolved path, device, and inode and revalidates them immediately before
   execution, before and after every target, and around checkpoint/evidence.
   The SQLAlchemy writer bound into `fin_collector.get_session()` is verified
   against the same device/inode before it can commit.
10. After an action, the bound writer pool is disposed, SQLite runs
    `PRAGMA wal_checkpoint(TRUNCATE)`, and only a verified checkpoint permits
    immutable post-run hash/count evidence. Cache hits use the same
    post-target free-space probe as collection targets.
11. The default collector binding replaces and restores both
    `kreports.db.engine.engine` and `SessionLocal` under a process-local lock.
    This keeps every already-imported `get_session` function used by financial
    collection, company lookup, flags, and Beneish on the same verified target
    writer for the entire bounded collector scope.

## Evidence report

The runner returns a JSON-serializable mapping with schema/version, canonical
database path, before/after SHA-256, planner denominator/numerator/target/
shortfall, exact target count and digest, dry-run/execute mode, maximum and
used request calls, endpoint counts, exact outcome totals with bounded samples,
stop code/message, completion state, relevant before/after row counts, and
free-space before/after observations. It does not rebuild compact facts,
quality, manifests, or release gates.

If checkpointing or post-run evidence fails after an action, the runner returns
an incomplete report instead of raising away causal evidence. It retains target
outcomes, the request budget, before evidence, and any safely available partial
post-evidence; `completed=false` and the stable stop code identify the phase.
When a WAL checkpoint fails, main-file post hash and post row counts are
explicitly unavailable because they cannot represent the uncheckpointed frames;
safe free-space observation may still be retained.

## Error handling

Validation failures raise stable coded runner errors before any action. Bounded
DART request-budget exhaustion, authentication failure, quota/limit failure,
and transport/HTTP failure stop the batch immediately; unstarted targets are
counted as not run. CLI errors emit a stable JSON error code/message and exit
nonzero. Stop messages are generic and do not serialize exception text that
could contain a credential or request parameters.

Within bounded request scope, a JSON decoding failure after HTTP success is a
generic redacted transport/protocol stop. Outside bounded scope, the fetcher
continues to raise its legacy JSON-decoding exception.

## Testing

Tests use planner/collector fakes, temporary regular SQLite databases, injected
disk probes, and patched HTTP clients. They cover dry-run side-effect absence,
deterministic source-ready Q4 targets, database binding and symlink rejection,
hash checks, exact request counts across retry/fallback attempts, immediate
bounded stop classes, disk reserve stops, settings restoration, credential
redaction, non-source-ready rejection, and the JSON CLI error contract.
