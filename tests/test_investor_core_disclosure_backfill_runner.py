"""Safety and scope tests for investor-core disclosure metadata remediation."""
from __future__ import annotations

from datetime import date
from contextlib import contextmanager
import sqlite3
from types import SimpleNamespace

import pytest


def _create_metadata_db(database):
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE disclosures ("
            "rcept_no TEXT PRIMARY KEY, corp_code TEXT NOT NULL, corp_name TEXT NOT NULL, "
            "disc_date TEXT NOT NULL, disc_type TEXT NOT NULL, report_nm TEXT NOT NULL, "
            "flr_nm TEXT, fetched_at TEXT NOT NULL)"
        )


def _metadata_plan():
    return {
        "denominator": 100,
        "numerator": 80,
        "target_numerator": 95,
        "shortfall": 15,
        "selected_companies": [
            {
                "corp_code": "00000002", "stock_code": "000002",
                "corp_name": "메타누락", "source_ready": False,
                "selected_years": [2025], "invalid_annual_anchor_years": [],
                "missing_disclosure_metadata_years": [2025],
            },
        ],
    }


def test_metadata_targets_include_only_nonready_missing_or_invalid_years():
    """Including ready or already-anchored years would expand paid DART scope."""
    from kreports.maintenance.investor_core_disclosure_backfill_runner import (
        metadata_targets_from_plan,
    )

    plan = {
        "selected_companies": [
            {
                "corp_code": "00000003",
                "stock_code": "000003",
                "corp_name": "이미준비",
                "source_ready": True,
                "selected_years": [2025],
                "invalid_annual_anchor_years": [],
                "missing_disclosure_metadata_years": [],
            },
            {
                "corp_code": "00000002",
                "stock_code": "0010V0",
                "corp_name": "메타누락",
                "source_ready": False,
                "selected_years": [2025, 2024],
                "invalid_annual_anchor_years": [],
                "missing_disclosure_metadata_years": [2025],
            },
            {
                "corp_code": "00000001",
                "stock_code": "000001",
                "corp_name": "앵커오류",
                "source_ready": False,
                "selected_years": [2024],
                "invalid_annual_anchor_years": [2024],
                "missing_disclosure_metadata_years": [],
            },
        ]
    }

    targets = metadata_targets_from_plan(plan, as_of_date=date(2026, 8, 5))

    assert targets == [
        {
            "corp_code": "00000001",
            "stock_code": "000001",
            "corp_name": "앵커오류",
            "refresh_years": [2024],
            "start_date": "20240101",
            "end_date": "20260805",
        },
        {
            "corp_code": "00000002",
            "stock_code": "0010V0",
            "corp_name": "메타누락",
            "refresh_years": [2025],
            "start_date": "20250101",
            "end_date": "20260805",
        },
    ]


def test_metadata_targets_reject_refresh_year_outside_selected_scope():
    """A malformed planner must not expand DART scope beyond selected years."""
    from kreports.maintenance.investor_core_backfill_runner import (
        InvestorCoreBackfillError,
    )
    from kreports.maintenance.investor_core_disclosure_backfill_runner import (
        metadata_targets_from_plan,
    )

    plan = {
        "selected_companies": [{
            "corp_code": "00000001", "stock_code": "000001",
            "corp_name": "범위오류", "source_ready": False,
            "selected_years": [2025],
            "invalid_annual_anchor_years": [],
            "missing_disclosure_metadata_years": [2024],
        }]
    }

    with pytest.raises(InvestorCoreBackfillError) as caught:
        metadata_targets_from_plan(plan, as_of_date=date(2026, 8, 5))

    assert caught.value.code == "invalid_planner_output"


