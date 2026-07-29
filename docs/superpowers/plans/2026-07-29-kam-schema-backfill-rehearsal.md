# KAM Schema and Backfill Rehearsal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and execute a fail-closed APFS-clone rehearsal that applies the current schema, reconstructs 2021–2025 KAM and audit-procedure data, validates all 17 professional MCP calls, and proves the live database stayed byte-identical.

**Architecture:** A path-safety module owns immutable source inspection and APFS clone creation. A fresh-process worker owns all database-bound migration, rebuild, snapshot, and MCP actions. A parent orchestrator calls those two boundaries, records an append-only phase report, verifies the source digest after every clone mutation phase, and exposes one operator CLI.

**Tech Stack:** Python 3.11/3.12, SQLite, SQLAlchemy 2, Typer, pytest, macOS APFS `/bin/cp -c`, existing KReports MCP dispatch and immutable runtime binding.

## Global Constraints

- The live database is never opened writable and must retain the same SHA-256, inode, and size throughout the run.
- The live database must have no non-empty `-wal` or `-shm` sidecar before cloning.
- The rehearsal directory must be explicit and same-volume APFS. It must not equal the source directory, repository root, home directory, or filesystem root, and it must not be inside either repository directory.
- A normal full copy is not a fallback when `/bin/cp -c` is unavailable or fails.
- At least `10 * 1024**3` bytes must remain free before cloning and before every later mutation phase.
- The target database must not already exist; no implicit overwrite is allowed.
- All database-bound actions run in fresh subprocesses with explicit `DB_URL`, `KREPORTS_RUNTIME_MODE`, and an empty `DART_API_KEY`.
- Migration uses the supported `init_db()` path and derives pending revisions from `MIGRATIONS`; it does not hard-code revision `08` as permanently latest.
- The rebuild range is exactly business years `2021, 2022, 2023, 2024, 2025`, in ascending order.
- KAM and procedure rebuilds use only local evidence already present in the clone and make no DART or other network call.
- `rows_written` is not an idempotency verdict; stable semantic snapshots and stable primary/foreign key identities are the verdict.
- Every MCP result uses canonical status `usable | limited | missing | error`; schema names and raw SQLite errors never reach chatbot, pack, or resource output.
- The four explicit KAM gates are `build_audit_acceptance_pack`, `get_audit_report_sections`, `get_kam_lifecycle`, and `compare_peer_kam_topics`.
- The professional probe company is Samsung Electronics stock code `005930`; five-year calls cover 2021–2025 and year-specific calls use 2025.
- The clone is retained after handoff and is never deleted automatically.
- No source database path, API key, or retained clone path is committed to documentation, fixtures, or Git history.
- No push, pull request, merge, or live migration occurs without a separate user decision.
- Core parsing and signal logic remain independent from MCP transports; evidence extraction stays separate from interpretation.
- Tests use real SQLite behavior whenever practical, and every production behavior is introduced through a witnessed failing test.
- Project verification commands use `uv run pytest` and `uv run ruff check`.

## File Map and Parallel Execution

Tasks 1 through 3 start from the same plan commit in three separate
project-local ignored worktrees and use Terra High. Their production and test
files do not overlap:

| Task | Owned files | Responsibility |
|---|---|---|
| 1 | `kreports/maintenance/rehearsal_safety.py`, `tests/test_rehearsal_safety.py` | source identity, immutable inspection, disk/APFS checks, clone creation |
| 2 | `kreports/maintenance/kam_rehearsal_worker.py`, `tests/test_kam_rehearsal_worker.py` | fresh-process migration, rebuild, semantic snapshot, MCP validation |
| 3 | `kreports/maintenance/kam_backfill_rehearsal.py`, `tests/test_kam_backfill_rehearsal.py`, `kreports/cli/main.py` | phase orchestration, reports, operator CLI |

Task 3 consumes the exact Task 1 and Task 2 interfaces specified below and
must not reimplement them. After each task is committed and reviewed, the
controller integrates the three commits into one dedicated integration
worktree. Task 4 runs only after that integration. Task 5 is the real retained
clone rehearsal and final evidence gate.

---

### Task 1: Fail-Closed Source Inspection and APFS Clone

**Files:**
- Create: `kreports/maintenance/rehearsal_safety.py`
- Create: `tests/test_rehearsal_safety.py`

**Interfaces:**
- Consumes: standard library only (`hashlib`, `os`, `pathlib`, `shutil`, `sqlite3`, `subprocess`, `sys`).
- Produces:

```python
MIN_FREE_BYTES = 10 * 1024**3

class RehearsalSafetyError(RuntimeError):
    code: str

@dataclass(frozen=True)
class FileIdentity:
    path: Path
    size: int
    inode: int
    device: int
    mtime_ns: int
    sha256: str

@dataclass(frozen=True)
class SourcePreflight:
    source: FileIdentity
    rehearsal_dir: Path
    free_bytes: int
    filesystem_type: str

def sha256_file(path: Path) -> str: ...
def inspect_source_database(source_db: Path) -> FileIdentity: ...
def preflight_rehearsal(
    source_db: Path,
    rehearsal_dir: Path,
    *,
    repository_root: Path,
    min_free_bytes: int = MIN_FREE_BYTES,
) -> SourcePreflight: ...
def assert_free_space(
    rehearsal_dir: Path,
    *,
    min_free_bytes: int = MIN_FREE_BYTES,
) -> int: ...
def create_apfs_clone(
    preflight: SourcePreflight,
    *,
    target_name: str = "kreports-rehearsal.db",
) -> FileIdentity: ...
def assert_source_unchanged(expected: FileIdentity) -> FileIdentity: ...
```

`RehearsalSafetyError.__init__(code, message)` stores a stable machine code
and exposes only the bounded message to the caller. Codes used by this task
are:

```python
{
    "source_not_absolute",
    "source_not_regular",
    "source_is_symlink",
    "source_is_hardlink",
    "source_sidecar_present",
    "source_integrity_failed",
    "active_backfill_lease",
    "unsafe_rehearsal_directory",
    "target_exists",
    "different_filesystem",
    "filesystem_not_apfs",
    "insufficient_free_space",
    "clonefile_unsupported",
    "clone_identity_mismatch",
    "source_changed",
}
```

