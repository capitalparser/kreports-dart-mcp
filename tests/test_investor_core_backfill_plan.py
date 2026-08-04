"""Read-only planning for the investor-core three-year release gate."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner


def _create_plan_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE companies (
                corp_code TEXT PRIMARY KEY,
                stock_code TEXT,
                corp_name TEXT NOT NULL,
                market TEXT
            );
            CREATE TABLE company_year_quality (
                corp_code TEXT NOT NULL,
                bsns_year INTEGER NOT NULL,
                financial_core_status TEXT NOT NULL,
                investor_grade TEXT NOT NULL,
                quality_version TEXT NOT NULL,
                evidence_summary_json TEXT NOT NULL,
                PRIMARY KEY (corp_code, bsns_year)
            );
            CREATE TABLE disclosures (
                rcept_no TEXT PRIMARY KEY,
                corp_code TEXT NOT NULL,
                corp_name TEXT NOT NULL,
                disc_date TEXT NOT NULL,
                disc_type TEXT NOT NULL,
                report_nm TEXT NOT NULL
            );
            """
        )


def _proof(years: tuple[int, ...], *, corp_code: str, corrected: bool = False) -> str:
    proven_years = []
    for year in years:
        report_nm = f"사업보고서 ({year}.12)"
        if corrected:
            report_nm = f"[기재정정]{report_nm}"
        proven_years.append(
            {
                "bsns_year": year,
                "fs_div": "CFS",
                "rcept_no": f"{year + 1}0331{int(corp_code):06d}",
                "report_nm": report_nm,
                "metric_digest": "a" * 64,
            }
        )
    return json.dumps(
        {
            "statuses": {
                "financial_core": "available",
                "auditor": "available",
                "audit_fee": "available",
                "policy": "full_body",
                "kam": "full_body",
                "audit_procedure": "available",
                "group_audit": "missing",
            },
            "grades": {
                "investor_core": "D",
                "auditor_full": "A",
                "group_audit": "D",
            },
            "blockers": [],
            "quality_version": "v2",
            "financial_core_proof": {
                "window_start_year": 2021,
                "window_end_year": 2025,
                "proven_years": proven_years,
            },
        },
        ensure_ascii=False,
    )


def _add_company(
    path: Path,
    *,
    corp_code: str,
    grade: str,
    status: str,
    proven_years: tuple[int, ...] = (),
) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO companies VALUES (?, ?, ?, 'KOSPI')",
            (corp_code, corp_code[-6:], f"회사-{corp_code}"),
        )
        connection.execute(
            "INSERT INTO company_year_quality VALUES (?, 2025, ?, ?, 'v2', ?)",
            (corp_code, status, grade, _proof(proven_years, corp_code=corp_code)),
        )


def _add_annual(
    path: Path,
    *,
    corp_code: str,
    year: int,
    corrected: bool = False,
    receipt: str | None = None,
    disc_date: date | None = None,
    report_nm: str | None = None,
) -> None:
    report_nm = report_nm or f"사업보고서 ({year}.12)"
    if corrected and report_nm == f"사업보고서 ({year}.12)":
        report_nm = f"[기재정정]{report_nm}"
    receipt = receipt or f"{year + 1}0331{int(corp_code):06d}"
    disc_date = disc_date or date(year + 1, 3, 31)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO disclosures VALUES (?, ?, ?, ?, 'A', ?)",
            (receipt, corp_code, f"회사-{corp_code}", disc_date.isoformat(), report_nm),
        )


def test_plan_uses_exact_ceiling_threshold_math(tmp_path):
    from kreports.maintenance.investor_core_backfill_plan import (
        plan_investor_core_backfill,
    )

    database = tmp_path / "planner.db"
    _create_plan_database(database)
    for corp_code in ("000001", "000002", "000003"):
        _add_company(
            database,
            corp_code=corp_code,
            grade="B",
            status="available",
        )

    plan = plan_investor_core_backfill(database, threshold_pct=66.67)

    assert plan["denominator"] == 3
    assert plan["numerator"] == 3
    assert plan["target_numerator"] == 3
    assert plan["shortfall"] == 0


@pytest.mark.parametrize("threshold_pct", [0, 100.01, float("nan")])
def test_plan_rejects_thresholds_outside_the_closed_release_range(
    tmp_path,
    threshold_pct,
):
    from kreports.maintenance.investor_core_backfill_plan import (
        plan_investor_core_backfill,
    )

    database = tmp_path / "planner.db"
    _create_plan_database(database)

    with pytest.raises(ValueError, match="threshold_pct"):
        plan_investor_core_backfill(database, threshold_pct=threshold_pct)


