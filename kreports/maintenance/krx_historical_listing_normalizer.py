"""Reconstruct year-end KOSPI/KOSDAQ membership from retained KIND receipts.

The output asserts company-year membership, not an invented exact listing date.
That distinction matters for companies listed before KIND's event-history window.
"""
from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
from io import StringIO
import json
from pathlib import Path
from typing import Iterable, Mapping, Sequence
from urllib.parse import urlparse

from kreports.maintenance.krx_listing_normalizer import (
    CORE_MARKETS,
    KrxListingNormalizationError,
    _HtmlTableParser,
    _decode_kind_document,
    _normalize_stock_code,
    _parse_kind_rows,
    _validate_company_corp_code,
    _validate_company_stock_code,
)


TRANSFORMATION_VERSION = "krx-year-end-listing-membership-v1"
MANIFEST_SCHEMA_VERSION = "krx-year-end-listing-membership-manifest-v1"
CSV_COLUMNS = (
    "corp_code",
    "stock_code",
    "bsns_year",
    "market",
    "status",
    "evidence_basis",
    "as_of",
)
_LISTING_HEADERS = {
    "company_name": "회사명",
    "stock_code": "종목코드",
    "event_date": "상장일",
    "event_type": "상장유형",
}
_DELISTING_HEADERS = {
    "company_name": "회사명",
    "stock_code": "종목코드",
    "event_date": "폐지일자",
}


class HistoricalListingNormalizationError(ValueError):
    """The retained receipts cannot prove a unique year-end population."""


@dataclass(frozen=True)
class HistoricalListingReceipt:
    """One market-specific KIND history export."""

    market: str
    payload: bytes


@dataclass(frozen=True)
class RawReceiptProvenance:
    """Retained local receipt metadata for the importer-facing manifest."""

    path: str | Path
    uri: str
    retrieved_at: datetime
    role: str
    market: str | None = None


@dataclass(frozen=True)
class NormalizedHistoricalListingResult:
    rows: list[dict[str, str]]
    csv_bytes: bytes
    summary: dict[str, object]


