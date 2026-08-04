"""Read-only preflight planning for investor-core three-year coverage."""
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING
from datetime import date
import json
from pathlib import Path
import re
import sqlite3
from typing import Any


CORE_MARKETS = ("KOSPI", "KOSDAQ")
WINDOW_YEARS = 5
MAX_REJECTED_PROOF_DIAGNOSTICS = 20
_ANNUAL_REPORT_RE = re.compile(r"사업보고서 \((\d{4})\.\d{2}\)")
_RECEIPT_RE = re.compile(r"\d{14}\Z")
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")


def _open_readonly_database(db_path: str | Path) -> sqlite3.Connection:
    try:
        path = Path(db_path).expanduser().resolve(strict=True)
    except OSError as exc:
        raise ValueError("db path must be an existing file") from exc
    if not path.is_file():
        raise ValueError("db path must be an existing file")
    try:
        connection = sqlite3.connect(
            f"{path.as_uri()}?mode=ro&immutable=1",
            uri=True,
        )
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


def _is_valid_receipt_date(receipt: object, disc_date: object) -> bool:
    if not isinstance(receipt, str) or not _RECEIPT_RE.fullmatch(receipt):
        return False
    if not isinstance(disc_date, str):
        return False
    try:
        return receipt[:8] == date.fromisoformat(disc_date).strftime("%Y%m%d")
    except ValueError:
        return False


def _annual_anchor(
    connection: sqlite3.Connection,
    *,
    corp_code: str,
    year: int,
) -> dict[str, str] | None:
    rows = connection.execute(
        "SELECT rcept_no, disc_date, report_nm FROM disclosures "
        "WHERE corp_code=? AND report_nm LIKE ? "
        "ORDER BY disc_date DESC, rcept_no DESC",
        (corp_code, f"%사업보고서 ({year}.%"),
    ).fetchall()
    for row in rows:
        if _is_valid_receipt_date(row["rcept_no"], row["disc_date"]):
            return {
                "rcept_no": str(row["rcept_no"]),
                "disc_date": str(row["disc_date"]),
                "report_nm": str(row["report_nm"]),
            }
    return None


def _diagnostic(
    diagnostics: list[dict[str, Any]],
    *,
    corp_code: str,
    index: int | None,
    reason: str,
) -> None:
    if len(diagnostics) < MAX_REJECTED_PROOF_DIAGNOSTICS:
        diagnostics.append({
            "corp_code": corp_code,
            "proof_index": index,
            "reason": reason,
        })


