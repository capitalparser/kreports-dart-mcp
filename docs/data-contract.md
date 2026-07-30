# Runtime Data Contract

KReports release claims are properties of a selected SQLite artifact. They are
not inferred from README counts, HTTP 200 responses, or a successful test suite.

## Evidence layers

- Raw: optional DART XML/HTML retained under the raw policy.
- Evidence: receipt-bound excerpts, section identity, source hashes, and DART
  links or an explicit source-access limitation.
- Structured: normalized financial, audit, peer, group, and company-year quality
 rows consumed by the 34-tool catalog.

Every professional response must expose DART provenance or say why the source is
not locally accessible. A caller-supplied DART key is request-scoped and never
crosses response, error, log, or release-manifest boundaries.

## Artifact binding

`build-release-manifest` binds the DB file name, bytes, streaming SHA-256,
schema and required indexes, dataset manifest, inline raw count, catalog wire
hash, isolated real-dispatch catalog smoke, approved packaged golden-contract
hash, and current release-gate evidence. The golden hash binds the declarative
semantic contract; fixture-backed semantic execution remains a test-suite
proof, not a claim that live company values are frozen.
Unknown or missing fields and unsupported versions are rejected.

`verify-release-artifact` recomputes those facts from immutable/read-only SQLite
access. It does not trust a stored ready flag. Non-empty WAL state, DB drift, a
missing index, a catalog change, or a current named blocker fails verification.

Coverage years, markets, denominators, exclusions, and feature grades must be
read from that verified artifact.

## Audit materiality preparation

`prepare_audit_materiality_inputs` is a read-only professional workflow. It
returns three- or five-year financial benchmark observations, transparent
stability calculations, rate-range candidates, and methodology references;
it always remains `not_assessed` until an auditor explicitly selects and
approves a benchmark and rate. ISA 320 illustrations are labelled as
illustrations, while KReports candidate ranges and stability handling are
labelled as internal methodology.  The rate registry is versioned here; only
the 5% PBT illustration carries the ISA 320 A8 reference.  KReports does not
attribute its other candidate rates to ISA 320.

KReports internal methodology references use the stable locator
`docs/data-contract.md#audit-materiality-preparation` and their returned
`methodology_version`; they are not published as external URLs.

Amounts are candidate calculations only and use Decimal arithmetic. A missing
unit, incompatible scope, absent receipt provenance, or unavailable compact
cache prevents a source-backed amount from becoming a candidate. Cache absence
is explicitly not a claim that the source filing lacks the fact.

The stability registry classifies a three-year-or-longer series from its sample
coefficient of variation and maximum relative year-over-year change: low is at
most 15% for both measures, moderate is otherwise below the high trigger, and
high is CV above 50%, relative year-over-year change above 50%, a sign change,
or a fivefold discontinuity. These are KReports internal descriptive rules,
not ISA thresholds. High or insufficient series remain visible but have no
numeric candidate range.