- [ ] **Step 1: Write source-path and immutable-inspection failing tests**

Before writing each test, name the break: accepting a relative path, symlink,
hardlink, source directory alias, non-empty sidecar, failed SQLite quick check,
or active `backfill_runs.status='running'` lease would permit an unsafe clone.
Use real temporary SQLite files and literal expected codes.

```python
@pytest.mark.parametrize(
    ("arrange", "expected_code"),
    [
        ("relative", "source_not_absolute"),
        ("symlink", "source_is_symlink"),
        ("hardlink", "source_is_hardlink"),
        ("wal", "source_sidecar_present"),
        ("shm", "source_sidecar_present"),
        ("corrupt", "source_integrity_failed"),
        ("running_lease", "active_backfill_lease"),
    ],
)
def test_preflight_rejects_unsafe_source(
    tmp_path: Path,
    arrange: str,
    expected_code: str,
) -> None:
    source, rehearsal_dir, repository_root = arrange_source_case(
        tmp_path,
        arrange,
    )
    with pytest.raises(RehearsalSafetyError) as caught:
        preflight_rehearsal(
            source,
            rehearsal_dir,
            repository_root=repository_root,
            min_free_bytes=1,
        )
    assert caught.value.code == expected_code
```

The fixture creates a minimal `backfill_runs(id, status)` table. For the
corruption case, overwrite a valid page after closing SQLite. Do not mock the
SQLite connection or its query results.

- [ ] **Step 2: Run source inspection tests and witness the expected failure**

Run:

```bash
uv run pytest tests/test_rehearsal_safety.py -k "unsafe_source" -vv
```

Expected: collection or import failure because
`kreports.maintenance.rehearsal_safety` does not exist.

- [ ] **Step 3: Implement immutable source inspection**

Implement chunked SHA-256 reads and this immutable connection boundary:

```python
def _open_immutable(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"{path.as_uri()}?mode=ro&immutable=1",
        uri=True,
    )
    connection.execute("PRAGMA query_only=ON")
    return connection
```

`inspect_source_database()` must:

1. reject non-absolute, symlink, non-file, and `st_nlink != 1` paths;
2. reject any existing non-empty `<source>-wal` or `<source>-shm`;
3. require `PRAGMA quick_check` to return the single literal row `("ok",)`;
4. if `backfill_runs` exists, reject a positive count for
   `status='running'`;
5. close the immutable connection before hashing;
6. return `FileIdentity` populated from one post-check `stat()` and
   `sha256_file()`.

- [ ] **Step 4: Run source inspection tests until green**

Run:

```bash
uv run pytest tests/test_rehearsal_safety.py -k "unsafe_source or inspect_source" -vv
```

Expected: all selected tests pass and the source has no new sidecar.

- [ ] **Step 5: Write rehearsal-directory and disk/APFS failing tests**

Name the break: allowing root, home, repository root, source parent, a
different device, a non-APFS filesystem, an existing target, or free space
below the exact reserve would violate the approved boundary.

```python
def test_preflight_requires_exact_free_space_floor(
    valid_paths: ValidPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        shutil,
        "disk_usage",
        lambda _path: shutil._ntuple_diskusage(20, 15, MIN_FREE_BYTES - 1),
    )
    with pytest.raises(RehearsalSafetyError) as caught:
        preflight_rehearsal(
            valid_paths.source,
            valid_paths.rehearsal_dir,
            repository_root=valid_paths.repository_root,
        )
    assert caught.value.code == "insufficient_free_space"
```

Patch only OS metadata that cannot be created portably. Keep path resolution,
same-file checks, and SQLite inspection real.

- [ ] **Step 6: Run directory preflight tests and witness the expected failures**

Run:

```bash
uv run pytest tests/test_rehearsal_safety.py -k "rehearsal_directory or free_space or filesystem" -vv
```

Expected: failure because directory/APFS/free-space validation is absent.

- [ ] **Step 7: Implement directory, volume, APFS, and reserve checks**

Resolve the rehearsal directory strictly, reject symlinks, and compare its
`st_dev` with the source device. Determine filesystem type with:

```python
completed = subprocess.run(
    ["/usr/bin/stat", "-f", "%T", str(rehearsal_dir)],
    check=True,
    capture_output=True,
    text=True,
)
filesystem_type = completed.stdout.strip().lower()
```

Require `sys.platform == "darwin"` and the literal filesystem type `apfs`.
Reject a directory equal to the source parent, repository root, `Path.home()`,
or `Path("/")`. Also reject descendants of the source parent or repository
root. Do not reject a safe sibling rehearsal directory merely because both it
and the repository are below `Path.home()`. Require
`disk_usage(...).free >= min_free_bytes`. Reject an existing target before
returning the preflight.

- [ ] **Step 8: Write APFS clone identity and source-stability failing tests**

The portable test replaces `/bin/cp` execution with a test-only runner that
materializes the destination and records the exact command. Assertions are on
the resulting file identity and error code, not on a mock call alone. Add a
Darwin-only integration test that runs real `/bin/cp -c` on a small SQLite
file when the temporary directory reports APFS.

```python
def test_create_apfs_clone_requires_equal_initial_digest(
    valid_preflight: SourcePreflight,
) -> None:
    mutate_source_after_clone(valid_preflight.source.path)
    with pytest.raises(RehearsalSafetyError) as caught:
        create_apfs_clone(valid_preflight)
    assert caught.value.code in {"clone_identity_mismatch", "source_changed"}
```

- [ ] **Step 9: Run clone tests and witness the expected failures**

Run:

```bash
uv run pytest tests/test_rehearsal_safety.py -k "clone or source_unchanged" -vv
```

Expected: failures because clone creation and repeat identity checks are
absent.

- [ ] **Step 10: Implement no-fallback APFS cloning and identity checks**

Execute only:

```python
subprocess.run(
    ["/bin/cp", "-c", str(source), str(target)],
    check=True,
    capture_output=True,
    text=True,
)
```

Map command absence or non-zero exit to `clonefile_unsupported`; never retry
without `-c`. After the command:

1. reject a symlink, hardlink, non-file, same inode, or unexpected target;
2. hash target and source;
3. require `target.sha256 == preflight.source.sha256`;
4. require the new source identity to equal the expected size, inode, device,
   mtime, and digest;
5. return the target `FileIdentity`.

`assert_source_unchanged()` repeats the full identity and sidecar checks and
raises `source_changed` on any difference.

- [ ] **Step 11: Run all Task 1 tests and Ruff**

Run:

```bash
uv run pytest tests/test_rehearsal_safety.py -vv
uv run ruff check kreports/maintenance/rehearsal_safety.py tests/test_rehearsal_safety.py
```

Expected: all tests pass; Darwin/APFS integration test passes on the target
host and skips only on unsupported hosts.

- [ ] **Step 12: Commit Task 1**

```bash
git add kreports/maintenance/rehearsal_safety.py tests/test_rehearsal_safety.py
git commit -m "feat: add fail-closed APFS rehearsal safety"
```

---

### Task 2: Fresh-Process Migration, Rebuild, Snapshot, and MCP Worker

**Files:**
- Create: `kreports/maintenance/kam_rehearsal_worker.py`
- Create: `tests/test_kam_rehearsal_worker.py`

**Interfaces:**
- Consumes: `DB_URL` and `KREPORTS_RUNTIME_MODE` already set before process
  start; existing `MIGRATIONS`, `init_db()`, KAM rebuild, procedure indexer,
  MCP `call_tool`, `dispatch_tool`, `handle_call_tool`, resource reader, and
  immutable runtime binding.
- Produces:

```python
YEARS = (2021, 2022, 2023, 2024, 2025)
CANONICAL_STATUSES = {"usable", "limited", "missing", "error"}
KAM_GATED_TOOLS = {
    "build_audit_acceptance_pack",
    "get_audit_report_sections",
    "get_kam_lifecycle",
    "compare_peer_kam_topics",
}
PROFESSIONAL_REHEARSAL_TOOLS: tuple[tuple[str, dict[str, object]], ...]

class WorkerActionError(RuntimeError):
    code: str

def execute_action(action: str, *, year: int | None = None) -> dict[str, object]: ...
def migration_state() -> dict[str, object]: ...
def semantic_snapshot() -> dict[str, object]: ...
def validate_professional_mcp() -> dict[str, object]: ...
def main(argv: list[str] | None = None) -> int: ...
```

Supported actions are the exact literals:

```python
{
    "migrate",
    "kam-dry-run",
    "kam-rebuild",
    "procedure-index",
    "semantic-snapshot",
    "mcp-validate",
}
```

The module prints exactly one compact JSON object to stdout. Diagnostic logs
go to stderr. On failure it prints:

```json
{"ok":false,"action":"migrate","error":{"code":"migration_failed","message":"bounded message"}}
```

and returns exit code `2`.

- [ ] **Step 1: Write fresh-process environment and action-validation failing tests**

Name the break: importing database modules before `DB_URL` binding or accepting
an action/year mismatch can bind the live database or run an unbounded rebuild.
Launch the module with `subprocess.run`, a temporary DB URL, collector/readonly
mode, and an empty API key.

```python
def test_worker_rejects_year_for_migrate(worker_env: dict[str, str]) -> None:
    result = run_worker_process(worker_env, "migrate", "--year", "2025")
    payload = json.loads(result.stdout)
    assert result.returncode == 2
    assert payload["error"]["code"] == "invalid_action_arguments"

def test_worker_rejects_year_outside_rehearsal_range(
    worker_env: dict[str, str],
) -> None:
    result = run_worker_process(worker_env, "kam-rebuild", "--year", "2020")
    assert json.loads(result.stdout)["error"]["code"] == "invalid_year"
```

The child environment must omit the parent `DB_URL`, `DART_API_KEY`, and
`KREPORTS_RUNTIME_MODE` before adding the explicit test values.

- [ ] **Step 2: Run action-validation tests and witness the expected failure**

Run:

```bash
uv run pytest tests/test_kam_rehearsal_worker.py -k "rejects_year or fresh_process" -vv
```

Expected: import/module failure because the worker does not exist.

- [ ] **Step 3: Implement the worker shell and delayed imports**

Parse `action` and optional `--year` with `argparse`. Validate arguments before
importing `kreports.config`, `kreports.db.engine`, any collector, or MCP module.
Enforce:

```python
if action in {"kam-dry-run", "kam-rebuild", "procedure-index"}:
    if year not in YEARS:
        raise WorkerActionError("invalid_year", "year must be one of 2021..2025")
elif year is not None:
    raise WorkerActionError(
        "invalid_action_arguments",
        f"{action} does not accept --year",
    )
```

Require collector mode for migration and rebuild actions and readonly mode for
snapshot and MCP validation. Serialize with
`json.dumps(..., ensure_ascii=False, sort_keys=True, separators=(",", ":"))`.
Bound exception messages to 500 characters and remove absolute paths.

- [ ] **Step 4: Run shell tests until green**

Run:

```bash
uv run pytest tests/test_kam_rehearsal_worker.py -k "rejects_year or fresh_process" -vv
```

Expected: selected tests pass.

- [ ] **Step 5: Write migration-ledger and schema verification failing tests**

Create a real legacy SQLite fixture with ledger revisions `01` through `04`,
legacy `audit_procedure_items`, legacy `audit_fees`, and `backfill_runs`.
Use the real checked-out migration checksums for the first four rows. The test
must prove behavior, not source text:

```python
def test_migrate_applies_every_pending_checked_out_revision(
    legacy_database: Path,
) -> None:
    first = run_worker("migrate", legacy_database, runtime_mode="collector")
    second = run_worker("migrate", legacy_database, runtime_mode="collector")
    assert first["before"]["recorded_revisions"] == [
        migration.revision for migration in MIGRATIONS[:4]
    ]
    assert first["applied_revisions"] == [
        migration.revision for migration in MIGRATIONS[4:]
    ]
    assert first["after"]["recorded_revisions"] == [
        migration.revision for migration in MIGRATIONS
    ]
    assert first["after"]["schema_complete"] is True
    assert second["applied_revisions"] == []
```

Also assert literal required KAM columns, procedure linkage columns, fee
availability columns, group graph tables, migration checksum match,
`quick_check == ["ok"]`, and an empty foreign-key-check list.

- [ ] **Step 6: Run migration tests and witness the expected failure**

Run:

```bash
uv run pytest tests/test_kam_rehearsal_worker.py -k "migrate or schema" -vv
```

Expected: failures because `migration_state()` and `migrate` are absent.

- [ ] **Step 7: Implement checked-out migration and schema state**

`migration_state()` opens the configured database read-only for inspection and
returns:

```python
{
    "recorded_revisions": list[str],
    "pending_revisions": list[str],
    "checksum_mismatches": list[str],
    "schema_complete": bool,
    "missing_tables": list[str],
    "missing_columns": dict[str, list[str]],
    "missing_indexes": list[str],
    "quick_check": list[str],
    "foreign_key_violations": list[dict[str, object]],
}
```

For `migrate`, capture `before`, invoke supported `init_db()`, capture `after`,
and derive `applied_revisions` as the ordered difference. Fail with
`migration_failed` when checksums, schema, quick check, or foreign keys are not
clean. Do not issue custom schema-changing SQL.

- [ ] **Step 8: Write KAM/procedure action and no-network failing tests**

Use real source rows in a small SQLite fixture. Patch `socket.socket.connect`
and `httpx.Client.send` to raise if invoked, then call `execute_action`
in-process only after the temporary engine is bound by the existing test
fixture.

```python
def test_kam_rebuild_and_procedure_index_use_local_evidence_only(
    kam_source_database: Path,
    forbid_network: None,
) -> None:
    dry = run_worker("kam-dry-run", kam_source_database, year=2025)
    rebuilt = run_worker("kam-rebuild", kam_source_database, year=2025)
    indexed = run_worker("procedure-index", kam_source_database, year=2025)
    assert dry["database_status"] == "available"
    assert dry["rows_written"] == 0
    assert rebuilt["receipt_counts"]["full_body"] == 1
    assert indexed["failed"] == 0
    assert indexed["rows_written"] >= 1
```

The worker returns aggregate counts and at most 20 limitation/error samples;
it never emits every receipt.

- [ ] **Step 9: Run rebuild tests and witness the expected failure**

Run:

```bash
uv run pytest tests/test_kam_rehearsal_worker.py -k "local_evidence or kam_rebuild or procedure_index" -vv
```

Expected: failures because the actions are absent.

- [ ] **Step 10: Implement bounded KAM and procedure actions**

Call the existing functions directly:

```python
rebuild_kam_items(year=year, dry_run=(action == "kam-dry-run"))
index_audit_procedures_from_sections(year=year)
```

Strip receipt arrays down to a maximum of 20 rows while preserving all
aggregate counts. A non-zero `error`/`failed` count raises
`backfill_failed`. Do not import or call any collector that fetches a remote
filing.

- [ ] **Step 11: Write semantic snapshot stability failing tests**

Name the break: excluding IDs, foreign keys, hashes, or typed linkage fields
would miss ID churn or changed semantics. Populate two KAM rows and linked
procedures, run the snapshot, update one `method`, and prove the digest changes.
Restore the method and prove the original digest returns.

```python
def test_semantic_snapshot_binds_stable_ids_and_typed_linkage(
    populated_database: Path,
) -> None:
    before = run_worker("semantic-snapshot", populated_database)
    execute_sql(populated_database, "UPDATE audit_procedure_items SET method='inquiry'")
    changed = run_worker("semantic-snapshot", populated_database)
    assert changed["semantic_sha256"] != before["semantic_sha256"]
    assert before["integrity"]["orphan_procedure_count"] == 0
```

- [ ] **Step 12: Run snapshot tests and witness the expected failure**

Run:

```bash
uv run pytest tests/test_kam_rehearsal_worker.py -k "semantic_snapshot" -vv
```

Expected: failure because the snapshot action is absent.

- [ ] **Step 13: Implement deterministic semantic snapshots**

Query ordered literal columns from `kam_items` and
`audit_procedure_items`. Include:

```python
KAM_SNAPSHOT_COLUMNS = (
    "id", "rcept_no", "dcm_no", "corp_code", "bsns_year", "source_type",
    "ordinal", "title", "normalized_topic", "reason_text",
    "audit_response_text", "related_note_references_json", "full_body_hash",
    "full_body_length", "source_basis", "parser_version", "quality_status",
)
PROCEDURE_SNAPSHOT_COLUMNS = (
    "id", "rcept_no", "dcm_no", "corp_code", "bsns_year", "source_type",
    "kam_item_id", "kam_topic", "method", "procedure_type",
    "procedure_text", "procedure_hash", "procedure_length",
    "assertion_hints_json", "linked_metric_keys_json",
    "linked_note_keys_json", "linked_event_keys_json", "parser_version",
    "quality_status", "section_ordinal", "procedure_ordinal",
)
```

Canonicalize JSON text fields by parsing and re-serializing with sorted keys.
Hash compact sorted JSON. Return counts, per-year quality distributions,
duplicate logical identities, orphan count, cross-receipt/source/ordinal link
count, usable-response-without-procedure count, and `semantic_sha256`.

- [ ] **Step 14: Write 17-tool MCP parity and schema-closure failing tests**

Assert the literal 17-name catalog, exact Samsung arguments, canonical
statuses, equality across legacy/envelope/stdio, pack status parity, and
resource status/core-source parity. Feed the pure result validator a synthetic
schema leak and prove it fails.

