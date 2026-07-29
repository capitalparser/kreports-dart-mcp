# Task 4 Brief: POSIX Signal Shutdown Subprocess Proof

## Objective

Route supported POSIX termination signals through asyncio task cancellation so
the existing `run()` finally path disposes the shared engine, then prove the
behavior in a real subprocess holding a temporary SQLite read handle.

## Contract

- `_run_with_signal_shutdown()` installs SIGINT and SIGTERM handlers when the
  running loop supports them.
- A handler only requests cancellation of the running task.
- Controlled cancellation returns normally after `run()` cleanup.
- Installed handlers are removed on exit.
- `main()` invokes the wrapper with `asyncio.run()`.
- A real SIGTERM subprocess exits with code 0 and writes a disposal marker.
- The file-backed temporary database main-file SHA-256 remains unchanged.
- Application-level `os.unlink` and `Path.unlink` are forbidden in the child.
- No assertion requires SQLite to remove a WAL/SHM sidecar.

## TDD boundary

1. Add the real subprocess test while the launcher still uses default signal
   termination.
2. Confirm the child reaches the open-handle marker, then exits by SIGTERM
   without graceful disposal.
3. Add only event-loop signal registration, task cancellation, cancellation
   handling, and handler removal.
4. Run focused/related/full/Ruff verification.

All database and marker files are confined to pytest `tmp_path`.
