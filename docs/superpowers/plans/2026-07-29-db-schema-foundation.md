# Database Schema Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add append-only revisions 09 through 11 and matching ORM contracts without moving or rewriting live data.

**Architecture:** The foundation worktree owns the shared migration registry and ORM models so later feature worktrees start from one conflict-free schema contract. Migrations are schema-only; all data backfills remain explicit maintenance commands in later slices.

**Tech Stack:** Python 3.12, SQLAlchemy, SQLite, pytest, Ruff, uv

## Global Constraints

- Do not open, migrate, checkpoint, vacuum, or modify the live `kreports.db`.
- Use only temporary SQLite databases for tests.
- Keep revisions append-only and preserve every checksum for revisions 01 through 08.
- Do not place a data backfill inside a schema migration.
- Preserve all existing table names, columns, unique keys, and MCP contracts.
- Do not push, open a pull request, merge, or deploy.

---

## File Structure

- Modify `kreports/db/migrations.py`: declare schema-only revisions 09–11.
- Modify `kreports/db/models.py`: add `AuditFeeObservationRecord` and additive ORM fields.
- Modify `tests/test_schema_migrations.py`: prove schema shape, revision order, checksums, and revision-08 upgrade behavior.
- Modify `tests/test_company_year_quality.py`: update the exact schema contract for revision 11.
- Modify `tests/test_runtime_db_export.py`: update compact table schema expectations.

### Task 1: Revision 09 Audit Observation Schema

**Files:**
- Modify: `kreports/db/migrations.py`
- Modify: `kreports/db/models.py`
- Modify: `tests/test_schema_migrations.py`

**Interfaces:**
- Consumes: existing `Migration`, `MIGRATIONS`, `Base`, and `AuditFee`.
- Produces: ORM model `AuditFeeObservationRecord` mapped to `audit_fee_observations`.

- [ ] **Step 1: Write the failing schema tests**

Add tests that require revision 09, its columns, its foreign key, three ordinary
indexes, and one partial unique current-slot index:

```python
def test_audit_fee_observation_migration_adds_immutable_claim_store(temp_engine):
    from kreports.db.migrations import MIGRATIONS

    assert MIGRATIONS[8].revision == "20260711_09_audit_fee_observations"
    columns = {
        item["name"]: item
        for item in inspect(temp_engine).get_columns("audit_fee_observations")
    }
    assert {
        "observation_hash", "source_slot_hash", "corp_code", "bsns_year",
        "source_class", "source_rcept_no", "source_period",
        "contract_fee_m", "contract_hours", "actual_fee_m", "actual_hours",
        "auditor_nm", "availability_status", "quality_status",
        "displayed_unit", "raw_values_json", "source_status",
        "source_message", "source_eligibility", "limitations_json",
        "parser_version", "is_current", "supersedes_hash", "observed_at",
    } == set(columns)
    indexes = {
        item["name"]: item
        for item in inspect(temp_engine).get_indexes("audit_fee_observations")
    }
    assert indexes["uq_audit_fee_observation_current_slot"]["unique"] == 1
    assert (
        indexes["uq_audit_fee_observation_current_slot"]["dialect_options"][
            "sqlite_where"
        ].text
        == "is_current = 1"
    )
```

Add an insertion test proving two current rows in the same slot fail, while a
historical plus current row succeeds.

- [ ] **Step 2: Run the test to verify RED**

Run:

```bash
uv run pytest tests/test_schema_migrations.py::test_audit_fee_observation_migration_adds_immutable_claim_store -q
```

Expected: FAIL because revision 09 and the table do not exist.

- [ ] **Step 3: Add the model and schema-only migration**

Add `text` to the SQLAlchemy imports in `models.py`, then define:

```python
class AuditFeeObservationRecord(Base):
    __tablename__ = "audit_fee_observations"

    observation_hash = Column(String(64), primary_key=True)
    source_slot_hash = Column(String(64), nullable=False)
    corp_code = Column(String(8), nullable=False)
    bsns_year = Column(SmallInteger, nullable=False)
    source_class = Column(String(40), nullable=False)
    source_rcept_no = Column(String(80), nullable=True)
    source_period = Column(String(80), nullable=True)
    contract_fee_m = Column(Integer, nullable=True)
    contract_hours = Column(Integer, nullable=True)
    actual_fee_m = Column(Integer, nullable=True)
    actual_hours = Column(Integer, nullable=True)
    auditor_nm = Column(String(100), nullable=True)
    availability_status = Column(String(40), nullable=False)
    quality_status = Column(String(24), nullable=False)
    displayed_unit = Column(String(40), nullable=True)
    raw_values_json = Column(Text, nullable=False, default="{}")
    source_status = Column(String(40), nullable=True)
    source_message = Column(Text, nullable=True)
    source_eligibility = Column(String(24), nullable=False, default="unknown")
    limitations_json = Column(Text, nullable=False, default="[]")
    parser_version = Column(String(40), nullable=False)
    is_current = Column(Boolean, nullable=False, default=True)
    supersedes_hash = Column(
        String(64),
        ForeignKey("audit_fee_observations.observation_hash"),
        nullable=True,
    )
    observed_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index(
            "idx_audit_fee_observation_corp_year",
            "corp_code",
            "bsns_year",
        ),
        Index(
            "idx_audit_fee_observation_receipt",
            "source_rcept_no",
        ),
        Index(
            "idx_audit_fee_observation_year_quality",
            "bsns_year",
            "quality_status",
        ),
        Index(
            "uq_audit_fee_observation_current_slot",
            "source_slot_hash",
            unique=True,
            sqlite_where=text("is_current = 1"),
        ),
    )
```

