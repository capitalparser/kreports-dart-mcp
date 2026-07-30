# XBRL Reference

## Route

Use DART `fnlttXbrlDs003.zip` when the task needs taxonomy-level financial facts, period contexts, or diagnosis beyond `fnlttSinglAcntAll.json`.

## Required Checks

- Parse every `.xbrl` file in the ZIP, but filter CFS/OFS by filename and context dimension when possible.
- Contexts must capture period type, start date, end date, instant date, and dimensions.
- Units must distinguish KRW monetary facts from shares, ratios, text blocks, and non-KRW units.
- `decimals` affects precision and scale. Record uncertainty when conversion is inferred.
- Deduplication should include statement, concept, period, dimensions, and `fs_div`.
- Statement type should prefer role/linkbase metadata when implemented. Until then, use concept maps plus context heuristics with confidence warnings.

## Common Concept Targets

- Revenue: `ifrs-full_Revenue`, `ifrs-full_RevenueFromContractsWithCustomers`, DART revenue extensions.
- Operating profit: `dart_OperatingIncomeLoss`, `ifrs-full_ProfitLossFromOperatingActivities`.
- Net income: `ifrs-full_ProfitLoss`, parent-owner variants when consolidated.
- Assets: `ifrs-full_Assets`.
- Liabilities: `ifrs-full_Liabilities`.
- Equity: `ifrs-full_Equity`, owner-attributable equity where appropriate.

## Failure Signals

- All balance sheet fields appear as `None`.
- Cash-flow concepts are classified as income statement facts.
- Requested CFS output contains obvious OFS contexts.
- Large company XBRL yields very few numeric facts.
- The same concept appears multiple times and the parser keeps an arbitrary row.