@pytest.mark.parametrize(
    "selected_companies",
    [
        None,
        ["not-an-object"],
        [{
            "corp_code": "00000001", "stock_code": "000001",
            "corp_name": "잘못된연도", "source_ready": False,
            "selected_years": "2025", "invalid_annual_anchor_years": [],
            "missing_disclosure_metadata_years": [2025],
        }],
        [{
            "corp_code": "00000001", "stock_code": "000001",
            "corp_name": "잘못된코드", "source_ready": False,
            "selected_years": [2025], "invalid_annual_anchor_years": [],
            "missing_disclosure_metadata_years": [2025],
        }, {
            "corp_code": "00000001", "stock_code": "000001",
            "corp_name": "중복회사", "source_ready": False,
            "selected_years": [2025], "invalid_annual_anchor_years": [],
            "missing_disclosure_metadata_years": [2025],
        }],
    ],
)
def test_metadata_targets_reject_malformed_or_duplicate_planner_companies(
    selected_companies,
):
    """Malformed planner data must fail closed before constructing DART calls."""
    from kreports.maintenance.investor_core_backfill_runner import (
        InvestorCoreBackfillError,
    )
    from kreports.maintenance.investor_core_disclosure_backfill_runner import (
        metadata_targets_from_plan,
    )

    with pytest.raises(InvestorCoreBackfillError) as caught:
        metadata_targets_from_plan(
            {"selected_companies": selected_companies},
            as_of_date=date(2026, 8, 5),
        )

    assert caught.value.code in {"invalid_planner_output", "duplicate_planner_target"}


def test_metadata_targets_reject_as_of_before_required_business_year():
    """An inverted query window must not be sent to DART."""
    from kreports.maintenance.investor_core_backfill_runner import (
        InvestorCoreBackfillError,
    )
    from kreports.maintenance.investor_core_disclosure_backfill_runner import (
        metadata_targets_from_plan,
    )

    with pytest.raises(InvestorCoreBackfillError) as caught:
        metadata_targets_from_plan(_metadata_plan(), as_of_date=date(2024, 12, 31))

    assert caught.value.code == "invalid_as_of_date"


def test_metadata_validation_keeps_only_exact_target_annual_receipts():
    """Wrong-company, wrong-date, and nonannual rows must never repair provenance."""
    from kreports.maintenance.investor_core_disclosure_backfill_runner import (
        validated_annual_disclosures,
    )

    target = {
        "corp_code": "00000001",
        "refresh_years": [2025],
        "start_date": "20250101",
        "end_date": "20260805",
    }
    raw_items = [
        {
            "rcept_no": "20260331000001", "corp_code": "00000001",
            "corp_name": "대상회사", "rcept_dt": "20260331",
            "report_nm": "사업보고서 (2025.12)", "flr_nm": "대상회사",
        },
        {
            "rcept_no": "20260331000002", "corp_code": "99999999",
            "corp_name": "다른회사", "rcept_dt": "20260331",
            "report_nm": "사업보고서 (2025.12)", "flr_nm": "다른회사",
        },
        {
            "rcept_no": "20260330000003", "corp_code": "00000001",
            "corp_name": "대상회사", "rcept_dt": "20260331",
            "report_nm": "사업보고서 (2025.12)", "flr_nm": "대상회사",
        },
        {
            "rcept_no": "20260515000004", "corp_code": "00000001",
            "corp_name": "대상회사", "rcept_dt": "20260515",
            "report_nm": "분기보고서 (2026.03)", "flr_nm": "대상회사",
        },
        {
            "rcept_no": "20250331000005", "corp_code": "00000001",
            "corp_name": "대상회사", "rcept_dt": "20250331",
            "report_nm": "사업보고서 (2024.12)", "flr_nm": "대상회사",
        },
        {
            "rcept_no": " 20260331000006", "corp_code": "00000001 ",
            "corp_name": "대상회사", "rcept_dt": "20260331junk",
            "report_nm": "사업보고서 (2025.12)", "flr_nm": "대상회사",
        },
        {
            "rcept_no": "20270101000007", "corp_code": "00000001",
            "corp_name": "대상회사", "rcept_dt": "20270101",
            "report_nm": "사업보고서 (2025.12)", "flr_nm": "대상회사",
        },
        {
            "rcept_no": "20240101000008", "corp_code": "00000001",
            "corp_name": "대상회사", "rcept_dt": "20240101",
            "report_nm": "사업보고서 (2025.12)", "flr_nm": "대상회사",
        },
    ]

    rows, rejected_count = validated_annual_disclosures(target, raw_items)

    assert rows == [{
        "rcept_no": "20260331000001",
        "corp_code": "00000001",
        "corp_name": "대상회사",
        "disc_date": "2026-03-31",
        "disc_type": "A",
        "report_nm": "사업보고서 (2025.12)",
        "flr_nm": "대상회사",
        "bsns_year": 2025,
    }]
    assert rejected_count == 7