Append `20260711_09_audit_fee_observations` with equivalent `CREATE TABLE`,
foreign-key, and index statements. Do not insert or select any data in the
migration.

- [ ] **Step 4: Run the focused tests to verify GREEN**

Run:

```bash
uv run pytest tests/test_schema_migrations.py -q
```

Expected: PASS after updating the exact declared revision list through 09.

- [ ] **Step 5: Commit**

```bash
git add kreports/db/migrations.py kreports/db/models.py tests/test_schema_migrations.py
git commit -m "feat: add audit fee observation schema"
```

### Task 2: Revision 10 Compact Financial Provenance Schema

**Files:**
- Modify: `kreports/db/migrations.py`
- Modify: `kreports/db/models.py`
- Modify: `tests/test_schema_migrations.py`
- Modify: `tests/test_runtime_db_export.py`

**Interfaces:**
- Consumes: existing `FinancialFactCompact` unique key.
- Produces: additive fields `source_table`, `unit`, `period_type`,
  `citation_rcept_no`, `citation_report_nm`, `citation_basis`, and
  `quality_status`.

- [ ] **Step 1: Write the failing revision-10 tests**

```python
def test_financial_compact_provenance_migration_is_additive(temp_engine):
    from kreports.db.migrations import MIGRATIONS

    assert MIGRATIONS[9].revision == "20260711_10_financial_compact_provenance"
    columns = {
        item["name"]: item
        for item in inspect(temp_engine).get_columns("financial_facts_compact")
    }
    assert {
        "source_table", "unit", "period_type", "citation_rcept_no",
        "citation_report_nm", "citation_basis", "quality_status",
    }.issubset(columns)
    assert columns["citation_basis"]["default"].strip("'") == "uncitable"
    assert columns["quality_status"]["default"].strip("'") == "limited"
```

Update `test_financial_facts_compact_schema` so the expected column set includes
the seven additive fields without changing the existing unique key.

- [ ] **Step 2: Run the test to verify RED**

Run:

```bash
uv run pytest tests/test_schema_migrations.py::test_financial_compact_provenance_migration_is_additive tests/test_runtime_db_export.py::test_financial_facts_compact_schema -q
```

Expected: FAIL because revision 10 and ORM fields do not exist.

- [ ] **Step 3: Add the model fields and migration**

Add these fields to `FinancialFactCompact`:

```python
source_table = Column(String(40), nullable=True)
unit = Column(String(30), nullable=True)
period_type = Column(String(20), nullable=True)
citation_rcept_no = Column(String(80), nullable=True)
citation_report_nm = Column(String(300), nullable=True)
citation_basis = Column(
    String(50),
    nullable=False,
    default="uncitable",
    server_default="uncitable",
)
quality_status = Column(
    String(24),
    nullable=False,
    default="limited",
    server_default="limited",
)
```

Append revision `20260711_10_financial_compact_provenance` with seven
`ALTER TABLE financial_facts_compact ADD COLUMN` statements. `citation_basis` and
`quality_status` use safe defaults so pre-existing compact rows remain
explicitly limited.

- [ ] **Step 4: Run focused tests**

Run:

```bash
uv run pytest tests/test_schema_migrations.py tests/test_runtime_db_export.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kreports/db/migrations.py kreports/db/models.py tests/test_schema_migrations.py tests/test_runtime_db_export.py
git commit -m "feat: add compact financial provenance schema"
```

### Task 3: Revision 11 Quality Freshness Schema

**Files:**
- Modify: `kreports/db/migrations.py`
- Modify: `kreports/db/models.py`
- Modify: `tests/test_schema_migrations.py`
- Modify: `tests/test_company_year_quality.py`

**Interfaces:**
- Consumes: existing `CompanyYearQuality` primary key and `updated_at`.
- Produces: `input_fingerprint: str` and `evidence_summary_json: str`.

- [ ] **Step 1: Write the failing revision-11 tests**

