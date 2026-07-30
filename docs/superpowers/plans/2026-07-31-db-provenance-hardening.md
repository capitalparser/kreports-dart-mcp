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
- `kreports/mcp/professional_surfaces/audit_effort.py`
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
- `kreports/mcp/handlers/auditor.py`
- `kreports/mcp/answer_pack.py`
- `tests/test_policy_changes.py`
- `tests/test_mcp_contracts.py`
- `tests/test_mcp_answer_pack.py`
- `tests/test_mcp_live_output_evidence.py`

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

## Task 4: Expose Materiality Conflict Provenance

Model lane: Luna-high.

Mutable paths:

- `kreports/analysis/materiality_benchmark.py`
- `kreports/mcp/professional_surfaces/audit_effort.py`
- `tests/test_materiality_benchmark.py`

Requirements:

1. Preserve `source_account_id` and `source_table` in bounded rejected-row
   diagnostics and the chatbot answer-pack table.
2. Continue withholding every rejected amount.

## Task 5: Unify Policy Chapter Schema Contracts

Model lane: Terra-high.

Requirements:

1. Use one exact table, column, and index-definition contract for release and
   retained-clone rehearsal.
2. Contract the full `accounting_note_chapters` ORM shape and deterministic
   identity indexes through an idempotent, concurrent-safe SQLite migration.
3. Treat duplicate logical chapters as a fail-closed migration precondition;
   never choose or delete a row automatically.
4. Measure policy-change readiness from at least two receipt-proven comparable
   annual chapter years, separately from policy-item readiness.

## Task 6: Prove Every QoE Financial Year

Model lane: Terra-high.

Requirements:

1. Admit a QoE year only when all required metrics have finite values,
   compatible explicit units and duration periods, usable quality, and exact
   company-year annual-filing receipts.
2. Reject conflicting duplicates, incomplete metric sets, contaminated
   receipts, and legacy rows without provenance columns.
3. Keep unproven years inspectable without money-backed signals or conclusions.
4. Preserve every proven year's receipt in the public provenance table and
   answer-pack sources even when the overall result is limited.

## Task 7: Gate Auditor Materiality Readiness

Model lane: Terra-high.

Requirements:

1. Measure three-year materiality support over the full declared listed-company
   denominator with the same direct and derived PBT proof semantics as runtime.
2. Reject conflicting duplicates, nonnumeric amounts, non-latest or unproven
   receipts, incompatible units/periods, and cross-series year borrowing.
3. Degrade public runtime when coverage is below threshold and block
   `auditor_full` with `materiality_benchmark_coverage`.
4. Preserve metric policy, denominators, and exclusion counts through the
   release report, artifact, CLI, and retained-clone evidence.

## Task 8: Explainable, Customizable Peer Note Presentation Comparison

Model lane: Terra-high.

Requirements:

1. Extend the existing peer-accounting-policy comparison without increasing
   the frozen MCP tool count. Preserve deterministic backward-compatible
   defaults while accepting an optional note topic, auditor/investor/balanced
   selection profile, bounded criterion weights, and explicit peer
   include/exclude overrides.
2. Separate the initial industry/business/market candidate universe from the
   final peer set. For every candidate expose inclusion status, selection
   basis, component scores actually supported by cached data, data year and
   FS, missing-data limitations, and whether the result came from defaults or
   user customization. Never fabricate an unavailable financial criterion.
3. Compare the subject and final peers on the same note/policy topic with
   heading, note placement, bounded body excerpt, and exact latest annual
   filing receipt proof. A missing cache row means
   `cache_missing_not_filing_absence`; it must never be described as a missing
   disclosure.
4. Return chatbot-ready peer-selection, note-presentation, and topic-coverage
   tables. Only exactly proven receipts may create top-level DART source
   links. Textual similarity is a screening signal and must not be described
   as an accounting-treatment conclusion.
5. Cover defaults, custom profiles/weights, include/exclude validation,
   missing financial dimensions, contaminated/foreign/older receipts,
   missing note rows, bounded excerpts, stable order, the MCP
   dispatch-envelope-answer-pack path, and the frozen tool count with strict
   RED-to-GREEN tests.

## Final Review and QA

The controller performs an independent diff review after each task and a
whole-branch review at the end. Final verification includes the affected
release/rehearsal, materiality, policy, QoE, peer-selection, note-comparison,
MCP envelope, and answer-pack suites, Ruff for every changed Python file,
`git diff --check`, and clean worktree status. Live DB checks, if any, are
immutable/read-only and must preserve its SHA-256.
