# DB Provenance Hardening Implementation Plan

## Goal

Close the remaining database schema and receipt-provenance gaps behind the
auditor and investor MCP surfaces without mutating the live database.

## Global Constraints

- Work only in `codex/db-provenance-hardening`, based on
  `e68c01dc587408a6c12669c9c28b7d5403f7791f`.
- Do not read-write, migrate, checkpoint, delete sidecars from, or otherwise
  mutate the live `kreports.db`.
- Public/runtime behavior remains cache-first and read-only.
- A filing-backed claim is `usable` only when its receipt is a canonical
  14-digit DART receipt and is proven to belong to the requested company and
  business year annual filing.
- A malformed, foreign, ambiguous, or unproven source must fail closed as
  `limited`; do not borrow a receipt from a different company, year, filing,
  or metric.
- Preserve evidence extraction separately from audit or investor
  interpretation.
- Preserve exact units and receipt-level traceability through
  domain result -> MCP envelope -> answer-pack table and sources.
- Conflicting duplicate database rows must never be resolved by incidental
  SQLite row order. Only value-and-provenance-identical duplicates may
  deduplicate.
- Use strict TDD: add a focused behavior test, run it and record the expected
  RED failure, then implement the minimum fix and record GREEN.
- Use literal, hand-checked fixtures and assert public behavior rather than
  source text or mocks.
- Keep each task within its listed mutable paths and commit it separately.

## Task 1: Close Mechanical Release and Rehearsal Schema Drift

Model lane: Luna-high.

Mutable paths:

- `kreports/release_artifact.py`
- `tests/test_release_artifact.py`
- `tests/test_kam_backfill_rehearsal.py`

Requirements:

1. A release artifact must block when an index-target table is absent. Add the
   omitted required tables `audit_fees`, `group_entities`,
   `group_relationships`, and `group_component_metrics` to the release schema
   contract.
2. A release artifact must block when
   `financial_facts_compact` lacks the provenance fields consumed by the
   retained-clone rehearsal and materiality tool:
   `corp_code`, `bsns_year`, `fs_div`, `metric_key`, `amount`,
   `source_account_id`, `source_table`, `unit`, `period_type`,
   `citation_rcept_no`, `citation_report_nm`, `citation_basis`, and
   `quality_status`.
3. Update the retained-clone orchestration test fixture so it exercises the
   current 18-tool professional matrix and includes
   `prepare_audit_materiality_inputs`. Do not weaken production validation.
4. Add deterministic regression tests that execute the release builder or
   schema blocker behavior, not tests that grep constants.

Acceptance:

- The old 17-tool fixture fails against the production validator before the
  fix and the corrected fixture passes afterward.
- Missing required tables and missing compact provenance columns each produce
  explicit release blockers.
- Existing release and rehearsal tests remain green.

## Task 2: Fail Closed on Ambiguous Materiality Benchmark Rows

Model lane: Terra-high.

Mutable paths:

- `kreports/analysis/materiality_benchmark.py`
- `tests/test_materiality_benchmark.py`

Requirements:

1. Admit a compact fact only when its `citation_rcept_no` is canonical and its
   `citation_basis` proves a company-year annual-filing match.
2. Verify the receipt against the requested company and business year annual
   filing before a direct or derived materiality observation becomes usable.
3. Group rows by the exact series identity before observation selection.
   Conflicting duplicates must produce a bounded explicit provenance
   limitation and no numeric candidate. Value-and-provenance-identical
   duplicates may deduplicate deterministically.
4. Apply the same admission rules to both operands of derived profit before
   tax and keep both valid operand sources.
5. Preserve rejected series rows and limitations in the public envelope and
   answer pack, while withholding candidate money.

Acceptance:

- Malformed, foreign-company, wrong-year, wrong-basis, and conflicting
  duplicate fixtures cannot create a usable observation or numeric candidate.
- Identical duplicate fixtures are deterministic.
- The public MCP envelope and answer pack remain inspectable and `limited`.

## Task 3: Prove Accounting Policy Change Receipts

Model lane: Terra-high.

Mutable paths:

- `kreports/analysis/policy_changes.py`
- the corresponding MCP handler under `kreports/mcp/handlers/`
- `tests/test_policy_changes.py`
- `tests/test_mcp_contracts.py`
- `tests/test_mcp_answer_pack.py`

Requirements:

1. Normalize and validate every accounting-note chapter receipt and prove it
   belongs to the requested company and business year annual filing.
2. Preserve valid receipt evidence in the domain result, MCP envelope,
   dedicated policy-change table, and answer-pack sources.
3. If change rows exist but no row has proven filing provenance, keep the rows
   inspectable but downgrade public quality to `limited` with an explicit
   provenance limitation.
4. Never replace an invalid or foreign receipt with another year's filing.

Acceptance:

- A valid changed chapter has one proven receipt across all public boundaries.
- A malformed, wrong-company, or wrong-year receipt cannot yield `usable`.
- Existing similarity/change classification semantics remain unchanged.

## Final Review and QA

The controller performs an independent diff review after each task and a
whole-branch review at the end. Final verification includes the affected
release/rehearsal, materiality, policy, MCP envelope, and answer-pack suites,
Ruff for every changed Python file, `git diff --check`, and clean worktree
status. Live DB checks, if any, are immutable/read-only and must preserve its
SHA-256.