def _valid_proven_years(
    connection: sqlite3.Connection,
    *,
    corp_code: str,
    evidence_summary_json: object,
    window_start: int,
    coverage_year: int,
    diagnostics: list[dict[str, Any]],
) -> tuple[list[int], dict[int, dict[str, str]]]:
    try:
        summary = json.loads(str(evidence_summary_json))
        proof = summary["financial_core_proof"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        _diagnostic(
            diagnostics,
            corp_code=corp_code,
            index=None,
            reason="malformed_financial_core_proof",
        )
        return [], {}
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
        return [], {}

    valid: dict[int, dict[str, str]] = {}
    for index, row in enumerate(proof["proven_years"]):
        if not isinstance(row, dict):
            _diagnostic(diagnostics, corp_code=corp_code, index=index, reason="proof_row_not_object")
            continue
        year = row.get("bsns_year")
        if isinstance(year, bool) or not isinstance(year, int) or not window_start <= year <= coverage_year:
            _diagnostic(diagnostics, corp_code=corp_code, index=index, reason="invalid_proof_year")
            continue
        if year in valid:
            _diagnostic(diagnostics, corp_code=corp_code, index=index, reason="duplicate_proof_year")
            continue
        report_nm = row.get("report_nm")
        if (
            row.get("fs_div") not in {"CFS", "OFS"}
            or not isinstance(report_nm, str)
            or _ANNUAL_REPORT_RE.search(report_nm) is None
            or _ANNUAL_REPORT_RE.search(report_nm).group(1) != str(year)
            or not isinstance(row.get("rcept_no"), str)
            or _RECEIPT_RE.fullmatch(row["rcept_no"]) is None
            or not isinstance(row.get("metric_digest"), str)
            or _DIGEST_RE.fullmatch(row["metric_digest"]) is None
        ):
            _diagnostic(diagnostics, corp_code=corp_code, index=index, reason="malformed_proof_row")
            continue
        anchor = _annual_anchor(connection, corp_code=corp_code, year=year)
        if anchor is None:
            _diagnostic(diagnostics, corp_code=corp_code, index=index, reason="missing_or_invalid_annual_anchor")
            continue
        if row["rcept_no"] != anchor["rcept_no"] or report_nm != anchor["report_nm"]:
            _diagnostic(diagnostics, corp_code=corp_code, index=index, reason="proof_anchor_mismatch")
            continue
        valid[year] = anchor
    return sorted(valid), valid


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
        denominator = int(connection.execute(
            "SELECT COUNT(*) FROM companies "
            "WHERE stock_code IS NOT NULL AND market IN ('KOSPI', 'KOSDAQ')"
        ).fetchone()[0])
        numerator = int(connection.execute(
            "SELECT COUNT(*) FROM companies c JOIN company_year_quality q "
            "ON q.corp_code=c.corp_code AND q.bsns_year=? "
            "WHERE c.stock_code IS NOT NULL AND c.market IN ('KOSPI', 'KOSDAQ') "
            "AND q.investor_grade IN ('A', 'B') "
            "AND q.financial_core_status='available'",
            (coverage_year,),
        ).fetchone()[0])
        window_start = coverage_year - WINDOW_YEARS + 1
        diagnostics: list[dict[str, Any]] = []
        candidates: list[dict[str, Any]] = []
        rows = connection.execute(
            "SELECT c.corp_code, c.stock_code, c.corp_name, q.investor_grade, "
            "q.financial_core_status, q.evidence_summary_json "
            "FROM companies c JOIN company_year_quality q "
            "ON q.corp_code=c.corp_code AND q.bsns_year=? "
            "WHERE c.stock_code IS NOT NULL AND c.market IN ('KOSPI', 'KOSDAQ') "
            "AND ((q.investor_grade IN ('A', 'B') "
            "AND q.financial_core_status != 'available') OR q.investor_grade='D') "
            "ORDER BY c.corp_code",
            (coverage_year,),
        ).fetchall()
        for row in rows:
            corp_code = str(row["corp_code"])
            proven_years, proof_anchors = _valid_proven_years(
                connection,
                corp_code=corp_code,
                evidence_summary_json=row["evidence_summary_json"],
                window_start=window_start,
                coverage_year=coverage_year,
                diagnostics=diagnostics,
            )
            annual_anchors = {
                year: _annual_anchor(connection, corp_code=corp_code, year=year)
                for year in range(window_start, coverage_year + 1)
            }
            available_anchors = {
                year: anchor
                for year, anchor in annual_anchors.items()
                if anchor is not None
            }
            required, selected_years, _missing_years, source_ready = _candidate_selection(
                grade=str(row["investor_grade"]),
                financial_core_status=str(row["financial_core_status"]),
                proven_years=proven_years,
                anchors=available_anchors,
                window_start=window_start,
                coverage_year=coverage_year,
            )
            candidates.append({
                "corp_code": corp_code,
                "stock_code": str(row["stock_code"]),
                "corp_name": str(row["corp_name"]),
                "current_investor_grade": str(row["investor_grade"]),
                "current_financial_core_status": str(row["financial_core_status"]),
                "proven_years": proven_years,
                "required_successful_year_count": required,
                "selected_years": selected_years,
                "annual_filing_anchors": [
                    {"bsns_year": year, **available_anchors[year]}
                    for year in selected_years
                    if year in available_anchors
                ],
                "missing_disclosure_metadata_years": [
                    year for year in selected_years if year not in available_anchors
                ],
                "source_ready": source_ready,
                "fillable": len(selected_years) == required,
                "proof_anchors": proof_anchors,
            })
    target_numerator = _target_numerator(denominator, threshold_pct)
    shortfall = max(target_numerator - numerator, 0)
    fillable = [candidate for candidate in candidates if candidate["fillable"]]
    prioritized = sorted(
        fillable,
        key=lambda candidate: (
            not candidate["source_ready"],
            candidate["required_successful_year_count"],
            candidate["corp_code"],
        ),
    )
    selected_companies = sorted(prioritized[:shortfall], key=lambda candidate: candidate["corp_code"])
    for candidate in selected_companies:
        candidate.pop("fillable")
        candidate.pop("proof_anchors")
    unfillable_shortfall = max(shortfall - len(fillable), 0)
    return {
        "coverage_year": coverage_year,
        "threshold_pct": float(threshold_pct),
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
        "unfillable_shortfall": unfillable_shortfall,
        "selected_companies": selected_companies,
        "rejected_proof_row_count": len(diagnostics),
        "rejected_proof_diagnostics": diagnostics,
        "limitations": [
            "No-network preflight only; it does not prove DART availability.",
            "This plan does not prove DART API quota or request success.",
            "This plan does not prove listing-period eligibility.",
            "This plan does not prove release readiness.",
        ],
    }
