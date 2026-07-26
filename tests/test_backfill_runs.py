import json
from datetime import datetime, timedelta, timezone

import pytest
from typer.testing import CliRunner


def _load_run(run_id):
    from kreports.db.engine import get_session
    from kreports.db.models import BackfillRun

    with get_session() as session:
        run = session.get(BackfillRun, run_id)
        session.expunge(run)
        return run


def test_lease_checkpoint_resumes_after_last_completed_company(temp_engine):
    from kreports.maintenance.backfill_runs import BackfillLease

    lease = BackfillLease.start(
        "financials",
        year=2021,
        market="LISTED",
        params={"year_to": 2025},
    )
    lease.checkpoint(
        {"last_corp_code": "00111999"},
        attempted=1200,
        saved=1130,
        no_data=60,
        errors=10,
    )

    assert BackfillLease.resume_point(lease.id) == {
        "last_corp_code": "00111999",
    }
    run = _load_run(lease.id)
    assert (
        run.attempted_count,
        run.saved_count,
        run.no_data_count,
        run.error_count,
    ) == (1200, 1130, 60, 10)


def test_non_owner_cannot_mutate_a_running_lease(temp_engine):
    from kreports.maintenance.backfill_runs import BackfillLease, LeaseOwnershipError

    owner = BackfillLease.start("financials", 2021, "LISTED", {})
    impostor = BackfillLease(id=owner.id, owner_token="not-the-owner")

    for operation in (
        impostor.heartbeat,
        lambda: impostor.checkpoint({}, 0, 0, 0, 0),
        lambda: impostor.succeed({}),
        lambda: impostor.fail("transport_error", "network down"),
    ):
        with pytest.raises(LeaseOwnershipError):
            operation()

    assert _load_run(owner.id).status == "running"


def test_new_lease_resumes_latest_failed_matching_run(temp_engine):
    from kreports.maintenance.backfill_runs import BackfillLease

    failed = BackfillLease.start(
        "financials",
        2021,
        "LISTED",
        {"year_to": 2025},
    )
    failed.checkpoint(
        {"last_corp_code": "00111999"},
        attempted=12,
        saved=10,
        no_data=1,
        errors=1,
    )
    failed.fail("interrupted", "stopped")

    resumed = BackfillLease.start(
        "financials",
        2021,
        "LISTED",
        {"year_to": 2025},
    )

    assert BackfillLease.resume_point(resumed.id) == {
        "last_corp_code": "00111999",
    }
    run = _load_run(resumed.id)
    assert (
        run.attempted_count,
        run.saved_count,
        run.no_data_count,
        run.error_count,
    ) == (12, 10, 1, 1)


def test_dead_timed_out_run_becomes_stale_failed(temp_engine, monkeypatch):
    from kreports.db.engine import get_session
    from kreports.db.models import BackfillRun
    from kreports.maintenance import backfill_runs

    now = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
    with get_session() as session:
        run = BackfillRun(
            task_type="financials",
            year=2021,
            market="LISTED",
            status="running",
            pid=61401,
            owner_token="dead-owner",
            heartbeat_at=now - timedelta(hours=2),
            started_at=now - timedelta(hours=3),
        )
        session.add(run)
        session.flush()
        run_id = run.id

    monkeypatch.setattr(backfill_runs, "pid_is_alive", lambda pid: False)
    first = backfill_runs.repair_stale_backfills(now, timeout_seconds=3600)
    second = backfill_runs.repair_stale_backfills(now, timeout_seconds=3600)

    assert first == {"repaired_count": 1, "repaired_ids": [run_id]}
    assert second == {"repaired_count": 0, "repaired_ids": []}
    assert _load_run(run_id).status == "stale_failed"


@pytest.mark.parametrize("heartbeat_age", [3599, 3600, 7200])
def test_stale_repair_preserves_live_owner(
    temp_engine,
    monkeypatch,
    heartbeat_age,
):
    from kreports.db.engine import get_session
    from kreports.db.models import BackfillRun
    from kreports.maintenance import backfill_runs

    now = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
    with get_session() as session:
        run = BackfillRun(
            task_type="financials",
            status="running",
            pid=61402,
            owner_token="live-owner",
            heartbeat_at=now - timedelta(seconds=heartbeat_age),
            started_at=now - timedelta(hours=3),
        )
        session.add(run)
        session.flush()
        run_id = run.id

    monkeypatch.setattr(backfill_runs, "pid_is_alive", lambda pid: True)

    assert backfill_runs.repair_stale_backfills(now, 3600)["repaired_ids"] == []
    assert _load_run(run_id).status == "running"


def test_equal_timeout_dead_owner_is_repaired_deterministically(
    temp_engine,
    monkeypatch,
):
    from kreports.db.engine import get_session
    from kreports.db.models import BackfillRun
    from kreports.maintenance import backfill_runs

    now = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
    with get_session() as session:
        runs = [
            BackfillRun(
                task_type=f"task-{index}",
                status="running",
                pid=62000 + index,
                owner_token=f"owner-{index}",
                heartbeat_at=now - timedelta(seconds=3600),
                started_at=now - timedelta(hours=2),
            )
            for index in (2, 1)
        ]
        session.add_all(runs)
        session.flush()
        expected = sorted(run.id for run in runs)

    monkeypatch.setattr(backfill_runs, "pid_is_alive", lambda pid: False)

    assert backfill_runs.repair_stale_backfills(now, 3600)["repaired_ids"] == expected


