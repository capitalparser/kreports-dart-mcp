# Database Evidence Hardening Execution Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute, review, integrate, and rehearse the approved database evidence hardening across isolated worktrees without modifying the live database.

**Architecture:** A sequential schema-foundation worktree removes shared migration/model conflicts. Three Terra High feature worktrees then run in parallel from the same foundation commit, followed by one integrated rehearsal worktree and an evidence-gated retained APFS clone run.

**Tech Stack:** Git linked worktrees, Terra High agents, Python 3.12, SQLAlchemy, SQLite, APFS clonefile, pytest, Ruff, uv

## Global Constraints

- The integration branch is `codex/professional-integration`.
- Every implementation agent uses `gpt-5.6-terra` with high reasoning effort.
- Follow test-driven development and the exact component plans.
- Every task receives specification-compliance review before code-quality review.
- Do not touch the live database, its sidecars, any network endpoint, or any remote Git ref.
- Do not push, open a pull request, merge remotely, deploy, or delete a retained clone.
- Stop the real clone run when preflight reports a non-empty WAL/SHM, active
  process, digest drift, failed quick check, insufficient reserve, or a
  non-APFS target.

---

## Component Plans

- `docs/superpowers/plans/2026-07-29-db-schema-foundation.md`
- `docs/superpowers/plans/2026-07-29-audit-fee-observation-store.md`
- `docs/superpowers/plans/2026-07-29-financial-compact-provenance.md`
- `docs/superpowers/plans/2026-07-29-quality-ledger-mcp-lifecycle.md`
- `docs/superpowers/plans/2026-07-29-db-evidence-clone-rehearsal.md`

### Task 1: Establish the Plan Baseline

**Files:**
- Read: the approved design and all five component plans.
- Test: current integration worktree only.

**Interfaces:**
- Produces a clean, committed integration HEAD used by the foundation worktree.

- [ ] **Step 1: Record branch and worktree identity**

```bash
git status --short --branch
git rev-parse HEAD
git worktree list --porcelain
```

Expected: `codex/professional-integration` is clean and no planned DB branch or
path is already occupied.

- [ ] **Step 2: Run the pre-change focused baseline**

```bash
uv run pytest tests/test_schema_migrations.py tests/test_audit_fee_collector.py tests/test_runtime_db_export.py tests/test_company_year_quality.py tests/test_mcp_resources.py -q
uv run ruff check kreports/db/migrations.py kreports/db/models.py kreports/collector/audit_fee_collector.py kreports/maintenance/financial_compact.py kreports/quality/company_year.py kreports/mcp/server.py
```

Expected: record exact pass/fail counts and distinguish pre-existing failures
from later regressions.

- [ ] **Step 3: Confirm live identity is not part of baseline tests**

Inspect test command arguments and environment. They must use pytest temporary
databases only and must not contain the absolute live database path.

### Task 2: Implement and Integrate Schema Foundation

**Files:**
- Worktree: `/Users/kjun/vault/01_Projects/kreports_dart_mcp/.worktrees/db-schema-foundation`
- Branch: `codex/db-schema-foundation`
- Plan: `docs/superpowers/plans/2026-07-29-db-schema-foundation.md`

**Interfaces:**
- Produces reviewed revisions 09–11 and matching ORM contracts.

- [ ] **Step 1: Create the linked worktree from current integration HEAD**

```bash
git worktree add /Users/kjun/vault/01_Projects/kreports_dart_mcp/.worktrees/db-schema-foundation -b codex/db-schema-foundation codex/professional-integration
```

- [ ] **Step 2: Dispatch the Terra High implementation agent**

Give the agent only the foundation plan, design, `AGENTS.md`, exact worktree
path, and prohibition on DB/network/remote operations. Require RED/GREEN
evidence and one commit per plan task.

- [ ] **Step 3: Review each foundation task**

For each task, first use a fresh reviewer to compare the commit against the
plan. After compliance passes, use a fresh code-quality reviewer for
correctness, migration safety, test quality, and backward compatibility.
Important or Critical findings return to the implementer for a new focused
commit and repeat review.

- [ ] **Step 4: Verify and fast-forward integration**

```bash
git -C /Users/kjun/vault/01_Projects/kreports_dart_mcp/.worktrees/db-schema-foundation status --short
git merge --ff-only codex/db-schema-foundation
```

Run the foundation verification commands again on the integration worktree.

### Task 3: Create Three Parallel Feature Worktrees

**Files:**
- Worktree: `/Users/kjun/vault/01_Projects/kreports_dart_mcp/.worktrees/db-audit-observations`
- Worktree: `/Users/kjun/vault/01_Projects/kreports_dart_mcp/.worktrees/db-financial-provenance`
- Worktree: `/Users/kjun/vault/01_Projects/kreports_dart_mcp/.worktrees/db-quality-lifecycle`

**Interfaces:**
- Consumes the same schema-foundation integration HEAD.
- Produces three independently reviewable feature branches.

- [ ] **Step 1: Create all three linked worktrees**

```bash
git worktree add /Users/kjun/vault/01_Projects/kreports_dart_mcp/.worktrees/db-audit-observations -b codex/db-audit-observations codex/professional-integration
git worktree add /Users/kjun/vault/01_Projects/kreports_dart_mcp/.worktrees/db-financial-provenance -b codex/db-financial-provenance codex/professional-integration
git worktree add /Users/kjun/vault/01_Projects/kreports_dart_mcp/.worktrees/db-quality-lifecycle -b codex/db-quality-lifecycle codex/professional-integration
```

- [ ] **Step 2: Verify the common base**

