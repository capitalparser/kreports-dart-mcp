from __future__ import annotations

import gzip
import hashlib
from pathlib import Path

import pytest

from kreports.storage.drive_archive import (
    DriveArchive,
    DriveArchiveConfigurationError,
    DriveArchiveVerificationError,
    drive_archive_from_runtime,
)


class FakeRcloneRunner:
    """In-memory rclone boundary that records immutable archive operations."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.copyto_calls: list[tuple[str, str]] = []
        self.cat_calls: list[str] = []
        self.metadata_calls: list[tuple[str, dict[str, str]]] = []
        self.corrupt_reads = False

    def run(self, args: list[str]) -> bytes:
        command = args[1]
        if command == "copyto":
            source, destination = args[2:4]
            self.copyto_calls.append((source, destination))
            metadata = {
                args[index + 1].split("=", 1)[0]: args[index + 1].split("=", 1)[1]
                for index, value in enumerate(args)
                if value == "--metadata-set"
            }
            self.metadata_calls.append((destination, metadata))
            self.objects[destination] = Path(source).read_bytes()
            return b""
        if command == "cat":
            storage_uri = args[2]
            self.cat_calls.append(storage_uri)
            compressed = self.objects[storage_uri]
            if self.corrupt_reads:
                return gzip.compress(b"x" * len(gzip.decompress(compressed)))
            return compressed
        raise AssertionError(f"unexpected rclone command: {args}")


def test_archive_bytes_is_content_addressed_uploaded_once_and_verified(tmp_path: Path):
    """A changed hash path, duplicate upload, or unverified readback is a bug."""
    runner = FakeRcloneRunner()
    archive = DriveArchive(
        remote="team-drive:",
        root="kreports/raw",
        spool_dir=tmp_path,
        runner=runner,
    )
    data = b"official filing bytes"
    expected_sha256 = hashlib.sha256(data).hexdigest()

    first = archive.archive_bytes(
        data=data,
        extension="xml",
        metadata={"source": "dart", "receipt": "20260828000001"},
    )
    second = archive.archive_bytes(
        data=data,
        extension="xml",
        metadata={"source": "dart", "receipt": "20260828000001"},
    )

    expected_path = (
        f"objects/sha256/{expected_sha256[:2]}/{expected_sha256[2:4]}/"
        f"{expected_sha256}.xml.gz"
    )
    assert first == second
    assert first.storage_uri == f"team-drive:kreports/raw/{expected_path}"
    assert first.object_path == expected_path
    assert first.sha256 == expected_sha256
    assert first.byte_length == len(data)
    assert first.compressed_length == len(gzip.compress(data))
    assert len(runner.copyto_calls) == 1
    assert runner.cat_calls == [first.storage_uri, first.storage_uri]
    assert runner.metadata_calls == [
        (first.storage_uri, {"source": "dart", "receipt": "20260828000001"})
    ]
    assert list(tmp_path.iterdir()) == []


def test_archive_bytes_rejects_a_mismatched_readback_and_keeps_the_spool_file(tmp_path: Path):
    """Deleting an unverified upload would remove the only local recovery copy."""
    runner = FakeRcloneRunner()
    runner.corrupt_reads = True
    archive = DriveArchive(
        remote="team-drive:",
        root="kreports/raw",
        spool_dir=tmp_path,
        runner=runner,
    )

    with pytest.raises(DriveArchiveVerificationError, match="SHA-256 mismatch"):
        archive.archive_bytes(data=b"official filing bytes", extension="xml", metadata={})

    assert len(runner.copyto_calls) == 1
    assert len(runner.cat_calls) == 1
    assert len(list(tmp_path.iterdir())) == 1


@pytest.mark.parametrize("remote, root", [("", "kreports/raw"), ("team-drive:", "")])
def test_archive_rejects_missing_drive_target_before_calling_runner(
    tmp_path: Path, remote: str, root: str
):
    """A blank remote or root must not trigger a storage command."""
    runner = FakeRcloneRunner()

    with pytest.raises(DriveArchiveConfigurationError):
        DriveArchive(remote=remote, root=root, spool_dir=tmp_path, runner=runner)

    assert runner.copyto_calls == []
    assert runner.cat_calls == []
    assert runner.metadata_calls == []


def test_drive_archive_from_runtime_requires_a_drive_target_before_runner_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A non-Drive backend or absent Drive remote must fail before rclone runs."""
    runner = FakeRcloneRunner()
    monkeypatch.setenv("RAW_STORAGE_BACKEND", "drive")
    monkeypatch.delenv("RAW_STORAGE_DRIVE_REMOTE", raising=False)
    monkeypatch.setenv("RAW_STORAGE_PREFIX", "kreports/raw")
    monkeypatch.setenv("RAW_STORAGE_SPOOL_DIR", str(tmp_path))

    with pytest.raises(DriveArchiveConfigurationError, match="RAW_STORAGE_DRIVE_REMOTE"):
        drive_archive_from_runtime(runner=runner)

    monkeypatch.setenv("RAW_STORAGE_BACKEND", "gcs")
    monkeypatch.setenv("RAW_STORAGE_DRIVE_REMOTE", "team-drive:")
    with pytest.raises(DriveArchiveConfigurationError, match="RAW_STORAGE_BACKEND=drive"):
        drive_archive_from_runtime(runner=runner)

    assert runner.copyto_calls == []
    assert runner.cat_calls == []
    assert runner.metadata_calls == []
