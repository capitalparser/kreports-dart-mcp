"""Import provenance-bound historical KOSPI/KOSDAQ year-end memberships.

The normalized CSV is derived evidence.  Its manifest is a retained receipt
ledger: every raw KRX source used in the reconstruction must be present,
unchanged, and traceable to an official KRX URL before a membership row can be
persisted.  This deliberately fails closed rather than quietly returning to a
survivor-only population.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlparse

from sqlalchemy import select

from kreports.db.engine import get_session
from kreports.db.models import Company, CompanyYearListingMembership
from kreports.runtime import require_runtime_write


OFFICIAL_KRX_HOSTS = frozenset({"data.krx.co.kr", "global.krx.co.kr", "kind.krx.co.kr"})
MEMBERSHIP_MARKETS = frozenset({"KOSPI", "KOSDAQ"})
MEMBERSHIP_STATUSES = frozenset({"verified", "unknown", "conflict"})
EVIDENCE_BASES = frozenset({
    "current_open_interval",
    "krx_event_interval",
    "pre_1999_listed_delisted_after_window_start",
    "source_conflict",
    "source_gap",
})
VERIFIED_EVIDENCE_BASES = frozenset({
    "current_open_interval",
    "krx_event_interval",
    "pre_1999_listed_delisted_after_window_start",
})
MANIFEST_SCHEMA_VERSION = "krx-year-end-listing-membership-manifest-v1"
SUPPORTED_TRANSFORMATION_VERSIONS = frozenset({"krx-year-end-listing-membership-v1"})
_CHECKSUM_RE = re.compile(r"[0-9a-f]{64}")
_TRANSFORMATION_VERSION_RE = re.compile(r"[A-Za-z0-9._-]{1,80}")
_SNAPSHOT_COLUMNS = (
    "corp_code", "stock_code", "bsns_year", "market", "status",
    "evidence_basis", "as_of",
)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _validated_checksum(value: object, *, field: str) -> str:
    checksum = str(value or "").strip().lower()
    if _CHECKSUM_RE.fullmatch(checksum) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 hex digest")
    return checksum


def _parse_iso_date(value: object, *, field: str, row_no: int | None = None) -> date:
    try:
        parsed = date.fromisoformat(str(value or "").strip())
    except ValueError as exc:
        location = f" at row {row_no}" if row_no is not None else ""
        raise ValueError(f"{field} must be an ISO date{location}") from exc
    return parsed


def _parse_retrieved_at(value: object, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").strip())
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO datetime") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _read_file(path_value: str | Path | None, *, error: str) -> tuple[Path, bytes]:
    if path_value is None:
        raise ValueError(error)
    path = Path(path_value)
    if not path.is_file():
        raise ValueError(error)
    try:
        return path, path.read_bytes()
    except OSError as exc:
        raise ValueError(error) from exc


def _validate_official_uri(value: object, *, field: str) -> str:
    uri = str(value or "").strip()
    parsed = urlparse(uri)
    if parsed.scheme != "https" or parsed.hostname not in OFFICIAL_KRX_HOSTS:
        raise ValueError(f"{field} must be an official KRX https URI")
    return uri


def _file_uri_payload(storage_uri: object, *, error: str) -> bytes:
    parsed = urlparse(str(storage_uri or ""))
    if parsed.scheme != "file" or not parsed.path:
        raise ValueError(error)
    try:
        return Path(unquote(parsed.path)).read_bytes()
    except OSError as exc:
        raise ValueError(error) from exc


def _validate_manifest(
    manifest: object,
    *,
    manifest_as_of: date,
    normalized_checksum: str,
    transformation_version: str,
) -> int:
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be a JSON object")
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError("manifest schema_version is unsupported")
    if _parse_iso_date(manifest.get("as_of"), field="manifest as_of") != manifest_as_of:
        raise ValueError("manifest as_of is invalid")
    if _validated_checksum(
        manifest.get("normalized_checksum"), field="manifest normalized_checksum"
    ) != normalized_checksum:
        raise ValueError("manifest normalized checksum does not bind normalized artifact")
    if manifest.get("transformation_version") != transformation_version:
        raise ValueError("manifest transformation_version does not bind normalized artifact")
    receipts = manifest.get("raw_receipts")
    if not isinstance(receipts, list) or not receipts:
        raise ValueError("manifest raw_receipts must be a non-empty list")
    receipt_signatures: set[tuple[str, str]] = set()
    for receipt in receipts:
        if not isinstance(receipt, dict):
            raise ValueError("manifest raw receipt must be an object")
        _validate_official_uri(receipt.get("uri"), field="raw receipt uri")
        checksum = _validated_checksum(receipt.get("checksum"), field="raw receipt checksum")
        try:
            expected_size = int(receipt.get("size_bytes"))
        except (TypeError, ValueError) as exc:
            raise ValueError("raw receipt size_bytes must be a positive integer") from exc
        if expected_size < 1:
            raise ValueError("raw receipt size_bytes must be a positive integer")
        retrieved_at = _parse_retrieved_at(
            receipt.get("retrieved_at"), field="raw receipt retrieved_at"
        )
        if retrieved_at.date() < manifest_as_of:
            raise ValueError("raw receipt retrieved_at precedes manifest as_of")
        signature = (str(receipt.get("storage_uri") or ""), checksum)
        if signature in receipt_signatures:
            raise ValueError("manifest contains duplicate raw receipt")
        receipt_signatures.add(signature)
        payload = _file_uri_payload(
            receipt.get("storage_uri"), error="raw receipt artifact is required"
        )
        if len(payload) != expected_size:
            raise ValueError("raw receipt size mismatch")
        if _sha256(payload) != checksum:
            raise ValueError("raw receipt checksum mismatch")
    return len(receipts)


def _same_membership(existing: CompanyYearListingMembership, candidate: dict[str, object]) -> bool:
    fields = (
        "stock_code", "market", "status", "evidence_basis", "as_of",
        "manifest_checksum", "manifest_storage_uri", "manifest_size_bytes",
        "manifest_raw_receipt_count", "normalized_checksum",
        "normalized_storage_uri", "normalized_size_bytes",
        "transformation_version", "source_row_no",
    )
    return all(getattr(existing, field) == candidate[field] for field in fields)


def import_company_year_listing_membership_snapshot(
    normalized_path: str | Path,
    *,
    manifest_path: str | Path,
    manifest_checksum: str,
    normalized_checksum: str,
    transformation_version: str,
) -> dict[str, object]:
    """Persist a verified year-end population bound to all KRX raw receipts.

    The CSV schema is exactly ``corp_code,stock_code,bsns_year,market,status,
    evidence_basis,as_of``.  It is safe to rerun an unchanged, verified
    artifact; any differing assertion for the same company-year is rejected.
    """
    require_runtime_write("import company year listing memberships")
    expected_manifest_checksum = _validated_checksum(
        manifest_checksum, field="manifest_checksum"
    )
    expected_normalized_checksum = _validated_checksum(
        normalized_checksum, field="normalized_checksum"
    )
    if (
        _TRANSFORMATION_VERSION_RE.fullmatch(transformation_version) is None
        or transformation_version not in SUPPORTED_TRANSFORMATION_VERSIONS
    ):
        raise ValueError("transformation_version is unsupported")

    manifest_file, manifest_payload = _read_file(
        manifest_path, error="manifest artifact is required"
    )
    if _sha256(manifest_payload) != expected_manifest_checksum:
        raise ValueError("manifest checksum mismatch")
    try:
        manifest = json.loads(manifest_payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("manifest must be valid UTF-8 JSON") from exc
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be a JSON object")
    manifest_as_of = _parse_iso_date(manifest.get("as_of"), field="manifest as_of")

    normalized_file, normalized_payload = _read_file(
        normalized_path, error="normalized artifact is required"
    )
    if _sha256(normalized_payload) != expected_normalized_checksum:
        raise ValueError("normalized checksum mismatch")
    raw_receipt_count = _validate_manifest(
        manifest,
        manifest_as_of=manifest_as_of,
        normalized_checksum=expected_normalized_checksum,
        transformation_version=transformation_version,
    )
    try:
        reader = csv.DictReader(normalized_payload.decode("utf-8-sig").splitlines())
    except UnicodeDecodeError as exc:
        raise ValueError("membership snapshot must be UTF-8") from exc
    if tuple(reader.fieldnames or ()) != _SNAPSHOT_COLUMNS:
        raise ValueError("membership snapshot columns must match the documented contract")
    rows = list(reader)
    if not rows:
        raise ValueError("membership snapshot must contain at least one row")

    normalized: list[dict[str, object]] = []
    seen_company_years: set[tuple[str, int]] = set()
    seen_source_rows: set[int] = set()
    with get_session() as session:
        company_stock = {
            str(corp_code): str(stock_code)
            for corp_code, stock_code in session.execute(
                select(Company.corp_code, Company.stock_code).where(
                    Company.stock_code.isnot(None)
                )
            )
        }
        for row_no, row in enumerate(rows, start=2):
            corp_code = str(row.get("corp_code") or "").strip()
            stock_code = str(row.get("stock_code") or "").strip()
            try:
                bsns_year = int(str(row.get("bsns_year") or "").strip())
            except ValueError as exc:
                raise ValueError(f"bsns_year must be an integer at row {row_no}") from exc
            market = str(row.get("market") or "").strip().upper()
            status = str(row.get("status") or "").strip().lower()
            evidence_basis = str(row.get("evidence_basis") or "").strip()
            as_of = _parse_iso_date(row.get("as_of"), field="as_of", row_no=row_no)
            company_year = (corp_code, bsns_year)
            if company_year in seen_company_years:
                raise ValueError(f"duplicate company-year at row {row_no}")
            seen_company_years.add(company_year)
            if row_no in seen_source_rows:
                raise ValueError(f"duplicate normalized source row at row {row_no}")
            seen_source_rows.add(row_no)
            if company_stock.get(corp_code) != stock_code:
                raise ValueError(f"stock_code does not bind to corp_code at row {row_no}")
            if bsns_year < 1900 or bsns_year > 2100:
                raise ValueError(f"bsns_year is unsupported at row {row_no}")
            if market not in MEMBERSHIP_MARKETS:
                raise ValueError(f"market is unsupported at row {row_no}")
            if status not in MEMBERSHIP_STATUSES:
                raise ValueError(f"status is unsupported at row {row_no}")
            if evidence_basis not in EVIDENCE_BASES:
                raise ValueError(f"evidence_basis is unsupported at row {row_no}")
            if status == "verified" and evidence_basis not in VERIFIED_EVIDENCE_BASES:
                raise ValueError(
                    f"verified status requires verified evidence_basis at row {row_no}"
                )
            if as_of != manifest_as_of:
                raise ValueError(f"as_of does not bind to manifest at row {row_no}")
            if date(bsns_year, 12, 31) > as_of:
                raise ValueError(f"bsns_year is after as_of at row {row_no}")
            normalized.append({
                "corp_code": corp_code,
                "stock_code": stock_code,
                "bsns_year": bsns_year,
                "market": market,
                "status": status,
                "evidence_basis": evidence_basis,
                "as_of": as_of,
                "manifest_checksum": expected_manifest_checksum,
                "manifest_storage_uri": manifest_file.resolve().as_uri(),
                "manifest_size_bytes": len(manifest_payload),
                "manifest_raw_receipt_count": raw_receipt_count,
                "normalized_checksum": expected_normalized_checksum,
                "normalized_storage_uri": normalized_file.resolve().as_uri(),
                "normalized_size_bytes": len(normalized_payload),
                "transformation_version": transformation_version,
                "source_row_no": row_no,
            })

        inserted = 0
        reused = 0
        for candidate in normalized:
            existing = session.scalar(
                select(CompanyYearListingMembership).where(
                    CompanyYearListingMembership.corp_code == candidate["corp_code"],
                    CompanyYearListingMembership.bsns_year == candidate["bsns_year"],
                )
            )
            existing_source_row = session.scalar(
                select(CompanyYearListingMembership).where(
                    CompanyYearListingMembership.normalized_checksum
                    == candidate["normalized_checksum"],
                    CompanyYearListingMembership.source_row_no
                    == candidate["source_row_no"],
                )
            )
            if existing is not None:
                if not _same_membership(existing, candidate):
                    raise ValueError("company-year membership conflicts with persisted evidence")
                if existing_source_row is not None and existing_source_row.id != existing.id:
                    raise ValueError("normalized artifact row conflicts with persisted evidence")
                reused += 1
                continue
            if existing_source_row is not None:
                raise ValueError("normalized artifact row conflicts with persisted evidence")
            session.add(CompanyYearListingMembership(**candidate))
            inserted += 1

    year_market_counts: dict[str, dict[str, int]] = {}
    for candidate in normalized:
        year = str(candidate["bsns_year"])
        market = str(candidate["market"])
        market_counts = year_market_counts.setdefault(year, {})
        market_counts[market] = market_counts.get(market, 0) + 1
    year_market_counts = {
        year: {market: counts[market] for market in sorted(counts)}
        for year, counts in sorted(year_market_counts.items())
    }
    return {
        "inserted": inserted,
        "reused": reused,
        "manifest_checksum": expected_manifest_checksum,
        "normalized_checksum": expected_normalized_checksum,
        "transformation_version": transformation_version,
        "year_market_counts": year_market_counts,
    }
