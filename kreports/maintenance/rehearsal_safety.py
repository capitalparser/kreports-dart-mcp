"""Fail-closed safety boundaries for APFS database rehearsal clones."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import plistlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import weakref


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


@dataclass(frozen=True)
class _PreflightApproval:
    reference: weakref.ReferenceType[SourcePreflight]
    repository_root: Path
    enforced_min_free_bytes: int


_PREFLIGHT_APPROVALS: dict[int, _PreflightApproval] = {}


def _register_preflight_approval(
    preflight: SourcePreflight,
    *,
    repository_root: Path,
    enforced_min_free_bytes: int,
) -> None:
    approval_id = id(preflight)

    def remove_dead_approval(
        dead_reference: weakref.ReferenceType[SourcePreflight],
    ) -> None:
        current = _PREFLIGHT_APPROVALS.get(approval_id)
        if current is not None and current.reference is dead_reference:
            _PREFLIGHT_APPROVALS.pop(approval_id, None)

    reference = weakref.ref(preflight, remove_dead_approval)
    _PREFLIGHT_APPROVALS[approval_id] = _PreflightApproval(
        reference=reference,
        repository_root=repository_root,
        enforced_min_free_bytes=enforced_min_free_bytes,
    )


def _require_preflight_approval(preflight: SourcePreflight) -> _PreflightApproval:
    approval = _PREFLIGHT_APPROVALS.get(id(preflight))
    if approval is None or approval.reference() is not preflight:
        raise RehearsalSafetyError(
            "unsafe_rehearsal_directory",
            "preflight must be the exact object approved in this process",
        )
    return approval


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
        df_completed = subprocess.run(
            ["/bin/df", "-P", str(rehearsal_dir)],
            check=True,
            capture_output=True,
            text=True,
        )
        df_lines = df_completed.stdout.splitlines()
        if len(df_lines) < 2:
            raise ValueError("df did not report a filesystem device")
        device = df_lines[-1].split(maxsplit=1)[0]
        if not device.startswith("/dev/"):
            raise ValueError("df reported an unsafe filesystem device")
        diskutil_completed = subprocess.run(
            ["/usr/sbin/diskutil", "info", "-plist", device],
            check=True,
            capture_output=True,
        )
        payload = plistlib.loads(diskutil_completed.stdout)
        filesystem_type = payload.get("FilesystemType")
        if not isinstance(filesystem_type, str) or not filesystem_type:
            raise ValueError("diskutil did not report a filesystem type")
    except (OSError, ValueError, plistlib.InvalidFileException, subprocess.SubprocessError) as exc:
        raise RehearsalSafetyError(
            "filesystem_not_apfs",
            "could not verify APFS rehearsal filesystem",
        ) from exc
    return filesystem_type.lower()


def assert_free_space(
    rehearsal_dir: Path,
    *,
    min_free_bytes: int = MIN_FREE_BYTES,
) -> int:
    if min_free_bytes < MIN_FREE_BYTES:
        raise RehearsalSafetyError(
            "insufficient_free_space",
            "free-space reserve cannot be lower than the global minimum",
        )
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
    if min_free_bytes < MIN_FREE_BYTES:
        raise RehearsalSafetyError(
            "insufficient_free_space",
            "free-space reserve cannot be lower than the global minimum",
        )
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
    preflight = SourcePreflight(
        source=source,
        rehearsal_dir=resolved_rehearsal_dir,
        free_bytes=free_bytes,
        filesystem_type=filesystem_type,
    )
    _register_preflight_approval(
        preflight,
        repository_root=repository,
        enforced_min_free_bytes=min_free_bytes,
    )
    return preflight


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
    approval = _require_preflight_approval(preflight)
    target = preflight.rehearsal_dir / target_name
    if target.name != target_name or target.parent != preflight.rehearsal_dir:
        raise RehearsalSafetyError(
            "unsafe_rehearsal_directory",
            "clone target must remain inside the rehearsal directory",
        )
    refreshed = preflight_rehearsal(
        preflight.source.path,
        preflight.rehearsal_dir,
        repository_root=approval.repository_root,
        min_free_bytes=approval.enforced_min_free_bytes,
    )
    if refreshed.source != preflight.source:
        raise RehearsalSafetyError(
            "source_changed",
            "source database identity changed after preflight",
        )
    if refreshed.rehearsal_dir != preflight.rehearsal_dir:
        raise RehearsalSafetyError(
            "unsafe_rehearsal_directory",
            "rehearsal approval paths do not match strict resolution",
        )
    if refreshed.filesystem_type != preflight.filesystem_type:
        raise RehearsalSafetyError(
            "filesystem_not_apfs",
            "rehearsal filesystem identity changed after preflight",
        )

    staging_dir = Path(
        tempfile.mkdtemp(
            prefix=".kreports-clone-",
            dir=refreshed.rehearsal_dir,
        )
    )
    os.chmod(staging_dir, 0o700)
    staged_clone = staging_dir / target_name
    try:
        current_source = inspect_source_database(preflight.source.path)
        if current_source != preflight.source:
            raise RehearsalSafetyError(
                "source_changed",
                "source database identity changed immediately before clone",
            )
        try:
            subprocess.run(
                [
                    "/bin/cp",
                    "-c",
                    str(preflight.source.path),
                    str(staged_clone),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RehearsalSafetyError(
                "clonefile_unsupported",
                "APFS clonefile operation is unavailable",
            ) from exc

        staged_identity = _inspect_clone_target(staged_clone, preflight.source)
        if staged_identity.sha256 != preflight.source.sha256:
            raise RehearsalSafetyError(
                "clone_identity_mismatch",
                "clone digest does not match the approved source digest",
            )
        assert_source_unchanged(preflight.source)
        try:
            os.link(staged_clone, target)
        except FileExistsError as exc:
            raise RehearsalSafetyError(
                "target_exists",
                "rehearsal clone target already exists",
            ) from exc
        except OSError as exc:
            raise RehearsalSafetyError(
                "clone_identity_mismatch",
                "clone target could not be installed atomically",
            ) from exc
        staged_clone.unlink()

        clone = _inspect_clone_target(target, preflight.source)
        if clone.sha256 != preflight.source.sha256:
            raise RehearsalSafetyError(
                "clone_identity_mismatch",
                "installed clone digest does not match the approved source",
            )
        return clone
    finally:
        if os.path.lexists(staged_clone):
            staged_clone.unlink()
        staging_dir.rmdir()


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
