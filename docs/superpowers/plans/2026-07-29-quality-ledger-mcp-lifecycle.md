# Quality Ledger and MCP Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make company-year quality reproducible and freshness-aware, and ensure the stdio MCP server explicitly releases KReports-owned database handles on controlled shutdown.

**Architecture:** A pure canonicalization module builds bounded evidence summaries and fingerprints that the existing quality rebuild persists atomically. Separately, an idempotent engine-disposal helper and asyncio signal wrapper provide one controlled cleanup path for EOF, cancellation, SIGINT, and SIGTERM without issuing maintenance SQL or deleting SQLite sidecars.

**Tech Stack:** Python 3.12, SQLAlchemy, SQLite, asyncio, POSIX signals, subprocess, pytest, Ruff, uv

## Global Constraints

- Start from the reviewed schema-foundation commit containing revisions 09–11.
- Do not open or modify the live database.
- Do not add timestamps or local paths to semantic fingerprints.
- Keep the existing grade and status algorithms unchanged.
- Do not checkpoint, vacuum, unlink, or otherwise manage SQLite sidecars.
- Run signal and WAL behavior only against file-backed temporary databases.
- Do not push, open a pull request, merge, or deploy.

---

## File Structure

- Create `kreports/quality/company_year_fingerprint.py`: canonical summary and SHA-256.
- Modify `kreports/quality/company_year.py`: persist and expose freshness fields.
- Modify `kreports/db/quality_snapshot.py`: include non-volatile freshness fields in release digests.
- Modify `tests/test_company_year_quality.py` and `tests/test_quality_release_gate.py`.
- Modify `kreports/db/engine.py`: idempotent shared-engine disposal helper.
- Modify `kreports/mcp/server.py`: `try/finally` and controlled POSIX signal cancellation.
- Create `tests/test_mcp_stdio_lifecycle.py`: unit and real subprocess lifecycle tests.

### Task 1: Canonical Quality Evidence Summary

**Files:**
- Create: `kreports/quality/company_year_fingerprint.py`
- Modify: `tests/test_company_year_quality.py`

**Interfaces:**
- Produces ordered constant `QUALITY_STATUS_KEYS` containing
  `financial_core`, `auditor`, `audit_fee`, `policy`, `kam`,
  `audit_procedure`, and `group_audit`.
- Produces ordered constant `QUALITY_GRADE_KEYS` containing `investor_core`,
  `auditor_full`, and `group_audit`.
- Produces
  `build_quality_evidence_summary(*, statuses: Mapping[str, str], grades: Mapping[str, str], blockers: Iterable[str], quality_version: str) -> dict[str, object]`.
- Produces
  `quality_input_fingerprint(summary: Mapping[str, object]) -> str`.

- [ ] **Step 1: Write failing pure-function tests**

```python
def test_quality_fingerprint_is_stable_across_mapping_and_blocker_order():
    left = build_quality_evidence_summary(
        statuses={
            "financial_core": "available",
            "auditor": "available",
            "audit_fee": "partial",
            "policy": "full_body",
            "kam": "summary_only",
            "audit_procedure": "missing",
            "group_audit": "partial",
        },
        grades={
            "investor_core": "A",
            "auditor_full": "D",
            "group_audit": "D",
        },
        blockers=("kam_summary_only", "procedure_missing"),
        quality_version="v1",
    )
    right = build_quality_evidence_summary(
        statuses=dict(reversed(list(left["statuses"].items()))),
        grades=dict(reversed(list(left["grades"].items()))),
        blockers=("procedure_missing", "kam_summary_only", "kam_summary_only"),
        quality_version="v1",
    )
    assert left == right
    assert quality_input_fingerprint(left) == quality_input_fingerprint(right)
    assert len(quality_input_fingerprint(left)) == 64
```

Add tests proving a changed status, grade, blocker, or quality version changes
the hash; an unknown/missing required key raises `ValueError`; and the
serialized summary contains no timestamp.

- [ ] **Step 2: Run tests to verify RED**

```bash
uv run pytest tests/test_company_year_quality.py -q
```

Expected: import failure for the new module.

- [ ] **Step 3: Implement strict canonicalization**

