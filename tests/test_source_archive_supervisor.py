from __future__ import annotations

from pathlib import Path

from kreports.maintenance.source_archive_campaign import (
    SourceArchivePlan,
    SourceArchiveReport,
)


def _plan() -> SourceArchivePlan:
    return SourceArchivePlan(
        years=(2021,), shard_count=1, targets=(), target_digest="a" * 64,
        state_dir=Path("campaign"),
    )


def _report(stop_reason: str | None) -> SourceArchiveReport:
    return SourceArchiveReport(
        shard=0, apply=True, status="partial", target_digest="a" * 64,
        outcomes=(), stop_reason=stop_reason,
    )


def test_local_call_boundary_starts_the_next_batch_after_short_pause():
    from kreports.maintenance.source_archive_supervisor import supervise_source_archive

    reports = iter([_report("api_budget_exhausted"), _report(None)])
    calls: list[int] = []
    sleeps: list[float] = []

    result = supervise_source_archive(
        _plan(), archive=object(), max_dart_calls=100,
        run_shard=lambda *_args, **_kwargs: (calls.append(1), next(reports))[1],
        sleeper=sleeps.append, max_batches=2,
    )

    assert len(calls) == 2
    assert sleeps == [30]
    assert result.batch_count == 2


def test_supervisor_completes_one_year_before_advancing_to_the_next_year():
    from kreports.maintenance.source_archive_supervisor import supervise_source_archive

    plan = SourceArchivePlan(
        years=(2021, 2022), shard_count=2, targets=(), target_digest="a" * 64,
        state_dir=Path("campaign"),
    )
    reports = iter([_report(None), _report(None), _report(None), _report(None)])
    calls: list[tuple[int, int | None]] = []

    def run_shard(*args, **kwargs):
        calls.append((args[1], kwargs.get("target_year")))
        return next(reports)

    supervise_source_archive(
        plan, archive=object(), max_dart_calls=100, run_shard=run_shard,
        sleeper=lambda _: None, max_batches=4,
    )

    assert calls == [(0, 2021), (1, 2021), (0, 2022), (1, 2022)]


def test_provider_quota_is_probed_again_after_fifteen_minutes():
    from kreports.maintenance.source_archive_supervisor import supervise_source_archive

    reports = iter([_report("dart_quota_failure"), _report(None)])
    sleeps: list[float] = []

    result = supervise_source_archive(
        _plan(), archive=object(), max_dart_calls=100,
        run_shard=lambda *_args, **_kwargs: next(reports),
        sleeper=sleeps.append, max_batches=2,
    )

    assert sleeps == [15 * 60]
    assert result.batch_count == 2


def test_drive_transport_failure_retries_the_same_bounded_batch_after_one_minute():
    from kreports.maintenance.source_archive_supervisor import supervise_source_archive

    reports = iter([_report("drive_transport_failure"), _report(None)])
    sleeps: list[float] = []

    result = supervise_source_archive(
        _plan(), archive=object(), max_dart_calls=100,
        run_shard=lambda *_args, **_kwargs: next(reports),
        sleeper=sleeps.append, max_batches=2,
    )

    assert sleeps == [60]
    assert result.batch_count == 2


def test_auth_failure_hard_stops_without_sleeping_or_retrying():
    from kreports.maintenance.source_archive_supervisor import supervise_source_archive

    sleeps: list[float] = []
    result = supervise_source_archive(
        _plan(), archive=object(), max_dart_calls=100,
        run_shard=lambda *_args, **_kwargs: _report("dart_auth_failure"),
        sleeper=sleeps.append,
    )

    assert result.stop_reason == "dart_auth_failure"
    assert result.batch_count == 1
    assert sleeps == []


def test_provider_quota_rotates_to_the_next_key_without_long_sleep(tmp_path):
    from kreports.collector import fetcher
    from kreports.collector.dart_api_key_ring import DartApiKeyRing
    from kreports.maintenance.source_archive_supervisor import supervise_source_archive

    key_file = tmp_path / "keys"
    key_file.write_text("second-secret\n", encoding="utf-8")
    key_file.chmod(0o600)
    ring = DartApiKeyRing.from_values(
        primary_key="primary-secret", key_file=key_file,
        state_file=tmp_path / "rotation.json",
    )
    reports = iter([_report("dart_quota_failure"), _report(None)])
    used_keys: list[str] = []
    sleeps: list[float] = []

    def run_shard(*_args, **_kwargs):
        used_keys.append(fetcher._dart_api_key())
        return next(reports)

    result = supervise_source_archive(
        _plan(), archive=object(), max_dart_calls=100,
        run_shard=run_shard, sleeper=sleeps.append, max_batches=2,
        key_ring=ring,
    )

    assert used_keys == ["primary-secret", "second-secret"]
    assert sleeps == []
    assert result.key_switch_count == 1


