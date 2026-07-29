# Task 3 Brief: Idempotent Engine Disposal on EOF and Cancellation

## Objective

Create one shared SQLAlchemy engine-disposal helper and guarantee the stdio MCP
`run()` path invokes it exactly once on normal return, cancellation, and
ordinary exceptions.

## Contract

- `dispose_engine()` delegates only to the shared engine's `dispose()`.
- Calling the helper repeatedly succeeds.
- Normal stdio EOF/server return invokes disposal exactly once.
- `asyncio.CancelledError` invokes disposal exactly once and remains
  observable from `run()`.
- An ordinary exception invokes disposal exactly once and is re-raised
  unchanged.
- `run()` issues no checkpoint, vacuum, sidecar unlink, or other SQL.
- Signal handling remains out of scope until Task 4.

## TDD boundary

1. Create unit lifecycle tests with only stdio/server/disposal boundaries
   patched.
2. Confirm disposal assertions/helper lookup fail before implementation.
3. Add the helper and one `try/finally` around the existing stdio body.
4. Run lifecycle and existing MCP resource/HTTP regressions.

The idempotency check uses only a file-backed database under `tmp_path`.
