# Audit Fee Observation Store Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make normalized audit-fee observations the authoritative claim history while preserving the existing one-row summary and bounded MCP compatibility view.

**Architecture:** A focused database store owns canonical hashing, source-slot supersession, and current-claim loading. The collector first promotes any legacy JSON claims when normalized rows are absent, persists new claims transactionally, and then uses the existing merge rules to project `audit_fees`.

**Tech Stack:** Python 3.12, SQLAlchemy, SQLite, Typer, pytest, Ruff, uv

## Global Constraints

- Start from the reviewed schema-foundation commit containing revisions 09–11.
- Do not open or modify the live database.
- Do not call DART or any network endpoint.
- Keep `audit_fees` and `source_observations_json` backward compatible.
- Never erase a verified non-null fee/hour because of missing or error evidence.
- Use company-year transactions and deterministic canonical JSON.
- Do not push, open a pull request, merge, or deploy.

---

## File Structure

- Modify `kreports/collector/audit_fee_sources.py`: canonical payload, hash, and parser-version contract.
- Create `kreports/db/audit_fee_observation_store.py`: immutable claim persistence and current-claim loading.
- Modify `kreports/collector/audit_fee_collector.py`: transactional promotion, persistence, and summary projection.
- Create `kreports/maintenance/audit_fee_observation_backfill.py`: explicit local JSON backfill.
- Modify `kreports/analysis/audit_reporting.py`: prefer normalized current claims when available and retain summary fallback.
- Modify `kreports/cli/main.py`: guarded dry-run/write command.
- Modify `kreports/release_artifact.py`: carry normalized claims into prepared runtime data when the table is present.
- Create `tests/test_audit_fee_observations.py`: canonical/store/collector/backfill/read tests.
- Modify `tests/test_audit_fee_collector.py`, `tests/test_audit_fee_availability.py`, and `tests/test_release_artifact.py`.

### Task 1: Canonical Observation Identity

**Files:**
- Modify: `kreports/collector/audit_fee_sources.py`
- Create: `tests/test_audit_fee_observations.py`

**Interfaces:**
- Produces constant
  `AUDIT_FEE_OBSERVATION_PARSER_VERSION = "v1"`.
- Produces
  `canonical_observation_payload(observation: AuditFeeObservation) -> dict[str, object]`.
- Produces
  `observation_hash(observation: AuditFeeObservation) -> str`.
- Produces
  `source_slot_hash(observation: AuditFeeObservation) -> str`.

- [ ] **Step 1: Write failing canonicalization tests**

```python
def test_observation_hash_is_semantic_and_order_independent():
    left = AuditFeeObservation(
        corp_code="00126380",
        bsns_year=2025,
        source_class="cached_business_report",
        actual_fee_m=1_000,
        actual_hours=2_000,
        source_rcept_no="20260310002820",
        source_period="2025",
        raw_values={"hours": "2,000", "fee": "1,000"},
        limitations=("second", "first", "first"),
    )
    right = replace(
        left,
        raw_values={"fee": "1,000", "hours": "2,000"},
        limitations=("first", "second"),
    )

    assert canonical_observation_payload(left) == canonical_observation_payload(right)
    assert observation_hash(left) == observation_hash(right)
    assert len(observation_hash(left)) == 64
```

Add tests proving a changed normalized amount changes `observation_hash`, while
a changed amount with the same company/year/class/receipt/period leaves
`source_slot_hash` unchanged. Add fail-closed size tests for more than 32 raw
keys, raw keys longer than 80 characters, raw values longer than 500
characters, more than 20 limitations, limitations longer than 300 characters,
source messages longer than 500 characters, and a canonical payload exceeding
32 KiB.

- [ ] **Step 2: Run the tests to verify RED**

```bash
uv run pytest tests/test_audit_fee_observations.py -q
```

Expected: collection error because the three helpers do not exist.

- [ ] **Step 3: Implement canonicalization and hashes**

Extend the frozen value object:

```python
parser_version: str = AUDIT_FEE_OBSERVATION_PARSER_VERSION
```

