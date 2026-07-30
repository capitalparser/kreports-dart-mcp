# KAM parser boundary hardening

## Root cause

Python's `HTMLParser` treats `TITLE` as an HTML CDATA element. DART documents
also use that tag as a structural wrapper, including nested pseudo-HTML,
self-closing presentation tags, comments, and XML-style CDATA. The parser
therefore received markup as title text and retained an unreliable heading
ancestry across malformed boundaries.

## Remediation

- adapt only `TITLE` tokens to a parser-safe structural token, then restore the
  logical title frame before KAM boundary classification;
- lex valid case-insensitive CDATA payloads as literal text without stripping
  source content, while returning `malformed_cdata` for spaced or unterminated
  declarations; and
- ignore comments and processing instructions as metadata while preserving
  decoded entity text.

This keeps malformed structure fail-closed: ambiguous title boundaries remain
`ambiguous`, incomplete explicit matters remain `error`, and no KAM evidence is
emitted from either outcome.

## Verification

- `uv run --extra dev pytest -q tests/test_kam_parser.py` — 193 passed
- Parent independent regression: 10 related parser/report/persistence/procedure
  files — 438 passed
- `uv run --extra dev ruff check kreports/processor/kam_parser.py` and
  `git diff --check` — passed

All verification used fixtures only; no live database, DART API, or network
request was made.