```python
def test_mcp_validator_rejects_schema_text_at_every_layer() -> None:
    leaked = professional_result_fixture(
        tool="get_kam_lifecycle",
        answer="판정: error\nno such table: kam_items",
    )
    with pytest.raises(WorkerActionError) as caught:
        validate_professional_result(leaked)
    assert caught.value.code == "mcp_schema_not_closed"
```

The forbidden literals are:

```python
(
    "no such table",
    "no such column",
    "OperationalError",
    "kam_items",
    "kam_item_id",
    "audit_procedure_items",
)
```

They are scanned only in public result, answer pack, and rendered resource,
not in internal diagnostic field names.

- [ ] **Step 15: Run MCP tests and witness the expected failure**

Run:

```bash
uv run pytest tests/test_kam_rehearsal_worker.py -k "mcp or professional" -vv
```

Expected: failure because the catalog and validator are absent.

- [ ] **Step 16: Implement bounded professional MCP validation**

For each literal catalog entry:

1. call `call_tool()` and parse JSON;
2. call `dispatch_tool().model_dump(mode="json")`;
3. call `asyncio.run(handle_call_tool(...))`;
4. require answer/status/section statuses/pack/domain verdict parity;
5. when `answer_pack.resource_uri` exists, call `read_resource()` in the same
   process and require the resource text to contain canonical status and each
   material receipt present in the bounded pack;
6. scan every public layer for forbidden schema tokens;
7. require each KAM-gated Samsung result to be non-`error`;
8. return only a bounded matrix row: tool, status, domain verdict, fact count,
   evidence count, pack status, table IDs, source count, resource checked,
   first answer paragraph, and limitation count.

Return:

```python
{
    "tool_count": 17,
    "schema_error_closed": True,
    "all_boundary_parity": True,
    "matrix": list[dict[str, object]],
}
```

- [ ] **Step 17: Run all Task 2 tests and Ruff**

Run:

```bash
uv run pytest tests/test_kam_rehearsal_worker.py -vv
uv run ruff check kreports/maintenance/kam_rehearsal_worker.py tests/test_kam_rehearsal_worker.py
```

Expected: all tests pass.

- [ ] **Step 18: Commit Task 2**

```bash
git add kreports/maintenance/kam_rehearsal_worker.py tests/test_kam_rehearsal_worker.py
git commit -m "feat: add isolated KAM rehearsal worker"
```

---

### Task 3: Phase Orchestrator, Reports, and Operator CLI

**Files:**
- Create: `kreports/maintenance/kam_backfill_rehearsal.py`
- Create: `tests/test_kam_backfill_rehearsal.py`
- Modify: `kreports/cli/main.py`

**Interfaces:**
- Consumes from Task 1:
  `MIN_FREE_BYTES`, `FileIdentity`, `RehearsalSafetyError`,
  `preflight_rehearsal()`, `assert_free_space()`, `create_apfs_clone()`, and
  `assert_source_unchanged()`.
- Invokes Task 2 only as a fresh subprocess:
  `python -m kreports.maintenance.kam_rehearsal_worker ACTION [--year YEAR]`.
- Produces:

```python
REHEARSAL_YEARS = (2021, 2022, 2023, 2024, 2025)
PHASES = (
    "source_preflight",
    "clone_created",
    "schema_migrated",
    "kam_dry_run_complete",
    "kam_rebuild_complete",
    "procedure_reconcile_complete",
    "idempotency_verified",
    "mcp_validation_complete",
    "live_immutability_verified",
)

@dataclass(frozen=True)
class WorkerInvocation:
    action: str
    runtime_mode: Literal["collector", "readonly"]
    year: int | None = None

class RehearsalRunError(RuntimeError):
    code: str
    report_path: Path | None

def invoke_worker(
    *,
    python_executable: Path,
    database: Path,
    invocation: WorkerInvocation,
) -> dict[str, object]: ...
def run_kam_schema_backfill_rehearsal(
    *,
    source_db: Path,
    rehearsal_dir: Path,
    repository_root: Path,
    python_executable: Path,
    min_free_bytes: int = MIN_FREE_BYTES,
) -> dict[str, object]: ...
def render_rehearsal_markdown(report: dict[str, object]) -> str: ...
```

The report schema is `kam-schema-backfill-rehearsal.v1`. Each phase record
contains `name`, `status`, `started_at`, `finished_at`, and bounded `evidence`.

- [ ] **Step 1: Write subprocess environment and output-boundary failing tests**

Name the break: inherited API keys or database settings could bind the wrong
database; malformed/multiple JSON outputs could hide a failed worker.

```python
def test_invoke_worker_uses_minimal_explicit_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DART_API_KEY", "must-not-propagate")
    payload = invoke_worker(
        python_executable=Path(sys.executable),
        database=tmp_path / "clone.db",
        invocation=WorkerInvocation("migrate", "collector"),
    )
    assert payload["observed_env"] == {
        "DART_API_KEY": "",
        "KREPORTS_RUNTIME_MODE": "collector",
        "DB_URL": f"sqlite:///{tmp_path / 'clone.db'}",
    }
```

Use a temporary executable module as the child so the assertion reflects the
real subprocess environment. Also test non-zero exit, empty output, multiple
JSON lines, `ok:false`, and an output larger than 2 MiB.

- [ ] **Step 2: Run worker-boundary tests and witness the expected failure**

Run:

```bash
uv run pytest tests/test_kam_backfill_rehearsal.py -k "invoke_worker" -vv
```

Expected: import/module failure because the orchestrator does not exist.

- [ ] **Step 3: Implement strict worker invocation**

Build the child environment from a small allowlist:

```python
environment = {
    "PATH": os.environ.get("PATH", ""),
    "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
    "DB_URL": f"sqlite:///{database}",
    "KREPORTS_RUNTIME_MODE": invocation.runtime_mode,
    "DART_API_KEY": "",
    "KREPORTS_HEADLESS": "1",
    "DART_HEADLESS": "1",
}
```

Add `--year` only when present. Use `capture_output=True`, `text=True`, a
phase timeout supplied by a module mapping, and no shell. Require exactly one
JSON document, `ok is True`, and a payload below 2 MiB. Convert worker failures
to `RehearsalRunError` without including an absolute database path.