def build_historical_membership_manifest(
    result: NormalizedHistoricalListingResult,
    *,
    raw_receipts: Sequence[RawReceiptProvenance],
) -> bytes:
    """Bind every source consumed by the normalizer to one canonical manifest."""
    if not raw_receipts:
        raise HistoricalListingNormalizationError("raw receipt provenance is required")
    try:
        as_of = date.fromisoformat(str(result.summary["as_of"]))
    except (KeyError, ValueError) as exc:
        raise HistoricalListingNormalizationError("result as_of is invalid") from exc
    if result.summary.get("transformation_version") != TRANSFORMATION_VERSION:
        raise HistoricalListingNormalizationError("result transformation version is unsupported")
    normalized_checksum = hashlib.sha256(result.csv_bytes).hexdigest()
    if result.summary.get("normalized_checksum") != normalized_checksum:
        raise HistoricalListingNormalizationError("result normalized checksum is inconsistent")

    manifest_receipts: list[dict[str, object]] = []
    actual_digests: list[str] = []
    signatures: set[tuple[str, str]] = set()
    for receipt in raw_receipts:
        path = Path(receipt.path)
        if not path.is_file():
            raise HistoricalListingNormalizationError("raw receipt artifact is required")
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise HistoricalListingNormalizationError("raw receipt artifact is required") from exc
        parsed = urlparse(receipt.uri)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"data.krx.co.kr", "global.krx.co.kr", "kind.krx.co.kr"}
        ):
            raise HistoricalListingNormalizationError("raw receipt URI must be official KRX HTTPS")
        if receipt.retrieved_at.tzinfo is None or receipt.retrieved_at.utcoffset() is None:
            raise HistoricalListingNormalizationError("raw receipt retrieved_at must be timezone-aware")
        retrieved_at = receipt.retrieved_at.astimezone(timezone.utc)
        if retrieved_at.date() < as_of:
            raise HistoricalListingNormalizationError("raw receipt retrieved_at precedes as_of")
        role = str(receipt.role or "").strip()
        if role not in {"current_listing", "listing_event", "delisting_event"}:
            raise HistoricalListingNormalizationError("raw receipt role is unsupported")
        market = str(receipt.market or "").strip().upper() or None
        if role == "current_listing" and market is not None:
            raise HistoricalListingNormalizationError("current listing receipt must not assert one market")
        if role != "current_listing" and market not in CORE_MARKETS:
            raise HistoricalListingNormalizationError("history receipt must assert a core market")
        checksum = hashlib.sha256(payload).hexdigest()
        storage_uri = path.resolve().as_uri()
        signature = (storage_uri, checksum)
        if signature in signatures:
            raise HistoricalListingNormalizationError("duplicate raw receipt provenance")
        signatures.add(signature)
        actual_digests.append(checksum)
        manifest_receipts.append({
            "uri": receipt.uri,
            "storage_uri": storage_uri,
            "checksum": checksum,
            "size_bytes": len(payload),
            "retrieved_at": retrieved_at.isoformat(),
            "role": role,
            "market": market,
        })

    expected_digests = result.summary.get("source_receipt_checksums")
    if not isinstance(expected_digests, list) or Counter(actual_digests) != Counter(
        str(value) for value in expected_digests
    ):
        raise HistoricalListingNormalizationError(
            "raw receipt provenance does not bind every normalizer input"
        )
    manifest_receipts.sort(
        key=lambda row: (
            str(row["role"]), str(row["market"] or ""), str(row["storage_uri"])
        )
    )
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "as_of": as_of.isoformat(),
        "normalized_checksum": normalized_checksum,
        "transformation_version": TRANSFORMATION_VERSION,
        "raw_receipts": manifest_receipts,
        "reconstruction": {
            "event_history_from": result.summary.get("event_history_from"),
            "years": result.summary.get("years"),
            "row_count": result.summary.get("row_count"),
            "year_market_counts": result.summary.get("year_market_counts"),
            "pre_1999_membership_count": result.summary.get(
                "pre_event_history_member_count"
            ),
            "duplicate_company_year_count": result.summary.get(
                "duplicate_company_year_count"
            ),
        },
    }
    return (
        json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _company_value(company: object, field: str) -> object:
    if isinstance(company, Mapping):
        return company.get(field)
    return getattr(company, field, None)


def _company_by_stock(companies: Iterable[object]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    corp_codes: set[str] = set()
    for company in companies:
        corp_code = _validate_company_corp_code(_company_value(company, "corp_code"))
        stock_code = _validate_company_stock_code(_company_value(company, "stock_code"))
        if corp_code in corp_codes:
            raise HistoricalListingNormalizationError("duplicate corp_code in company master")
        if stock_code in result:
            raise HistoricalListingNormalizationError("duplicate stock_code in company master")
        corp_codes.add(corp_code)
        result[stock_code] = {"corp_code": corp_code, "stock_code": stock_code}
    if not result:
        raise HistoricalListingNormalizationError("company master is empty")
    return result


def _parse_date(raw: str, *, field: str, row_no: int, as_of: date) -> date:
    try:
        value = date.fromisoformat(raw.strip().replace(".", "-").replace("/", "-"))
    except ValueError as exc:
        raise HistoricalListingNormalizationError(
            f"invalid {field} at row {row_no}"
        ) from exc
    if value > as_of:
        raise HistoricalListingNormalizationError(f"{field} is after as_of at row {row_no}")
    return value


def _parse_history_table(
    payload: bytes,
    *,
    headers: Mapping[str, str],
    as_of: date,
) -> list[dict[str, object]]:
    try:
        document = _decode_kind_document(payload)
    except KrxListingNormalizationError as exc:
        raise HistoricalListingNormalizationError("KIND history receipt could not be parsed") from exc
    if "결과값이 없습니다." in document:
        if document.count("결과값이 없습니다.") != 1 or not all(
            label in document for label in headers.values()
        ):
            raise HistoricalListingNormalizationError(
                "empty KIND history receipt has an invalid header contract"
            )
        return []
    try:
        parser = _HtmlTableParser()
        parser.feed(document)
        parser.close()
        parser.finalize()
    except Exception as exc:
        raise HistoricalListingNormalizationError("KIND history receipt could not be parsed") from exc

    matches: list[tuple[list[list[str]], dict[str, int]]] = []
    for table in parser.tables:
        if not table:
            continue
        compact_headers = ["".join(value.split()) for value in table[0]]
        indexes: dict[str, int] = {}
        for key, label in headers.items():
            found = [index for index, value in enumerate(compact_headers) if value == label]
            if len(found) == 1:
                indexes[key] = found[0]
        if len(indexes) == len(headers):
            matches.append((table, indexes))
    if len(matches) != 1:
        raise HistoricalListingNormalizationError(
            "required KIND history columns are missing or ambiguous"
        )

    table, indexes = matches[0]
    width = len(table[0])
    rows: list[dict[str, object]] = []
    for row_no, source_row in enumerate(table[1:], start=2):
        if len(source_row) != width:
            raise HistoricalListingNormalizationError(
                f"KIND history row {row_no} does not match header length"
            )
        company_name = source_row[indexes["company_name"]].strip()
        if not company_name:
            raise HistoricalListingNormalizationError(f"company_name is blank at row {row_no}")
        row: dict[str, object] = {
            "company_name": company_name,
            "stock_code": _normalize_stock_code(source_row[indexes["stock_code"]]),
            "event_date": _parse_date(
                source_row[indexes["event_date"]],
                field="event_date",
                row_no=row_no,
                as_of=as_of,
            ),
        }
        if "event_type" in indexes:
            event_type = " ".join(source_row[indexes["event_type"]].split())
            if not event_type:
                raise HistoricalListingNormalizationError(f"event_type is blank at row {row_no}")
            row["event_type"] = event_type
        rows.append(row)
    return rows


def _validate_receipt(receipt: HistoricalListingReceipt) -> str:
    market = str(receipt.market or "").strip().upper()
    if market not in CORE_MARKETS:
        raise HistoricalListingNormalizationError("history receipt market is unsupported")
    if not isinstance(receipt.payload, bytes):
        raise HistoricalListingNormalizationError("history receipt payload must be bytes")
    return market


def _csv_bytes(rows: Sequence[Mapping[str, str]]) -> bytes:
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def normalize_krx_year_end_memberships(
    *,
    current_listing_bytes: bytes,
    listing_receipts: Sequence[HistoricalListingReceipt],
    delisting_receipts: Sequence[HistoricalListingReceipt],
    companies: Iterable[object],
    years: Sequence[int],
    event_history_from: date,
    as_of: date,
) -> NormalizedHistoricalListingResult:
    """Produce one verified row for each company listed at each year end.

    Closed intervals without a matching listing event are usable only when the
    requested year end is after the declared exhaustive event-history start.
    Their evidence basis records that bounded inference; no fake date is stored.
    """
    if not isinstance(as_of, date) or isinstance(as_of, datetime):
        raise HistoricalListingNormalizationError("as_of must be a date")
    if not isinstance(event_history_from, date) or isinstance(event_history_from, datetime):
        raise HistoricalListingNormalizationError("event_history_from must be a date")
    normalized_years = sorted(set(years))
    if not normalized_years or any(
        not isinstance(year, int) or year < 1999 or date(year, 12, 31) > as_of
        for year in normalized_years
    ):
        raise HistoricalListingNormalizationError("years must be completed years from 1999")
    if event_history_from > date(normalized_years[0], 12, 31):
        raise HistoricalListingNormalizationError(
            "event history must begin before the earliest requested year end"
        )

    company_by_stock = _company_by_stock(companies)
    try:
        current_rows = _parse_kind_rows(current_listing_bytes, as_of=as_of)
    except KrxListingNormalizationError as exc:
        raise HistoricalListingNormalizationError(str(exc)) from exc

    listing_events: dict[tuple[str, str], set[date]] = {}
    receipt_digests: list[str] = [hashlib.sha256(current_listing_bytes).hexdigest()]
    for receipt in listing_receipts:
        market = _validate_receipt(receipt)
        receipt_digests.append(hashlib.sha256(receipt.payload).hexdigest())
        for row in _parse_history_table(
            receipt.payload, headers=_LISTING_HEADERS, as_of=as_of
        ):
            event_type = str(row["event_type"])
            if "폐지" in event_type:
                continue
            if not event_type.startswith(("신규상장", "재상장", "이전상장")):
                raise HistoricalListingNormalizationError(
                    f"unsupported listing event type: {event_type}"
                )
            listing_events.setdefault((str(row["stock_code"]), market), set()).add(
                row["event_date"]  # type: ignore[arg-type]
            )

    # (stock, market, start, end-exclusive, basis)
    intervals: set[tuple[str, str, date | None, date | None, str]] = set()
    for _name, stock_code, market, listed_from_raw in current_rows:
        if market not in CORE_MARKETS:
            continue
        listed_from = date.fromisoformat(listed_from_raw)
        if listed_from > date(normalized_years[-1], 12, 31):
            continue
        if stock_code not in company_by_stock:
            raise HistoricalListingNormalizationError(
                f"current listing stock_code {stock_code} does not bind to company master"
            )
        intervals.add((
            stock_code,
            market,
            listed_from,
            None,
            "current_open_interval",
        ))

    pre_history_intervals = 0
    latest_year_end = date(normalized_years[-1], 12, 31)
    earliest_year_end = date(normalized_years[0], 12, 31)
    for receipt in delisting_receipts:
        market = _validate_receipt(receipt)
        receipt_digests.append(hashlib.sha256(receipt.payload).hexdigest())
        for row in _parse_history_table(
            receipt.payload, headers=_DELISTING_HEADERS, as_of=as_of
        ):
            delisted_on = row["event_date"]
            assert isinstance(delisted_on, date)
            if delisted_on <= earliest_year_end or event_history_from > latest_year_end:
                continue
            stock_code = str(row["stock_code"])
            if stock_code not in company_by_stock:
                raise HistoricalListingNormalizationError(
                    f"delisted stock_code {stock_code} does not bind to company master"
                )
            prior_events = [
                value
                for value in listing_events.get((stock_code, market), set())
                if value <= delisted_on
            ]
            if prior_events:
                listed_from: date | None = max(prior_events)
                basis = "krx_event_interval"
            else:
                listed_from = None
                basis = "pre_1999_listed_delisted_after_window_start"
                pre_history_intervals += 1
            intervals.add((stock_code, market, listed_from, delisted_on, basis))

    output_rows: list[dict[str, str]] = []
    duplicate_company_year_count = 0
    for year in normalized_years:
        year_end = date(year, 12, 31)
        memberships: dict[str, list[tuple[str, str]]] = {}
        for stock_code, market, listed_from, listed_to, basis in intervals:
            starts_before = listed_from is None or listed_from <= year_end
            ends_after = listed_to is None or year_end < listed_to
            if starts_before and ends_after:
                memberships.setdefault(stock_code, []).append((market, basis))
        for stock_code, options in sorted(memberships.items()):
            unique_options = sorted(set(options))
            markets = {market for market, _basis in unique_options}
            if len(markets) != 1:
                duplicate_company_year_count += 1
                raise HistoricalListingNormalizationError(
                    f"stock_code {stock_code} belongs to multiple markets at {year} year end"
                )
            # Multiple receipts may prove the same market. Prefer the exact/open
            # basis over the bounded pre-history inference.
            basis_rank = {
                "current_open_interval": 0,
                "krx_event_interval": 1,
                "pre_1999_listed_delisted_after_window_start": 2,
            }
            market, basis = min(unique_options, key=lambda item: basis_rank[item[1]])
            company = company_by_stock[stock_code]
            output_rows.append({
                "corp_code": company["corp_code"],
                "stock_code": stock_code,
                "bsns_year": str(year),
                "market": market,
                "status": "verified",
                "evidence_basis": basis,
                "as_of": as_of.isoformat(),
            })

    output_rows.sort(key=lambda row: (int(row["bsns_year"]), row["corp_code"]))
    artifact = _csv_bytes(output_rows)
    year_market_counts = {
        str(year): {
            market: sum(
                row["bsns_year"] == str(year) and row["market"] == market
                for row in output_rows
            )
            for market in sorted(CORE_MARKETS)
        }
        for year in normalized_years
    }
    return NormalizedHistoricalListingResult(
        rows=output_rows,
        csv_bytes=artifact,
        summary={
            "transformation_version": TRANSFORMATION_VERSION,
            "as_of": as_of.isoformat(),
            "event_history_from": event_history_from.isoformat(),
            "years": normalized_years,
            "row_count": len(output_rows),
            "year_market_counts": year_market_counts,
            "pre_event_history_member_count": sum(
                row["evidence_basis"]
                == "pre_1999_listed_delisted_after_window_start"
                for row in output_rows
            ),
            "pre_event_history_interval_count": pre_history_intervals,
            "duplicate_company_year_count": duplicate_company_year_count,
            "normalized_checksum": hashlib.sha256(artifact).hexdigest(),
            "normalized_size_bytes": len(artifact),
            "source_receipt_checksums": sorted(receipt_digests),
            "source_receipt_count": len(receipt_digests),
        },
    )
