# Professional MCP Hardening Design

## Approval

The user reviewed the live Samsung Electronics accounting-note search result,
then explicitly broadened the scope to the remaining auditor and investor
surfaces and requested a remediation and enhancement plan. This document records
the approved direction before production implementation.

## Capability

KReports must answer professional auditor and investor questions through the MCP
chatbot without requiring the user to inspect raw JSON. The primary `answer`,
structured `answer_pack`, and detailed resource must expose the same status,
core facts, and filing provenance.

The hardening scope covers:

- audit fees, audit hours, and standard-audit-hours input preparation;
- audit acceptance and continuance screening;
- auditor history, opinion, opinion basis, KAM, and audit-report matters;
- financial trends, peer selection, peer risk, and multi-year benchmarks;
- investor quality signals and quality of earnings;
- DCF input readiness and DCF model availability;
- bounded release-readiness context on professional responses.

Accounting-note search remains unchanged except for shared-contract regression
coverage.

## Kickoff Contract

### Input Data

- `financials` and `financial_facts_compact` provide annual financial facts.
- `audit_fees` provides disclosed audit fee and audit-hour observations.
- `auditors` provides auditor, opinion, tenure, and filing receipt history.
- `report_sections`, `audit_matter_items`, and `audit_procedure_items` provide
  audit-report evidence.
- `accounting_policy_items` and `accounting_note_chapters` provide accounting
  policy coverage.
- `disclosure_events` and `disclosures` provide event titles, dates, types, and
  filing receipts.
- `companies` supplies public company identity and peer-selection attributes.
- Dataset manifest and company-year quality records provide release context.
- Raw DART filings remain the source of record. Local tables are cache and
  derived screening layers.

### Data Schema

Every professional result uses these conceptual fields:

```text
data_quality.status = usable | limited | missing | error
domain_verdict = tool-specific conclusion such as monitor or partial_model
data_quality.section_statuses = optional per-section coverage
confirmed_facts[] = filing-backed facts
analysis[] = explicitly labeled interpretation
next_checks[] = concrete follow-up work
answer_pack.summary.status = canonical data_quality.status
```

Facts requiring public filing support contain a valid DART receipt number or an
explicit safe public source URL. A structured value without such provenance may
remain visible as an uncitable screening value, but it cannot support `usable`.

Each section status has a typed shape:

```text
status = usable | limited | missing | error
required = true | false
applicability = applicable | not_applicable | unknown
coverage = bounded named measures
blockers = explicit insufficiency reasons
sources = validated public references
not_applicable_basis = filing-backed reason or null
```

### Source Priority

1. Receipt-linked DART filing or attached audit report.
2. Structured KReports value linked to the same company, year, FS basis, and
   source filing.
3. Structured value without filing linkage, labeled `uncitable`.
4. Derived classification or interpretation, never presented as a filing fact.

No tool may borrow another year, another company, or a generic filing link to
make an uncitable fact appear sourced.

### Business Terms

- `usable`: the requested professional screening question is answered with the
  required data and at least one valid public source for each material fact.
- `limited`: relevant data exists, but a required field, time period, source,
  denominator, or semantic extraction is incomplete.
- `missing`: no usable local evidence exists for the requested question. This
  means cache absence, not filing absence.
- `error`: the tool could not complete the request.
- `domain_verdict`: an allowlisted analytical outcome set by a tool-specific
  professional builder, separate from data availability. Examples include
  `monitor`, `stable`, `partial_model`, and `not_assessed`. The common
  normalizer never promotes an arbitrary legacy `verdict`. Allowlisting is
  per tool; an unrecognized value remains only in the raw compatibility
  payload and is not rendered.
- `data_quality.section_statuses`: nested coverage facts that do not override
  the canonical top-level status.
- `uncitable`: a cached number exists but no public filing reference can be
  resolved.
- `candidate_status`: whether historical DCF candidates can be reviewed.
- `valuation_readiness`: whether a valuation can actually be calculated.
- `standard_audit_hours_assessment`: always `not_assessed` in this phase.

### Output Shape

The display order is fixed:

1. `answer`: verdict-first Korean explanation and a 5–10-row core Markdown
   table when a table materially improves the answer.
2. `answer_pack`: complete structured tables, charts, sources, and limitations.
3. `kreports://visualization/...`: optional detailed exploration using the same
   status and source set.

The chatbot answer starts with:

```text
판정: {canonical status}
업무 결론: {domain verdict or not_assessed}
```

The answer then contains confirmed facts, labeled analysis, sources, data
limits, and next checks. It must not expose snake_case internal keys.

The envelope remains schema `1.0`. Optional `domain_verdict` and typed
`section_statuses` are additive compatibility fields: old payloads validate
without them, and existing clients may ignore them.

### Stop Conditions

