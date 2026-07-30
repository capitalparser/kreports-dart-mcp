---
name: dart-financial-parser
description: Use when inspecting, improving, or validating Korean DART financial statement and note disclosure parsing for KReports, including XBRL instance facts, fnlttSinglAcntAll data, document.xml notes, accounting policies, parser failures, and golden corpus cases.
---

# DART Financial Parser

Use this skill to analyze Korean DART filings for KReports parser work. The job is to preserve source traceability, classify parser failures precisely, and turn filing examples into durable corpus cases and tests.

## Workflow

1. Identify the target company, reporting year, `reprt_code`, `fs_div`, and `rcept_no` if available.
2. Choose the source route:
   - Use `fnlttSinglAcntAll.json` for broad statement facts.
   - Use `fnlttXbrlDs003.zip` for XBRL instance-level facts and taxonomy diagnosis.
   - Use `document.xml` for notes, accounting policies, business sections, and tables.
   - Use DART viewer HTML only to diagnose layout that XML/API routes lost.
3. Preserve traceability in every output: `corp_code`, `stock_code`, `corp_name`, `rcept_no`, `bsns_year`, `reprt_code`, `fs_div`, source route, source file, and section title or context.
4. Separate failures into fetch, structure, normalization, validation, or signal-layer issues.
5. When a new pattern is valuable, add or update a case in `corpus/dart-samples/manifest.yaml`.
6. Only change parser code after an expected output or failure record is clear.

## Read These References As Needed

- `references/xbrl.md`: XBRL parsing rules, context handling, duplicate facts, statement classification.
- `references/note-disclosures.md`: document.xml note boundary and note-family extraction.
- `references/validation.md`: corpus case quality, expected outputs, and regression checks.

## KReports Integration Points

- Financial facts: `kreports/processor/fin_parser.py`, `kreports/processor/xbrl_parser.py`
- Note and policy extraction: `kreports/processor/policy_parser.py`, `kreports/processor/report_section_parser.py`
- Acquisition: `kreports/collector/fetcher.py`, `kreports/collector/fin_collector.py`
- Regression assets: `corpus/dart-samples/manifest.yaml`, future `tests/fixtures/dart-samples/`

## Guardrails

- Do not commit DART API keys, private working papers, or client-sensitive data.
- Do not store full raw filings unless redistribution and size are acceptable for the repository.
- Do not silently infer accounting meaning from a Korean label when the source route, period, or statement type is ambiguous.
- Do not merge CFS and OFS facts unless the caller explicitly asks for both.
- Do not treat a text excerpt as structured evidence when table row/column relationships are necessary.

## Output Shape

For parser diagnosis, report:

- verdict: `pass`, `fail`, or `conditional`
- source route and identifiers
- expected extraction
- actual extraction
- gap class
- proposed corpus/test update
- parser files likely affected
