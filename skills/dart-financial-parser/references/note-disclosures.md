# Note Disclosure Reference

## Route

Use DART `document.xml` ZIP for note disclosures, accounting policies, management discussion sections, and tables.

## Boundary Method

- Start with XML `TITLE` tags.
- Normalize title text before keyword matching.
- Infer title level from roman numerals, numbered headings, parenthesized numbers, and Korean letter headings.
- For broad parent sections, include child titles until the next sibling or parent-level title.
- Preserve raw XML and text excerpts. Do not discard tables.

## Priority Note Families

- Significant accounting policies
- Revenue recognition
- Leases
- Financial instruments
- Related parties
- Commitments, contingencies, and litigation
- Provisions
- Impairment
- Subsidiaries, associates, and joint arrangements
- Subsequent events
- Going concern and capital impairment

## Table Handling

When a note's meaning depends on a table:

1. Keep raw table XML.
2. Extract table caption or nearest heading.
3. Preserve row and column order.
4. Normalize only after creating a corpus expected output.

## Failure Signals

- The parser returns only a heading and misses child paragraphs.
- A note stops at the first child `TITLE`.
- Related-party or lease maturity tables are flattened into unreadable text.
- CFS and OFS notes are mixed without labels.
