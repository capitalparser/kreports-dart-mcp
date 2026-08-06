"""Focused safety and request-budget tests for the bounded investor runner."""
from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


class _Response:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, str]:
        return {"status": "000", "message": "정상"}


class _RetryingClient:
    def __init__(self) -> None:
        self.calls = 0

    def __enter__(self) -> "_RetryingClient":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def get(self, url: str, **kwargs: object) -> _Response:
        del url, kwargs
        self.calls += 1
        raise httpx.RequestError("transport fixture")


def test_request_budget_counts_retry_attempts_and_blocks_next_http_call(monkeypatch):
    from kreports.collector import fetcher

    client = _RetryingClient()
    monkeypatch.setattr(fetcher, "_get_client", lambda: client)
    monkeypatch.setattr(fetcher.settings, "dart_api_key", "fixture-key")
    monkeypatch.setattr(fetcher.settings, "max_retries", 3)
    monkeypatch.setattr(fetcher.settings, "request_delay", 0)

    with pytest.raises(fetcher.DartRequestBudgetExceeded):
        with fetcher.request_budget(2) as budget:
            fetcher.fetch_financial_statements("00000001", 2025, "11011")

    assert client.calls == 2
    assert budget.used_calls == 2
    assert budget.endpoint_counts == {"fnlttSinglAcntAll.json": 2}


class _SequenceClient:
    def __init__(self, responses: list[dict[str, str]]) -> None:
        self.responses = iter(responses)
        self.calls: list[str] = []

    def __enter__(self) -> "_SequenceClient":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def get(self, url: str, **kwargs: object) -> _Response:
        del kwargs
        self.calls.append(url.rsplit("/", 1)[-1])
        response = _Response()
        response.json = lambda: next(self.responses)  # type: ignore[method-assign]
        return response


def test_request_budget_records_distinct_financial_endpoints(monkeypatch):
    from kreports.collector import fetcher

    client = _SequenceClient(
        [
            {"status": "013", "message": "no data"},
            {"status": "013", "message": "no data"},
        ]
    )
    monkeypatch.setattr(fetcher, "_get_client", lambda: client)
    monkeypatch.setattr(fetcher.settings, "dart_api_key", "fixture-key")
    monkeypatch.setattr(fetcher.settings, "max_retries", 1)
    monkeypatch.setattr(fetcher.settings, "request_delay", 0)

    with fetcher.request_budget(2) as budget:
        fetcher.fetch_financial_statements("00000001", 2025, "11011", "CFS")
        fetcher.fetch_financial_summary("00000001", 2025, "11011", "CFS")

    assert client.calls == ["fnlttSinglAcntAll.json", "fnlttSinglAcnt.json"]
    assert budget.used_calls == 2
    assert budget.endpoint_counts == {
        "fnlttSinglAcntAll.json": 1,
        "fnlttSinglAcnt.json": 1,
    }


def test_request_budget_counts_each_disclosure_list_page_and_blocks_the_next(monkeypatch):
    """Removing the per-page budget charge would permit an unbounded list crawl."""
    from kreports.collector import fetcher

    client = _SequenceClient(
        [
            {
                "status": "000", "message": "정상", "total_count": 201,
                "list": [{"rcept_no": f"2025030100{index:04d}"} for index in range(100)],
            },
            {
                "status": "000", "message": "정상", "total_count": 201,
                "list": [{"rcept_no": f"2025030200{index:04d}"} for index in range(100)],
            },
        ]
    )
    monkeypatch.setattr(fetcher, "_get_client", lambda: client)
    monkeypatch.setattr(fetcher.settings, "dart_api_key", "fixture-key")
    monkeypatch.setattr(fetcher.settings, "request_delay", 0)

    with pytest.raises(fetcher.DartRequestBudgetExceeded):
        with fetcher.request_budget(2) as budget:
            fetcher.fetch_disclosure_list(
                "00000001", "20250101", "20251231", disc_type="A",
            )

    assert client.calls == ["list.json", "list.json"]
    assert budget.used_calls == 2
    assert budget.endpoint_counts == {"list.json": 2}


@pytest.mark.parametrize(
    ("response", "expected_error"),
    [
        (
            {"status": "020", "message": "사용한도 초과"},
            "DartApiLimitExceeded",
        ),
        (
            {"status": "010", "message": "등록되지 않은 인증키"},
            "DartApiAuthError",
        ),
    ],
)
def test_disclosure_list_promotes_quota_and_auth_to_bounded_stops(
    monkeypatch,
    response,
    expected_error,
):
    """Stable stop classes prevent secret-bearing DART messages entering reports."""
    from kreports.collector import fetcher

    client = _SequenceClient([response])
    monkeypatch.setattr(fetcher, "_get_client", lambda: client)
    monkeypatch.setattr(fetcher.settings, "dart_api_key", "fixture-key")

    error_type = getattr(fetcher, expected_error)
    with pytest.raises(error_type):
        with fetcher.request_budget(1):
            fetcher.fetch_disclosure_list(
                "00000001", "20250101", "20251231", disc_type="A",
            )


def test_disclosure_list_malformed_json_is_redacted_bounded_transport(monkeypatch):
    """Malformed list payloads must not expose the request credential."""
    from kreports.collector import fetcher

    secret = "fixture-secret-key"

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, *args, **kwargs):
            return _MalformedJsonResponse()

    monkeypatch.setattr(fetcher, "_get_client", Client)
    monkeypatch.setattr(fetcher.settings, "dart_api_key", secret)

    with pytest.raises(fetcher.DartTransportError) as caught:
        with fetcher.request_budget(1):
            fetcher.fetch_disclosure_list(
                "00000001", "20250101", "20251231", disc_type="A",
            )

    assert secret not in str(caught.value)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"status": "000", "message": "정상", "total_count": 1, "list": "bad"},
        {"status": "000", "message": "정상", "total_count": "bad", "list": []},
        {"status": "000", "message": "정상", "list": [{"rcept_no": "1"}]},
        {"status": "000", "message": "정상", "total_count": -1, "list": []},
        {
            "status": "000", "message": "정상", "total_count": 0,
            "list": [{"rcept_no": "1"}],
        },
        {"status": "000", "message": "정상", "total_count": 1, "list": []},
    ],
)
def test_disclosure_list_rejects_malformed_success_payload(monkeypatch, payload):
    """Successful DART status still requires a bounded list response shape."""
    from kreports.collector import fetcher

    client = _SequenceClient([payload])
    monkeypatch.setattr(fetcher, "_get_client", lambda: client)
    monkeypatch.setattr(fetcher.settings, "dart_api_key", "fixture-key")

    with pytest.raises(fetcher.DartTransportError):
        with fetcher.request_budget(1):
            fetcher.fetch_disclosure_list(
                "00000001", "20250101", "20251231", disc_type="A",
            )


def test_disclosure_list_rejects_total_count_drift_between_pages(monkeypatch):
    """A changing total cannot prove a complete, deterministic bounded crawl."""
    from kreports.collector import fetcher

    client = _SequenceClient([
        {
            "status": "000", "message": "정상", "total_count": 101,
            "list": [{"rcept_no": str(index)} for index in range(100)],
        },
        {
            "status": "000", "message": "정상", "total_count": 102,
            "list": [{"rcept_no": "last"}],
        },
    ])
    monkeypatch.setattr(fetcher, "_get_client", lambda: client)
    monkeypatch.setattr(fetcher.settings, "dart_api_key", "fixture-key")
    monkeypatch.setattr(fetcher.settings, "request_delay", 0)

    with pytest.raises(fetcher.DartTransportError):
        with fetcher.request_budget(2) as budget:
            fetcher.fetch_disclosure_list(
                "00000001", "20250101", "20251231", disc_type="A",
            )

    assert budget.used_calls == 2