- [ ] **Step 4: Write append-only phase and stop-on-failure tests**

Use real temporary JSON report files and deterministic fake worker payloads.
The test must prove no later mutating invocation occurs after a failed year:

```python
def test_rehearsal_stops_before_next_year_after_backfill_failure(
    rehearsal_fixture: RehearsalFixture,
) -> None:
    report = run_with_worker_failure(
        rehearsal_fixture,
        action="kam-rebuild",
        year=2023,
    )
    assert report["status"] == "backfill_failed"
    assert report["last_phase"] == "kam_rebuild_complete"
    assert ("kam-rebuild", 2024) not in report["test_invocations"]
    assert ("procedure-index", 2021) not in report["test_invocations"]
```

Cover the exact phase order, ascending years, free-space check before each
mutation phase, source identity check after clone/migration/rebuild/MCP/final,
and rejection of an existing report/clone.

- [ ] **Step 5: Run phase tests and witness the expected failures**

Run:

```bash
uv run pytest tests/test_kam_backfill_rehearsal.py -k "phase or stops_before or year_order" -vv
```

Expected: failures because the state machine is absent.

- [ ] **Step 6: Implement the phase state machine**

Use a local `_ReportWriter` that writes a new JSON file atomically after each
phase with `open(..., "x")` for the first record and `os.replace()` only for
that same run's subsequent snapshots. The report filename is:

```text
kam-schema-backfill-rehearsal-YYYYMMDDTHHMMSSZ.json
```

The flow is exact:

```python
preflight
clone
migrate
for year in REHEARSAL_YEARS: kam-dry-run
snapshot_before
for year in REHEARSAL_YEARS: kam-rebuild
for year in REHEARSAL_YEARS: procedure-index
snapshot_after_first
for year in REHEARSAL_YEARS: kam-rebuild
for year in REHEARSAL_YEARS: procedure-index
snapshot_after_second
require snapshot_after_first["semantic_sha256"] == snapshot_after_second["semantic_sha256"]
require identity/count integrity fields equal
mcp-validate
final source identity
```

Run `assert_free_space()` before migration, each five-year write loop, and the
second idempotency pass. Run `assert_source_unchanged()` after the phases
specified in the design. Keep the clone and report on every outcome.

- [ ] **Step 7: Write report classification and Markdown failing tests**

Hand-build one literal report for each terminal status and assert exact
operator-facing content:

```python
@pytest.mark.parametrize(
    "status",
    [
        "preflight_blocked",
        "migration_failed",
        "backfill_failed",
        "data_quality_limited",
        "mcp_schema_closed",
        "live_digest_changed",
        "complete",
    ],
)
def test_markdown_names_terminal_status_and_live_digest_result(
    status: str,
) -> None:
    markdown = render_rehearsal_markdown(report_fixture(status))
    assert f"Final status: `{status}`" in markdown
    assert "Live SHA-256 unchanged:" in markdown
```

Also assert no API key, absolute source path, raw SQL error, or receipt-level
unbounded array is emitted in committed-safe Markdown.

- [ ] **Step 8: Run report tests and witness the expected failure**

Run:

```bash
uv run pytest tests/test_kam_backfill_rehearsal.py -k "markdown or terminal_status" -vv
```

Expected: failure because report classification/rendering is absent.

- [ ] **Step 9: Implement terminal classification and bounded reports**

Classify:

- safety failure before clone: `preflight_blocked`;
- migration worker failure: `migration_failed`;
- rebuild, procedure, or idempotency failure: `backfill_failed`;
- MCP validator proving schema closed with genuine incomplete evidence:
  `data_quality_limited`;
- MCP validator proving schema closed without required limitations:
  `mcp_schema_closed`;
- any source identity change: `live_digest_changed`;
- completed orchestration plus all acceptance gates: `complete`.

The JSON report keeps the local clone path for operator handoff. The Markdown
renderer accepts `redact_paths=True` by default and shows only clone filename,
logical/allocated sizes, counts, statuses, digests, and cleanup warning.

- [ ] **Step 10: Write CLI behavior failing tests**

Invoke Typer with explicit absolute paths. Assert a successful run prints
final status, JSON report path, Markdown report path, retained clone path, and
live digest equality. Assert a safety failure exits `2` and still prints the
report path when one exists.

```python
result = CliRunner().invoke(
    app,
    [
        "rehearse-kam-schema-backfill",
        "--source-db", str(source),
        "--rehearsal-dir", str(rehearsal_dir),
        "--python-executable", sys.executable,
    ],
)
assert result.exit_code == 0
assert "live_sha256_unchanged=true" in result.stdout
assert "clone_retained=true" in result.stdout
```

- [ ] **Step 11: Run CLI tests and witness the expected failure**

Run:

```bash
uv run pytest tests/test_kam_backfill_rehearsal.py -k "cli" -vv
```

Expected: failure because the command is not registered.

- [ ] **Step 12: Add the lazy-import operator CLI**

Register `rehearse-kam-schema-backfill` in `kreports/cli/main.py`. Require
absolute existing `--source-db`, absolute existing `--rehearsal-dir`, and an
existing `--python-executable` defaulting to `sys.executable`. Import the
orchestrator inside the command body so ordinary CLI startup does not bind a
database or platform-specific module.

Print only:

```text
status=<terminal status>
json_report=<absolute local report path>
markdown_report=<absolute local report path>
clone=<absolute retained clone path>
clone_retained=true
live_sha256_unchanged=true|false
```

Treat `complete`, `mcp_schema_closed`, and `data_quality_limited` as successful
rehearsal outcomes and exit `0`; the last status is successful execution with
explicit evidence limitations. Exit `2` for every other terminal state.

- [ ] **Step 13: Run all Task 3 tests and Ruff**

Run:

```bash
uv run pytest tests/test_kam_backfill_rehearsal.py -vv
uv run ruff check kreports/maintenance/kam_backfill_rehearsal.py tests/test_kam_backfill_rehearsal.py kreports/cli/main.py
```

