"""Fail-closed safety boundaries for APFS database rehearsal clones."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys


MIN_FREE_BYTES = 10 * 1024**3
_MAX_ERROR_MESSAGE_CHARS = 512


class RehearsalSafetyError(RuntimeError):
    """A stable machine code with a bounded, caller-safe explanation."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message[:_MAX_ERROR_MESSAGE_CHARS])


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


def sha256_file(path: Path) -> str:
    """Return a chunked SHA-256 digest without loading a database into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _open_immutable(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"{path.as_uri()}?mode=ro&immutable=1",
        uri=True,
    )
    connection.execute("PRAGMA query_only=ON")
    return connection


def _identity_from_stat(path: Path, metadata: os.stat_result) -> FileIdentity:
    return FileIdentity(
        path=path,
        size=metadata.st_size,
        inode=metadata.st_ino,
        device=metadata.st_dev,
        mtime_ns=metadata.st_mtime_ns,
        sha256=sha256_file(path),
    )


def _raise_if_nonempty_sidecar(source_db: Path) -> None:
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{source_db}{suffix}")
        if sidecar.exists() and sidecar.stat().st_size > 0:
            raise RehearsalSafetyError(
                "source_sidecar_present",
                "source database has a non-empty SQLite sidecar",
            )


def _has_running_backfill(connection: sqlite3.Connection) -> bool:
    table = connection.execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type = 'table' AND name = 'backfill_runs'"
    ).fetchone()
    if table is None:
        return False
    running_count = connection.execute(
        "SELECT COUNT(*) FROM backfill_runs WHERE status = 'running'"
    ).fetchone()
    return running_count is not None and running_count[0] > 0


def inspect_source_database(source_db: Path) -> FileIdentity:
    """Inspect an immutable SQLite source and return its witnessed identity."""
    if not source_db.is_absolute():
        raise RehearsalSafetyError(
            "source_not_absolute",
            "source database path must be absolute",
        )
    if source_db.is_symlink():
        raise RehearsalSafetyError(
            "source_is_symlink",
            "source database must not be a symlink",
        )
    if not source_db.is_file():
        raise RehearsalSafetyError(
            "source_not_regular",
            "source database must be a regular file",
        )
    source_stat = source_db.stat()
    if source_stat.st_nlink != 1:
        raise RehearsalSafetyError(
            "source_is_hardlink",
            "source database must not be hard-linked",
        )
    _raise_if_nonempty_sidecar(source_db)

    try:
        connection = _open_immutable(source_db)
        try:
            if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise RehearsalSafetyError(
                    "source_integrity_failed",
                    "source database failed SQLite quick_check",
                )
            if _has_running_backfill(connection):
                raise RehearsalSafetyError(
                    "active_backfill_lease",
                    "source database has an active backfill lease",
                )
        finally:
            connection.close()
    except RehearsalSafetyError:
        raise
    except sqlite3.Error as exc:
        raise RehearsalSafetyError(
            "source_integrity_failed",
            "source database could not complete SQLite quick_check",
        ) from exc

    return _identity_from_stat(source_db, source_db.stat())


def _is_descendant(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return path != parent


def _resolve_rehearsal_directory(rehearsal_dir: Path) -> Path:
    if not rehearsal_dir.is_absolute() or rehearsal_dir.is_symlink():
        raise RehearsalSafetyError(
            "unsafe_rehearsal_directory",
            "rehearsal directory must be a real absolute directory",
        )
    try:
        resolved = rehearsal_dir.resolve(strict=True)
    except OSError as exc:
        raise RehearsalSafetyError(
            "unsafe_rehearsal_directory",
            "rehearsal directory must exist and resolve strictly",
        ) from exc
    if not resolved.is_dir():
        raise RehearsalSafetyError(
            "unsafe_rehearsal_directory",
            "rehearsal directory must be a directory",
        )
    return resolved


def _filesystem_type(rehearsal_dir: Path) -> str:
    try:
        completed = subprocess.run(
            ["/usr/bin/stat", "-f", "%T", str(rehearsal_dir)],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RehearsalSafetyError(
            "filesystem_not_apfs",
            "could not verify APFS rehearsal filesystem",
        ) from exc
    return completed.stdout.strip().lower()


def assert_free_space(
    rehearsal_dir: Path,
    *,
    min_free_bytes: int = MIN_FREE_BYTES,
) -> int:
    free_bytes = shutil.disk_usage(rehearsal_dir).free
    if free_bytes < min_free_bytes:
        raise RehearsalSafetyError(
            "insufficient_free_space",
            "rehearsal filesystem does not meet the free-space reserve",
        )
    return free_bytes


def preflight_rehearsal(
    source_db: Path,
    rehearsal_dir: Path,
    *,
    repository_root: Path,
    min_free_bytes: int = MIN_FREE_BYTES,
) -> SourcePreflight:
    """Approve only a safely isolated APFS clone destination."""
    source = inspect_source_database(source_db)
    resolved_rehearsal_dir = _resolve_rehearsal_directory(rehearsal_dir)
    source_parent = source.path.parent.resolve(strict=True)
    repository = repository_root.resolve(strict=True)
    protected_directories = (source_parent, repository)
    if (
        resolved_rehearsal_dir in (Path("/"), Path.home().resolve())
        or resolved_rehearsal_dir in protected_directories
        or any(
            _is_descendant(resolved_rehearsal_dir, protected)
            for protected in protected_directories
        )
    ):
        raise RehearsalSafetyError(
            "unsafe_rehearsal_directory",
            "rehearsal directory overlaps a protected directory",
        )
    if resolved_rehearsal_dir.stat().st_dev != source.device:
        raise RehearsalSafetyError(
            "different_filesystem",
            "source and rehearsal directory must share a filesystem",
        )
    filesystem_type = _filesystem_type(resolved_rehearsal_dir)
    if sys.platform != "darwin" or filesystem_type != "apfs":
        raise RehearsalSafetyError(
            "filesystem_not_apfs",
            "rehearsal directory must be on Darwin APFS",
        )
    free_bytes = assert_free_space(
        resolved_rehearsal_dir,
        min_free_bytes=min_free_bytes,
    )
    target = resolved_rehearsal_dir / "kreports-rehearsal.db"
    if os.path.lexists(target):
        raise RehearsalSafetyError(
            "target_exists",
            "rehearsal clone target already exists",
        )
    return SourcePreflight(
        source=source,
        rehearsal_dir=resolved_rehearsal_dir,
        free_bytes=free_bytes,
        filesystem_type=filesystem_type,
    )


def _inspect_clone_target(target: Path, expected: FileIdentity) -> FileIdentity:
    if target.is_symlink() or not target.is_file():
        raise RehearsalSafetyError(
            "clone_identity_mismatch",
            "clone target is not a regular file",
        )
    target_stat = target.stat()
    if target_stat.st_nlink != 1 or target_stat.st_ino == expected.inode:
        raise RehearsalSafetyError(
            "clone_identity_mismatch",
            "clone target does not have an independent file identity",
        )
    return _identity_from_stat(target, target_stat)


def create_apfs_clone(
    preflight: SourcePreflight,
    *,
    target_name: str = "kreports-rehearsal.db",
) -> FileIdentity:
    """Create one APFS clone with no copy fallback and verify both identities."""
    target = preflight.rehearsal_dir / target_name
    if target.name != target_name or target.parent != preflight.rehearsal_dir:
        raise RehearsalSafetyError(
            "unsafe_rehearsal_directory",
            "clone target must remain inside the rehearsal directory",
        )
    if os.path.lexists(target):
        raise RehearsalSafetyError(
            "target_exists",
            "rehearsal clone target already exists",
        )
    try:
        subprocess.run(
            ["/bin/cp", "-c", str(preflight.source.path), str(target)],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RehearsalSafetyError(
            "clonefile_unsupported",
            "APFS clonefile operation is unavailable",
        ) from exc

    clone = _inspect_clone_target(target, preflight.source)
    if clone.sha256 != preflight.source.sha256:
        raise RehearsalSafetyError(
            "clone_identity_mismatch",
            "clone digest does not match the approved source digest",
        )
    assert_source_unchanged(preflight.source)
    return clone


def assert_source_unchanged(expected: FileIdentity) -> FileIdentity:
    """Repeat immutable inspection and require an exact original identity."""
    try:
        current = inspect_source_database(expected.path)
    except RehearsalSafetyError as exc:
        raise RehearsalSafetyError(
            "source_changed",
            "source database is no longer unchanged",
        ) from exc
    if current != expected:
        raise RehearsalSafetyError(
            "source_changed",
            "source database identity changed after preflight",
        )
    return current