- Do not mark a result `usable` because rows merely exist.
- Do not render `usable`, `limited`, and `missing` for the same result across
  answer layers.
- Do not create a DART link without a valid 14-digit receipt number.
- Do not treat an unavailable investor check as `fail`.
- Do not call an incomplete DCF candidate pack valuation-ready.
- Do not display an empty enterprise value or equity value as a model result.
- Do not present a cached event classifier as proof that a control change,
  dilution event, or governance outcome occurred.
- Do not present a standard audit hour or audit acceptance conclusion.
- Do not render unapproved legacy verdict strings such as approval, rejection,
  buy, sell, or confirmed audit-opinion language as a domain verdict.
- Do not treat generic audit-report boilerplate as an emphasis, other-matter,
  going-concern, or opinion-basis signal without classified source evidence.
- Do not modify or regenerate the live `kreports.db`.

### Done Criteria

- Every public tool returns a supported canonical status after MCP enrichment.
- Priority tools use the same canonical status in raw enriched result, answer,
  answer pack, and detailed resource.
- Priority tools always return a non-empty dedicated answer pack with required
  table IDs, material rows, and source coverage or an explicit limited-source
  blocker.
- `compare_peer_audit_fees` and `build_audit_acceptance_pack` expose the target
  company’s three-year assets, revenue, fee, and hours without mixing CFS/OFS.
- A dedicated `prepare_standard_audit_hours_inputs` tool returns three years of
  inputs and always labels the assessment `not_assessed`.
- Samsung Electronics 2023 missing fee and hours are shown as missing, not
  filled or inferred.
- Auditor history shows auditor changes, opinions, tenure, and receipt links.
- Opinion, opinion-basis, KAM, emphasis, other matter, and going concern use
  category-specific analysis and next checks.
- KAM timelines without topic, reason, or procedure coverage are `limited`.
- Acceptance uses an explicit requirement matrix. KAM remains `limited` until
  the semantic reducer proves current-period topic, reason, procedure, and
  source coverage or a filing-backed not-applicable basis.
- The acceptance matrix requires: a documented peer basis with at least five
  peers; three cited audit-effort years; required financial-risk metrics with
  at least five observations each; current and prior cited audit history;
  current cited accounting policy; semantic-complete KAM or cited
  not-applicability; and complete current audit-report classification.
- Financial snapshot and peer outputs retain the five-year rows and peer
  denominators in the chatbot/pack.
- Investor checks use `pass | fail | unknown`; unknown checks do not support a
  positive quality conclusion.
- DCF candidate usability and valuation readiness are separate. Missing WACC,
  working capital, or source facts blocks valuation readiness.
- A DCF result without enterprise value starts with `산출 불가`.
- Quality-of-earnings audit-matter counts disclose receipt count, section count,
  and de-duplication basis.
- Question-level usability remains distinct from release readiness.
- Focused, related, and full regression tests pass on Python 3.12.7.
- The current Python 3.11 KAM parser compatibility issue is reported separately
  unless fixed by an independently approved parser slice.
- Live read-only Samsung calls demonstrate the output and leave the database
  SHA-256 unchanged.

## Live Baseline

Read-only calls at commit `4b6b7fb19d679026e8e805256f905ac24edf2a20`
showed:

| Tool | Answer status | Answer-pack status | Material payload |
|---|---|---|---|
| `compare_peer_audit_fees` | limited | limited | subject and 10 peer rows |
| `estimate_audit_hours_proxy` | limited | missing | one-year proxy drivers |
| `build_audit_acceptance_pack` | limited | missing | policy, KAM, matter data |
| `compare_peer_risk_profile` | limited | missing | subject and peer benchmarks |
| `get_audit_history` | limited | missing | five years of auditor/opinion rows |
| `get_kam_lifecycle` | usable | usable | 22 timeline rows, no cited facts |
| `compare_peer_kam_topics` | usable | missing | cached KAM sections |
| `get_financial_snapshot` | limited | limited | five annual rows |
| `compare_to_industry_multi` | limited | limited | 40 metric rows, zero sources |
| `get_quality_of_earnings_pack` | usable | usable | monitor signals |
| `get_dcf_input_candidates` | usable | usable | candidates with required gaps |
| `get_investor_signals` | limited | limited | quality/risk/event details |

The existing uncommitted audit-effort candidate in the original dirty worktree
already constructs a three-year assets, revenue, fee, and hours table. Its two
recent years contain all four values, while the oldest year has assets and
revenue but no fee or hours. That candidate is useful input but not complete
because its rows do not yet carry resolved filing provenance or confirmed
facts. Exact live-company values remain verification evidence and are not
committed to documentation.

## Considered Approaches

### Chosen: canonical status kernel plus domain-specific surfaces

Normalize status once during MCP enrichment, keep domain verdicts separate, and
give each professional domain its own deterministic facts, tables, and
rendering module. This fixes the root inconsistency while preserving the public
MCP model.