def test_plan_repairs_b_and_sizes_d_effort_from_valid_proven_years(tmp_path):
    from kreports.maintenance.investor_core_backfill_plan import (
        plan_investor_core_backfill,
    )

    database = tmp_path / "planner.db"
    _create_plan_database(database)
    _add_company(
        database,
        corp_code="000001",
        grade="B",
        status="missing",
        proven_years=(2021, 2022, 2023, 2024),
    )
    _add_company(
        database,
        corp_code="000002",
        grade="D",
        status="available",
        proven_years=(2023, 2024),
    )
    _add_company(
        database,
        corp_code="000003",
        grade="D",
        status="available",
        proven_years=(2023,),
    )
    _add_company(
        database,
        corp_code="000004",
        grade="D",
        status="missing",
    )
    for corp_code, years in {
        "000001": (2021, 2022, 2023, 2024, 2025),
        "000002": (2023, 2024, 2025),
        "000003": (2023, 2024, 2025),
        "000004": (2023, 2024, 2025),
    }.items():
        for year in years:
            _add_annual(database, corp_code=corp_code, year=year)

    plan = plan_investor_core_backfill(database, threshold_pct=100)

    assert [row["corp_code"] for row in plan["selected_companies"]] == [
        "000001",
        "000002",
        "000003",
        "000004",
    ]
    selected = {row["corp_code"]: row for row in plan["selected_companies"]}
    assert selected["000001"]["required_successful_year_count"] == 1
    assert selected["000001"]["selected_years"] == [2025]
    assert selected["000002"]["required_successful_year_count"] == 1
    assert selected["000002"]["selected_years"] == [2025]
    assert selected["000003"]["required_successful_year_count"] == 2
    assert selected["000003"]["selected_years"] == [2025, 2024]
    assert selected["000004"]["required_successful_year_count"] == 3
    assert selected["000004"]["selected_years"] == [2025, 2024, 2023]
    assert plan["selected_successful_company_year_request_count"] == 7


def test_plan_accepts_corrected_annual_names_and_rejects_bad_proof_rows(tmp_path):
    from kreports.maintenance.investor_core_backfill_plan import (
        plan_investor_core_backfill,
    )

    database = tmp_path / "planner.db"
    _create_plan_database(database)
    _add_company(
        database,
        corp_code="000001",
        grade="D",
        status="available",
        proven_years=(2024,),
    )
    _add_annual(database, corp_code="000001", year=2024, corrected=True)
    _add_annual(database, corp_code="000001", year=2025)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE company_year_quality SET evidence_summary_json=? WHERE corp_code='000001'",
            (_proof((2024,), corp_code="000001", corrected=True),),
        )
    _add_company(
        database,
        corp_code="000002",
        grade="D",
        status="available",
        proven_years=(2024,),
    )
    _add_annual(
        database,
        corp_code="000002",
        year=2024,
        receipt="20250330000002",
        disc_date=date(2025, 3, 31),
    )
    _add_company(database, corp_code="000003", grade="D", status="available")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE company_year_quality SET evidence_summary_json='not json' WHERE corp_code='000003'"
        )

    plan = plan_investor_core_backfill(database, threshold_pct=100)

    selected = {row["corp_code"]: row for row in plan["selected_companies"]}
    assert selected["000001"]["proven_years"] == [2024]
    assert selected["000002"]["proven_years"] == []
    assert selected["000003"]["proven_years"] == []
    assert plan["rejected_proof_row_count"] == 2
    assert {item["reason"] for item in plan["rejected_proof_diagnostics"]} == {
        "missing_or_invalid_annual_anchor",
        "invalid_quality_evidence_summary",
    }


@pytest.mark.parametrize("mutation", ["v1", "duplicate", "unsorted", "extra_key"])
def test_plan_fails_closed_when_any_quality_summary_contract_rule_is_broken(
    tmp_path,
    mutation,
):
    from kreports.maintenance.investor_core_backfill_plan import (
        plan_investor_core_backfill,
    )

    database = tmp_path / "planner.db"
    _create_plan_database(database)
    _add_company(
        database,
        corp_code="000001",
        grade="D",
        status="available",
        proven_years=(2023, 2024),
    )
    for year in (2023, 2024, 2025):
        _add_annual(database, corp_code="000001", year=year)
    with sqlite3.connect(database) as connection:
        raw_summary = json.loads(_proof((2023, 2024), corp_code="000001"))
        if mutation == "v1":
            connection.execute(
                "UPDATE company_year_quality SET quality_version='v1' WHERE corp_code='000001'"
            )
        elif mutation == "duplicate":
            raw_summary["financial_core_proof"]["proven_years"].append(
                dict(raw_summary["financial_core_proof"]["proven_years"][1])
            )
        elif mutation == "unsorted":
            raw_summary["financial_core_proof"]["proven_years"].reverse()
        else:
            raw_summary["unexpected"] = True
        if mutation != "v1":
            connection.execute(
                "UPDATE company_year_quality SET evidence_summary_json=? WHERE corp_code='000001'",
                (json.dumps(raw_summary, ensure_ascii=False),),
            )

    plan = plan_investor_core_backfill(database, threshold_pct=100)

    selected = plan["selected_companies"][0]
    assert selected["proven_years"] == []
    assert selected["required_successful_year_count"] == 3
    assert plan["rejected_proof_diagnostics"] == [
        {
            "corp_code": "000001",
            "proof_index": None,
            "reason": "invalid_quality_evidence_summary",
        }
    ]


