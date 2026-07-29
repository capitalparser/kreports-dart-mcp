# Round 1 signal restoration report

## Review finding

The review finding was valid. The controlled shutdown wrapper replaced existing
SIGINT and SIGTERM handlers, then only removed its own handlers. On POSIX this
discarded a prior asyncio callback or raw `signal.signal` handler.

## RED

A POSIX regression installs:

- an existing asyncio SIGINT callback with an argument; and
- an existing raw SIGTERM handler.

It exercises successful return, `CancelledError`, and an ordinary exception.
Before the implementation all three cases failed because the prior asyncio
handler was absent after shutdown.

## GREEN

Before installing the cancellation callback, the wrapper records the raw signal
handler and any existing asyncio `Handle`. During cleanup it:

- removes only its cancellation callback;
- restores the raw handler when no prior loop callback existed; and
- reinstalls the prior loop callback while reusing the original `Handle`, which
  preserves its callback, arguments, and context.

Restoration runs in `finally`, so it applies to success, controlled
cancellation, and ordinary exceptions.

## Verification

Focused lifecycle command:

```text
uv run pytest tests/test_mcp_stdio_lifecycle.py -q
```

Result: `8 passed`, including the real POSIX SIGTERM subprocess probe.

Changed-file lint:

```text
uv run ruff check kreports/mcp/server.py tests/test_mcp_stdio_lifecycle.py
```

Result: `All checks passed!`

No full suite, default/live database, network, remote, or sidecar was used.