```python
def _ordered_values(
    values: Mapping[str, str],
    required: tuple[str, ...],
    label: str,
) -> dict[str, str]:
    if set(values) != set(required):
        raise ValueError(f"{label} keys must equal {required}")
    return {key: str(values[key]) for key in required}


def build_quality_evidence_summary(
    *,
    statuses: Mapping[str, str],
    grades: Mapping[str, str],
    blockers: Iterable[str],
    quality_version: str,
) -> dict[str, object]:
    return {
        "statuses": _ordered_values(statuses, QUALITY_STATUS_KEYS, "status"),
        "grades": _ordered_values(grades, QUALITY_GRADE_KEYS, "grade"),
        "blockers": sorted({str(value) for value in blockers}),
        "quality_version": str(quality_version),
    }


def quality_input_fingerprint(summary: Mapping[str, object]) -> str:
    payload = json.dumps(
        summary,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run pure and existing quality tests**

```bash
uv run pytest tests/test_company_year_quality.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kreports/quality/company_year_fingerprint.py tests/test_company_year_quality.py
git commit -m "feat: define quality evidence fingerprints"
```

### Task 2: Persist and Read Quality Freshness

**Files:**
- Modify: `kreports/quality/company_year.py`
- Modify: `kreports/db/quality_snapshot.py`
- Modify: `tests/test_company_year_quality.py`
- Modify: `tests/test_quality_release_gate.py`
- Modify: `tests/test_dataset_manifest.py`

**Interfaces:**
- Consumes: Task 1 functions and revision-11 ORM fields.
- Produces: additive `input_fingerprint`, `evidence_summary`, and
  `freshness_limitations` keys from `company_year_quality()`.

- [ ] **Step 1: Write failing rebuild and read tests**

After a rebuild:

```python
assert quality["input_fingerprint"]
assert quality["evidence_summary"]["statuses"] == quality["statuses"]
assert quality["evidence_summary"]["grades"] == {
    "investor_core": quality["feature_grades"]["investor_core"],
    "auditor_full": quality["feature_grades"]["auditor_full"],
    "group_audit": quality["feature_grades"]["group_audit"],
}
assert quality["freshness_limitations"] == []
```

Run the rebuild twice and assert the fingerprint and summary JSON are identical
while `updated_at` may change. Add one evidence row that changes an existing
status, rebuild, and assert the fingerprint changes.

Seed a legacy row with blank fingerprint/default summary and assert:

```python
assert legacy["input_fingerprint"] is None
assert legacy["evidence_summary"] == {}
assert legacy["freshness_limitations"] == [
    "품질 원장이 입력 증거 fingerprint 도입 이전 상태입니다."
]
```

- [ ] **Step 2: Run tests to verify RED**

```bash
uv run pytest tests/test_company_year_quality.py tests/test_quality_release_gate.py tests/test_dataset_manifest.py -q
```

Expected: FAIL because rebuild/read and release digest ignore the new fields.

- [ ] **Step 3: Persist in the existing company-year transaction**

Construct explicit maps after all current status and grade functions run:

```python
status_values = {
    "financial_core": statuses[year],
    "auditor": auditor_status,
    "audit_fee": audit_fee_status,
    "policy": policy_status,
    "kam": kam_status,
    "audit_procedure": audit_procedure_status,
    "group_audit": group_audit_status,
}
grade_values = {
    "investor_core": investor_grade,
    "auditor_full": auditor_grade,
    "group_audit": group_audit_grade,
}
evidence_summary = build_quality_evidence_summary(
    statuses=status_values,
    grades=grade_values,
    blockers=blockers,
    quality_version=QUALITY_VERSION,
)
row.evidence_summary_json = json.dumps(
    evidence_summary,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
)
row.input_fingerprint = quality_input_fingerprint(evidence_summary)
```

Expose the parsed summary and fingerprint. Validate that stored JSON is an
object and its recomputed hash matches the stored fingerprint; if either check
fails, return an empty summary and a bounded freshness limitation instead of
claiming current evidence.

Append `input_fingerprint` and `evidence_summary_json` to
`QUALITY_CONTENT_FIELDS`. Parse and canonicalize `evidence_summary_json` as a
JSON object inside `quality_content_digest()` rather than hashing whitespace in
the raw JSON string. Update release-gate and dataset-manifest expected digests;
keep `updated_at` excluded.

- [ ] **Step 4: Run quality and release regressions**

```bash
uv run pytest tests/test_company_year_quality.py tests/test_quality_release_gate.py tests/test_dataset_manifest.py tests/test_mcp_resources.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kreports/quality/company_year.py kreports/db/quality_snapshot.py tests/test_company_year_quality.py tests/test_quality_release_gate.py tests/test_dataset_manifest.py tests/test_mcp_resources.py
git commit -m "feat: persist quality ledger freshness"
```

### Task 3: Idempotent Engine Disposal on EOF and Cancellation

**Files:**
- Modify: `kreports/db/engine.py`
- Modify: `kreports/mcp/server.py`
- Create: `tests/test_mcp_stdio_lifecycle.py`

**Interfaces:**
- Produces `dispose_engine() -> None`.
- Produces `run() -> None` as an async function.
- Produces `_run_with_signal_shutdown() -> None` as an async function.

- [ ] **Step 1: Write failing unit lifecycle tests**

Monkeypatch `stdio_server`, `server.run`, and `dispose_engine`. Assert
`dispose_engine()` is called exactly once when the MCP server:

- returns normally after EOF;
- raises `asyncio.CancelledError`;
- raises an ordinary exception, which remains observable after cleanup.

Also call `dispose_engine()` twice against a temporary engine and assert both
calls succeed.

- [ ] **Step 2: Run tests to verify RED**

```bash
uv run pytest tests/test_mcp_stdio_lifecycle.py -q
```

Expected: FAIL because `run()` has no final disposal and the helper is absent.

- [ ] **Step 3: Implement the shared cleanup path**

In `kreports/db/engine.py`:

```python
def dispose_engine() -> None:
    """Release KReports-owned pooled connections without issuing SQL."""
    engine.dispose()
