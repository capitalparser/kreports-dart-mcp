# Database Evidence, Provenance, and Lifecycle Design

## Approval and Purpose

The user approved a code-and-rehearsal-only database hardening boundary. This
slice may add migrations, models, local backfills, and lifecycle safeguards,
then validate them on temporary databases and an APFS clone. It does not
authorize any write, migration, checkpoint, vacuum, or cleanup operation on
the live `kreports.db`.

The current database contract already has append-only revisions through
`20260711_08_group_audit_graph`, typed audit-fee summary fields, compact
financial facts, and a company-year quality ledger. The remaining gaps are:

- audit-fee source observations survive only in a bounded JSON compatibility
  field, so individual claims and corrections cannot be queried or constrained;
- compact financial values retain account identity but not their value source,
  period semantics, unit, or the exact basis of the filing citation displayed
  to a professional user;
- company-year quality rows do not expose a deterministic fingerprint or
  bounded evidence summary, so consumers cannot tell whether a persisted grade
  reflects the current derived evidence;
- the stdio MCP launcher does not explicitly dispose the shared SQLAlchemy
  engine on normal EOF, cancellation, or supported process signals.

The design normalizes evidence while preserving every existing summary and MCP
contract. It distinguishes a citation matched by company and business year
from direct receipt-level lineage. It also keeps operational maintenance
separate from product data semantics.

## Goals

- Preserve every audit-fee/hour observation as a queryable, immutable claim.
- Retain correction history and deterministically identify the current claim
  for a source slot.
- Continue serving `audit_fees` as the stable one-row-per-company-year summary.
- Add explicit, bounded provenance to `financial_facts_compact`.
- Label annual filing citations honestly as matched citation anchors, not as
  proof that the financial API row came from that receipt.
- Make persisted company-year quality reproducible and freshness-aware.
- Explicitly release MCP database handles on controlled shutdown paths.
- Provide idempotent local backfills that do not call DART or erase verified
  evidence when a newer source is missing or erroneous.
- Prove migrations and backfills first on temporary SQLite databases and then
  on an APFS clone while keeping the live database immutable.

## Non-Goals

- No operation on the live database.
- No DART API call, remote fetch, or source-document repair.
- No change to the public `usable`, `limited`, `missing`, and `error`
  semantics.
- No removal or rename of existing `audit_fees`,
  `financial_facts_compact`, or `company_year_quality` fields.
- No claim that an annual filing citation is direct endpoint lineage when the
  local source tables do not retain a receipt number.
- No automatic `VACUUM`, `REINDEX`, journal-mode change, WAL checkpoint, or
  sidecar deletion.
- No database-size optimization in this slice. Freelist observations remain an
  operator advisory.
- No push, pull request, merge, production migration, or deployment.

## Alternatives Considered

### A. Normalized Evidence with Backward-Compatible Summaries

Add a normalized audit observation table, additive compact-financial
provenance, and additive quality freshness fields. Existing summary rows and
MCP response fields remain stable.

This is the approved direction because it preserves traceability and supports
database constraints without breaking existing consumers.

### B. Summary-Table Columns Only

Add more columns directly to `audit_fees` and
`financial_facts_compact` without storing individual observations.

This has a smaller migration footprint but cannot preserve multiple source
claims or corrections. It is insufficient for an auditor reviewing why a
compatibility value changed.

### C. Continue Using JSON as the Source of Truth

Keep `source_observations_json` as the only audit-fee provenance store and add
quality metadata around it.

This has the least initial code change, but JSON entries cannot be reliably
indexed, constrained, or joined. A bounded field also cannot be the
authoritative history store.

## Architecture and Data Flow

```text
cached or endpoint observation
        |
        v
audit_fee_observations  -- immutable claims and supersession
        |
        v
deterministic merge
        |
        v
audit_fees              -- stable compatibility summary and bounded JSON view

financial_facts / financials
        |
        v
financial_facts_compact -- value source, unit, period, citation basis
        |
        +-------------------------------+
                                        v
KAM / procedure / policy / group evidence --> company_year_quality
                                             status, grades, evidence summary,
                                             deterministic input fingerprint
                                                        |
                                                        v
                                               MCP/API/read models
```

Collectors and maintenance jobs own evidence persistence. Analysis code reads
normalized or compact evidence. MCP, API, and dashboard layers do not implement
their own database semantics.

## Revision 09: Normalized Audit-Fee Observations

### Table Contract

