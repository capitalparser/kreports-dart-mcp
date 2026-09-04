from __future__ import annotations

import subprocess

import pytest

from kreports.storage.drive_archive import (
    DriveArchive,
    DriveArchiveConfigurationError,
    DriveArchiveObjectMissing,
    DriveArchivePermissionError,
    DriveArchiveRateLimitError,
    drive_archive_from_runtime,
)
from kreports.storage.drive_rate_limit import DriveCommandGateway


class _Runner:
    def __init__(self, errors: list[BaseException] | None = None) -> None:
        self.errors = list(errors or [])
        self.calls: list[list[str]] = []

    def run(self, args: list[str], *, timeout_seconds: float | None = None) -> bytes:
        del timeout_seconds
        self.calls.append(args)
        if self.errors:
            error = self.errors.pop(0)
            raise error
        return b""


class _ConfigRunner(_Runner):
    def __init__(self, configuration: str) -> None:
        super().__init__()
        self.configuration = configuration

    def run(self, args: list[str], *, timeout_seconds: float | None = None) -> bytes:
        self.calls.append(args)
        if args[1:3] == ["config", "show"]:
            return self.configuration.encode()
        return super().run(args, timeout_seconds=timeout_seconds)


def _called_process_error(message: str) -> subprocess.CalledProcessError:
    return subprocess.CalledProcessError(
        1,
        ["rclone", "cat", "team-drive:objects/test"],
        stderr=message.encode(),
    )


def test_drive_403_permission_is_not_treated_as_missing(tmp_path):
    runner = _Runner([_called_process_error("HTTP 403: insufficient permissions")])
    archive = DriveArchive("team-drive:", "kreports/raw", tmp_path, runner)

    with pytest.raises(DriveArchivePermissionError):
        archive.verify_object("team-drive:objects/test", "a" * 64, 1)


def test_drive_403_rate_limit_retries_after_at_least_one_minute_and_exposes_metrics(tmp_path):
    sleeps: list[float] = []
    runner = _Runner([
        _called_process_error("HTTP 403: rateLimitExceeded"),
        _called_process_error("HTTP 403: userRateLimitExceeded"),
        _called_process_error("HTTP 429: Too Many Requests"),
    ])
    archive = DriveArchive(
        "team-drive:",
        "kreports/raw",
        tmp_path,
        runner,
        rate_limit_retries=2,
        rate_limit_sleeper=sleeps.append,
        rate_limit_jitter=lambda _delay: 0.0,
    )

    with pytest.raises(DriveArchiveRateLimitError) as raised:
        archive.verify_object("team-drive:objects/test", "a" * 64, 1)

    assert sleeps and sleeps[0] >= 60
    assert raised.value.cooldown_seconds >= 60
    assert archive.metrics.rate_limit_events == 3
    assert archive.metrics.retry_attempts == 2
    assert archive.metrics.command_attempts == 3
    assert "Too Many Requests" in str(raised.value)


def test_drive_404_is_the_only_called_process_error_classified_as_missing(tmp_path):
    runner = _Runner([_called_process_error("HTTP 404: File not found")])
    archive = DriveArchive("team-drive:", "kreports/raw", tmp_path, runner)

    with pytest.raises(DriveArchiveObjectMissing):
        archive.verify_object("team-drive:objects/test", "a" * 64, 1)


def test_drive_cat_treats_rclone_missing_parent_directory_as_missing_object(tmp_path):
    runner = _Runner([_called_process_error("Failed to cat: directory not found")])
    archive = DriveArchive("team-drive:", "kreports/raw", tmp_path, runner)

    with pytest.raises(DriveArchiveObjectMissing):
        archive.verify_object("team-drive:objects/new-prefix/test", "a" * 64, 1)


def test_drive_commands_carry_conservative_tps_flags(tmp_path):
    runner = _Runner([FileNotFoundError("team-drive:objects/test")])
    archive = DriveArchive("team-drive:", "kreports/raw", tmp_path, runner)

    with pytest.raises(DriveArchiveObjectMissing):
        archive.verify_object("team-drive:objects/test", "a" * 64, 1)

    assert "--tpslimit" in runner.calls[0]
    assert runner.calls[0][runner.calls[0].index("--tpslimit") + 1] == "0.5"
    assert "--tpslimit-burst" in runner.calls[0]
    assert runner.calls[0][runner.calls[0].index("--tpslimit-burst") + 1] == "1"


