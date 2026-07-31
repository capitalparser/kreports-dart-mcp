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

## Follow-up: lexical boundaries and raw-text evidence isolation

- scan tags by index, respecting quoted attributes, so a CDATA-like literal in
  an attribute is never treated as a declaration;
- scan DOCTYPE declarations with the same quote awareness and internal-subset
  bracket depth, skipping nested comments and processing instructions, while
  treating an unclosed declaration as `malformed_doctype`;
- treat `SCRIPT` and `STYLE` payloads, including an unclosed payload through
  EOF, as non-evidence raw text in the structural projection; and
- avoid per-tag remainder copies. The adapter now uses indexed prefix checks,
  compiled regexes with `match`/`search` positions, and a bounded-suffix guard
  regression to retain linear behavior on tag-dense reports.

The raw filing body is retained by its document/evidence layer. This parser
only builds the intermediate structural projection, where script and style
text must never be accepted as audit evidence.

This keeps malformed structure fail-closed: ambiguous title boundaries remain
`ambiguous`, incomplete explicit matters remain `error`, and no KAM evidence is
emitted from either outcome.

## Verification

- `uv run --extra dev pytest -q tests/test_kam_parser.py` — 210 passed
- Structural guard plus 2,000,000-character tag-dense probe — 0.261 seconds
  locally
- `uv run --extra dev ruff check kreports/processor/kam_parser.py` and
  `git diff --check` — passed

All verification used fixtures only; no live database, DART API, or network
request was made.
