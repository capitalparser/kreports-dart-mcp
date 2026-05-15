# Validation Reference

## Corpus Case Minimum

Each corpus case needs:

- `corp_code`, `stock_code`, `corp_name`
- `rcept_no`, `bsns_year`, `reprt_code`, `filing_name`
- source route
- target extraction
- expected output or explicit failure record
- known parser risk

## Validation Levels

| Level | Meaning |
| --- | --- |
| Candidate | Case is identified but not regression-ready. |
| Expected defined | Expected output exists in machine-comparable form. |
| Fixture ready | A test fixture or script can compare parser output. |
| Regression locked | Automated tests cover the case. |

## Gap Classes

- Fetch: DART endpoint or payload retrieval problem.
- Structure: document, section, title, context, unit, or table boundary problem.
- Normalization: source value found but mapped incorrectly.
- Validation: period, scale, CFS/OFS, duplicate, or traceability check missing.
- Signal: parsed output is correct but MCP or analysis interpretation is misleading.

## Before Claiming A Parser Improvement

- Run the narrow parser tests.
- Add or update a corpus manifest case when the behavior comes from a real filing pattern.
- Explain whether the improvement changes MCP-facing output.
- Name residual uncertainty if the source route is ambiguous.
