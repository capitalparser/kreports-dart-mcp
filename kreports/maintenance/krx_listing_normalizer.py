"""Strict, read-only normalization of KIND listing-company HTML-XLS exports.

This module deliberately does not import the listing-period importer: producing
an evidence artifact is separate from deciding whether to persist or consume
it.  Its only filesystem mutation is the explicit output writer.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
from html.parser import HTMLParser
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Iterable, Mapping, Sequence

from kreports.db.readonly_snapshot import (
    ReadonlySQLiteSnapshotUnavailable,
    open_checkpointed_readonly_sqlite,
)


TRANSFORMATION_VERSION = "krx-listing-normalize-v1"
CSV_COLUMNS = (
    "corp_code",
    "stock_code",
    "market",
    "listed_from",
    "listed_to",
    "status",
)
CORE_MARKETS = frozenset({"KOSPI", "KOSDAQ"})
_KRX_MARKETS = {"유가": "KOSPI", "코스닥": "KOSDAQ", "코넥스": "KONEX"}
_HEADER_ALIASES = {
    "company_name": frozenset({"회사명"}),
    "stock_code": frozenset({"종목코드"}),
    "market": frozenset({"시장구분", "시장"}),
    "listed_from": frozenset({"상장일", "상장일자"}),
}


class KrxListingNormalizationError(ValueError):
    """The raw KIND receipt or current-company input cannot be normalized."""


@dataclass(frozen=True)
class NormalizedListingResult:
    """Deterministic artifact and the metadata needed to bind it to its receipt."""

    rows: list[dict[str, str]]
    csv_bytes: bytes
    summary: dict[str, object]


class _HtmlTableParser(HTMLParser):
    """Small HTML table reader sufficient for the table-shaped KIND XLS export."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag == "table" and self._table is None:
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell_parts = []
        elif tag == "br" and self._cell_parts is not None:
            self._cell_parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self._cell_parts is not None:
            self._cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell_parts is not None and self._row is not None:
            self._row.append("".join(self._cell_parts).strip())
            self._cell_parts = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if self._row:
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None


def _normalize_corp_code(value: object) -> str:
    normalized = str(value or "").strip()
    if not normalized.isascii() or not normalized.isdigit() or len(normalized) > 8:
        raise KrxListingNormalizationError("corp_code must be an 8-digit numeric code")
    return normalized.zfill(8)


def _normalize_stock_code(value: object) -> str:
    """Canonicalize ASCII stock codes while preserving six-character alphanumerics.

    KIND and the company master currently use upper-case identifiers. Lowercase
    ASCII is explicitly canonicalized; non-ASCII and punctuation are rejected.
    """
    raw = str(value or "").strip()
    if not raw or not raw.isascii():
        raise KrxListingNormalizationError(
            "stock_code must be a 6-character uppercase alphanumeric code"
        )
    canonical = raw.upper()
    if canonical.isdigit():
        if len(canonical) > 6:
            raise KrxListingNormalizationError(
                "stock_code must be a 6-character uppercase alphanumeric code"
            )
        return canonical.zfill(6)
    if len(canonical) != 6 or any(
        not ("0" <= char <= "9" or "A" <= char <= "Z") for char in canonical
    ):
        raise KrxListingNormalizationError(
            "stock_code must be a 6-character uppercase alphanumeric code"
        )
    return canonical


def _company_value(company: object, field: str) -> object:
    if isinstance(company, Mapping):
        return company.get(field)
    return getattr(company, field, None)