def test_stream_uses_the_operator_runner_command_builder(monkeypatch):
    import kreports.storage.drive_rate_limit as rate_limit_module

    class Runner(_Runner):
        def _command(self, args):
            return ["rclone", "--config", "/private/operator.conf", *args[1:]]

    class Pipe:
        def read(self, _size=-1):
            return b""

        def close(self):
            pass

    class Process:
        returncode = 0

        def __init__(self):
            self.stdout = Pipe()
            self.stderr = Pipe()

        def wait(self, timeout=None):
            del timeout

    seen = []
    monkeypatch.setattr(
        rate_limit_module.subprocess,
        "Popen",
        lambda args, **_kwargs: (seen.append(args), Process())[1],
    )

    gateway = DriveCommandGateway(Runner())
    gateway.stream(
        ["rclone", "cat", "team-drive:objects/test"],
        timeout_seconds=None,
        consume=lambda _: None,
    )

    assert seen[0][:3] == ["rclone", "--config", "/private/operator.conf"]


def test_gateway_default_jitter_is_bounded_and_injectable(monkeypatch):
    import kreports.storage.drive_rate_limit as rate_limit_module

    monkeypatch.setattr(rate_limit_module.random, "uniform", lambda low, high: high)
    gateway = DriveCommandGateway(_Runner())

    assert gateway.jitter(60) == 6
    assert 0 <= gateway.jitter(900) <= 30


def test_source_archive_factory_requires_dedicated_client_id_when_requested(tmp_path, monkeypatch):
    # Import settings before setting test-only environment values so this test
    # does not leave an environment-derived default in the process singleton.
    import kreports.config  # noqa: F401

    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    monkeypatch.setenv("KREPORTS_ENABLE_RAW_BACKFILL", "1")
    monkeypatch.setenv("RAW_STORAGE_BACKEND", "drive")
    monkeypatch.setenv("RAW_STORAGE_DRIVE_REMOTE", "team-drive:")
    monkeypatch.setenv("RAW_STORAGE_PREFIX", "kreports/raw")
    monkeypatch.setenv("RAW_STORAGE_SPOOL_DIR", str(tmp_path))
    monkeypatch.setenv("RAW_STORAGE_DRIVE_CLIENT_ID", "marker-that-must-not-be-trusted")
    monkeypatch.delenv("RCLONE_DRIVE_CLIENT_ID", raising=False)
    monkeypatch.delenv("RCLONE_CONFIG_TEAM_DRIVE_CLIENT_ID", raising=False)
    monkeypatch.delenv("KREPORTS_DRIVE_ALLOW_SHARED_CLIENT_DIAGNOSTIC", raising=False)

    with pytest.raises(DriveArchiveConfigurationError, match="dedicated Drive client_id"):
        drive_archive_from_runtime(
            runner=_ConfigRunner("[team-drive]\ntype = drive\n"),
            require_dedicated_client=True,
        )


def test_source_archive_factory_proves_dedicated_client_from_rclone_config_without_exposing_id(
    tmp_path, monkeypatch
):
    import kreports.config  # noqa: F401

    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    monkeypatch.setenv("KREPORTS_ENABLE_RAW_BACKFILL", "1")
    monkeypatch.setenv("RAW_STORAGE_BACKEND", "drive")
    monkeypatch.setenv("RAW_STORAGE_DRIVE_REMOTE", "team-drive:")
    monkeypatch.setenv("RAW_STORAGE_PREFIX", "kreports/raw")
    monkeypatch.setenv("RAW_STORAGE_SPOOL_DIR", str(tmp_path))
    monkeypatch.delenv("RAW_STORAGE_DRIVE_CLIENT_ID", raising=False)
    monkeypatch.delenv("RCLONE_DRIVE_CLIENT_ID", raising=False)
    monkeypatch.delenv("RCLONE_CONFIG_TEAM_DRIVE_CLIENT_ID", raising=False)

    client_id = "real-rclone-client-id-that-must-not-leak"
    runner = _ConfigRunner(f"[team-drive]\ntype = drive\nclient_id = {client_id}\n")
    archive = drive_archive_from_runtime(runner=runner, require_dedicated_client=True)

    assert archive.metrics.dedicated_client_configured is True
    assert client_id not in str(archive.metrics.to_dict())
    assert all(client_id not in " ".join(call) for call in runner.calls)


