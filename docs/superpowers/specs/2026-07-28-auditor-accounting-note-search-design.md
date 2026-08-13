# Auditor Accounting Note Search Design

## Approval

The user reviewed the live Samsung Electronics note-search output, confirmed
that the remediation scope was clear, and explicitly requested goal-mode
implementation through final verification. This document records that approved
direction before implementation.

## Capability

When an auditor asks for an accounting-note topic, the existing
`search_dataset` tool with `dataset=accounting_note_chapters` must return a
chatbot-first, filing-grounded answer. A matching database row is not enough:
the answer must expose the relevant passage, its filing provenance, the
auditor-facing implication, and an honest coverage status.

## Kickoff Contract

### Input Data

- `accounting_note_chapters` is the primary source for parsed note bodies.
- `companies` supplies public company identity.
- The DART receipt number on each note row supplies filing provenance.
- Raw DART filings remain the source of record; local note rows are a bounded
  cache used for screening.

### Data Schema

Relevant note fields are `corp_code`, `bsns_year`, `fs_div`, `rcept_no`,
`dcm_no`, `source_type`, `note_no`, `note_title`, `section_type`, `body`, and
`body_length`. Search results add bounded keyword-centered excerpts without
changing durable storage.

### Source Priority

The matched note row and its receipt number are authoritative for the answer.
General accounting knowledge may explain an audit implication but must not be
presented as a filing fact. No other year or filing may be borrowed to make a
current-year result appear sourced.

### Business Terms

- `usable`: at least one relevant excerpt and public filing reference reach the
  user-facing answer.
- `limited`: a matching row exists, but the requested passage or public
  provenance cannot be rendered reliably.
- `missing`: the local cache has no matching row. This means cache absence, not
  absence from the filing.
- `confirmed fact`: a statement directly backed by the returned note passage.
- `audit implication`: deterministic screening guidance, kept separate from
  the filing fact and never presented as an audit conclusion.

### Output Shape

The chatbot answer is primary. It contains:

1. verdict and direct result,
2. relevant note number and title,
3. keyword-centered filing passage,
4. auditor-perspective implication,
5. DART receipt number and link,
6. coverage limitation and next check.

`answer_pack` provides a compact table with topic, year, statement basis,
matched passage, audit implication, and receipt number.

### Stop Conditions

- Do not call an empty cache result “not disclosed” or “not applicable.”
- Do not mark a result `usable` when no cited fact reaches the answer.
- Do not infer amounts, balances, legal exposure, or audit conclusions from a
  policy passage.
- Do not expose an unrelated beginning-of-note excerpt merely because the full
  row contains the keyword later.

### Done Criteria

- Inventory search exposes the average-cost and net-realizable-value passage.
- Provision search exposes the warranty-provision passage.
- Broad revenue search exposes keyword-centered passages rather than only the
  beginning of the note.
- Missing contingencies search clearly states cache absence, not filing
  absence.
- Raw result, chatbot verdict, and `answer_pack` status agree.
- Every `usable` result has at least one confirmed fact and DART source.
- Focused, related, and full regression tests pass.
- Immutable live calls against the current Samsung dataset demonstrate the
  behavior without modifying the database.

## Considered Approaches

### Chosen: deepen the existing dataset search contract

The analysis adapter extracts bounded match passages. The MCP handler converts
those passages into facts, audit implications, and next checks. Renderers and
the answer-pack builder only present that structured contract. This keeps
extraction, interpretation, and presentation separate and preserves the public
tool interface.

### Rejected: add a separate accounting-note search tool

A new tool could offer a narrower schema but would duplicate company, year,
source, and cache-search behavior and require new routing for a capability the
existing tool already advertises.

### Rejected: renderer-only correction

Presentation cannot recover the requested passage when the adapter discards all
context beyond the first 1,200 characters. Fixing only prose would leave the
evidence defect intact.

## Component Responsibilities

### `kreports.analysis.search_adapter`

- Find exact keyword occurrences after display normalization.
- Return up to three bounded, de-duplicated, keyword-centered excerpts.
- Preserve the existing `body_excerpt` field for compatibility while ensuring
  it is relevant when a keyword is supplied.
- Report cache-level search coverage without audit interpretation.

### `kreports.mcp.handlers.search`

- For accounting-note results, build filing-backed confirmed facts.
- Add deterministic auditor-perspective implications for common topics such as
  revenue, inventory, provisions, estimates, impairment, and contingencies.
- Set status fail-closed from passage and provenance coverage.
- Add next checks that distinguish policy review from balance and disclosure
  testing.

### `kreports.mcp.answer_pack`

- Build an accounting-note evidence table from the same facts and analysis.
- Use the shared status and shared DART sources; never recompute availability
  from row presence alone.

### `kreports.mcp.renderers`

- Render the professional answer envelope first.
- Show the compact answer-pack table as the chatbot fallback.
- Avoid raw internal field names and duplicate long excerpts.

## Error Handling

No-match results remain successful tool calls with `missing` data quality and a
clear cache-coverage warning. Malformed or uncitable matched rows are
`limited`. The query remains read-only and does not trigger DART collection.

## Testing Strategy

TDD tests exercise the public `call_tool` path with the real temporary database
models. Each failure is verified before implementation. Live immutable probes
then validate inventory, provisions, revenue, and contingencies against the
current local database.
