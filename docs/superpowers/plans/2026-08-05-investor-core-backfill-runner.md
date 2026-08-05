# Bounded Investor-Core Backfill Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and commit a fail-closed, bounded DART investor-core backfill runner that executes only deterministic planner-selected annual Q4 source-ready targets under an exact request and disk budget.

**Architecture:** A new maintenance runner performs all database binding, hash, planner-target, free-space, cache, row-count, and evidence-report work. A scoped request budget in the fetcher counts every actual DART HTTP attempt, while the financial collector propagates bounded stop signals without changing normal unbounded behavior. A JSON-only Typer command exposes dry-run by default and explicit execution.

**Tech Stack:** Python 3.11+, SQLite read-only connections, SQLAlchemy-backed existing collector, `httpx`, Typer, pytest, Ruff.

## Global Constraints

- Do not access the network or any real DB under `/Users/kjun` or `/private/tmp` outside test-created temporary databases.
- Work only in `/private/tmp/kreports-investor-runner-wt` on `codex/investor-core-backfill-runner` based on `5f5d895`.
- Use the existing interpreter at `/Users/kjun/vault/01_Projects/kreports_dart_mcp/.worktrees/final-kreports-integration/.venv/bin/python` for tests and Ruff.
- Use `apply_patch` for source and test edits; do not push, create a PR, clean caches, or perform unrelated edits.
- Dry-run is the default and must make zero network, collector, or database writes.
- Execute mode requires collector runtime mode, explicit `--execute`, positive `--max-api-calls`, and `--expected-db-sha256`.
- Require an existing regular non-symlink SQLite file and exact resolved binding to process `DB_URL`/`settings.db_url`; fail closed on mismatch.
- Consume only planner `selected_companies`; select only `source_ready=true` by default and reject non-source-ready execution.
- Use annual `quarter=4` exactly once per uncached target; never run all four quarters or force cached rows.
- Count every actual DART HTTP attempt, including retries and fallback endpoints, and never exceed the request budget.
- Force `settings.max_retries=1` only inside bounded execution and restore it afterward.
- Enforce the existing global 10 GiB free-space minimum before execution and before every target with an injectable probe.
- Return JSON-serializable evidence without exposing `crtfc_key`/the API key and do not rebuild downstream release artifacts.
- Define facts cache sufficiency as the seven canonical
  `CORE_FINANCIAL_METRICS`, not the whole compact metric projection.
- Reject hardlinks (`st_nlink != 1`) and capture/revalidate resolved path,
  device, and inode before execution, around each target, and around
  checkpoint/evidence; verify the actual `fin_collector.get_session()` writer
  against that same identity.
- After any target action, dispose the bound writer pool, perform and verify
  `PRAGMA wal_checkpoint(TRUNCATE)`, then collect immutable post-run hash/count
  evidence. Checkpoint/evidence failures return incomplete reports retaining
  outcomes and request-budget evidence.
- Apply the post-target free-space probe to cache hits as well as collector
  calls. Under a bounded request scope, HTTP-success JSON decoding failures are
  redacted generic transport/protocol stops; unbounded behavior stays legacy.

## File map

- Create: `CONTEXT.md` — required domain terminology boundary.
- Create: `kreports/maintenance/investor_core_backfill_runner.py` — bounded runner, validation, target extraction, execution, evidence report, and stable error classes.
- Modify: `kreports/collector/fetcher.py` — scoped actual-request budget and bounded transport stop signal.
- Modify: `kreports/collector/fin_collector.py` — preserve existing behavior outside bounded sessions and re-raise bounded stop signals.
- Modify: `kreports/cli/main.py` — JSON `run-investor-core-backfill` command and stable error handling.
- Create: `tests/test_investor_core_backfill_runner.py` — runner, safety, budget, and report tests.
- Create: `tests/test_investor_core_backfill_cli.py` — Typer command success/error tests.
- Create: `docs/superpowers/specs/2026-08-05-investor-core-backfill-runner-design.md` — approved design record.

### Task 1: Establish documentation and request-budget contract

**Interfaces:** `fetcher.request_budget(max_calls)` yields a budget object with `used_calls`, `endpoint_counts`, and an actual-attempt consume method. `fetcher.DartRequestBudgetExceeded` and `fetcher.DartTransportError` are bounded stop signals.

- [ ] Write tests proving an active budget counts each actual attempt, including retry attempts and distinct financial fallback endpoints, and rejects the next request before it occurs.
- [ ] Run the focused tests and observe the expected missing-budget failure.
- [ ] Implement the scoped context variable budget and instrument financial fetch attempts without putting credentials in endpoint labels or messages.
- [ ] Run the focused fetcher tests and existing fetcher/collector fallback tests.

### Task 2: Preserve bounded stop signals in the financial collector

**Interfaces:** `fin_collector.collect_financial` and `_try_summary_fallback` continue returning ordinary statuses for unbounded normal errors, but propagate `DartRequestBudgetExceeded`, `DartTransportError`, `DartApiAuthError`, and `DartApiLimitExceeded`.

- [ ] Add tests that inject each bounded stop signal through the CFS path and verify no OFS/summary fallback or later batch target is attempted.
- [ ] Run those tests red.
- [ ] Reuse the fetcher stop classes and add narrow re-raise clauses before existing broad exception handlers.
- [ ] Run the focused collector suite and confirm existing normal fallback tests remain green.

### Task 3: Implement fail-closed runner core

**Interfaces:** `run_investor_core_backfill(db_path, *, expected_db_sha256=None, execute=False, max_api_calls=None, coverage_year=None, threshold_pct=95.0, source_ready_only=True, planner_fn=..., collector_fn=..., cache_checker=..., disk_probe=..., settings_obj=...) -> dict[str, object]`.

- [ ] Write tests first for dry-run side-effect absence, deterministic source-ready target extraction/Q4 calls, DB URL mismatch, symlink, expected hash, non-source-ready rejection, free-space stop, settings restoration, cache skip, exact report counts, and credential redaction.
- [ ] Run the runner tests red.
- [ ] Implement path/SQLite/binding/hash validation, planner-only target extraction, canonical target digest, read-only relevant row counts, injectable free-space checks, and stable coded validation errors.
- [ ] Implement bounded execution with max-retry restoration, request-budget evidence, cache checks, one annual Q4 collector call, immediate durable stop classification, and report generation.
- [ ] Run runner tests green, then refactor only while preserving the evidence contract.

### Task 4: Add the JSON Typer command

**Interfaces:** `kreports run-investor-core-backfill --db PATH [--execute --expected-db-sha256 HEX --max-api-calls N]` always emits JSON; `--include-non-source-ready` is an explicit opt-in that execute mode rejects.

- [ ] Add CLI tests for default dry-run JSON and nonzero stable JSON error paths.
- [ ] Run the CLI tests red.
- [ ] Add the lazy CLI command, pass all safety options to the runner, print reports/errors without secrets, and exit nonzero for incomplete bounded stops.
- [ ] Run CLI tests green and run the existing planner CLI tests.

### Task 5: Verify, review, and commit

- [ ] Run focused runner/fetcher/collector/CLI tests with the required interpreter.
- [ ] Run Ruff on changed Python files with the required interpreter.
- [ ] Review `git diff --check`, `git diff`, status, and commit only feature files on the requested branch.
- [ ] Report commit SHA, changed files, exact commands/results, and limitations; explicitly state that no live backfill occurred.