Revision `20260711_09_audit_fee_observations` creates
`audit_fee_observations` with the following logical contract:

| Column | Contract |
|---|---|
| `observation_hash` | SHA-256 of canonical observation payload; primary key |
| `source_slot_hash` | SHA-256 of company, year, source class, receipt, and period |
| `corp_code`, `bsns_year` | company-year identity |
| `source_class` | existing typed source class |
| `source_rcept_no`, `source_period` | nullable source identity fields |
| `contract_fee_m`, `contract_hours` | independently nullable contract values |
| `actual_fee_m`, `actual_hours` | independently nullable actual values |
| `auditor_nm` | source-stated auditor name |
| `availability_status`, `quality_status` | existing typed evidence states |
| `displayed_unit` | unit stated by the source before normalization |
| `raw_values_json` | deterministic, bounded raw value map |
| `source_status`, `source_message`, `source_eligibility` | endpoint/source outcome |
| `limitations_json` | deterministic, bounded limitations |
| `parser_version` | parser contract that produced the observation |
| `is_current` | whether this is the current claim for its source slot |
| `supersedes_hash` | prior current observation in the same source slot |
| `observed_at` | local observation time; not part of semantic identity |

Required indexes are:

- `(corp_code, bsns_year)`;
- `(source_rcept_no)`;
- `(bsns_year, quality_status)`;
- a partial unique index on `source_slot_hash` where `is_current = 1`.

Nullable composite columns are not used as the observation identity because
SQLite permits multiple `NULL` values in an ordinary unique constraint.

### Canonical Identity

The canonical observation payload contains all source-stated and normalized
semantic fields, but excludes database IDs, `observed_at`, current-state flags,
and supersession links. JSON objects use sorted keys, compact separators, and
UTF-8. Limitations are deduplicated and sorted. Raw values remain strings.

The source-slot payload contains normalized `corp_code`, integer
`bsns_year`, `source_class`, and empty-string-normalized receipt and period.

An identical payload in an existing slot is an idempotent no-op except for a
safe last-seen diagnostic if one is later required. A different payload in the
same slot is inserted as a new immutable observation and supersedes the prior
current row in the same transaction. Historical rows are never rewritten into
different claims or deleted.

### Summary Projection

`audit_fees` remains the public compatibility table with its existing unique
key `(corp_code, bsns_year)`. After observations are persisted:

1. load current observations for the company-year in deterministic order;
2. rehydrate the existing `AuditFeeObservation` value objects;
3. run the existing merge precedence and conflict logic;
4. update the typed summary fields;
5. derive `source_observations_json` as a bounded compatibility view.

Only current observations participate in summary selection. The normalized
table, not the bounded JSON view, is the authoritative history after the
backfill completes.

Missing, ineligible, transport-error, and parse-error observations may add
status evidence but may not erase a previously verified non-null compatibility
value. Existing fail-closed merge behavior remains in force.

### Legacy JSON Backfill

The migration creates schema only. A separate explicit maintenance command
backfills existing `audit_fees.source_observations_json` rows.

For each company-year transaction:

- parse the JSON as a bounded list using the existing typed parser;
- reject malformed container shapes, identity mismatches, and invalid typed
  entries without changing that company-year summary;
- preserve stored list order when multiple legacy entries occupy the same
  source slot, so the later entry retains the current position;
- insert observations by canonical hash and apply supersession rules;
- recompute the summary from normalized current observations;
- verify that verified legacy fee/hour values were not erased;
- commit only when the normalized and compatibility projections agree.

The command reports processed rows, inserted observations, unchanged
observations, superseded observations, malformed rows, and failed
company-years. It supports year bounds and dry-run. Re-running it must produce
zero semantic changes.

## Revision 10: Compact Financial Provenance

Revision `20260711_10_financial_compact_provenance` adds nullable or
default-safe fields to `financial_facts_compact`:

| Column | Values and meaning |
|---|---|
| `source_table` | `financial_facts` or `financials` |
| `unit` | canonical stored amount unit, currently `KRW` when proven by collector contract |
| `period_type` | `instant` or `duration`, derived from the metric registry |
| `citation_rcept_no` | annual filing receipt selected by the citation matcher |
| `citation_report_nm` | matched annual report name |
| `citation_basis` | `company_year_annual_filing_match` or `uncitable` |
| `quality_status` | `usable` or `limited` for this compact row |

The compact row builder remains the only writer for these fields.

### Value Lineage