Expected: all tests pass.

- [ ] **Step 14: Commit Task 3**

```bash
git add kreports/maintenance/kam_backfill_rehearsal.py tests/test_kam_backfill_rehearsal.py kreports/cli/main.py
git commit -m "feat: orchestrate KAM schema backfill rehearsal"
```

---

### Task 4: Integrated Contract and Regression Verification

**Files:**
- Modify if a real integration defect is found:
  `kreports/maintenance/rehearsal_safety.py`
- Modify if a real integration defect is found:
  `kreports/maintenance/kam_rehearsal_worker.py`
- Modify if a real integration defect is found:
  `kreports/maintenance/kam_backfill_rehearsal.py`
- Modify if a real integration defect is found:
  `kreports/cli/main.py`
- Create: `tests/test_kam_rehearsal_integration.py`
- Modify: `tests/test_professional_mcp_live.py`

**Interfaces:**
- Consumes all Task 1–3 interfaces.
- Produces one real small-database end-to-end contract and extends the existing
  live test so a migrated clone must close the four KAM schema gates.

- [ ] **Step 1: Write a real APFS small-database end-to-end failing test**

Build a legacy SQLite source that records revisions `01` through `04`, has
legacy procedure/fee columns, one Samsung company, one 2025 audit-report KAM
source body, and no KAM table. Run the real orchestrator with a 1-byte test
reserve on APFS.

```python
@pytest.mark.skipif(sys.platform != "darwin", reason="APFS clonefile required")
def test_real_rehearsal_migrates_rebuilds_and_preserves_source(
    legacy_kam_source: Path,
    apfs_rehearsal_dir: Path,
) -> None:
    source_before = sha256_file(legacy_kam_source)
    report = run_kam_schema_backfill_rehearsal(
        source_db=legacy_kam_source,
        rehearsal_dir=apfs_rehearsal_dir,
        repository_root=PROJECT_ROOT,
        python_executable=Path(sys.executable),
        min_free_bytes=1,
    )
    assert report["status"] in {
        "mcp_schema_closed",
        "data_quality_limited",
        "complete",
    }
    assert report["mcp"]["tool_count"] == 17
    assert report["mcp"]["schema_error_closed"] is True
    assert report["idempotency"]["semantic_sha256_equal"] is True
    assert sha256_file(legacy_kam_source) == source_before
    assert Path(report["clone"]["path"]).is_file()
```

No worker subprocess is mocked. The test is allowed to report `limited`
because its synthetic source lacks the complete professional dataset.

- [ ] **Step 2: Run the end-to-end test and witness the expected failure**

Run:

```bash
uv run pytest tests/test_kam_rehearsal_integration.py -vv
```

Expected: a concrete integration failure identifying the first mismatched
interface or missing fixture dependency.

- [ ] **Step 3: Fix only witnessed integration defects**

For each failure, add the smallest focused test before changing production.
Common permitted corrections are:

- mismatch between Task 1 dataclass serialization and Task 3 report encoding;
- worker stdout polluted by application logging;
- current `init_db()` creating an expected table before the ledger records its
  migration;
- visualization resource cache lifetime within MCP validation;
- SQLite URI formatting for an absolute clone path.

Do not relax source safety, APFS, digest, year, network, idempotency, or MCP
schema-closure gates to make the integration test pass.

- [ ] **Step 4: Run the integrated test until green**

Run:

```bash
uv run pytest tests/test_kam_rehearsal_integration.py -vv
```

Expected: pass with a retained clone and unchanged source digest.

- [ ] **Step 5: Strengthen the opt-in live contract for a migrated clone**

In `tests/test_professional_mcp_live.py`, preserve the existing unmigrated
schema branch. In the migrated branch require:

```python
assert all(outputs[name]["data_quality"]["status"] != "error" for name in affected)
assert all(
    token not in rendered
    for token in (
        "no such table",
        "no such column",
        "OperationalError",
        "kam_items",
        "kam_item_id",
        "audit_procedure_items",
    )
)
```

Also read every available `answer_pack.resource_uri` in the same process and
require canonical status plus each pack receipt to appear in the resource.

- [ ] **Step 6: Run focused and related regressions**

Run:

```bash
uv run pytest \
  tests/test_rehearsal_safety.py \
  tests/test_kam_rehearsal_worker.py \
  tests/test_kam_backfill_rehearsal.py \
  tests/test_kam_rehearsal_integration.py \
  tests/test_schema_migrations.py \
  tests/test_kam_parser.py \
  tests/test_audit_procedure_indexer.py \
  tests/test_professional_mcp_contract.py \
  tests/test_professional_mcp_live.py \
  -m "not live" -vv
uv run ruff check \
  kreports/maintenance/rehearsal_safety.py \
  kreports/maintenance/kam_rehearsal_worker.py \
  kreports/maintenance/kam_backfill_rehearsal.py \
  kreports/cli/main.py \
  tests/test_rehearsal_safety.py \
  tests/test_kam_rehearsal_worker.py \
  tests/test_kam_backfill_rehearsal.py \
  tests/test_kam_rehearsal_integration.py \
  tests/test_professional_mcp_live.py
```

Expected: all selected non-live tests and Ruff pass.

- [ ] **Step 7: Commit Task 4**

```bash
git add \
  kreports/maintenance/rehearsal_safety.py \
  kreports/maintenance/kam_rehearsal_worker.py \
  kreports/maintenance/kam_backfill_rehearsal.py \
  kreports/cli/main.py \
  tests/test_rehearsal_safety.py \
  tests/test_kam_rehearsal_worker.py \
  tests/test_kam_backfill_rehearsal.py \
  tests/test_kam_rehearsal_integration.py \
  tests/test_professional_mcp_live.py
git commit -m "test: verify integrated KAM rehearsal"
```

If no production file changed, stage only the test files that changed.

---

### Task 5: Retained Live-Clone Rehearsal and Final Evidence

**Files:**
- Create locally, never commit:
  `<rehearsal-directory>/kam-schema-backfill-rehearsal-*.json`
