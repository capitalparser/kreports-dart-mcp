"""Test-only builder for a fully replayable historical membership artifact."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
import hashlib
import json
from pathlib import Path

from kreports.db.models import CompanyYearListingMembership
from kreports.maintenance.krx_historical_listing_normalizer import (
    HistoricalListingReceipt,
    RawReceiptProvenance,
    TRANSFORMATION_VERSION,
    build_historical_membership_manifest,
    normalize_krx_year_end_memberships,
)
from kreports.maintenance.krx_request_receipt_ledger import (
    LEDGER_SCHEMA_VERSION,
    canonical_request,
)


def _xls(headers: list[str], rows: list[list[str]]) -> bytes:
    body = "".join(
        "<tr>" + "".join(f"<td>{value}</td>" for value in row) + "</tr>"
        for row in rows
    )
    return (
        "<html><head><meta charset='EUC-KR'></head><body><table><tr>"
        + "".join(f"<th>{header}</th>" for header in headers)
        + f"</tr>{body}</table></body></html>"
    ).encode("euc-kr")


def write_request_receipt_ledger(
    path: Path,
    receipts: list[dict[str, object]],
) -> Path:
    entries = []
    for receipt in receipts:
        response_path = Path(receipt["path"]).resolve()
        role = str(receipt["role"])
        market = receipt.get("market")
        window_from = receipt.get("window_from")
        window_to = receipt.get("window_to")
        uri, request_method, request_params = canonical_request(
            role,
            market=str(market) if market is not None else None,
            window_from=window_from if isinstance(window_from, date) else None,
            window_to=window_to if isinstance(window_to, date) else None,
        )
        payload = response_path.read_bytes()
        retrieved_at = receipt.get("retrieved_at")
        assert isinstance(retrieved_at, datetime)
        entries.append({
            "role": role,
            "market": market,
            "window_from": window_from.isoformat() if isinstance(window_from, date) else None,
            "window_to": window_to.isoformat() if isinstance(window_to, date) else None,
            "uri": uri,
            "request_method": request_method,
            "request_params": request_params,
            "response_storage_uri": response_path.as_uri(),
            "response_checksum": hashlib.sha256(payload).hexdigest(),
            "response_size_bytes": len(payload),
            "retrieved_at": retrieved_at.isoformat(),
        })
    path.write_text(json.dumps({
        "schema_version": LEDGER_SCHEMA_VERSION,
        "receipts": entries,
    }, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return path


def verified_membership(
    *,
    root: Path,
    corp_code: str,
    stock_code: str,
    year: int,
    market: str,
) -> tuple[CompanyYearListingMembership, Path]:
    """Return one DB row whose normalized artifact can be replayed from raw."""
    root.mkdir(parents=True, exist_ok=True)
    as_of = date(2026, 8, 10)
    event_history_from = date(1999, 1, 1)
    retrieved_at = datetime(2026, 8, 10, 1, tzinfo=UTC)
    current = _xls(
        ["회사명", "시장구분", "종목코드", "상장일"],
        [[corp_code, "유가" if market == "KOSPI" else "코스닥", stock_code, "2000-01-02"]],
    )
    listing_empty = _xls(
        ["회사명", "종목코드", "상장일", "상장유형"], []
    )
    delisting_empty = _xls(
        ["번호", "회사명", "종목코드", "폐지일자", "폐지사유", "비고"], []
    )
    current_path = root / "current.xls"
    current_path.write_bytes(current)
    listing_receipts = []
    delisting_receipts = []
    provenance = [RawReceiptProvenance(
        path=current_path,
        uri="https://kind.krx.co.kr/corpgeneral/corpList.do",
        retrieved_at=retrieved_at,
        role="current_listing",
    )]
    for receipt_market in ("KOSPI", "KOSDAQ"):
        listing_path = root / f"listing-{receipt_market}.xls"
        delisting_path = root / f"delisting-{receipt_market}.xls"
        listing_path.write_bytes(listing_empty)
        delisting_path.write_bytes(delisting_empty)
        listing_receipts.append(HistoricalListingReceipt(
            receipt_market, listing_empty, event_history_from, as_of
        ))
        delisting_receipts.append(HistoricalListingReceipt(
            receipt_market, delisting_empty, date(year, 1, 1), as_of
        ))
        provenance.extend((
            RawReceiptProvenance(
                path=listing_path,
                uri="https://kind.krx.co.kr/listinvstg/listingcompany.do",
                retrieved_at=retrieved_at,
                role="listing_event",
                market=receipt_market,
                window_from=event_history_from,
                window_to=as_of,
            ),
            RawReceiptProvenance(
                path=delisting_path,
                uri="https://kind.krx.co.kr/investwarn/delcompany.do",
                retrieved_at=retrieved_at,
                role="delisting_event",
                market=receipt_market,
                window_from=date(year, 1, 1),
                window_to=as_of,
            ),
        ))
    ledger_path = write_request_receipt_ledger(
        root / "request-receipts.json",
        [
            {
                "path": receipt.path,
                "role": receipt.role,
                "market": receipt.market,
                "window_from": receipt.window_from,
                "window_to": receipt.window_to,
                "retrieved_at": receipt.retrieved_at,
            }
            for receipt in provenance
        ],
    )
    provenance = [
        replace(receipt, request_ledger_path=ledger_path)
        for receipt in provenance
    ]
    normalized = normalize_krx_year_end_memberships(
        current_listing_bytes=current,
        listing_receipts=listing_receipts,
        delisting_receipts=delisting_receipts,
        companies=[{
            "corp_code": corp_code,
            "stock_code": stock_code,
            "market": market,
        }],
        years=[year],
        event_history_from=event_history_from,
        as_of=as_of,
    )
    normalized_path = root / "normalized.csv"
    manifest_path = root / "manifest.json"
    normalized_path.write_bytes(normalized.csv_bytes)
    manifest = build_historical_membership_manifest(
        normalized, raw_receipts=provenance
    )
    manifest_path.write_bytes(manifest)
    normalized_checksum = hashlib.sha256(normalized.csv_bytes).hexdigest()
    manifest_checksum = hashlib.sha256(manifest).hexdigest()
    return CompanyYearListingMembership(
        corp_code=corp_code,
        stock_code=stock_code,
        bsns_year=year,
        market=market,
        status="verified",
        evidence_basis="current_open_interval",
        as_of=as_of,
        manifest_checksum=manifest_checksum,
        manifest_storage_uri=manifest_path.resolve().as_uri(),
        manifest_size_bytes=len(manifest),
        manifest_raw_receipt_count=len(provenance),
        normalized_checksum=normalized_checksum,
        normalized_storage_uri=normalized_path.resolve().as_uri(),
        normalized_size_bytes=len(normalized.csv_bytes),
        transformation_version=TRANSFORMATION_VERSION,
        source_row_no=2,
    ), current_path
