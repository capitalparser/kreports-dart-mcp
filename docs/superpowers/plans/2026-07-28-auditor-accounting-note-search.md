# Auditor Accounting Note Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `search_dataset(accounting_note_chapters)` return keyword-relevant, filing-grounded, auditor-usable chatbot answers with one consistent status and structured table.

**Architecture:** The analysis adapter owns bounded keyword extraction. The MCP search handler turns extracted passages into filing facts, deterministic audit implications, and fail-closed quality semantics. The renderer and answer-pack builder present that shared structure without independently inferring availability.

**Tech Stack:** Python 3.11+, SQLAlchemy, Pydantic v2, pytest, existing KReports MCP dispatcher and immutable SQLite runtime.

## Global Constraints

- Keep the public `search_dataset` input interface backward compatible.
- Keep extraction, audit interpretation, and presentation in separate modules.
- A `usable` result requires at least one relevant passage and public DART receipt reference.
- `missing` means local-cache absence, never confirmed filing absence.
- Do not infer balances, exposures, or audit conclusions from policy text.
- Use synthetic test companies and receipt numbers in committed fixtures.
- Do not modify or regenerate the live `kreports.db`.

---

### Task 1: Keyword-Centered Note Evidence

**Files:**
- Modify: `kreports/analysis/search_adapter.py`
- Create: `tests/test_accounting_note_search_adapter.py`

**Interfaces:**
- Consumes: `search_dataset(dataset="accounting_note_chapters", keyword=str, ...)`
- Produces: each matching record retains `body_excerpt: str` and adds `match_excerpts: list[str]`, where excerpts are normalized, bounded, contain the requested keyword, and are de-duplicated.

- [ ] **Step 1: Write failing adapter tests**

Create a synthetic `AccountingNoteChapter` whose keyword occurs after character
1,200. Assert that `body_excerpt` contains the keyword rather than the beginning
of the body. Add a second test with repeated keyword occurrences and assert
`1 <= len(match_excerpts) <= 3`, every excerpt contains the literal keyword, and
duplicate windows are removed.

- [ ] **Step 2: Verify RED**

Run:

```bash
uv run pytest tests/test_accounting_note_search_adapter.py -q
```

Expected: failure because the current adapter returns `body[:1200]` and has no
`match_excerpts`.

- [ ] **Step 3: Implement bounded extraction**

Add a deterministic private helper with this contract:

```python
def _keyword_centered_excerpts(
    body: str,
    keyword: str,
    *,
    limit: int = 3,
    context_chars: int = 320,
) -> list[str]:
    """Return normalized, de-duplicated windows centered on literal matches."""
```

Use sentence or clause boundaries when they fall inside the bounded window.
Never return the unrelated beginning of a body for a keyword query. In the
accounting-note branch, populate `match_excerpts` and set `body_excerpt` to the
first match passage. Preserve the old first-1,200-character behavior for
non-keyword searches and other datasets.

- [ ] **Step 4: Verify GREEN and local regression**

Run:

```bash
uv run pytest tests/test_accounting_note_search_adapter.py tests/test_auditor_peer_tools.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add kreports/analysis/search_adapter.py tests/test_accounting_note_search_adapter.py
git commit -m "feat: return keyword-centered note evidence"
```

---

### Task 2: Auditor-Facing MCP Answer Contract

**Files:**
- Modify: `kreports/mcp/handlers/search.py`
- Modify: `kreports/mcp/answer_pack.py`
- Modify: `kreports/mcp/renderers.py`
- Create: `tests/test_accounting_note_answer_surface.py`

**Interfaces:**
- Consumes: accounting-note records with `match_excerpts`, `body_excerpt`,
  `note_no`, `note_title`, `year`, `fs_div`, and `rcept_no`.
- Produces: `confirmed_facts`, auditor `analysis`, `next_checks`, shared
  `data_quality.status`, and an `answer_pack` table with id
  `accounting_note_evidence`.

- [ ] **Step 1: Write failing handler tests**

Exercise a deterministic helper against a literal result containing one matched
note passage. Assert:

```python
assert result["data_quality"]["status"] == "usable"
assert result["confirmed_facts"][0]["excerpt"] == matched_excerpt
assert result["confirmed_facts"][0]["source"]["rcept_no"] == "20250312000001"
assert result["analysis"][0]["perspective"] == "auditor"
```

Add a malformed matched-row case without a receipt number and assert `limited`.
Add an empty-company case and assert `missing` plus wording that cache absence
does not establish filing absence.

- [ ] **Step 2: Verify RED**

Run:

```bash
uv run pytest tests/test_accounting_note_answer_surface.py -q
```

Expected: failure because note searches currently contain no confirmed facts,
audit analysis, or dedicated pack.

- [ ] **Step 3: Implement handler enrichment**

Add one private enrichment function in `handlers/search.py` and call it only
when `dataset == "accounting_note_chapters"`. It must:

- create one filing fact per de-duplicated record passage,
- bind company, year, note title, receipt number, and `source_table`,
- add deterministic auditor screening guidance for revenue, inventory,
  provisions, estimates, impairment, and contingencies,
- set `usable` only when at least one passage has a valid 14-digit receipt,
- set `limited` for matched but uncitable/unrenderable rows,
- keep empty results `missing`,
- add next checks for balances, comparative amounts, estimation inputs, and
  full-note review.

- [ ] **Step 4: Implement the dedicated answer pack**

Route `search_dataset` through a builder that delegates non-note datasets to the
generic pack and builds this note table:

```text
topic | year | fs_div | note_reference | confirmed_statement |
matched_excerpt | audit_implication | rcept_no
```

Use the same `data_quality.status` and collected DART sources as the answer
envelope.

- [ ] **Step 5: Render the compact table in chatbot fallback**

Permit `search_dataset` in the existing visual-table fallback only when the
dedicated note table exists. Keep the professional narrative before the table
and avoid repeating the full passage in both detail and table.

- [ ] **Step 6: Verify GREEN and local regression**

Run:

```bash
uv run pytest tests/test_accounting_note_answer_surface.py tests/test_mcp_answer_pack.py tests/test_mcp_narrative_responses.py -q
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit**

```bash
git add kreports/mcp/handlers/search.py kreports/mcp/answer_pack.py kreports/mcp/renderers.py tests/test_accounting_note_answer_surface.py
git commit -m "feat: add auditor note search answers"
```

---

### Task 3: Public-Path Acceptance Contract

**Files:**
- Create: `tests/test_accounting_note_mcp_contract.py`

**Interfaces:**
- Consumes: the public `call_tool("search_dataset", arguments)` path.
- Produces: black-box regression evidence for the combined adapter and MCP
  surface.

- [ ] **Step 1: Write public-path failing tests**

Using the real temporary database engine and synthetic company:

- store inventory policy text beyond character 1,200,
- call the public tool with `keyword="재고자산"`,
- assert the chatbot answer contains `평균법`, `순실현가능가치`, the receipt
  number, and a DART link,
- assert raw, narrative, and answer-pack statuses are all `usable`,
- assert the note evidence table has at least one row.

For `keyword="우발"` with no match, assert:

- status is `missing`,
- the answer says the local cache has no matching evidence,
- the answer does not say the filing has no contingencies.

- [ ] **Step 2: Verify RED**

Run:

```bash
uv run pytest tests/test_accounting_note_mcp_contract.py -q
```

Expected: the baseline fails on irrelevant excerpts, absent facts, and missing
pack rows.

- [ ] **Step 3: Commit the independent acceptance tests**

```bash
git add tests/test_accounting_note_mcp_contract.py
git commit -m "test: define auditor note search MCP contract"
```

The integration owner must not weaken these assertions to fit implementation.

---

### Task 4: Integration and Live Verification

**Files:**
- Integrate the commits from Tasks 1–3 in an isolated integration worktree.
- Resolve only semantic conflicts in the listed production/test files.

**Interfaces:**
- Consumes: Task 1 record contract, Task 2 answer contract, Task 3 black-box
  acceptance contract.
- Produces: one integrated branch ready for transfer to the user's active
  worktree.

- [ ] **Step 1: Cherry-pick the three reviewed commits**

Cherry-pick Task 1, Task 2, and Task 3 commits in that order. Review every
conflict against the design spec rather than choosing either side wholesale.

- [ ] **Step 2: Run focused tests**

```bash
uv run pytest \
  tests/test_accounting_note_search_adapter.py \
  tests/test_accounting_note_answer_surface.py \
  tests/test_accounting_note_mcp_contract.py -q
```

- [ ] **Step 3: Run related MCP regression**

```bash
uv run pytest \
  tests/test_auditor_peer_tools.py \
  tests/test_mcp_answer_pack.py \
  tests/test_mcp_narrative_responses.py \
  tests/test_mcp_contracts.py -q
```

- [ ] **Step 4: Run full verification**

```bash
uv run pytest -q
uv run ruff check kreports/analysis/search_adapter.py kreports/mcp/handlers/search.py kreports/mcp/answer_pack.py kreports/mcp/renderers.py tests/test_accounting_note_*.py
git diff --check 170c7a1..HEAD
```

- [ ] **Step 5: Run immutable live probes**

Bind the existing `kreports.db` through `_bound_explicit_runtime` and call
Samsung Electronics 2025 CFS searches for `수익`, `재고자산`, `충당부채`, and
`우발`. Record verdict, confirmed facts, table ids, excerpt content, receipt
number, and database checksum before/after.

- [ ] **Step 6: Independent review and final handoff**

Review the integrated diff for overclaim, provenance borrowing, status
divergence, duplicate prose, and accidental database mutation. Transfer the
integrated commits without overwriting unrelated dirty files in the user's
active worktree.
