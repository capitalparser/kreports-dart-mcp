"""Immutable, content-addressed archive storage through a local rclone remote."""

from __future__ import annotations

from dataclasses import dataclass
import gzip
import hashlib
from io import BytesIO
import os
from pathlib import Path
import re
import stat
import subprocess
from tempfile import NamedTemporaryFile
import time
from typing import Any, Callable, Mapping
import zlib

from kreports.storage.drive_rate_limit import (
    CommandRunner,
    DriveArchiveCommandError,
    DriveArchiveCommandTimeoutError,
    DriveArchiveMetrics,
    DriveArchiveObjectMissing,
    DriveArchivePermissionError,
    DriveArchiveRateLimitError,
    DriveArchiveUploadError,
    DriveArchiveVerificationError,
    DriveArchiveWriterLeaseError,
    DriveCommandGateway,
    DriveWriterLease,
    redacted_drive_diagnostic,
)


__all__ = [
    "ArchivedObject",
    "CommandRunner",
    "DriveArchive",
    "DriveArchiveCommandError",
    "DriveArchiveCommandTimeoutError",
    "DriveArchiveConfigurationError",
    "DriveArchiveMetrics",
    "DriveArchiveObjectMissing",
    "DriveArchivePermissionError",
    "DriveArchiveProvenanceError",
    "DriveArchiveRateLimitError",
    "DriveArchiveUploadError",
    "DriveArchiveVerificationError",
    "DriveArchiveWriterLeaseError",
    "DriveCommandGateway",
    "DriveWriterLease",
    "drive_archive_from_runtime",
    "database_drive_archive_from_runtime",
]


DRIVE_PROPERTY_MAX_BYTES = 124
_DRIVE_REQUIRED_METADATA_KEYS = ("source_receipt", "source_uri", "archive_version")


class DriveArchiveConfigurationError(ValueError):
    """Raised when immutable Drive archive storage is not configured safely."""


class DriveArchiveProvenanceError(ValueError):
    """Raised when source evidence lacks the minimum archive provenance."""


# Keep the private name for the existing internal control flow while exposing
# the typed missing-object signal to integration tests and callers.
_DriveArchiveObjectMissing = DriveArchiveObjectMissing


class SubprocessCommandRunner:
    """Execute rclone without a shell or embedded archive credentials."""

    def __init__(self, *, config_path: Path | None = None) -> None:
        self.config_path = config_path

    def _command(self, args: list[str]) -> list[str]:
        if not args or args[0] != "rclone":
            raise DriveArchiveConfigurationError(
                "Drive archive subprocess runner accepts only rclone commands."
            )
        if self.config_path is None or "--config" in args:
            return list(args)
        return [args[0], "--config", str(self.config_path), *args[1:]]

    def run(self, args: list[str], *, timeout_seconds: float | None = None) -> bytes:
        command = self._command(args)
        if timeout_seconds is None:
            return subprocess.run(command, check=True, capture_output=True).stdout
        return subprocess.run(
            command, check=True, capture_output=True, timeout=timeout_seconds
        ).stdout


@dataclass(frozen=True)
class ArchivedObject:
    storage_uri: str
    object_path: str
    sha256: str
    byte_length: int
    compressed_length: int


