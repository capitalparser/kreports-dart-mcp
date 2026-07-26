import json
from datetime import datetime, timedelta, timezone
import os
import socket
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
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


def test_concurrent_force_claims_have_exactly_one_database_owner(
    monkeypatch,
    tmp_path,
):
    import kreports.db.engine as engine_module
    from kreports.db.models import BackfillRun, Base
    from kreports.maintenance.backfill_runs import (
        BackfillAlreadyRunning,
        BackfillLease,
    )

    isolated = create_engine(
        f"sqlite:///{tmp_path / 'lease-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.create_all(isolated)
    monkeypatch.setattr(
        engine_module,
        "SessionLocal",
        sessionmaker(bind=isolated, autocommit=False, autoflush=False),
    )
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    barrier = Barrier(2)

    def claim():
        barrier.wait()
        try:
            return BackfillLease.start(
                "financials",
                2021,
                "LISTED",
                {"year_to": 2025},
                force=True,
            )
        except BackfillAlreadyRunning:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: claim(), range(2)))

    assert sum(claim is not None for claim in claims) == 1
    with isolated.connect() as connection:
        running = connection.execute(
            BackfillRun.__table__.select().where(
                BackfillRun.status == "running"
            )
        ).mappings().all()
    assert len(running) == 1
    assert running[0]["lease_key"] == "financials|2021|LISTED"


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
            owner_host=socket.gethostname(),
            owner_process_start="dead-start",
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
            owner_host=socket.gethostname(),
            owner_process_start="live-start",
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
                owner_host=socket.gethostname(),
                owner_process_start=f"dead-start-{index}",
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


def test_stale_repair_detects_same_pid_from_a_different_process_start(
    temp_engine,
    monkeypatch,
):
    from kreports.db.engine import get_session
    from kreports.db.models import BackfillRun
    from kreports.maintenance import backfill_runs

    now = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
    with get_session() as session:
        run = BackfillRun(
            task_type="financials",
            status="running",
            pid=os.getpid(),
            owner_token="old-process",
            owner_host=socket.gethostname(),
            owner_process_start="old-start",
            heartbeat_at=now - timedelta(hours=2),
            started_at=now - timedelta(hours=3),
        )
        session.add(run)
        session.flush()
        run_id = run.id

    monkeypatch.setattr(
        backfill_runs,
        "process_start_identity",
        lambda pid: "new-start",
    )

    assert backfill_runs.repair_stale_backfills(now, 3600)["repaired_ids"] == [
        run_id
    ]


def test_stale_repair_fails_safe_for_remote_owner_identity(
    temp_engine,
    monkeypatch,
):
    from kreports.db.engine import get_session
    from kreports.db.models import BackfillRun
    from kreports.maintenance import backfill_runs

    now = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
    with get_session() as session:
        run = BackfillRun(
            task_type="financials",
            status="running",
            pid=63111,
            owner_token="unknown-owner",
            owner_host="remote-collector",
            owner_process_start="remote-start",
            heartbeat_at=now - timedelta(hours=2),
            started_at=now - timedelta(hours=3),
        )
        session.add(run)
        session.flush()
        run_id = run.id

    monkeypatch.setattr(backfill_runs, "pid_is_alive", lambda pid: False)

    assert backfill_runs.repair_stale_backfills(now, 3600)["repaired_ids"] == []
    assert _load_run(run_id).status == "running"


def test_stale_repair_repairs_dead_legacy_local_row_268_idempotently(
    temp_engine,
    monkeypatch,
):
    from kreports.db.engine import get_session
    from kreports.db.models import BackfillRun
    from kreports.maintenance import backfill_runs

    now = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
    with get_session() as session:
        session.add(
            BackfillRun(
                id=268,
                task_type="financials",
                status="running",
                pid=61401,
                owner_token=None,
                owner_host=None,
                owner_process_start=None,
                heartbeat_at=None,
                started_at=now - timedelta(hours=3),
            )
        )

    monkeypatch.setattr(backfill_runs, "pid_is_alive", lambda pid: False)

    assert backfill_runs.repair_stale_backfills(now, 3600) == {
        "repaired_count": 1,
        "repaired_ids": [268],
    }
    assert backfill_runs.repair_stale_backfills(now, 3600) == {
        "repaired_count": 0,
        "repaired_ids": [],
    }
    assert _load_run(268).status == "stale_failed"