- Create locally, never commit:
  `<rehearsal-directory>/kam-schema-backfill-rehearsal-*.md`
- Create locally, never commit:
  `<rehearsal-directory>/kreports-rehearsal.db`
- Update the SDD task report and progress ledger only in the plan-specific
  ignored workspace.

**Interfaces:**
- Consumes the reviewed Task 1–4 implementation and the explicit live database.
- Produces actual migration, KAM quality, procedure linkage, MCP matrix,
  idempotency, disk, and live-digest evidence.

- [ ] **Step 1: Record integration HEAD, worktree state, source identity, and disk**

Run from the integrated implementation worktree:

```bash
git status --short --branch
git rev-parse HEAD
df -k /Users/kjun/vault/01_Projects
shasum -a 256 /Users/kjun/vault/01_Projects/kreports_dart_mcp/kreports.db
ls -l \
  /Users/kjun/vault/01_Projects/kreports_dart_mcp/kreports.db \
  /Users/kjun/vault/01_Projects/kreports_dart_mcp/kreports.db-wal \
  /Users/kjun/vault/01_Projects/kreports_dart_mcp/kreports.db-shm
```

Expected: implementation worktree clean, at least 10 GiB free, and no
non-empty source sidecar. A missing sidecar is acceptable.

- [ ] **Step 2: Create one explicit same-volume rehearsal directory**

Run:

```bash
mkdir -p /Users/kjun/vault/01_Projects/.kreports-rehearsals
mktemp -d /Users/kjun/vault/01_Projects/.kreports-rehearsals/kam-20260729.XXXXXX
```

Record the exact returned directory. Do not use a shell variable whose empty
value could broaden a later path operation.

- [ ] **Step 3: Execute the real CLI once**

Substitute the exact returned directory literally:

```bash
uv run kreports rehearse-kam-schema-backfill \
  --source-db /Users/kjun/vault/01_Projects/kreports_dart_mcp/kreports.db \
  --rehearsal-dir /Users/kjun/vault/01_Projects/.kreports-rehearsals/kam-20260729.EXACT \
  --python-executable .venv/bin/python
```

Expected: the CLI retains the clone, prints both report paths, and prints
`live_sha256_unchanged=true`. Do not retry automatically after a failure.

- [ ] **Step 4: Inspect the real report and clone integrity**

Run with the exact emitted paths:

```bash
jq '{
  schema_version,
  status,
  phases,
  migration,
  kam,
  procedures,
  idempotency,
  mcp,
  live_immutability
}' /absolute/emitted/report.json
sqlite3 'file:/absolute/emitted/kreports-rehearsal.db?mode=ro&immutable=1' \
  'PRAGMA quick_check; PRAGMA foreign_key_check;'
du -h /absolute/emitted/kreports-rehearsal.db
ls -ls /absolute/emitted/kreports-rehearsal.db
```

Expected:

- pending checked-out revisions applied and the second migration pass empty;
- 2021–2025 KAM and procedure passes contain no unreviewed error;
- first and second semantic digests equal;
- zero duplicate/orphan/cross-receipt integrity failures;
- `tool_count == 17`, `schema_error_closed == true`, and boundary parity true;
- immutable SQLite checks pass;
- the report classifies genuine evidence gaps as limitations, not schema
  failures.

- [ ] **Step 5: Run the existing opt-in live test against the retained clone**

Run:

```bash
KREPORTS_LIVE_DB=/absolute/emitted/kreports-rehearsal.db \
  uv run pytest tests/test_professional_mcp_live.py -m live -vv -s
```

Expected: all live-marked clone tests pass and print identical before/after
clone SHA-256 values.

- [ ] **Step 6: Re-prove the original live database identity**

Run:

```bash
shasum -a 256 /Users/kjun/vault/01_Projects/kreports_dart_mcp/kreports.db
stat -f '%i %z %m' /Users/kjun/vault/01_Projects/kreports_dart_mcp/kreports.db
```

Expected: digest, inode, and size equal the source-preflight record and no new
non-empty sidecar exists.

- [ ] **Step 7: Run broad regression comparison**

Run the focused suite first, then the full suite on an empty/default test
database using the same Python version and environment as the prior baseline:

```bash
uv run pytest \
  tests/test_rehearsal_safety.py \
  tests/test_kam_rehearsal_worker.py \
  tests/test_kam_backfill_rehearsal.py \
  tests/test_kam_rehearsal_integration.py \
  tests/test_schema_migrations.py \
  tests/test_kam_parser.py \
  tests/test_audit_procedure_indexer.py \
  tests/test_professional_mcp_contract.py \
  tests/test_professional_mcp_live.py \
  -m "not live"
uv run pytest
uv run ruff check .
git diff --check
```

Compare the full-suite failures against the recorded branch baseline. Do not
label local results as CI results.

- [ ] **Step 8: Independent whole-branch review**

Give the reviewer:

- the approved design;
- this plan;
- the whole-branch diff from the plan commit;
- focused, related, full-suite, and Ruff evidence;
- the real JSON/Markdown report;
- the retained clone identity;
- initial/final live database identity;
- any deferred Minor findings from the task ledger.

The review must explicitly verdict:

- source/live immutability and destructive-path safety;
- no implicit copy fallback or overwrite;
- migration correctness and checksum handling;
- network isolation;
- idempotency and stable identity proof;
- KAM/procedure relational integrity;
- MCP chatbot/pack/resource parity and information sufficiency;
- artifact bounding and secret/path redaction;
- production rollout recommendation.

- [ ] **Step 9: Final handoff without production mutation**

Report:

- implementation branch and HEAD;
- committed files and tests;
- retained clone and report paths;
- actual per-year KAM quality counts;
- actual procedure linkage counts;
- actual 17-tool MCP status matrix;
- idempotency digest result;
- focused/full regression evidence separated from release readiness;
- initial/final live SHA-256 equality;
- independent review verdict;
- remaining auditor/investor evidence limitations;
- explicit statement that live migration, push, PR, and cleanup did not occur.

Leave the clone in place until the user separately approves cleanup or live
migration.
