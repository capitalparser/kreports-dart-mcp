# DART Parsing Playbook Plan

## Verdict

Build the DART parsing knowledge layer inside KReports first, then extract a reusable skill later if it proves useful outside this repo.

## Rationale

KReports already owns the DART collector, parser, database, analysis, and MCP layers. Keeping docs, corpus metadata, skill guidance, and future regression fixtures in this repository lets parser knowledge feed directly into code and tests.

## Scope

Required now:

- Add `docs/dart-parsing/` as the human and developer playbook.
- Add `corpus/dart-samples/manifest.yaml` as the metadata-first golden corpus index.
- Add `skills/dart-financial-parser/` as an agent-facing workflow.
- Avoid changing parser code while MCP updates are happening in the main checkout.

Recommended next:

- Fill `corp_code` and `rcept_no` for the first five sample cases.
- Define expected outputs for one XBRL financial fact case and one note disclosure case.
- Convert those expected outputs into fixtures and narrow regression tests.
- Only then modify `xbrl_parser.py`, `policy_parser.py`, or a future note parser.

## Non-Goals

- No MCP tool changes in this branch.
- No DB schema changes in this branch.
- No raw filing archive commits until redistribution and repository-size tradeoffs are reviewed.
