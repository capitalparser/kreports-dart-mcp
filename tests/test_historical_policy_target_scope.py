import pytest
from sqlalchemy import text
from typer.testing import CliRunner

from kreports.cli.main import (
    _select_policy_targets,
    app,
)
from kreports.db.engine import get_session
from kreports.db.models import Company


def _create_year_membership_table() -> None:
    """Create the integration-owned historical-membership contract for this test."""
    with get_session() as session:
        session.execute(
            text(
                "CREATE TABLE company_year_listing_memberships ("
                "corp_code TEXT NOT NULL, "
                "bsns_year INTEGER NOT NULL, "
                "market TEXT NOT NULL, "
                "status TEXT NOT NULL"
                ")"
            )
        )
        session.commit()


def _seed_company(*, corp_code: str, stock_code: str, market: str | None) -> None:
    with get_session() as session:
        session.add(
            Company(
                corp_code=corp_code,
                stock_code=stock_code,
                corp_name=corp_code,
                market=market,
                induty_code="264",
            )
        )


def _seed_membership(*, corp_code: str, year: int, market: str, status: str = "verified") -> None:
    with get_session() as session:
        session.execute(
            text(
                "INSERT INTO company_year_listing_memberships "
                "(corp_code, bsns_year, market, status) "
                "VALUES (:corp_code, :year, :market, :status)"
            ),
            {"corp_code": corp_code, "year": year, "market": market, "status": status},
        )
        session.commit()


def test_historical_membership_selects_delisted_company_and_excludes_future_survivor(temp_engine):
    """Catches a selector that falls back to the current company master for 2021."""
    _create_year_membership_table()
    _seed_company(corp_code="00000001", stock_code="000001", market=None)
    _seed_company(corp_code="00000002", stock_code="000002", market="KOSPI")
    _seed_company(corp_code="00000003", stock_code="000003", market="KOSPI")

    _seed_membership(corp_code="00000001", year=2021, market="KOSPI")
    # A current survivor that listed after 2021 has no 2021 membership row.
    _seed_membership(corp_code="00000002", year=2022, market="KOSPI")
    # A transfer can have duplicated raw records; a company-year target must
    # still be emitted once when selecting the all-market population.
    _seed_membership(corp_code="00000003", year=2021, market="KOSDAQ")
    _seed_membership(corp_code="00000003", year=2021, market="KOSPI")

    targets = _select_policy_targets(
        year=2021,
        fs_div="CFS",
        market=None,
        limit=None,
        missing_only=False,
    )

    assert targets == [
        ("00000001", 2021, "CFS"),
        ("00000003", 2021, "CFS"),
    ]

    kospi_targets = _select_policy_targets(
        year=2021,
        fs_div="CFS",
        market="KOSPI",
        limit=None,
        missing_only=False,
    )
    assert kospi_targets == [
        ("00000001", 2021, "CFS"),
        ("00000003", 2021, "CFS"),
    ]


@pytest.mark.parametrize("requested_year", [2021, 2026])
def test_membership_scope_fails_closed_without_membership_evidence(temp_engine, requested_year):
    """Catches a survivor fallback for historical, current, and future requests."""
    _create_year_membership_table()
    _seed_company(corp_code="00000001", stock_code="000001", market="KOSPI")
    _seed_membership(corp_code="00000001", year=2022, market="KOSPI")

    with pytest.raises(RuntimeError, match="historical year-end membership evidence"):
        _select_policy_targets(
            year=requested_year,
            fs_div="CFS",
            market="KOSPI",
            limit=None,
            missing_only=False,
        )


def test_collect_policies_dry_run_reports_historical_population_source_and_counts(temp_engine):
    """Catches dry-run output that hides which historical population supplied targets."""
    _create_year_membership_table()
    _seed_company(corp_code="00000001", stock_code="000001", market=None)
    _seed_company(corp_code="00000002", stock_code="000002", market="KOSDAQ")
    _seed_membership(corp_code="00000001", year=2021, market="KOSPI")
    _seed_membership(corp_code="00000002", year=2021, market="KOSDAQ")

    result = CliRunner().invoke(
        app,
        ["collect-policies", "--all", "--year", "2021", "--dry-run"],
    )

    assert result.exit_code == 0
    assert "population_source=historical_year_end_membership" in result.output
    assert "population_market_counts=KOSDAQ:1,KOSPI:1" in result.output
    assert "population_targets=2" in result.output
    assert "targets=2" in result.output


def test_collect_policies_dry_run_reports_clear_error_when_membership_evidence_is_absent(temp_engine):
    """Catches an --all dry-run that misrepresents a survivor cohort as full history."""
    _seed_company(corp_code="00000001", stock_code="000001", market="KOSPI")

    result = CliRunner().invoke(
        app,
        ["collect-policies", "--all", "--year", "2021", "--dry-run"],
    )

    assert result.exit_code == 1
    assert "historical year-end membership evidence" in result.output