def test_disclosure_list_rejects_no_data_status_after_partial_page(monkeypatch):
    """Status 013 cannot silently terminate an already incomplete pagination."""
    from kreports.collector import fetcher

    client = _SequenceClient([
        {
            "status": "000", "message": "정상", "total_count": 101,
            "list": [{"rcept_no": str(index)} for index in range(100)],
        },
        {"status": "013", "message": "조회된 데이터가 없습니다"},
    ])
    monkeypatch.setattr(fetcher, "_get_client", lambda: client)
    monkeypatch.setattr(fetcher.settings, "dart_api_key", "fixture-key")
    monkeypatch.setattr(fetcher.settings, "request_delay", 0)

    with pytest.raises(fetcher.DartTransportError):
        with fetcher.request_budget(2):
            fetcher.fetch_disclosure_list(
                "00000001", "20250101", "20251231", disc_type="A",
            )


def test_disclosure_list_unknown_status_does_not_echo_remote_message(monkeypatch):
    """Unknown DART failures may carry request data and must stay redacted."""
    from kreports.collector import fetcher

    secret = "fixture-secret-key"
    client = _SequenceClient([
        {"status": "999", "message": f"remote echoed crtfc_key={secret}"},
    ])
    monkeypatch.setattr(fetcher, "_get_client", lambda: client)
    monkeypatch.setattr(fetcher.settings, "dart_api_key", secret)

    with pytest.raises(RuntimeError) as caught:
        with fetcher.request_budget(1):
            fetcher.fetch_disclosure_list(
                "00000001", "20250101", "20251231", disc_type="A",
            )

    assert str(caught.value) == "DART list.json returned an unsupported status"
    assert secret not in str(caught.value)


@pytest.mark.parametrize(
    "bounded_failure",
    [
        "budget",
        "transport",
    ],
)
def test_financial_collector_propagates_bounded_request_stops(
    monkeypatch,
    bounded_failure,
):
    from kreports.collector import fin_collector as collector

    monkeypatch.setattr(collector, "get_corp_code", lambda stock_code: "00000001")
    monkeypatch.setattr(collector, "_is_listed", lambda corp_code: True)
    if bounded_failure == "budget":
        failure = collector.DartRequestBudgetExceeded(1)
    else:
        failure = collector.DartTransportError("fnlttSinglAcntAll.json")
    statements = monkeypatch.setattr(
        collector,
        "fetch_financial_statements",
        lambda *args: (_ for _ in ()).throw(failure),
    )
    del statements
    summary_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        collector,
        "fetch_financial_summary",
        lambda *args: summary_calls.append(args),
    )

    with pytest.raises(type(failure)):
        collector.collect_financial("000001", 2025, 4)

    assert summary_calls == []


def test_financial_collector_does_not_swallow_bounded_stop_in_summary_fallback(
    monkeypatch,
):
    from kreports.collector import fin_collector as collector

    monkeypatch.setattr(collector, "get_corp_code", lambda stock_code: "00000001")
    monkeypatch.setattr(collector, "_is_listed", lambda corp_code: True)
    monkeypatch.setattr(
        collector,
        "fetch_financial_statements",
        lambda *args: {"status": "013", "message": "no data"},
    )
    summary_calls: list[str] = []

    def fail_summary(*args):
        summary_calls.append(args[3])
        raise collector.DartTransportError("fnlttSinglAcnt.json")

    monkeypatch.setattr(collector, "fetch_financial_summary", fail_summary)

    with pytest.raises(collector.DartTransportError):
        collector.collect_financial("000001", 2025, 4)

    assert summary_calls == ["CFS"]


def test_financial_collector_classifies_common_dart_auth_and_quota_messages():
    from kreports.collector.fin_collector import (
        _is_dart_auth_response,
        _is_dart_limit_response,
    )

    assert _is_dart_auth_response({"status": "010", "message": "인증키가 유효하지 않습니다."})
    assert _is_dart_limit_response({"status": "020", "message": "quota exceeded"})


def test_runner_dry_run_is_side_effect_free_and_reports_planner_targets(
    tmp_path: Path,
    monkeypatch,
):
    from kreports.maintenance.investor_core_backfill_runner import (
        run_investor_core_backfill,
    )
    from kreports.maintenance import investor_core_backfill_runner as runner

    database = tmp_path / "runner.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE marker (value TEXT NOT NULL)")
        connection.execute("INSERT INTO marker VALUES ('unchanged')")

    planner = {
        "coverage_year": 2025,
        "denominator": 188,
        "numerator": 135,
        "target_numerator": 179,
        "shortfall": 44,
        "selected_companies": [
            {
                "corp_code": "00000002",
                "stock_code": "000002",
                "source_ready": True,
                "selected_years": [2024],
            },
            {
                "corp_code": "00000001",
                "stock_code": "000001",
                "source_ready": True,
                "selected_years": [2025],
            },
        ],
    }
    monkeypatch.setenv("DB_URL", f"sqlite:///{database}")
    monkeypatch.setattr(runner.settings, "db_url", f"sqlite:///{database}")
    collector_calls: list[tuple[object, ...]] = []

    def planner_fn(*args, **kwargs):
        del args, kwargs
        return planner

    def collector_fn(*args, **kwargs):
        collector_calls.append((*args, kwargs))
        return "success"

    report = run_investor_core_backfill(
        database,
        planner_fn=planner_fn,
        collector_fn=collector_fn,
        disk_probe=lambda path: 20 * 1024**3,
    )

    assert report["dry_run"] is True
    assert report["execute"] is False
    assert report["completed"] is True
    assert report["target_count"] == 2
    assert report["target_samples"] == [
        {"corp_code": "00000001", "stock_code": "000001", "year": 2025},
        {"corp_code": "00000002", "stock_code": "000002", "year": 2024},
    ]
    assert report["target_outcomes"]["counts"] == {"planned": 2}
    assert collector_calls == []

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value FROM marker").fetchone()[0] == "unchanged"


def test_runner_dry_run_does_not_create_sqlite_sidecars(
    tmp_path: Path,
    monkeypatch,
):
    from kreports.maintenance import investor_core_backfill_runner as runner

    database = tmp_path / "runner.db"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE marker (value TEXT NOT NULL)")
        connection.execute("INSERT INTO marker VALUES ('unchanged')")
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(f"{database}{suffix}")
        if sidecar.exists():
            sidecar.unlink()

    _bind_runner_db(monkeypatch, runner, database)
    runner.run_investor_core_backfill(
        database,
        planner_fn=lambda *args, **kwargs: _runner_plan(),
        disk_probe=lambda path: 20 * 1024**3,
    )

    assert not Path(f"{database}-wal").exists()
    assert not Path(f"{database}-shm").exists()
    assert not Path(f"{database}-journal").exists()


