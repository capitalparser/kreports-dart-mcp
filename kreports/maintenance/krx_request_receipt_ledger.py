"""Validate captured KIND request envelopes against retained response bytes."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import unquote, urlparse


LEDGER_SCHEMA_VERSION = "krx-kind-request-receipt-ledger-v1"
CORE_MARKETS = frozenset({"KOSPI", "KOSDAQ"})
ROLE_URIS = {
    "current_listing": "https://kind.krx.co.kr/corpgeneral/corpList.do",
    "listing_event": "https://kind.krx.co.kr/listinvstg/listingcompany.do",
    "delisting_event": "https://kind.krx.co.kr/investwarn/delcompany.do",
}


@dataclass(frozen=True)
class VerifiedRequestReceipt:
    response_path: Path
    role: str
    market: str | None
    window_from: date | None
    window_to: date | None
    uri: str
    request_method: str
    request_params: dict[str, str]
    response_checksum: str
    response_size_bytes: int
    retrieved_at: datetime


@dataclass(frozen=True)
class RequestReceiptSpec:
    response_path: Path
    role: str
    market: str | None = None
    window_from: date | None = None
    window_to: date | None = None


def canonical_request(
    role: str,
    *,
    market: str | None = None,
    window_from: date | None = None,
    window_to: date | None = None,
) -> tuple[str, str, dict[str, str]]:
    """Return the exact KIND download envelope for one receipt role."""
    if role == "current_listing":
        if market is not None or window_from is not None or window_to is not None:
            raise ValueError("current listing request must not assert market or window")
        return ROLE_URIS[role], "GET", {"method": "download", "searchType": "13"}
    normalized_market = str(market or "").upper()
    if normalized_market not in CORE_MARKETS:
        raise ValueError("history request must assert a core market")
    if not isinstance(window_from, date) or not isinstance(window_to, date):
        raise ValueError("history request must assert an inclusive window")
    if window_from > window_to:
        raise ValueError("history request window is invalid")
    common = {
        "currentPageSize": "3000",
        "pageIndex": "1",
        "marketType": "1" if normalized_market == "KOSPI" else "2",
        "fromDate": window_from.isoformat(),
        "toDate": window_to.isoformat(),
    }
    if role == "listing_event":
        return ROLE_URIS[role], "POST", {
            "method": "searchListingTypeSub",
            **common,
            "forward": "listingtype_down",
            "listTypeArrStr": "01|02|03|04|05|",
            "secuGrpArrStr": "0|ST|FS|MF|SC|RT|IF|DR|",
        }
    if role == "delisting_event":
        return ROLE_URIS[role], "POST", {
            "method": "searchDelCompanySub",
            **common,
            "forward": "delcompany_down",
        }
    raise ValueError("request receipt role is unsupported")


def _path_from_file_uri(value: object, *, field: str) -> Path:
    parsed = urlparse(str(value or ""))
    if parsed.scheme != "file" or not parsed.path:
        raise ValueError(f"{field} must be a local file URI")
    path = Path(unquote(parsed.path))
    if not path.is_file():
        raise ValueError(f"{field} is unavailable")
    return path


def load_verified_request_receipt_ledger(
    ledger_path: str | Path,
) -> dict[str, VerifiedRequestReceipt]:
    """Load a capture-time request ledger and verify every retained response."""
    path = Path(ledger_path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError("request receipt ledger must be a readable file")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("request receipt ledger must be valid UTF-8 JSON") from exc
    if not isinstance(document, dict) or document.get("schema_version") != LEDGER_SCHEMA_VERSION:
        raise ValueError("request receipt ledger schema is unsupported")
    receipts = document.get("receipts")
    if not isinstance(receipts, list) or not receipts:
        raise ValueError("request receipt ledger must contain receipts")
    verified: dict[str, VerifiedRequestReceipt] = {}
    for raw in receipts:
        if not isinstance(raw, dict):
            raise ValueError("request receipt ledger entry must be an object")
        role = str(raw.get("role") or "")
        market = str(raw.get("market") or "").upper() or None
        try:
            window_from = (
                date.fromisoformat(str(raw["window_from"]))
                if raw.get("window_from") is not None else None
            )
            window_to = (
                date.fromisoformat(str(raw["window_to"]))
                if raw.get("window_to") is not None else None
            )
        except ValueError as exc:
            raise ValueError("request receipt ledger window is invalid") from exc
        uri, request_method, request_params = canonical_request(
            role,
            market=market,
            window_from=window_from,
            window_to=window_to,
        )
        if (
            raw.get("uri") != uri
            or raw.get("request_method") != request_method
            or raw.get("request_params") != request_params
        ):
            raise ValueError("captured request envelope is not canonical")
        response_path = _path_from_file_uri(
            raw.get("response_storage_uri"), field="captured response"
        )
        payload = response_path.read_bytes()
        checksum = hashlib.sha256(payload).hexdigest()
        try:
            size = int(raw.get("response_size_bytes"))
            retrieved_at = datetime.fromisoformat(str(raw.get("retrieved_at") or ""))
        except (TypeError, ValueError) as exc:
            raise ValueError("captured response metadata is invalid") from exc
        if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
            raise ValueError("captured response timestamp must be timezone-aware")
        if raw.get("response_checksum") != checksum or size != len(payload):
            raise ValueError("captured response bytes do not match request ledger")
        storage_uri = response_path.resolve().as_uri()
        if storage_uri in verified:
            raise ValueError("request receipt ledger contains duplicate response")
        verified[storage_uri] = VerifiedRequestReceipt(
            response_path=response_path,
            role=role,
            market=market,
            window_from=window_from,
            window_to=window_to,
            uri=uri,
            request_method=request_method,
            request_params=request_params,
            response_checksum=checksum,
            response_size_bytes=size,
            retrieved_at=retrieved_at.astimezone(timezone.utc),
        )
    return verified


def capture_verified_request_receipt_ledger(
    specs: list[RequestReceiptSpec],
    *,
    output_path: str | Path,
    client: object | None = None,
) -> dict[str, object]:
    """Replay exact KIND requests and retain envelopes only on byte identity."""
    if not specs:
        raise ValueError("at least one request receipt specification is required")
    import httpx

    owned_client = client is None
    session = client or httpx.Client(
        follow_redirects=False,
        timeout=httpx.Timeout(60.0),
        headers={"User-Agent": "KReports-KRX-Evidence/1.0"},
    )
    entries = []
    try:
        for spec in specs:
            response_path = spec.response_path.expanduser().resolve(strict=True)
            if not response_path.is_file():
                raise ValueError("retained KRX response must be a readable file")
            uri, request_method, request_params = canonical_request(
                spec.role,
                market=spec.market,
                window_from=spec.window_from,
                window_to=spec.window_to,
            )
            if request_method == "GET":
                response = session.get(uri, params=request_params)
            else:
                response = session.post(uri, data=request_params)
            if response.status_code != 200:
                raise ValueError(
                    f"KIND request failed for {spec.role}: HTTP {response.status_code}"
                )
            retained = response_path.read_bytes()
            if not retained or response.content != retained:
                raise ValueError(
                    f"live KIND response does not match retained bytes: {response_path.name}"
                )
            retrieved_at = datetime.now(timezone.utc)
            entries.append({
                "role": spec.role,
                "market": spec.market,
                "window_from": spec.window_from.isoformat() if spec.window_from else None,
                "window_to": spec.window_to.isoformat() if spec.window_to else None,
                "uri": uri,
                "request_method": request_method,
                "request_params": request_params,
                "response_storage_uri": response_path.as_uri(),
                "response_checksum": hashlib.sha256(retained).hexdigest(),
                "response_size_bytes": len(retained),
                "retrieved_at": retrieved_at.isoformat(),
            })
    finally:
        if owned_client:
            session.close()
    payload = (
        json.dumps(
            {"schema_version": LEDGER_SCHEMA_VERSION, "receipts": entries},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    from kreports.maintenance.krx_listing_normalizer import (
        write_normalized_listing_csv,
    )

    write_normalized_listing_csv(output_path, payload)
    return {
        "receipt_count": len(entries),
        "ledger_checksum": hashlib.sha256(payload).hexdigest(),
        "ledger_size_bytes": len(payload),
        "output_path": str(Path(output_path)),
    }
