# Database Evidence Clone Rehearsal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the retained-clone rehearsal so revisions 09–11, all local evidence rebuilds, semantic idempotency, professional MCP output, and live-database immutability are proved in one fail-closed run.

**Architecture:** The existing KAM rehearsal remains backward compatible by default. A new opt-in evidence-hardening mode adds three fresh-process worker actions before the semantic snapshot, repeats them during the second pass, and expands the snapshot so one report covers the normalized audit claims, compact provenance, quality fingerprints, KAM/procedure evidence, and 17 MCP calls.

**Tech Stack:** Python 3.12, SQLite, APFS clonefile, subprocess, Typer, pytest, Ruff, uv

## Global Constraints

- Implement only after the schema foundation and all three feature slices are integrated.
- Do not open the live database writable.
- Do not delete or modify live `-wal` or `-shm` sidecars.
- Keep the existing `rehearse-kam-schema-backfill` behavior unchanged unless the new mode is explicitly selected.
- Every mutating worker binds only the retained clone in collector mode.
- Do not call DART or expose absolute source paths in committed fixtures.
- Stop before cloning if the source has any non-empty sidecar or identity drift.
- Do not push, open a pull request, merge, deploy, or run a production migration.

---

## File Structure

- Modify `kreports/maintenance/kam_rehearsal_worker.py`: local DB evidence actions and expanded semantic snapshot.
- Modify `kreports/maintenance/kam_backfill_rehearsal.py`: optional phases and second-pass ordering.
- Modify `kreports/cli/main.py`: new explicit evidence-hardening rehearsal command.
- Modify `tests/test_kam_rehearsal_worker.py`: worker action and digest tests.
- Modify `tests/test_kam_backfill_rehearsal.py`: phase ordering, stop conditions, and live identity checks.
- Modify `tests/test_kam_rehearsal_integration.py`: file-backed end-to-end proof through revision 11.

### Task 1: Add Fresh-Process Evidence Rebuild Actions

**Files:**
- Modify: `kreports/maintenance/kam_rehearsal_worker.py`
- Modify: `tests/test_kam_rehearsal_worker.py`

**Interfaces:**
- Produces worker actions:
  - `audit-fee-observation-backfill`
  - `financial-compact-rebuild`
  - `company-year-quality-rebuild`
- Every action accepts the existing required `--year`.

- [ ] **Step 1: Write failing worker-action tests**

Bind a temporary revision-08 database through the existing worker harness,
apply current migrations, seed local evidence, and assert:

```python
audit = run_worker("audit-fee-observation-backfill", year=2025)
financial = run_worker("financial-compact-rebuild", year=2025)
quality = run_worker("company-year-quality-rebuild", year=2025)

assert audit["ok"] is True
assert audit["inserted_observations"] >= 1
assert financial["ok"] is True
assert financial["total_inserted_or_updated"] >= 1
assert quality["ok"] is True
assert quality["rows_written"] >= 1
```

Patch the DART client constructor and network transports to raise if invoked.
Assert every action completes without hitting them.

- [ ] **Step 2: Run tests to verify RED**

```bash
uv run pytest tests/test_kam_rehearsal_worker.py -q
```

Expected: the worker rejects the three unknown actions.

- [ ] **Step 3: Implement bounded per-year actions**

Each action validates collector mode and the explicit clone capability before
importing database-bound modules:

```python
if action == "audit-fee-observation-backfill":
    return backfill_audit_fee_observations(
        year_from=year,
        year_to=year,
        dry_run=False,
    )
if action == "financial-compact-rebuild":
    return rebuild_financial_facts_compact(
        year_from=year,
        year_to=year,
    )
if action == "company-year-quality-rebuild":
    return rebuild_company_year_quality(
        year_from=year,
        year_to=year,
    )
```

- [ ] **Step 4: Run worker tests**

```bash
uv run pytest tests/test_kam_rehearsal_worker.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kreports/maintenance/kam_rehearsal_worker.py tests/test_kam_rehearsal_worker.py
git commit -m "feat: add database evidence rehearsal workers"
```

### Task 2: Expand the Semantic Snapshot

**Files:**
- Modify: `kreports/maintenance/kam_rehearsal_worker.py`
- Modify: `tests/test_kam_rehearsal_worker.py`

**Interfaces:**
- Consumes: existing `semantic-snapshot` action.
- Produces additive snapshot sections `audit_fee_observations`,
  `financial_compact_provenance`, and `company_year_quality_freshness`.