The common kernel is implemented first. Auditor-effort, audit-acceptance, and
investor slices then branch from that shared commit and are integrated after
independent verification.

### Rejected: patch only missing tables

Adding tables to `answer_pack.py` would recover some numbers but would preserve
contradictory status, uncited facts, and renderer-specific judgment. It would
also leave DCF and unknown-check semantics unsafe.

### Rejected: backfill before UX correction

Backfill remains necessary for release completeness, especially audit
fee/hour provenance and older policy coverage. It must not block honest
presentation of currently available evidence. This phase exposes gaps rather
than mutating the production database.

### Rejected: harden all 32 tools with bespoke renderers

All tools receive the canonical status invariant, but bespoke presentation is
limited to professional tools with demonstrated information loss. Adding 32
dedicated renderers would create unnecessary code and review burden.

## Component Responsibilities

### `kreports.mcp.contracts`

- Normalize one canonical quality status.
- Preserve tool-specific outcomes as `domain_verdict`.
- Downgrade uncited material facts from `usable` to `limited`.
- Attach normalized quality before answer-pack or narrative generation.

### `kreports.mcp.professional_surfaces`

- Register domain-specific pack builders and detail renderers.
- Keep audit-effort, auditor, and investor presentation isolated.
- Render only the domain result; never infer a new status.

### `kreports.analysis.filing_provenance`

- Resolve same-company, same-year annual filing sources.
- Validate receipt numbers before returning a public source.
- Return `None` when provenance cannot be established.

### `kreports.analysis.audit_effort_inputs`

- Build a three-year, same-FS-basis subject table.
- Separate actual, contract, and legacy-inferred fee/hour observations.
- Compute field/source coverage without calculating standard audit hours.

### `kreports.analysis.auditor_decisions`

- Wrap existing peer calculations and build confirmed facts, section
  requirements, and blockers for acceptance and peer risk.
- Consume KAM semantic coverage rather than inferring completeness from row
  presence.
- Compose audit-effort inputs only after that slice is integrated.

### Auditor reporting modules

- Build confirmed facts and blockers for history, opinion, matters, and KAM.
- Keep evidence extraction separate from audit interpretation.
- Reject boilerplate-only acceptance signals.

### `kreports.analysis.investor_peer_evidence`

- Wrap the existing peer selector and multi-year comparator.
- Add denominators, cohort digest, facts, and source limitations without
  modifying the shared peer algorithm during parallel work.

### Investor analysis modules

- Preserve five-year financial and peer rows.
- Separate unknown from failed checks.
- Separate DCF candidate review from valuation readiness.
- Attach receipt-level audit-matter and event provenance.

### `kreports.mcp.resources` and `kreports.mcp.dispatch`

- Attach a bounded release context without changing question-level status.
- Avoid running a heavy release-gate calculation on every tool call.

## Worktree And Integration Strategy

1. Create and verify a shared status/provenance base branch.
2. Branch three Terra High implementation worktrees from that exact commit:
   - audit effort and standard-hours inputs;
   - acceptance, history, opinion, matters, and KAM;
   - financial trend, peer, investor quality, QoE, and DCF.
3. Each worktree completes its slice through synthetic public-path tests.
4. Integrate commits in dependency order, then explicitly wire the audit-effort
   helper into the public acceptance handler and pass its typed status and
   three annual rows to `auditor_decisions`.
5. Resolve only central registry changes; semantic merge conflicts are not
   deferred to integration.
6. Run cross-slice contract tests, full suite, Ruff, diff checks, and immutable
   opt-in database verification.

The original dirty worktree remains untouched. The existing uncommitted
three-year candidate is reviewed as reference and is not copied blindly.

## Error Handling

- Missing cache data is a successful `missing` result with a filing-absence
  disclaimer.
- Partial inputs or missing provenance are `limited`.
- Invalid input or tool exceptions are `error`.
- Pack generation failure cannot replace a non-empty domain result with
  `missing`; it must surface as an explicit presentation error during testing.
- Release context failure is non-fatal and represented as unavailable context.

## Testing Strategy

Tests use synthetic companies and receipt numbers. Each implementation task
starts with a public-contract failure, verifies RED, adds the smallest
production change, verifies GREEN, and commits.

Required layers:

- deterministic unit tests for status and provenance;
- temporary-database domain tests;
- public `call_tool` tests for answer/pack/resource parity;
- low-level dispatcher and stdio MCP parity tests;
- all-tool status invariant tests;
- real read-only Samsung probes gated by an explicit `KREPORTS_LIVE_DB` path
  and excluded from default pytest;
- database digest comparison before and after live probes;
- full Python 3.12.7 regression suite.

No committed fixture contains private engagement, fee, or client data.
