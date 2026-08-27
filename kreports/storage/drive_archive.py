"""Immutable, content-addressed archive storage through a local rclone remote."""

from __future__ import annotations

from dataclasses import dataclass
import gzip
import hashlib
from io import BytesIO
from pathlib import Path
import subprocess
from tempfile import NamedTemporaryFile
from typing import Mapping, Protocol


__all__ = [
    "ArchivedObject",
    "CommandRunner",
    "DriveArchive",
    "DriveArchiveConfigurationError",
    "DriveArchiveProvenanceError",
    "DriveArchiveVerificationError",
    "drive_archive_from_runtime",
]


class DriveArchiveConfigurationError(ValueError):
    """Raised when immutable Drive archive storage is not configured safely."""


class DriveArchiveProvenanceError(ValueError):
    """Raised when source evidence lacks the minimum archive provenance."""


class DriveArchiveVerificationError(RuntimeError):
    """Raised when a remote archive object differs from its expected source."""


class _DriveArchiveObjectMissing(DriveArchiveVerificationError):
    """Internal signal used to distinguish an absent immutable object."""


class CommandRunner(Protocol):
    """Small rclone command boundary so archive behavior stays deterministic."""

    def run(self, args: list[str]) -> bytes:
        """Run a command and return its stdout bytes."""


class SubprocessCommandRunner:
    """Execute rclone without a shell or embedded archive credentials."""

    def run(self, args: list[str]) -> bytes:
        return subprocess.run(args, check=True, capture_output=True).stdout


@dataclass(frozen=True)
class ArchivedObject:
    storage_uri: str
    object_path: str
    sha256: str
    byte_length: int
    compressed_length: int


class DriveArchive:
    """Write only verified source bytes to an immutable Drive object path."""

    def __init__(
        self,
        remote: str,
        root: str,
        spool_dir: Path,
        runner: CommandRunner,
    ) -> None:
        self.remote = remote.strip()
        self.root = root.strip("/")
        self.spool_dir = Path(spool_dir)
        self.runner = runner
        self._verified_objects: set[str] = set()
        self._target_validated = False

        if not self.remote:
            raise DriveArchiveConfigurationError("Drive archive remote must not be blank")
        if not self.root:
            raise DriveArchiveConfigurationError("Drive archive root must not be blank")

    def archive_bytes(
        self,
        *,
        data: bytes,
        extension: str,
        metadata: Mapping[str, str],
    ) -> ArchivedObject:
        """Archive one payload and retain its spool copy until readback verifies."""
        from kreports.runtime import require_drive_archive_mode

        require_drive_archive_mode("archive raw source bytes to Drive")
        normalized_extension = _normalized_extension(extension)
        sha256 = hashlib.sha256(data).hexdigest()
        object_path = (
            f"objects/sha256/{sha256[:2]}/{sha256[2:4]}/"
            f"{sha256}.{normalized_extension}.gz"
        )
        storage_uri = self._storage_uri(object_path)
        compressed = _deterministic_gzip(data)
        archive_metadata = _archive_metadata(
            metadata,
            sha256=sha256,
            byte_length=len(data),
            compressed_length=len(compressed),
        )
        self._validate_drive_remote()

        if storage_uri not in self._verified_objects:
            try:
                self.verify_object(
                    storage_uri,
                    expected_sha256=sha256,
                    expected_bytes=len(data),
                )
            except _DriveArchiveObjectMissing:
                self._create_and_verify(
                    compressed=compressed,
                    storage_uri=storage_uri,
                    extension=normalized_extension,
                    metadata=archive_metadata,
                    sha256=sha256,
                    byte_length=len(data),
                )
            self._verified_objects.add(storage_uri)
        else:
            self.verify_object(
                storage_uri,
                expected_sha256=sha256,
                expected_bytes=len(data),
            )

        return ArchivedObject(
            storage_uri=storage_uri,
            object_path=object_path,
            sha256=sha256,
            byte_length=len(data),
            compressed_length=len(compressed),
        )

    def verify_object(
        self,
        storage_uri: str,
        expected_sha256: str,
        expected_bytes: int,
    ) -> None:
        """Read and validate a compressed remote object before accepting it."""
        try:
            compressed = self.runner.run(["rclone", "cat", storage_uri])
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            raise _DriveArchiveObjectMissing(
                f"Drive archive object is missing: {storage_uri}"
            ) from exc
        try:
            data = gzip.decompress(compressed)
        except (OSError, EOFError) as exc:
            raise DriveArchiveVerificationError(
                f"Drive archive readback could not be decompressed: {storage_uri}"
            ) from exc

        if len(data) != expected_bytes:
            raise DriveArchiveVerificationError(
                f"Drive archive byte length mismatch: expected {expected_bytes}, got {len(data)}"
            )
        actual_sha256 = hashlib.sha256(data).hexdigest()
        if actual_sha256 != expected_sha256:
            raise DriveArchiveVerificationError(
                "Drive archive SHA-256 mismatch: "
                f"expected {expected_sha256}, got {actual_sha256}"
            )

    def _create_and_verify(
        self,
        *,
        compressed: bytes,
        storage_uri: str,
        extension: str,
        metadata: Mapping[str, str],
        sha256: str,
        byte_length: int,
    ) -> None:
        spool_path = self._write_spool_file(compressed, suffix=f".{extension}.gz")
        command = [
            "rclone",
            "copyto",
            str(spool_path),
            storage_uri,
            "--immutable",
            "--metadata",
        ]
        for key, value in sorted(metadata.items()):
            command.extend(["--metadata-set", f"{key}={value}"])
        try:
            self.runner.run(command)
        except subprocess.CalledProcessError:
            try:
                self.verify_object(
                    storage_uri,
                    expected_sha256=sha256,
                    expected_bytes=byte_length,
                )
            except DriveArchiveVerificationError:
                raise
        else:
            self.verify_object(
                storage_uri,
                expected_sha256=sha256,
                expected_bytes=byte_length,
            )
        spool_path.unlink()

    def _write_spool_file(self, compressed: bytes, *, suffix: str) -> Path:
        self.spool_dir.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            mode="wb", dir=self.spool_dir, prefix="drive-archive-", suffix=suffix, delete=False
        ) as spool:
            spool.write(compressed)
            return Path(spool.name)

    def _storage_uri(self, object_path: str) -> str:
        if self.remote.endswith(":"):
            return f"{self.remote}{self.root}/{object_path}"
        return f"{self.remote.rstrip('/')}/{self.root}/{object_path}"

    def _validate_drive_remote(self) -> None:
        if self._target_validated:
            return
        if not self.remote.endswith(":") or self.remote.count(":") != 1:
            raise DriveArchiveConfigurationError(
                "Drive archive remote must be a named rclone remote ending in ':'."
            )
        remote_name = self.remote[:-1]
        if not remote_name or "/" in remote_name:
            raise DriveArchiveConfigurationError(
                "Drive archive remote must be a named rclone remote ending in ':'."
            )
        try:
            configuration = self.runner.run(["rclone", "config", "show", remote_name]).decode()
        except (OSError, UnicodeDecodeError, subprocess.CalledProcessError) as exc:
            raise DriveArchiveConfigurationError(
                f"Drive archive remote could not be validated: {self.remote}"
            ) from exc
        remote_type = next(
            (
                value.strip().lower()
                for line in configuration.splitlines()
                if line.partition("=")[0].strip() == "type"
                for value in [line.partition("=")[2]]
            ),
            "",
        )
        if remote_type != "drive":
            raise DriveArchiveConfigurationError(
                "Drive archive remote must declare rclone type=drive."
            )
        self._target_validated = True


