"""Quota-aware command and writer-lease boundaries for a Drive archive.

The archive uses ``rclone`` as a process boundary.  A single gateway owns the
command options, error classification, bounded retry policy, and non-secret
metrics so a new call site cannot accidentally bypass the Drive quota guard.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import fcntl
import hashlib
import os
from pathlib import Path
import re
import random
import subprocess
import time
from typing import Callable, Protocol


DEFAULT_DRIVE_TPS_LIMIT = 0.5
DEFAULT_DRIVE_TPS_LIMIT_BURST = 1
DEFAULT_DRIVE_RATE_LIMIT_RETRIES = 2
DEFAULT_DRIVE_RATE_LIMIT_COOLDOWN_SECONDS = 60.0
DEFAULT_DRIVE_RATE_LIMIT_MAX_COOLDOWN_SECONDS = 900.0
MIN_DRIVE_TPS_LIMIT = 0.1
MAX_DRIVE_TPS_LIMIT = 2.0
MIN_DRIVE_TPS_LIMIT_BURST = 1
MAX_DRIVE_TPS_LIMIT_BURST = 4
MAX_DRIVE_RATE_LIMIT_RETRIES = 5
MAX_DRIVE_RATE_LIMIT_COOLDOWN_SECONDS = 3600.0

_RATE_LIMIT_MARKERS = (
    "ratelimitexceeded",
    "userratelimitexceeded",
    "rate_limit_exceeded",
    "rate limit exceeded",
    "too many requests",
    "quota exceeded",
    "quotaexceeded",
)
_STATUS_429 = re.compile(r"(?<!\d)429(?!\d)")
_STATUS_403 = re.compile(r"(?<!\d)403(?!\d)")
_STATUS_404 = re.compile(r"(?<!\d)404(?!\d)")
_RETRY_AFTER = re.compile(r"(?im)^\s*retry-after\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*$")
_REDACT_BEARER = re.compile(
    r"(?im)(\bauthorization\s*:\s*bearer\s+)([^\s,;]+)"
)
_REDACT_ASSIGNMENT = re.compile(
    r"(?im)(\b(?:access_token|client_secret|token|authorization|password|secret)\b\s*[:=]\s*)(?!Bearer\b)([^\s,;]+)"
)


class DriveArchiveError(RuntimeError):
    """Base class for typed Drive archive failures."""


class DriveArchiveVerificationError(DriveArchiveError):
    """Raised when a remote archive object differs from its expected source."""


class DriveArchiveCommandTimeoutError(DriveArchiveVerificationError):
    """Raised when a remote archive content operation exceeds its deadline."""


class DriveArchiveCommandError(DriveArchiveVerificationError):
    """Raised when a non-quota Drive content command fails transiently."""


class DriveArchiveUploadError(DriveArchiveVerificationError):
    """Raised when a failed upload cannot be verified by remote readback."""


class DriveArchiveObjectMissing(DriveArchiveVerificationError):
    """Internal/public signal for a confirmed HTTP-404 or missing object."""


class DriveArchivePermissionError(DriveArchiveVerificationError):
    """Raised when Drive rejects an operation for permission/auth reasons."""


class DriveArchiveRateLimitError(DriveArchiveVerificationError):
    """Raised after bounded retries are exhausted by Drive quota throttling."""

    def __init__(
        self,
        message: str,
        *,
        operation: str,
        attempts: int,
        cooldown_seconds: float,
        diagnostic: str,
    ) -> None:
        super().__init__(message)
        self.operation = operation
        self.attempts = attempts
        self.cooldown_seconds = cooldown_seconds
        self.diagnostic = diagnostic


@dataclass
class DriveArchiveMetrics:
    """Non-secret counters collected for one archive worker invocation."""

    command_attempts: int = 0
    rate_limit_events: int = 0
    retry_attempts: int = 0
    throttle_wait_seconds: float = 0.0
    cooldown_wait_seconds: float = 0.0
    commands_by_operation: dict[str, int] = field(default_factory=dict)
    dedicated_client_configured: bool = False
    pending_event_bundles: int = 0

    def record_command(self, operation: str) -> None:
        self.command_attempts += 1
        self.commands_by_operation[operation] = self.commands_by_operation.get(operation, 0) + 1

    def to_dict(self) -> dict[str, object]:
        return {
            "command_attempts": self.command_attempts,
            "rate_limit_events": self.rate_limit_events,
            "retry_attempts": self.retry_attempts,
            "throttle_wait_seconds": round(self.throttle_wait_seconds, 3),
            "cooldown_wait_seconds": round(self.cooldown_wait_seconds, 3),
            "commands_by_operation": dict(sorted(self.commands_by_operation.items())),
            "dedicated_client_configured": self.dedicated_client_configured,
            "pending_event_bundles": self.pending_event_bundles,
        }


class CommandRunner(Protocol):
    def run(self, args: list[str], *, timeout_seconds: float | None = None) -> bytes:
        """Run a command and return stdout bytes."""


def redacted_drive_diagnostic(error: BaseException) -> str:
    """Return a bounded diagnostic with credentials removed."""
    raw: object = getattr(error, "stderr", None) or getattr(error, "stdout", None) or str(error)
    if isinstance(raw, bytes):
        diagnostic = raw.decode("utf-8", errors="replace")
    else:
        diagnostic = str(raw)
    normalized = diagnostic.replace("\r\n", "\n").replace("\r", "\n")
    bearer_redacted = _REDACT_BEARER.sub(r"\1[REDACTED]", normalized)
    return _REDACT_ASSIGNMENT.sub(r"\1[REDACTED]", bearer_redacted)[:500]


def classify_drive_failure(error: BaseException) -> str:
    """Classify a transport failure without turning permission errors into 404s."""
    if isinstance(error, FileNotFoundError):
        return "missing"
    text = redacted_drive_diagnostic(error).lower()
    # Check quota before status 403: Google Drive reports rate limits as 403.
    if _STATUS_429.search(text) or any(marker in text for marker in _RATE_LIMIT_MARKERS):
        return "rate_limit"
    if _STATUS_403.search(text):
        return "permission"
    if _STATUS_404.search(text):
        return "missing"
    # rclone's Drive backend generally includes this phrase with a 404 status,
    # but only a non-permission, explicit object-not-found response is eligible.
    if (
        "file not found" in text
        or "object not found" in text
        or "directory not found" in text
    ):
        return "missing"
    return "other"


def _operation(args: list[str], *, streaming: bool = False) -> str:
    command = args[1] if len(args) > 1 else "unknown"
    if command == "config" and len(args) > 2:
        command = f"config_{args[2]}"
    if streaming:
        command = f"{command}_stream"
    return command


def _retry_after_seconds(error: BaseException) -> float | None:
    match = _RETRY_AFTER.search(redacted_drive_diagnostic(error))
    return float(match.group(1)) if match else None


class DriveCommandGateway:
    """Single retry/throttle boundary for every rclone operation."""

    def __init__(
        self,
        runner: CommandRunner,
        *,
        tps_limit: float = DEFAULT_DRIVE_TPS_LIMIT,
        tps_limit_burst: int = DEFAULT_DRIVE_TPS_LIMIT_BURST,
        rate_limit_retries: int = DEFAULT_DRIVE_RATE_LIMIT_RETRIES,
        rate_limit_cooldown_seconds: float = DEFAULT_DRIVE_RATE_LIMIT_COOLDOWN_SECONDS,
        rate_limit_max_cooldown_seconds: float = DEFAULT_DRIVE_RATE_LIMIT_MAX_COOLDOWN_SECONDS,
        sleeper: Callable[[float], None] = time.sleep,
        jitter: Callable[..., float] | None = None,
        metrics: DriveArchiveMetrics | None = None,
    ) -> None:
        self.runner = runner
        self.tps_limit = _bounded_float(
            tps_limit,
            minimum=MIN_DRIVE_TPS_LIMIT,
            maximum=MAX_DRIVE_TPS_LIMIT,
            name="Drive TPS limit",
        )
        self.tps_limit_burst = _bounded_int(
            tps_limit_burst,
            minimum=MIN_DRIVE_TPS_LIMIT_BURST,
            maximum=MAX_DRIVE_TPS_LIMIT_BURST,
            name="Drive TPS burst",
        )
        self.rate_limit_retries = _bounded_int(
            rate_limit_retries,
            minimum=0,
            maximum=MAX_DRIVE_RATE_LIMIT_RETRIES,
            name="Drive rate-limit retries",
        )
        self.rate_limit_cooldown_seconds = _bounded_float(
            rate_limit_cooldown_seconds,
            minimum=DEFAULT_DRIVE_RATE_LIMIT_COOLDOWN_SECONDS,
            maximum=MAX_DRIVE_RATE_LIMIT_COOLDOWN_SECONDS,
            name="Drive rate-limit cooldown",
        )
        self.rate_limit_max_cooldown_seconds = _bounded_float(
            rate_limit_max_cooldown_seconds,
            minimum=self.rate_limit_cooldown_seconds,
            maximum=MAX_DRIVE_RATE_LIMIT_COOLDOWN_SECONDS,
            name="Drive maximum rate-limit cooldown",
        )
        self.sleeper = sleeper
        self.jitter = jitter or _default_jitter
        self.metrics = metrics or DriveArchiveMetrics()

    def command_args(self, args: list[str]) -> list[str]:
        """Append bounded per-process rclone quota flags exactly once."""
        decorated = list(args)
        if "--tpslimit" not in decorated:
            decorated.extend(["--tpslimit", _format_number(self.tps_limit)])
        if "--tpslimit-burst" not in decorated:
            decorated.extend(["--tpslimit-burst", str(self.tps_limit_burst)])
        return decorated

    def run(self, args: list[str], *, timeout_seconds: float | None = None) -> bytes:
        decorated = self.command_args(args)
        operation = _operation(decorated)
        last_rate_error: BaseException | None = None
        last_delay = self.rate_limit_cooldown_seconds
        for attempt in range(self.rate_limit_retries + 1):
            self.metrics.record_command(operation)
            try:
                return self.runner.run(decorated, timeout_seconds=timeout_seconds)
            except (subprocess.TimeoutExpired, KeyboardInterrupt):
                raise
            except BaseException as exc:
                classification = classify_drive_failure(exc)
                if classification == "missing" and decorated[1:2] == ["cat"]:
                    raise DriveArchiveObjectMissing(
                        "Drive archive object is missing: "
                        f"{decorated[2] if len(decorated) > 2 else '[redacted]'}"
                    ) from exc
                if classification == "permission":
                    raise DriveArchivePermissionError(
                        "Drive archive operation was rejected by Drive permissions: "
                        f"{redacted_drive_diagnostic(exc)}"
                    ) from exc
                if classification != "rate_limit":
                    raise
                self.metrics.rate_limit_events += 1
                last_rate_error = exc
                last_delay = self._cooldown(attempt, exc)
                if attempt >= self.rate_limit_retries:
                    break
                self.metrics.retry_attempts += 1
                self.metrics.cooldown_wait_seconds += last_delay
                self.sleeper(last_delay)
        assert last_rate_error is not None
        diagnostic = redacted_drive_diagnostic(last_rate_error)
        raise DriveArchiveRateLimitError(
            "Drive archive rate limit exhausted after "
            f"{self.metrics.rate_limit_events} rate-limit event(s) for {operation}; "
            f"cooldown={last_delay:g}s; diagnostic={diagnostic}",
            operation=operation,
            attempts=self.rate_limit_retries + 1,
            cooldown_seconds=last_delay,
            diagnostic=diagnostic,
        ) from last_rate_error

    def stream(
        self,
        args: list[str],
        *,
        timeout_seconds: float | None,
        consume: Callable[[bytes], None],
        reset: Callable[[], None] | None = None,
    ) -> None:
        """Stream stdout through the same quota/error gate without buffering it."""
        decorated = self.command_args(args)
        command_builder = getattr(self.runner, "_command", None)
        if callable(command_builder):
            decorated = command_builder(decorated)
        operation = _operation(decorated, streaming=True)
        last_rate_error: BaseException | None = None
        last_delay = self.rate_limit_cooldown_seconds
        for attempt in range(self.rate_limit_retries + 1):
            self.metrics.record_command(operation)
            process: subprocess.Popen[bytes] | None = None
            try:
                process = subprocess.Popen(
                    decorated,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                assert process.stdout is not None
                while chunk := process.stdout.read(1024 * 1024):
                    consume(chunk)
                stderr = process.stderr.read() if process.stderr is not None else b""
                process.stdout.close()
                process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                if process is not None:
                    process.kill()
                    process.wait()
                raise DriveArchiveCommandTimeoutError(
                    "Drive archive cat command timed out after "
                    f"{timeout_seconds:g} seconds."
                ) from exc
            except OSError as exc:
                raise DriveArchiveVerificationError(
                    f"Drive archive streaming command failed: {redacted_drive_diagnostic(exc)}"
                ) from exc
            if process.returncode == 0:
                return
            failure = subprocess.CalledProcessError(
                process.returncode,
                decorated,
                stderr=stderr,
            )
            classification = classify_drive_failure(failure)
            if classification == "missing":
                raise DriveArchiveObjectMissing(
                    "Drive archive object is missing: "
                    f"{decorated[2] if len(decorated) > 2 else '[redacted]'}"
                ) from failure
            if classification == "permission":
                raise DriveArchivePermissionError(
                    "Drive archive operation was rejected by Drive permissions: "
                    f"{redacted_drive_diagnostic(failure)}"
                ) from failure
            if classification != "rate_limit":
                raise DriveArchiveCommandError(
                    "Drive archive streaming command failed: "
                    f"{redacted_drive_diagnostic(failure)}"
                ) from failure
            self.metrics.rate_limit_events += 1
            last_rate_error = failure
            last_delay = self._cooldown(attempt, failure)
            if attempt >= self.rate_limit_retries:
                break
            if reset is not None:
                reset()
            self.metrics.retry_attempts += 1
            self.metrics.cooldown_wait_seconds += last_delay
            self.sleeper(last_delay)
        assert last_rate_error is not None
        diagnostic = redacted_drive_diagnostic(last_rate_error)
        raise DriveArchiveRateLimitError(
            "Drive archive rate limit exhausted after "
            f"{self.metrics.rate_limit_events} rate-limit event(s) for {operation}; "
            f"cooldown={last_delay:g}s; diagnostic={diagnostic}",
            operation=operation,
            attempts=self.rate_limit_retries + 1,
            cooldown_seconds=last_delay,
            diagnostic=diagnostic,
        ) from last_rate_error

    def _cooldown(self, attempt: int, error: BaseException) -> float:
        retry_after = _retry_after_seconds(error)
        exponential = self.rate_limit_cooldown_seconds * (2**attempt)
        delay = max(self.rate_limit_cooldown_seconds, exponential, retry_after or 0.0)
        delay = min(delay, self.rate_limit_max_cooldown_seconds)
        try:
            extra = float(self.jitter(delay))
        except TypeError:
            extra = float(self.jitter())
        return min(self.rate_limit_max_cooldown_seconds, max(self.rate_limit_cooldown_seconds, delay + max(0.0, extra)))


def _default_jitter(delay: float) -> float:
    """Add bounded jitter while preserving the minimum quota cooldown."""
    # A small additive range avoids synchronized workers without allowing
    # random jitter to violate the configured cooldown or cap.
    return random.uniform(0.0, min(30.0, max(0.0, delay * 0.1)))


class DriveWriterLease:
    """Process-exclusive lease for all writes to one named Drive remote."""

    def __init__(self, spool_dir: Path, remote: str, *, control_dir: Path | None = None) -> None:
        self.spool_dir = Path(spool_dir).expanduser()
        self.remote = remote.strip()
        configured = os.environ.get("RAW_STORAGE_CONTROL_DIR", "").strip()
        self.control_dir = Path(configured).expanduser() if configured else self.spool_dir / "control"
        digest = hashlib.sha256(self.remote.encode("utf-8")).hexdigest()[:24]
        self.path = self.control_dir / f"drive-writer-{digest}.lock"
        self._handle = None

    def __enter__(self) -> "DriveWriterLease":
        self.control_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._handle = self.path.open("a+")
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            if self._handle is not None:
                self._handle.close()
                self._handle = None
            if isinstance(exc, BlockingIOError) or getattr(exc, "errno", None) in {11, 35}:
                raise DriveArchiveWriterLeaseError(
                    "another source collector already owns the Drive writer lease"
                ) from exc
            raise DriveArchiveWriterLeaseError(
                "Drive writer lease could not be acquired"
            ) from exc
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        if self._handle is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None


class DriveArchiveWriterLeaseError(DriveArchiveError):
    """Raised when another process owns the remote's writer lease."""


