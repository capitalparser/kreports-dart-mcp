# Semantic Layer

KReports keeps extraction, semantics, modeling, and judgment separate.

## Stable response semantics

All catalog dispatches return `AnswerEnvelopeV1` with readable text and explicit
data quality. Unknown fields are rejected. Missing local evidence is `missing`
or `limited`; it is never promoted to `usable` merely because the transport
returned HTTP 200.

Golden contracts stabilize shapes, provenance, quality, limitations, CFS/OFS
fallback, multi-entity QSC identity, modified opinions, multiple KAM items, and
incomplete-company behavior. Filing amounts are deliberately not golden because
amendments and dataset refreshes may legitimately change them.

## DCF boundary

A DCF pack has four distinct parts:

1. Source actuals tied to filing year, statement scope, and provenance.
2. Explicit analyst assumptions such as WACC and terminal growth.
3. Decimal model mechanics, including UFCF, terminal value, and the net-debt
   bridge.
4. Analyst judgment and limitations.

Missing assumptions are not backfilled from unrelated facts, and model output is
not an investment recommendation, fairness opinion, forecast approval, or audit
conclusion.

## Product readiness

Investor-core functions are ready only under the verified public-runtime gate.
Accounting-policy, audit-procedure, and group-audit functions may remain
conditional with their individual coverage and grade visible.

## Semantic peer context workflow

`semantic_peer_context_review` is a bounded, read-only host workflow for an
exact company and business year. It composes three existing local surfaces:

1. `get_semantic_company_context` for cached DART business report, audit
   report, note, disclosure, and financial evidence;
2. one customizable `peer_criteria` cohort selection; and
3. `compare_peer_accounting_notes`, which receives that same in-process cohort
   rather than resolving another peer universe.

The workflow returns a `context_pack.v1` alongside the semantic context and
peer-note comparison. It does not add a public MCP tool, call an external API,
search the web, backfill DART, write SQLite, or create a runtime artifact.
Existing `kreports://company/{corp_code}/{year}` and filing-evidence resources
remain local-DART resources; they do not store or fetch external IR/news.
The host-only adapter is intentionally not advertised as a public MCP prompt:
public prompt names must correspond to callable MCP surfaces.

### Evidence and answer boundary

The required source precedence is **DART → company IR → web/news → LLM**.

- `dart_filing` is primary evidence and may populate `confirmed facts` only.
- `company_ir` is a `management claims` bucket: it is company self-description,
  not a DART-confirmed fact.
- `web_news` is secondary `external context`, not a substitute for a filing.
- `llm_analysis` is `analysis`; it must cite known `source_id` values and never
  become an unlabelled fact source.

IR and web/news evidence is caller-supplied only. `context_pack.v1` rejects an
unlabelled external claim, a source-class/bucket mismatch, a duplicate
cross-bucket `source_id`, or an analysis citation that is unknown or ambiguous.
This makes a missing IR/news input explicit instead of silently triggering a
network fetch.

### Peer and statement-scope semantics

`peer_criteria` accepts the explainable strict, adaptive, or ranked profile;
the resulting `selection_policy` records inclusion logic, coverage, requested
year, and `fs_div_used`. The workflow selects this cohort once and reuses it
for its peer-note matrix. A comparison row is not comparable to another
independently selected cohort merely because it has the same industry label.

The selected CFS/OFS basis filters semantic financials and notes before they
enter the context pack. If the selected note basis is unavailable for a topic,
the note comparison can use its documented CFS-then-OFS fallback. In that case
the retained semantic note includes the same `fs_div_selection` (`requested`,
`used`, and `status`) and its `source_locator`; it is not silently reported as
unavailable or as an exact match.

Every excerpt retains a source locator and, where cached, receipt number,
checksum, externalization metadata, and availability. `summary_only` means the
full filing text is externalized, compressed, or truncated in the local cache.
`unavailable` and `missing` mean matching local evidence was not available;
they never prove that DART made no filing or that a company made no disclosure.

### Host usage

Use the adapter when the host can pass caller-supplied context without making
it part of the runtime database:

```python
from kreports.mcp.workflows import semantic_peer_context_review

result = semantic_peer_context_review(
    "005930",
    2024,
    topics=["risks", "leases"],
    peer_criteria={"mode": "strict", "prefix_len": 3},
    company_ir=[{
        "source_class": "company_ir",
        "source_id": "ir-q1-2025",
        "excerpt": "Caller-supplied management presentation excerpt",
    }],
    web_news=[{
        "source_class": "web_news",
        "source_id": "news-2025-01-01",
        "excerpt": "Caller-supplied external reporting excerpt",
    }],
)
```

Use `get_semantic_company_context` or `compare_peer_accounting_notes` directly
when only a single local-DART surface is needed. Their local-cache limitations
and provenance remain visible; neither call obtains IR or web/news evidence.

### Output budget

`build_mcp_context_pack` caps its JSON-safe adapter response at **60,000 UTF-8
bytes**. `semantic_peer_context_review` caps its complete workflow response at
**100,000 UTF-8 bytes**. When either boundary is reached, the response sets
`truncation.applied=true`, states `max_output_bytes`, and gives the applicable
budget reason. The bounded response retains source class, source ID, source
precedence, availability, and the compact provenance fields required to state
the limitation; it never fetches or stores more evidence to fill the budget.
