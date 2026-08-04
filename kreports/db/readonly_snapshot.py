"""Shared fail-closed SQLite snapshot opening for explicit read paths."""
from __future__ import annotations

from pathlib import Path
import sqlite3


class ReadonlySQLiteSnapshotUnavailable(RuntimeError):
    """An immutable reader cannot safely use the supplied SQLite snapshot."""


def require_checkpointed_sqlite_snapshot(database_path: Path) -> None:
    """Reject sidecars that make an immutable main-file read stale or unsafe.

    A standalone SHM file is intentionally allowed: SQLite's immutable reader
    does not consult or modify it. A non-empty WAL or rollback journal instead
    represents state absent from the immutable main database and must fail
    closed.
    """
    journal_path = Path(f"{database_path}-journal")
    if journal_path.exists() and journal_path.stat().st_size > 0:
        raise ReadonlySQLiteSnapshotUnavailable(
            "runtime_db_unavailable:hot_rollback_journal"
        )
    wal_path = Path(f"{database_path}-wal")
    if wal_path.exists() and wal_path.stat().st_size > 0:
        raise ReadonlySQLiteSnapshotUnavailable(
            "runtime_db_unavailable:uncheckpointed_wal"
        )


def open_checkpointed_readonly_sqlite(database_path: Path) -> sqlite3.Connection:
    """Open a quiescent SQLite snapshot without creating or replaying sidecars."""
    require_checkpointed_sqlite_snapshot(database_path)
    return sqlite3.connect(
        f"{database_path.as_uri()}?mode=ro&immutable=1",
        uri=True,
        check_same_thread=False,
        timeout=60,
    )
