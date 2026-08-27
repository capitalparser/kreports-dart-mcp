# Runtime Data Contract

KReports release claims are properties of a selected SQLite artifact. They are
not inferred from README counts, HTTP 200 responses, or a successful test suite.

## Evidence layers

- Raw: optional DART XML/HTML retained under the raw policy.
- Evidence: receipt-bound excerpts, section identity, source hashes, and DART
  links or an explicit source-access limitation.
- Structured: normalized financial, audit, peer, group, and company-year quality
  rows consumed by the 33-tool public catalog.

Every professional response must expose DART provenance or say why the source is
not locally accessible. A caller-supplied DART key is request-scoped and never
crosses response, error, log, or release-manifest boundaries.

## Report package and source contract

KReports uses the following report meanings.

- `business_report` means the business-report body used for business overview,
  products and services, sales and purchase structure, risks, R&D, major
  contracts, governance, and other statutory business disclosures.
- `audit_report_package` means the independent auditor's report together with
  the attached audited financial statements, cash-flow and equity statements,
  complete financial-statement notes, significant accounting policies,
  significant estimates and judgments, and related schedules.

The canonical source for financial-statement notes, accounting policies, and
significant estimates and judgments is the audit report package. A legacy
business-report note row may be used only as an explicitly labelled fallback
when the corresponding audit report package evidence is unavailable. It must
retain its actual report type, receipt number, source status, and limitation;
it must not be described as primary audit-report evidence.

A structured row derived from one report type must not silently inherit the
provenance of another report type. Business year, receipt number, source type,
CFS/OFS basis, availability, and hash or locator information must remain bound
to the evidence used.

## Database access contract

- The maintained source and release databases have one designated database
  owner who performs collection, migration, repair, runtime export, and release
  verification.
- Other project contributors use the shared database in read-only mode. They may
  create disposable local test databases and fixtures for development.
- A data defect is repaired by fixing the collector, parser, mapping, or
  transformation and rebuilding the affected data. Contributors must not make
  untracked manual edits to the shared database.
- MCP runtime access remains read-only. A successful tool response never grants
  permission to mutate the release database.

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

Coverage years, markets, denominators, exclusions, source-package coverage, and
feature grades must be read from that verified artifact.

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
