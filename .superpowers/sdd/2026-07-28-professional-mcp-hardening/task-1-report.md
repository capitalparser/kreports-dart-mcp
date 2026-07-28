# Task 1 report — Canonical Status Truth Kernel And Surface Registries

## TDD evidence

### RED

Command:

```bash
uv run --python 3.12.7 pytest \
  tests/test_professional_status_truth.py::test_enrichment_uses_one_canonical_status_across_layers -q
```

Observed result: `1 failed`.  The assertion for `out["domain_verdict"]` raised
`KeyError`: legacy enrichment had not attached a normalized domain verdict or
one canonical quality result before pack/prose rendering.

### GREEN

Command:

```bash
uv run --python 3.12.7 pytest \
  tests/test_professional_status_truth.py \
  tests/test_mcp_contracts.py \
  tests/test_mcp_answer_pack.py \
  tests/test_mcp_narrative_renderers.py \
  tests/test_accounting_note_answer_surface.py \
  tests/test_accounting_note_mcp_contract.py -q
```

Observed result: `73 passed` in 0.63s. Three existing SQLAlchemy
`datetime.utcnow()` deprecation warnings were emitted by the DB-backed
accounting-note fixtures; no production database was read or changed.

## Changed files

- `CONTEXT.md`
- `kreports/mcp/contracts.py`
- `kreports/mcp/answer_pack.py`
- `kreports/mcp/renderers.py`
- `kreports/mcp/visual_contracts.py`
- `kreports/mcp/professional_surfaces/__init__.py`
- `kreports/mcp/professional_surfaces/audit_effort.py`
- `kreports/mcp/professional_surfaces/auditor.py`
- `kreports/mcp/professional_surfaces/investor.py`
- `tests/test_professional_status_truth.py`
- `tests/test_mcp_contracts.py`

## Contract evidence

- `normalize_answer_result()` validates one bounded `DataQualityV1` and stores
  its status as `quality_status`; `AnswerEnvelopeV1.verdict` is now restricted
  to `usable`, `limited`, `missing`, or `error`.
- `domain_verdict` is additive in schema `1.0` and copied only for the task's
  explicit per-tool allowlists. Legacy approval, trading, and audit-opinion
  strings remain raw payload values and never become professional prose.
- Uncited confirmed facts downgrade `usable` to `limited`; missing results use
  the cache-absence disclaimer and errors remain errors.
- Typed, bounded section statuses are validated into explicit limited blockers
  when malformed and are passed unchanged to envelope, answer-pack, and
  visualization data-quality fields.
- Empty professional-surface registries merge into central dispatch. The
  accounting-note route remains ahead of generic fallback, covered by the
  focused accounting-note regression tests.
- The narrative now renders distinct canonical `판정` and optional `업무 결론`
  sections. Visualization availability preserves a supplied non-usable
  canonical status instead of rewriting a limited result as missing.

## Self-review

- Reviewed the full changed diff for status promotion, dispatch ordering, and
  section-status serialization. No unallowlisted verdict is promoted.
- `git diff --check` passed.
- No live DB access was introduced; the selected DB tests use their temporary
  fixture engine.

## Commit

`0757ab4` — `refactor: establish professional MCP status truth`

## Concerns

- The selected test run has three pre-existing SQLAlchemy `utcnow` deprecation
  warnings. They are unrelated to this status-contract change.

## Fix Round 1

### RED evidence

Each finding was reproduced before its production change:

```bash
uv run --python 3.12.7 pytest \
  tests/test_professional_status_truth.py::test_empty_upstream_usable_response_is_missing_across_response_and_pack -q
# 1 failed: quality_status was usable, expected missing

uv run --python 3.12.7 pytest \
  tests/test_professional_status_truth.py::test_allowlisted_domain_verdict_uses_public_korean_label_not_snake_case -q
# 1 failed: prose contained screen_grade

uv run --python 3.12.7 pytest \
  tests/test_professional_status_truth.py::test_enrichment_replaces_injected_professional_verdict_prose -q
# 1 failed: injected approval prose was returned unchanged

uv run --python 3.12.7 pytest \
  tests/test_mcp_contracts.py::test_direct_envelope_rejects_domain_verdict_outside_tool_allowlist -q
# 1 failed: direct model construction did not raise ValidationError
```

An additional analysis-only usable-status regression was also RED before the
payload-presence guard excluded unsupported analysis from affirmative inputs.

### GREEN evidence

Covering command:

```bash
uv run --python 3.12.7 pytest \
  tests/test_professional_status_truth.py \
  tests/test_mcp_contracts.py \
  tests/test_mcp_answer_pack.py \
  tests/test_mcp_narrative_renderers.py \
  tests/test_accounting_note_answer_surface.py \
  tests/test_accounting_note_mcp_contract.py -q
```

Output: `80 passed, 3 warnings in 0.62s`. The same three existing SQLAlchemy
`datetime.utcnow()` deprecation warnings occurred in temporary-fixture
accounting-note tests. `git diff --check` passed.

### Changed files

- `kreports/mcp/contracts.py`
- `kreports/mcp/renderers.py`
- `tests/test_professional_status_truth.py`
- `tests/test_mcp_contracts.py`

### Resolution evidence

- Empty usable payloads and analysis-only payloads now normalize to missing
  before any answer pack/resource is built, keeping response and pack status
  aligned.
- Allowlisted domain conclusion codes are rendered only through tool-aware
  Korean labels; the three DCF values named in the finding are absent from
  prose.
- Enrichment always regenerates the public answer from the normalized envelope,
  so injected approval, trading, and audit-opinion prose cannot bypass it.
- `AnswerEnvelopeV1` has an after-model validator that rejects a non-null
  `domain_verdict` outside the allowlist for its own `tool_name`.

### Commit

`41d762c` — `fix: harden professional MCP status rendering`

### Concerns

- No new concern. The only test warnings remain the unrelated SQLAlchemy
  deprecation warnings above.

## Fix Round 2

### RED evidence

```bash
uv run --python 3.12.7 pytest \
  tests/test_professional_status_truth.py::test_arbitrary_metadata_list_cannot_keep_upstream_usable_status -q
# 1 failed: labels=['x'] retained quality_status usable, expected missing

uv run --python 3.12.7 pytest \
  tests/test_professional_status_truth.py::test_renderer_empty_result_replaces_injected_raw_answer -q
# 2 failed: raw approval/trading/audit-opinion prose remained as answer
```

The renderer-failure fallback is separately covered by
`test_renderer_failure_uses_nonempty_canonical_fallback`.

### GREEN evidence

```bash
uv run --python 3.12.7 pytest \
  tests/test_professional_status_truth.py \
  tests/test_mcp_contracts.py \
  tests/test_mcp_answer_pack.py \
  tests/test_mcp_narrative_renderers.py \
  tests/test_accounting_note_answer_surface.py \
  tests/test_accounting_note_mcp_contract.py -q
```

Output: `85 passed, 3 warnings in 0.74s`; `git diff --check` passed. The three
warnings are the existing SQLAlchemy `datetime.utcnow()` deprecations from
temporary accounting-note fixtures.

### Changed files

- `kreports/mcp/contracts.py`
- `tests/test_professional_status_truth.py`

### Resolution evidence

- Status evidence now uses explicit registered list, mapping, and count fields
  only. Arbitrary metadata lists, mappings, and strings cannot justify usable;
  the test verifies normalized response, envelope, answer pack, and published
  visualization resource all show missing. A subsidiary-record positive case
  remains usable.
- Enrichment removes the raw answer before rendering. Empty, null, or thrown
  renderer results receive a nonempty Korean fallback beginning with `판정:`.

### Commit

`a30f2fe` — `fix: constrain professional status evidence`

### Concerns

- No new concern beyond the existing fixture-only SQLAlchemy deprecation
  warnings.

## Fix Round 3

### RED evidence

```bash
uv run --python 3.12.7 pytest \
  tests/test_professional_status_truth.py::test_generic_payload_keys_cannot_keep_unknown_or_unrelated_tool_usable \
  tests/test_professional_status_truth.py::test_other_tools_registered_key_cannot_be_used_by_this_tool \
  tests/test_professional_status_truth.py::test_tool_registered_purpose_payloads_remain_usable -q
```

Output: `6 failed, 3 passed`. The four generic fields (`items`, `inputs`,
`results`, and `assumptions`) and a cross-tool DCF field kept an unrelated
business-overview result usable. The initial QoE positive fixture also exposed
the real catalog output name (`metrics`), which was corrected before GREEN.

### GREEN evidence

```bash
uv run --python 3.12.7 pytest \
  tests/test_professional_status_truth.py \
  tests/test_mcp_contracts.py \
  tests/test_mcp_answer_pack.py \
  tests/test_mcp_narrative_renderers.py \
  tests/test_accounting_note_answer_surface.py \
  tests/test_accounting_note_mcp_contract.py -q
```