def test_stale_repair_preserves_live_legacy_pid(temp_engine, monkeypatch):
    from kreports.db.engine import get_session
    from kreports.db.models import BackfillRun
    from kreports.maintenance import backfill_runs

    now = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
    with get_session() as session:
        run = BackfillRun(
            task_type="financials",
            status="running",
            pid=61401,
            owner_token=None,
            owner_host=None,
            owner_process_start=None,
            heartbeat_at=None,
            started_at=now - timedelta(hours=3),
        )
        session.add(run)
        session.flush()
        run_id = run.id

    monkeypatch.setattr(backfill_runs, "pid_is_alive", lambda pid: True)

    assert backfill_runs.repair_stale_backfills(now, 3600)["repaired_ids"] == []
    assert _load_run(run_id).status == "running"


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


def test_succeed_rejects_summary_with_errors(temp_engine):
    from kreports.maintenance.backfill_runs import BackfillLease, BackfillRunError

    lease = BackfillLease.start("financials", 2021, "LISTED", {})

    with pytest.raises(BackfillRunError) as raised:
        lease.succeed({"saved": 3, "errors": 1})

    assert raised.value.outcome == "storage_error"
    assert _load_run(lease.id).status == "running"


@pytest.mark.parametrize(
    ("exc", "outcome"),
    [
        (TimeoutError("network timeout"), "transport_error"),
        (ValueError("JSON parse failed"), "parse_error"),
        (OSError("disk is full"), "storage_error"),
    ],
)
def test_failure_taxonomy_classifies_real_failure_classes(exc, outcome):
    from kreports.maintenance.backfill_runs import classify_backfill_error

    assert classify_backfill_error(exc) == outcome


def test_financial_company_error_raises_structured_transport_failure(
    temp_engine,
    monkeypatch,
):
    from kreports.collector import fin_collector
    from kreports.collector.scheduler import run_resumable_financial_backfill
    from kreports.db.engine import get_session
    from kreports.db.models import Company, FetchLog
    from kreports.maintenance.backfill_runs import BackfillLease, BackfillRunError

    with get_session() as session:
        session.add(
            Company(
                corp_code="00000001",
                stock_code="000001",
                corp_name="실패회사",
                market="KOSPI",
            )
        )

    def failed_collect(*args, **kwargs):
        with get_session() as session:
            session.add(
                FetchLog(
                    task_type="financial",
                    corp_code="00000001",
                    year=2021,
                    quarter=4,
                    status="error",
                    error_msg="network timeout",
                    fetched_at=datetime(2026, 7, 26),
                )
            )
        return {"success": 0, "no_data": 0, "error": 1, "skipped": 0}

    monkeypatch.setattr(fin_collector, "collect_financial_range", failed_collect)
    lease = BackfillLease.start("financials", 2021, "LISTED", {})

    with pytest.raises(BackfillRunError) as raised:
        run_resumable_financial_backfill(
            lease,
            year_from=2021,
            year_to=2021,
            market=None,
        )

    assert raised.value.outcome == "transport_error"
    assert BackfillLease.resume_point(lease.id) == {}


def test_structured_child_quota_failure_is_not_succeeded(
    temp_engine,
    monkeypatch,
):
    import kreports.collector.scheduler as scheduler
    import kreports.cli.main as cli
    from kreports.db.engine import get_session
    from kreports.db.models import BackfillRun
    from kreports.maintenance.backfill_runs import BackfillRunError

    monkeypatch.setattr(cli.settings, "dart_api_key", "test-key")
    monkeypatch.setattr(
        scheduler,
        "_run_cli_with_heartbeat",
        lambda lease, args: (_ for _ in ()).throw(
            BackfillRunError("quota_exceeded", "DART quota exhausted")
        ),
    )

    result = CliRunner().invoke(
        cli.app,
        ["orchestrate-complete-backfill"],
    )

    assert result.exit_code != 0
    with get_session() as session:
        run = (
            session.query(BackfillRun)
            .filter(BackfillRun.task_type == "complete_dataset")
            .order_by(BackfillRun.id.desc())
            .first()
        )
        assert run is not None, (
            f"CLI exception={result.exception!r}, output={result.output!r}, "
            f"rows={session.query(BackfillRun.task_type, BackfillRun.status).all()!r}"
        )
        assert run.status == "quota_exceeded"
        assert run.status != "no_data"


