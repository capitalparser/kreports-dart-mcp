# Task 6 — QoE multi-year filing provenance

## RED

`UV_CACHE_DIR=/tmp/kreports-qoe-provenance-uv-cache uv run --extra dev python -m pytest tests/test_qoe_multiyear_provenance.py -q`

- `3 failed`: a single cited end-year still produced `usable`; no annual-source
  list or provenance table existed; a conflicting duplicate remained usable.

Review fix RED evidence:

- contaminated attachment-style receipt: `1 failed`, incorrectly `usable`
- non-usable, non-finite, non-duration, and mixed-unit rows: `4 failed`, all
  incorrectly `usable`
- three revenue-only years: `1 failed`, incorrectly counted as three QoE years
- provenance-columns-missing legacy table: `1 failed`, incorrectly returned
  `stable`
- two proven years: `1 failed`, incorrectly created a public confirmed fact
- whitespace-only unit: `1 failed`, classified as a ratio mismatch instead of
  a missing unit
- whitespace-padded raw receipt: `1 failed`, incorrectly admitted as `usable`
- two proven years plus one unproven year: `1 failed`, the limited answer pack
  omitted both valid annual filing links from top-level `sources`
- a 100,014-character rejected SQLite receipt: `1 failed`, the complete raw
  value leaked through `raw_citation_rcept_nos`

## GREEN

- `kreports.analysis.investor_quality` admits QoE financial years only when all
  four canonical inputs (`revenue`, `operating_profit`, `profit_loss`, and
  `operating_cash_flow`) have a trimmed raw receipt exactly equal to the
  canonical company/year annual filing, `company_year_annual_filing_match`,
  `quality_status=usable`, a finite numeric value, `period_type=duration`, and
  a nonblank recorded unit. It groups duplicate rows deterministically,
  requires compatible units for each computed ratio, and excludes conflicts.
- Unproven or conflicting years remain in `financial_observations` with an
  explicit limitation and no public receipt. Proven years are emitted through
  `financial_sources` and each QoE evidence row.
- `kreports.analysis.financial_analysis` binds all year observations, rather
  than only the final year, into a QoE confirmed fact only after at least three
  complete, proven years. The answer pack exposes
  `quality_financial_provenance` and its summary status follows data quality.
- A legacy compact table without provenance columns exposes only years and
  available metric keys. It does not expose money, calculate signals, create a
  confirmed fact, or return `stable`.
- Stored receipt proof compares the untouched raw string with the canonical
  14 digits. Whitespace and attachment contamination remain bounded and
  inspectable but cannot establish proof.
- A limited QoE result keeps up to 20 deduplicated source links only when a
  `financial_sources` receipt exactly matches a proven annual observation.
  Unproven years never enter top-level sources.
- Rejected receipt diagnostics are capped at eight entries. Each value longer
  than 80 characters is represented by a 32-character prefix, original length,
  SHA-256, and explicit `truncated=true`; the raw value is absent from domain
  and MCP payloads.
- Added literal isolated-DB tests for wrong-company/year receipt rejection,
  fully proven three-year evidence, and conflicting compact duplicates.

## Verification

`UV_CACHE_DIR=/tmp/kreports-qoe-provenance-uv-cache uv run --extra dev python -m pytest tests/test_qoe_multiyear_provenance.py tests/test_investor_quality.py tests/test_api_evidence_packs.py tests/test_mcp_contracts.py tests/test_mcp_answer_pack.py tests/test_dcf_readiness_surface.py tests/test_professional_mcp_contract.py -q`

- `141 passed`

`uv run ruff check kreports/analysis/investor_quality.py kreports/analysis/financial_analysis.py kreports/mcp/answer_pack.py tests/test_qoe_multiyear_provenance.py tests/test_investor_quality.py tests/test_api_evidence_packs.py`

- `All checks passed`

`git diff --check`

- clean

## Commit

`bd8821c761c07b0adce9236fad02b0938c33b57f` — `Harden QoE multi-year filing provenance`

`ba15970b0d894968c993ce5ef54baaeab8ab4722` — `Fix QoE provenance admission gaps`

`d967a21983fb2bb7281263903c3264fab312d23e` — `Close final QoE source boundaries`

`2a76480cab8a20c8782add7f888ab95a775b7c19` — `Bound rejected QoE receipt diagnostics`