def test_plan_uses_shared_annual_identity_boundary_for_proof_and_anchor(tmp_path):
    from kreports.maintenance.investor_core_backfill_plan import (
        plan_investor_core_backfill,
    )

    database = tmp_path / "planner.db"
    _create_plan_database(database)
    _add_company(
        database,
        corp_code="000001",
        grade="D",
        status="available",
        proven_years=(2024,),
    )
    shared_valid_name = "[기재정정] 사업보고서 (2024.1) 추가설명"
    _add_annual(
        database,
        corp_code="000001",
        year=2024,
        report_nm=shared_valid_name,
    )
    _add_annual(database, corp_code="000001", year=2025)
    with sqlite3.connect(database) as connection:
        summary = json.loads(_proof((2024,), corp_code="000001"))
        summary["financial_core_proof"]["proven_years"][0]["report_nm"] = shared_valid_name
        connection.execute(
            "UPDATE company_year_quality SET evidence_summary_json=? WHERE corp_code='000001'",
            (json.dumps(summary, ensure_ascii=False),),
        )

    plan = plan_investor_core_backfill(database, threshold_pct=100)

    assert plan["selected_companies"][0]["proven_years"] == [2024]


def test_plan_prioritizes_lower_effort_before_source_readiness(tmp_path):
    from kreports.maintenance.investor_core_backfill_plan import (
        plan_investor_core_backfill,
    )

    database = tmp_path / "planner.db"
    _create_plan_database(database)
    _add_company(database, corp_code="000000", grade="A", status="available")
    _add_company(database, corp_code="000001", grade="D", status="available")
    _add_company(
        database,
        corp_code="000002",
        grade="D",
        status="available",
        proven_years=(2023, 2024),
    )
    for year in (2023, 2024, 2025):
        _add_annual(database, corp_code="000001", year=year)
    for year in (2023, 2024):
        _add_annual(database, corp_code="000002", year=year)

    plan = plan_investor_core_backfill(database, threshold_pct=50)

    assert plan["shortfall"] == 1
    assert [row["corp_code"] for row in plan["selected_companies"]] == ["000002"]
    assert plan["selected_successful_company_year_request_count"] == 1
    assert plan["selected_source_ready_count"] == 0
    assert plan["selected_needing_disclosure_metadata_count"] == 1


def test_plan_reports_insufficient_candidates_and_keeps_database_immutable(tmp_path):
    from kreports.maintenance.investor_core_backfill_plan import (
        plan_investor_core_backfill,
    )

    database = tmp_path / "planner.db"
    _create_plan_database(database)
    _add_company(
        database,
        corp_code="000001",
        grade="D",
        status="available",
        proven_years=(2021, 2022, 2023, 2024, 2025),
    )
    for year in range(2021, 2026):
        _add_annual(database, corp_code="000001", year=year)
    before = hashlib.sha256(database.read_bytes()).hexdigest()

    plan = plan_investor_core_backfill(database, threshold_pct=100)

    assert plan["shortfall"] == 1
    assert plan["selected_candidate_count"] == 0
    assert plan["unselected_candidate_count"] == 1
    assert plan["unfillable_shortfall"] == 1
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before


def test_cli_emits_stable_json_for_an_explicit_database(tmp_path):
    from kreports.cli.main import app

    database = tmp_path / "planner.db"
    _create_plan_database(database)
    _add_company(database, corp_code="000001", grade="B", status="available")

    result = CliRunner().invoke(
        app,
        ["plan-investor-core-backfill", "--db", str(database), "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert {
        "coverage_year": 2025,
        "denominator": 1,
        "numerator": 1,
        "selected_candidate_count": 0,
        "shortfall": 0,
        "target_numerator": 1,
        "threshold_pct": 95.0,
    }.items() <= payload.items()


def test_cli_reports_missing_schema_without_a_traceback_even_for_json(tmp_path):
    from kreports.cli.main import app

    database = tmp_path / "empty.db"
    with sqlite3.connect(database):
        pass

    result = CliRunner().invoke(
        app,
        ["plan-investor-core-backfill", "--db", str(database), "--json"],
    )

    assert result.exit_code == 2
    assert isinstance(result.exception, SystemExit)
    assert json.loads(result.output) == {
        "error": "investor_core_backfill_plan_unavailable"
    }
    assert "Traceback" not in result.output
