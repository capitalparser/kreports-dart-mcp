"""Read-only preflight planning for investor-core three-year coverage."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_CEILING
import json
from pathlib import Path
import sqlite3
from typing import Any

from kreports.annual_filing_identity import (
    annual_report_name_matches_business_year,
)
from kreports.analysis.filing_provenance import (
    latest_annual_filing_anchor_from_rows,
)
from kreports.db.quality_snapshot import QUALITY_VERSION
from kreports.db.readonly_snapshot import (
    ReadonlySQLiteSnapshotUnavailable,
    open_checkpointed_readonly_sqlite,
)
from kreports.quality.company_year_fingerprint import (
    validate_quality_evidence_summary,
)

WINDOW_YEARS = 5
MEMBERSHIP_WINDOW_YEARS = 3
MAX_REJECTED_PROOF_DIAGNOSTICS = 20
CORE_MARKETS = ("KOSPI", "KOSDAQ")
_HISTORICAL_MEMBERSHIP_TABLE = "company_year_listing_memberships"
_HISTORICAL_MEMBERSHIP_REQUIRED_COLUMNS = {
    "corp_code",
    "stock_code",
    "bsns_year",
    "market",
    "status",
}


@dataclass
class _RejectedProofDiagnostics:
    """Keep complete rejection accounting while bounding rendered details."""

    total_count: int = 0
    samples: list[dict[str, Any]] = field(default_factory=list)


def _open_readonly_database(db_path: str | Path) -> sqlite3.Connection:
    try:
        path = Path(db_path).expanduser().resolve(strict=True)
    except OSError as exc:
        raise ValueError("db path must be an existing file") from exc
    if not path.is_file():
        raise ValueError("db path must be an existing file")
    try:
        connection = open_checkpointed_readonly_sqlite(path)
    except ReadonlySQLiteSnapshotUnavailable as exc:
        raise ValueError("db snapshot must be checkpointed") from exc
    except sqlite3.Error as exc:
        raise ValueError("db path must be a readable SQLite database") from exc
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _target_numerator(denominator: int, threshold_pct: float) -> int:
    threshold = Decimal(str(threshold_pct))
    if (
        not threshold.is_finite()
        or not Decimal("0") < threshold <= Decimal("100")
    ):
        raise ValueError("threshold_pct must be greater than 0 and at most 100")
    return int(
        (Decimal(denominator) * threshold / Decimal("100")).to_integral_value(
            rounding=ROUND_CEILING
        )
    )


def _verified_historical_population(
    connection: sqlite3.Connection,
    *,
    coverage_year: int,
) -> tuple[list[str], tuple[int, ...]]:
    """Return the same verified three-year listed population as the release gate.

    Current company-market values are not an eligibility predicate. They are
    used only to reject orphaned membership rows; membership status and market
    come from the retained company-year observations.
    """
    required_years = tuple(
        range(
            coverage_year - MEMBERSHIP_WINDOW_YEARS + 1,
            coverage_year + 1,
        )
    )
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (_HISTORICAL_MEMBERSHIP_TABLE,),
    ).fetchone()
    if table is None:
        raise ValueError(
            "historical listing membership evidence is unavailable: "
            "company_year_listing_memberships is not installed"
        )
    columns = {
        str(row["name"])
        for row in connection.execute(
            f"PRAGMA table_info({_HISTORICAL_MEMBERSHIP_TABLE})"
        ).fetchall()
    }
    missing_columns = sorted(_HISTORICAL_MEMBERSHIP_REQUIRED_COLUMNS - columns)
    if missing_columns:
        raise ValueError(
            "historical listing membership evidence is unavailable: "
            "company_year_listing_memberships is missing "
            f"{','.join(missing_columns)}"
        )

    year_bindings = ", ".join("?" for _ in required_years)
    verified_pairs = {
        (int(row["bsns_year"]), str(row["market"]))
        for row in connection.execute(
            "SELECT DISTINCT bsns_year, market "
            f"FROM {_HISTORICAL_MEMBERSHIP_TABLE} "
            f"WHERE bsns_year IN ({year_bindings}) "
            "AND market IN ('KOSPI', 'KOSDAQ') AND status='verified'",
            required_years,
        ).fetchall()
    }
    missing_pairs = [
        f"{year}:{market}"
        for year in required_years
        for market in CORE_MARKETS
        if (year, market) not in verified_pairs
    ]
    if missing_pairs:
        raise ValueError(
            "historical listing membership evidence is unavailable: "
            "missing verified market-year "
            f"{','.join(missing_pairs)}"
        )

    rows = connection.execute(
        "SELECT m.corp_code "
        f"FROM {_HISTORICAL_MEMBERSHIP_TABLE} AS m "
        "JOIN companies AS c ON c.corp_code=m.corp_code "
        f"WHERE m.bsns_year IN ({year_bindings}) "
        "AND m.market IN ('KOSPI', 'KOSDAQ') AND m.status='verified' "
        "GROUP BY m.corp_code "
        "HAVING COUNT(DISTINCT m.bsns_year)=? "
        "ORDER BY m.corp_code",
        (*required_years, len(required_years)),
    ).fetchall()
    return [str(row["corp_code"]) for row in rows], required_years


def _annual_anchor(
    connection: sqlite3.Connection,
    *,
    corp_code: str,
    year: int,
) -> dict[str, str] | None:
    anchor, _has_matching_annual_row = _annual_anchor_diagnostic(
        connection,
        corp_code=corp_code,
        year=year,
    )
    return anchor


def _annual_anchor_diagnostic(
    connection: sqlite3.Connection,
    *,
    corp_code: str,
    year: int,
) -> tuple[dict[str, str] | None, bool]:
    """Return strict-anchor validity and whether business-year metadata exists."""
    rows = connection.execute(
        "SELECT corp_code, rcept_no, disc_date, report_nm FROM disclosures "
        "WHERE corp_code=? "
        "ORDER BY disc_date DESC, rcept_no DESC",
        (corp_code,),
    ).fetchall()
    disclosure_rows = [dict(row) for row in rows]
    anchor = latest_annual_filing_anchor_from_rows(
        disclosure_rows,
        corp_code=corp_code,
        bsns_year=year,
    )
    has_matching_annual_row = any(
        annual_report_name_matches_business_year(row.get("report_nm"), year)
        for row in disclosure_rows
    )
    return anchor, has_matching_annual_row


def _diagnostic(
    diagnostics: _RejectedProofDiagnostics,
    *,
    corp_code: str,
    index: int | None,
    reason: str,
) -> None:
    diagnostics.total_count += 1
    if len(diagnostics.samples) < MAX_REJECTED_PROOF_DIAGNOSTICS:
        diagnostics.samples.append({
            "corp_code": corp_code,
            "proof_index": index,
            "reason": reason,
        })


def _valid_proven_years(
    connection: sqlite3.Connection,
    *,
    corp_code: str,
    quality_version: object,
    evidence_summary_json: object,
    window_start: int,
    coverage_year: int,
    diagnostics: _RejectedProofDiagnostics,
) -> list[int]:
    try:
        summary = json.loads(str(evidence_summary_json))
        if str(quality_version) != QUALITY_VERSION:
            raise ValueError("persisted quality version is unsupported")
        canonical_summary = validate_quality_evidence_summary(
            summary,
            expected_quality_version=QUALITY_VERSION,
        )
        proof = canonical_summary["financial_core_proof"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        _diagnostic(
            diagnostics,
            corp_code=corp_code,
            index=None,
            reason="invalid_quality_evidence_summary",
        )
        return []
    if not isinstance(proof, dict) or (
        proof.get("window_start_year") != window_start
        or proof.get("window_end_year") != coverage_year
        or not isinstance(proof.get("proven_years"), list)
    ):
        _diagnostic(
            diagnostics,
            corp_code=corp_code,
            index=None,
            reason="invalid_financial_core_proof_window",
        )
        return []

    valid: dict[int, dict[str, str]] = {}
    for index, row in enumerate(proof["proven_years"]):
        year = int(row["bsns_year"])
        report_nm = str(row["report_nm"])
        anchor = _annual_anchor(connection, corp_code=corp_code, year=year)
        if anchor is None:
            _diagnostic(diagnostics, corp_code=corp_code, index=index, reason="missing_or_invalid_annual_anchor")
            continue
        if (
            not annual_report_name_matches_business_year(report_nm, year)
            or row["rcept_no"] != anchor["rcept_no"]
            or report_nm != anchor["report_nm"]
        ):
            _diagnostic(diagnostics, corp_code=corp_code, index=index, reason="proof_anchor_mismatch")
            continue
        valid[year] = anchor
    return sorted(valid)


def _candidate_selection(
    *,
    grade: str,
    financial_core_status: str,
    proven_years: list[int],
    anchors: dict[int, dict[str, str]],
    window_start: int,
    coverage_year: int,
) -> tuple[int, list[int], list[int], bool]:
    if grade in {"A", "B"}:
        required = 1
    else:
        required = max(1, 3 - len(proven_years))
    missing_years = [year for year in range(window_start, coverage_year + 1) if year not in proven_years]
    mandatory_years = [coverage_year] if financial_core_status != "available" else []
    # Current status is a separate gate condition.  It must be repaired even
    # when a stale/otherwise valid proof row happens to mention that year.
    selected = mandatory_years[:]
    remaining = [year for year in missing_years if year not in selected]
    selected.extend(sorted((year for year in remaining if year in anchors), reverse=True))
    selected.extend(sorted((year for year in remaining if year not in anchors), reverse=True))
    selected = selected[:required]
    source_ready = len(selected) == required and all(year in anchors for year in selected)
    return required, selected, missing_years, source_ready


def plan_investor_core_backfill(
    db_path: str | Path,
    *,
    coverage_year: int | None = None,
    threshold_pct: float = 95.0,
) -> dict[str, Any]:
    """Return a deterministic, non-mutating investor-core remediation plan."""
    # Validate caller input before attempting any database interaction.
    _target_numerator(0, threshold_pct)
    with _open_readonly_database(db_path) as connection:
        if coverage_year is None:
            value = connection.execute(
                "SELECT MAX(bsns_year) FROM company_year_quality"
            ).fetchone()[0]
            if value is None:
                raise ValueError("coverage_year is unavailable")
            coverage_year = int(value)
        eligible_corp_codes, membership_years = _verified_historical_population(
            connection,
            coverage_year=coverage_year,
        )
        denominator = len(eligible_corp_codes)
        eligible_bindings = ", ".join("?" for _ in eligible_corp_codes)
        numerator = (
            int(connection.execute(
                "SELECT COUNT(*) FROM company_year_quality q "
                f"WHERE q.corp_code IN ({eligible_bindings}) "
                "AND q.bsns_year=? "
                "AND q.investor_grade IN ('A', 'B') "
                "AND q.financial_core_status='available'",
                (*eligible_corp_codes, coverage_year),
            ).fetchone()[0])
            if eligible_corp_codes
            else 0
        )
        window_start = coverage_year - WINDOW_YEARS + 1
        diagnostics = _RejectedProofDiagnostics()
        candidates: list[dict[str, Any]] = []
        rows = (
            connection.execute(
                "SELECT c.corp_code, m.stock_code, c.corp_name, q.investor_grade, "
                "q.financial_core_status, q.quality_version, q.evidence_summary_json "
                "FROM companies c "
                f"JOIN {_HISTORICAL_MEMBERSHIP_TABLE} m "
                "ON m.corp_code=c.corp_code AND m.bsns_year=? "
                "AND m.market IN ('KOSPI', 'KOSDAQ') AND m.status='verified' "
                "LEFT JOIN company_year_quality q "
                "ON q.corp_code=c.corp_code AND q.bsns_year=? "
                f"WHERE c.corp_code IN ({eligible_bindings}) "
                "AND (q.corp_code IS NULL "
                "OR COALESCE(q.investor_grade, '') NOT IN ('A', 'B') "
                "OR COALESCE(q.financial_core_status, '') != 'available') "
                "ORDER BY c.corp_code",
                (coverage_year, coverage_year, *eligible_corp_codes),
            ).fetchall()
            if eligible_corp_codes
            else []
        )
        for row in rows:
            corp_code = str(row["corp_code"])
            proven_years = _valid_proven_years(
                connection,
                corp_code=corp_code,
                quality_version=row["quality_version"],
                evidence_summary_json=row["evidence_summary_json"],
                window_start=window_start,
                coverage_year=coverage_year,
                diagnostics=diagnostics,
            )
            annual_anchor_diagnostics = {
                year: _annual_anchor_diagnostic(
                    connection,
                    corp_code=corp_code,
                    year=year,
                )
                for year in range(window_start, coverage_year + 1)
            }
            annual_anchors = {
                year: anchor
                for year, (anchor, _has_matching_annual_row) in annual_anchor_diagnostics.items()
            }
            available_anchors = {
                year: anchor
                for year, anchor in annual_anchors.items()
                if anchor is not None
            }
            required, selected_years, _missing_years, source_ready = _candidate_selection(
                grade=str(row["investor_grade"] or "D"),
                financial_core_status=str(row["financial_core_status"] or "missing"),
                proven_years=proven_years,
                anchors=available_anchors,
                window_start=window_start,
                coverage_year=coverage_year,
            )
            candidates.append({
                "corp_code": corp_code,
                "stock_code": str(row["stock_code"]),
                "corp_name": str(row["corp_name"]),
                "current_investor_grade": str(row["investor_grade"] or "D"),
                "current_financial_core_status": str(
                    row["financial_core_status"] or "missing"
                ),
                "proven_years": proven_years,
                "required_successful_year_count": required,
                "selected_years": selected_years,
                "annual_filing_anchors": [
                    {"bsns_year": year, **available_anchors[year]}
                    for year in selected_years
                    if year in available_anchors
                ],
                "invalid_annual_anchor_years": [
                    year
                    for year in selected_years
                    if annual_anchors[year] is None
                    and annual_anchor_diagnostics[year][1]
                ],
                "missing_disclosure_metadata_years": [
                    year
                    for year in selected_years
                    if annual_anchors[year] is None
                    and not annual_anchor_diagnostics[year][1]
                ],
                "source_ready": source_ready,
                "fillable": len(selected_years) == required,
            })
    target_numerator = _target_numerator(denominator, threshold_pct)
    shortfall = max(target_numerator - numerator, 0)
    fillable = [candidate for candidate in candidates if candidate["fillable"]]
    prioritized = sorted(
        fillable,
        key=lambda candidate: (
            candidate["required_successful_year_count"],
            not candidate["source_ready"],
            candidate["corp_code"],
        ),
    )
    selected_companies = sorted(prioritized[:shortfall], key=lambda candidate: candidate["corp_code"])
    for candidate in selected_companies:
        candidate.pop("fillable")
    unfillable_shortfall = max(shortfall - len(fillable), 0)
    return {
        "coverage_year": coverage_year,
        "threshold_pct": float(threshold_pct),
        "population_source": "verified_company_year_listing_memberships",
        "membership_required_years": list(membership_years),
        "membership_market_scope": list(CORE_MARKETS),
        "denominator": denominator,
        "numerator": numerator,
        "target_numerator": target_numerator,
        "shortfall": shortfall,
        "selected_candidate_count": len(selected_companies),
        "unselected_candidate_count": len(candidates) - len(selected_companies),
        "selected_successful_company_year_request_count": sum(
            int(candidate["required_successful_year_count"])
            for candidate in selected_companies
        ),
        "selected_source_ready_count": sum(
            1 for candidate in selected_companies if candidate["source_ready"]
        ),
        "selected_needing_disclosure_metadata_count": sum(
            1 for candidate in selected_companies if not candidate["source_ready"]
        ),
        "selected_valid_annual_anchor_company_count": sum(
            1 for candidate in selected_companies if candidate["annual_filing_anchors"]
        ),
        "selected_valid_annual_anchor_year_count": sum(
            len(candidate["annual_filing_anchors"])
            for candidate in selected_companies
        ),
        "selected_invalid_annual_anchor_company_count": sum(
            1 for candidate in selected_companies if candidate["invalid_annual_anchor_years"]
        ),
        "selected_invalid_annual_anchor_year_count": sum(
            len(candidate["invalid_annual_anchor_years"])
            for candidate in selected_companies
        ),
        "selected_true_missing_disclosure_metadata_company_count": sum(
            1
            for candidate in selected_companies
            if candidate["missing_disclosure_metadata_years"]
        ),
        "selected_true_missing_disclosure_metadata_year_count": sum(
            len(candidate["missing_disclosure_metadata_years"])
            for candidate in selected_companies
        ),
        "unfillable_shortfall": unfillable_shortfall,
        "selected_companies": selected_companies,
        "rejected_proof_row_count": diagnostics.total_count,
        "rejected_proof_diagnostics": diagnostics.samples,
        "rejected_proof_diagnostics_omitted_count": (
            diagnostics.total_count - len(diagnostics.samples)
        ),
        "limitations": [
            "No-network preflight only; it does not prove DART availability.",
            "This plan does not prove DART API quota or request success.",
            "Historical listed-company eligibility is limited to verified "
            "KOSPI/KOSDAQ membership observations for the required years.",
            "This plan does not prove release readiness.",
            "Source readiness requires a strict latest annual-filing anchor; "
            "a business-year matching row with an invalid anchor is not filing absence.",
        ],
    }
