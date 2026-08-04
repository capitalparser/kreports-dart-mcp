# Task 1 report — semantic peer context + DB provenance integration

## Scope and safety

- Worktree: `/Users/kjun/vault/01_Projects/kreports_dart_mcp/.worktrees/final-kreports-integration`
- Starting HEAD: `fadbc702a6c5ee1a8bf0e4170ab25c6a71b89a6f`
- Incoming exact head: `9b0ac2af209899ee92b91c9491a1c17c229db77c`
- Merge base: `6197b6eef79875e6b418c45808a177bc6556ef79`
- The merge used `git merge --no-commit --no-ff` with the exact incoming SHA.
- No database, SQLite sidecar, release artifact, network, or remote operation was performed. The local Python virtualenv was rebuilt from the existing offline uv cache only to make the required test dependency available.

## Git evidence

Pre-merge `git status --short` contained only the task-local untracked
`.superpowers/sdd/2026-08-04-final-integration/` directory. The merge had 14
content conflicts. One explicit merge commit has the requested starting and
incoming heads as parents; its final SHA is recorded in the task handoff.
Post-commit `git status --short` is empty.

## Conflict resolutions

1. `README.md` — kept the 34-tool documentation and replaced the two incoming
   standalone semantic names with the controller-approved existing tool names.
2. `docs/data-contract.md` — kept the 34-tool contract wording.
3. `kreports/mcp/answer_pack.py` — retained the starting professional pack
   builders and provenance rows; added a bounded side-by-side topic-note table
   and receipt sources when the existing policy tool requests note comparison.
4. `kreports/mcp/catalog.py` — retained the starting exact 34 public names,
   expanded `get_business_overview` and `compare_peer_accounting_policies`
   descriptions, and omitted standalone semantic registrations.
5. `kreports/mcp/handlers/auditor.py` — retained starting professional wrappers
   and added `note_comparison` to the existing policy handler behind additive
   optional inputs.
6. `kreports/mcp/input_models.py` — retained peer selector/model safeguards and
   added optional semantic context and note-comparison parameters to existing
   typed inputs.
7. `kreports/mcp/renderers.py` — retained starting audit-hours presentation.
8. `kreports/release_artifact.py` — retained strict 34-tool release contracts
   and recomputed the consolidated wire hash:
   `b723c76295f1ea66cce904ff64bd0e2eaf4f6e063b5477158718782290df0cdc`.
9. `tests/test_all_tools_contract.py` — retained the 34-tool assertion and
   contract-derived hash assertion.
10. `tests/test_dart_mcp.py` — documented the consolidated semantic behavior.
11. `tests/test_dcf_model_tool.py` — retained the 34-tool catalog assertion.
12. `tests/test_mcp_answer_pack.py` — retained the starting professional
   presentation assertions and added consolidated note-table coverage.
13. `tests/test_mcp_catalog.py` — retained the starting names/order and updated
   the generated consolidated wire hash.
14. `tests/test_release_artifact.py` — retained strict frozen-count references
   and current 34-tool release-gate fixtures.

Additionally, the controller ruling is implemented as follows:

- `get_business_overview` retains legacy defaults and gains
  `include_semantic_context`, `semantic_topics`, and `note_topics` optional
  fields. When requested, it exposes the local read-only company-year context.
- `compare_peer_accounting_policies` retains its legacy inputs and gains
  `include_note_comparison`, `note_topics`, pagination, and peer-criteria
  options. It adds source-separated side-by-side note excerpts and provenance
  only when requested.
- `accounting_policy_peer_review` now composes the retained public tools with
  these additive options; no new top-level MCP tool is registered.
- Underlying semantic analysis functions/models remain separately testable.

## Verification

Required baseline + semantic suites:

```text
275 passed in 9.95s
```

Focused overlap suites:

```text
tests/test_mcp_answer_pack.py tests/test_mcp_narrative_responses.py
59 passed in 0.49s
```

Ruff on all changed Python paths: passed.

`git diff --cached --check`: passed.

## Notes

The initial `uv run pytest` invocation used a stale Python 3.12 entrypoint
against a Python 3.11 environment and failed to import `mcp`. An offline-only
rebuild from the already present lock/cache followed by `uv run python -m
pytest` resolved the test environment mismatch; no package download or network
access occurred.

## Fix round 1/5 — semantic evidence, schema references, and shared cohort

### RED evidence

The focused pre-fix run produced six expected failures in the provenance and
schema cases: an unbound context excerpt was promoted to `dart_filing`; a
wrong-company `source_documents` row made a semantic section `available`; an
unbound peer note remained `available` and retained its receipt; the answer
pack built a DART link from that row; and both public peer-criteria schemas
contained dangling `#/$defs/PeerCriteriaProfile` references. The separately
run cohort RED test also showed that a `custom_codes` criteria of `00000003`
left policy selection on `00000002`.

### Fix

- Added one canonical source-binding check: an exact 14-digit receipt must
  bind a same-company, same-business-year `source_documents` row and an
  exact-date `사업보고서 (YYYY.*)` disclosure before it is marked
  `proven_annual_filing`.
- Semantic sections, note chapters, and derived evidence retain unproven cache
  text as `summary_only` with `cached_rcept_no`, while clearing public
  `rcept_no`; context-pack DART records require the explicit canonical binding.
- Peer note comparison uses the same binding and answer-pack source collection
  now accepts its rows only when that proof is present.
- Inlined local Pydantic `$defs` references before producing legacy public MCP
  schemas; regenerated the frozen 34-tool wire hash to
  `96b586b92a681f4641411b0ac86f190b7f152e4da5382c0d1b76a644967db72d`.
- Applied `peer_criteria` to policy selection and passed the final selected
  policy cohort (including explicit include/exclude outcomes) into the
  optional note-comparison section.

### GREEN evidence

Focused provenance, MCP-contract, schema, answer-contract, and consolidated
peer tests:

```text
144 passed in 1.28s
```

`git diff --check` and Ruff on every changed Python file passed.

### Full-suite boundary

An unscoped `uv run python -m pytest -q` does not currently provide a clean
repository-wide signal: it first stops at the existing base-golden parity test
(`tests/test_analysis_facade_parity.py`), whose expected base snapshot differs
from the already integrated peer-policy/procedure output. With that test
ignored, the next failure is `tests/test_audit_landscape.py` attempting to use
an uninitialized default SQLite engine (`no such table: companies`). The
focused tests above use the isolated `temp_engine` fixture and are the valid
evidence for this no-network, no-artifact-change fix.
