# Financial Compact Provenance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist value lineage, unit and period semantics, and honestly labeled annual filing citation anchors on every rebuilt compact financial row.

**Architecture:** The existing compact builder remains the sole writer. It derives unit and period from explicit parser/metric contracts, resolves annual filing anchors in bounded scope batches, and atomically writes value plus provenance; compact readers prefer persisted anchors and use the legacy matcher only when revision-10 columns are absent.

**Tech Stack:** Python 3.12, SQLAlchemy Core, SQLite window functions, pandas, pytest, Ruff, uv

## Global Constraints

- Start from the reviewed schema-foundation commit containing revisions 09–11.
- Do not open or modify the live database and do not call DART.
- Treat a company/year annual filing match as a citation anchor, not direct endpoint lineage.
- Retain amounts that cannot be cited and mark them `uncitable` and `limited`.
- Keep the compact unique key and every existing response key stable.
- Avoid one query per compact row or company-year.
- Do not push, open a pull request, merge, or deploy.

---

## File Structure

- Modify `kreports/processor/fin_parser.py`: explicit raw-amount storage-unit contract.
- Modify `tests/test_fin_parser.py`: prove raw KRW integers are preserved without scaling.
- Modify `kreports/analysis/filing_provenance.py`: bounded citation-anchor resolver.
- Create `tests/test_financial_compact_provenance.py`: builder and persisted-read contract.
- Modify `kreports/maintenance/financial_compact.py`: provenance derivation and atomic upserts.
- Modify `kreports/analysis/financial_analysis.py`: prefer persisted compact citations.
- Modify `kreports/mcp/handlers/company.py`: use the latest row's persisted source.
- Modify `kreports/analysis/dcf_source.py`: retain provenance limitations on selected inputs.
- Modify related compact, filing, semantic-registry, DCF, and MCP tests.

### Task 1: Prove the Financial Amount Storage Contract

**Files:**
- Modify: `kreports/processor/fin_parser.py`
- Modify: `tests/test_fin_parser.py`
- Modify: `tests/test_semantic_registry.py`

**Interfaces:**
- Produces:

```python
FINANCIAL_AMOUNT_STORAGE_UNIT = "KRW"
```

- [ ] **Step 1: Write the failing unit-contract tests**

```python
def test_financial_amount_storage_contract_is_unscaled_krw(
    dart_response_samsung_2024,
):
    from kreports.processor.fin_parser import (
        FINANCIAL_AMOUNT_STORAGE_UNIT,
        parse_all_accounts,
    )

    rows = parse_all_accounts(
        dart_response_samsung_2024,
        corp_code="00126380",
        year=2024,
        reprt_code="11011",
        fs_div="CFS",
    )
    revenue = next(row for row in rows if row["account_id"] == "ifrs-full_Revenue")
    assert FINANCIAL_AMOUNT_STORAGE_UNIT == "KRW"
    assert revenue["thstrm_amount"] == 300_869_340_000_000
```

Add a registry test proving every compact metric uses `unit="KRW"`,
`source_unit="KRW"`, `source_multiplier=1`, and a period type in
`{"instant", "duration"}`.

- [ ] **Step 2: Run tests to verify RED**

```bash
uv run pytest tests/test_fin_parser.py::test_financial_amount_storage_contract_is_unscaled_krw tests/test_semantic_registry.py -q
```

Expected: FAIL because the explicit storage constant is absent.

- [ ] **Step 3: Add the explicit contract**

At module scope in `fin_parser.py`:

```python
# OpenDART financial amount strings are parsed and persisted without scaling.
# FinancialFact and Financial integer amount columns therefore store KRW.
FINANCIAL_AMOUNT_STORAGE_UNIT = "KRW"
```