def test_metadata_upsert_repairs_same_company_but_rejects_receipt_collision():
    """A foreign-company receipt collision must remain untouched and explicit."""
    from kreports.maintenance.investor_core_disclosure_backfill_runner import (
        upsert_validated_disclosures,
    )

    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE disclosures ("
        "rcept_no TEXT PRIMARY KEY, corp_code TEXT NOT NULL, corp_name TEXT NOT NULL, "
        "disc_date TEXT NOT NULL, disc_type TEXT NOT NULL, report_nm TEXT NOT NULL, "
        "flr_nm TEXT, fetched_at TEXT NOT NULL)"
    )
    connection.executemany(
        "INSERT INTO disclosures VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                "20260331000001", "00000001", "대상회사", "2026-04-01", "A",
                "사업보고서 (2025.12)", "과거제출인", "2026-04-01T00:00:00",
            ),
            (
                "20260331000002", "99999999", "다른회사", "2026-03-31", "A",
                "사업보고서 (2025.12)", "다른회사", "2026-03-31T00:00:00",
            ),
        ],
    )
    rows = [
        {
            "rcept_no": "20260331000001", "corp_code": "00000001",
            "corp_name": "대상회사", "disc_date": "2026-03-31", "disc_type": "A",
            "report_nm": "[기재정정]사업보고서 (2025.12)", "flr_nm": "대상회사",
            "bsns_year": 2025,
        },
        {
            "rcept_no": "20260401000003", "corp_code": "00000001",
            "corp_name": "대상회사", "disc_date": "2026-04-01", "disc_type": "A",
            "report_nm": "사업보고서 (2025.12)", "flr_nm": "대상회사",
            "bsns_year": 2025,
        },
        {
            "rcept_no": "20260331000002", "corp_code": "00000001",
            "corp_name": "대상회사", "disc_date": "2026-03-31", "disc_type": "A",
            "report_nm": "사업보고서 (2025.12)", "flr_nm": "대상회사",
            "bsns_year": 2025,
        },
    ]

    result = upsert_validated_disclosures(
        connection,
        target_corp_code="00000001",
        rows=rows,
        fetched_at="2026-08-05T12:00:00+00:00",
    )

    assert result == {"inserted": 1, "updated": 1, "unchanged": 0, "conflicts": 1}
    repaired = dict(connection.execute(
        "SELECT * FROM disclosures WHERE rcept_no='20260331000001'"
    ).fetchone())
    assert repaired["corp_code"] == "00000001"
    assert repaired["disc_date"] == "2026-03-31"
    assert repaired["report_nm"] == "[기재정정]사업보고서 (2025.12)"
    assert repaired["fetched_at"] == "2026-08-05T12:00:00+00:00"
    foreign = dict(connection.execute(
        "SELECT * FROM disclosures WHERE rcept_no='20260331000002'"
    ).fetchone())
    assert foreign["corp_code"] == "99999999"
    assert connection.execute(
        "SELECT COUNT(*) FROM disclosures WHERE corp_code='00000001'"
    ).fetchone()[0] == 2


def test_metadata_upsert_rejects_cross_table_receipt_identity_conflict():
    """A receipt owned by another company's source document cannot be rebound."""
    from kreports.maintenance.investor_core_disclosure_backfill_runner import (
        upsert_validated_disclosures,
    )

    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE disclosures ("
        "rcept_no TEXT PRIMARY KEY, corp_code TEXT NOT NULL, corp_name TEXT NOT NULL, "
        "disc_date TEXT NOT NULL, disc_type TEXT NOT NULL, report_nm TEXT NOT NULL, "
        "flr_nm TEXT, fetched_at TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE source_documents ("
        "id INTEGER PRIMARY KEY, rcept_no TEXT NOT NULL, corp_code TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO source_documents (rcept_no, corp_code) VALUES (?, ?)",
        ("20260331000001", "99999999"),
    )

    result = upsert_validated_disclosures(
        connection,
        target_corp_code="00000001",
        rows=[{
            "rcept_no": "20260331000001", "corp_code": "00000001",
            "corp_name": "대상회사", "disc_date": "2026-03-31", "disc_type": "A",
            "report_nm": "사업보고서 (2025.12)", "flr_nm": "대상회사",
            "bsns_year": 2025,
        }],
        fetched_at="2026-08-05T12:00:00+00:00",
    )

    assert result == {"inserted": 0, "updated": 0, "unchanged": 0, "conflicts": 1}
    assert connection.execute("SELECT COUNT(*) FROM disclosures").fetchone()[0] == 0