```

In `kreports/mcp/server.py`:

```python
async def run() -> None:
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
    finally:
        from kreports.db.engine import dispose_engine

        dispose_engine()
```

Do not swallow ordinary exceptions in `run()`.

- [ ] **Step 4: Run unit lifecycle tests**

```bash
uv run pytest tests/test_mcp_stdio_lifecycle.py tests/test_mcp_resources.py tests/test_http_mcp_server.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kreports/db/engine.py kreports/mcp/server.py tests/test_mcp_stdio_lifecycle.py
git commit -m "fix: dispose MCP database engine on shutdown"
```

### Task 4: POSIX Signal Shutdown Subprocess Proof

**Files:**
- Modify: `kreports/mcp/server.py`
- Modify: `tests/test_mcp_stdio_lifecycle.py`

**Interfaces:**
- Consumes: Task 3 `run()` cleanup path.
- Produces: bounded SIGINT/SIGTERM cancellation in `_run_with_signal_shutdown()`.

- [ ] **Step 1: Write the failing real-subprocess test**

The test creates a file-backed temporary SQLite database, enables WAL during
fixture setup, closes the setup connection, and records the main database
SHA-256. It spawns a Python probe that:

```python
@asynccontextmanager
async def fake_stdio():
    yield object(), object()


async def fake_server_run(*_args):
    with engine.connect() as connection:
        connection.exec_driver_sql("SELECT COUNT(*) FROM lifecycle_probe")
        Path(os.environ["OPEN_MARKER"]).write_text("open")
        await asyncio.Event().wait()
```

The probe wraps the real `engine.dispose()` to write `DISPOSED_MARKER` after
cleanup, patches only the stdio transport/server body, and invokes the real
`kreports.mcp.server.main()`.

The parent waits for `OPEN_MARKER`, sends `SIGTERM`, and asserts:

```python
assert process.wait(timeout=5) == 0
assert disposed_marker.read_text() == "disposed"
assert sha256(database_path) == initial_sha256
```

In the child probe, replace Python-level `os.unlink` and `Path.unlink` with
functions that raise `AssertionError`. This detects any application cleanup
attempt while leaving SQLite's own C-level file handling untouched. Do not ask
SQLite to open malformed sidecar content, and do not require a read-only close
to remove a stale SHM file.

- [ ] **Step 2: Run the subprocess test to verify RED**

```bash
uv run pytest tests/test_mcp_stdio_lifecycle.py -q
```

Expected: the process exits by the default SIGTERM path and does not write the
graceful-disposal marker.

- [ ] **Step 3: Add bounded signal cancellation**

```python
async def _run_with_signal_shutdown() -> None:
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    installed: list[signal.Signals] = []
    if task is None:
        raise RuntimeError("MCP signal wrapper requires a running task")
    for candidate in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(candidate, task.cancel)
        except (NotImplementedError, RuntimeError):
            continue
        installed.append(candidate)
    try:
        await run()
    except asyncio.CancelledError:
        return
    finally:
        for candidate in installed:
            loop.remove_signal_handler(candidate)


def main() -> None:
    asyncio.run(_run_with_signal_shutdown())
```

The handler schedules cancellation only. It performs no database operation,
file deletion, checkpoint, or wait.

- [ ] **Step 4: Run lifecycle, related, and full verification**

```bash
uv run pytest tests/test_mcp_stdio_lifecycle.py tests/test_mcp_resources.py tests/test_http_mcp_server.py tests/test_all_tools_contract.py -q
uv run pytest
uv run ruff check kreports/quality/company_year_fingerprint.py kreports/quality/company_year.py kreports/db/quality_snapshot.py kreports/db/engine.py kreports/mcp/server.py tests/test_company_year_quality.py tests/test_quality_release_gate.py tests/test_mcp_stdio_lifecycle.py
```

Expected: focused and full suites pass; subprocess exits through the cleanup
marker; Ruff reports no issue.

- [ ] **Step 5: Commit**

```bash
git add kreports/mcp/server.py tests/test_mcp_stdio_lifecycle.py
git commit -m "fix: handle controlled MCP signal shutdown"
```