def test_failure_taxonomy_never_maps_failures_to_no_data(temp_engine):
    from kreports.maintenance.backfill_runs import (
        BACKFILL_OUTCOMES,
        BackfillLease,
    )

    assert BACKFILL_OUTCOMES == (
        "success",
        "no_data",
        "quota_exceeded",
        "transport_error",
        "parse_error",
        "storage_error",
        "stale_failed",
        "interrupted",
    )
    for outcome in BACKFILL_OUTCOMES[2:]:
        lease = BackfillLease.start(f"task-{outcome}", None, None, {})
        lease.fail(outcome, f"{outcome} detail")
        run = _load_run(lease.id)
        assert run.status == outcome
        assert run.status != "no_data"


def test_checkpoint_json_is_canonical_and_bounded(temp_engine):
    from kreports.maintenance.backfill_runs import BackfillLease

    lease = BackfillLease.start("financials", 2021, "LISTED", {})
    lease.checkpoint(
        {"z": 1, "a": {"y": 2, "x": 1}},
        attempted=1,
        saved=1,
        no_data=0,
        errors=0,
    )

    assert _load_run(lease.id).checkpoint_json == '{"a":{"x":1,"y":2},"z":1}'
    with pytest.raises(ValueError, match="checkpoint"):
        lease.checkpoint(
            {"payload": "x" * 20_000},
            attempted=2,
            saved=1,
            no_data=0,
            errors=1,
        )


def test_financial_orchestrator_checkpoints_after_each_company_and_resumes(
    temp_engine,
    monkeypatch,
):
    from kreports.collector import fin_collector
    from kreports.collector.scheduler import run_resumable_financial_backfill
    from kreports.db.engine import get_session
    from kreports.db.models import Company
    from kreports.maintenance.backfill_runs import BackfillLease

    with get_session() as session:
        session.add_all(
            [
                Company(
                    corp_code="00000001",
                    stock_code="000001",
                    corp_name="첫째",
                    market="KOSPI",
                ),
                Company(
                    corp_code="00000002",
                    stock_code="000002",
                    corp_name="둘째",
                    market="KOSDAQ",
                ),
                Company(
                    corp_code="00000003",
                    stock_code="000003",
                    corp_name="셋째",
                    market="KONEX",
                ),
            ]
        )

    calls = []

    def fake_collect(stock_code, year_from, year_to, *, force=False):
        calls.append(stock_code)
        return {"success": 1, "no_data": 0, "error": 0, "skipped": 3}

    monkeypatch.setattr(fin_collector, "collect_financial_range", fake_collect)
    lease = BackfillLease.start("financials", 2021, "LISTED", {})
    lease.checkpoint(
        {"last_corp_code": "00000001"},
        attempted=1,
        saved=1,
        no_data=0,
        errors=0,
    )

    result = run_resumable_financial_backfill(
        lease,
        year_from=2021,
        year_to=2025,
        market=None,
    )

    assert calls == ["000002", "000003"]
    assert result == {
        "attempted": 3,
        "saved": 3,
        "no_data": 0,
        "errors": 0,
        "skipped": 6,
    }
    assert BackfillLease.resume_point(lease.id)["last_corp_code"] == "00000003"


def test_backfill_status_json_is_stable_and_bounded(temp_engine):
    from kreports.cli.main import app
    from kreports.maintenance.backfill_runs import BackfillLease

    first = BackfillLease.start("financials", 2021, "LISTED", {"b": 2, "a": 1})
    first.heartbeat()
    second = BackfillLease.start("audit_fees", 2022, "KOSPI", {})
    second.succeed({"saved": 3})

    result = CliRunner().invoke(app, ["backfill-status", "--json", "--limit", "1"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["count"] == 1
    assert payload["runs"][0]["id"] == second.id
    assert list(payload) == ["count", "runs"]


def test_repair_stale_backfills_cli_emits_json(temp_engine, monkeypatch):
    from kreports.cli.main import app
    from kreports.maintenance import backfill_runs

    monkeypatch.setattr(
        backfill_runs,
        "repair_stale_backfills",
        lambda now, timeout_seconds: {
            "repaired_count": 0,
            "repaired_ids": [],
        },
    )

    result = CliRunner().invoke(
        app,
        ["repair-stale-backfills", "--timeout-seconds", "3600", "--json"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "repaired_count": 0,
        "repaired_ids": [],
    }


def test_shell_wrappers_delegate_progress_to_one_python_command():
    complete = open(
        "scripts/run_complete_dataset_backfill.sh",
        encoding="utf-8",
    ).read()
    wrapper = open(
        "scripts/dart_limit_aware_backfill.sh",
        encoding="utf-8",
    ).read()

    assert "require_backfill_free_space" in complete
    assert "backfill_preflight.sh" in complete
    assert "kreports orchestrate-complete-backfill" in complete
    assert "sqlite3 " not in wrapper
    assert "mark_stale_backfills" not in wrapper
    assert "has_live_backfill" not in wrapper
    assert wrapper.count("orchestrate-complete-backfill") == 1