def test_metadata_runner_dry_run_is_no_network_and_no_write(tmp_path, monkeypatch):
    """Dry-run must prove scope without touching DART, DB bytes, or sidecars."""
    from kreports.maintenance.investor_core_disclosure_backfill_runner import (
        run_investor_core_disclosure_backfill,
    )

    database = tmp_path / "metadata.db"
    _create_metadata_db(database)
    before = database.read_bytes()
    monkeypatch.setenv("DB_URL", f"sqlite:///{database}")

    def no_network(*args, **kwargs):
        raise AssertionError("dry-run attempted network")

    report = run_investor_core_disclosure_backfill(
        database,
        execute=False,
        as_of_date=date(2026, 8, 5),
        planner_fn=lambda *args, **kwargs: _metadata_plan(),
        fetch_fn=no_network,
        settings_obj=SimpleNamespace(
            db_url=f"sqlite:///{database}", dart_api_key="", max_retries=3,
        ),
    )

    assert report["completed"] is True
    assert report["dry_run"] is True
    assert report["as_of_date"] == "2026-08-05"
    assert report["target_count"] == 1
    assert report["target_samples"] == [{
        "corp_code": "00000002", "stock_code": "000002", "corp_name": "메타누락",
        "refresh_years": [2025], "start_date": "20250101", "end_date": "20260805",
    }]
    assert report["target_outcomes"]["counts"] == {"planned": 1}
    assert report["used_api_calls"] == 0
    assert report["db_sha256_before"] == report["db_sha256_after"]
    assert database.read_bytes() == before
    assert not (tmp_path / "metadata.db-wal").exists()
    assert not (tmp_path / "metadata.db-journal").exists()


def test_metadata_runner_execute_uses_budget_and_persists_only_valid_annual_row(
    tmp_path,
    monkeypatch,
):
    """A bounded real fetch path must produce durable before/after evidence."""
    from kreports.collector import fetcher
    from kreports.maintenance.investor_core_backfill_runner import _sha256_file
    from kreports.maintenance.investor_core_disclosure_backfill_runner import (
        run_investor_core_disclosure_backfill,
    )

    database = tmp_path / "metadata.db"
    _create_metadata_db(database)
    monkeypatch.setenv("DB_URL", f"sqlite:///{database}")
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    monkeypatch.setattr(fetcher.settings, "dart_api_key", "fixture-key")
    monkeypatch.setattr(fetcher.settings, "request_delay", 0)

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "000", "message": "정상", "total_count": 2,
                "list": [
                    {
                        "rcept_no": "20260331000001", "corp_code": "00000002",
                        "corp_name": "메타누락", "rcept_dt": "20260331",
                        "report_nm": "사업보고서 (2025.12)", "flr_nm": "메타누락",
                    },
                    {
                        "rcept_no": "20260515000002", "corp_code": "00000002",
                        "corp_name": "메타누락", "rcept_dt": "20260515",
                        "report_nm": "분기보고서 (2026.03)", "flr_nm": "메타누락",
                    },
                ],
            }

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(fetcher, "_get_client", Client)
    runner_settings = SimpleNamespace(
        db_url=f"sqlite:///{database}", dart_api_key="fixture-key", max_retries=3,
    )
    before_sha = _sha256_file(database)

    report = run_investor_core_disclosure_backfill(
        database,
        execute=True,
        expected_db_sha256=before_sha,
        max_api_calls=1,
        as_of_date=date(2026, 8, 5),
        planner_fn=lambda *args, **kwargs: _metadata_plan(),
        fetch_fn=fetcher.fetch_disclosure_list,
        disk_probe=lambda path: 20 * 1024**3,
        settings_obj=runner_settings,
    )

    assert report["completed"] is True
    assert report["dry_run"] is False
    assert report["used_api_calls"] == 1
    assert report["endpoint_call_counts"] == {"list.json": 1}
    assert report["target_outcomes"]["counts"] == {"repaired": 1}
    assert report["validation_counts"] == {"accepted": 1, "rejected": 1}
    assert report["write_counts"] == {
        "inserted": 1, "updated": 0, "unchanged": 0, "conflicts": 0,
    }
    assert report["db_sha256_before"] != report["db_sha256_after"]
    assert report["wal_checkpointed"] is True
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT rcept_no, corp_code, disc_date, report_nm FROM disclosures"
        ).fetchall() == [
            ("20260331000001", "00000002", "2026-03-31", "사업보고서 (2025.12)"),
        ]