def test_relevant_row_counts_batches_more_than_a_thousand_targets_without_double_counting(
    tmp_path: Path,
):
    """Each target is counted once even when the query must be split for SQLite."""
    from kreports.maintenance import investor_core_backfill_runner as runner

    database = tmp_path / "runner.db"
    target_count = 1_201
    targets = [
        runner._Target(f"{index:08d}", f"{index:06d}", 2025)
        for index in range(1, target_count + 1)
    ]
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE financials (corp_code TEXT, year INTEGER, quarter INTEGER);
            CREATE TABLE financial_facts (corp_code TEXT, bsns_year INTEGER, reprt_code TEXT);
            CREATE TABLE fetch_log (task_type TEXT, corp_code TEXT, year INTEGER, quarter INTEGER);
            """
        )
        connection.executemany(
            "INSERT INTO financials VALUES (?, ?, ?)",
            [(target.corp_code, target.year, 4) for target in targets],
        )
        connection.executemany(
            "INSERT INTO financial_facts VALUES (?, ?, ?)",
            [(target.corp_code, target.year, "11011") for target in targets],
        )
        connection.executemany(
            "INSERT INTO fetch_log VALUES (?, ?, ?, ?)",
            [("financial", target.corp_code, target.year, 4) for target in targets],
        )
        connection.execute("INSERT INTO financials VALUES ('99999999', 2025, 4)")

    assert runner._relevant_row_counts(database, targets) == {
        "financials": target_count,
        "financial_facts": target_count,
        "fetch_log": target_count,
    }


def test_runner_executes_sorted_source_ready_annual_q4_targets_once_and_skips_cache(
    tmp_path: Path,
    monkeypatch,
):
    from kreports.maintenance import investor_core_backfill_runner as runner

    database = tmp_path / "runner.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE financials (corp_code TEXT, year INTEGER, quarter INTEGER);
            CREATE TABLE financial_facts (corp_code TEXT, bsns_year INTEGER, reprt_code TEXT);
            CREATE TABLE fetch_log (task_type TEXT, corp_code TEXT, year INTEGER, quarter INTEGER);
            INSERT INTO financials VALUES ('00000999', 2025, 4);
            """
        )
    planner = {
        "coverage_year": 2025,
        "denominator": 188,
        "numerator": 135,
        "target_numerator": 179,
        "shortfall": 44,
        "selected_companies": [
            {
                "corp_code": "00000002",
                "stock_code": "000002",
                "source_ready": False,
                "selected_years": [2023],
            },
            {
                "corp_code": "00000002",
                "stock_code": "000002",
                "source_ready": True,
                "selected_years": [2024],
            },
            {
                "corp_code": "00000001",
                "stock_code": "000001",
                "source_ready": True,
                "selected_years": [2025],
            },
        ],
    }
    monkeypatch.setenv("DB_URL", f"sqlite:///{database}")
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    monkeypatch.setattr(runner.settings, "db_url", f"sqlite:///{database}")
    monkeypatch.setattr(runner.settings, "dart_api_key", "fixture-key")
    monkeypatch.setattr(runner.settings, "max_retries", 7)
    calls: list[tuple[str, int, int]] = []

    def collector(stock_code: str, year: int, *, quarter: int) -> str:
        calls.append((stock_code, year, quarter))
        return "success"

    def planner_fn(*args, **kwargs):
        del args, kwargs
        return planner

    report = runner.run_investor_core_backfill(
        database,
        execute=True,
        expected_db_sha256=runner._sha256_file(database),
        max_api_calls=4,
        planner_fn=planner_fn,
        collector_fn=collector,
        cache_checker=lambda corp_code, year, quarter: corp_code == "00000002",
        disk_probe=lambda path: 20 * 1024**3,
    )

    assert calls == [("000001", 2025, 4)]
    assert report["target_count"] == 2
    assert report["target_outcomes"]["counts"] == {"cached": 1, "success": 1}
    assert report["used_api_calls"] == 0
    assert report["completed"] is True
    assert runner.settings.max_retries == 7
    assert report["relevant_row_counts"]["before"] == report["relevant_row_counts"]["after"]


def test_default_cache_rejects_incomplete_annual_summary(
    tmp_path: Path,
):
    from kreports.maintenance import investor_core_backfill_runner as runner

    database = tmp_path / "runner.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE financials (
                corp_code TEXT, year INTEGER, quarter INTEGER, fs_div TEXT,
                revenue INTEGER, operating_profit INTEGER, net_income INTEGER,
                total_assets INTEGER, total_debt INTEGER, total_equity INTEGER,
                operating_cf INTEGER, source TEXT
            );
            CREATE TABLE financial_facts (
                corp_code TEXT, bsns_year INTEGER, reprt_code TEXT, fs_div TEXT,
                sj_div TEXT, account_id TEXT, account_nm TEXT,
                thstrm_amount INTEGER
            );
            INSERT INTO financials VALUES (
                '00000001', 2025, 4, 'CFS', 1, 1, 1, 1, 1, 1, NULL,
                'acntall'
            );
            """
        )

    assert runner._annual_core_source_cached(
        database,
        "00000001",
        2025,
        4,
    ) is False


def test_default_cache_accepts_complete_annual_summary(
    tmp_path: Path,
):
    from kreports.maintenance import investor_core_backfill_runner as runner

    database = tmp_path / "runner.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE financials (
                corp_code TEXT, year INTEGER, quarter INTEGER, fs_div TEXT,
                revenue INTEGER, operating_profit INTEGER, net_income INTEGER,
                total_assets INTEGER, total_debt INTEGER, total_equity INTEGER,
                operating_cf INTEGER, source TEXT
            );
            CREATE TABLE financial_facts (
                corp_code TEXT, bsns_year INTEGER, reprt_code TEXT, fs_div TEXT,
                sj_div TEXT, account_id TEXT, account_nm TEXT,
                thstrm_amount INTEGER
            );
            INSERT INTO financials VALUES (
                '00000001', 2025, 4, 'CFS', 1, 1, 1, 1, 1, 1, 1,
                'acntall'
            );
            """
        )

    assert runner._annual_core_source_cached(
        database,
        "00000001",
        2025,
        4,
    ) is True