class DriveArchive:
    """Write immutable content-addressed objects to a Drive archive path."""

    def __init__(
        self,
        remote: str,
        root: str,
        spool_dir: Path,
        runner: CommandRunner,
        readback_retries: int = 2,
        readback_delay_seconds: float = 1.0,
        sleeper: Callable[[float], None] = time.sleep,
        *,
        command_timeout_seconds: float = 60,
        tps_limit: float = 0.5,
        tps_limit_burst: int = 1,
        rate_limit_retries: int = 2,
        rate_limit_cooldown_seconds: float = 60.0,
        rate_limit_max_cooldown_seconds: float = 900.0,
        rate_limit_sleeper: Callable[[float], None] = time.sleep,
        rate_limit_jitter: Callable[..., float] | None = None,
        verify_after_upload: bool = True,
    ) -> None:
        self.remote = remote.strip()
        self.root = root.strip("/")
        self.spool_dir = Path(spool_dir)
        self.runner = runner
        self.command_timeout_seconds = command_timeout_seconds
        self.readback_retries = readback_retries
        self.readback_delay_seconds = readback_delay_seconds
        self.sleeper = sleeper
        self.verify_after_upload = verify_after_upload
        self._verified_objects: set[str] = set()
        self._target_validated = False
        self._remote_configuration: str | None = None

        if not self.remote:
            raise DriveArchiveConfigurationError("Drive archive remote must not be blank")
        if not self.root:
            raise DriveArchiveConfigurationError("Drive archive root must not be blank")
        if self.command_timeout_seconds <= 0:
            raise DriveArchiveConfigurationError(
                "Drive archive command deadline must be positive."
            )
        if not isinstance(self.verify_after_upload, bool):
            raise DriveArchiveConfigurationError(
                "Drive archive verify_after_upload must be a boolean."
            )
        if not 0 <= self.readback_retries <= 2:
            raise DriveArchiveConfigurationError(
                "Drive archive readback retries must be between zero and at most two."
            )
        try:
            self.gateway = DriveCommandGateway(
                runner,
                tps_limit=tps_limit,
                tps_limit_burst=tps_limit_burst,
                rate_limit_retries=rate_limit_retries,
                rate_limit_cooldown_seconds=rate_limit_cooldown_seconds,
                rate_limit_max_cooldown_seconds=rate_limit_max_cooldown_seconds,
                sleeper=rate_limit_sleeper,
                jitter=rate_limit_jitter,
            )
        except ValueError as exc:
            raise DriveArchiveConfigurationError(str(exc)) from exc
        self.metrics = self.gateway.metrics

    def writer_lease(self) -> DriveWriterLease:
        """Return the process-exclusive lease for this named Drive remote."""
        return DriveWriterLease(self.spool_dir, self.remote)

    def archive_bytes(
        self,
        *,
        data: bytes,
        extension: str,
        metadata: Mapping[str, str],
    ) -> ArchivedObject:
        """Archive one payload, retaining its spool copy until success is accepted."""
        from kreports.runtime import require_drive_archive_mode

        require_drive_archive_mode("archive raw source bytes to Drive")
        normalized_extension = _normalized_extension(extension)
        sha256 = hashlib.sha256(data).hexdigest()
        object_path = _archive_object_path(
            sha256=sha256,
            extension=normalized_extension,
            metadata=metadata,
        )
        storage_uri = self._storage_uri(object_path)
        compressed = _deterministic_gzip(data)
        archive_metadata = _archive_metadata(
            metadata,
            sha256=sha256,
            byte_length=len(data),
            compressed_length=len(compressed),
        )
        transport_metadata = _drive_transport_metadata(archive_metadata)
        self._validate_drive_remote()

        if not self.verify_after_upload:
            if storage_uri not in self._verified_objects:
                self._upload_without_readback(
                    compressed=compressed,
                    storage_uri=storage_uri,
                    extension=normalized_extension,
                    metadata=transport_metadata,
                )
                self._verified_objects.add(storage_uri)
        elif storage_uri not in self._verified_objects:
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
                    metadata=transport_metadata,
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

    def _upload_without_readback(
        self,
        *,
        compressed: bytes,
        storage_uri: str,
        extension: str,
        metadata: Mapping[str, str],
    ) -> None:
        """Upload a source object once, retaining its spool on any failure.

        Source-archive backfills use content-addressed object names under a
        process-exclusive writer lease.  Their normal success path therefore
        accepts rclone's successful ``copyto --ignore-existing`` without the
        two remote ``cat`` calls used by the stricter database-artifact path.
        An interrupted or failed command leaves the local spool in place for
        the resumable campaign rather than treating a remote object as proven.
        """
        spool_path = self._write_spool_file(compressed, suffix=f".{extension}.gz")
        command = [
            "rclone",
            "copyto",
            str(spool_path),
            storage_uri,
            "--ignore-existing",
            "--metadata",
        ]
        for key, value in sorted(metadata.items()):
            command.extend(["--metadata-set", f"{key}={value}"])
        try:
            self.gateway.run(command, timeout_seconds=self.command_timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            raise DriveArchiveCommandTimeoutError(
                "Drive archive copy command timed out after "
                f"{self.command_timeout_seconds:g} seconds."
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise DriveArchiveCommandError(
                "Drive archive copy command failed: "
                f"{redacted_drive_diagnostic(exc)}"
            ) from exc
        spool_path.unlink()

    def archive_file(
        self,
        *,
        path: Path,
        metadata: Mapping[str, str],
    ) -> ArchivedObject:
        """Archive one file through a bounded gzip spool and verify its raw bytes.

        This is deliberately separate from :meth:`archive_bytes`: release and
        candidate SQLite files can be several GiB and must never be loaded into
        the maintainer process at once.
        """
        from kreports.runtime import require_database_archive_mode

        require_database_archive_mode("archive local database artifact to Drive")
        source = Path(path).expanduser().resolve(strict=True)
        if not source.is_file():
            raise DriveArchiveConfigurationError("Drive archive file must be a readable regular file")
        sha256, byte_length = _sha256_file(source)
        extension = _normalized_extension(source.suffix or ".bin")
        object_path = (
            f"objects/sha256/{sha256[:2]}/{sha256[2:4]}/"
            f"{sha256}.{extension}.gz"
        )
        storage_uri = self._storage_uri(object_path)
        archive_metadata = _archive_metadata(
            metadata,
            sha256=sha256,
            byte_length=byte_length,
            compressed_length=0,
        )
        self._validate_drive_remote()

        try:
            self._verify_file_object(
                storage_uri, expected_sha256=sha256, expected_bytes=byte_length
            )
        except _DriveArchiveObjectMissing:
            spool_path, compressed_length = self._write_gzip_file_spool(source, extension)
            archive_metadata["compressed_length"] = str(compressed_length)
            try:
                self._upload_file_spool_and_verify(
                    spool_path=spool_path,
                    storage_uri=storage_uri,
                    metadata=_drive_transport_metadata(archive_metadata),
                    sha256=sha256,
                    byte_length=byte_length,
                )
            finally:
                if spool_path.exists() and storage_uri in self._verified_objects:
                    spool_path.unlink()
        self._verified_objects.add(storage_uri)
        return ArchivedObject(
            storage_uri=storage_uri,
            object_path=object_path,
            sha256=sha256,
            byte_length=byte_length,
            compressed_length=int(archive_metadata["compressed_length"]),
        )

    def verify_object(
        self,
        storage_uri: str,
        expected_sha256: str,
        expected_bytes: int,
    ) -> None:
        """Read and validate a compressed remote object before accepting it."""
        try:
            compressed = self.gateway.run(
                ["rclone", "cat", storage_uri],
                timeout_seconds=self.command_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise DriveArchiveCommandTimeoutError(
                "Drive archive cat command timed out after "
                f"{self.command_timeout_seconds:g} seconds."
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise DriveArchiveCommandError(
                "Drive archive cat command failed: "
                f"{redacted_drive_diagnostic(exc)}"
            ) from exc
        try:
            data = gzip.decompress(compressed)
        except (OSError, EOFError, zlib.error) as exc:
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
            self.gateway.run(command, timeout_seconds=self.command_timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            try:
                self.verify_object(
                    storage_uri,
                    expected_sha256=sha256,
                    expected_bytes=byte_length,
                )
            except DriveArchiveVerificationError:
                raise DriveArchiveUploadError(
                    "Drive archive upload timed out and object could not be verified."
                ) from exc
        except subprocess.CalledProcessError as exc:
            try:
                self.verify_object(
                    storage_uri,
                    expected_sha256=sha256,
                    expected_bytes=byte_length,
                )
            except _DriveArchiveObjectMissing:
                diagnostic = _bounded_redacted_diagnostic(exc)
                raise DriveArchiveUploadError(
                    "Drive archive upload failed and object could not be verified: "
                    f"{diagnostic}"
                ) from exc
        else:
            self._verify_successful_copy_readback(
                storage_uri=storage_uri,
                sha256=sha256,
                byte_length=byte_length,
            )
        spool_path.unlink()

    def _upload_file_spool_and_verify(
        self,
        *,
        spool_path: Path,
        storage_uri: str,
        metadata: Mapping[str, str],
        sha256: str,
        byte_length: int,
    ) -> None:
        command = ["rclone", "copyto", str(spool_path), storage_uri, "--immutable", "--metadata"]
        for key, value in sorted(metadata.items()):
            command.extend(["--metadata-set", f"{key}={value}"])
        try:
            self.gateway.run(command, timeout_seconds=self.command_timeout_seconds)
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
            try:
                self._verify_file_object(
                    storage_uri, expected_sha256=sha256, expected_bytes=byte_length
                )
            except DriveArchiveVerificationError:
                raise DriveArchiveUploadError(
                    "Drive archive file upload failed and object could not be verified."
                ) from exc
        else:
            self._verify_file_object(
                storage_uri, expected_sha256=sha256, expected_bytes=byte_length
            )
        self._verified_objects.add(storage_uri)

    def _verify_file_object(
        self,
        storage_uri: str,
        *,
        expected_sha256: str,
        expected_bytes: int,
    ) -> None:
        """Verify a remote gzip object without retaining its uncompressed body."""
        if not isinstance(self.runner, SubprocessCommandRunner):
            self.verify_object(storage_uri, expected_sha256, expected_bytes)
            return
        state: dict[str, Any] = {
            "digest": hashlib.sha256(),
            "byte_length": 0,
            "decompressor": zlib.decompressobj(16 + zlib.MAX_WBITS),
        }

        def consume(compressed_chunk: bytes) -> None:
            try:
                decompressed = state["decompressor"].decompress(compressed_chunk)
                state["digest"].update(decompressed)
                state["byte_length"] += len(decompressed)
            except (OSError, EOFError, zlib.error) as exc:
                raise DriveArchiveVerificationError(
                    f"Drive archive readback could not be decompressed: {storage_uri}"
                ) from exc

        def reset() -> None:
            state["digest"] = hashlib.sha256()
            state["byte_length"] = 0
            state["decompressor"] = zlib.decompressobj(16 + zlib.MAX_WBITS)

        self.gateway.stream(
            ["rclone", "cat", storage_uri],
            timeout_seconds=self.command_timeout_seconds,
            consume=consume,
            reset=reset,
        )
        try:
            tail = state["decompressor"].flush()
        except zlib.error as exc:
            raise DriveArchiveVerificationError(
                f"Drive archive readback could not be decompressed: {storage_uri}"
            ) from exc
        if tail:
            state["digest"].update(tail)
            state["byte_length"] += len(tail)
        if not state["decompressor"].eof:
            raise DriveArchiveVerificationError(
                f"Drive archive readback could not be decompressed: {storage_uri}"
            )
        byte_length = int(state["byte_length"])
        digest = state["digest"]
        if byte_length != expected_bytes:
            raise DriveArchiveVerificationError(
                f"Drive archive byte length mismatch: expected {expected_bytes}, got {byte_length}"
            )
        actual_sha256 = digest.hexdigest()
        if actual_sha256 != expected_sha256:
            raise DriveArchiveVerificationError(
                "Drive archive SHA-256 mismatch: "
                f"expected {expected_sha256}, got {actual_sha256}"
            )

    def _write_gzip_file_spool(self, source: Path, extension: str) -> tuple[Path, int]:
        self.spool_dir.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            mode="wb", dir=self.spool_dir, prefix="drive-archive-", suffix=f".{extension}.gz", delete=False
        ) as raw_spool:
            spool_path = Path(raw_spool.name)
            with gzip.GzipFile(fileobj=raw_spool, mode="wb", filename="", mtime=0) as compressed:
                with source.open("rb") as input_file:
                    while chunk := input_file.read(1024 * 1024):
                        compressed.write(chunk)
        return spool_path, spool_path.stat().st_size

    def _verify_successful_copy_readback(
        self,
        *,
        storage_uri: str,
        sha256: str,
        byte_length: int,
    ) -> None:
        for attempt in range(self.readback_retries + 1):
            try:
                self.verify_object(
                    storage_uri,
                    expected_sha256=sha256,
                    expected_bytes=byte_length,
                )
                return
            except _DriveArchiveObjectMissing:
                if attempt == self.readback_retries:
                    raise
                self.sleeper(self.readback_delay_seconds)

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

    def _validate_drive_remote(self) -> str:
        if self._target_validated:
            return self._remote_configuration or ""
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
            configuration = self.gateway.run(["rclone", "config", "show", remote_name]).decode()
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
        self._remote_configuration = configuration
        self._target_validated = True
        return configuration


def drive_archive_from_runtime(
    *,
    runner: CommandRunner | None = None,
    require_dedicated_client: bool = True,
) -> DriveArchive:
    """Build the collector-only Drive archive from deploy-time configuration.

    The source-archive CLI passes ``require_dedicated_client=True`` so a
    collector cannot accidentally spend a shared/default Drive OAuth client's
    quota.  Direct ``DriveArchive`` object tests may omit this factory policy;
    a factory caller must explicitly pass ``require_dedicated_client=False``
    for a documented diagnostic-only shared-client run.
    """
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
    command_timeout_seconds = _collector_command_timeout_seconds()
    archive = DriveArchive(
        remote=remote,
        root=root,
        spool_dir=Path(spool_dir),
        runner=runner or SubprocessCommandRunner(
            config_path=_operator_rclone_config_path_from_runtime()
        ),
        command_timeout_seconds=command_timeout_seconds,
        tps_limit=_drive_tps_limit_from_runtime(),
        tps_limit_burst=_drive_tps_burst_from_runtime(),
        rate_limit_retries=_drive_rate_limit_retries_from_runtime(),
        rate_limit_cooldown_seconds=_drive_rate_limit_cooldown_from_runtime(),
        rate_limit_max_cooldown_seconds=_drive_rate_limit_max_cooldown_from_runtime(),
        verify_after_upload=_source_archive_readback_verification_from_runtime(),
    )
    configuration = archive._validate_drive_remote()
    dedicated_client_configured = _dedicated_client_configured(
        remote_name=remote[:-1], configuration=configuration
    )
    if require_dedicated_client and not dedicated_client_configured:
        if not _shared_client_diagnostic_override():
            raise DriveArchiveConfigurationError(
                "source-archive apply requires a dedicated Drive client_id proven by "
                "the named rclone remote or an RCLONE_* client_id override"
            )
    archive.metrics.dedicated_client_configured = dedicated_client_configured
    return archive


def database_drive_archive_from_runtime(*, runner: CommandRunner | None = None) -> DriveArchive:
    """Build the separately opt-in Drive archive used for inactive DB artifacts."""
    from kreports.runtime import drive_archive_policy, require_database_archive_mode

    require_database_archive_mode("build Drive database archive")
    backend, remote, _source_root, spool_dir = drive_archive_policy()
    root = os.environ.get("KREPORTS_DB_ARCHIVE_PREFIX", "").strip().strip("/")
    if backend != "drive":
        raise DriveArchiveConfigurationError(
            "Database archive requires RAW_STORAGE_BACKEND=drive."
        )
    if not remote:
        raise DriveArchiveConfigurationError(
            "Database archive requires RAW_STORAGE_DRIVE_REMOTE."
        )
    if not root:
        raise DriveArchiveConfigurationError(
            "Database archive requires KREPORTS_DB_ARCHIVE_PREFIX."
        )
    if not spool_dir:
        raise DriveArchiveConfigurationError(
            "Database archive requires RAW_STORAGE_SPOOL_DIR."
        )
    archive = DriveArchive(
        remote=remote,
        root=root,
        spool_dir=Path(spool_dir),
        runner=runner or SubprocessCommandRunner(
            config_path=_operator_rclone_config_path_from_runtime()
        ),
        command_timeout_seconds=_collector_command_timeout_seconds(),
        tps_limit=_drive_tps_limit_from_runtime(),
        tps_limit_burst=_drive_tps_burst_from_runtime(),
        rate_limit_retries=_drive_rate_limit_retries_from_runtime(),
        rate_limit_cooldown_seconds=_drive_rate_limit_cooldown_from_runtime(),
        rate_limit_max_cooldown_seconds=_drive_rate_limit_max_cooldown_from_runtime(),
    )
    archive._validate_drive_remote()
    return archive


def _collector_command_timeout_seconds() -> int:
    configured_timeout = os.environ.get("RAW_STORAGE_COMMAND_TIMEOUT_SECONDS")
    if configured_timeout is None:
        return 60
    if not re.fullmatch(r"[0-9]+", configured_timeout):
        raise DriveArchiveConfigurationError(
            "RAW_STORAGE_COMMAND_TIMEOUT_SECONDS must be an integer from 1 through 300."
        )
    command_timeout_seconds = int(configured_timeout)
    if not 1 <= command_timeout_seconds <= 300:
        raise DriveArchiveConfigurationError(
            "RAW_STORAGE_COMMAND_TIMEOUT_SECONDS must be an integer from 1 through 300."
        )
    return command_timeout_seconds


def _source_archive_readback_verification_from_runtime() -> bool:
    """Resolve the explicitly opt-in strict readback for raw source collection.

    The collector defaults to the historical strict behavior.  Production
    high-volume source backfill can set ``RAW_STORAGE_VERIFY_READBACK_ON_SUCCESS=0``
    to rely on the immutable SHA-256 object path and the writer lease, while
    retaining the spool whenever rclone does not report success.  Database
    artifact archives do not use this setting and remain strict.
    """
    configured = os.environ.get("RAW_STORAGE_VERIFY_READBACK_ON_SUCCESS")
    if configured is None:
        return True
    normalized = configured.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise DriveArchiveConfigurationError(
        "RAW_STORAGE_VERIFY_READBACK_ON_SUCCESS must be 0 or 1."
    )


def _operator_rclone_config_path_from_runtime() -> Path | None:
    """Resolve an explicitly selected, owner-only rclone credential file.

    This follows the existing Drive operations pattern used by the other local
    archive pipeline: the application receives only a path, never copies or
    prints the OAuth configuration, and every rclone subprocess is pinned to
    that same file.  An unset value preserves rclone's normal default lookup.
    """
    configured = os.environ.get("RAW_STORAGE_RCLONE_CONFIG", "").strip()
    if not configured:
        return None
    try:
        path = Path(configured).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise DriveArchiveConfigurationError(
            "RAW_STORAGE_RCLONE_CONFIG must name an existing regular file."
        ) from exc
    if not path.is_file():
        raise DriveArchiveConfigurationError(
            "RAW_STORAGE_RCLONE_CONFIG must name an existing regular file."
        )
    details = path.stat()
    if hasattr(os, "getuid") and details.st_uid != os.getuid():
        raise DriveArchiveConfigurationError(
            "RAW_STORAGE_RCLONE_CONFIG must be owned by the collector user."
        )
    if stat.S_IMODE(details.st_mode) & 0o077:
        raise DriveArchiveConfigurationError(
            "RAW_STORAGE_RCLONE_CONFIG must use owner-only permissions (chmod 600)."
        )
    return path


def _first_environment_value(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value is not None and value.strip():
            return value.strip()
    return None


def _dedicated_client_configured(*, remote_name: str, configuration: str) -> bool:
    """Prove that the rclone Drive backend has a separate OAuth client.

    ``RAW_STORAGE_DRIVE_CLIENT_ID`` and other application-private markers are
    deliberately ignored: they are not consumed by rclone and cannot prove
    which client the named remote will use.  The config output or one of the
    backend's documented environment overrides is the only accepted proof.
    Values are checked but never returned, logged, or placed in metrics.
    """
    configured = _rclone_config_value(configuration, "client_id")
    if _usable_client_id(configured):
        return True
    normalized_remote = re.sub(r"[^A-Za-z0-9]", "_", remote_name).upper()
    override = _first_environment_value(
        f"RCLONE_CONFIG_{normalized_remote}_CLIENT_ID", "RCLONE_DRIVE_CLIENT_ID"
    )
    return _usable_client_id(override)


def _rclone_config_value(configuration: str, key: str) -> str | None:
    for line in configuration.splitlines():
        name, separator, value = line.partition("=")
        if separator and name.strip().lower() == key.lower():
            return value.strip()
    return None


def _usable_client_id(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() not in {"", "none", "null", "***", "redacted", "[redacted]"}


def _shared_client_diagnostic_override() -> bool:
    value = os.environ.get("KREPORTS_DRIVE_ALLOW_SHARED_CLIENT_DIAGNOSTIC", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _bounded_runtime_number(
    names: tuple[str, ...],
    *,
    default: float,
    minimum: float,
    maximum: float,
    label: str,
) -> float:
    value = _first_environment_value(*names)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError as exc:
        raise DriveArchiveConfigurationError(
            f"{label} must be between {minimum:g} and {maximum:g}"
        ) from exc
    if not minimum <= parsed <= maximum:
        raise DriveArchiveConfigurationError(
            f"{label} must be between {minimum:g} and {maximum:g}"
        )
    return parsed


def _bounded_runtime_integer(
    names: tuple[str, ...], *, default: int, minimum: int, maximum: int, label: str
) -> int:
    value = _first_environment_value(*names)
    if value is None:
        return default
    if not re.fullmatch(r"[0-9]+", value):
        raise DriveArchiveConfigurationError(
            f"{label} must be between {minimum} and {maximum}"
        )
    parsed = int(value)
    if not minimum <= parsed <= maximum:
        raise DriveArchiveConfigurationError(
            f"{label} must be between {minimum} and {maximum}"
        )
    return parsed


def _drive_tps_limit_from_runtime() -> float:
    return _bounded_runtime_number(
        ("RAW_STORAGE_RCLONE_TPSLIMIT", "KREPORTS_DRIVE_TPS_LIMIT"),
        default=0.5,
        minimum=0.1,
        maximum=2.0,
        label="RAW_STORAGE_RCLONE_TPSLIMIT",
    )


def _drive_tps_burst_from_runtime() -> int:
    return _bounded_runtime_integer(
        ("RAW_STORAGE_RCLONE_TPSLIMIT_BURST", "KREPORTS_DRIVE_TPS_BURST"),
        default=1,
        minimum=1,
        maximum=4,
        label="RAW_STORAGE_RCLONE_TPSLIMIT_BURST",
    )


def _drive_rate_limit_retries_from_runtime() -> int:
    return _bounded_runtime_integer(
        ("RAW_STORAGE_DRIVE_RATE_LIMIT_RETRIES", "KREPORTS_DRIVE_RATE_LIMIT_RETRIES"),
        default=2,
        minimum=0,
        maximum=5,
        label="RAW_STORAGE_DRIVE_RATE_LIMIT_RETRIES",
    )


def _drive_rate_limit_cooldown_from_runtime() -> float:
    return _bounded_runtime_number(
        ("RAW_STORAGE_DRIVE_RATE_LIMIT_COOLDOWN_SECONDS", "KREPORTS_DRIVE_RATE_LIMIT_COOLDOWN_SECONDS"),
        default=60.0,
        minimum=60.0,
        maximum=3600.0,
        label="RAW_STORAGE_DRIVE_RATE_LIMIT_COOLDOWN_SECONDS",
    )


def _drive_rate_limit_max_cooldown_from_runtime() -> float:
    configured = _bounded_runtime_number(
        ("RAW_STORAGE_DRIVE_RATE_LIMIT_MAX_COOLDOWN_SECONDS", "KREPORTS_DRIVE_RATE_LIMIT_MAX_COOLDOWN_SECONDS"),
        default=900.0,
        minimum=60.0,
        maximum=3600.0,
        label="RAW_STORAGE_DRIVE_RATE_LIMIT_MAX_COOLDOWN_SECONDS",
    )
    cooldown = _drive_rate_limit_cooldown_from_runtime()
    if configured < cooldown:
        raise DriveArchiveConfigurationError(
            "RAW_STORAGE_DRIVE_RATE_LIMIT_MAX_COOLDOWN_SECONDS must be at least the cooldown"
        )
    return configured


def _normalized_extension(extension: str) -> str:
    normalized = extension.strip().lstrip(".")
    if not normalized or not normalized.replace("_", "").replace("-", "").isalnum():
        raise ValueError("archive extension must contain only letters, digits, underscores, or hyphens")
    return normalized


def _archive_object_path(
    *, sha256: str, extension: str, metadata: Mapping[str, str]
) -> str:
    """Prefer a human-browsable annual layout for source-identified assets.

    Raw assets, their parser packages, and their document manifests all carry
    the annual filing identity.  Store those under the fiscal year so a Drive
    collaborator can navigate a report without decoding hash fan-out folders.
    The filename remains the SHA-256, preserving immutable source identity.
    Generic/database archive calls without this full identity retain the
    original content-addressed fan-out layout.
    """
    year = str(metadata.get("bsns_year") or "").strip()
    corp_code = str(metadata.get("corp_code") or "").strip()
    receipt = str(metadata.get("source_receipt") or "").strip()
    report_kind = str(metadata.get("report_kind") or "").strip()
    if (
        re.fullmatch(r"[0-9]{4}", year)
        and re.fullmatch(r"[0-9]{8}", corp_code)
        and re.fullmatch(r"[0-9]{14}", receipt)
        and report_kind in {"business_report", "audit_report"}
    ):
        return (
            f"{year}/{corp_code}/{receipt}/{report_kind}/"
            f"{_source_object_role(metadata)}/{sha256}.{extension}.gz"
        )
    return f"objects/sha256/{sha256[:2]}/{sha256[2:4]}/{sha256}.{extension}.gz"


def _source_object_role(metadata: Mapping[str, str]) -> str:
    archive_version = str(metadata.get("archive_version") or "")
    if archive_version.startswith("raw-source-"):
        return "raw"
    if archive_version.startswith("raw-document-"):
        return "container"
    if archive_version.startswith("source-archive-document-manifest-"):
        return "manifest"
    if archive_version.startswith("document-structure-"):
        return "parsed"
    return "derived"


def _deterministic_gzip(data: bytes) -> bytes:
    output = BytesIO()
    with gzip.GzipFile(fileobj=output, mode="wb", filename="", mtime=0) as compressed:
        compressed.write(data)
    return output.getvalue()


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    byte_length = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            byte_length += len(chunk)
    return digest.hexdigest(), byte_length


def _archive_metadata(
    metadata: Mapping[str, str],
    *,
    sha256: str,
    byte_length: int,
    compressed_length: int,
) -> dict[str, str]:
    missing = [key for key in _DRIVE_REQUIRED_METADATA_KEYS if not metadata.get(key, "").strip()]
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


def _drive_transport_metadata(metadata: Mapping[str, str]) -> dict[str, str]:
    """Return Drive-safe custom properties without changing manifest provenance."""
    transport: dict[str, str] = {}
    for key, value in metadata.items():
        encoded_length = len(key.encode("utf-8")) + len(value.encode("utf-8"))
        if encoded_length <= DRIVE_PROPERTY_MAX_BYTES:
            transport[key] = value
        elif key in _DRIVE_REQUIRED_METADATA_KEYS:
            raise DriveArchiveProvenanceError(
                f"Drive archive provenance metadata exceeds {DRIVE_PROPERTY_MAX_BYTES} bytes: {key}"
            )
    return transport


def _bounded_redacted_diagnostic(error: subprocess.CalledProcessError) -> str:
    return redacted_drive_diagnostic(error)
