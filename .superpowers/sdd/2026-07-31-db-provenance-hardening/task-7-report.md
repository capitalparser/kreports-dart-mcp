# Task 7 — Materiality benchmark readiness coverage

## Scope

- Adds a read-only `materiality_benchmark` coverage feature to the release
  gate.  It is degraded for `public_runtime` and becomes the explicit
  `materiality_benchmark_coverage` blocker for `auditor_full` below 95%.
- The denominator is every listed KOSPI/KOSDAQ company in the declared
  `coverage_year`; it is not narrowed to companies that already happen to
  have compact rows.
- A numerator company needs one `(fs_div, metric_key)` alternative with all
  three years in `[coverage_year-2, coverage_year]`.  Accepted alternatives
  are PBT, revenue, assets, and equity.  Each compact row must be usable KRW,
  use its metric's registered instant/duration period type, name a populated
  source account/table, have the exact annual-filing receipt and report name,
  and use `company_year_annual_filing_match`.
- One- and two-year supports are retained as bounded exclusion counts, never
  borrowed across metric, statement, company, or year.
- Gate report/CLI and release manifest/runtime readiness retain the coverage,
  policy metadata, denominator, and exclusions.

## TDD evidence

- RED: six literal temp-DB cases failed before the implementation: zero
  coverage, only two annual years, exact three-year series, wrong receipt,
  wrong citation basis, and non-KRW unit.
- GREEN:
  - `uv run --extra dev python -m pytest tests/test_quality_release_gate.py -q`
    → `27 passed`
  - `uv run --extra dev python -m pytest tests/test_release_artifact.py -q`
    → `53 passed`
  - retained-clone/MCP/materiality regression suite → `194 passed`
  - Ruff and `git diff --check` passed.

## Safety and tradeoff

- The aggregation is a bounded, read-only SQL CTE; it does not invoke MCP per
  company and did not open or mutate `kreports.db`.
- The metric admits one complete benchmark alternative, not all four.  This
  preserves explicit partial support while avoiding a false claim that every
  materiality benchmark is present.  The selected metric set and statement
  policy are emitted in `coverage_metadata` for operator review.

## Commit

- Pending commit.