Implement canonicalization:

```python
def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_observation_payload(
    observation: AuditFeeObservation,
) -> dict[str, object]:
    payload = observation.to_dict()
    payload["limitations"] = sorted(set(observation.limitations))
    payload["raw_values"] = {
        str(key): None if value is None else str(value)
        for key, value in sorted(observation.raw_values.items())
    }
    return payload


def observation_hash(observation: AuditFeeObservation) -> str:
    payload = _canonical_json(canonical_observation_payload(observation))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def source_slot_hash(observation: AuditFeeObservation) -> str:
    slot = {
        "corp_code": observation.corp_code.strip(),
        "bsns_year": int(observation.bsns_year),
        "source_class": observation.source_class.strip(),
        "source_rcept_no": (observation.source_rcept_no or "").strip(),
        "source_period": (observation.source_period or "").strip(),
    }
    return hashlib.sha256(_canonical_json(slot).encode("utf-8")).hexdigest()
```

Reject blank company/source class and non-positive years before hashing.
Reject oversized raw/source fields using the tested fixed limits; do not
truncate them into a colliding semantic hash.

- [ ] **Step 4: Run focused tests**

```bash
uv run pytest tests/test_audit_fee_observations.py tests/test_audit_fee_availability.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kreports/collector/audit_fee_sources.py tests/test_audit_fee_observations.py
git commit -m "feat: define audit fee observation identity"
```

### Task 2: Immutable Observation Store

**Files:**
- Create: `kreports/db/audit_fee_observation_store.py`
- Modify: `tests/test_audit_fee_observations.py`

**Interfaces:**
- Consumes: `AuditFeeObservation`, `observation_hash`, `source_slot_hash`,
  and foundation model `AuditFeeObservationRecord`.
- Produces dataclass `AuditFeeObservationWriteResult` with integer fields
  `inserted`, `unchanged`, and `superseded`.
- Produces
  `persist_audit_fee_observations(session: Session, observations: Sequence[AuditFeeObservation], *, observed_at: datetime | None = None) -> AuditFeeObservationWriteResult`.
- Produces
  `load_current_audit_fee_observations(session: Session, *, corp_code: str, bsns_year: int) -> list[AuditFeeObservation]`.

- [ ] **Step 1: Write failing store tests**

Test these exact cases:

```python
assert first == AuditFeeObservationWriteResult(
    inserted=1, unchanged=0, superseded=0
)
assert second == AuditFeeObservationWriteResult(
    inserted=0, unchanged=1, superseded=0
)
assert correction == AuditFeeObservationWriteResult(
    inserted=1, unchanged=0, superseded=1
)
```

After correction, assert the old row is `is_current=False`, the new row points
to it with `supersedes_hash`, exactly one current row exists for the slot, and
another receipt/period slot remains current independently.

- [ ] **Step 2: Run the tests to verify RED**

```bash
uv run pytest tests/test_audit_fee_observations.py -q
```

Expected: FAIL because the store module is missing.

- [ ] **Step 3: Implement transactional store behavior**

For each observation in caller order:

```python
semantic_hash = observation_hash(observation)
slot_hash = source_slot_hash(observation)
known = session.get(AuditFeeObservationRecord, semantic_hash)
if known is not None:
    unchanged += 1
    continue
current = (
    session.query(AuditFeeObservationRecord)
    .filter_by(source_slot_hash=slot_hash, is_current=True)
    .one_or_none()
)
if current is not None:
    current.is_current = False
    superseded += 1
session.add(
    AuditFeeObservationRecord(
        observation_hash=semantic_hash,
        source_slot_hash=slot_hash,
        supersedes_hash=current.observation_hash if current else None,
        is_current=True,
        observed_at=observed_at or datetime.now(timezone.utc),
        **_record_fields(observation),
    )
)
```

Flush after clearing the old current row and before inserting the successor so
the partial unique constraint remains satisfied. Do not commit inside the
store. Load current rows ordered by source class, source period, receipt, and
observation hash, then rehydrate through `observation_from_dict`.