def test_source_archive_factory_accepts_only_rclone_consumed_client_override(tmp_path, monkeypatch):
    import kreports.config  # noqa: F401

    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    monkeypatch.setenv("KREPORTS_ENABLE_RAW_BACKFILL", "1")
    monkeypatch.setenv("RAW_STORAGE_BACKEND", "drive")
    monkeypatch.setenv("RAW_STORAGE_DRIVE_REMOTE", "team-drive:")
    monkeypatch.setenv("RAW_STORAGE_PREFIX", "kreports/raw")
    monkeypatch.setenv("RAW_STORAGE_SPOOL_DIR", str(tmp_path))
    monkeypatch.delenv("RAW_STORAGE_DRIVE_CLIENT_ID", raising=False)
    monkeypatch.setenv("RCLONE_CONFIG_TEAM_DRIVE_CLIENT_ID", "remote-override-id")

    archive = drive_archive_from_runtime(
        runner=_ConfigRunner("[team-drive]\ntype = drive\n"),
        require_dedicated_client=True,
    )

    assert archive.metrics.dedicated_client_configured is True


def test_source_archive_factory_reuses_explicit_operator_rclone_config(
    tmp_path, monkeypatch
):
    """The collector can reuse another pipeline's operator-owned rclone auth."""
    import kreports.config  # noqa: F401
    import kreports.storage.drive_archive as drive_archive_module

    config_path = tmp_path / "operator-rclone.conf"
    config_path.write_text(
        "[team-drive]\ntype = drive\nclient_id = existing-pipeline-client\n",
        encoding="utf-8",
    )
    config_path.chmod(0o600)
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    monkeypatch.setenv("KREPORTS_ENABLE_RAW_BACKFILL", "1")
    monkeypatch.setenv("RAW_STORAGE_BACKEND", "drive")
    monkeypatch.setenv("RAW_STORAGE_DRIVE_REMOTE", "team-drive:")
    monkeypatch.setenv("RAW_STORAGE_PREFIX", "kreports/raw")
    monkeypatch.setenv("RAW_STORAGE_SPOOL_DIR", str(tmp_path / "spool"))
    monkeypatch.setenv("RAW_STORAGE_RCLONE_CONFIG", str(config_path))

    calls: list[list[str]] = []

    def fake_run(args, **_kwargs):
        calls.append(list(args))
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=b"[team-drive]\ntype = drive\nclient_id = existing-pipeline-client\n",
            stderr=b"",
        )

    monkeypatch.setattr(drive_archive_module.subprocess, "run", fake_run)

    archive = drive_archive_module.drive_archive_from_runtime()

    assert archive.metrics.dedicated_client_configured is True
    assert calls
    assert calls[0][:3] == ["rclone", "--config", str(config_path)]
    assert "existing-pipeline-client" not in " ".join(calls[0])


def test_source_archive_factory_rejects_non_private_explicit_rclone_config(
    tmp_path, monkeypatch
):
    import kreports.config  # noqa: F401

    config_path = tmp_path / "operator-rclone.conf"
    config_path.write_text("[team-drive]\ntype = drive\n", encoding="utf-8")
    config_path.chmod(0o644)
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    monkeypatch.setenv("KREPORTS_ENABLE_RAW_BACKFILL", "1")
    monkeypatch.setenv("RAW_STORAGE_BACKEND", "drive")
    monkeypatch.setenv("RAW_STORAGE_DRIVE_REMOTE", "team-drive:")
    monkeypatch.setenv("RAW_STORAGE_PREFIX", "kreports/raw")
    monkeypatch.setenv("RAW_STORAGE_SPOOL_DIR", str(tmp_path / "spool"))
    monkeypatch.setenv("RAW_STORAGE_RCLONE_CONFIG", str(config_path))

    with pytest.raises(DriveArchiveConfigurationError, match="owner-only"):
        drive_archive_from_runtime()


def test_drive_writer_lease_is_exclusive_per_remote(tmp_path):
    first = DriveArchive("team-drive:", "kreports/raw", tmp_path, _Runner())
    second = DriveArchive("team-drive:", "other-root", tmp_path, _Runner())

    with first.writer_lease():
        with pytest.raises(Exception, match="writer lease"):
            with second.writer_lease():
                pass