```bash
git -C /Users/kjun/vault/01_Projects/kreports_dart_mcp/.worktrees/db-audit-observations merge-base HEAD codex/professional-integration
git -C /Users/kjun/vault/01_Projects/kreports_dart_mcp/.worktrees/db-financial-provenance merge-base HEAD codex/professional-integration
git -C /Users/kjun/vault/01_Projects/kreports_dart_mcp/.worktrees/db-quality-lifecycle merge-base HEAD codex/professional-integration
```

Expected: all three results equal the integration HEAD recorded immediately
after foundation integration.

### Task 4: Run Three Terra High Slices to Completion

**Files:**
- Audit plan: `docs/superpowers/plans/2026-07-29-audit-fee-observation-store.md`
- Financial plan: `docs/superpowers/plans/2026-07-29-financial-compact-provenance.md`
- Quality/lifecycle plan: `docs/superpowers/plans/2026-07-29-quality-ledger-mcp-lifecycle.md`

**Interfaces:**
- Produces three clean branches, each with focused and full verification
  evidence.

- [ ] **Step 1: Dispatch all three Terra High agents**

Each agent owns one worktree and completes every task in its component plan
without pausing between slices. It reports commit SHAs, RED/GREEN commands,
full-suite result, Ruff result, and any residual risk.

- [ ] **Step 2: Apply two-stage review**

As each branch completes, perform specification-compliance review followed by
code-quality review. Reviewers may inspect only that worktree and must not
modify the live database or remote Git state.

- [ ] **Step 3: Require clean branch gates**

```bash
git -C /Users/kjun/vault/01_Projects/kreports_dart_mcp/.worktrees/db-audit-observations status --short
git -C /Users/kjun/vault/01_Projects/kreports_dart_mcp/.worktrees/db-financial-provenance status --short
git -C /Users/kjun/vault/01_Projects/kreports_dart_mcp/.worktrees/db-quality-lifecycle status --short
```

Expected: no uncommitted file.

### Task 5: Integrate Feature Branches

**Files:**
- Integration worktree only.

**Interfaces:**
- Consumes the three reviewed feature branches.
- Produces one integrated database evidence implementation.

- [ ] **Step 1: Merge audit observation work**

```bash
git merge --no-ff codex/db-audit-observations -m "merge: add audit fee observation store"
```

Run the audit plan's focused suite.

- [ ] **Step 2: Merge financial provenance work**

```bash
git merge --no-ff codex/db-financial-provenance -m "merge: add financial compact provenance"
```

Resolve shared test-file conflicts by retaining the union of schema revisions
and assertions. Run the financial plan's focused suite.

- [ ] **Step 3: Merge quality and lifecycle work**

```bash
git merge --no-ff codex/db-quality-lifecycle -m "merge: add quality freshness and MCP cleanup"
```

Resolve shared test-file conflicts by retaining all revision-09–11,
observation, compact, and quality expectations. Run all three component focused
suites, the default suite, and Ruff.

- [ ] **Step 4: Independent integrated review**

A fresh reviewer checks cross-slice transaction ordering, migration list and
checksums, public status parity, runtime artifact coverage, and absence of
live-path or network dependencies. Fix and re-review every Important or
Critical finding.

### Task 6: Implement Integrated Clone Rehearsal

**Files:**
- Worktree: `/Users/kjun/vault/01_Projects/kreports_dart_mcp/.worktrees/db-evidence-rehearsal`
- Branch: `codex/db-evidence-rehearsal`
- Plan: `docs/superpowers/plans/2026-07-29-db-evidence-clone-rehearsal.md`

**Interfaces:**
- Consumes the fully integrated feature HEAD.
- Produces a reviewed opt-in end-to-end rehearsal path.

- [ ] **Step 1: Create the rehearsal worktree**

```bash
git worktree add /Users/kjun/vault/01_Projects/kreports_dart_mcp/.worktrees/db-evidence-rehearsal -b codex/db-evidence-rehearsal codex/professional-integration
```

- [ ] **Step 2: Execute the rehearsal plan with Terra High**

Require real file-backed temporary SQLite, the 17-call MCP matrix, expanded
semantic snapshot, idempotency, and source digest tests. No live source path is
passed during implementation tests.

- [ ] **Step 3: Two-stage review and integration**

After compliance and code-quality reviews pass:

```bash
git merge --ff-only codex/db-evidence-rehearsal
```

Run the rehearsal plan's full verification on integration HEAD.

### Task 7: Final Temporary and APFS Evidence

**Files:**
- No source edit unless a verified test defect requires a reviewed fix.

**Interfaces:**
- Produces final local evidence, retained clone/report paths when preflight
  permits, and an explicit production recommendation.

- [ ] **Step 1: Run complete local verification**

```bash
uv run pytest
uv run ruff check kreports tests
git status --short --branch
```

- [ ] **Step 2: Run disposable file-backed revision-08 to revision-11 rehearsal**

Use the integrated CLI against a generated temporary database. Verify
migrations, all local backfills, second-pass semantic equality, integrity, and
17 MCP calls.

- [ ] **Step 3: Revalidate the real source read-only preflight**

Run the exact process, sidecar, stat, immutable quick-check, free-space, and
SHA-256 checks specified in the clone-rehearsal plan. If a non-empty stale SHM
remains, report `preflight_blocked` and request the separate SQLite normal-close
authorization; do not clean it.

- [ ] **Step 4: Run and retain the APFS clone rehearsal when preflight passes**

Report the source initial/final SHA-256, clone path, logical and allocated size,
revision ledger, backfill counts, provenance coverage, fingerprint coverage,
semantic digest equality, 17-call MCP status matrix, and remaining limitations.

- [ ] **Step 5: Final review and handoff**

The handoff separates:

- local focused/default test evidence;
- disposable DB rehearsal evidence;
- real APFS clone evidence or exact preflight blocker;
- live database immutability;
- local-only commit state and absence of push/PR/deployment;
- later production decision items.