def test_metadata_runner_rehashes_after_acquiring_writer_lock(tmp_path, monkeypatch):
    """A content change between preflight and lock acquisition must stop before DART."""
    from kreports.maintenance import investor_core_disclosure_backfill_runner as runner
    from kreports.maintenance.investor_core_backfill_runner import (
        InvestorCoreBackfillError,
        _sha256_file,
    )

    database = tmp_path / "metadata.db"
    _create_metadata_db(database)
    monkeypatch.setenv("DB_URL", f"sqlite:///{database}")
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")

    @contextmanager
    def mutating_guard(identity):
        del identity
        with sqlite3.connect(database) as connection:
            connection.execute(
                "INSERT INTO disclosures VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "20250101000099", "99999999", "외부변경", "2025-01-01", "A",
                    "사업보고서 (2024.12)", None, "2025-01-01T00:00:00",
                ),
            )
        yield

    monkeypatch.setattr(runner, "_exclusive_execution_guard", mutating_guard)
    settings_obj = SimpleNamespace(
        db_url=f"sqlite:///{database}", dart_api_key="fixture-key", max_retries=3,
    )

    with pytest.raises(InvestorCoreBackfillError) as caught:
        runner.run_investor_core_disclosure_backfill(
            database,
            execute=True,
            expected_db_sha256=_sha256_file(database),
            max_api_calls=1,
            as_of_date=date(2026, 8, 5),
            planner_fn=lambda *args, **kwargs: _metadata_plan(),
            fetch_fn=lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("network reached after database changed")
            ),
            disk_probe=lambda path: 20 * 1024**3,
            settings_obj=settings_obj,
        )

    assert caught.value.code == "database_changed_before_execution"


def test_metadata_runner_rolls_back_target_when_receipt_belongs_to_another_company(
    tmp_path,
    monkeypatch,
):
    """A collision discovered after an insert must roll back the whole target."""
    from kreports.maintenance import investor_core_disclosure_backfill_runner as runner
    from kreports.maintenance.investor_core_backfill_runner import _sha256_file

    database = tmp_path / "metadata.db"
    _create_metadata_db(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO disclosures VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "20260401000002", "99999999", "다른회사", "2026-04-01", "A",
                "사업보고서 (2025.12)", "다른회사", "2026-04-01T00:00:00",
            ),
        )
    monkeypatch.setenv("DB_URL", f"sqlite:///{database}")
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    settings_obj = SimpleNamespace(
        db_url=f"sqlite:///{database}", dart_api_key="fixture-key", max_retries=3,
    )
    raw_items = [
        {
            "rcept_no": "20260331000001", "corp_code": "00000002",
            "corp_name": "메타누락", "rcept_dt": "20260331",
            "report_nm": "사업보고서 (2025.12)", "flr_nm": "메타누락",
        },
        {
            "rcept_no": "20260401000002", "corp_code": "00000002",
            "corp_name": "메타누락", "rcept_dt": "20260401",
            "report_nm": "[기재정정]사업보고서 (2025.12)", "flr_nm": "메타누락",
        },
    ]

    checkpointed = False

    def checkpoint(identity):
        nonlocal checkpointed
        del identity
        checkpointed = True
        return True

    monkeypatch.setattr(runner, "_checkpoint_wal", checkpoint)
    report = runner.run_investor_core_disclosure_backfill(
        database,
        execute=True,
        expected_db_sha256=_sha256_file(database),
        max_api_calls=1,
        as_of_date=date(2026, 8, 5),
        planner_fn=lambda *args, **kwargs: _metadata_plan(),
        fetch_fn=lambda *args, **kwargs: raw_items,
        disk_probe=lambda path: 20 * 1024**3,
        settings_obj=settings_obj,
    )

    assert report["completed"] is False
    assert report["stop_reason"] == "disclosure_receipt_identity_conflict"
    assert report["target_outcomes"]["counts"] == {"stopped": 1}
    assert report["write_counts"] == {
        "inserted": 0, "updated": 0, "unchanged": 0, "conflicts": 1,
    }
    assert checkpointed is True
    assert report["wal_checkpointed"] is True
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT rcept_no, corp_code FROM disclosures ORDER BY rcept_no"
        ).fetchall() == [("20260401000002", "99999999")]