Do not change `_parse_amount()` or historical amount values.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_fin_parser.py tests/test_semantic_registry.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kreports/processor/fin_parser.py tests/test_fin_parser.py tests/test_semantic_registry.py
git commit -m "test: define financial amount storage unit"
```

### Task 2: Bounded Annual Filing Citation Anchors

**Files:**
- Modify: `kreports/analysis/filing_provenance.py`
- Create: `tests/test_financial_compact_provenance.py`
- Modify: `tests/test_filing_provenance.py`

**Interfaces:**
- Produces alias `CompactCitationScope = tuple[str, int, str]`.
- Produces
  `compact_citation_anchors(scopes: Iterable[CompactCitationScope], *, batch_size: int = 100) -> dict[CompactCitationScope, dict[str, Any]]`.

The tuple is `(corp_code, bsns_year, fs_div)`. Returned anchors always contain
`citation_basis="company_year_annual_filing_match"`.

- [ ] **Step 1: Write failing bounded-resolver tests**

Create scopes for multiple companies, years, and CFS/OFS divisions. Seed:

- a valid annual filing and a newer correction for one scope;
- an invalid receipt for another;
- a filing for the wrong company;
- a filing for the wrong business year.

Assert:

```python
assert anchors[("00126380", 2025, "CFS")] == {
    "corp_code": "00126380",
    "bsns_year": 2025,
    "fs_div": "CFS",
    "rcept_no": "20260310002820",
    "report_nm": "사업보고서 (2025.12) [정정]",
    "citation_basis": "company_year_annual_filing_match",
}
assert ("00999999", 2025, "CFS") not in anchors
```

Capture SQL and assert the resolver executes `ceil(unique_scopes / 100)`
queries, not one query per scope.

- [ ] **Step 2: Run tests to verify RED**

```bash
uv run pytest tests/test_financial_compact_provenance.py tests/test_filing_provenance.py -q
```

Expected: import failure for `compact_citation_anchors`.

- [ ] **Step 3: Implement chunked scope matching**

Normalize, deduplicate, and sort scopes. Reject `batch_size < 1`. For each
chunk, build a parameterized CTE:

```sql
WITH requested(corp_code, bsns_year, fs_div) AS (
  VALUES (:corp_0, :year_0, :fs_0), (:corp_1, :year_1, :fs_1)
),
ranked AS (
  SELECT
    requested.corp_code,
    requested.bsns_year,
    requested.fs_div,
    d.rcept_no,
    d.report_nm,
    ROW_NUMBER() OVER (
      PARTITION BY requested.corp_code, requested.bsns_year, requested.fs_div
      ORDER BY d.disc_date DESC, d.rcept_no DESC
    ) AS source_rank
  FROM requested
  JOIN disclosures AS d ON d.corp_code = requested.corp_code
  WHERE d.report_nm LIKE
        ('%사업보고서 (' || requested.bsns_year || '.%')
    AND d.rcept_no GLOB
        '*[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]*'
)
SELECT corp_code, bsns_year, fs_div, rcept_no, report_nm
FROM ranked
WHERE source_rank = 1
```

Normalize attachment receipts through `parent_rcept_no`. This resolver accepts
only scopes already proven by the compact builder; it does not claim the
receipt generated the underlying endpoint row.

- [ ] **Step 4: Run resolver tests**

```bash
uv run pytest tests/test_financial_compact_provenance.py tests/test_filing_provenance.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kreports/analysis/filing_provenance.py tests/test_financial_compact_provenance.py tests/test_filing_provenance.py
git commit -m "feat: resolve bounded financial citation anchors"
```

### Task 3: Persist Compact Value and Citation Provenance

**Files:**
- Modify: `kreports/maintenance/financial_compact.py`
- Modify: `tests/test_financial_compact_provenance.py`
- Modify: `tests/test_runtime_db_export.py`
- Modify: `tests/test_dcf_model_source.py`

**Interfaces:**
- Consumes: `metric_definition()`,
  `FINANCIAL_AMOUNT_STORAGE_UNIT`, and `compact_citation_anchors()`.
- Produces
  `_compact_provenance(*, metric_key: str, source_table: str, citation: dict[str, Any] | None) -> dict[str, str | None]`.

- [ ] **Step 1: Write failing writer tests**

Test authoritative and fallback rows:

```python
assert authoritative == {
    "source_table": "financial_facts",
    "unit": "KRW",
    "period_type": "duration",
    "citation_rcept_no": "20250318000001",
    "citation_report_nm": "사업보고서 (2024.12)",
    "citation_basis": "company_year_annual_filing_match",
    "quality_status": "usable",
}
assert fallback["source_table"] == "financials"
assert assets["period_type"] == "instant"
assert uncitable["amount"] == 100
assert uncitable["citation_rcept_no"] is None
assert uncitable["citation_basis"] == "uncitable"
assert uncitable["quality_status"] == "limited"
```

Add a test that a compact metric with `period_type="event"` raises before the
scoped delete, leaving pre-existing compact rows intact. Add a semantic snapshot
test excluding `fetched_at` and proving two rebuilds match exactly.

- [ ] **Step 2: Run tests to verify RED**

```bash
uv run pytest tests/test_financial_compact_provenance.py tests/test_runtime_db_export.py tests/test_dcf_model_source.py -q
```

Expected: FAIL because provenance fields remain defaults.

- [ ] **Step 3: Implement provenance derivation**

```python
def _compact_provenance(
    *,
    metric_key: str,
    source_table: str,
    citation: dict[str, Any] | None,
) -> dict[str, str | None]:
    if source_table not in {"financial_facts", "financials"}:
        raise ValueError(f"unsupported compact source table: {source_table}")
    definition = metric_definition(metric_key)
    if definition.period_type not in {"instant", "duration"}:
        raise ValueError(
            f"unsupported compact period type: {definition.period_type}"
        )
    unit = (
        FINANCIAL_AMOUNT_STORAGE_UNIT
        if definition.unit == "KRW"
        and definition.source_unit == "KRW"
        and definition.source_multiplier == 1
        else None
    )
    cited = citation is not None
    return {
        "source_table": source_table,
        "unit": unit,
        "period_type": definition.period_type,
        "citation_rcept_no": citation["rcept_no"] if cited else None,
        "citation_report_nm": citation["report_nm"] if cited else None,
        "citation_basis": (
            "company_year_annual_filing_match" if cited else "uncitable"
        ),
        "quality_status": "usable" if cited and unit else "limited",
    }