def _create_runner_db(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE marker (value TEXT NOT NULL)")
        connection.execute("INSERT INTO marker VALUES ('fixture')")


def _runner_plan(*, source_ready: bool = True) -> dict[str, object]:
    return {
        "coverage_year": 2025,
        "denominator": 1,
        "numerator": 0,
        "target_numerator": 1,
        "shortfall": 1,
        "selected_companies": [
            {
                "corp_code": "00000001",
                "stock_code": "000001",
                "source_ready": source_ready,
                "selected_years": [2025],
            }
        ],
    }


def _bind_runner_db(monkeypatch, runner, database: Path) -> None:
    monkeypatch.setenv("DB_URL", f"sqlite:///{database}")
    monkeypatch.setattr(runner.settings, "db_url", f"sqlite:///{database}")
    monkeypatch.setattr(runner.settings, "dart_api_key", "fixture-key")
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")


def _unsafe_lock_fixture(lock_path: Path, kind: str) -> None:
    target = lock_path.with_name(f"{lock_path.name}.{kind}.target")
    target.write_bytes(b"fixture")
    if kind == "symlink":
        lock_path.symlink_to(target)
    elif kind == "fifo":
        target.unlink()
        os.mkfifo(lock_path)
    elif kind == "hardlink":
        os.link(target, lock_path)
    else:  # pragma: no cover - test fixture contract
        raise AssertionError(f"unsupported unsafe lock fixture: {kind}")


def test_runner_fails_closed_when_lock_path_is_unlinked_and_recreated_while_flocking(
    tmp_path: Path,
    monkeypatch,
):
    """Catch a new pathname inode replacing the descriptor after preflight."""
    from kreports.maintenance import investor_core_backfill_runner as runner

    database = tmp_path / "runner.db"
    _create_runner_db(database)
    identity = runner._capture_database_identity(database)
    lock_path = tmp_path / ".runner.db.investor-core.lock"
    preserved_path = tmp_path / "preserved-lock-inode"
    original_flock = runner.fcntl.flock
    replaced = False

    def flock_then_replace(descriptor: int, operation: int) -> None:
        nonlocal replaced
        original_flock(descriptor, operation)
        if operation & runner.fcntl.LOCK_EX and not replaced:
            os.link(lock_path, preserved_path)
            lock_path.unlink()
            lock_path.write_bytes(b"replacement")
            replaced = True

    monkeypatch.setattr(runner.fcntl, "flock", flock_then_replace)

    with pytest.raises(runner.InvestorCoreBackfillError) as caught:
        with runner._exclusive_execution_guard(identity):
            pytest.fail("replaced lock pathname must not enter bounded execution")

    assert replaced is True
    assert caught.value.code == "single_writer_guard_unavailable"


@pytest.mark.parametrize("kind", ("symlink", "fifo", "hardlink"))
def test_runner_rejects_unsafe_preexisting_single_writer_lock_file(
    tmp_path: Path,
    kind: str,
):
    """Catch an unsafe filesystem object being accepted as the lock inode."""
    from kreports.maintenance import investor_core_backfill_runner as runner

    database = tmp_path / "runner.db"
    _create_runner_db(database)
    identity = runner._capture_database_identity(database)
    lock_path = tmp_path / ".runner.db.investor-core.lock"
    _unsafe_lock_fixture(lock_path, kind)

    with pytest.raises(runner.InvestorCoreBackfillError) as caught:
        with runner._exclusive_execution_guard(identity):
            pytest.fail("unsafe lock filesystem object must fail closed")

    assert caught.value.code == "single_writer_guard_unavailable"


def test_runner_maps_flock_oserror_to_single_writer_guard_unavailable(
    tmp_path: Path,
    monkeypatch,
):
    """Catch an OS lock failure being collapsed into an ambiguous runner error."""
    from kreports.maintenance import investor_core_backfill_runner as runner

    database = tmp_path / "runner.db"
    _create_runner_db(database)
    identity = runner._capture_database_identity(database)

    def fail_flock(_descriptor: int, operation: int) -> None:
        if operation & runner.fcntl.LOCK_EX:
            raise OSError(errno.EIO, "fixture flock failure")

    monkeypatch.setattr(runner.fcntl, "flock", fail_flock)

    with pytest.raises(runner.InvestorCoreBackfillError) as caught:
        with runner._exclusive_execution_guard(identity):
            pytest.fail("flock failure must not enter bounded execution")

    assert caught.value.code == "single_writer_guard_unavailable"


def test_runner_fails_closed_when_a_second_thread_enters_same_database(
    tmp_path: Path,
    monkeypatch,
):
    """Catch removal of the process-local single-writer execution guard."""
    from kreports.maintenance import investor_core_backfill_runner as runner

    database = tmp_path / "runner.db"
    _create_runner_db(database)
    _bind_runner_db(monkeypatch, runner, database)
    expected_hash = runner._sha256_file(database)
    holder_entered = threading.Event()
    release_holder = threading.Event()
    holder_reports: list[dict[str, object]] = []
    holder_failures: list[BaseException] = []

    def holder_collector(*_args, **_kwargs) -> str:
        holder_entered.set()
        if not release_holder.wait(timeout=10):
            raise RuntimeError("fixture holder was not released")
        return "success"

    def run_holder() -> None:
        try:
            holder_reports.append(
                runner.run_investor_core_backfill(
                    database,
                    execute=True,
                    expected_db_sha256=expected_hash,
                    max_api_calls=1,
                    planner_fn=lambda *_args, **_kwargs: _runner_plan(),
                    collector_fn=holder_collector,
                    cache_checker=lambda *_args: False,
                    disk_probe=lambda _path: 20 * 1024**3,
                )
            )
        except BaseException as exc:  # pragma: no cover - assertion evidence
            holder_failures.append(exc)

    holder = threading.Thread(target=run_holder, name="investor-runner-holder")
    holder.start()
    assert holder_entered.wait(timeout=10)
    contender_calls: list[bool] = []
    try:
        with pytest.raises(runner.InvestorCoreBackfillError) as caught:
            runner.run_investor_core_backfill(
                database,
                execute=True,
                expected_db_sha256=expected_hash,
                max_api_calls=1,
                planner_fn=lambda *_args, **_kwargs: _runner_plan(),
                collector_fn=lambda *_args, **_kwargs: (
                    contender_calls.append(True) or "success"
                ),
                cache_checker=lambda *_args: False,
                disk_probe=lambda _path: 20 * 1024**3,
            )
    finally:
        release_holder.set()
        holder.join(timeout=10)

    assert caught.value.code == "backfill_already_running"
    assert contender_calls == []
    assert not holder.is_alive()
    assert holder_failures == []
    assert [report["completed"] for report in holder_reports] == [True]


def test_runner_fails_closed_when_a_second_process_enters_same_database(
    tmp_path: Path,
):
    """Catch a process-local lock that permits two independent writers."""
    database = tmp_path / "runner.db"
    _create_runner_db(database)
    ready = tmp_path / "holder.ready"
    release = tmp_path / "holder.release"
    project_root = Path(__file__).resolve().parents[1]
    script = textwrap.dedent(
        """
        import json
        import os
        from pathlib import Path
        import sys
        import time

        from kreports.maintenance import investor_core_backfill_runner as runner

        database = Path(sys.argv[1])
        role = sys.argv[2]
        ready = Path(sys.argv[3])
        release = Path(sys.argv[4])
        runner.settings.db_url = f"sqlite:///{database}"
        runner.settings.dart_api_key = "fixture-key"

        plan = {
            "coverage_year": 2025,
            "denominator": 1,
            "numerator": 0,
            "target_numerator": 1,
            "shortfall": 1,
            "selected_companies": [{
                "corp_code": "00000001",
                "stock_code": "000001",
                "source_ready": True,
                "selected_years": [2025],
            }],
        }

        def collector(*_args, **_kwargs):
            if role == "holder":
                ready.write_text("ready", encoding="utf-8")
                deadline = time.monotonic() + 15
                while not release.exists():
                    if time.monotonic() >= deadline:
                        raise RuntimeError("fixture holder was not released")
                    time.sleep(0.02)
            return "success"

        try:
            report = runner.run_investor_core_backfill(
                database,
                execute=True,
                expected_db_sha256=runner._sha256_file(database),
                max_api_calls=1,
                planner_fn=lambda *_args, **_kwargs: plan,
                collector_fn=collector,
                cache_checker=lambda *_args: False,
                disk_probe=lambda _path: 20 * 1024**3,
            )
            print(json.dumps({"ok": True, "completed": report["completed"]}))
        except runner.InvestorCoreBackfillError as exc:
            print(json.dumps({"ok": False, "code": exc.code}))
        """
    )
    environment = {
        **os.environ,
        "DB_URL": f"sqlite:///{database}",
        "DART_API_KEY": "fixture-key",
        "KREPORTS_RUNTIME_MODE": "collector",
        "PYTHONPATH": str(project_root),
    }
    command = [
        sys.executable,
        "-c",
        script,
        str(database),
        "holder",
        str(ready),
        str(release),
    ]
    holder = subprocess.Popen(
        command,
        cwd=project_root,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists():
            if holder.poll() is not None:
                stdout, stderr = holder.communicate()
                pytest.fail(f"holder exited before entering collector: {stdout} {stderr}")
            if time.monotonic() >= deadline:
                pytest.fail("holder did not enter collector")
            time.sleep(0.02)

        contender = subprocess.run(
            [*command[:3], str(database), "contender", str(ready), str(release)],
            cwd=project_root,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
        assert contender.returncode == 0, contender.stderr
        contender_payload = json.loads(contender.stdout)
        assert contender_payload == {
            "ok": False,
            "code": "backfill_already_running",
        }
    finally:
        release.write_text("release", encoding="utf-8")
        try:
            holder_stdout, holder_stderr = holder.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            holder.terminate()
            holder_stdout, holder_stderr = holder.communicate(timeout=10)

    assert holder.returncode == 0, holder_stderr
    assert json.loads(holder_stdout) == {"ok": True, "completed": True}


def test_runner_rejects_process_db_binding_before_planner_or_collector(
    tmp_path: Path,
    monkeypatch,
):
    from kreports.maintenance import investor_core_backfill_runner as runner

    database = tmp_path / "target.db"
    configured = tmp_path / "configured.db"
    _create_runner_db(database)
    _create_runner_db(configured)
    _bind_runner_db(monkeypatch, runner, configured)
    planner_calls: list[bool] = []
    collector_calls: list[bool] = []

    with pytest.raises(runner.InvestorCoreBackfillError) as caught:
        runner.run_investor_core_backfill(
            database,
            planner_fn=lambda *args, **kwargs: planner_calls.append(True),
            collector_fn=lambda *args, **kwargs: collector_calls.append(True),
        )

    assert caught.value.code == "database_binding_mismatch"
    assert planner_calls == []
    assert collector_calls == []


def test_runner_rejects_database_symlink_before_process_binding(
    tmp_path: Path,
    monkeypatch,
):
    from kreports.maintenance import investor_core_backfill_runner as runner

    database = tmp_path / "real.db"
    alias = tmp_path / "alias.db"
    _create_runner_db(database)
    alias.symlink_to(database)
    _bind_runner_db(monkeypatch, runner, database)

    with pytest.raises(runner.InvestorCoreBackfillError) as caught:
        runner.run_investor_core_backfill(
            alias,
            planner_fn=lambda *args, **kwargs: _runner_plan(),
            disk_probe=lambda path: 20 * 1024**3,
        )

    assert caught.value.code == "database_symlink_rejected"


def test_runner_rejects_nonempty_wal_before_planner(
    tmp_path: Path,
    monkeypatch,
):
    from kreports.maintenance import investor_core_backfill_runner as runner

    database = tmp_path / "runner.db"
    _create_runner_db(database)
    Path(f"{database}-wal").write_bytes(b"uncheckpointed")
    _bind_runner_db(monkeypatch, runner, database)
    planner_calls: list[bool] = []

    with pytest.raises(runner.InvestorCoreBackfillError) as caught:
        runner.run_investor_core_backfill(
            database,
            planner_fn=lambda *args, **kwargs: planner_calls.append(True),
        )

    assert caught.value.code == "database_unavailable"
    assert planner_calls == []


def test_runner_rejects_expected_hash_mismatch_before_planner_or_collector(
    tmp_path: Path,
    monkeypatch,
):
    from kreports.maintenance import investor_core_backfill_runner as runner

    database = tmp_path / "runner.db"
    _create_runner_db(database)
    _bind_runner_db(monkeypatch, runner, database)
    calls: list[str] = []

    with pytest.raises(runner.InvestorCoreBackfillError) as caught:
        runner.run_investor_core_backfill(
            database,
            execute=True,
            expected_db_sha256="0" * 64,
            max_api_calls=1,
            planner_fn=lambda *args, **kwargs: calls.append("planner"),
            collector_fn=lambda *args, **kwargs: calls.append("collector"),
            disk_probe=lambda path: 20 * 1024**3,
        )

    assert caught.value.code == "expected_db_sha256_mismatch"
    assert calls == []


def test_runner_rechecks_database_hash_before_execution(
    tmp_path: Path,
    monkeypatch,
):
    from kreports.maintenance import investor_core_backfill_runner as runner

    database = tmp_path / "runner.db"
    _create_runner_db(database)
    _bind_runner_db(monkeypatch, runner, database)
    initial_hash = runner._sha256_file(database)
    collector_calls: list[bool] = []

    def mutating_planner(*args, **kwargs):
        del args, kwargs
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE marker SET value='changed'")
        return _runner_plan()

    with pytest.raises(runner.InvestorCoreBackfillError) as caught:
        runner.run_investor_core_backfill(
            database,
            execute=True,
            expected_db_sha256=initial_hash,
            max_api_calls=1,
            planner_fn=mutating_planner,
            collector_fn=lambda *args, **kwargs: collector_calls.append(True),
            disk_probe=lambda path: 20 * 1024**3,
        )

    assert caught.value.code == "database_changed_before_execution"
    assert collector_calls == []


def test_runner_rejects_readonly_runtime_with_stable_error(
    tmp_path: Path,
    monkeypatch,
):
    from kreports.maintenance import investor_core_backfill_runner as runner

    database = tmp_path / "runner.db"
    _create_runner_db(database)
    _bind_runner_db(monkeypatch, runner, database)
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    with pytest.raises(runner.InvestorCoreBackfillError) as caught:
        runner.run_investor_core_backfill(
            database,
            execute=True,
            expected_db_sha256=runner._sha256_file(database),
            max_api_calls=1,
            planner_fn=lambda *args, **kwargs: _runner_plan(),
            disk_probe=lambda path: 20 * 1024**3,
        )

    assert caught.value.code == "collector_mode_required"


def test_runner_rejects_non_source_ready_execution_explicitly(
    tmp_path: Path,
    monkeypatch,
):
    from kreports.maintenance import investor_core_backfill_runner as runner

    database = tmp_path / "runner.db"
    _create_runner_db(database)
    _bind_runner_db(monkeypatch, runner, database)
    calls: list[str] = []

    with pytest.raises(runner.InvestorCoreBackfillError) as caught:
        runner.run_investor_core_backfill(
            database,
            execute=True,
            source_ready_only=False,
            expected_db_sha256=runner._sha256_file(database),
            max_api_calls=1,
            planner_fn=lambda *args, **kwargs: calls.append("planner"),
            collector_fn=lambda *args, **kwargs: calls.append("collector"),
            disk_probe=lambda path: 20 * 1024**3,
        )

    assert caught.value.code == "non_source_ready_execution_rejected"
    assert calls == []


def test_runner_stops_before_first_target_when_free_space_reserve_is_low(
    tmp_path: Path,
    monkeypatch,
):
    from kreports.maintenance import investor_core_backfill_runner as runner

    database = tmp_path / "runner.db"
    _create_runner_db(database)
    _bind_runner_db(monkeypatch, runner, database)
    collector_calls: list[bool] = []

    report = runner.run_investor_core_backfill(
        database,
        execute=True,
        expected_db_sha256=runner._sha256_file(database),
        max_api_calls=1,
        planner_fn=lambda *args, **kwargs: _runner_plan(),
        collector_fn=lambda *args, **kwargs: collector_calls.append(True),
        disk_probe=lambda path: runner.MIN_FREE_SPACE_BYTES - 1,
    )

    assert report["completed"] is False
    assert report["stop_reason"] == "insufficient_free_space"
    assert report["target_outcomes"]["counts"] == {"not_run": 1}
    assert collector_calls == []


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        ("budget", "api_budget_exhausted"),
        ("auth", "dart_auth_failure"),
        ("quota", "dart_quota_failure"),
        ("transport", "dart_transport_failure"),
    ],
)
def test_runner_stops_immediately_on_durable_dart_failures(
    tmp_path: Path,
    monkeypatch,
    failure,
    expected_code,
):
    from kreports.maintenance import investor_core_backfill_runner as runner

    database = tmp_path / "runner.db"
    _create_runner_db(database)
    _bind_runner_db(monkeypatch, runner, database)
    calls: list[tuple[str, int, int]] = []
    exceptions = {
        "budget": runner.DartRequestBudgetExceeded(1),
        "auth": runner.DartApiAuthError("fixture auth failure"),
        "quota": runner.DartApiLimitExceeded("fixture quota failure"),
        "transport": runner.DartTransportError("fnlttSinglAcntAll.json"),
    }

    def collector(stock_code: str, year: int, *, quarter: int) -> str:
        calls.append((stock_code, year, quarter))
        raise exceptions[failure]

    plan = _runner_plan()
    plan["selected_companies"] = [
        {
            "corp_code": "00000001",
            "stock_code": "000001",
            "source_ready": True,
            "selected_years": [2025],
        },
        {
            "corp_code": "00000002",
            "stock_code": "000002",
            "source_ready": True,
            "selected_years": [2024],
        },
    ]
    report = runner.run_investor_core_backfill(
        database,
        execute=True,
        expected_db_sha256=runner._sha256_file(database),
        max_api_calls=2,
        planner_fn=lambda *args, **kwargs: plan,
        collector_fn=collector,
        cache_checker=lambda *args: False,
        disk_probe=lambda path: 20 * 1024**3,
    )

    assert report["stop_reason"] == expected_code
    assert report["completed"] is False
    assert calls == [("000001", 2025, 4)]
    assert report["target_outcomes"]["total"] == 2
    assert report["target_outcomes"]["counts"] == {"not_run": 1, "stopped": 1}


def test_runner_report_never_serializes_api_key(
    tmp_path: Path,
    monkeypatch,
):
    import json

    from kreports.maintenance import investor_core_backfill_runner as runner

    database = tmp_path / "runner.db"
    _create_runner_db(database)
    _bind_runner_db(monkeypatch, runner, database)
    secret = "fixture-secret-api-key"
    monkeypatch.setattr(runner.settings, "dart_api_key", secret)

    report = runner.run_investor_core_backfill(
        database,
        planner_fn=lambda *args, **kwargs: _runner_plan(),
        disk_probe=lambda path: 20 * 1024**3,
    )

    assert secret not in json.dumps(report, ensure_ascii=False)


def test_runner_budget_covers_financial_fallback_endpoints_and_stops_before_next_call(
    tmp_path: Path,
    monkeypatch,
):
    from kreports.collector import fetcher
    from kreports.maintenance import investor_core_backfill_runner as runner

    database = tmp_path / "runner.db"
    _create_runner_db(database)
    _bind_runner_db(monkeypatch, runner, database)
    monkeypatch.setattr(fetcher.settings, "dart_api_key", "fixture-key")
    responses = _SequenceClient(
        [
            {"status": "013", "message": "no data"},
            {"status": "013", "message": "no data"},
            {"status": "013", "message": "no data"},
        ]
    )
    monkeypatch.setattr(fetcher, "_get_client", lambda: responses)
    monkeypatch.setattr(fetcher.settings, "max_retries", 5)
    monkeypatch.setattr(runner.settings, "max_retries", 5)

    def fallback_collector(stock_code: str, year: int, *, quarter: int) -> str:
        del stock_code, year, quarter
        assert runner.settings.max_retries == 1
        fetcher.fetch_financial_statements("00000001", 2025, "11011", "CFS")
        fetcher.fetch_financial_statements("00000001", 2025, "11011", "OFS")
        fetcher.fetch_financial_summary("00000001", 2025, "11011", "CFS")
        fetcher.fetch_financial_summary("00000001", 2025, "11011", "OFS")
        return "no_data"

    report = runner.run_investor_core_backfill(
        database,
        execute=True,
        expected_db_sha256=runner._sha256_file(database),
        max_api_calls=3,
        planner_fn=lambda *args, **kwargs: _runner_plan(),
        collector_fn=fallback_collector,
        cache_checker=lambda *args: False,
        disk_probe=lambda path: 20 * 1024**3,
    )

    assert report["stop_reason"] == "api_budget_exhausted"
    assert report["used_api_calls"] == 3
    assert report["endpoint_call_counts"] == {
        "fnlttSinglAcnt.json": 1,
        "fnlttSinglAcntAll.json": 2,
    }
    assert responses.calls == [
        "fnlttSinglAcntAll.json",
        "fnlttSinglAcntAll.json",
        "fnlttSinglAcnt.json",
    ]
    assert runner.settings.max_retries == 5


def test_default_cache_accepts_facts_with_exactly_the_canonical_core_seven(
    tmp_path: Path,
):
    from kreports.maintenance import investor_core_backfill_runner as runner
    from kreports.semantic.metrics import CORE_FINANCIAL_METRICS, metric_definition

    database = tmp_path / "runner.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE financials (
                corp_code TEXT, year INTEGER, quarter INTEGER, fs_div TEXT,
                revenue INTEGER, operating_profit INTEGER, net_income INTEGER,
                total_assets INTEGER, total_debt INTEGER, total_equity INTEGER,
                operating_cf INTEGER, source TEXT
            );
            CREATE TABLE financial_facts (
                corp_code TEXT, bsns_year INTEGER, reprt_code TEXT, fs_div TEXT,
                sj_div TEXT, account_id TEXT, account_nm TEXT,
                thstrm_amount INTEGER
            );
            """
        )
        for metric_key in CORE_FINANCIAL_METRICS:
            definition = metric_definition(metric_key)
            account_id = definition.source_account_groups[0][0]
            statement = definition.statement_division_preference[0]
            connection.execute(
                "INSERT INTO financial_facts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "00000001",
                    2025,
                    "11011",
                    "CFS",
                    statement,
                    account_id,
                    metric_key,
                    1,
                ),
            )

    assert runner._annual_core_source_cached(database, "00000001", 2025, 4) is True


def test_runner_probes_free_space_after_a_cached_target(
    tmp_path: Path,
    monkeypatch,
):
    from kreports.maintenance import investor_core_backfill_runner as runner

    database = tmp_path / "runner.db"
    _create_runner_db(database)
    _bind_runner_db(monkeypatch, runner, database)
    plan = _runner_plan()
    plan["selected_companies"] = [
        {
            "corp_code": "00000001",
            "stock_code": "000001",
            "source_ready": True,
            "selected_years": [2025],
        },
        {
            "corp_code": "00000002",
            "stock_code": "000002",
            "source_ready": True,
            "selected_years": [2024],
        },
    ]
    events: list[str] = []

    def disk_probe(path: Path) -> int:
        assert path == database
        events.append("probe")
        return 20 * 1024**3

    def cache_checker(corp_code: str, year: int, quarter: int) -> bool:
        assert corp_code in {"00000001", "00000002"}
        assert quarter == 4
        events.append(f"cache:{year}")
        return year == 2025

    def collector(*args, **kwargs) -> str:
        del args, kwargs
        events.append("collect")
        return "success"

    report = runner.run_investor_core_backfill(
        database,
        execute=True,
        expected_db_sha256=runner._sha256_file(database),
        max_api_calls=1,
        planner_fn=lambda *args, **kwargs: plan,
        collector_fn=collector,
        cache_checker=cache_checker,
        disk_probe=disk_probe,
    )

    assert report["completed"] is True
    assert events == [
        "probe",  # pre-execution
        "probe", "cache:2025", "probe",  # cached target
        "probe", "cache:2024", "collect", "probe",  # uncached target
        "probe",  # post-run evidence
    ]


def test_runner_checkpoints_actual_wal_writer_before_hashing_post_evidence(
    tmp_path: Path,
    monkeypatch,
):
    from kreports.collector import fin_collector
    from kreports.maintenance import investor_core_backfill_runner as runner

    database = tmp_path / "runner.db"
    _create_runner_db(database)
    _bind_runner_db(monkeypatch, runner, database)

    def durable_collector(stock_code: str, year: int, quarter: int) -> str:
        del stock_code, year, quarter
        with fin_collector.get_session() as session:
            session.execute(text("PRAGMA journal_mode=WAL"))
            session.execute(text("INSERT INTO marker VALUES ('durable')"))
        return "success"

    monkeypatch.setattr(fin_collector, "collect_financial", durable_collector)
    report = runner.run_investor_core_backfill(
        database,
        execute=True,
        expected_db_sha256=runner._sha256_file(database),
        max_api_calls=1,
        planner_fn=lambda *args, **kwargs: _runner_plan(),
        cache_checker=lambda *args: False,
        disk_probe=lambda path: 20 * 1024**3,
    )

    assert report["completed"] is True
    assert report["wal_checkpointed"] is True
    assert report["db_sha256_before"] != report["db_sha256_after"]
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone() == (0, 0, 0)
        assert connection.execute("SELECT COUNT(*) FROM marker").fetchone()[0] == 2


def test_runner_returns_incomplete_report_when_post_evidence_fails_after_action(
    tmp_path: Path,
    monkeypatch,
):
    from kreports.maintenance import investor_core_backfill_runner as runner

    database = tmp_path / "runner.db"
    _create_runner_db(database)
    _bind_runner_db(monkeypatch, runner, database)
    actual_counts = runner._relevant_row_counts
    calls = 0

    def failing_after_counts(path, targets):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise runner.InvestorCoreBackfillError(
                "relevant_row_count_failed",
                "fixture post evidence failure",
            )
        return actual_counts(path, targets)

    monkeypatch.setattr(runner, "_relevant_row_counts", failing_after_counts)
    report = runner.run_investor_core_backfill(
        database,
        execute=True,
        expected_db_sha256=runner._sha256_file(database),
        max_api_calls=3,
        planner_fn=lambda *args, **kwargs: _runner_plan(),
        collector_fn=lambda *args, **kwargs: "success",
        cache_checker=lambda *args: False,
        disk_probe=lambda path: 20 * 1024**3,
    )

    assert report["completed"] is False
    assert report["stop_reason"] == "evidence_collection_failed"
    assert report["target_outcomes"]["counts"] == {"success": 1}
    assert report["max_api_calls"] == 3
    assert report["used_api_calls"] == 0
    assert report["relevant_row_counts"]["after"] is None


def test_runner_preserves_outcomes_and_budget_when_real_wal_checkpoint_is_busy(
    tmp_path: Path,
    monkeypatch,
):
    from kreports.maintenance import investor_core_backfill_runner as runner

    database = tmp_path / "runner.db"
    _create_runner_db(database)
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    reader = sqlite3.connect(database)
    reader.execute("BEGIN")
    reader.execute("SELECT * FROM marker").fetchall()
    _bind_runner_db(monkeypatch, runner, database)

    def durable_collector(*args, **kwargs) -> str:
        del args, kwargs
        with sqlite3.connect(database) as connection:
            connection.execute("INSERT INTO marker VALUES ('checkpoint-blocked')")
        return "success"

    try:
        report = runner.run_investor_core_backfill(
            database,
            execute=True,
            expected_db_sha256=runner._sha256_file(database),
            max_api_calls=7,
            planner_fn=lambda *args, **kwargs: _runner_plan(),
            collector_fn=durable_collector,
            cache_checker=lambda *args: False,
            disk_probe=lambda path: 20 * 1024**3,
        )
    finally:
        reader.close()

    assert report["completed"] is False
    assert report["stop_reason"] == "durability_checkpoint_failed"
    assert report["target_outcomes"]["counts"] == {"success": 1}
    assert report["max_api_calls"] == 7
    assert report["used_api_calls"] == 0
    assert report["db_sha256_after"] is None
    assert report["relevant_row_counts"]["after"] is None


def test_default_collector_binds_all_imported_engine_sessions_to_target_database(
    tmp_path: Path,
    monkeypatch,
):
    from kreports.collector import corp_sync, fin_collector
    from kreports.db import engine as db_engine
    from kreports.judge import beneish, flags
    from kreports.maintenance import investor_core_backfill_runner as runner

    target_database = tmp_path / "target.db"
    stale_database = tmp_path / "stale.db"
    _create_runner_db(target_database)
    _create_runner_db(stale_database)
    _bind_runner_db(monkeypatch, runner, target_database)
    stale_engine = create_engine(f"sqlite:///{stale_database}")
    stale_session = sessionmaker(bind=stale_engine, autocommit=False, autoflush=False)
    original_engine = db_engine.engine
    original_session = db_engine.SessionLocal
    db_engine.engine = stale_engine
    db_engine.SessionLocal = stale_session

    def collector(stock_code: str, year: int, quarter: int) -> str:
        del stock_code, year, quarter
        for module in (fin_collector, corp_sync, flags, beneish):
            with module.get_session() as session:
                session.execute(text("INSERT INTO marker VALUES ('target-only')"))
        return "success"

    monkeypatch.setattr(fin_collector, "collect_financial", collector)
    try:
        report = runner.run_investor_core_backfill(
            target_database,
            execute=True,
            expected_db_sha256=runner._sha256_file(target_database),
            max_api_calls=1,
            planner_fn=lambda *args, **kwargs: _runner_plan(),
            cache_checker=lambda *args: False,
            disk_probe=lambda path: 20 * 1024**3,
        )
    finally:
        db_engine.engine = original_engine
        db_engine.SessionLocal = original_session
        stale_engine.dispose()

    assert report["completed"] is True
    with sqlite3.connect(target_database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM marker").fetchone()[0] == 5
    with sqlite3.connect(stale_database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM marker").fetchone()[0] == 1


def test_checkpoint_open_does_not_create_database_after_path_is_deleted(
    tmp_path: Path,
    monkeypatch,
):
    from kreports.maintenance import investor_core_backfill_runner as runner

    database = tmp_path / "runner.db"
    _create_runner_db(database)
    identity = runner._capture_database_identity(database)
    original_open = runner.os.open

    def delete_before_open(path, flags, *args):
        if Path(path) == database:
            database.unlink()
        return original_open(path, flags, *args)

    monkeypatch.setattr(runner.os, "open", delete_before_open)
    with pytest.raises(runner.InvestorCoreBackfillError) as caught:
        runner._checkpoint_wal(identity)

    assert caught.value.code == "database_connection_identity_mismatch"
    assert not database.exists()


def test_verified_writer_open_does_not_recreate_path_deleted_after_fd_authentication(
    tmp_path: Path,
    monkeypatch,
):
    """Catch a creation-capable SQLite connect after the checked FD is open."""
    from kreports.maintenance import investor_core_backfill_runner as runner

    database = tmp_path / "runner.db"
    _create_runner_db(database)
    identity = runner._capture_database_identity(database)
    original_connect = runner.sqlite3.connect
    authenticated_open_reached = False

    def delete_immediately_before_sqlite_connect(*args, **kwargs):
        nonlocal authenticated_open_reached
        authenticated_open_reached = True
        database.unlink()
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(
        runner.sqlite3,
        "connect",
        delete_immediately_before_sqlite_connect,
    )
    with pytest.raises(runner.InvestorCoreBackfillError) as caught:
        runner._open_verified_sqlite_connection(identity)

    assert authenticated_open_reached is True
    assert caught.value.code == "database_connection_identity_mismatch"
    assert not database.exists()


def test_verified_writer_open_rejects_wrong_inode_after_path_is_restored(
    tmp_path: Path,
    monkeypatch,
):
    """Catch SQLite retaining a swapped inode after the pathname looks safe again."""
    from kreports.maintenance import investor_core_backfill_runner as runner

    database = tmp_path / "runner.db"
    wrong_database = tmp_path / "wrong-opened.db"
    retained_wrong_database = tmp_path / "retained-wrong-opened.db"
    staged_expected_database = tmp_path / "staged-expected.db"
    _create_runner_db(database)
    _create_runner_db(wrong_database)
    with sqlite3.connect(wrong_database) as connection:
        connection.execute("INSERT INTO marker VALUES ('wrong-opened')")
    retained_wrong_database.hardlink_to(wrong_database)
    expected_identity = runner._capture_database_identity(database)
    wrong_identity = wrong_database.stat()
    wrong_bytes_before = retained_wrong_database.read_bytes()
    original_connect = runner.sqlite3.connect
    authenticated_open_reached = False

    def connect_wrong_inode_then_restore_expected(*args, **kwargs):
        nonlocal authenticated_open_reached
        authenticated_open_reached = True
        assert (database.stat().st_dev, database.stat().st_ino) == (
            expected_identity.device,
            expected_identity.inode,
        )
        database.replace(staged_expected_database)
        wrong_database.replace(database)
        try:
            return original_connect(*args, **kwargs)
        finally:
            staged_expected_database.replace(database)

    monkeypatch.setattr(
        runner.sqlite3,
        "connect",
        connect_wrong_inode_then_restore_expected,
    )
    with pytest.raises(runner.InvestorCoreBackfillError) as caught:
        runner._open_verified_sqlite_connection(expected_identity)

    assert authenticated_open_reached is True
    assert caught.value.code == "database_connection_identity_mismatch"
    current_expected = database.stat()
    assert (current_expected.st_dev, current_expected.st_ino) == (
        expected_identity.device,
        expected_identity.inode,
    )
    retained_wrong = retained_wrong_database.stat()
    assert (retained_wrong.st_dev, retained_wrong.st_ino) == (
        wrong_identity.st_dev,
        wrong_identity.st_ino,
    )
    assert retained_wrong_database.read_bytes() == wrong_bytes_before


def test_verified_writer_open_rejects_replacement_inode_before_sqlite_connects(
    tmp_path: Path,
    monkeypatch,
):
    from kreports.maintenance import investor_core_backfill_runner as runner

    database = tmp_path / "runner.db"
    replacement = tmp_path / "replacement.db"
    _create_runner_db(database)
    _create_runner_db(replacement)
    identity = runner._capture_database_identity(database)
    original_open = runner.os.open

    def replace_before_open(path, flags, *args):
        if Path(path) == database:
            replacement.replace(database)
        return original_open(path, flags, *args)

    monkeypatch.setattr(runner.os, "open", replace_before_open)
    with pytest.raises(runner.InvestorCoreBackfillError) as caught:
        runner._open_verified_sqlite_connection(identity)

    assert caught.value.code == "database_connection_identity_mismatch"
    current = database.stat()
    assert (current.st_dev, current.st_ino) != (identity.device, identity.inode)


def test_verified_writer_allows_reused_authenticated_descriptor(
    tmp_path: Path,
    monkeypatch,
):
    """Descriptor-number reuse must not fail an otherwise pinned connection."""
    from kreports.maintenance import investor_core_backfill_runner as runner

    database = tmp_path / "runner.db"
    _create_runner_db(database)
    identity = runner._capture_database_identity(database)
    descriptor = os.open(database, os.O_RDONLY)
    try:
        authenticated = {descriptor: (identity.device, identity.inode)}
        monkeypatch.setattr(
            runner,
            "_open_file_identities",
            lambda: dict(authenticated),
        )
        connection = sqlite3.connect(database)
        try:
            runner._verify_sqlite_descriptor_delta(
                connection,
                identity,
                descriptors_before=authenticated,
            )
        finally:
            connection.close()
    finally:
        os.close(descriptor)


def test_default_writer_creator_verifies_each_new_dbapi_connection(
    tmp_path: Path,
    monkeypatch,
):
    from kreports.collector import fin_collector
    from kreports.maintenance import investor_core_backfill_runner as runner

    database = tmp_path / "runner.db"
    _create_runner_db(database)
    _bind_runner_db(monkeypatch, runner, database)
    identity = runner._capture_database_identity(database)
    original_open = runner.os.open
    opened_identities: list[tuple[int, int]] = []

    def recording_open(path, flags, *args):
        descriptor = original_open(path, flags, *args)
        if Path(path) == database:
            metadata = runner.os.fstat(descriptor)
            opened_identities.append((metadata.st_dev, metadata.st_ino))
        return descriptor

    monkeypatch.setattr(runner.os, "open", recording_open)

    def collector(stock_code: str, year: int, quarter: int) -> str:
        del stock_code, year, quarter
        for _ in range(2):
            with fin_collector.get_session() as session:
                session.execute(text("INSERT INTO marker VALUES ('verified-writer')"))
        return "success"

    monkeypatch.setattr(fin_collector, "collect_financial", collector)
    report = runner.run_investor_core_backfill(
        database,
        execute=True,
        expected_db_sha256=runner._sha256_file(database),
        max_api_calls=1,
        planner_fn=lambda *args, **kwargs: _runner_plan(),
        cache_checker=lambda *args: False,
        disk_probe=lambda path: 20 * 1024**3,
    )

    assert report["completed"] is True
    assert len(opened_identities) >= 3
    assert all(item == (identity.device, identity.inode) for item in opened_identities)


def test_runner_partitions_fifty_three_targets_into_cached_and_collected(
    tmp_path: Path,
    monkeypatch,
):
    from kreports.maintenance import investor_core_backfill_runner as runner

    database = tmp_path / "runner.db"
    _create_runner_db(database)
    _bind_runner_db(monkeypatch, runner, database)
    years = list(range(1973, 2026))
    cached_years = {year for year in years if year % 5 == 0}
    plan = _runner_plan()
    plan["selected_companies"] = [{
        "corp_code": "00000001",
        "stock_code": "000001",
        "source_ready": True,
        "selected_years": years,
    }]
    calls: list[int] = []

    def collector(stock_code: str, year: int, *, quarter: int) -> str:
        assert stock_code == "000001"
        assert quarter == 4
        calls.append(year)
        return "success"

    report = runner.run_investor_core_backfill(
        database,
        execute=True,
        expected_db_sha256=runner._sha256_file(database),
        max_api_calls=53,
        planner_fn=lambda *args, **kwargs: plan,
        collector_fn=collector,
        cache_checker=lambda corp_code, year, quarter: year in cached_years,
        disk_probe=lambda path: 20 * 1024**3,
    )

    assert report["completed"] is True
    assert report["target_count"] == 53
    assert calls == [year for year in years if year not in cached_years]
    assert len(calls) == report["target_count"] - len(cached_years)
    assert report["target_outcomes"]["counts"] == {
        "cached": len(cached_years),
        "success": len(calls),
    }


def test_runner_rejects_database_hardlink_before_planning(
    tmp_path: Path,
    monkeypatch,
):
    from kreports.maintenance import investor_core_backfill_runner as runner

    database = tmp_path / "runner.db"
    alias = tmp_path / "alias.db"
    _create_runner_db(database)
    alias.hardlink_to(database)
    _bind_runner_db(monkeypatch, runner, database)

    with pytest.raises(runner.InvestorCoreBackfillError) as caught:
        runner.run_investor_core_backfill(
            database,
            planner_fn=lambda *args, **kwargs: _runner_plan(),
        )

    assert caught.value.code == "database_hardlink_rejected"


def test_runner_stops_when_database_path_is_replaced_during_target(
    tmp_path: Path,
    monkeypatch,
):
    from kreports.maintenance import investor_core_backfill_runner as runner

    database = tmp_path / "runner.db"
    replacement = tmp_path / "replacement.db"
    _create_runner_db(database)
    _create_runner_db(replacement)
    _bind_runner_db(monkeypatch, runner, database)

    def replace_during_cache_check(*args) -> bool:
        replacement.replace(database)
        return True

    report = runner.run_investor_core_backfill(
        database,
        execute=True,
        expected_db_sha256=runner._sha256_file(database),
        max_api_calls=1,
        planner_fn=lambda *args, **kwargs: _runner_plan(),
        collector_fn=lambda *args, **kwargs: "success",
        cache_checker=replace_during_cache_check,
        disk_probe=lambda path: 20 * 1024**3,
    )

    assert report["completed"] is False
    assert report["stop_reason"] == "database_identity_changed"
    assert report["target_outcomes"]["counts"] == {"cached": 1}


class _MalformedJsonResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, str]:
        raise ValueError("fixture-secret-response?crtfc_key=fixture-secret-key")


def test_bounded_malformed_json_stops_redacted_without_leaking_request_secrets(
    tmp_path: Path,
    monkeypatch,
    caplog,
):
    from kreports.collector import fetcher
    from kreports.maintenance import investor_core_backfill_runner as runner

    secret = "fixture-secret-key"
    database = tmp_path / "runner.db"
    _create_runner_db(database)
    _bind_runner_db(monkeypatch, runner, database)
    monkeypatch.setattr(fetcher.settings, "dart_api_key", secret)
    monkeypatch.setattr(fetcher.settings, "max_retries", 1)
    monkeypatch.setattr(fetcher.settings, "request_delay", 0)

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, *args, **kwargs):
            return _MalformedJsonResponse()

    monkeypatch.setattr(fetcher, "_get_client", Client)

    with pytest.raises(fetcher.DartTransportError) as caught:
        with fetcher.request_budget(1):
            fetcher.fetch_financial_statements("00000001", 2025, "11011")
    assert secret not in str(caught.value)

    report = runner.run_investor_core_backfill(
        database,
        execute=True,
        expected_db_sha256=runner._sha256_file(database),
        max_api_calls=1,
        planner_fn=lambda *args, **kwargs: _runner_plan(),
        collector_fn=lambda *args, **kwargs: fetcher.fetch_financial_statements(
            "00000001", 2025, "11011"
        ),
        cache_checker=lambda *args: False,
        disk_probe=lambda path: 20 * 1024**3,
    )

    assert report["stop_reason"] == "dart_transport_failure"
    assert secret not in str(report)
    assert secret not in caplog.text
