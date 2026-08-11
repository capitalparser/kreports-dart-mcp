# Public response and provenance contract

KReports responses are bound to a selected release artifact. README feature
lists and HTTP success responses are not coverage proof.

## Evidence states

- `available`: the requested source or structured fact was retrieved and bound
  to a filing/source locator;
- `summary_only`: a derived or summarized value is available but the requested
  original span is not included;
- `unverified`: a locator or external source is known, but the content has not
  been checked in the selected release;
- `unavailable`: no usable source was found for the requested company, year, or
  topic.

Clients must display these states and must not render `summary_only`,
`unverified`, or `unavailable` as confirmed original text.

## Provenance minimum

Evidence-bearing results should include company identity, filing year, DART
receipt/source locator, section or note topic, and the state above. Peer
comparisons should also return the selected cohort and the rule used to select
it.

## Release binding

Coverage claims are properties of the immutable runtime artifact selected by
the private core deployment. The private release pipeline verifies the artifact
before serving it; public clients should treat the artifact's coverage and
feature grades as authoritative.