Output: `95 passed, 3 warnings in 0.84s`; `git diff --check` passed. Warnings
remain the existing SQLAlchemy `datetime.utcnow()` deprecations in temporary
accounting-note fixtures.

### Changed files

- `kreports/mcp/contracts.py`
- `tests/test_professional_status_truth.py`

### Resolution evidence

- `_TOOL_PURPOSE_FIELDS` is a tool-aware registry with an entry for every one
  of the 32 names in `TOOL_CATALOG`, asserted exact by test. It was populated
  from catalog descriptions and the handler/analysis result shapes, rather
  than a global field-name allowlist.
- The detector now accepts only the current tool's registered list, mapping,
  and count fields. Unknown tools and cross-tool fields cannot retain usable.
- `confirmed_facts` remain a dedicated path: a fact is affirmative but an
  unresolvable source is downgraded to limited by the existing evidence-gap
  check, so it cannot retain usable without a public source.
- Generic-key tests assert response/envelope/pack/published-resource missing
  parity; representative auditor, QoE, DCF, and note-search payloads remain
  usable.

### Commit

`b84ae4c` — `fix: scope professional payload evidence by tool`

### Concerns

- The registry intentionally defaults unknown tools to no affirmative payload.
  A future public catalog tool must add its audited output fields here, and the
  exact catalog-parity test makes that omission visible.

## Fix Round 4

### RED evidence

```bash
uv run --python 3.12.7 pytest \
  tests/test_professional_status_truth.py::test_cited_cross_tool_fact_cannot_make_business_overview_usable \
  tests/test_professional_status_truth.py::test_peer_selection_metadata_without_returned_peers_is_missing \
  tests/test_professional_status_truth.py::test_multi_industry_cohort_metadata_without_results_is_missing \
  tests/test_professional_status_truth.py::test_auditor_and_investor_no_data_shapes_cannot_keep_usable -q
```

Output: `5 failed`. A cited DCF fact kept `get_business_overview` usable;
`selection_policy`, `cohort_metadata`, audit-fee zero-count metadata, and an
empty investor snapshot likewise retained an upstream `usable` claim.

### GREEN evidence

```bash
uv run --python 3.12.7 pytest \
  tests/test_professional_status_truth.py \
  tests/test_mcp_contracts.py \
  tests/test_mcp_answer_pack.py \
  tests/test_mcp_narrative_renderers.py \
  tests/test_accounting_note_answer_surface.py \
  tests/test_accounting_note_mcp_contract.py -q
```

Output: `100 passed, 3 warnings in 0.61s`. The warnings are existing
SQLAlchemy `datetime.utcnow()` deprecations from temporary accounting-note
fixtures. No production database was read or changed.

```bash
uv run --python 3.12.7 ruff check \
  kreports/mcp/contracts.py kreports/mcp/answer_pack.py \
  tests/test_professional_status_truth.py tests/test_mcp_contracts.py \
  tests/test_mcp_answer_pack.py
git diff --check
```

Both checks passed.

### Resolution evidence

- The generic field-presence registry is replaced by a predicate for each
  public catalog tool, audited against handler result and no-data shapes.
  Predicates require real rows, positive relevant counts, or a sufficient
  domain object; unknown tools fail closed.
- `confirmed_facts` no longer establish purpose evidence. A cited DCF fact
  cannot make a business-overview response usable; a business section with
  substantive text can. The regression checks normalized response, envelope,
  answer-pack status, and the published visualization resource.
- `select_peer_group` accepts returned peers or a positive
  `returned_peer_count` only. `compare_to_industry_multi` accepts non-empty
  `results` only; selection policy and cohort descriptors are never evidence.
- Missing normalized results now produce an empty availability pack. This
  avoids legacy cited-fact or metadata tables carrying rows under `missing`
  status and preserves raw/envelope/pack/resource parity.
- Auditor and investor no-data branches remain missing; representative real
  purpose results and existing note-search behavior retain their prior paths.

### Self-review

- Re-read every predicate against its current handler/analysis output,
  including peer benchmark no-data branches. `peer_benchmarks.py` was read but
  not modified.
- Reviewed the changed diff for cross-tool promotion, status parity, and
  unrelated staging. Only Task 1 contracts, answer-pack code, and scoped tests
  are in the implementation commit.

### Commit

`e23264f` — `fix: require purpose-bound MCP status evidence`

### Concerns

- The three fixture-only SQLAlchemy deprecation warnings remain. Future public
  tools must add an audited predicate; the exact catalog-parity test exposes a
  missing registration.
