"""Import and diagnose provenance-bound historical listing-period evidence.

The release denominator remains the current KOSPI/KOSDAQ population until a
separately approved eligibility policy consumes this evidence.  In particular,
an absent, unknown, or conflicting source row never silently removes a company
from that denominator.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, timezone
import hashlib
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from sqlalchemy import inspect, select

from kreports.db.engine import get_session
from kreports.db.models import Company, CompanyListingPeriod
from kreports.runtime import require_runtime_write


OFFICIAL_KRX_HOSTS = frozenset({"data.krx.co.kr", "global.krx.co.kr", "kind.krx.co.kr"})
LISTING_MARKETS = frozenset({"KOSPI", "KOSDAQ", "KONEX"})
LISTING_STATUSES = frozenset({"verified", "unknown", "conflict"})
FULL_YEAR_RULE = (
    "verified_as_of_on_or_after_year_end_and_period_covers_jan1_through_dec31"
)
_CHECKSUM_RE = re.compile(r"[0-9a-f]{64}")
_TRANSFORMATION_VERSION_RE = re.compile(r"[A-Za-z0-9._-]{1,80}")
NORMALIZED_SOURCE_TYPE = "normalized_listing_period_csv"
SUPPORTED_TRANSFORMATION_VERSIONS = frozenset({"krx-listing-normalize-v1"})
_SNAPSHOT_COLUMNS = (
    "corp_code",
    "stock_code",
    "market",
    "listed_from",
    "listed_to",
    "status",
)


def _parse_iso_date(value: str, *, field: str, row_no: int) -> date | None:
    raw = value.strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"{field} must be ISO date at row {row_no}") from exc


def _validate_raw_source_metadata(
    *, raw_source_uri: str, raw_source_checksum: str, raw_source_retrieved_at: datetime
) -> tuple[str, datetime]:
    parsed = urlparse(raw_source_uri)
    if parsed.scheme != "https" or parsed.hostname not in OFFICIAL_KRX_HOSTS:
        raise ValueError("raw_source_uri must be an official KRX https URI")
    checksum = raw_source_checksum.strip().lower()
    if _CHECKSUM_RE.fullmatch(checksum) is None:
        raise ValueError("raw_source_checksum must be a lowercase SHA-256 hex digest")
    if (
        not isinstance(raw_source_retrieved_at, datetime)
        or raw_source_retrieved_at.tzinfo is None
    ):
        raise ValueError("raw_source_retrieved_at must be timezone-aware")
    return checksum, raw_source_retrieved_at.astimezone(timezone.utc)


def import_listing_period_snapshot(
    normalized_path: str | Path,
    *,
    raw_source_path: str | Path | None,
    raw_source_uri: str,
    raw_source_checksum: str,
    raw_source_retrieved_at: datetime,
    normalized_checksum: str,
    transformation_version: str,
    as_of: date,
) -> dict[str, object]:
    """Persist a normalized listing CSV bound to an official KRX raw receipt.

    ``normalized_path`` is a transformed UTF-8 CSV, not an official KRX
    artifact. Its digest and transformation version are persisted separately
    from the downloaded raw receipt.  The normalized columns are exactly
    ``corp_code,stock_code,market,listed_from,listed_to,status``.  ``as_of``
    must be no later than the raw-receipt date and prevents a current company
    master from being misread as historical listing evidence.
    """
    require_runtime_write("import company listing periods")
    if raw_source_path is None:
        raise ValueError("raw source artifact is required")
    raw_path = Path(raw_source_path)
    if not raw_path.is_file():
        raise ValueError("raw source artifact is required")
    raw_checksum, raw_retrieved_at_utc = _validate_raw_source_metadata(
        raw_source_uri=raw_source_uri,
        raw_source_checksum=raw_source_checksum,
        raw_source_retrieved_at=raw_source_retrieved_at,
    )
    raw_payload = raw_path.read_bytes()
    if hashlib.sha256(raw_payload).hexdigest() != raw_checksum:
        raise ValueError("raw source checksum mismatch")
    checksum = normalized_checksum.strip().lower()
    if _CHECKSUM_RE.fullmatch(checksum) is None:
        raise ValueError("normalized_checksum must be a lowercase SHA-256 hex digest")
    if (
        _TRANSFORMATION_VERSION_RE.fullmatch(transformation_version) is None
        or transformation_version not in SUPPORTED_TRANSFORMATION_VERSIONS
    ):
        raise ValueError("transformation_version is unsupported")
    if not isinstance(as_of, date) or isinstance(as_of, datetime):
        raise ValueError("as_of must be a date")
    if as_of > raw_retrieved_at_utc.date():
        raise ValueError("as_of cannot be after raw_source_retrieved_at date")
    path = Path(normalized_path)
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != checksum:
        raise ValueError("normalized checksum mismatch")

    try:
        rows = list(csv.DictReader(payload.decode("utf-8-sig").splitlines()))
    except UnicodeDecodeError as exc:
        raise ValueError("listing snapshot must be UTF-8") from exc
    if not rows or tuple(rows[0]) != _SNAPSHOT_COLUMNS:
        raise ValueError("listing snapshot columns must match the documented contract")

    normalized: list[dict[str, object]] = []
    seen_corp_codes: set[str] = set()
    with get_session() as session:
        company_stock = {
            str(corp_code): str(stock_code)
            for corp_code, stock_code in session.query(
                Company.corp_code, Company.stock_code
            ).filter(Company.stock_code.isnot(None)).all()
        }
        for row_no, row in enumerate(rows, start=2):
            corp_code = str(row["corp_code"] or "").strip()
            stock_code = str(row["stock_code"] or "").strip()
            market = str(row["market"] or "").strip().upper()
            status = str(row["status"] or "").strip().lower()
            if corp_code in seen_corp_codes:
                raise ValueError(f"duplicate corp_code at row {row_no}")
            seen_corp_codes.add(corp_code)
            if company_stock.get(corp_code) != stock_code:
                raise ValueError(f"stock_code does not bind to corp_code at row {row_no}")
            if market not in LISTING_MARKETS:
                raise ValueError(f"market is unsupported at row {row_no}")
            if status not in LISTING_STATUSES:
                raise ValueError(f"status is unsupported at row {row_no}")
            listed_from = _parse_iso_date(
                str(row["listed_from"] or ""), field="listed_from", row_no=row_no
            )
            listed_to = _parse_iso_date(
                str(row["listed_to"] or ""), field="listed_to", row_no=row_no
            )
            if status == "verified" and listed_from is None:
                raise ValueError(f"verified status requires listed_from at row {row_no}")
            if status != "verified" and (listed_from is not None or listed_to is not None):
                raise ValueError(f"{status} status cannot assert a listing period at row {row_no}")
            if listed_from and listed_from > as_of:
                raise ValueError(f"listed_from is after as_of at row {row_no}")
            if listed_to and (listed_from is None or listed_to < listed_from):
                raise ValueError(f"listed_to precedes listed_from at row {row_no}")
            if listed_to and listed_to > as_of:
                raise ValueError(f"listed_to is after as_of at row {row_no}")
            normalized.append({
                "corp_code": corp_code,
                "stock_code": stock_code,
                "market": market,
                "listed_from": listed_from,
                "listed_to": listed_to,
                "status": status,
                "as_of": as_of,
                "raw_source_uri": raw_source_uri,
                "raw_source_checksum": raw_checksum,
                "raw_source_retrieved_at": raw_retrieved_at_utc,
                "raw_source_storage_uri": raw_path.resolve().as_uri(),
                "raw_source_size_bytes": len(raw_payload),
                "normalized_checksum": checksum,
                "normalized_storage_uri": path.resolve().as_uri(),
                "normalized_size_bytes": len(payload),
                "transformation_version": transformation_version,
                "source_type": NORMALIZED_SOURCE_TYPE,
                "source_row_no": row_no,
            })
        session.add_all(CompanyListingPeriod(**row) for row in normalized)

    return {
        "inserted": len(normalized),
        "raw_source_checksum": raw_checksum,
        "normalized_checksum": checksum,
        "source_type": NORMALIZED_SOURCE_TYPE,
        "transformation_version": transformation_version,
    }


def _stored_file_artifact_available(
    row: dict[str, object], *, storage_uri_key: str, size_key: str, checksum_key: str,
    availability_cache: dict[tuple[str, int, str], bool] | None = None,
) -> bool:
    """Verify a retained local artifact without treating origin URLs as receipts."""
    storage_uri = str(row.get(storage_uri_key) or "")
    expected_checksum = str(row.get(checksum_key) or "").lower()
    try:
        expected_size = int(row[size_key])
    except (TypeError, ValueError):
        return False
    cache_key = (storage_uri, expected_size, expected_checksum)
    if availability_cache is not None and cache_key in availability_cache:
        return availability_cache[cache_key]
    parsed = urlparse(storage_uri)
    if parsed.scheme != "file" or not parsed.path:
        available = False
    else:
        try:
            receipt_path = Path(unquote(parsed.path))
            payload = receipt_path.read_bytes()
        except OSError:
            available = False
        else:
            available = (
                len(payload) == expected_size
                and hashlib.sha256(payload).hexdigest() == expected_checksum
            )
    if availability_cache is not None:
        availability_cache[cache_key] = available
    return available


def _raw_receipt_available(
    row: dict[str, object], *, availability_cache: dict[tuple[str, int, str], bool]
) -> bool:
    return _stored_file_artifact_available(
        row,
        storage_uri_key="raw_source_storage_uri",
        size_key="raw_source_size_bytes",
        checksum_key="raw_source_checksum",
        availability_cache=availability_cache,
    )


def _normalized_artifact_available(
    row: dict[str, object], *, availability_cache: dict[tuple[str, int, str], bool]
) -> bool:
    return _stored_file_artifact_available(
        row,
        storage_uri_key="normalized_storage_uri",
        size_key="normalized_size_bytes",
        checksum_key="normalized_checksum",
        availability_cache=availability_cache,
    )


def _stored_provenance_is_well_formed(row: dict[str, object]) -> bool:
    parsed = urlparse(str(row.get("raw_source_uri") or ""))
    return (
        parsed.scheme == "https"
        and parsed.hostname in OFFICIAL_KRX_HOSTS
        and _CHECKSUM_RE.fullmatch(str(row.get("raw_source_checksum") or "")) is not None
        and _CHECKSUM_RE.fullmatch(str(row.get("normalized_checksum") or "")) is not None
        and str(row.get("transformation_version") or "")
        in SUPPORTED_TRANSFORMATION_VERSIONS
        and str(row.get("source_type") or "") == NORMALIZED_SOURCE_TYPE
    )


def _stored_row_invariants_are_valid(row: dict[str, object], *, stock_code: str) -> bool:
    """Recheck persisted facts before they can support an eligibility diagnosis."""
    as_of = row.get("as_of")
    retrieved_at = row.get("raw_source_retrieved_at")
    listed_from = row.get("listed_from")
    listed_to = row.get("listed_to")
    status = str(row.get("status") or "")
    if (
        not isinstance(as_of, date)
        or isinstance(as_of, datetime)
        or not isinstance(retrieved_at, datetime)
        or str(row.get("stock_code") or "") != stock_code
        or as_of > retrieved_at.date()
    ):
        return False
    if status == "verified":
        return (
            isinstance(listed_from, date)
            and listed_from <= as_of
            and (listed_to is None or (
                isinstance(listed_to, date)
                and listed_from <= listed_to <= as_of
            ))
        )
    return status in {"unknown", "conflict"} and listed_from is None and listed_to is None


def listing_eligibility_snapshot(
    coverage_year: int,
    *,
    session_scope=get_session,
) -> dict[str, object]:
    """Return non-binding current-population diagnostics for listing evidence."""
    year_start = date(int(coverage_year), 1, 1)
    year_end = date(int(coverage_year), 12, 31)
    with session_scope() as session:
        bind = session.get_bind()
        core_company_stocks = {
            str(corp_code): str(stock_code)
            for corp_code, stock_code in session.query(
                Company.corp_code, Company.stock_code
            ).filter(
                Company.stock_code.isnot(None),
                Company.market.in_(("KOSPI", "KOSDAQ")),
            ).all()
        }
        core_codes = set(core_company_stocks)
        inspector = inspect(bind)
        required_provenance_columns = {
            "raw_source_uri", "raw_source_checksum", "raw_source_retrieved_at",
            "raw_source_storage_uri", "raw_source_size_bytes",
            "normalized_checksum", "normalized_storage_uri",
            "normalized_size_bytes", "transformation_version",
        }
        if (
            "company_listing_periods" not in inspector.get_table_names()
            or not required_provenance_columns.issubset({
                column["name"]
                for column in inspector.get_columns("company_listing_periods")
            })
        ):
            return {
                "policy": "diagnostic_only_current_core_denominator",
                "full_year_rule": FULL_YEAR_RULE,
                "coverage_year": int(coverage_year),
                "current_core_population": len(core_codes),
                "verified_full_year": 0,
                "verified_partial_year": 0,
                "unknown": 0,
                "conflict": 0,
                "uncovered": len(core_codes),
                "raw_receipt_available": 0,
                "raw_receipt_unavailable": 0,
                "normalized_artifact_available": 0,
                "normalized_artifact_unavailable": 0,
                "source_types": [],
                "source_table_available": False,
            }
        rows = [
            dict(row)
            for row in session.execute(select(
                CompanyListingPeriod.corp_code,
                CompanyListingPeriod.stock_code,
                CompanyListingPeriod.status,
                CompanyListingPeriod.listed_from,
                CompanyListingPeriod.listed_to,
                CompanyListingPeriod.source_type,
                CompanyListingPeriod.market,
                CompanyListingPeriod.as_of,
                CompanyListingPeriod.raw_source_uri,
                CompanyListingPeriod.raw_source_checksum,
                CompanyListingPeriod.raw_source_retrieved_at,
                CompanyListingPeriod.raw_source_storage_uri,
                CompanyListingPeriod.raw_source_size_bytes,
                CompanyListingPeriod.normalized_checksum,
                CompanyListingPeriod.normalized_storage_uri,
                CompanyListingPeriod.normalized_size_bytes,
                CompanyListingPeriod.transformation_version,
            ).where(
                CompanyListingPeriod.corp_code.in_(core_codes),
                CompanyListingPeriod.as_of >= year_end,
            )).mappings().all()
        ] if core_codes else []

    by_company: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        by_company.setdefault(str(row["corp_code"]), []).append(row)
    counts = {
        "verified_full_year": 0,
        "verified_partial_year": 0,
        "unknown": 0,
        "conflict": 0,
        "uncovered": 0,
        "raw_receipt_available": 0,
        "raw_receipt_unavailable": 0,
        "normalized_artifact_available": 0,
        "normalized_artifact_unavailable": 0,
    }
    raw_receipt_cache: dict[tuple[str, int, str], bool] = {}
    normalized_artifact_cache: dict[tuple[str, int, str], bool] = {}
    for corp_code in core_codes:
        evidence = by_company.get(corp_code, [])
        if evidence:
            latest_as_of = max(row["as_of"] for row in evidence)
            evidence = [row for row in evidence if row["as_of"] == latest_as_of]
        statuses = {str(row["status"]) for row in evidence}
        verified = [row for row in evidence if row["status"] == "verified"]
        receipt_available = bool(evidence) and all(
            _raw_receipt_available(row, availability_cache=raw_receipt_cache)
            for row in evidence
        )
        normalized_available = bool(evidence) and all(
            _normalized_artifact_available(
                row, availability_cache=normalized_artifact_cache
            )
            for row in evidence
        )
        if evidence:
            counts[
                "raw_receipt_available" if receipt_available else "raw_receipt_unavailable"
            ] += 1
            counts[
                "normalized_artifact_available"
                if normalized_available
                else "normalized_artifact_unavailable"
            ] += 1
        verified_signatures = {
            (row["market"], row["listed_from"], row["listed_to"])
            for row in verified
        }
        if (
            "conflict" in statuses
            or len(verified_signatures) > 1
            or not all(_stored_provenance_is_well_formed(row) for row in evidence)
            or not all(
                _stored_row_invariants_are_valid(
                    row, stock_code=core_company_stocks[corp_code]
                )
                for row in evidence
            )
            or (bool(evidence) and not normalized_available)
            or (bool(verified) and not receipt_available)
        ):
            counts["conflict"] += 1
            continue
        if "unknown" in statuses:
            counts["unknown"] += 1
            continue
        full_year = any(
            row["status"] == "verified"
            and row["listed_from"] is not None
            and row["listed_from"] <= year_start
            and (row["listed_to"] is None or row["listed_to"] >= year_end)
            for row in evidence
        )
        if full_year:
            counts["verified_full_year"] += 1
        elif any(row["status"] == "verified" for row in evidence):
            counts["verified_partial_year"] += 1
        else:
            counts["uncovered"] += 1
    return {
        "policy": "diagnostic_only_current_core_denominator",
        "full_year_rule": FULL_YEAR_RULE,
        "coverage_year": int(coverage_year),
        "current_core_population": len(core_codes),
        **counts,
        "source_types": sorted({str(row["source_type"]) for row in rows}),
        "source_table_available": True,
    }