@pytest.mark.parametrize(
    "outcome",
    ["quota_exceeded", "transport_error", "parse_error", "storage_error"],
)
def test_child_process_failure_preserves_recorded_outcome(
    temp_engine,
    outcome,
):
    from kreports.collector.scheduler import _child_process_failure
    from kreports.db.engine import get_session
    from kreports.db.models import BackfillRun

    with get_session() as session:
        session.add(
            BackfillRun(
                task_type="child",
                status=outcome,
                pid=64001,
                error_msg=f"{outcome} detail",
                started_at=datetime(2026, 7, 26),
                finished_at=datetime(2026, 7, 26),
            )
        )

    failure = _child_process_failure(
        pid=64001,
        return_code=1,
        args=["collect-all"],
    )

    assert failure.outcome == outcome
    assert str(failure) == f"{outcome} detail"


def test_raw_enabled_pipeline_accepts_external_non_inline_policy(
    temp_engine,
    monkeypatch,
):
    import kreports.collector.scheduler as scheduler
    from kreports.maintenance.backfill_runs import BackfillLease

    monkeypatch.setenv("KREPORTS_ENABLE_RAW_BACKFILL", "1")
    monkeypatch.setenv("RAW_STORAGE_BACKEND", "file")
    monkeypatch.setenv("RAW_STORAGE_KEEP_INLINE", "false")
    monkeypatch.setattr(
        scheduler,
        "_run_cli_with_heartbeat",
        lambda lease, args: None,
    )
    lease = BackfillLease.start("complete_dataset", 2021, "LISTED", {})

    result = scheduler.orchestrate_complete_backfill(lease)

    assert result["errors"] == 0


def test_raw_enabled_pipeline_rejects_inline_retention(
    temp_engine,
    monkeypatch,
):
    import kreports.collector.scheduler as scheduler
    from kreports.maintenance.backfill_runs import BackfillLease

    monkeypatch.setenv("KREPORTS_ENABLE_RAW_BACKFILL", "1")
    monkeypatch.setenv("RAW_STORAGE_BACKEND", "file")
    monkeypatch.setenv("RAW_STORAGE_KEEP_INLINE", "true")
    lease = BackfillLease.start("complete_dataset", 2021, "LISTED", {})

    with pytest.raises(RuntimeError, match="must not keep raw bodies inline"):
        scheduler.orchestrate_complete_backfill(lease)


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


@pytest.mark.parametrize(
    "invalid",
    [
        {"values": {"a", "b"}},
        {"object": object()},
        {1: "non-string-key"},
    ],
)
def test_lease_json_rejects_non_json_types_and_non_string_keys(
    temp_engine,
    invalid,
):
    from kreports.maintenance.backfill_runs import BackfillLease

    with pytest.raises(ValueError, match="JSON"):
        BackfillLease.start("strict-json", 2021, "LISTED", invalid)


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


def test_backfill_status_json_is_readonly_and_does_not_initialize(
    temp_engine,
    monkeypatch,
):
    import kreports.cli.main as cli
    from kreports.maintenance.backfill_runs import BackfillLease

    lease = BackfillLease.start("financials", 2021, "LISTED", {})
    lease.succeed({"saved": 1})
    statements = []
    event.listen(
        temp_engine,
        "before_cursor_execute",
        lambda conn, cursor, statement, parameters, context, executemany: (
            statements.append(statement)
        ),
    )
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")
    monkeypatch.setattr(
        cli,
        "init_db",
        lambda: (_ for _ in ()).throw(
            AssertionError("readonly status must not initialize")
        ),
    )

    result = CliRunner().invoke(cli.app, ["backfill-status", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["runs"][0]["id"] == lease.id
    assert statements
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)


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
