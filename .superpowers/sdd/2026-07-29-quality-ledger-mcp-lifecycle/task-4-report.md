# Task 4 Report: POSIX Signal Shutdown Subprocess Proof

## Result

- Added an asyncio signal wrapper that installs supported SIGINT/SIGTERM
  handlers, requests cancellation of the current task, awaits the existing
  `run()` cleanup path, returns normally from controlled cancellation, and
  removes installed handlers.
- `main()` now invokes the signal wrapper.
- Added a real SIGTERM subprocess proof against a file-backed temporary SQLite
  database initialized in WAL mode.
- The child forbids Python-level `os.unlink` and `Path.unlink`, holds a real
  SQLAlchemy connection during the signal, and writes the disposal marker only
  after the real shared engine helper returns.

## RED

Command:

```bash
uv run pytest tests/test_mcp_stdio_lifecycle.py -q
```

Result: `1 failed, 4 passed`.

The subprocess reached the open-connection marker, then exited through the
default SIGTERM path with return code `-15` instead of `0`. It did not reach
graceful disposal.

## GREEN

Commands:

```bash
uv run pytest \
  tests/test_mcp_stdio_lifecycle.py \
  tests/test_mcp_resources.py \
  tests/test_http_mcp_server.py \
  tests/test_all_tools_contract.py \
  -q

uv run ruff check \
  kreports/quality/company_year_fingerprint.py \
  kreports/quality/company_year.py \
  kreports/db/quality_snapshot.py \
  kreports/db/engine.py \
  kreports/mcp/server.py \
  tests/test_company_year_quality.py \
  tests/test_quality_release_gate.py \
  tests/test_mcp_stdio_lifecycle.py

git diff --check
```

Results:

- Related lifecycle/MCP suite: `59 passed`, `17 warnings`, exit status `0`.
- Task 4 lifecycle file alone: `5 passed`.
- Ruff: `All checks passed!`
- `git diff --check`: passed.

The real child exits within the five-second bound with code `0`, writes
`disposed`, and leaves the temporary database main-file SHA-256 unchanged.

## Full-suite evidence

Command:

```bash
uv run pytest
```

Result:

- `1903 passed`
- `4 skipped`
- `84 failed`
- `29 errors`
- `18545 warnings`
- Runtime: `69.45s`

This is not a green full-suite result. Root-cause sampling separated it from
the lifecycle slice:

- Many integration tests open the repository/default database directly and
  fail with `sqlite3.OperationalError: no such table: companies`. Supplying,
  opening, migrating, or seeding a live database is expressly outside this
  task's authority.
- Isolated MCP narrative/smoke reruns return the expected fail-closed local
  cache schema error for the same missing database.
- Isolated `group_graph` answer-pack tests fail in untouched answer-pack
  behavior, and the facade golden test differs in untouched audit-matter and
  audit-history output.
- The lifecycle test file passes inside the same full run.

No unrelated fix or fixture weakening was attempted.

## Self-review

- Signal callbacks contain only `task.cancel`; they perform no SQL, disposal,
  checkpoint, wait, or file operation.
- Disposal remains centralized in `run()` and therefore shared by EOF,
  cancellation, SIGINT, and SIGTERM.
- Installed handlers are tracked and removed in `finally`.
- The subprocess waits on a state marker rather than a timing guess.
- WAL is enabled only during temporary fixture setup. The setup connection is
  closed before hashing and child launch.
- The acceptance assertion concerns handle release and main-file hash only;
  it does not require SQLite to remove a WAL/SHM sidecar.
- Application unlink attempts would raise in the child, while SQLite C-level
  behavior remains unmodified.
- No live database, existing sidecar, DART, network, or remote Git operation
  occurred.
