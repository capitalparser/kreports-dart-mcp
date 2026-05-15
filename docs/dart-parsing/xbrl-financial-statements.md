# XBRL Financial Statements

KReports should treat XBRL as the highest-fidelity route for statement facts when available, but not as the only route. DART XBRL coverage, taxonomy usage, and company-specific extensions vary enough that the parser must degrade into structured API and document XML paths.

## Current Parser Contract

`kreports.processor.xbrl_parser.parse_xbrl_zip()` accepts DART `fnlttXbrlDs003.zip` bytes and returns `FinancialFact`-shaped dictionaries:

- `corp_code`
- `bsns_year`
- `reprt_code`
- `fs_div`
- `sj_div`
- `account_id`
- `account_nm`
- `thstrm_amount`

The parser currently extracts numeric facts from instance files, uses context period metadata, filters by target year and CFS/OFS, infers `sj_div`, and converts negative `decimals` into KRW-scale amounts.

## Required Extraction Decisions

Make these decisions explicit in any parser change or corpus case:

| Decision | Required handling |
| --- | --- |
| Reporting entity | Preserve CFS/OFS from filename, context dimension, or requested `fs_div`. |
| Period | Separate instant balance sheet contexts from duration performance/cash-flow contexts. |
| Unit | Accept KRW first; mark non-KRW or shares/unit facts as non-financial-fact candidates. |
| Scale | Apply `decimals` carefully and record when scale was inferred rather than explicit. |
| Statement type | Prefer role/linkbase when available; fall back to element/context heuristics only with lower confidence. |
| Duplicate facts | Dedupe by concept, period, statement, dimensions, and fs division, not by concept alone. |
| Labels | Prefer Korean labels from taxonomy/linkbase when available; otherwise keep element local name. |

## Validation Checklist

For each XBRL sample, verify:

- Balance sheet fields use instant contexts ending in the target `bsns_year`.
- Income statement and cash-flow fields use duration contexts for the correct fiscal period.
- `ifrs-full_Assets = ifrs-full_Liabilities + equity concept` within tolerance when all concepts exist.
- CFS and OFS facts are not mixed in one output unless explicitly requested.
- DART extension concepts such as `dart_OperatingIncomeLoss` map into summary fields without overriding better IFRS concepts incorrectly.
- Cash-flow facts are not misclassified as income statement facts just because they are duration facts.

## Known Next Improvements

- Add taxonomy label extraction for Korean `account_nm`.
- Parse role/linkbase metadata to improve `sj_div` inference.
- Preserve context dimensions beyond CFS/OFS for segment-level facts.
- Store parser confidence and extraction route with each fact.
- Add fixture-level expected outputs for the sample matrix in `corpus/dart-samples/manifest.yaml`.