def drive_archive_from_runtime(*, runner: CommandRunner | None = None) -> DriveArchive:
    """Build the collector-only Drive archive from deploy-time configuration."""
    from kreports.runtime import drive_archive_policy, require_drive_archive_mode

    require_drive_archive_mode("build Drive source archive")
    backend, remote, root, spool_dir = drive_archive_policy()
    if backend != "drive":
        raise DriveArchiveConfigurationError(
            "Drive archive requires RAW_STORAGE_BACKEND=drive."
        )
    if not remote:
        raise DriveArchiveConfigurationError(
            "Drive archive requires RAW_STORAGE_DRIVE_REMOTE."
        )
    if not root:
        raise DriveArchiveConfigurationError(
            "Drive archive requires RAW_STORAGE_PREFIX."
        )
    if not spool_dir:
        raise DriveArchiveConfigurationError(
            "Drive archive requires RAW_STORAGE_SPOOL_DIR."
        )
    archive = DriveArchive(
        remote=remote,
        root=root,
        spool_dir=Path(spool_dir),
        runner=runner or SubprocessCommandRunner(),
    )
    archive._validate_drive_remote()
    return archive


def _normalized_extension(extension: str) -> str:
    normalized = extension.strip().lstrip(".")
    if not normalized or not normalized.replace("_", "").replace("-", "").isalnum():
        raise ValueError("archive extension must contain only letters, digits, underscores, or hyphens")
    return normalized


def _deterministic_gzip(data: bytes) -> bytes:
    output = BytesIO()
    with gzip.GzipFile(fileobj=output, mode="wb", filename="", mtime=0) as compressed:
        compressed.write(data)
    return output.getvalue()


def _archive_metadata(
    metadata: Mapping[str, str],
    *,
    sha256: str,
    byte_length: int,
    compressed_length: int,
) -> dict[str, str]:
    required = ("source_receipt", "source_uri", "archive_version")
    missing = [key for key in required if not metadata.get(key, "").strip()]
    if missing:
        raise DriveArchiveProvenanceError(
            "Drive archive requires provenance metadata: " + ", ".join(missing)
        )
    archived = dict(metadata)
    archived.update(
        {
            "sha256": sha256,
            "byte_length": str(byte_length),
            "compressed_length": str(compressed_length),
        }
    )
    return archived
