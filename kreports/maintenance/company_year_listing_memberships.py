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

from sqlalchemy import delete, select

from kreports.db.engine import get_session
from kreports.db.models import Company, CompanyYearListingMembership
from kreports.maintenance.krx_request_receipt_ledger import (
    canonical_request,
    load_verified_request_receipt_ledger,
)
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
MANIFEST_SCHEMA_VERSION = "krx-year-end-listing-membership-manifest-v2"
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
) -> tuple[list[dict[str, object]], dict[str, object]]:
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
    reconstruction = manifest.get("reconstruction")
    if not isinstance(reconstruction, dict):
        raise ValueError("manifest reconstruction contract is required")
    years = reconstruction.get("years")
    if (
        not isinstance(years, list)
        or not years
        or any(not isinstance(year, int) or year < 1999 for year in years)
        or sorted(set(years)) != years
    ):
        raise ValueError("manifest reconstruction years are invalid")
    event_history_from = _parse_iso_date(
        reconstruction.get("event_history_from"),
        field="manifest event_history_from",
    )
    if event_history_from > date(years[0], 12, 31):
        raise ValueError("manifest event history does not cover requested years")
    year_market_counts = reconstruction.get("year_market_counts")
    if not isinstance(year_market_counts, dict) or set(year_market_counts) != {
        str(year) for year in years
    }:
        raise ValueError("manifest year_market_counts are invalid")
    declared_row_count = 0
    for year in years:
        counts = year_market_counts.get(str(year))
        if not isinstance(counts, dict) or set(counts) != MEMBERSHIP_MARKETS:
            raise ValueError("manifest year_market_counts must cover both core markets")
        for count in counts.values():
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise ValueError("manifest year_market_counts are invalid")
            declared_row_count += count
    if reconstruction.get("row_count") != declared_row_count:
        raise ValueError("manifest reconstruction row_count is inconsistent")
    if reconstruction.get("duplicate_company_year_count") != 0:
        raise ValueError("manifest reconstruction contains duplicate company-years")

    receipts = manifest.get("raw_receipts")
    if not isinstance(receipts, list) or not receipts:
        raise ValueError("manifest raw_receipts must be a non-empty list")
    receipt_signatures: set[tuple[str, str]] = set()
    validated_receipts: list[dict[str, object]] = []
    validated_ledgers: dict[str, dict[str, object]] = {}
    for receipt in receipts:
        if not isinstance(receipt, dict):
            raise ValueError("manifest raw receipt must be an object")
        uri = _validate_official_uri(receipt.get("uri"), field="raw receipt uri")
        role = str(receipt.get("role") or "").strip()
        if role not in {"current_listing", "listing_event", "delisting_event"}:
            raise ValueError("raw receipt role is unsupported")
        market = str(receipt.get("market") or "").strip().upper() or None
        parsed_uri = urlparse(uri)
        expected_path = {
            "current_listing": "/corpgeneral/corpList.do",
            "listing_event": "/listinvstg/listingcompany.do",
            "delisting_event": "/investwarn/delcompany.do",
        }[role]
        if parsed_uri.hostname != "kind.krx.co.kr" or parsed_uri.path != expected_path:
            raise ValueError("raw receipt endpoint does not match role")
        if role == "current_listing":
            if market is not None:
                raise ValueError("current listing receipt must not assert a market")
            window_from = window_to = None
        else:
            if market not in MEMBERSHIP_MARKETS:
                raise ValueError("history raw receipt must assert a core market")
            window_from = _parse_iso_date(
                receipt.get("window_from"), field="raw receipt window_from"
            )
            window_to = _parse_iso_date(
                receipt.get("window_to"), field="raw receipt window_to"
            )
            if window_from > window_to:
                raise ValueError("raw receipt window is invalid")
        _uri, expected_method, expected_request = canonical_request(
            role,
            market=market,
            window_from=window_from,
            window_to=window_to,
        )
        if (
            receipt.get("request_method") != expected_method
            or receipt.get("request_params") != expected_request
        ):
            raise ValueError("raw receipt request parameters do not match role and market")
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
        ledger_uri = str(receipt.get("request_ledger_storage_uri") or "")
        ledger_payload = _file_uri_payload(
            ledger_uri, error="request receipt ledger artifact is required"
        )
        ledger_checksum = _validated_checksum(
            receipt.get("request_ledger_checksum"),
            field="request receipt ledger checksum",
        )
        try:
            ledger_size = int(receipt.get("request_ledger_size_bytes"))
        except (TypeError, ValueError) as exc:
            raise ValueError("request receipt ledger size is invalid") from exc
        if len(ledger_payload) != ledger_size or _sha256(ledger_payload) != ledger_checksum:
            raise ValueError("request receipt ledger is unavailable or tampered")
        if ledger_uri not in validated_ledgers:
            ledger_path = Path(unquote(urlparse(ledger_uri).path))
            validated_ledgers[ledger_uri] = load_verified_request_receipt_ledger(
                ledger_path
            )
        captured = validated_ledgers[ledger_uri].get(
            str(receipt.get("storage_uri") or "")
        )
        if captured is None or (
            getattr(captured, "role") != role
            or getattr(captured, "market") != market
            or getattr(captured, "window_from") != window_from
            or getattr(captured, "window_to") != window_to
            or getattr(captured, "uri") != uri
            or getattr(captured, "request_method") != expected_method
            or getattr(captured, "request_params") != expected_request
            or getattr(captured, "response_checksum") != checksum
            or getattr(captured, "retrieved_at") != retrieved_at
        ):
            raise ValueError("raw receipt does not match captured request envelope")
        validated_receipts.append({
            **receipt,
            "role": role,
            "market": market,
            "window_from": window_from,
            "window_to": window_to,
            "payload": payload,
        })

    current_receipts = [
        receipt for receipt in validated_receipts
        if receipt["role"] == "current_listing"
    ]
    if len(current_receipts) != 1:
        raise ValueError("manifest requires exactly one current listing receipt")

    def require_coverage(role: str, required_from: date) -> None:
        for market in sorted(MEMBERSHIP_MARKETS):
            windows = sorted(
                (receipt["window_from"], receipt["window_to"])
                for receipt in validated_receipts
                if receipt["role"] == role and receipt["market"] == market
            )
            cursor = required_from
            for window_from, window_to in windows:
                assert isinstance(window_from, date) and isinstance(window_to, date)
                if window_to < cursor:
                    continue
                if window_from > cursor:
                    break
                cursor = date.fromordinal(window_to.toordinal() + 1)
                if cursor > manifest_as_of:
                    break
            if cursor <= manifest_as_of:
                raise ValueError(f"manifest has incomplete {role} coverage for {market}")

    require_coverage("listing_event", event_history_from)
    require_coverage("delisting_event", date(years[0], 1, 1))
    return validated_receipts, reconstruction


