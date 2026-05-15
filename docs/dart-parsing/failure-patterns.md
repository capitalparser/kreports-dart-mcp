# Parser Failure Patterns

Use this file to classify parser gaps found while reviewing DART filings. Every failure should name the source route, expected behavior, actual behavior, and whether it is a fetch, structure, normalization, validation, or signal-layer issue.

## Classification

| Class | Meaning | Typical fix |
| --- | --- | --- |
| Fetch | Source payload unavailable, wrong endpoint, DART status not handled | Collector retry, fallback, or status handling |
| Structure | Parser misses document, section, title, context, unit, or table boundary | XML/XBRL structural parser |
| Normalization | Source value found but mapped to wrong concept or label | Account map, taxonomy map, note family keyword |
| Validation | Output lacks checks for period, CFS/OFS, scale, duplicates, or traceability | Validation rule and confidence metadata |
| Signal | Parsed data is correct but MCP/dashboard interpretation is misleading | Analysis or MCP output contract |

## Known Patterns

### XBRL Context Ambiguity

Some XBRL facts share the same concept across multiple contexts. Deduping only by `account_id` can drop useful facts or keep the wrong one.

Required response:
- Include context period, dimensions, and statement role in dedupe keys.
- Preserve the discarded alternatives in diagnostics when confidence is low.

### Statement Type Misclassification

Duration facts are not always income statement facts. Cash-flow facts and changes in equity also use duration contexts.

Required response:
- Prefer role/linkbase or known concept map.
- Use local-name heuristics only as fallback.

### CFS/OFS Leakage

Filename and context dimensions may disagree or be absent. A requested CFS extraction can accidentally include OFS facts.

Required response:
- Record how `fs_div` was determined: requested, filename, context dimension, or unknown.
- Warn when facts lack enough evidence for the requested division.

### Note Section Early Cutoff

Naive "next TITLE" logic can cut a broad accounting policy or note section at its first child heading.

Required response:
- Infer title level.
- Stop at next sibling or parent-level title, not every next title.

### Table Flattening Loss

DART XML tables can carry the core meaning of lease maturity, related-party balances, fair value hierarchy, and revenue breakdowns. Plain text extraction can lose row/column relationships.

Required response:
- Preserve raw table XML in note output.
- Add structured table normalization only after adding a golden case.

### Korean Label Drift

The same concept appears under different Korean labels across companies and years.

Required response:
- Keep source label and normalized concept separately.
- Add aliases only with a corpus example and a test.

## Failure Record Template

```yaml
id: "FP-YYYY-NNN"
class: "Structure"
source_route: "document_xml"
corp_code: ""
stock_code: ""
corp_name: ""
rcept_no: ""
bsns_year: 2024
reprt_code: "11011"
target: "revenue note"
expected: ""
actual: ""
suspected_cause: ""
next_action: ""
test_path: ""
```
