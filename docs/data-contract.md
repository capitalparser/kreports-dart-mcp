# Runtime Data Contract

KReports release claims are properties of a selected SQLite artifact. They are
not inferred from README counts, HTTP 200 responses, or a successful test suite.

## Evidence layers

- Raw: optional DART XML/HTML retained under the raw policy.
- Evidence: receipt-bound excerpts, section identity, source hashes, and DART
  links or an explicit source-access limitation.
- Structured: normalized financial, audit, peer, group, and company-year quality
  rows consumed by the 32-tool catalog.

Every professional response must expose DART provenance or say why the source is
not locally accessible. A caller-supplied DART key is request-scoped and never
crosses response, error, log, or release-manifest boundaries.

## Artifact binding

`build-release-manifest` binds the DB file name, bytes, streaming SHA-256,
schema and required indexes, dataset manifest, inline raw count, catalog wire
hash, catalog-wide tool contract, golden contract hash, and current release-gate
evidence.
Unknown or missing fields and unsupported versions are rejected.

`verify-release-artifact` recomputes those facts from immutable/read-only SQLite
access. It does not trust a stored ready flag. Non-empty WAL state, DB drift, a
missing index, a catalog change, or a current named blocker fails verification.

Coverage years, markets, denominators, exclusions, and feature grades must be
read from that verified artifact.
