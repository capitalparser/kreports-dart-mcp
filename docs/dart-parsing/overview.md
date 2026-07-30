# DART Parsing Playbook

This playbook defines how KReports should extract, normalize, validate, and improve parsing for Korean DART financial statements and note disclosures.

It is intentionally tied to the KReports codebase. Keep parser guidance, sample cases, and regression expectations close to the implementation so the documentation becomes executable over time instead of drifting away from the parser.

## Goals

- Turn DART filings into stable financial facts, note excerpts, and audit/accounting signals.
- Preserve source traceability by keeping `corp_code`, `rcept_no`, `bsns_year`, `reprt_code`, `fs_div`, section title, and extraction path with every important output.
- Build a golden corpus of representative filings so parser changes can be evaluated against known Korean disclosure patterns.
- Package proven workflows into `skills/dart-financial-parser` so agents can inspect new filings using the same method.

## Source Routes

Use source routes in this order unless a task explicitly needs another route.

| Route | DART endpoint or source | Best for | KReports layer |
| --- | --- | --- | --- |
| Structured financial API | `fnlttSinglAcntAll.json` | Broad financial fact collection | `kreports.collector.fin_collector`, `kreports.processor.fin_parser` |
| XBRL ZIP | `fnlttXbrlDs003.zip` | Full XBRL instance inspection and taxonomy-level facts | `kreports.processor.xbrl_parser` |
| Document XML ZIP | `document.xml` | Business report body, financial statement notes, policy text | `kreports.collector.fetcher`, `kreports.processor.report_section_parser`, `kreports.processor.policy_parser` |
| DART viewer HTML | DART web viewer | Manual diagnosis when XML/API output loses layout or table meaning | Docs and future fixtures |

## Parsing Layers

1. **Acquisition layer** downloads source payloads and records fetch status without interpreting accounting meaning.
2. **Structural layer** identifies document type, statement/note boundaries, table structures, contexts, units, and periods.
3. **Normalization layer** maps source-specific labels and element IDs into KReports concepts such as revenue, operating profit, lease policy, or related-party note.
4. **Validation layer** checks completeness, duplicate handling, period alignment, CFS/OFS consistency, and source traceability.
5. **Signal layer** turns parsed data into MCP-facing outputs, dashboards, or audit/investor risk flags.

Keep these layers separate. A source fetch failure, a section boundary miss, and an accounting mapping miss require different fixes and should not be collapsed into a generic parser error.

## Initial Corpus Strategy

Start with a compact but diverse corpus before expanding volume.

| Segment | Why it matters | Example target |
| --- | --- | --- |
| Large manufacturing CFS | Standard IFRS taxonomy and long notes | Samsung Electronics |
| Semiconductor / capex-heavy | PPE, impairment, inventory, cash flow pressure | SK hynix |
| Platform / service revenue | revenue recognition and intangible assets | NAVER or Kakao |
| Bio / development cost | R&D, capitalization, impairment sensitivity | Celltrion or Samsung Biologics |
| Construction / order backlog | construction contracts, progress revenue, provisions | Hyundai Engineering & Construction |
| Financial institution | banking/insurance taxonomy divergence | KB Financial Group or Samsung Life |
| Holding company | separate vs consolidated interpretation | SK Inc. or LG Corp. |
| Distressed issuer | going-concern and audit opinion parsing | A recent cautionary case |
| Unlisted audit report | non-listed statement shape and missing structured fields | A DART audit-report-only company |

The corpus should begin with metadata and expected outputs rather than full raw filing archives. Store raw source only when redistribution and size are acceptable.

## Done Criteria For A New Case

A sample case is useful only when it includes:

- `corp_code`, `stock_code`, `corp_name`, `rcept_no`, `bsns_year`, `reprt_code`, and filing name.
- Source route used: API, XBRL ZIP, document XML, or viewer HTML.
- Target extraction task: financial facts, accounting policy, specific note, table, or section.
- Expected output in a machine-comparable form.
- Known failure mode or parser gap, if any.
- A validation command or future test path.

## Related Files

- `docs/dart-parsing/xbrl-financial-statements.md`
- `docs/dart-parsing/note-disclosures.md`
- `docs/dart-parsing/failure-patterns.md`
- `corpus/dart-samples/manifest.yaml`
- `skills/dart-financial-parser/SKILL.md`
