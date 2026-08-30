from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path

import pytest

from kreports.maintenance.database_archive_lifecycle import (
    DatabaseArchiveLifecycle,
    DatabaseArchiveSafetyError,
)
from kreports.storage.drive_archive import ArchivedObject


class RecordingArchive:
    def __init__(self) -> None:
        self.calls: list[Path] = []

    def archive_file(self, *, path: Path, metadata: dict[str, str]) -> ArchivedObject:
        self.calls.append(path)
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        return ArchivedObject(
            storage_uri=f"vault:KReports Data Lake/db-archive/{digest}.db",
            object_path=f"objects/sha256/{digest}.db",
            sha256=digest,
            byte_length=len(data),
            compressed_length=len(data),
        )


def _lifecycle(tmp_path: Path, *, now: datetime) -> DatabaseArchiveLifecycle:
    return DatabaseArchiveLifecycle(
        candidate_roots=(tmp_path / "candidates",),
        protected_paths=(tmp_path / "releases" / "active.db",),
        ledger_path=tmp_path / "ledger" / "db-archive.jsonl",
        grace_period=timedelta(days=7),
        now=lambda: now,
    )


def test_plan_excludes_protected_and_sqlite_sidecars(tmp_path: Path):
    """A planner must never treat the active artifact or a SQLite sidecar as movable."""
    now = datetime(2026, 8, 30, tzinfo=UTC)
    candidates = tmp_path / "candidates"
    releases = tmp_path / "releases"
    candidates.mkdir()
    releases.mkdir()
    protected = releases / "active.db"
    protected.write_bytes(b"active")
    inactive = candidates / "candidate-2024.db"
    inactive.write_bytes(b"candidate")
    (candidates / "orphan.db-wal").write_bytes(b"wal")
    (candidates / "orphan.db-shm").write_bytes(b"shm")

    plan = _lifecycle(tmp_path, now=now).plan()

    assert plan.eligible == [inactive]
    assert plan.protected_paths == [protected]
    assert plan.excluded_sidecars == [
        candidates / "orphan.db-shm",
        candidates / "orphan.db-wal",
    ]


def test_archive_then_prune_requires_verified_ledger_and_grace_period(tmp_path: Path):
    """Local deletion is a second step after immutable Drive verification and grace."""
    archived_at = datetime(2026, 8, 30, tzinfo=UTC)
    candidate_root = tmp_path / "candidates"
    candidate_root.mkdir()
    candidate = candidate_root / "candidate-2023.db"
    candidate.write_bytes(b"durable candidate")
    archive = RecordingArchive()
    lifecycle = _lifecycle(tmp_path, now=archived_at)

    archived = lifecycle.archive(archive)

    assert archive.calls == [candidate]
    assert candidate.exists()
    assert archived.archived_paths == [candidate]
    ledger = [json.loads(line) for line in lifecycle.ledger_path.read_text().splitlines()]
    assert ledger[0]["verification_status"] == "verified"
    assert ledger[0]["local_pruned_at"] is None

    before_grace = _lifecycle(tmp_path, now=archived_at + timedelta(days=6))
    assert before_grace.prune().pruned_paths == []
    assert candidate.exists()

    after_grace = _lifecycle(tmp_path, now=archived_at + timedelta(days=7))
    pruned = after_grace.prune()

    assert pruned.pruned_paths == [candidate]
    assert not candidate.exists()


def test_archive_refuses_a_candidate_with_sqlite_wal(tmp_path: Path):
    """A live or uncheckpointed SQLite artifact is not a safe archival source."""
    now = datetime(2026, 8, 30, tzinfo=UTC)
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    candidate = candidates / "candidate-2025.db"
    candidate.write_bytes(b"candidate")
    candidate.with_name("candidate-2025.db-wal").write_bytes(b"uncheckpointed")

    with pytest.raises(DatabaseArchiveSafetyError, match="SQLite sidecar"):
        _lifecycle(tmp_path, now=now).archive(RecordingArchive())


def test_capacity_prune_continues_from_minimum_threshold_to_target_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Once pressure starts a prune, it keeps reclaiming until the target is met."""
    archived_at = datetime(2026, 8, 30, tzinfo=UTC)
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    (candidates / "a.db").write_bytes(b"a")
    (candidates / "b.db").write_bytes(b"b")
    _lifecycle(tmp_path, now=archived_at).archive(RecordingArchive())
    reported_free = iter((19 * 1024**3, 19 * 1024**3, 22 * 1024**3))
    monkeypatch.setattr(
        "kreports.maintenance.database_archive_lifecycle._free_bytes",
        lambda _path: next(reported_free),
    )
    monkeypatch.setattr(
        "kreports.maintenance.database_archive_lifecycle._is_open_by_a_process",
        lambda _path: False,
    )

    result = _lifecycle(tmp_path, now=archived_at + timedelta(days=7)).prune(
        only_when_below_free_bytes=20 * 1024**3,
        target_free_bytes=25 * 1024**3,
    )

    assert result.pruned_paths == [candidates / "a.db", candidates / "b.db"]


def test_cli_plan_is_read_only_and_discloses_only_eligible_database_artifacts(tmp_path: Path):
    """Operators can inspect the exact archive set before any Drive access or deletion."""
    from typer.testing import CliRunner

    from kreports.cli.main import app

    candidates = tmp_path / "candidates"
    candidates.mkdir()
    candidate = candidates / "candidate.db"
    candidate.write_bytes(b"candidate")
    ledger = tmp_path / "ledger" / "archive.jsonl"

    result = CliRunner().invoke(app, [
        "db-archive-plan", "--candidate-root", str(candidates), "--ledger", str(ledger),
    ])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["eligible_paths"] == [str(candidate)]
    assert not ledger.exists()
