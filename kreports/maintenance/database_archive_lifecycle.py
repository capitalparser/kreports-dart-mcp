"""Verified, bounded lifecycle management for inactive local SQLite artifacts.

The service owns only maintainer-side candidate/release archival.  It never
opens the public runtime database for writing and Google Drive remains an
immutable archive, not a SQLite serving layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Callable, Protocol

from kreports.storage.drive_archive import ArchivedObject

__all__ = [
    "DatabaseArchiveLifecycle",
    "DatabaseArchivePlan",
    "DatabaseArchiveResult",
    "DatabaseArchiveSafetyError",
]


class DatabaseArchiveSafetyError(RuntimeError):
    """Raised before an unsafe local database archive or prune action."""


class DatabaseArchiveWriter(Protocol):
    def archive_file(self, *, path: Path, metadata: dict[str, str]) -> ArchivedObject:
        """Upload and readback-verify one immutable database artifact."""


@dataclass(frozen=True)
class DatabaseArchivePlan:
    eligible: list[Path]
    protected_paths: list[Path]
    excluded_sidecars: list[Path]
    unsafe_paths: list[Path]


@dataclass(frozen=True)
class DatabaseArchiveResult:
    archived_paths: list[Path]
    pruned_paths: list[Path]
    skipped_paths: list[Path]


class DatabaseArchiveLifecycle:
    """Archive inactive SQLite files before a separate, grace-bound prune."""

    def __init__(
        self,
        *,
        candidate_roots: tuple[Path, ...],
        protected_paths: tuple[Path, ...],
        ledger_path: Path,
        grace_period: timedelta = timedelta(days=7),
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if grace_period < timedelta(0):
            raise ValueError("database archive grace period must not be negative")
        self.candidate_roots = tuple(Path(root).expanduser() for root in candidate_roots)
        self.protected_paths = tuple(Path(path).expanduser() for path in protected_paths)
        self.ledger_path = Path(ledger_path).expanduser()
        self.grace_period = grace_period
        self.now = now

    def plan(self) -> DatabaseArchivePlan:
        protected = sorted(
            (path.resolve() for path in self.protected_paths if path.exists()), key=str
        )
        protected_set = set(protected)
        eligible: list[Path] = []
        sidecars: list[Path] = []
        unsafe: list[Path] = []
        for root in self.candidate_roots:
            if not root.exists():
                continue
            for sidecar in sorted(root.rglob("*.db-wal")) + sorted(root.rglob("*.db-shm")) + sorted(root.rglob("*.db-journal")):
                sidecars.append(sidecar.resolve())
            for database in sorted(root.rglob("*.db")):
                resolved = database.resolve()
                if resolved in protected_set:
                    continue
                if _sqlite_has_sidecar(resolved):
                    unsafe.append(resolved)
                    continue
                eligible.append(resolved)
        return DatabaseArchivePlan(
            eligible=sorted(set(eligible), key=str),
            protected_paths=protected,
            excluded_sidecars=sorted(set(sidecars), key=str),
            unsafe_paths=sorted(set(unsafe), key=str),
        )

    def archive(self, archive: DatabaseArchiveWriter) -> DatabaseArchiveResult:
        plan = self.plan()
        if plan.unsafe_paths:
            raise DatabaseArchiveSafetyError(
                "SQLite sidecar present; checkpoint or close the database before archival: "
                + ", ".join(str(path) for path in plan.unsafe_paths)
            )
        records = self._latest_records()
        archived: list[Path] = []
        skipped: list[Path] = []
        for database in plan.eligible:
            sha256, byte_length = _sha256_file(database)
            existing = records.get(str(database))
            if _record_matches(existing, sha256, byte_length):
                skipped.append(database)
                continue
            result = archive.archive_file(
                path=database,
                metadata={
                    "source_receipt": f"local-db-{sha256[:12]}",
                    "source_uri": f"file-sha256://{sha256}",
                    "archive_version": "kreports-db-archive-v1",
                },
            )
            if result.sha256 != sha256 or result.byte_length != byte_length:
                raise DatabaseArchiveSafetyError(
                    f"Drive verification identity differs from local database: {database}"
                )
            record = {
                "schema": "kreports-db-archive-ledger.v1",
                "local_path": str(database),
                "sha256": sha256,
                "byte_length": byte_length,
                "storage_uri": result.storage_uri,
                "object_path": result.object_path,
                "archived_at": self._timestamp(),
                "verification_status": "verified",
                "local_pruned_at": None,
            }
            self._append_record(record)
            records[str(database)] = record
            archived.append(database)
        return DatabaseArchiveResult(archived, [], skipped)

    def prune(
        self,
        *,
        only_when_below_free_bytes: int | None = None,
        target_free_bytes: int | None = None,
    ) -> DatabaseArchiveResult:
        if only_when_below_free_bytes is not None and only_when_below_free_bytes < 0:
            raise ValueError("free-space threshold must not be negative")
        if target_free_bytes is not None and target_free_bytes < 0:
            raise ValueError("target free space must not be negative")
        records = self._latest_records()
        pruned: list[Path] = []
        skipped: list[Path] = []
        if only_when_below_free_bytes is not None:
            first_root = next((root for root in self.candidate_roots if root.exists()), None)
            if first_root is not None and _free_bytes(first_root) >= only_when_below_free_bytes:
                return DatabaseArchiveResult([], [], [])
        for path_text, record in sorted(records.items()):
            database = Path(path_text)
            if not database.exists() or record.get("local_pruned_at"):
                continue
            if database.resolve() in {path.resolve() for path in self.protected_paths if path.exists()}:
                skipped.append(database)
                continue
            if not _record_is_prunable(record, self.now(), self.grace_period):
                skipped.append(database)
                continue
            if _sqlite_has_sidecar(database):
                skipped.append(database)
                continue
            sha256, byte_length = _sha256_file(database)
            if not _record_matches(record, sha256, byte_length):
                skipped.append(database)
                continue
            free_bytes = _free_bytes(database.parent)
            if target_free_bytes is not None and free_bytes >= target_free_bytes:
                break
            if _is_open_by_a_process(database):
                skipped.append(database)
                continue
            database.unlink()
            self._append_record({
                **record,
                "local_pruned_at": self._timestamp(),
                "prune_reason": "verified_archive_grace_elapsed",
            })
            pruned.append(database)
        return DatabaseArchiveResult([], pruned, skipped)

    def _latest_records(self) -> dict[str, dict[str, object]]:
        if not self.ledger_path.exists():
            return {}
        latest: dict[str, dict[str, object]] = {}
        for raw_line in self.ledger_path.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip():
                continue
            record = json.loads(raw_line)
            if record.get("schema") != "kreports-db-archive-ledger.v1":
                raise DatabaseArchiveSafetyError("database archive ledger schema is not recognized")
            path = record.get("local_path")
            if not isinstance(path, str) or not path:
                raise DatabaseArchiveSafetyError("database archive ledger has no local_path")
            latest[path] = record
        return latest

    def _append_record(self, record: dict[str, object]) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger_path.open("a", encoding="utf-8") as ledger:
            ledger.write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")

    def _timestamp(self) -> str:
        return self.now().astimezone(UTC).isoformat().replace("+00:00", "Z")


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    byte_length = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            byte_length += len(chunk)
    return digest.hexdigest(), byte_length


def _sqlite_has_sidecar(path: Path) -> bool:
    return any(path.with_name(path.name + suffix).exists() for suffix in ("-wal", "-shm", "-journal"))


def _record_matches(record: dict[str, object] | None, sha256: str, byte_length: int) -> bool:
    return bool(record and record.get("verification_status") == "verified" and record.get("sha256") == sha256 and record.get("byte_length") == byte_length)


def _record_is_prunable(record: dict[str, object], now: datetime, grace_period: timedelta) -> bool:
    if record.get("verification_status") != "verified" or record.get("local_pruned_at"):
        return False
    archived_at = record.get("archived_at")
    if not isinstance(archived_at, str):
        return False
    try:
        archived = datetime.fromisoformat(archived_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    return now.astimezone(UTC) >= archived.astimezone(UTC) + grace_period


def _free_bytes(path: Path) -> int:
    import shutil
    return shutil.disk_usage(path).free


def _is_open_by_a_process(path: Path) -> bool:
    """Use lsof when available; inability to inspect fails closed during prune."""
    try:
        completed = subprocess.run(
            ["lsof", "--", str(path)], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.TimeoutExpired):
        return True
    return completed.returncode == 0
