# Document-First Accounting Policy Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make accounting policy, accounting estimate/judgment, and basis-of-preparation disclosures first-class datasets extracted from business report financial statement notes, then expose them through MCP search and peer tools.

**Architecture:** The business report is the canonical document anchor. Collectors fetch and cache the filing package, extract financial statement note chapters into structured tables, and MCP tools read only local cache. Endpoint collectors for financials, audit fees, and auditors remain supporting datasets and should not block document extraction.

**Tech Stack:** Python 3.12, Typer CLI, SQLAlchemy/SQLite, pytest, MCP Tool registry.

---

### Task 0: Baseline Existing Work

**Files:** current modified code, docs, tests, scripts already present in the working tree.

- [ ] **Step 1: Verify no secret is staged**

Run:
```bash
rg -n "df786|DART_API_KEY=.*[A-Za-z0-9]{20,}|crtfc_key=[A-Za-z0-9]{20,}" --hidden --glob '!kreports.db*' --glob '!.venv/**' --glob '!logs/**' --glob '!.git/**'
```
Expected: no real API key values.

- [ ] **Step 2: Run full tests**

Run:
```bash
uv run pytest -q
```
Expected: `301 passed, 2 skipped` or better.

- [ ] **Step 3: Commit baseline**

Run:
```bash
git add .gitignore .env.example AGENTS.md CONTEXT.md Dockerfile docker-compose.deploy.yml docs plans scripts kreports tests uv.lock
git commit -m "feat: add readonly MCP dataset foundation"
```
Expected: commit succeeds; local runtime files remain untracked/ignored.

### Task 1: Schema For Note Chapters

**Files:**
- Modify: `kreports/db/models.py`
- Modify: `kreports/cli/main.py` init/migration helpers if present
- Test: `tests/test_accounting_note_chapters.py`

- [ ] **Step 1: Write failing schema test**

Add test asserting a new `accounting_note_chapters` table has columns:
`corp_code`, `bsns_year`, `fs_div`, `rcept_no`, `dcm_no`, `source_type`, `note_no`, `note_title`, `section_type`, `body`, `body_hash`, `body_length`, `fetched_at` and unique key over `corp_code, bsns_year, fs_div, note_no, section_type`.

- [ ] **Step 2: Run failing test**

Run:
```bash
uv run pytest tests/test_accounting_note_chapters.py -q
```
Expected: failure because model/table is missing.

- [ ] **Step 3: Implement model and migration**

Add SQLAlchemy model `AccountingNoteChapter`. `section_type` values are stored as strings: `basis`, `policy`, `estimate_judgment`, `other_note`.

- [ ] **Step 4: Run tests**

Run:
```bash
uv run pytest tests/test_accounting_note_chapters.py tests/test_integrity.py -q
```
Expected: pass.

- [ ] **Step 5: Commit**

Run:
```bash
git add kreports/db/models.py kreports/cli/main.py tests/test_accounting_note_chapters.py tests/test_integrity.py
git commit -m "feat: add accounting note chapter table"
```

### Task 2: Extract Note 2/3/4 Chapters From Business Report ZIP

**Files:**
- Modify: `kreports/analysis/queries.py`
- Modify: `kreports/collector/policy_collector.py`
- Test: `tests/test_accounting_note_chapters.py`

- [ ] **Step 1: Write failing parser tests**

Add XML fixture snippets where notes contain:
`2. 재무제표 작성기준`, `3. 중요한 회계정책`, `4. 중요한 회계추정 및 판단`.
Assert extractor returns three chapters with correct `note_no` and `section_type`.

- [ ] **Step 2: Verify red**

Run:
```bash
uv run pytest tests/test_accounting_note_chapters.py::test_extracts_basis_policy_estimate_chapters -q
```
Expected: fail because extractor is missing.

- [ ] **Step 3: Implement extractor**

Implement a pure parser that takes `note_section` text and returns chapter dicts. It should classify by heading keywords, not fixed note numbers only, because issuers may combine basis and policy.

- [ ] **Step 4: Run parser tests**

Run:
```bash
uv run pytest tests/test_accounting_note_chapters.py -q
```
Expected: pass.

- [ ] **Step 5: Commit**

Run:
```bash
git add kreports/analysis/queries.py kreports/collector/policy_collector.py tests/test_accounting_note_chapters.py
git commit -m "feat: extract accounting note chapters"
```

### Task 3: Persist Chapters During Policy Collection

**Files:**
- Modify: `kreports/collector/policy_collector.py`
- Modify: `kreports/cli/main.py`
- Test: `tests/test_policy_persistence.py`

- [ ] **Step 1: Write failing persistence test**

Mock `get_accounting_policy` or a new collector helper to return `chapters` and `items`. Assert `collect_policies_for_company()` upserts both `AccountingPolicyItem` rows and `AccountingNoteChapter` rows idempotently.