- [ ] **Step 1: Write failing semantic-digest tests**

Run the three evidence rebuilds, take two snapshots, and assert:

```python
assert first["semantic_sha256"] == second["semantic_sha256"]
assert first["audit_fee_observations"]["current_count"] >= 1
assert first["audit_fee_observations"]["historical_count"] >= 0
assert first["financial_compact_provenance"]["uncitable_count"] >= 0
assert first["company_year_quality_freshness"]["blank_fingerprint_count"] == 0
```

Change one current audit claim, one compact citation basis, and one quality
fingerprint in separate cases; each change must alter the semantic SHA-256.
Changing only `observed_at`, `fetched_at`, or `updated_at` must not alter it.

- [ ] **Step 2: Run tests to verify RED**

```bash
uv run pytest tests/test_kam_rehearsal_worker.py -q
```

Expected: FAIL because the snapshot covers only KAM/procedure semantics.

- [ ] **Step 3: Add bounded ordered snapshot queries**

Include these non-volatile fields:

```python
AUDIT_OBSERVATION_SEMANTIC_FIELDS = (
    "observation_hash", "source_slot_hash", "corp_code", "bsns_year",
    "source_class", "source_rcept_no", "source_period",
    "contract_fee_m", "contract_hours", "actual_fee_m", "actual_hours",
    "availability_status", "quality_status", "parser_version",
    "is_current", "supersedes_hash",
)
FINANCIAL_COMPACT_PROVENANCE_FIELDS = (
    "corp_code", "bsns_year", "fs_div", "metric_key", "amount",
    "source_account_id", "source_table", "unit", "period_type",
    "citation_rcept_no", "citation_report_nm", "citation_basis",
    "quality_status",
)
QUALITY_FRESHNESS_FIELDS = (
    "corp_code", "bsns_year", "input_fingerprint",
    "evidence_summary_json", "quality_version",
)
```

Sort every row by its logical key, canonicalize JSON fields, and exclude all
timestamps and SQLite layout. Add bounded aggregate counts to the report while
hashing every selected semantic row.

- [ ] **Step 4: Run semantic snapshot tests**

```bash
uv run pytest tests/test_kam_rehearsal_worker.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kreports/maintenance/kam_rehearsal_worker.py tests/test_kam_rehearsal_worker.py
git commit -m "test: expand database semantic snapshot"
```

### Task 3: Add Opt-In Evidence-Hardening Phases

**Files:**
- Modify: `kreports/maintenance/kam_backfill_rehearsal.py`
- Modify: `tests/test_kam_backfill_rehearsal.py`

**Interfaces:**
- Produces additive keyword argument
  `include_db_evidence: bool = False` on
  `run_kam_schema_backfill_rehearsal() -> dict[str, object]`.

- [ ] **Step 1: Write failing phase-order tests**

With `include_db_evidence=False`, assert the existing `PHASES` sequence and
worker calls are unchanged. With `True`, assert this exact order after procedure
reconciliation and before the first semantic snapshot:

```python
(
    "audit_fee_observations_backfilled",
    "financial_compact_provenance_rebuilt",
    "quality_ledger_rebuilt",
)
```

During the second idempotency pass, assert worker action order:

```python
[
    *five_years("kam-rebuild"),
    *five_years("procedure-index"),
    *five_years("audit-fee-observation-backfill"),
    *five_years("financial-compact-rebuild"),
    *five_years("company-year-quality-rebuild"),
    "semantic-snapshot",
]
```

Inject a failure into every new phase in parameterized tests. Assert later
workers do not run and `safety.assert_source_unchanged()` is called before the
next worker and during finalization.

- [ ] **Step 2: Run tests to verify RED**

```bash
uv run pytest tests/test_kam_backfill_rehearsal.py -q
```

Expected: FAIL because the opt-in argument and phases are absent.

- [ ] **Step 3: Implement conditional phases**

Use one helper:

```python
def evidence_year_loop(action: str) -> list[dict[str, object]]:
    outputs: list[dict[str, object]] = []
    for year in REHEARSAL_YEARS:
        safety.assert_source_unchanged(expected_source)
        safety.assert_free_space(rehearsal_dir, min_free_bytes=min_free_bytes)
        outputs.append(
            run_worker(WorkerInvocation(action, "collector", year=year))
        )
    return outputs
```