For authoritative XBRL-derived rows, `source_table` is `financial_facts`.
For summary fallback rows, it is `financials`. Existing
`source_account_id` and `source_account_nm` continue to identify the selected
account or fallback field.

`period_type` comes from an explicit metric-registry attribute. Balance-sheet
stock metrics such as assets are `instant`; flow metrics such as revenue are
`duration`. The builder fails closed for an unregistered period type rather
than guessing from a Korean account label.

`unit = KRW` is set only where the collector/storage contract establishes that
the stored integer is Korean won. If the source contract cannot prove the
unit, the value remains null and the row is `limited`.

### Citation Semantics

The existing annual filing resolver joins independently stored facts and
disclosures by company, business year, annual report pattern, and optional
financial-statement division. That is a valid citation anchor but not direct
row-to-receipt lineage.

Therefore:

- a successful match is labeled
  `citation_basis = company_year_annual_filing_match`;
- no match produces `citation_basis = uncitable`, null citation fields, and
  `quality_status = limited`;
- user-facing source rendering must not call this a direct endpoint receipt;
- a compact row with an amount but no citation is retained and explicitly
  limited, not silently deleted.

The builder resolves citations in bounded batches and writes the selected
receipt and report name into the compact row. Read paths prefer these persisted
fields and may use the existing resolver only as backward compatibility for
databases before revision 10.

### Rebuild Behavior

The existing scoped compact rebuild continues deleting and reconstructing only
the requested registered metrics and years. Each rebuilt row includes all new
provenance fields in the same upsert. The authoritative
`financial_facts` source continues to outrank `financials` fallback.

A second rebuild over unchanged source tables must preserve all semantic
fields. Timestamps may change and are excluded from the semantic digest.

## Revision 11: Quality Freshness

Revision `20260711_11_company_year_quality_freshness` adds:

| Column | Contract |
|---|---|
| `input_fingerprint` | 64-character SHA-256 of deterministic quality inputs |
| `evidence_summary_json` | bounded deterministic evidence/status summary |

`updated_at` remains the computation timestamp; no duplicate `computed_at`
column is added.

### Evidence Summary

The summary contains only bounded facts already used by the grade computation:

- status for financial core, auditor, audit fee, policy, KAM, audit procedure,
  and group audit;
- resulting investor, auditor, and group-audit grades;
- sorted blockers;
- the quality contract version;
- bounded evidence counts or source-availability flags needed to explain those
  statuses.

It contains no raw filing body, unbounded list, credential, local path, or
volatile timestamp.

### Fingerprint

The fingerprint is SHA-256 over canonical JSON containing the evidence summary
and exact quality outputs. Keys and blockers are sorted. Volatile timestamps
are excluded.

Consequences:

- unchanged evidence and code contract produce the same fingerprint;
- changed evidence, blockers, grades, or quality version produce a different
  fingerprint;
- a row with a blank/default fingerprint is readable for backward
  compatibility but explicitly freshness-limited;
- `company_year_quality()` exposes the two additive fields without changing
  existing keys.

The existing `rebuild_company_year_quality()` remains the sole grade writer.
It computes the evidence summary and fingerprint in the same transaction as
the status and grade fields.

## Backfill Ordering

The local evidence rebuild order is:

1. apply append-only migrations 09 through 11;
2. backfill normalized audit-fee observations from local compatibility JSON;
3. rebuild compact financial facts with persisted provenance;
4. run any already-approved KAM and procedure local reconstruction;
5. rebuild company-year quality last.

No step invokes DART. A failure stops dependent later steps. An upstream
limited or missing result remains an explicit quality input rather than being
promoted by the existence of a new schema.

## MCP Database Lifecycle

The shared engine receives one idempotent disposal helper. The stdio launcher
uses `try/finally` so normal EOF, MCP server return, and task cancellation
dispose pooled connections. On POSIX, the launcher handles `SIGINT` and
`SIGTERM` by requesting cancellation through the running event loop and then
following the same `finally` path.

The signal handler does not perform SQL, checkpoint, vacuum, or file deletion.
Repeated signals may fall back to ordinary process termination. Shutdown is
bounded and must not hang waiting for a client.

Tests run only against a temporary database in a subprocess. They prove:

- a database connection opened by an MCP call is no longer held after EOF;
- supported signal shutdown exits within the test timeout and releases open
  handles;
- no new write is issued by the shutdown path;
- an existing sidecar is not deleted by application code.

