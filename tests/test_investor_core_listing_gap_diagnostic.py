"""Read-only, non-activated pre-listing gap diagnostics."""
from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest
from typer.testing import CliRunner


def _create_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE companies (corp_code TEXT PRIMARY KEY, stock_code TEXT, corp_name TEXT, market TEXT)"
        )
        connection.executemany(
            "INSERT INTO companies VALUES (?, ?, ?, ?)",
            [
                ("00000001", "000001", "One", "KOSPI"),
                ("00000002", "000002", "Two", "KOSDAQ"),
            ],
        )


def _listing_csv() -> bytes:
    return (
        b"corp_code,stock_code,market,listed_from,listed_to,status\n"
        b"00000001,000001,KOSPI,2024-01-02,,verified\n"
        b"00000002,000002,KOSDAQ,2024-01-02,,verified\n"
    )


def _company_snapshot_checksum() -> str:
    return hashlib.sha256(
        b"00000001,000001,KOSPI\n00000002,000002,KOSDAQ\n"
    ).hexdigest()


def _plan() -> dict[str, object]:
    return {
        "coverage_year": 2025,
        "threshold_pct": 100.0,
        "denominator": 2,
        "numerator": 0,
        "target_numerator": 2,
        "shortfall": 2,
        "selected_companies": [
            {
                "corp_code": "00000001", "stock_code": "000001", "corp_name": "One",
                "selected_years": [2025, 2024, 2023],
                "annual_filing_anchors": [{"bsns_year": 2025, "rcept_no": "20260331000001", "disc_date": "2026-03-31", "report_nm": "사업보고서 (2025.12)"}],
                "invalid_annual_anchor_years": [2024],
                "missing_disclosure_metadata_years": [2023],
                "source_ready": False,
            },
            {
                "corp_code": "00000002", "stock_code": "000002", "corp_name": "Two",
                "selected_years": [2023],
                "annual_filing_anchors": [],
                "invalid_annual_anchor_years": [],
                "missing_disclosure_metadata_years": [2023],
                "source_ready": False,
            },
        ],
    }


def _diagnose(tmp_path: Path, monkeypatch):
    from kreports.maintenance import investor_core_listing_gap_diagnostic as diagnostic

    database = tmp_path / "planner.sqlite"
    _create_database(database)
    listing = tmp_path / "listing.csv"
    payload = _listing_csv()
    listing.write_bytes(payload)
    monkeypatch.setattr(diagnostic, "plan_investor_core_backfill", lambda *_args, **_kwargs: _plan())
    return diagnostic, database, listing, payload


def test_holds_only_true_missing_targets_strictly_before_verified_listing_and_keeps_release_semantics(tmp_path, monkeypatch):
    diagnostic, database, listing, payload = _diagnose(tmp_path, monkeypatch)

    report = diagnostic.diagnose_investor_core_listing_gaps(
        database,
        listing_csv=listing,
        expected_listing_sha256=hashlib.sha256(payload).hexdigest(),
        listing_as_of=date(2026, 8, 5),
        expected_company_snapshot_sha256=_company_snapshot_checksum(),
        coverage_year=2025,
        threshold_pct=100,
    )

    assert report["limitations"] == ["diagnostic_only_not_activated"]
    assert report["planner"] == {
        "coverage_year": 2025, "threshold_pct": 100.0, "denominator": 2,
        "numerator": 0, "target_numerator": 2, "shortfall": 2,
    }
    assert report["valid_annual_anchor_company_year_count"] == 1
    assert report["invalid_annual_anchor_company_year_count"] == 1
    assert report["missing_disclosure_metadata_company_year_count"] == 2
    assert report["held_pre_listing_true_missing_company_years"] == [
        {"corp_code": "00000001", "stock_code": "000001", "bsns_year": 2023, "reason": "verified_listing_after_target_year"},
        {"corp_code": "00000002", "stock_code": "000002", "bsns_year": 2023, "reason": "verified_listing_after_target_year"},
    ]
    assert report["remaining_company_year_count"] == 2
    assert report["zero_remaining_target_company_count"] == 1
    assert report["diagnostic_adjusted_numerator"] == 1
    assert report["diagnostic_adjusted_coverage_pct"] == 50.0
    assert report["remaining_shortfall"] == 1
    assert report["http_request_estimates"] == {
        "normal_financial_remaining_year_count": 2,
        "metadata_remaining_invalid_plus_missing_year_count": 1,
        "financial_fallback_request_ceiling": 8,
        "combined_request_ceiling_before_retry_or_pagination": 9,
    }
    assert report["invariants"] == {
        "partitions_cover_target_company_years": True,
        "remaining_and_held_cover_target_company_years": True,
        "no_duplicate_company_years": True,
        "http_formulae_hold": True,
    }


def test_rejects_tampered_or_nonbinding_listing_input_before_planner_call(tmp_path, monkeypatch):
    diagnostic, database, listing, payload = _diagnose(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="listing checksum mismatch"):
        diagnostic.diagnose_investor_core_listing_gaps(
            database, listing_csv=listing, expected_listing_sha256="0" * 64,
            listing_as_of=date(2026, 8, 5),
            expected_company_snapshot_sha256=_company_snapshot_checksum(),
        )
    listing.write_bytes(payload.replace(b"00000002,000002", b"00000002,999999"))
    with pytest.raises(ValueError, match="listing stock_code does not bind"):
        diagnostic.diagnose_investor_core_listing_gaps(
            database, listing_csv=listing, expected_listing_sha256=hashlib.sha256(listing.read_bytes()).hexdigest(),
            listing_as_of=date(2026, 8, 5), expected_company_snapshot_sha256=_company_snapshot_checksum(),
        )


@pytest.mark.parametrize("status", ["unknown", "conflict"])
def test_unknown_or_conflict_listing_never_holds_true_missing_targets(tmp_path, monkeypatch, status):
    diagnostic, database, listing, payload = _diagnose(tmp_path, monkeypatch)
    payload = payload.replace(
        b"00000002,000002,KOSDAQ,2024-01-02,,verified",
        f"00000002,000002,KOSDAQ,,,{status}".encode("ascii"),
    )
    listing.write_bytes(payload)

    report = diagnostic.diagnose_investor_core_listing_gaps(
        database,
        listing_csv=listing,
        expected_listing_sha256=hashlib.sha256(payload).hexdigest(),
        listing_as_of=date(2026, 8, 5),
        expected_company_snapshot_sha256=_company_snapshot_checksum(),
        coverage_year=2025,
        threshold_pct=100,
    )

    assert report["held_pre_listing_true_missing_company_years"] == [{
        "corp_code": "00000001", "stock_code": "000001", "bsns_year": 2023,
        "reason": "verified_listing_after_target_year",
    }]
    assert report["remaining_company_year_count"] == 3


def test_cli_is_json_readonly_diagnostic(tmp_path, monkeypatch):
    diagnostic, database, listing, payload = _diagnose(tmp_path, monkeypatch)
    from kreports.cli.main import app

    result = CliRunner().invoke(app, [
        "diagnose-investor-core-listing-gaps", "--db", str(database), "--listing-csv", str(listing),
        "--expected-listing-sha256", hashlib.sha256(payload).hexdigest(),
        "--listing-as-of", "2026-08-05",
        "--expected-company-snapshot-sha256", _company_snapshot_checksum(),
        "--coverage-year", "2025", "--threshold-pct", "100", "--json",
    ])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["limitations"] == ["diagnostic_only_not_activated"]
