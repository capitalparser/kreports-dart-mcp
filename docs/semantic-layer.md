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
