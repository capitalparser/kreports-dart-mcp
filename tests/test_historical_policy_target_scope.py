from datetime import date

import pytest
from typer.testing import CliRunner

from kreports.cli.main import (
    _select_policy_targets,
    app,
)
from kreports.db.engine import get_session
from kreports.db.models import Company, CompanyYearListingMembership


def _create_year_membership_table() -> None:
    """The shared fixture creates the integration-owned ORM contract."""


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
        stock_code = session.get(Company, corp_code).stock_code
        session.add(
            CompanyYearListingMembership(
                corp_code=corp_code,
                stock_code=stock_code,
                bsns_year=year,
                market=market,
                status=status,
                evidence_basis=(
                    "current_open_interval" if status == "verified" else "source_gap"
                ),
                as_of=date(2026, 8, 10),
                manifest_checksum="a" * 64,
                manifest_storage_uri="file:///tmp/test-manifest.json",
                manifest_size_bytes=2,
                manifest_raw_receipt_count=1,
                normalized_checksum="b" * 64,
                normalized_storage_uri="file:///tmp/test-memberships.csv",
                normalized_size_bytes=2,
                transformation_version="krx-year-end-listing-membership-v1",
                source_row_no=int(corp_code) * 10 + year,
            )
        )


def test_historical_membership_selects_delisted_company_and_excludes_future_survivor(temp_engine):
    """Catches a selector that falls back to the current company master for 2021."""
    _create_year_membership_table()
    _seed_company(corp_code="00000001", stock_code="000001", market=None)
    _seed_company(corp_code="00000002", stock_code="000002", market="KOSPI")
    _seed_company(corp_code="00000003", stock_code="000003", market="KOSPI")

    _seed_membership(corp_code="00000001", year=2021, market="KOSPI")
    # A current survivor that listed after 2021 has no 2021 membership row.
    _seed_membership(corp_code="00000002", year=2022, market="KOSPI")
    # A market transfer is represented by exactly one year-end market.
    _seed_membership(corp_code="00000003", year=2021, market="KOSDAQ")

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