- [ ] **Step 4: Run focused tests**

```bash
uv run pytest tests/test_audit_fee_observations.py -q
```

Expected: PASS, including SQLite partial-unique enforcement.

- [ ] **Step 5: Commit**

```bash
git add kreports/db/audit_fee_observation_store.py tests/test_audit_fee_observations.py
git commit -m "feat: persist immutable audit fee claims"
```

### Task 3: Collector Promotion and Compatibility Projection

**Files:**
- Modify: `kreports/collector/audit_fee_collector.py`
- Modify: `tests/test_audit_fee_collector.py`
- Modify: `tests/test_audit_fee_observations.py`

**Interfaces:**
- Consumes: the store interfaces from Task 2 and existing
  `merge_audit_fee_observations`.
- Produces: unchanged public function
  `upsert_audit_fee_observations(observations: list[AuditFeeObservation], **compatibility_fields) -> None`.

- [ ] **Step 1: Write failing transition and projection tests**

Seed an `audit_fees` row with legacy JSON but no normalized rows. Call the
collector with one new claim, then assert:

```python
assert normalized_count == legacy_claim_count + 1
assert row.audit_fee_m == expected_verified_fee
assert row.audit_hours == expected_verified_hours
assert json.loads(row.source_observations_json)
assert row.non_audit_fee_m == seeded_non_audit_fee
```

Add tests proving:

- legacy JSON is promoted before the first post-revision collector write;
- current normalized observations, not historical rows, feed the merge;
- a missing/transport/parse error does not erase verified values;
- repeated identical collector input adds no normalized row;
- company-year identity mismatch rolls back all writes.

- [ ] **Step 2: Run tests to verify RED**

```bash
uv run pytest tests/test_audit_fee_collector.py tests/test_audit_fee_observations.py -q
```

Expected: FAIL because the collector is still JSON-authoritative.

- [ ] **Step 3: Replace the persistence source of truth**

Within the existing `get_session()` transaction:

```python
current = load_current_audit_fee_observations(
    session,
    corp_code=corp_code,
    bsns_year=bsns_year,
)
if not current and existing is not None:
    legacy = _persisted_observations(existing)
    if legacy:
        persist_audit_fee_observations(session, legacy)
persist_audit_fee_observations(session, observations)
current = load_current_audit_fee_observations(
    session,
    corp_code=corp_code,
    bsns_year=bsns_year,
)
merged = merge_audit_fee_observations(current, previous=previous)
```

Keep the current error-state overlay and non-null preservation logic. Derive
`source_observations_json` from current normalized claims as a bounded view.

- [ ] **Step 4: Run collector regressions**

```bash
uv run pytest tests/test_audit_fee_collector.py tests/test_audit_fee_availability.py tests/test_audit_fee_observations.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kreports/collector/audit_fee_collector.py tests/test_audit_fee_collector.py tests/test_audit_fee_observations.py
git commit -m "feat: project audit fee summaries from claims"
```

### Task 4: Explicit Legacy Backfill and CLI

**Files:**
- Create: `kreports/maintenance/audit_fee_observation_backfill.py`
- Modify: `kreports/cli/main.py`
- Modify: `tests/test_audit_fee_observations.py`

**Interfaces:**
- Produces
  `backfill_audit_fee_observations(*, year_from: int | None = None, year_to: int | None = None, dry_run: bool = False) -> dict[str, int | bool | None]`.

- [ ] **Step 1: Write failing backfill tests**

Use file-backed temporary SQLite and assert:

```python
assert dry_run["dry_run"] is True
assert normalized_count_after_dry_run == 0
assert write["processed_company_years"] == 1
assert write["inserted_observations"] == 2
assert rerun["inserted_observations"] == 0
assert rerun["semantic_changes"] == 0
```

Add malformed non-list JSON, typed-entry identity mismatch, and same-slot
ordered-correction cases. Each invalid company-year must retain its original
summary unchanged and increment `failed_company_years`.