def _same_membership(existing: CompanyYearListingMembership, candidate: dict[str, object]) -> bool:
    fields = (
        "stock_code", "market", "status", "evidence_basis", "as_of",
        "manifest_checksum", "manifest_storage_uri", "manifest_size_bytes",
        "manifest_raw_receipt_count", "normalized_checksum",
        "normalized_storage_uri", "normalized_size_bytes",
        "transformation_version", "source_row_no",
    )
    return all(getattr(existing, field) == candidate[field] for field in fields)


def validate_retained_membership_artifacts(
    evidence_sets: list[dict[str, object]],
) -> None:
    """Recheck retained artifacts, their semantics, and persisted row binding."""
    if not evidence_sets:
        raise ValueError("membership evidence set is unavailable")
    checked_raw: set[tuple[str, str, int]] = set()
    for evidence in evidence_sets:
        manifest_payload = _file_uri_payload(
            evidence.get("manifest_storage_uri"),
            error="membership manifest artifact is unavailable",
        )
        try:
            manifest_size = int(evidence.get("manifest_size_bytes"))
        except (TypeError, ValueError) as exc:
            raise ValueError("membership manifest metadata is invalid") from exc
        manifest_checksum = _validated_checksum(
            evidence.get("manifest_checksum"), field="manifest checksum"
        )
        if len(manifest_payload) != manifest_size or _sha256(manifest_payload) != manifest_checksum:
            raise ValueError("membership manifest artifact is unavailable or tampered")
        try:
            manifest = json.loads(manifest_payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("membership manifest artifact is invalid") from exc
        if not isinstance(manifest, dict) or not isinstance(manifest.get("raw_receipts"), list):
            raise ValueError("membership manifest raw receipts are unavailable")

        normalized_payload = _file_uri_payload(
            evidence.get("normalized_storage_uri"),
            error="membership normalized artifact is unavailable",
        )
        try:
            normalized_size = int(evidence.get("normalized_size_bytes"))
        except (TypeError, ValueError) as exc:
            raise ValueError("membership normalized metadata is invalid") from exc
        normalized_checksum = _validated_checksum(
            evidence.get("normalized_checksum"), field="normalized checksum"
        )
        if (
            len(normalized_payload) != normalized_size
            or _sha256(normalized_payload) != normalized_checksum
            or manifest.get("normalized_checksum") != normalized_checksum
        ):
            raise ValueError("membership normalized artifact is unavailable or tampered")

        evidence_as_of = evidence.get("as_of")
        if isinstance(evidence_as_of, datetime):
            raise ValueError("membership evidence as_of is invalid")
        if not isinstance(evidence_as_of, date):
            evidence_as_of = _parse_iso_date(evidence_as_of, field="membership as_of")
        transformation_version = str(
            evidence.get("transformation_version") or ""
        ).strip()
        if transformation_version not in SUPPORTED_TRANSFORMATION_VERSIONS:
            raise ValueError("membership transformation version is unsupported")
        validated_receipts, reconstruction = _validate_manifest(
            manifest,
            manifest_as_of=evidence_as_of,
            normalized_checksum=normalized_checksum,
            transformation_version=transformation_version,
        )
        try:
            declared_receipt_count = int(evidence.get("manifest_raw_receipt_count"))
        except (TypeError, ValueError) as exc:
            raise ValueError("membership raw receipt count is invalid") from exc
        if declared_receipt_count != len(validated_receipts):
            raise ValueError("membership raw receipt count does not match manifest")

        raw_receipts = manifest["raw_receipts"]
        if not raw_receipts:
            raise ValueError("membership manifest raw receipts are unavailable")
        for receipt in raw_receipts:
            if not isinstance(receipt, dict):
                raise ValueError("membership manifest raw receipt is invalid")
            checksum = _validated_checksum(
                receipt.get("checksum"), field="raw receipt checksum"
            )
            try:
                size = int(receipt.get("size_bytes"))
            except (TypeError, ValueError) as exc:
                raise ValueError("membership raw receipt metadata is invalid") from exc
            key = (str(receipt.get("storage_uri") or ""), checksum, size)
            if key in checked_raw:
                continue
            payload = _file_uri_payload(
                receipt.get("storage_uri"),
                error="membership raw receipt artifact is unavailable",
            )
            if len(payload) != size or _sha256(payload) != checksum:
                raise ValueError("membership raw receipt artifact is unavailable or tampered")
            checked_raw.add(key)

        with get_session() as session:
            company_records = [
                {
                    "corp_code": str(corp_code),
                    "stock_code": str(stock_code),
                    "market": market,
                }
                for corp_code, stock_code, market in session.execute(
                    select(Company.corp_code, Company.stock_code, Company.market).where(
                        Company.stock_code.isnot(None)
                    )
                )
            ]
            persisted = [
                {
                    column: getattr(row, column)
                    for column in (
                        "corp_code", "stock_code", "bsns_year", "market",
                        "status", "evidence_basis", "as_of", "source_row_no",
                        "manifest_checksum", "manifest_storage_uri",
                        "manifest_size_bytes", "manifest_raw_receipt_count",
                        "normalized_storage_uri", "normalized_size_bytes",
                        "transformation_version",
                    )
                }
                for row in session.scalars(
                    select(CompanyYearListingMembership).where(
                        CompanyYearListingMembership.normalized_checksum
                        == normalized_checksum
                    )
                )
            ]

        from kreports.maintenance.krx_historical_listing_normalizer import (
            HistoricalListingReceipt,
            normalize_krx_year_end_memberships,
        )

        current_receipt = next(
            receipt for receipt in validated_receipts
            if receipt["role"] == "current_listing"
        )
        rederived = normalize_krx_year_end_memberships(
            current_listing_bytes=bytes(current_receipt["payload"]),
            listing_receipts=[
                HistoricalListingReceipt(
                    market=str(receipt["market"]),
                    payload=bytes(receipt["payload"]),
                    window_from=receipt["window_from"],
                    window_to=receipt["window_to"],
                )
                for receipt in validated_receipts
                if receipt["role"] == "listing_event"
            ],
            delisting_receipts=[
                HistoricalListingReceipt(
                    market=str(receipt["market"]),
                    payload=bytes(receipt["payload"]),
                    window_from=receipt["window_from"],
                    window_to=receipt["window_to"],
                )
                for receipt in validated_receipts
                if receipt["role"] == "delisting_event"
            ],
            companies=company_records,
            years=list(reconstruction["years"]),
            event_history_from=_parse_iso_date(
                reconstruction["event_history_from"],
                field="manifest event_history_from",
            ),
            as_of=evidence_as_of,
        )
        if rederived.csv_bytes != normalized_payload:
            raise ValueError(
                "membership normalized artifact does not match raw reconstruction"
            )
        expected_reconstruction = {
            "event_history_from": rederived.summary["event_history_from"],
            "years": rederived.summary["years"],
            "row_count": rederived.summary["row_count"],
            "year_market_counts": rederived.summary["year_market_counts"],
            "pre_1999_membership_count": rederived.summary[
                "pre_event_history_member_count"
            ],
            "duplicate_company_year_count": rederived.summary[
                "duplicate_company_year_count"
            ],
        }
        if reconstruction != expected_reconstruction:
            raise ValueError("membership reconstruction summary has drifted")

        expected_rows = {
            (
                row["corp_code"],
                row["stock_code"],
                int(row["bsns_year"]),
                row["market"],
                row["status"],
                row["evidence_basis"],
                date.fromisoformat(row["as_of"]),
                row_no,
            )
            for row_no, row in enumerate(rederived.rows, start=2)
        }
        actual_rows = {
            (
                row["corp_code"],
                row["stock_code"],
                row["bsns_year"],
                row["market"],
                row["status"],
                row["evidence_basis"],
                row["as_of"],
                row["source_row_no"],
            )
            for row in persisted
            if (
                row["manifest_checksum"] == manifest_checksum
                and row["manifest_storage_uri"] == evidence.get("manifest_storage_uri")
                and row["manifest_size_bytes"] == manifest_size
                and row["manifest_raw_receipt_count"] == declared_receipt_count
                and row["normalized_storage_uri"] == evidence.get("normalized_storage_uri")
                and row["normalized_size_bytes"] == normalized_size
                and row["transformation_version"] == transformation_version
            )
        }
        if actual_rows != expected_rows or len(persisted) != len(expected_rows):
            raise ValueError(
                "persisted membership rows do not match normalized reconstruction"
            )


def import_company_year_listing_membership_snapshot(
    normalized_path: str | Path,
    *,
    manifest_path: str | Path,
    manifest_checksum: str,
    normalized_checksum: str,
    transformation_version: str,
    replace_existing: bool = False,
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
    validated_receipts, reconstruction = _validate_manifest(
        manifest,
        manifest_as_of=manifest_as_of,
        normalized_checksum=expected_normalized_checksum,
        transformation_version=transformation_version,
    )
    raw_receipt_count = len(validated_receipts)
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
        company_records = [
            {
                "corp_code": str(corp_code),
                "stock_code": str(stock_code),
                "market": market,
            }
            for corp_code, stock_code, market in session.execute(
                select(Company.corp_code, Company.stock_code, Company.market).where(
                    Company.stock_code.isnot(None)
                )
            )
        ]
        company_stock = {
            record["corp_code"]: record["stock_code"]
            for record in company_records
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

        from kreports.maintenance.krx_historical_listing_normalizer import (
            HistoricalListingReceipt,
            normalize_krx_year_end_memberships,
        )

        current_receipt = next(
            receipt for receipt in validated_receipts
            if receipt["role"] == "current_listing"
        )
        listing_receipts = [
            HistoricalListingReceipt(
                market=str(receipt["market"]),
                payload=bytes(receipt["payload"]),
                window_from=receipt["window_from"],
                window_to=receipt["window_to"],
            )
            for receipt in validated_receipts
            if receipt["role"] == "listing_event"
        ]
        delisting_receipts = [
            HistoricalListingReceipt(
                market=str(receipt["market"]),
                payload=bytes(receipt["payload"]),
                window_from=receipt["window_from"],
                window_to=receipt["window_to"],
            )
            for receipt in validated_receipts
            if receipt["role"] == "delisting_event"
        ]
        rederived = normalize_krx_year_end_memberships(
            current_listing_bytes=bytes(current_receipt["payload"]),
            listing_receipts=listing_receipts,
            delisting_receipts=delisting_receipts,
            companies=company_records,
            years=list(reconstruction["years"]),
            event_history_from=_parse_iso_date(
                reconstruction["event_history_from"],
                field="manifest event_history_from",
            ),
            as_of=manifest_as_of,
        )
        if rederived.csv_bytes != normalized_payload:
            raise ValueError(
                "normalized membership artifact does not match raw receipt reconstruction"
            )
        expected_reconstruction = {
            "event_history_from": rederived.summary["event_history_from"],
            "years": rederived.summary["years"],
            "row_count": rederived.summary["row_count"],
            "year_market_counts": rederived.summary["year_market_counts"],
            "pre_1999_membership_count": rederived.summary[
                "pre_event_history_member_count"
            ],
            "duplicate_company_year_count": rederived.summary[
                "duplicate_company_year_count"
            ],
        }
        if reconstruction != expected_reconstruction:
            raise ValueError("manifest reconstruction summary does not match raw receipts")

        deleted = 0
        if replace_existing:
            deletion = session.execute(
                delete(CompanyYearListingMembership).where(
                    CompanyYearListingMembership.bsns_year.in_(
                        list(reconstruction["years"])
                    )
                )
            )
            deleted = int(deletion.rowcount or 0)
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
        "deleted": deleted,
        "reused": reused,
        "manifest_checksum": expected_manifest_checksum,
        "normalized_checksum": expected_normalized_checksum,
        "transformation_version": transformation_version,
        "year_market_counts": year_market_counts,
    }
