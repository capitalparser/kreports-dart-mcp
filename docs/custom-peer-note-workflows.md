# Custom peer and accounting-note workflows

This document describes the additive peer-selection and note-search contract layered on the existing 34 MCP tools.

## 1. Resolve a reproducible peer group

Use `select_peer_group` with an explicit year and `peer_criteria`.

```json
{
  "company": "005930",
  "year": 2024,
  "peer_criteria": {
    "mode": "strict",
    "industry_basis": "ksic",
    "prefix_len": 3,
    "size_metric": "total_assets",
    "size_log10_tolerance": 1.0,
    "excluded_corp_codes": ["00126380"],
    "required_features": ["financials", "notes"],
    "minimum_coverage": 1.0
  },
  "peer_limit": 30
}
```

The result keeps `criteria_applied`, `requested_year`, `resolved_year`, inclusion reasons, exclusion reasons, coverage by peer, and the actual peer list.

## 2. Multi-year financial industry analysis using the same custom logic

`compare_to_industry_multi` accepts the same `peer_criteria` plus a reference year. When either field is supplied, the comparison uses a resolved explicit peer group rather than independently rebuilding a generic KSIC population.

```json
{
  "company": "005930",
  "year": 2024,
  "metrics": ["영업이익률", "ROE", "부채비율"],
  "years_back": 5,
  "peer_criteria": {
    "mode": "ranked",
    "industry_basis": "ksic",
    "prefix_len": 3,
    "fallback_prefix_len": 2,
    "size_metric": "total_assets",
    "size_log10_tolerance": 1.0,
    "weights": {
      "industry": 0.4,
      "sector": 0.2,
      "size": 0.2,
      "coverage": 0.2
    }
  }
}
```

Each metric-year cell reports its own `n`, P25/P50/P75, subject value, percentile and unit. Missing peer financials reduce that cell's `n`; they are not silently imputed.

## 3. Search companies that disclose a particular accounting-note topic

Use the existing `search_dataset` tool with `dataset=accounting_note_chapters` and a keyword. The handler returns a company-oriented evidence result.

```json
{
  "dataset": "accounting_note_chapters",
  "keyword": "자금보충약정",
  "year": 2024,
  "market": "KOSPI",
  "induty_prefix": "35",
  "fs_div": "CFS",
  "limit": 50,
  "include_excerpt": true
}
```

The response preserves company, year, note number/title, receipt number and bounded excerpt from the cached note chapter. An empty result means no matching row exists in the prepared KReports cache; it does **not** prove that no DART filer disclosed the subject.

## 4. Compare original notes across the chosen peer group

`compare_peer_accounting_notes` already accepts `peer_criteria`. For bundled internal workflows, KReports first resolves the requested cohort and then converts the resolved peer list to an exact `custom_codes` profile before note comparison. This prevents a second peer-selection pass from drifting to a different cohort.

```json
{
  "company": "005930",
  "year": 2024,
  "topics": ["leases", "impairment", "provisions_contingencies"],
  "peer_criteria": {
    "mode": "strict",
    "industry_basis": "ksic",
    "prefix_len": 3,
    "required_features": ["notes"]
  }
}
```

## 5. Other peer comparisons

The following tools accept the same `peer_criteria` contract without increasing the frozen 34-tool count:

- `compare_peer_audit_fees`
- `compare_peer_risk_profile`
- `compare_peer_accounting_policies`
- `compare_peer_kam_topics`
- `compare_peer_audit_report_matters`
- `compare_peer_audit_procedures`

The selected peer group and its selection policy remain visible in each result.

## Data-quality interpretation

- A selected peer is not a valuation conclusion or audit judgment.
- `strict` means requested evidence/filters are enforced rather than silently relaxed.
- `adaptive` may use the declared KSIC fallback rule.
- `custom_codes` is an explicit user universe, not an assertion that those companies are economically comparable.
- Note and audit-report searches are cache-first/read-only. Cache absence is not filing absence.
- Peer statistics report actual available `n` and do not impute missing company-year observations.
