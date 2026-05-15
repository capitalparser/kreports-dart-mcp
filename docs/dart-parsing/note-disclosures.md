# Note Disclosure Parsing

Korean DART note disclosures are semi-structured. They often use XML `TITLE`, paragraphs, and tables, but important accounting meaning can depend on numbering, table layout, and company-specific wording. KReports should parse notes with source traceability and conservative confidence scoring.

## Priority Note Families

Start with note families that create useful investor and audit signals.

| Note family | Example outputs | Why it matters |
| --- | --- | --- |
| Significant accounting policies | revenue, leases, impairment, financial instruments, inventory, provisions | Baseline accounting model |
| Revenue | revenue streams, performance obligations, timing, contract balances | Quality of revenue |
| Leases | right-of-use assets, lease liabilities, maturity table | Debt-like obligations |
| Financial instruments | credit risk, fair value hierarchy, liquidity risk | Risk exposure |
| Related parties | transactions, balances, key management compensation | Governance risk |
| Commitments and contingencies | litigation, guarantees, construction/provision exposure | Off-balance-sheet risk |
| Subsidiaries and associates | group perimeter, ownership, auditor matrix hooks | Consolidation and group audit |
| Subsequent events | material events after reporting date | Update risk |
| Going concern / capital impairment | uncertainty wording, equity erosion | Audit risk |

## Extraction Strategy

1. Locate the financial statement note section inside DART `document.xml` ZIP contents.
2. Identify note-level boundaries with `TITLE` tags and numbering patterns.
3. Preserve raw XML slice, plain text, and table text for each extracted note.
4. Match note family with keyword sets and section titles.
5. Extract conservative excerpts first; add structured table extraction only after a corpus case proves the table shape.
6. Return source metadata with every extracted note: filename, title, character span, and `rcept_no`.

## Boundary Rules

Use these rules before adding custom heuristics:

- Prefer explicit `TITLE` boundaries over paragraph text.
- For broad titles like `재무제표 주석`, descend into numbered child titles.
- Stop a note at the next sibling title with the same or higher inferred title level.
- Keep table content inside the note boundary even when it appears after a paragraph heading.
- Do not merge notes across standalone and consolidated financial statements unless the source explicitly labels them as the same statement set.

## Output Contract Draft

Future note outputs should be shaped like this:

```yaml
corp_code: "00126380"
rcept_no: "20250312000000"
bsns_year: 2024
reprt_code: "11011"
fs_div: "CFS"
note_key: "revenue_recognition"
title: "수익"
source_route: "document_xml"
source_file: "..."
span:
  start: 12345
  end: 15678
confidence: 0.82
text_excerpt: "..."
tables:
  - caption: "..."
    normalized_rows: []
warnings: []
```

## Current KReports Hooks

- `policy_parser.py` already extracts significant accounting policy sections and item-level excerpts.
- `report_section_parser.py` already contains reusable title-level heuristics.
- The next implementation should reuse those helpers rather than creating a parallel note parser from scratch.