def _bounded_float(value: float, *, minimum: float, maximum: float, name: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be between {minimum:g} and {maximum:g}") from exc
    if not minimum <= numeric <= maximum:
        raise ValueError(f"{name} must be between {minimum:g} and {maximum:g}")
    return numeric


def _bounded_int(value: int, *, minimum: int, maximum: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _format_number(value: float) -> str:
    return f"{value:g}"


__all__ = [
    "CommandRunner",
    "DEFAULT_DRIVE_RATE_LIMIT_COOLDOWN_SECONDS",
    "DEFAULT_DRIVE_RATE_LIMIT_MAX_COOLDOWN_SECONDS",
    "DEFAULT_DRIVE_RATE_LIMIT_RETRIES",
    "DEFAULT_DRIVE_TPS_LIMIT",
    "DEFAULT_DRIVE_TPS_LIMIT_BURST",
    "DriveArchiveError",
    "DriveArchiveMetrics",
    "DriveArchiveObjectMissing",
    "DriveArchivePermissionError",
    "DriveArchiveRateLimitError",
    "DriveArchiveVerificationError",
    "DriveArchiveCommandError",
    "DriveArchiveCommandTimeoutError",
    "DriveArchiveUploadError",
    "DriveArchiveWriterLeaseError",
    "DriveCommandGateway",
    "DriveWriterLease",
    "classify_drive_failure",
    "redacted_drive_diagnostic",
]