def test_metadata_runner_holds_writer_lock_through_checkpoint_and_post_evidence(
    tmp_path,
    monkeypatch,
):
    """Durability and post-state evidence must belong to the locked execution."""
    from kreports.maintenance import investor_core_disclosure_backfill_runner as runner

    database = tmp_path / "metadata.db"
    _create_metadata_db(database)
    monkeypatch.setenv("DB_URL", f"sqlite:///{database}")
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    settings_obj = SimpleNamespace(
        db_url=f"sqlite:///{database}", dart_api_key="fixture-key", max_retries=3,
    )
    lock_active = False
    original_counts = runner._disclosure_row_counts
    count_calls = 0

    @contextmanager
    def tracked_guard(identity):
        nonlocal lock_active
        del identity
        lock_active = True
        try:
            yield
        finally:
            lock_active = False

    def tracked_counts(path, targets):
        nonlocal count_calls
        count_calls += 1
        if count_calls == 2:
            assert lock_active is True
        return original_counts(path, targets)

    def tracked_checkpoint(identity):
        del identity
        assert lock_active is True
        return True

    monkeypatch.setattr(runner, "_exclusive_execution_guard", tracked_guard)
    monkeypatch.setattr(runner, "_disclosure_row_counts", tracked_counts)
    monkeypatch.setattr(runner, "_checkpoint_wal", tracked_checkpoint)

    report = runner.run_investor_core_disclosure_backfill(
        database,
        execute=True,
        expected_db_sha256=runner._sha256_file(database),
        max_api_calls=1,
        as_of_date=date(2026, 8, 5),
        planner_fn=lambda *args, **kwargs: _metadata_plan(),
        fetch_fn=lambda *args, **kwargs: [{
            "rcept_no": "20260331000001", "corp_code": "00000002",
            "corp_name": "메타누락", "rcept_dt": "20260331",
            "report_nm": "사업보고서 (2025.12)", "flr_nm": "메타누락",
        }],
        disk_probe=lambda path: 20 * 1024**3,
        settings_obj=settings_obj,
    )

    assert report["completed"] is True
    assert count_calls == 2


def test_metadata_runner_returns_report_when_post_evidence_fails(
    tmp_path,
    monkeypatch,
):
    """Evidence failure after a write must not discard bounded-run outcomes."""
    from kreports.maintenance import investor_core_disclosure_backfill_runner as runner

    database = tmp_path / "metadata.db"
    _create_metadata_db(database)
    monkeypatch.setenv("DB_URL", f"sqlite:///{database}")
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    settings_obj = SimpleNamespace(
        db_url=f"sqlite:///{database}", dart_api_key="fixture-key", max_retries=3,
    )
    original_counts = runner._disclosure_row_counts
    count_calls = 0

    def failing_post_counts(path, targets):
        nonlocal count_calls
        count_calls += 1
        if count_calls == 2:
            raise sqlite3.OperationalError("fixture evidence failure")
        return original_counts(path, targets)

    monkeypatch.setattr(runner, "_disclosure_row_counts", failing_post_counts)
    report = runner.run_investor_core_disclosure_backfill(
        database,
        execute=True,
        expected_db_sha256=runner._sha256_file(database),
        max_api_calls=1,
        as_of_date=date(2026, 8, 5),
        planner_fn=lambda *args, **kwargs: _metadata_plan(),
        fetch_fn=lambda *args, **kwargs: [{
            "rcept_no": "20260331000001", "corp_code": "00000002",
            "corp_name": "메타누락", "rcept_dt": "20260331",
            "report_nm": "사업보고서 (2025.12)", "flr_nm": "메타누락",
        }],
        disk_probe=lambda path: 20 * 1024**3,
        settings_obj=settings_obj,
    )

    assert report["completed"] is False
    assert report["stop_reason"] == "evidence_collection_failed"
    assert report["target_outcomes"]["counts"] == {"repaired": 1}
    assert report["relevant_row_counts"]["after"] is None


def test_metadata_runner_reports_malformed_fetch_payload_without_escaping(
    tmp_path,
    monkeypatch,
):
    """A non-list DART payload is a stable bounded failure, never a traceback."""
    from kreports.maintenance import investor_core_disclosure_backfill_runner as runner

    database = tmp_path / "metadata.db"
    _create_metadata_db(database)
    monkeypatch.setenv("DB_URL", f"sqlite:///{database}")
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    settings_obj = SimpleNamespace(
        db_url=f"sqlite:///{database}", dart_api_key="fixture-key", max_retries=3,
    )

    report = runner.run_investor_core_disclosure_backfill(
        database,
        execute=True,
        expected_db_sha256=runner._sha256_file(database),
        max_api_calls=1,
        as_of_date=date(2026, 8, 5),
        planner_fn=lambda *args, **kwargs: _metadata_plan(),
        fetch_fn=lambda *args, **kwargs: {"list": "not-a-list"},
        disk_probe=lambda path: 20 * 1024**3,
        settings_obj=settings_obj,
    )

    assert report["completed"] is False
    assert report["stop_reason"] == "collector_failure"
    assert report["stop_message"] == "bounded collector failed"
    assert report["target_outcomes"]["counts"] == {"stopped": 1}


