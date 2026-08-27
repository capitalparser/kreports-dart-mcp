from __future__ import annotations

import gzip
import hashlib
from io import BytesIO
from pathlib import Path
import subprocess

import pytest

from kreports.storage.drive_archive import (
    DriveArchive,
    DriveArchiveConfigurationError,
    DriveArchiveProvenanceError,
    DriveArchiveUploadError,
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
        self.config_calls: list[str] = []
        self.copyto_commands: list[list[str]] = []
        self.uploaded_payloads: list[bytes] = []
        self.corrupt_reads = False
        self.remote_type = "drive"
        self.reject_oversized_metadata = False
        self.copyto_error: subprocess.CalledProcessError | None = None
        self.post_copy_missing_reads = 0

    def run(self, args: list[str]) -> bytes:
        command = args[1]
        if command == "copyto":
            source, destination = args[2:4]
            self.copyto_calls.append((source, destination))
            self.copyto_commands.append(args)
            metadata = {
                args[index + 1].split("=", 1)[0]: args[index + 1].split("=", 1)[1]
                for index, value in enumerate(args)
                if value == "--metadata-set"
            }
            self.metadata_calls.append((destination, metadata))
            if self.reject_oversized_metadata and any(
                len(key.encode("utf-8")) + len(value.encode("utf-8")) > 124
                for key, value in metadata.items()
            ):
                raise subprocess.CalledProcessError(
                    1, args, stderr=b"PropertyLengthLimitExceeded"
                )
            if self.copyto_error is not None:
                raise self.copyto_error
            compressed = Path(source).read_bytes()
            self.uploaded_payloads.append(compressed)
            self.objects[destination] = compressed
            return b""
        if command == "config":
            assert args[2] == "show"
            self.config_calls.append(args[3])
            return f"[{args[3]}]\ntype = {self.remote_type}\n".encode()
        if command == "cat":
            storage_uri = args[2]
            self.cat_calls.append(storage_uri)
            try:
                compressed = self.objects[storage_uri]
            except KeyError as exc:
                raise FileNotFoundError(storage_uri) from exc
            if self.post_copy_missing_reads:
                self.post_copy_missing_reads -= 1
                raise FileNotFoundError(storage_uri)
            if self.corrupt_reads:
                return gzip.compress(b"x" * len(gzip.decompress(compressed)))
            return compressed
        raise AssertionError(f"unexpected rclone command: {args}")


@pytest.fixture(autouse=True)
def _allow_collector_archive(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    monkeypatch.setenv("KREPORTS_ENABLE_RAW_BACKFILL", "1")


def _source_metadata() -> dict[str, str]:
    return {
        "source_receipt": "20260828000001",
        "source_uri": "https://dart.example.test/20260828000001",
        "archive_version": "annual-source-archive-v1",
    }


def _deterministic_gzip(data: bytes) -> bytes:
    output = BytesIO()
    with gzip.GzipFile(fileobj=output, mode="wb", filename="", mtime=0) as compressed:
        compressed.write(data)
    return output.getvalue()


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
        metadata=_source_metadata(),
    )
    second = archive.archive_bytes(
        data=data,
        extension="xml",
        metadata=_source_metadata(),
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
    assert runner.cat_calls == [first.storage_uri, first.storage_uri, first.storage_uri]
    assert runner.metadata_calls == [
        (
            first.storage_uri,
            {
                **_source_metadata(),
                "sha256": expected_sha256,
                "byte_length": str(len(data)),
                "compressed_length": str(first.compressed_length),
            },
        )
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
        archive.archive_bytes(
            data=b"official filing bytes",
            extension="xml",
            metadata=_source_metadata(),
        )

    assert len(runner.copyto_calls) == 1
    assert len(runner.cat_calls) == 2
    assert len(list(tmp_path.iterdir())) == 1


def test_archive_metadata_omits_an_oversized_optional_container_uri_from_drive_transport(tmp_path: Path):
    """An optional index value past Drive's limit must not reject verified bytes."""
    runner = FakeRcloneRunner()
    runner.reject_oversized_metadata = True
    archive = DriveArchive(
        remote="team-drive:", root="kreports/raw", spool_dir=tmp_path, runner=runner
    )
    container_storage_uri = "drive:containers/" + "x" * 120

    archive.archive_bytes(
        data=b"official filing bytes",
        extension="xml",
        metadata={**_source_metadata(), "container_storage_uri": container_storage_uri},
    )

    assert len(runner.copyto_calls) == 1
    assert "container_storage_uri" not in runner.metadata_calls[0][1]
    assert list(tmp_path.iterdir()) == []


def test_archive_metadata_rejects_an_oversized_required_source_uri_before_copyto(tmp_path: Path):
    """A long source locator is required provenance and must fail rather than truncate."""
    runner = FakeRcloneRunner()
    archive = DriveArchive(
        remote="team-drive:", root="kreports/raw", spool_dir=tmp_path, runner=runner
    )

    with pytest.raises(DriveArchiveProvenanceError, match="source_uri"):
        archive.archive_bytes(
            data=b"official filing bytes",
            extension="xml",
            metadata={**_source_metadata(), "source_uri": "https://dart.example.test/" + "x" * 120},
        )

    assert runner.copyto_calls == []
    assert list(tmp_path.iterdir()) == []


def test_archive_upload_reports_copy_failure_after_missing_readback_and_keeps_spool(tmp_path: Path):
    """A rejected copy must retain its reason and local recovery object when absent remotely."""
    runner = FakeRcloneRunner()
    runner.copyto_error = subprocess.CalledProcessError(
        1, ["rclone", "copyto"], stderr=b"PropertyLengthLimitExceeded"
    )
    archive = DriveArchive(
        remote="team-drive:", root="kreports/raw", spool_dir=tmp_path, runner=runner
    )

    with pytest.raises(DriveArchiveUploadError, match="upload failed.*PropertyLengthLimitExceeded"):
        archive.archive_bytes(data=b"official filing bytes", extension="xml", metadata=_source_metadata())

    assert len(runner.copyto_calls) == 1
    assert len(list(tmp_path.iterdir())) == 1


def test_archive_upload_diagnostic_redacts_bearer_and_assignment_credentials(tmp_path: Path):
    """A failed upload diagnostic must never expose copy-command credentials."""
    runner = FakeRcloneRunner()
    runner.copyto_error = subprocess.CalledProcessError(
        1,
        ["rclone", "copyto"],
        stderr=(
            b"PropertyLengthLimitExceeded\n"
            b"Authorization: Bearer super-secret-value\n"
            b"access_token=super-secret-value\n"
            b"client_secret=super-secret-value"
        ),
    )
    archive = DriveArchive(
        remote="team-drive:", root="kreports/raw", spool_dir=tmp_path, runner=runner
    )

    with pytest.raises(DriveArchiveUploadError) as exc:
        archive.archive_bytes(data=b"official filing bytes", extension="xml", metadata=_source_metadata())

    message = str(exc.value)
    assert "PropertyLengthLimitExceeded" in message
    assert "super-secret-value" not in message
    assert "Bearer [REDACTED]" in message
    assert "access_token=[REDACTED]" in message
    assert "client_secret=[REDACTED]" in message


def test_archive_retries_one_missing_post_copy_readback_without_a_second_upload(tmp_path: Path):
    """Drive visibility lag after a completed copy must not cause a duplicate immutable upload."""
    runner = FakeRcloneRunner()
    runner.post_copy_missing_reads = 1
    sleeps: list[float] = []
    archive = DriveArchive(
        remote="team-drive:",
        root="kreports/raw",
        spool_dir=tmp_path,
        runner=runner,
        sleeper=sleeps.append,
    )

    archive.archive_bytes(data=b"official filing bytes", extension="xml", metadata=_source_metadata())

    assert len(runner.copyto_calls) == 1
    assert len(sleeps) == 1
    assert list(tmp_path.iterdir()) == []


def test_archive_retry_configuration_rejects_more_than_two_readbacks(tmp_path: Path):
    """Allowing a third retry would violate the bounded Drive visibility contract."""
    with pytest.raises(DriveArchiveConfigurationError, match="at most two"):
        DriveArchive(
            remote="team-drive:",
            root="kreports/raw",
            spool_dir=tmp_path,
            runner=FakeRcloneRunner(),
            readback_retries=3,
        )


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


def test_drive_archive_from_runtime_validates_the_configured_rclone_remote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Configured target validation must reject a non-Drive rclone remote early."""
    runner = FakeRcloneRunner()
    runner.remote_type = "s3"
    monkeypatch.setenv("RAW_STORAGE_BACKEND", "drive")
    monkeypatch.setenv("RAW_STORAGE_DRIVE_REMOTE", "team-drive:")
    monkeypatch.setenv("RAW_STORAGE_PREFIX", "kreports/raw")
    monkeypatch.setenv("RAW_STORAGE_SPOOL_DIR", str(tmp_path))

    with pytest.raises(DriveArchiveConfigurationError, match="type=drive"):
        drive_archive_from_runtime(runner=runner)

    assert runner.config_calls == ["team-drive"]
    assert runner.copyto_calls == []
    assert runner.cat_calls == []


@pytest.mark.parametrize(
    ("runtime_mode", "raw_backfill", "error"),
    [
        ("readonly", "1", "requires collector mode"),
        ("collector", "", "KREPORTS_ENABLE_RAW_BACKFILL=1"),
    ],
)
def test_drive_archive_from_runtime_blocks_unauthorized_runtime_before_rclone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runtime_mode: str,
    raw_backfill: str,
    error: str,
):
    """Public/read-only and non-opted-in processes cannot construct an archive."""
    runner = FakeRcloneRunner()
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", runtime_mode)
    monkeypatch.setenv("KREPORTS_ENABLE_RAW_BACKFILL", raw_backfill)
    monkeypatch.setenv("RAW_STORAGE_BACKEND", "drive")
    monkeypatch.setenv("RAW_STORAGE_DRIVE_REMOTE", "team-drive:")
    monkeypatch.setenv("RAW_STORAGE_PREFIX", "kreports/raw")
    monkeypatch.setenv("RAW_STORAGE_SPOOL_DIR", str(tmp_path))

    with pytest.raises(RuntimeError, match=error):
        drive_archive_from_runtime(runner=runner)

    assert runner.config_calls == []
    assert runner.copyto_calls == []
    assert runner.cat_calls == []


def test_archive_bytes_blocks_readonly_runtime_before_target_or_spool_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Constructing directly cannot bypass the collector-only write boundary."""
    runner = FakeRcloneRunner()
    archive = DriveArchive(
        remote="team-drive:", root="kreports/raw", spool_dir=tmp_path, runner=runner
    )
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    with pytest.raises(RuntimeError, match="requires collector mode"):
        archive.archive_bytes(data=b"official filing bytes", extension="xml", metadata=_source_metadata())

    assert runner.config_calls == []
    assert runner.copyto_calls == []
    assert runner.cat_calls == []
    assert list(tmp_path.iterdir()) == []


def test_existing_object_from_a_new_archive_instance_is_verified_without_overwrite(tmp_path: Path):
    """A restart must verify an immutable object rather than copy over it."""
    runner = FakeRcloneRunner()
    first = DriveArchive(
        remote="team-drive:", root="kreports/raw", spool_dir=tmp_path, runner=runner
    ).archive_bytes(data=b"official filing bytes", extension="xml", metadata=_source_metadata())
    second = DriveArchive(
        remote="team-drive:", root="kreports/raw", spool_dir=tmp_path, runner=runner
    ).archive_bytes(data=b"official filing bytes", extension="xml", metadata=_source_metadata())

    assert first == second
    assert len(runner.copyto_calls) == 1
    assert "--immutable" in runner.copyto_commands[0]
    assert runner.uploaded_payloads == [_deterministic_gzip(b"official filing bytes")]


def test_archive_rejects_a_configured_rclone_remote_that_is_not_drive(tmp_path: Path):
    """A syntactically plausible target is unsafe unless rclone declares type=drive."""
    runner = FakeRcloneRunner()
    runner.remote_type = "s3"
    archive = DriveArchive(
        remote="team-drive:", root="kreports/raw", spool_dir=tmp_path, runner=runner
    )

    with pytest.raises(DriveArchiveConfigurationError, match="type=drive"):
        archive.archive_bytes(data=b"official filing bytes", extension="xml", metadata=_source_metadata())

    assert runner.config_calls == ["team-drive"]
    assert runner.copyto_calls == []
    assert runner.cat_calls == []
    assert list(tmp_path.iterdir()) == []


def test_archive_requires_complete_source_provenance_before_running_rclone(tmp_path: Path):
    """Bytes without receipt, source URI, and archive version cannot become evidence."""
    runner = FakeRcloneRunner()
    archive = DriveArchive(
        remote="team-drive:", root="kreports/raw", spool_dir=tmp_path, runner=runner
    )

    with pytest.raises(DriveArchiveProvenanceError, match="source_uri"):
        archive.archive_bytes(
            data=b"official filing bytes",
            extension="xml",
            metadata={"source_receipt": "20260828000001", "archive_version": "v1"},
        )

    assert runner.config_calls == []
    assert runner.copyto_calls == []
    assert runner.cat_calls == []
    assert list(tmp_path.iterdir()) == []
