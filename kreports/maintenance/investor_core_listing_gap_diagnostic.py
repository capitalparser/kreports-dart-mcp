"""Read-only, non-activated pre-listing diagnostics for investor-core plans."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
from io import StringIO
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from kreports.maintenance.investor_core_backfill_plan import (
    _open_readonly_database,
    plan_investor_core_backfill,
)


CSV_COLUMNS = (
    "corp_code",
    "stock_code",
    "market",
    "listed_from",
    "listed_to",
    "status",
)
CORE_MARKETS = frozenset({"KOSPI", "KOSDAQ"})
LISTING_STATUSES = frozenset({"verified", "unknown", "conflict"})
MAX_RENDERED_COMPANY_YEAR_ROWS = 200
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class _ListingRow:
    corp_code: str
    stock_code: str
    market: str
    listed_from: date | None
    listed_to: date | None
    status: str


def _require_sha256(value: str, *, field: str) -> str:
    checksum = value.strip().lower()
    if _SHA256_RE.fullmatch(checksum) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 hex digest")
    return checksum


def _parse_date(value: str, *, field: str, row_no: int) -> date | None:
    raw = value.strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO date at row {row_no}") from exc


def _current_core_companies(db_path: str | Path) -> list[dict[str, str]]:
    with _open_readonly_database(db_path) as connection:
        rows = connection.execute(
            "SELECT corp_code, stock_code, market FROM companies "
            "WHERE stock_code IS NOT NULL AND market IN ('KOSPI', 'KOSDAQ') "
            "ORDER BY corp_code"
        ).fetchall()
    companies = [
        {
            "corp_code": str(row["corp_code"]),
            "stock_code": str(row["stock_code"]),
            "market": str(row["market"]),
        }
        for row in rows
    ]
    if not companies:
        raise ValueError("current core company population is empty")
    if len({company["corp_code"] for company in companies}) != len(companies):
        raise ValueError("current core company population has duplicate corp_code")
    if len({company["stock_code"] for company in companies}) != len(companies):
        raise ValueError("current core company population has duplicate stock_code")
    return companies


def _company_snapshot_checksum(companies: Sequence[Mapping[str, str]]) -> str:
    payload = "".join(
        f"{company['corp_code']},{company['stock_code']},{company['market']}\n"
        for company in companies
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _read_listing(
    listing_csv: str | Path,
    *,
    expected_listing_sha256: str,
    listing_as_of: date,
    companies: Sequence[Mapping[str, str]],
) -> list[_ListingRow]:
    path = Path(listing_csv)
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ValueError("listing CSV must be a readable file") from exc
    if hashlib.sha256(payload).hexdigest() != expected_listing_sha256:
        raise ValueError("listing checksum mismatch")
    try:
        reader = csv.DictReader(StringIO(payload.decode("utf-8"), newline=""))
    except UnicodeDecodeError as exc:
        raise ValueError("listing CSV must be UTF-8") from exc
    if tuple(reader.fieldnames or ()) != CSV_COLUMNS:
        raise ValueError("listing CSV columns must match the documented contract")
    rows: list[_ListingRow] = []
    seen_corp_codes: set[str] = set()
    seen_stock_codes: set[str] = set()
    for row_no, raw in enumerate(reader, start=2):
        if None in raw:
            raise ValueError(f"listing CSV has an unexpected column at row {row_no}")
        corp_code = str(raw["corp_code"] or "")
        stock_code = str(raw["stock_code"] or "")
        market = str(raw["market"] or "")
        status = str(raw["status"] or "")
        if not corp_code or corp_code in seen_corp_codes:
            raise ValueError(f"listing CSV corp_code must be unique at row {row_no}")
        if not stock_code or stock_code in seen_stock_codes:
            raise ValueError(f"listing CSV stock_code must be unique at row {row_no}")
        seen_corp_codes.add(corp_code)
        seen_stock_codes.add(stock_code)
        if market not in CORE_MARKETS:
            raise ValueError(f"listing market is unsupported at row {row_no}")
        if status not in LISTING_STATUSES:
            raise ValueError(f"listing status is unsupported at row {row_no}")
        listed_from = _parse_date(str(raw["listed_from"] or ""), field="listed_from", row_no=row_no)
        listed_to = _parse_date(str(raw["listed_to"] or ""), field="listed_to", row_no=row_no)
        if status == "verified" and listed_from is None:
            raise ValueError(f"verified status requires listed_from at row {row_no}")
        if status != "verified" and (listed_from is not None or listed_to is not None):
            raise ValueError(f"{status} status cannot assert a listing period at row {row_no}")
        if listed_from is not None and listed_from > listing_as_of:
            raise ValueError(f"listed_from is after listing_as_of at row {row_no}")
        if listed_to is not None and (listed_from is None or listed_to < listed_from):
            raise ValueError(f"listed_to precedes listed_from at row {row_no}")
        if listed_to is not None and listed_to > listing_as_of:
            raise ValueError(f"listed_to is after listing_as_of at row {row_no}")
        rows.append(_ListingRow(corp_code, stock_code, market, listed_from, listed_to, status))
    if not rows:
        raise ValueError("listing CSV has no rows")
    company_by_corp = {company["corp_code"]: company for company in companies}
    if len(rows) != len(companies) or set(seen_corp_codes) != set(company_by_corp):
        raise ValueError("listing CSV row count does not match current core population")
    for row in rows:
        company = company_by_corp[row.corp_code]
        if row.stock_code != company["stock_code"]:
            raise ValueError("listing stock_code does not bind to current company master")
        if row.market != company["market"]:
            raise ValueError("listing market does not bind to current company master")
    return rows


def _bounded(rows: list[dict[str, object]]) -> tuple[list[dict[str, object]], int]:
    return rows[:MAX_RENDERED_COMPANY_YEAR_ROWS], max(len(rows) - MAX_RENDERED_COMPANY_YEAR_ROWS, 0)


def _company_year_row(candidate: Mapping[str, object], year: int) -> dict[str, object]:
    return {
        "corp_code": str(candidate["corp_code"]),
        "stock_code": str(candidate["stock_code"]),
        "bsns_year": year,
    }


def diagnose_investor_core_listing_gaps(
    db_path: str | Path,
    *,
    listing_csv: str | Path,
    expected_listing_sha256: str,
    listing_as_of: date,
    expected_company_snapshot_sha256: str,
    coverage_year: int | None = None,
    threshold_pct: float = 95.0,
) -> dict[str, Any]:
    """Reclassify only selected pre-listing true-missing targets diagnostically."""
    if not isinstance(listing_as_of, date) or isinstance(listing_as_of, datetime):
        raise ValueError("listing_as_of must be a date")
    expected_listing_checksum = _require_sha256(
        expected_listing_sha256, field="expected_listing_sha256"
    )
    expected_snapshot_checksum = _require_sha256(
        expected_company_snapshot_sha256,
        field="expected_company_snapshot_sha256",
    )
    companies = _current_core_companies(db_path)
    actual_snapshot_checksum = _company_snapshot_checksum(companies)
    if actual_snapshot_checksum != expected_snapshot_checksum:
        raise ValueError("current company snapshot checksum mismatch")
    listing_rows = _read_listing(
        listing_csv,
        expected_listing_sha256=expected_listing_checksum,
        listing_as_of=listing_as_of,
        companies=companies,
    )
    listing_by_corp = {row.corp_code: row for row in listing_rows}
    plan = plan_investor_core_backfill(
        db_path,
        coverage_year=coverage_year,
        threshold_pct=threshold_pct,
    )
    valid_rows: list[dict[str, object]] = []
    invalid_rows: list[dict[str, object]] = []
    missing_rows: list[dict[str, object]] = []
    held_rows: list[dict[str, object]] = []
    remaining_rows: list[dict[str, object]] = []
    target_keys: set[tuple[str, int]] = set()
    remaining_by_company: dict[str, int] = {}
    selected_companies = plan["selected_companies"]
    if not isinstance(selected_companies, list):
        raise ValueError("planner selected_companies must be a list")
    for candidate in selected_companies:
        if not isinstance(candidate, Mapping):
            raise ValueError("planner selected company must be an object")
        corp_code = str(candidate["corp_code"])
        listing = listing_by_corp.get(corp_code)
        if listing is None:
            raise ValueError("planner company is absent from current listing CSV")
        selected_years = [int(year) for year in candidate["selected_years"]]
        valid_years = {int(anchor["bsns_year"]) for anchor in candidate["annual_filing_anchors"]}
        invalid_years = {int(year) for year in candidate["invalid_annual_anchor_years"]}
        missing_years = {int(year) for year in candidate["missing_disclosure_metadata_years"]}
        if (valid_years & invalid_years) or (valid_years & missing_years) or (invalid_years & missing_years):
            raise ValueError("planner annual-anchor partitions overlap")
        for year in selected_years:
            key = (corp_code, year)
            if key in target_keys:
                raise ValueError("planner selected company-year targets must be unique")
            target_keys.add(key)
            row = _company_year_row(candidate, year)
            if year in valid_years:
                valid_rows.append(row)
                remaining_rows.append(row)
                remaining_by_company[corp_code] = remaining_by_company.get(corp_code, 0) + 1
            elif year in invalid_years:
                invalid_rows.append(row)
                remaining_rows.append(row)
                remaining_by_company[corp_code] = remaining_by_company.get(corp_code, 0) + 1
            elif year in missing_years:
                missing_rows.append(row)
                if listing.status == "verified" and listing.listed_from is not None and year < listing.listed_from.year:
                    held_rows.append({
                        **row,
                        "reason": "verified_listing_after_target_year",
                    })
                else:
                    remaining_rows.append(row)
                    remaining_by_company[corp_code] = remaining_by_company.get(corp_code, 0) + 1
            else:
                raise ValueError("planner annual-anchor partitions do not cover selected year")
    valid_keys = {(str(row["corp_code"]), int(row["bsns_year"])) for row in valid_rows}
    invalid_keys = {(str(row["corp_code"]), int(row["bsns_year"])) for row in invalid_rows}
    missing_keys = {(str(row["corp_code"]), int(row["bsns_year"])) for row in missing_rows}
    held_keys = {(str(row["corp_code"]), int(row["bsns_year"])) for row in held_rows}
    remaining_keys = {(str(row["corp_code"]), int(row["bsns_year"])) for row in remaining_rows}
    partitions_cover = valid_keys | invalid_keys | missing_keys == target_keys
    remaining_and_held_cover = remaining_keys | held_keys == target_keys and not (remaining_keys & held_keys)
    no_duplicate = (
        len(valid_keys) == len(valid_rows)
        and len(invalid_keys) == len(invalid_rows)
        and len(missing_keys) == len(missing_rows)
        and len(held_keys) == len(held_rows)
        and len(remaining_keys) == len(remaining_rows)
    )
    if not partitions_cover or not remaining_and_held_cover or not no_duplicate:
        raise ValueError("diagnostic partition invariants failed")
    selected_corp_codes = {str(candidate["corp_code"]) for candidate in selected_companies}
    zero_remaining_count = sum(
        remaining_by_company.get(corp_code, 0) == 0 for corp_code in selected_corp_codes
    )
    denominator = int(plan["denominator"])
    numerator = int(plan["numerator"])
    target_numerator = int(plan["target_numerator"])
    adjusted_numerator = min(denominator, numerator + zero_remaining_count)
    adjusted_coverage = (adjusted_numerator * 100.0 / denominator) if denominator else 0.0
    # Every remaining target ultimately needs a financial endpoint attempt;
    # invalid/missing metadata targets first consume metadata refresh capacity.
    normal_financial_count = len(remaining_rows)
    metadata_remaining_count = len(invalid_rows) + sum(
        (str(row["corp_code"]), int(row["bsns_year"])) in remaining_keys
        for row in missing_rows
    )
    fallback_ceiling = normal_financial_count * 4
    combined_ceiling = fallback_ceiling + metadata_remaining_count
    formulae_hold = (
        fallback_ceiling == normal_financial_count * 4
        and combined_ceiling == fallback_ceiling + metadata_remaining_count
    )
    if not formulae_hold:
        raise ValueError("diagnostic HTTP estimate invariants failed")
    valid_rendered, valid_omitted = _bounded(valid_rows)
    invalid_rendered, invalid_omitted = _bounded(invalid_rows)
    missing_rendered, missing_omitted = _bounded(missing_rows)
    held_rendered, held_omitted = _bounded(held_rows)
    remaining_rendered, remaining_omitted = _bounded(remaining_rows)
    return {
        "schema": "investor_core_listing_gap_diagnostic_v1",
        "listing_checksum": expected_listing_checksum,
        "listing_as_of": listing_as_of.isoformat(),
        "company_snapshot_checksum": actual_snapshot_checksum,
        "planner": {
            key: plan[key]
            for key in ("coverage_year", "threshold_pct", "denominator", "numerator", "target_numerator", "shortfall")
        },
        "valid_annual_anchor_company_year_count": len(valid_rows),
        "valid_annual_anchor_company_years": valid_rendered,
        "valid_annual_anchor_company_years_omitted_count": valid_omitted,
        "invalid_annual_anchor_company_year_count": len(invalid_rows),
        "invalid_annual_anchor_company_years": invalid_rendered,
        "invalid_annual_anchor_company_years_omitted_count": invalid_omitted,
        "missing_disclosure_metadata_company_year_count": len(missing_rows),
        "missing_disclosure_metadata_company_years": missing_rendered,
        "missing_disclosure_metadata_company_years_omitted_count": missing_omitted,
        "held_pre_listing_true_missing_company_year_count": len(held_rows),
        "held_pre_listing_true_missing_company_years": held_rendered,
        "held_pre_listing_true_missing_company_years_omitted_count": held_omitted,
        "remaining_company_year_count": len(remaining_rows),
        "remaining_company_years": remaining_rendered,
        "remaining_company_years_omitted_count": remaining_omitted,
        "zero_remaining_target_company_count": zero_remaining_count,
        "diagnostic_adjusted_numerator": adjusted_numerator,
        "diagnostic_adjusted_coverage_pct": adjusted_coverage,
        "remaining_shortfall": max(target_numerator - adjusted_numerator, 0),
        "http_request_estimates": {
            "normal_financial_remaining_year_count": normal_financial_count,
            "metadata_remaining_invalid_plus_missing_year_count": metadata_remaining_count,
            "financial_fallback_request_ceiling": fallback_ceiling,
            "combined_request_ceiling_before_retry_or_pagination": combined_ceiling,
        },
        "invariants": {
            "partitions_cover_target_company_years": partitions_cover,
            "remaining_and_held_cover_target_company_years": remaining_and_held_cover,
            "no_duplicate_company_years": no_duplicate,
            "http_formulae_hold": formulae_hold,
        },
        "limitations": ["diagnostic_only_not_activated"],
    }
