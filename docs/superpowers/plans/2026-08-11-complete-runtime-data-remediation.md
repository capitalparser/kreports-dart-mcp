# Complete Runtime Data Remediation Plan

**Goal:** Turn the current investor-ready compact database into an evidence-consistent runtime whose latest derived rows, quality ledger, release manifest, and MCP presentation agree, while making remaining auditor/full-population gaps explicit and safely backfillable.

**Implementation workspace:** `/Users/kjun/vault/01_Projects/kreports_dart_mcp/.worktrees/final-kreports-integration`

**Current runtime candidate:** `/private/tmp/kreports-investor-r5.KVjjJj/kreports-rehearsal.db`

## Global constraints

- Preserve fail-closed public/read-only MCP behavior and the approved 33-tool public catalog.
- Never claim `auditor_full` readiness unless every required release-gate threshold passes.
- Use verified KOSPI/KOSDAQ historical membership as the population denominator; never fall back to the current company master.
- Preserve receipt-level evidence and do not substitute another company's or another year's receipt.
- Treat local-cache absence as `missing` or `unavailable`, never as proof that the public filing omitted the disclosure.
- Run all data mutations against an APFS clone or another recoverable candidate first; do not mutate or delete the currently verified runtime DB in place.
- Run disk preflight before a bulk write. Protect source DBs, raw evidence, active worktrees, and unidentified temp data.
- Use existing cached report sections, evidence documents, KAM bodies, and externalized raw sources before making DART calls.
- If DART collection is needed, keep it bounded, idempotent, resumable, and recorded in `backfill_runs`.
- Add public-interface regression tests before changing release, export, or MCP behavior.
- Do not push, publish, or replace a deployed artifact without separate user approval.

## Task 1: Refresh quality truth and prevent stale release snapshots

1. Reproduce the observed condition where policy/note rows are newer than `company_year_quality` while the release artifact still verifies.
2. Add one failing public-interface regression test proving that a release gate or artifact cannot claim a current quality snapshot after its quality inputs change.
3. Implement the minimum fail-closed freshness validation using the existing quality fingerprints/evidence summaries where possible.
4. On a recoverable clone of the runtime candidate, rebuild `company_year_quality` for 2021-2025, rebuild the release manifest, and verify both `public_runtime` and `auditor_full` profiles.
5. Record before/after coverage, timestamps, file integrity, and artifact verification.

## Task 2: Rebuild audit-procedure coverage from existing evidence, then bounded source recovery

1. Diagnose 2025 procedure gaps by source availability, KAM body quality, parser eligibility, and historical membership.
2. Add a failing regression test for any discovered indexing or quality-ledger defect before changing code.
3. Reindex audit procedures from cached full-body KAM/report-section/evidence sources for all eligible years.
4. Rebuild affected quality rows and measure the canonical 2025 denominator coverage.
5. If cache recovery is insufficient, execute a disk-safe bounded DART/raw-source batch, verify it, and leave an idempotent resume point.

## Task 3: Close remaining current-year and time-series gaps

1. Produce exact gap cohorts for accounting policy, accounting note chapters, materiality inputs, three-year core financials, and five-year financials.
2. Prioritize rows recoverable from current DB evidence and externalized raw storage.
3. Run bounded, resumable backfill batches for the remaining cohorts and rebuild affected quality rows after each batch.
4. Recompute release coverage and stop only on a concrete external blocker or when the cohort is exhausted.

## Task 4: Make compact source-storage metadata truthful

1. Add a failing runtime-export test showing that a compact row cannot remain `storage_status='inline'` when `raw_content` is removed and no URI exists.
2. Define and implement a truthful compact-runtime state without breaking externalized or derived-only semantics.
3. Verify search, release-artifact, raw-coverage, and runtime-export contracts.

## Task 5: Connect MCP peer-policy results to facts and sources

1. Add a failing dispatch-level test for a usable peer-policy comparison that currently renders tables but no confirmed facts or receipt-level sources.
2. Populate the answer envelope from the same rows shown in the policy/note comparison tables.
3. Downgrade the result when adequate receipt-level evidence is unavailable.
4. Verify the Samsung sample and at least one missing-cache sample through `dispatch_tool`.

## Completion evidence

- `PRAGMA integrity_check` and `PRAGMA foreign_key_check` on the final candidate.
- Focused tests for every changed slice plus the repository's relevant release/MCP regression suite.
- Fresh release artifacts and exact `public_runtime` / `auditor_full` gate output.
- Actual MCP samples for financial snapshot, audit-hours inputs, peer policy comparison, and audit-procedure search.
- Clean implementation worktree with commits and no push or deployment replacement.
