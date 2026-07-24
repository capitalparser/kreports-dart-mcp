# Provenance and requested-year peer consistency

## Scope

- Bound annual-report provenance to the requested fiscal year, including non-December year ends.
- Keep the professional answer envelope from calling unlinked confirmed facts `usable`.
- Resolve and reuse requested-year peer cohorts across peer comparison tools and the acceptance pack.
- Kept the public MCP tool registry and input schemas unchanged. The added `year` and `_peer_group` parameters are analysis-internal only.

## Red evidence

The focused tests were run against detached pre-task `HEAD` `0beefa87c3d93b5a0cfbee0cef578964338c739e` in `/tmp/kreports-provenance-red-check`:

```text
pytest -q tests/test_provenance_peer_red.py
3 failed

test_requested_annual_report_never_borrows_later_receipt:
  assert '20260318001234' is None

test_requested_annual_report_matches_non_december_year_end:
  assert '20260318001234' == '20230318001234'

test_contract_downgrades_unlinked_confirmed_fact:
  assert 'usable' == 'limited'
```

This established that the previous implementation borrowed the latest 2025 annual-report receipt for requested 2022 data, failed to select `사업보고서 (2022.03)` in the presence of a later report, and allowed fact-without-link responses to be `usable`.

## Green evidence

```text
uv run pytest -q \
  tests/test_api_evidence_packs.py \
  tests/test_peer_selection.py \
  tests/test_auditor_peer_tools.py \
  tests/test_mcp_contracts.py \
  tests/test_compare_industry_multi.py \
  tests/test_industry_aggregates.py \
  tests/test_mcp_tools_registration.py \
  tests/test_mcp_answer_pack.py \
  tests/test_mcp_narrative_responses.py

136 passed, 1 skipped
```

`git diff --check` also passed.

## Behavior delivered

- Annual sources query the requested title year (`사업보고서 (YYYY.MM)`) and never fall back to a different-year receipt. Missing requested filings carry `provenance_status=requested_annual_report_not_cached` and a source-gap explanation.
- The contract downgrades `usable` to `limited` only when confirmed facts have no resolvable evidence. Explicit public non-DART `source_url` values remain valid evidence.
- `select_peer_group(..., year=...)` passes the exact year to financial-statement and peer resolution, and exposes `requested_year` with `resolved_year`.
- Fees, risk, accounting policies, KAM topics, audit-report matters, and procedures all pass their requested year into cohort selection. The audit-hours proxy resolves one cohort and shares it with fee/risk. The acceptance pack resolves one cohort and passes that same object to every child comparison.

## Limitations

- This is a bounded provenance/cohort consistency change; it does not introduce the broader Task 13 datamodel or quality ledger.
- A missing local annual report remains a cache/provenance gap, not proof that the filing does not exist in DART.
- Existing test warnings concern deprecated `datetime.utcnow()` use outside this task’s scope.

## Follow-up review fixes

### Red evidence

Before the follow-up implementation, the focused URL and contract suite produced 12 failures:

- Ten unsafe explicit URLs were accepted as evidence, including `javascript:`, `data:`, `file:`, `ftp:`, protocol-relative, missing-host, credential-bearing, localhost, and loopback URLs.
- An unsafe URL was rendered while quality remained `usable`.
- A result with one cited fact and one requested-year provenance gap remained `usable` because the previous check only required any one cited fact.

### Green evidence

```text
uv run pytest -q \
  tests/test_api_evidence_packs.py \
  tests/test_evidence_helpers.py \
  tests/test_peer_selection.py \
  tests/test_auditor_peer_tools.py \
  tests/test_mcp_contracts.py \
  tests/test_compare_industry_multi.py \
  tests/test_industry_aggregates.py \
  tests/test_mcp_tools_registration.py \
  tests/test_mcp_answer_pack.py \
  tests/test_mcp_narrative_responses.py

152 passed, 1 skipped
```

Explicit non-DART evidence now requires an absolute public HTTP(S) URL with a host and without credentials, localhost, loopback, or private/reserved IP destinations. Unsafe values never become envelope evidence or renderer links. Quality now counts unresolved confirmed facts and downgrades an explicit `usable` status whenever any fact lacks resolvable evidence; the warning includes the count. Empty confirmed-fact lists retain the existing status inference.