```python
def test_company_year_quality_freshness_migration_is_additive(temp_engine):
    from kreports.db.migrations import MIGRATIONS

    assert MIGRATIONS[10].revision == (
        "20260711_11_company_year_quality_freshness"
    )
    columns = {
        item["name"]: item
        for item in inspect(temp_engine).get_columns("company_year_quality")
    }
    assert columns["input_fingerprint"]["nullable"] is False
    assert columns["evidence_summary_json"]["nullable"] is False
```

Update the exact schema set in `test_company_year_quality_schema_is_versioned_append_only`.

- [ ] **Step 2: Run the test to verify RED**

Run:

```bash
uv run pytest tests/test_schema_migrations.py::test_company_year_quality_freshness_migration_is_additive tests/test_company_year_quality.py::test_company_year_quality_schema_is_versioned_append_only -q
```

Expected: FAIL because revision 11 and fields are absent.

- [ ] **Step 3: Add the model fields and migration**

```python
input_fingerprint = Column(
    String(64),
    nullable=False,
    default="",
    server_default="",
)
evidence_summary_json = Column(
    Text,
    nullable=False,
    default="{}",
    server_default="{}",
)
```

Append revision `20260711_11_company_year_quality_freshness`:

```sql
ALTER TABLE company_year_quality
ADD COLUMN input_fingerprint VARCHAR(64) NOT NULL DEFAULT ''
```

```sql
ALTER TABLE company_year_quality
ADD COLUMN evidence_summary_json TEXT NOT NULL DEFAULT '{}'
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
uv run pytest tests/test_schema_migrations.py tests/test_company_year_quality.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kreports/db/migrations.py kreports/db/models.py tests/test_schema_migrations.py tests/test_company_year_quality.py
git commit -m "feat: add quality freshness schema"
```

### Task 4: Prove Revision-08 Upgrade and Foundation Baseline

**Files:**
- Modify: `tests/test_schema_migrations.py`
- Modify: `tests/test_kam_rehearsal_worker.py`
- Modify: `tests/test_kam_rehearsal_integration.py`
- Modify: `tests/test_group_graph.py`

**Interfaces:**
- Consumes: revisions 09–11 from Tasks 1–3.
- Produces: an evidence-backed foundation commit suitable as the base of three
  linked feature worktrees.

- [ ] **Step 1: Write an explicit revision-08 upgrade test**

Build a file-backed temporary database containing the revision-08 table shapes
and recorded checksums through `MIGRATIONS[:8]`. Seed one row in each affected
table. Apply `MIGRATIONS[8:]`, then assert:

```python
assert applied == [
    "20260711_09_audit_fee_observations",
    "20260711_10_financial_compact_provenance",
    "20260711_11_company_year_quality_freshness",
]
assert second_applied == []
assert seeded_audit_fee == ("00126380", 2025, 1000, 2000)
assert seeded_compact == ("00126380", 2025, "revenue", 333_000)
assert seeded_quality == ("00126380", 2025, "A", "")
assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
assert connection.exec_driver_sql("PRAGMA quick_check").scalar_one() == "ok"
```

Update KAM rehearsal expectations so pending migrations are derived from
`MIGRATIONS[4:]` rather than hard-coded through revision 08.

- [ ] **Step 2: Run the new test to verify RED**

Run:

```bash
uv run pytest tests/test_schema_migrations.py tests/test_kam_rehearsal_worker.py tests/test_kam_rehearsal_integration.py -q
```

Expected before expectation updates: FAIL on the old exact revision list.

- [ ] **Step 3: Make test fixtures version-relative**

Keep checks for the semantic revisions 05–08, but derive “latest checked-out
schema” assertions from `MIGRATIONS[-1].revision`. In the group-graph test,
assert `MIGRATIONS[7].revision == "20260711_08_group_audit_graph"` so the test
continues proving revision 08 without falsely requiring it to remain latest.
Do not weaken checksum, ordering, or expected pending-set assertions.

- [ ] **Step 4: Run foundation verification**

Run:

```bash
uv run pytest tests/test_schema_migrations.py tests/test_company_year_quality.py tests/test_runtime_db_export.py tests/test_group_graph.py tests/test_kam_rehearsal_worker.py tests/test_kam_rehearsal_integration.py -q
uv run ruff check kreports/db/migrations.py kreports/db/models.py tests/test_schema_migrations.py tests/test_company_year_quality.py tests/test_runtime_db_export.py tests/test_group_graph.py tests/test_kam_rehearsal_worker.py tests/test_kam_rehearsal_integration.py
```

Expected: all selected tests pass and Ruff reports no issue.

- [ ] **Step 5: Commit**

```bash
git add tests/test_schema_migrations.py tests/test_group_graph.py tests/test_kam_rehearsal_worker.py tests/test_kam_rehearsal_integration.py
git commit -m "test: prove database schema foundation upgrade"
```

## Foundation Handoff Gate

Before creating feature worktrees:

```bash
git status --short
git log -4 --oneline
```

The foundation worktree must be clean, its commits must be reviewed, and only
then may the commits be integrated into `codex/professional-integration`.