SQLite may retain a stale `-shm` file after a read-only close. Sidecar deletion
is therefore not an acceptance criterion. The correct lifecycle guarantee is
that KReports releases its handles and does not mutate or manually unlink the
sidecar.

## Failure and Transaction Semantics

- Schema migrations remain append-only and checksum-verified.
- Schema migrations do not perform large data backfills.
- Backfills are explicit collector-mode commands and reject readonly mode.
- Company-year audit observation writes are transactional.
- Malformed provenance does not partially update a summary row.
- Hash or slot mismatch is an error, not an automatic repair.
- Missing citations remain `uncitable`; they do not borrow a receipt from a
  different company, year, or report type.
- Quality rebuild cannot run before its required upstream local rebuilds in the
  integrated rehearsal.
- Reports distinguish schema success, data coverage, and MCP usability.

## Worktree and Integration Strategy

The shared migration registry and ORM models make fully independent schema
branches likely to conflict. Work proceeds in two stages.

### Stage 1: Schema Foundation

Worktree `codex/db-schema-foundation` owns:

- revisions 09 through 11;
- ORM columns and the normalized observation model;
- schema and migration-idempotency tests;
- stable helper interfaces needed by the feature slices.

It is reviewed and integrated before feature worktrees are created from the
new integration HEAD.

### Stage 2: Parallel Feature Slices

Three linked worktrees then start from the same schema-foundation commit:

| Worktree | Ownership |
|---|---|
| `codex/db-audit-observations` | canonical identity, normalized persistence, legacy JSON backfill, summary projection |
| `codex/db-financial-provenance` | metric period contract, compact provenance rebuild, citation reads |
| `codex/db-quality-lifecycle` | evidence fingerprint, quality rebuild/read contract, MCP engine shutdown |

Implementation agents use Terra High. Each slice follows test-driven
development, receives an independent review, and is integrated by commit rather
than by copying files. No agent pushes or modifies the live database.

## Verification Strategy

### Focused Tests

- fresh schema and revision 08-to-11 migration;
- migration checksum and second-run idempotency;
- audit observation canonicalization and source-slot supersession;
- legacy JSON backfill, malformed JSON rollback, and no verified-value erasure;
- deterministic summary selection and conflict preservation;
- compact authoritative/fallback source labeling;
- instant/duration registry behavior and unknown-period fail-closed behavior;
- matched citation versus uncitable output;
- stable quality fingerprint and changed-evidence fingerprint;
- backward-compatible quality read behavior;
- stdio EOF and supported signal cleanup on a temporary WAL database.

### Integrated Tests

- related collector, analysis, MCP, resource, and quality suites;
- default `uv run pytest` comparison against the pre-change baseline;
- Ruff on changed Python files;
- real migration and complete local rebuild on a disposable temporary database;
- the existing APFS rehearsal path against a retained clone;
- live database identity and SHA-256 equality before and after clone work.

Tests may mock failure boundaries, but completion requires real SQLite
migrations, real local backfills, real query paths, and a real subprocess
shutdown test. The APFS clone run is reported separately from unit and
integration tests.

## Acceptance Criteria

The slice is complete only when:

- revisions 09 through 11 apply once and verify by checksum;
- revision 08 databases migrate without destructive table replacement;
- normalized audit observations preserve source claims and correction history;
- legacy JSON backfill is idempotent and does not erase verified values;
- `audit_fees` remains backward compatible and agrees with current normalized
  observations;
- compact financial rows identify value source, period semantics, unit basis,
  citation basis, and quality;
- annual filing matches are never described as direct endpoint lineage;
- company-year quality exposes a stable fingerprint and bounded evidence
  summary;
- MCP controlled shutdown releases KReports-owned database handles without
  deleting sidecars;
- focused, related, default-suite, and Ruff checks introduce no regression;
- the APFS clone passes migration, rebuild, integrity, MCP, and idempotency
  checks;
- the live database path, inode, size, modification time, and SHA-256 remain
  unchanged;
- independent review reports no unresolved Critical or Important issue.

## Production Decision Boundary

Successful implementation and clone rehearsal prove that the additive schema
and local backfills are technically safe on representative data. They do not
authorize a live migration.

A later production decision must separately review:

- migration and backfill duration;
- normalized observation counts, malformed legacy rows, and conflict
  distribution;
- financial citation and unit coverage;
- quality fingerprint population and grade distribution;
- database allocated-space growth and free-space reserve;
- MCP before/after output for auditor and investor questions;
- backup, rollback, maintenance window, and process coordination.