@pytest.mark.parametrize(
    ("post_probe", "expected_reason"),
    [
        (9 * 1024**3, "insufficient_free_space"),
        (OSError("fixture probe failure"), "free_space_probe_failed"),
    ],
)
def test_metadata_runner_checks_disk_after_no_data_target(
    tmp_path,
    monkeypatch,
    post_probe,
    expected_reason,
):
    """Even a no-data request may consume disk and must pass the common tail gate."""
    from kreports.maintenance import investor_core_disclosure_backfill_runner as runner

    database = tmp_path / "metadata.db"
    _create_metadata_db(database)
    monkeypatch.setenv("DB_URL", f"sqlite:///{database}")
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    settings_obj = SimpleNamespace(
        db_url=f"sqlite:///{database}", dart_api_key="fixture-key", max_retries=3,
    )
    probes = iter([20 * 1024**3, 20 * 1024**3, post_probe, 20 * 1024**3])

    def disk_probe(path):
        del path
        result = next(probes)
        if isinstance(result, Exception):
            raise result
        return result

    report = runner.run_investor_core_disclosure_backfill(
        database,
        execute=True,
        expected_db_sha256=runner._sha256_file(database),
        max_api_calls=1,
        as_of_date=date(2026, 8, 5),
        planner_fn=lambda *args, **kwargs: _metadata_plan(),
        fetch_fn=lambda *args, **kwargs: [],
        disk_probe=disk_probe,
        settings_obj=settings_obj,
    )

    assert report["completed"] is False
    assert report["stop_reason"] == expected_reason
    assert report["target_outcomes"]["counts"] == {"not_found": 1}


def test_metadata_runner_fails_closed_when_initial_disk_probe_fails(
    tmp_path,
    monkeypatch,
):
    """Preflight disk evidence must use a stable safety error."""
    from kreports.maintenance import investor_core_disclosure_backfill_runner as runner
    from kreports.maintenance.investor_core_backfill_runner import (
        InvestorCoreBackfillError,
    )

    database = tmp_path / "metadata.db"
    _create_metadata_db(database)
    monkeypatch.setenv("DB_URL", f"sqlite:///{database}")

    with pytest.raises(InvestorCoreBackfillError) as caught:
        runner.run_investor_core_disclosure_backfill(
            database,
            execute=False,
            as_of_date=date(2026, 8, 5),
            planner_fn=lambda *args, **kwargs: _metadata_plan(),
            disk_probe=lambda path: (_ for _ in ()).throw(OSError("fixture")),
            settings_obj=SimpleNamespace(
                db_url=f"sqlite:///{database}", dart_api_key="", max_retries=3,
            ),
        )

    assert caught.value.code == "free_space_probe_failed"


def test_metadata_runner_rejects_invalid_planner_summary(tmp_path, monkeypatch):
    """Coverage evidence must be a validated planner contract, not arbitrary JSON."""
    from kreports.maintenance import investor_core_disclosure_backfill_runner as runner
    from kreports.maintenance.investor_core_backfill_runner import (
        InvestorCoreBackfillError,
    )

    database = tmp_path / "metadata.db"
    _create_metadata_db(database)
    monkeypatch.setenv("DB_URL", f"sqlite:///{database}")
    malformed = _metadata_plan()
    malformed["shortfall"] = "15"

    with pytest.raises(InvestorCoreBackfillError) as caught:
        runner.run_investor_core_disclosure_backfill(
            database,
            execute=False,
            as_of_date=date(2026, 8, 5),
            planner_fn=lambda *args, **kwargs: malformed,
            disk_probe=lambda path: 20 * 1024**3,
            settings_obj=SimpleNamespace(
                db_url=f"sqlite:///{database}", dart_api_key="", max_retries=3,
            ),
        )

    assert caught.value.code == "invalid_planner_output"
