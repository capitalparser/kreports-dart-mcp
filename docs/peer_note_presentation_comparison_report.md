# Peer note presentation comparison

`compare_peer_accounting_policies` remains one of the frozen 34 MCP tools. Its
legacy default response is unchanged; its chatbot pack now renders the existing
subject-item cache, peer item coverage, and default selection basis without
pretending that a side-by-side topic comparison was requested. Supplying a topic
selector activates the presentation-comparison extension.

- Topic: `item_key` and/or `keyword`; rows show heading, a maximum 400-character
  excerpt, length/hash, and cache-missing status distinct from filing absence.
- Peer selection: `auditor`, `investor`, or `balanced` profiles; bounded weights
  and asset-size range; exact explicit include/exclude selectors. The response
  exposes the candidate universe, selected/excluded status, component scores,
  weighted contributions, missing dimensions, and override provenance.
- Candidate universe: adaptive KSIC-prefix and sector filters only. Market is
  display context, and business text is not indexed for candidate selection.
- Financial similarity: cached `financials` values are internal screening
  inputs, not receipt-proven filing evidence. Size averages the available
  positive revenue/total-assets similarities (one dimension is sufficient),
  and visible ratio values are rounded to four decimal places.
- These profiles and weights are internal screening heuristics, not auditing or
  accounting standards. A supplied weight map is complete: omitted components
  have zero weight. More direct includes than `peer_limit` fail closed.
- Evidence: a DART link is emitted only where the cached, exact 14-digit receipt
  equals that company's latest matching annual filing for the requested year.
  Contaminated, older, and cross-company receipts remain uncitable.

The comparison is a cached-text screening view, not an accounting-treatment
conclusion. A selected topic becomes limited if a final peer is cache-missing or
has only an unproven receipt. Without an `item_key` or `keyword`, custom peer
selection returns a bounded item inventory, never policy bodies. Broad keyword
results have total/per-company caps and report truncation. Business-text overlap
and market-cap threshold customization are not currently indexed and are
explicitly reported as unavailable rather than scored.

Verification was fixture-only: no live database, DART API, or network filing
request was used.