- [ ] **Step 2: Verify red**

Run:
```bash
uv run pytest tests/test_policy_persistence.py::test_collect_policies_persists_note_chapters -q
```
Expected: fail because chapters are ignored.

- [ ] **Step 3: Implement chapter upsert**

Add bulk upsert into `accounting_note_chapters`, including body hash and source citation.

- [ ] **Step 4: Run focused tests**

Run:
```bash
uv run pytest tests/test_policy_persistence.py tests/test_accounting_policy_cache.py -q
```
Expected: pass.

- [ ] **Step 5: Commit**

Run:
```bash
git add kreports/collector/policy_collector.py kreports/cli/main.py tests/test_policy_persistence.py tests/test_accounting_policy_cache.py
git commit -m "feat: persist accounting note chapters"
```

### Task 4: Put Policy Backfill In Document-First Order

**Files:**
- Modify: `scripts/run_full_dataset_backfill.sh`
- Modify: `scripts/run_document_first_backfill.sh`
- Modify: `docs/automated-backfill.md`
- Test: `tests/test_auditor_readiness.py` or script smoke test

- [ ] **Step 1: Write failing script assertion test**

Add a test that reads `scripts/run_full_dataset_backfill.sh` and asserts policy collection appears after business report collection and before `collect-all` financial endpoint backfill.

- [ ] **Step 2: Verify red**

Run:
```bash
uv run pytest tests/test_auditor_readiness.py::test_full_backfill_runs_policies_before_financial_endpoint -q
```
Expected: fail with current ordering.

- [ ] **Step 3: Reorder scripts**

Move `collect-policies` loops for 2021-2025 KOSPI/KOSDAQ immediately after business report collection and document extraction. Keep financial endpoint collection later.

- [ ] **Step 4: Run tests**

Run:
```bash
uv run pytest tests/test_auditor_readiness.py -q
```
Expected: pass.

- [ ] **Step 5: Commit**

Run:
```bash
git add scripts/run_full_dataset_backfill.sh scripts/run_document_first_backfill.sh docs/automated-backfill.md tests/test_auditor_readiness.py
git commit -m "chore: prioritize policy backfill after documents"
```

### Task 5: Expose Note Chapters In Search Layer

**Files:**
- Modify: `kreports/analysis/api.py`
- Modify: `kreports/mcp/tools.py`
- Test: `tests/test_auditor_peer_tools.py`
- Test: `tests/test_mcp_tools_registration.py`
- Test: `tests/test_dart_mcp.py`

- [ ] **Step 1: Write failing MCP search test**

Add `search_dataset(dataset="accounting_note_chapters", company="005930", year=2024, section_type="policy")` and assert company-grouped records include `note_no`, `note_title`, `section_type`, `body_excerpt`.

- [ ] **Step 2: Verify red**

Run:
```bash
uv run pytest tests/test_auditor_peer_tools.py::test_search_dataset_accounting_note_chapters -q
```
Expected: fail because dataset enum is missing.

- [ ] **Step 3: Implement API and MCP schema**

Add `accounting_note_chapters` to search dataset enum, SQL branch, MCP schema, and validation for `section_type`.

- [ ] **Step 4: Run focused tests**

Run:
```bash
uv run pytest tests/test_auditor_peer_tools.py tests/test_mcp_tools_registration.py tests/test_dart_mcp.py::TestToolRegistryConsistency -q
```
Expected: pass.

- [ ] **Step 5: Commit**

Run:
```bash
git add kreports/analysis/api.py kreports/mcp/tools.py tests/test_auditor_peer_tools.py tests/test_mcp_tools_registration.py tests/test_dart_mcp.py
git commit -m "feat: expose accounting note chapters in MCP search"
```

### Task 6: Verification And Backfill Smoke

**Files:**
- Modify: `scripts/evaluate_current_mcp_quality.py`
- Modify: `docs/disclosure-db-completeness.md`

- [ ] **Step 1: Add quality metrics**

Update evaluator to report chapter coverage by year/market and section type.

- [ ] **Step 2: Run full tests**

Run:
```bash
uv run pytest -q
```
Expected: pass.

- [ ] **Step 3: Run smoke command**

Run:
```bash
KREPORTS_RUNTIME_MODE=readonly uv run python - <<'PY'
import json
from kreports.mcp.tools import call_tool
print(call_tool('search_dataset', {'dataset':'accounting_note_chapters','year':2024,'market':'KOSPI','section_type':'policy','limit':3})[:1000])
PY
```
Expected: valid JSON. If current DB has not been backfilled yet, `data_quality.status` may be `missing`, but schema and tool must work.

- [ ] **Step 4: Commit**

Run:
```bash
git add scripts/evaluate_current_mcp_quality.py docs/disclosure-db-completeness.md
git commit -m "chore: report accounting note chapter coverage"
```