def _normalize_companies(companies: Iterable[object]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    seen_corp_codes: set[str] = set()
    seen_stock_codes: set[str] = set()
    for company in companies:
        market = str(_company_value(company, "market") or "").strip().upper()
        if market not in CORE_MARKETS:
            continue
        corp_code = _normalize_corp_code(_company_value(company, "corp_code"))
        if corp_code in seen_corp_codes:
            raise KrxListingNormalizationError("duplicate corp_code in company records")
        seen_corp_codes.add(corp_code)
        stock_code = _normalize_stock_code(_company_value(company, "stock_code"))
        if stock_code in seen_stock_codes:
            raise KrxListingNormalizationError("duplicate stock_code in company records")
        seen_stock_codes.add(stock_code)
        normalized.append({
            "corp_code": corp_code,
            "stock_code": stock_code,
            "market": market,
        })
    return sorted(normalized, key=lambda row: row["corp_code"])


def _header_indexes(table: Sequence[Sequence[str]]) -> dict[str, int] | None:
    if not table:
        return None
    found: dict[str, int] = {}
    for index, header in enumerate(table[0]):
        compact = "".join(header.split())
        for field, aliases in _HEADER_ALIASES.items():
            if compact in aliases:
                if field in found:
                    raise KrxListingNormalizationError("required KIND columns are ambiguous")
                found[field] = index
    return found if set(found) == set(_HEADER_ALIASES) else None


def _parse_kind_rows(raw_bytes: bytes, *, as_of: date) -> list[tuple[str, str, str, str]]:
    try:
        document = raw_bytes.decode("euc-kr")
    except UnicodeDecodeError as exc:
        raise KrxListingNormalizationError("KIND HTML-XLS must be EUC-KR") from exc
    parser = _HtmlTableParser()
    try:
        parser.feed(document)
        parser.close()
    except Exception as exc:  # HTMLParser rarely raises, but malformed input must not leak through.
        raise KrxListingNormalizationError("KIND HTML-XLS could not be parsed") from exc
    matching = [(table, _header_indexes(table)) for table in parser.tables]
    matching = [(table, indexes) for table, indexes in matching if indexes is not None]
    if len(matching) != 1:
        raise KrxListingNormalizationError("required KIND columns are missing or ambiguous")
    table, indexes = matching[0]
    assert indexes is not None
    rows: list[tuple[str, str, str, str]] = []
    for row_no, source_row in enumerate(table[1:], start=2):
        if not any(cell.strip() for cell in source_row):
            continue
        if len(source_row) <= max(indexes.values()):
            raise KrxListingNormalizationError(f"KIND row {row_no} is shorter than its header")
        company_name = source_row[indexes["company_name"]].strip()
        if not company_name:
            raise KrxListingNormalizationError(f"company_name is blank at row {row_no}")
        stock_code = _normalize_stock_code(source_row[indexes["stock_code"]])
        raw_market = "".join(source_row[indexes["market"]].split())
        try:
            market = _KRX_MARKETS[raw_market]
        except KeyError as exc:
            raise KrxListingNormalizationError(f"unsupported KRX market at row {row_no}") from exc
        raw_date = source_row[indexes["listed_from"]].strip().replace(".", "-").replace("/", "-")
        try:
            listed_from = date.fromisoformat(raw_date)
        except ValueError as exc:
            raise KrxListingNormalizationError(f"invalid listed_from at row {row_no}") from exc
        if listed_from > as_of:
            raise KrxListingNormalizationError(f"listed_from is after as_of at row {row_no}")
        rows.append((company_name, stock_code, market, listed_from.isoformat()))
    return rows


def _csv_bytes(rows: Sequence[Mapping[str, str]]) -> bytes:
    from io import StringIO

    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def normalize_krx_listing_bytes(
    raw_bytes: bytes,
    companies: Iterable[object],
    *,
    as_of: date,
) -> NormalizedListingResult:
    """Create a CSV for every current KOSPI/KOSDAQ company without side effects."""
    if not isinstance(raw_bytes, bytes):
        raise KrxListingNormalizationError("raw KIND payload must be bytes")
    if not isinstance(as_of, date) or isinstance(as_of, datetime):
        raise KrxListingNormalizationError("as_of must be a date")
    current_companies = _normalize_companies(companies)
    source_rows = _parse_kind_rows(raw_bytes, as_of=as_of)
    source_by_stock: dict[str, set[tuple[str, str, str]]] = {}
    for company_name, stock_code, market, listed_from in source_rows:
        source_by_stock.setdefault(stock_code, set()).add((company_name, market, listed_from))

    known_stock_codes = {row["stock_code"] for row in current_companies}
    output_rows: list[dict[str, str]] = []
    for company in current_companies:
        source_options = source_by_stock.get(company["stock_code"], set())
        row = {
            "corp_code": company["corp_code"],
            "stock_code": company["stock_code"],
            "market": company["market"],
            "listed_from": "",
            "listed_to": "",
            "status": "unknown",
        }
        if len(source_options) > 1:
            row["status"] = "conflict"
        elif len(source_options) == 1:
            _company_name, source_market, listed_from = next(iter(source_options))
            if source_market == company["market"]:
                row["listed_from"] = listed_from
                row["status"] = "verified"
            else:
                row["status"] = "conflict"
        output_rows.append(row)
    artifact = _csv_bytes(output_rows)
    status_counts = {status: sum(row["status"] == status for row in output_rows) for status in ("conflict", "unknown", "verified")}
    return NormalizedListingResult(
        rows=output_rows,
        csv_bytes=artifact,
        summary={
            "raw_checksum": hashlib.sha256(raw_bytes).hexdigest(),
            "normalized_checksum": hashlib.sha256(artifact).hexdigest(),
            "row_count": len(output_rows),
            "status_counts": status_counts,
            "transformation_version": TRANSFORMATION_VERSION,
            "as_of": as_of.isoformat(),
            "unmatched_krx_stock_codes": sorted(set(source_by_stock) - known_stock_codes),
        },
    )


def normalize_krx_listing_path(
    raw_path: str | Path,
    companies: Iterable[object],
    *,
    as_of: date,
) -> NormalizedListingResult:
    """Read an explicit local KIND receipt and normalize it without mutation."""
    try:
        raw_bytes = Path(raw_path).read_bytes()
    except OSError as exc:
        raise KrxListingNormalizationError("raw KIND path must be a readable file") from exc
    return normalize_krx_listing_bytes(raw_bytes, companies, as_of=as_of)


def read_current_core_companies(db_path: str | Path) -> list[dict[str, str]]:
    """Read the current KOSPI/KOSDAQ population from an immutable SQLite snapshot."""
    try:
        path = Path(db_path).expanduser().resolve(strict=True)
    except OSError as exc:
        raise KrxListingNormalizationError("db path must be an existing file") from exc
    if not path.is_file():
        raise KrxListingNormalizationError("db path must be an existing file")
    try:
        connection = open_checkpointed_readonly_sqlite(path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        records = connection.execute(
            "SELECT corp_code, stock_code, market FROM companies "
            "WHERE market IN ('KOSPI', 'KOSDAQ') AND stock_code IS NOT NULL "
            "ORDER BY corp_code"
        ).fetchall()
    except ReadonlySQLiteSnapshotUnavailable as exc:
        raise KrxListingNormalizationError("db snapshot must be checkpointed") from exc
    except sqlite3.Error as exc:
        raise KrxListingNormalizationError("db path must contain a readable companies table") from exc
    finally:
        if "connection" in locals():
            connection.close()
    return _normalize_companies([dict(record) for record in records])


def write_normalized_listing_csv(output_path: str | Path, payload: bytes) -> None:
    """Atomically create an output artifact and never replace an existing path."""
    path = Path(output_path)
    if os.path.lexists(path):
        raise FileExistsError("output path already exists")
    if not path.parent.is_dir():
        raise FileNotFoundError("output directory must exist")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as temporary_file:
            temporary_file.write(payload)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.link(temporary_path, path)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
