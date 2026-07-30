# Task 3 — Prove Accounting Policy Change Receipts

## Scope

- Base at implementation start: `5879bdddea9814af7e4c0661d784683549a982eb`
- Changed tracked files only:
  - `kreports/analysis/policy_changes.py`
  - `kreports/mcp/handlers/auditor.py`
  - `kreports/mcp/answer_pack.py`
  - `tests/test_policy_changes.py`
  - `tests/test_mcp_contracts.py`
  - `tests/test_mcp_answer_pack.py`
  - `tests/test_mcp_live_output_evidence.py`
- No live database was opened or mutated.

## RED

Before production edits, added literal public-behavior regressions and ran:

```sh
UV_CACHE_DIR=/tmp/kreports-db-hardening-uv-cache \
  uv run --extra dev python -m pytest tests/test_policy_changes.py -q
```

Result: `2 failed, 1 passed`.

- A changed chapter had no `provenance_status` or filing source despite a
  matching annual report disclosure.
- Malformed, foreign-company, and wrong-business-year receipts had no
  provenance classification or explicit limited-quality result.

Then added MCP handler and answer-pack regressions and ran:

```sh
UV_CACHE_DIR=/tmp/kreports-db-hardening-uv-cache \
  uv run --extra dev python -m pytest \
    tests/test_mcp_contracts.py::test_policy_change_handler_exposes_only_proven_chapter_receipt_as_confirmed_fact \
    tests/test_mcp_answer_pack.py::test_policy_change_pack_keeps_proven_receipt_in_table_and_sources_only -q
```

Result: `2 failed`.

- A syntactically valid but unproven receipt was incorrectly promoted to a
  confirmed MCP fact.
- The dedicated `accounting_policy_changes` table did not expose receipt
  verification status.

## GREEN

- Added a note-chapter-specific annual disclosure resolver. It only considers
  the requested company and each chapter's own business year, then accepts a
  chapter receipt only when its canonical value exactly matches that annual
  filing. It never substitutes a different receipt.
- Added `provenance_status` and a receipt-level `filing_source` to every
  chapter row. Valid rows preserve the company, year, FS division, report
  name, receipt, and note section; malformed/foreign/wrong-year rows retain
  their inspectable chapter row but no filing source.
- Downgraded result quality to `limited` whenever any cached chapter row is
  unproven, with an explicit Korean provenance limitation. A result with
  chapter rows but no proven annual filing is therefore never usable.
- Promoted only `proven_annual_filing` rows into MCP `confirmed_facts` and
  evidence. Rows without explicit provenance metadata remain inspectable but
  cannot become confirmed facts or answer-pack sources.
- Added `접수번호 검증` to the policy-change answer-pack table and retained all
  changed rows for inspection. Only confirmed facts feed answer-pack sources,
  so invalid receipts are not cited.
- Review remediation: removed the legacy handler path that promoted any
  syntactically valid 14-digit receipt. Confirmed facts now require both
  `proven_annual_filing` and a matching `filing_source`; the disclosure date
  must also equal the receipt's `YYYYMMDD` prefix.

Validated with:

```sh
UV_CACHE_DIR=/tmp/kreports-db-hardening-uv-cache \
  uv run --extra dev python -m pytest \
    tests/test_policy_changes.py \
    tests/test_mcp_contracts.py \
    tests/test_mcp_answer_pack.py \
    tests/test_mcp_live_output_evidence.py -q
UV_CACHE_DIR=/tmp/kreports-db-hardening-uv-cache \
  uv run --extra dev ruff check \
    kreports/analysis/policy_changes.py \
    kreports/mcp/handlers/auditor.py \
    kreports/mcp/answer_pack.py \
    tests/test_policy_changes.py \
    tests/test_mcp_contracts.py \
    tests/test_mcp_answer_pack.py
git diff --check
```

Results: `67 passed`; scoped Ruff and diff check clean.

The broader 255-test selection containing real-cache narrative and auditor
tests ran with `23 failed, 232 passed`; every failure was an existing local
environment/data precondition (`sqlite3.OperationalError: no such table:
companies`) after the live cache was intentionally not opened. The isolated
policy/MCP suites above are the task-relevant verification evidence.

## Self-review

- The exact receipt comparison is per row and per business year; no newest
  filing, same-company alternative, or other-year receipt can be borrowed.
- Classification remains unchanged: `new`, `stable`, and `changed` continue
  to use the original hash/similarity behavior.
- A valid changed chapter retains the same receipt through domain result,
  handler confirmed fact, MCP envelope evidence, answer-pack table, and
  answer-pack sources.

## Whole-branch review remediation — canonical raw receipt exactness

### RED

Added literal fixtures for a contaminated chapter receipt and a contaminated
disclosure receipt, then ran:

```sh
UV_CACHE_DIR=/tmp/kreports-db-hardening-uv-cache \
  uv run --extra dev python -m pytest \
    tests/test_policy_changes.py::test_accounting_policy_changes_rejects_contaminated_chapter_receipt \
    tests/test_policy_changes.py::test_accounting_policy_changes_rejects_contaminated_disclosure_receipt -q
```

Result: `2 failed`. `valid_annual_filing_receipt` extracted a parent receipt
from `synthetic-20250301000001-attachment`, allowing both contaminated source
positions to participate in annual-filing proof.

### GREEN

- Disclosure admission now requires its trimmed raw receipt to equal the
  canonical 14-digit receipt returned by the shared validator.
- Chapter admission applies the same exactness rule. A contaminated chapter
  retains its raw identifier for inspection, is classified
  `invalid_receipt`, and has no filing source.
- A contaminated disclosure cannot prove a plain chapter receipt; the chapter
  remains `unproven_annual_filing`, limited, and source-free.

Validated with:

```sh
UV_CACHE_DIR=/tmp/kreports-db-hardening-uv-cache \
  uv run --extra dev python -m pytest \
    tests/test_policy_changes.py tests/test_mcp_contracts.py \
    tests/test_mcp_answer_pack.py tests/test_mcp_live_output_evidence.py -q
UV_CACHE_DIR=/tmp/kreports-db-hardening-uv-cache \
  uv run --extra dev ruff check \
    kreports/analysis/policy_changes.py tests/test_policy_changes.py
git diff --check
```

Results: `69 passed`; Ruff and diff check clean.