- [ ] **Step 2: Run tests to verify RED**

```bash
uv run pytest tests/test_audit_fee_observations.py -q
```

Expected: import failure for the missing maintenance module.

- [ ] **Step 3: Implement the bounded local backfill**

Select summary rows ordered by company and year with optional bounds. For each
row, open a separate session transaction, parse at most 20 typed entries using
`observation_from_dict`, validate every identity, and replay stored order.
Dry-run performs validation and hash calculation but calls no persistence
function.

Return these fixed counters:

```python
{
    "year_from": year_from,
    "year_to": year_to,
    "dry_run": dry_run,
    "processed_company_years": processed,
    "inserted_observations": inserted,
    "unchanged_observations": unchanged,
    "superseded_observations": superseded,
    "malformed_company_years": malformed,
    "failed_company_years": failed,
    "semantic_changes": inserted + superseded,
}
```

Add `backfill-audit-fee-observations` with `--year-from`, `--year-to`, and
`--dry-run`. The command calls `init_db()` only in collector mode and prints the
bounded JSON result.

- [ ] **Step 4: Run backfill and CLI tests**

```bash
uv run pytest tests/test_audit_fee_observations.py tests/test_runtime_write_policy.py -q
```

Expected: PASS and readonly mode fails before any write.

- [ ] **Step 5: Commit**

```bash
git add kreports/maintenance/audit_fee_observation_backfill.py kreports/cli/main.py tests/test_audit_fee_observations.py tests/test_runtime_write_policy.py
git commit -m "feat: backfill normalized audit fee claims"
```

### Task 5: Professional Reads and Runtime Artifact

**Files:**
- Modify: `kreports/analysis/audit_reporting.py`
- Modify: `kreports/release_artifact.py`
- Modify: `tests/test_audit_fee_availability.py`
- Modify: `tests/test_release_artifact.py`

**Interfaces:**
- Consumes: normalized current claims when the table is present.
- Produces: unchanged professional result shape and a runtime artifact that
  retains the current normalized claim set.

- [ ] **Step 1: Write failing read and export tests**

Seed a summary JSON that differs from the normalized current claims. Assert the
professional audit-fee result uses normalized current claims and still emits
the existing bounded result keys. Export a prepared runtime DB and assert its
current-claim count and summary values agree.

- [ ] **Step 2: Run tests to verify RED**

```bash
uv run pytest tests/test_audit_fee_availability.py tests/test_release_artifact.py -q
```

Expected: FAIL because both paths currently rely on the summary table only.

- [ ] **Step 3: Prefer normalized claims without breaking old databases**

Inspect the database for `audit_fee_observations`. When present, load current
rows for the requested company-years and derive the bounded observation view.
When absent, use `source_observations_json`. Do not catch arbitrary SQL errors
as schema absence.

Include `audit_fee_observations` in runtime export only when the source table
exists. Copy all current and historical rows required by the selected
company/year population and verify every exported summary has the same
current-claim merge result.

- [ ] **Step 4: Run related and full verification**

```bash
uv run pytest tests/test_audit_fee_observations.py tests/test_audit_fee_collector.py tests/test_audit_fee_availability.py tests/test_auditor_peer_tools.py tests/test_standard_audit_hours_inputs.py tests/test_company_year_quality.py tests/test_release_artifact.py -q
uv run pytest
uv run ruff check kreports/collector/audit_fee_sources.py kreports/db/audit_fee_observation_store.py kreports/collector/audit_fee_collector.py kreports/maintenance/audit_fee_observation_backfill.py kreports/analysis/audit_reporting.py kreports/release_artifact.py kreports/cli/main.py tests/test_audit_fee_observations.py
```

Expected: focused and full suites pass; Ruff reports no issue.

- [ ] **Step 5: Commit**

```bash
git add kreports/analysis/audit_reporting.py kreports/release_artifact.py tests/test_audit_fee_availability.py tests/test_release_artifact.py
git commit -m "feat: expose normalized audit fee provenance"
```
