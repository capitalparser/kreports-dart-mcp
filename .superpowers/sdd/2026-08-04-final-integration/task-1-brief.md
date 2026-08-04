# Task 1 — Integrate semantic peer context into DB provenance hardening

## Objective

Merge `codex/raw-storage-evidence-db` at `9b0ac2af209899ee92b91c9491a1c17c229db77c`
into `codex/final-kreports-integration`, whose starting HEAD is
`fadbc702a6c5ee1a8bf0e4170ab25c6a71b89a6f`.

## Binding requirements

- Work only in `/Users/kjun/vault/01_Projects/kreports_dart_mcp/.worktrees/final-kreports-integration`.
- Produce one explicit merge commit; do not rebase, squash, push, deploy, access the network, or modify any database/artifact/sidecar.
- Preserve the complete DB provenance, professional auditor/investor, materiality, QoE, KAM, readonly SQLite lifecycle, release/rehearsal, exact receipt, and peer note presentation behavior from the starting branch.
- Preserve and integrate the semantic evidence contracts, semantic index/workflow, source-separated context pack, explainable peer criteria, accounting-note comparison, note source index, extraction-gap audit, and actionable release guidance from the incoming branch.
- Resolve overlaps as a semantic union. Do not accept a resolution that removes starting-branch modules, tests, plans, reports, or public professional surfaces merely because they are absent on the incoming branch.
- Frozen MCP tool count remains exactly 34. Existing names and old inputs remain backward compatible; semantic/peer criteria behavior is additive.
- Public/runtime reads remain cache-first, readonly, fail-closed, and receipt-proven. No release blocker, missing cache, or textual similarity may be presented as a filing fact or accounting conclusion.
- Keep release schema/index/migration contracts and retained-clone safety at the stricter starting-branch level while adding current semantic tool/catalog evidence.
- Do not address the separately recorded chatbot blocker-token localization finding in this merge task unless it is required to resolve a direct merge conflict. It will receive its own reviewed fix.

## Required evidence

1. Record pre/post `git status`, merge base, and merge commit.
2. List every conflicted path and the resolution principle used.
3. Run the baseline suites:
   `tests/test_release_artifact.py tests/test_peer_note_presentation_comparison.py tests/test_all_tools_contract.py tests/test_mcp_catalog.py`.
4. Run the incoming semantic suites:
   `tests/test_mcp_prompts.py tests/test_semantic_workflow_docs.py tests/test_context_pack.py tests/test_mcp_workflows.py tests/test_answer_contracts.py tests/test_semantic_index.py tests/test_note_comparison.py tests/test_semantic_context_mcp.py tests/test_company_year_quality.py tests/test_quality_release_gate.py`.
5. Add focused overlap suites needed by the conflicts, then run Ruff on changed Python paths and `git diff --check`.
6. Leave the worktree clean and write the full report to
   `.superpowers/sdd/2026-08-04-final-integration/task-1-report.md`.

## Stop conditions

Stop and report `BLOCKED` rather than weakening either plan if a conflict cannot preserve both behaviors, if a test requires live DB/network access, or if the merge would mutate user data.

## Controller ruling on the 34-tool conflict

The starting branch's exact 34 public tool names govern. Preserve
`prepare_standard_audit_hours_inputs` and `prepare_audit_materiality_inputs`.
Do not register `get_semantic_company_context` or
`compare_peer_accounting_notes` as two additional top-level tools.

Preserve their implementation and user-visible capability by integrating:

- semantic company-year evidence buckets into the existing
  `get_business_overview` result and relevant workflow context, using additive
  fields and unchanged legacy defaults;
- side-by-side topic note comparison, peer-selection explanations, excerpts,
  receipt provenance, and table output into the existing
  `compare_peer_accounting_policies` tool through additive optional inputs;
- the semantic peer-context composition into the existing
  `accounting_policy_peer_review` prompt/workflow without adding a catalog
  tool.

Update incoming tests and docs to assert the consolidated 34-tool public
surface. Underlying analysis functions and typed models may remain separately
testable. This ruling resolves the structural conflict and is binding for the
merge.
