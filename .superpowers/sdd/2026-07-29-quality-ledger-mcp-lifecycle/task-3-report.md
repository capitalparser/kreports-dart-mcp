# Task 3 Report: Idempotent Engine Disposal on EOF and Cancellation

## Result

- Added `dispose_engine()` as the single shared-engine pool cleanup helper.
- Wrapped the existing stdio MCP body in `try/finally`.
- Normal return, cancellation, and ordinary exception paths each invoke
  disposal exactly once.
- `run()` still propagates `CancelledError` and ordinary exceptions; Task 4's
  outer signal wrapper will own controlled cancellation handling.

## RED

Command:

```bash
uv run pytest tests/test_mcp_stdio_lifecycle.py -q
```

Result: `4 failed`.

- All three lifecycle cases observed only the server call and no disposal.
- The idempotency case raised `AttributeError` because `dispose_engine` did
  not exist.

## GREEN

Commands:

```bash
uv run pytest \
  tests/test_mcp_stdio_lifecycle.py \
  tests/test_mcp_resources.py \
  tests/test_http_mcp_server.py \
  -q

uv run ruff check \
  kreports/db/engine.py \
  kreports/mcp/server.py \
  tests/test_mcp_stdio_lifecycle.py

git diff --check
```

Results:

- `53 passed`, `15 warnings`, exit status `0`.
- Ruff: `All checks passed!`
- `git diff --check`: passed.

## Self-review

- The cleanup helper contains only `engine.dispose()` and cannot issue
  application SQL or delete a sidecar.
- The `finally` is outside the stdio context, so transport teardown completes
  before pool disposal while all exits share one cleanup path.
- The ordinary exception test checks object identity, not only exception type.
- The cancellation test proves `run()` does not swallow cancellation.
- The idempotency test opens, closes, disposes twice, and reconnects to a
  file-backed SQLite database created under `tmp_path`.
- Signal handlers and cancellation swallowing are intentionally absent until
  Task 4.
- Import/UTC cleanup in `engine.py` is mechanical. The existing dataset
  manifest `ValueError` contract was preserved with an explicit Ruff
  suppression rather than changed to `TypeError`.
- No live database, existing sidecar, DART, network, or remote Git operation
  occurred.