Add action-specific timeouts to `_WORKER_TIMEOUT_SECONDS`:

```python
"audit-fee-observation-backfill": 900,
"financial-compact-rebuild": 1800,
"company-year-quality-rebuild": 1800,
```

Persist each first-pass phase independently. In the existing idempotency
operation, repeat the same three loops after KAM and procedure loops and before
the second snapshot. Extend `_snapshot_integrity()` with the new aggregate
sections so the concise report agrees with the semantic digest. Preserve the
old path exactly when `include_db_evidence=False`.

- [ ] **Step 4: Run orchestrator tests**

```bash
uv run pytest tests/test_kam_backfill_rehearsal.py tests/test_kam_rehearsal_worker.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kreports/maintenance/kam_backfill_rehearsal.py tests/test_kam_backfill_rehearsal.py
git commit -m "feat: rehearse full database evidence rebuild"
```

### Task 4: Explicit CLI and File-Backed End-to-End Test

**Files:**
- Modify: `kreports/cli/main.py`
- Modify: `tests/test_kam_backfill_rehearsal.py`
- Modify: `tests/test_kam_rehearsal_integration.py`

**Interfaces:**
- Produces CLI command `rehearse-db-evidence-hardening` with the same required
  source, rehearsal-directory, repository-root, and Python arguments as the
  KAM rehearsal command.

- [ ] **Step 1: Write failing CLI and integration tests**

Assert the new CLI calls:

```python
run_kam_schema_backfill_rehearsal(
    source_db=resolved_source,
    rehearsal_dir=resolved_rehearsal_dir,
    repository_root=resolved_repository,
    python_executable=resolved_python,
    include_db_evidence=True,
)
```

Build a small file-backed revision-04 fixture with financial, disclosure,
audit-fee JSON, KAM, and procedure source evidence. Run the real orchestrator
with the existing small-test free-space threshold and assert:

```python
assert report["status"] in {
    "complete", "mcp_schema_closed", "data_quality_limited"
}
assert report["live_sha256_unchanged"] is True
assert report["idempotency"]["semantic_sha256_equal"] is True
assert report["mcp"]["tool_count"] == 17
assert clone_revisions[-1] == "20260711_11_company_year_quality_freshness"
```

- [ ] **Step 2: Run tests to verify RED**

```bash
uv run pytest tests/test_kam_backfill_rehearsal.py tests/test_kam_rehearsal_integration.py -q
```

Expected: CLI lookup failure and missing evidence phases.

- [ ] **Step 3: Add the explicit command**

Reuse the existing command's strict path resolution and error rendering. The
new command differs only by setting `include_db_evidence=True` and its
operator-facing description.

- [ ] **Step 4: Run related and full verification**

```bash
uv run pytest tests/test_kam_rehearsal_worker.py tests/test_kam_backfill_rehearsal.py tests/test_kam_rehearsal_integration.py tests/test_all_tools_contract.py -q
uv run pytest
uv run ruff check kreports/maintenance/kam_rehearsal_worker.py kreports/maintenance/kam_backfill_rehearsal.py kreports/cli/main.py tests/test_kam_rehearsal_worker.py tests/test_kam_backfill_rehearsal.py tests/test_kam_rehearsal_integration.py
```

Expected: focused and full suites pass; Ruff reports no issue.

- [ ] **Step 5: Commit**

```bash
git add kreports/cli/main.py tests/test_kam_backfill_rehearsal.py tests/test_kam_rehearsal_integration.py
git commit -m "test: verify database evidence clone rehearsal"
```

## Real APFS Clone Gate

After all code and test reviews pass, revalidate the exact live source without
opening it writable:

```bash
lsof -- /absolute/source/kreports.db /absolute/source/kreports.db-wal /absolute/source/kreports.db-shm
stat -f '%d %i %z %m %l' /absolute/source/kreports.db
shasum -a 256 /absolute/source/kreports.db
```

The real rehearsal may start only when no process holds the files, the WAL and
SHM preflight satisfies the approved KAM safety contract, the source
`quick_check` passes through the immutable reader, and the 10 GiB reserve is
available. A stale non-empty SHM is a `preflight_blocked` result; this plan does
not authorize cleaning it.

Run the new CLI with explicit resolved paths, retain the clone and reports, and
then independently compare source identity and SHA-256 again. Do not delete the
clone without separate user approval.