def test_all_quota_limited_keys_wait_then_begin_a_new_probe_cycle(tmp_path):
    from kreports.collector import fetcher
    from kreports.collector.dart_api_key_ring import DartApiKeyRing
    from kreports.maintenance.source_archive_supervisor import supervise_source_archive

    key_file = tmp_path / "keys"
    key_file.write_text("second-secret\n", encoding="utf-8")
    key_file.chmod(0o600)
    ring = DartApiKeyRing.from_values(
        primary_key="primary-secret", key_file=key_file,
        state_file=tmp_path / "rotation.json",
    )
    reports = iter([
        _report("dart_quota_failure"),
        _report("dart_quota_failure"),
        _report(None),
    ])
    used_keys: list[str] = []
    sleeps: list[float] = []

    def run_shard(*_args, **_kwargs):
        used_keys.append(fetcher._dart_api_key())
        return next(reports)

    result = supervise_source_archive(
        _plan(), archive=object(), max_dart_calls=100,
        run_shard=run_shard, sleeper=sleeps.append, max_batches=3,
        key_ring=ring,
    )

    assert used_keys == ["primary-secret", "second-secret", "primary-secret"]
    assert sleeps == [15 * 60]
    assert result.key_switch_count == 2


def test_single_key_quota_probe_cycle_is_not_counted_as_a_switch(tmp_path):
    from kreports.collector.dart_api_key_ring import DartApiKeyRing
    from kreports.maintenance.source_archive_supervisor import supervise_source_archive

    key_file = tmp_path / "keys"
    key_file.write_text("", encoding="utf-8")
    key_file.chmod(0o600)
    ring = DartApiKeyRing.from_values(
        primary_key="primary-secret", key_file=key_file,
        state_file=tmp_path / "rotation.json",
    )
    reports = iter([_report("dart_quota_failure"), _report(None)])

    result = supervise_source_archive(
        _plan(), archive=object(), max_dart_calls=100,
        run_shard=lambda *_args, **_kwargs: next(reports),
        sleeper=lambda _: None, max_batches=2, key_ring=ring,
    )

    assert result.key_switch_count == 0


def test_one_invalid_key_is_quarantined_while_next_key_continues(tmp_path):
    from kreports.collector import fetcher
    from kreports.collector.dart_api_key_ring import DartApiKeyRing
    from kreports.maintenance.source_archive_supervisor import supervise_source_archive

    key_file = tmp_path / "keys"
    key_file.write_text("second-secret\n", encoding="utf-8")
    key_file.chmod(0o600)
    ring = DartApiKeyRing.from_values(
        primary_key="primary-secret", key_file=key_file,
        state_file=tmp_path / "rotation.json",
    )
    reports = iter([_report("dart_auth_failure"), _report(None)])
    used_keys: list[str] = []

    def run_shard(*_args, **_kwargs):
        used_keys.append(fetcher._dart_api_key())
        return next(reports)

    result = supervise_source_archive(
        _plan(), archive=object(), max_dart_calls=100,
        run_shard=run_shard, sleeper=lambda _: None, max_batches=2,
        key_ring=ring,
    )

    assert used_keys == ["primary-secret", "second-secret"]
    assert result.stop_reason == "max_batches_reached"
    assert result.key_switch_count == 1


def test_auto_run_cli_loads_the_runtime_key_file_into_supervisor(
    tmp_path, monkeypatch
):
    from typer.testing import CliRunner

    from kreports.cli import main
    from kreports.maintenance import source_archive_supervisor as supervisor
    from kreports.storage import drive_archive

    key_file = tmp_path / "keys"
    key_file.write_text("second-secret\n", encoding="utf-8")
    key_file.chmod(0o600)
    monkeypatch.setenv("DART_API_KEYS_FILE", str(key_file))
    monkeypatch.setattr(main.settings, "dart_api_key", "primary-secret")
    monkeypatch.setattr(
        main, "_source_archive_plan_from_database",
        lambda *_args, **_kwargs: _plan(),
    )
    monkeypatch.setattr(
        drive_archive, "drive_archive_from_runtime", lambda **_kwargs: object()
    )
    captured: dict[str, object] = {}

    def supervise(*_args, **kwargs):
        captured.update(kwargs)
        return supervisor.SourceArchiveSupervisorReport(
            batch_count=1, sweep_count=0, stop_reason="max_batches_reached",
            last_report=None,
        )

    monkeypatch.setattr(supervisor, "supervise_source_archive", supervise)

    result = CliRunner().invoke(main.app, [
        "source-archive-auto-run",
        "--db", str(tmp_path / "candidate.db"),
        "--state-dir", str(tmp_path / "campaign"),
        "--year", "2021",
        "--max-batches", "1",
    ])

    assert result.exit_code == 0, result.output
    assert captured["key_ring"].key_count == 2
