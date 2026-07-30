# Peer note presentation comparison

`compare_peer_accounting_policies` remains one of the frozen 34 MCP tools. Its
legacy default response is unchanged. Supplying a topic selector or a peer
selection customization activates the presentation-comparison extension.

- Topic: `item_key` and/or `keyword`; rows show heading, a maximum 400-character
  excerpt, length/hash, and cache-missing status distinct from filing absence.
- Peer selection: `auditor`, `investor`, or `balanced` profiles; bounded weights
  and asset-size range; exact explicit include/exclude selectors. The response
  exposes the candidate universe, selected/excluded status, component scores,
  weighted contributions, missing dimensions, and override provenance.
- Evidence: a DART link is emitted only where the cached, exact 14-digit receipt
  equals that company's latest matching annual filing for the requested year.
  Contaminated, older, and cross-company receipts remain uncitable.

The comparison is a cached-text screening view, not an accounting-treatment
conclusion. Business-text overlap and market-cap threshold customization are not
currently indexed and are explicitly reported as unavailable rather than scored.

Verification was fixture-only: no live database, DART API, or network filing
request was used.