```

Before deleting scoped compact rows, validate all selected metric definitions
and resolve unique `(corp_code, year, fs_div)` scopes in batches. Add
`source_table` to `_compact_rows()` output. Extend both insert/upsert statements
to write all seven fields atomically; the authoritative source continues to
outrank fallback.

- [ ] **Step 4: Run writer regressions**

```bash
uv run pytest tests/test_financial_compact_provenance.py tests/test_runtime_db_export.py tests/test_semantic_registry.py tests/test_dcf_model_source.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kreports/maintenance/financial_compact.py tests/test_financial_compact_provenance.py tests/test_runtime_db_export.py tests/test_dcf_model_source.py
git commit -m "feat: persist compact financial provenance"
```

### Task 4: Prefer Persisted Citations in Professional Reads

**Files:**
- Modify: `kreports/analysis/financial_analysis.py`
- Modify: `kreports/mcp/handlers/company.py`
- Modify: `kreports/analysis/dcf_source.py`
- Modify: `tests/test_financial_compact_provenance.py`
- Modify: `tests/test_filing_provenance.py`
- Modify: `tests/test_mcp_contracts.py`
- Modify: `tests/test_mcp_narrative_responses.py`

**Interfaces:**
- Consumes: revision-10 compact fields.
- Produces: the existing snapshot and MCP structures with each compact row's
  `source` derived from persisted citation fields.

- [ ] **Step 1: Write failing persisted-read tests**

Rebuild compact rows, then insert a newer disclosure without rebuilding. Assert
the snapshot still uses the persisted receipt and report name. Assert an
`uncitable` compact row has no receipt and makes `data_quality.status`
`limited`. Build an old-schema fixture without revision-10 columns and assert
only that fixture invokes the legacy matcher.

For the MCP handler:

```python
latest = max(result["rows"], key=lambda row: row["연도"])
assert result["confirmed_facts"][0]["source"] == latest["source"]
assert "direct endpoint" not in result["answer"].lower()
```

- [ ] **Step 2: Run tests to verify RED**

```bash
uv run pytest tests/test_financial_compact_provenance.py tests/test_filing_provenance.py tests/test_mcp_contracts.py tests/test_mcp_narrative_responses.py -q
```

Expected: FAIL because `_attach_annual_sources()` re-matches disclosures and
the handler resolves the source again.

- [ ] **Step 3: Implement schema-aware persisted reads**

Inspect `financial_facts_compact` columns once per request. When revision-10
fields exist, select them with each metric row, combine the year's metric
citations deterministically, and attach:

```python
{
    "corp_code": corp_code,
    "corp_name": corp_name,
    "report_nm": citation_report_nm,
    "bsns_year": year,
    "rcept_no": citation_rcept_no,
    "section_title": "재무제표",
    "source_table": "financial_facts_compact",
    "citation_basis": citation_basis,
}
```

If metrics in the same year disagree on citation receipt or basis, mark the
year limited and do not select one silently. Use the old
`annual_filing_sources()` path only when the columns do not exist.

Change `handle_get_financial_snapshot()` to reuse the latest row's `source`.
In `dcf_source.py`, keep the new columns optional for old artifacts but append a
limitation when a selected compact value is uncitable or lacks proven KRW unit.

- [ ] **Step 4: Run related and full verification**

```bash
uv run pytest tests/test_financial_compact_provenance.py tests/test_runtime_db_export.py tests/test_filing_provenance.py tests/test_dcf_model_source.py tests/test_mcp_contracts.py tests/test_mcp_narrative_responses.py tests/test_standard_audit_hours_inputs.py -q
uv run pytest
uv run ruff check kreports/processor/fin_parser.py kreports/analysis/filing_provenance.py kreports/maintenance/financial_compact.py kreports/analysis/financial_analysis.py kreports/mcp/handlers/company.py kreports/analysis/dcf_source.py tests/test_financial_compact_provenance.py
```

Expected: focused and full suites pass; Ruff reports no issue.

- [ ] **Step 5: Commit**

```bash
git add kreports/analysis/financial_analysis.py kreports/mcp/handlers/company.py kreports/analysis/dcf_source.py tests/test_financial_compact_provenance.py tests/test_filing_provenance.py tests/test_mcp_contracts.py tests/test_mcp_narrative_responses.py
git commit -m "feat: use persisted financial citation anchors"
```
