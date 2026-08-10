"""Year-end KOSPI/KOSDAQ population reconstruction from retained KIND receipts."""
from __future__ import annotations

from datetime import UTC, date, datetime
import hashlib
import json

import pytest


def _xls(headers: list[str], rows: list[list[str]]) -> bytes:
    body = "".join(
        "<tr>" + "".join(f"<td>{value}</td>" for value in row) + "</tr>"
        for row in rows
    )
    return (
        "<html><head><meta http-equiv='Content-Type' "
        "content='text/html; charset=EUC-KR'></head><body><table>"
        "<tr>" + "".join(f"<th>{header}</th>" for header in headers) + "</tr>"
        f"{body}</table></body></html>"
    ).encode("euc-kr")


def _current(rows: list[list[str]]) -> bytes:
    return _xls(["회사명", "시장구분", "종목코드", "상장일"], rows)


def _events(rows: list[list[str]]) -> bytes:
    return _xls(["회사명", "종목코드", "상장일", "상장유형"], rows)


def _delistings(rows: list[list[str]]) -> bytes:
    return _xls(["번호", "회사명", "종목코드", "폐지일자", "폐지사유", "비고"], rows)


def _normalize_complete(**kwargs):
    from kreports.maintenance.krx_historical_listing_normalizer import (
        HistoricalListingReceipt,
        normalize_krx_year_end_memberships,
    )

    as_of = kwargs["as_of"]
    event_history_from = kwargs["event_history_from"]
    for key, empty_payload in (
        ("listing_receipts", _events([])),
        ("delisting_receipts", _delistings([])),
    ):
        supplied = list(kwargs.get(key) or [])
        complete = [
            HistoricalListingReceipt(
                receipt.market,
                receipt.payload,
                window_from=event_history_from,
                window_to=as_of,
            )
            for receipt in supplied
        ]
        present = {receipt.market for receipt in complete}
        for market in ("KOSPI", "KOSDAQ"):
            if market not in present:
                complete.append(HistoricalListingReceipt(
                    market,
                    empty_payload,
                    window_from=event_history_from,
                    window_to=as_of,
                ))
        kwargs[key] = complete
    return normalize_krx_year_end_memberships(**kwargs)


def _companies():
    return [
        {"corp_code": "00000001", "stock_code": "000001", "market": "KOSPI"},
        {"corp_code": "00000002", "stock_code": "000002", "market": None},
        {"corp_code": "00000003", "stock_code": "000003", "market": "KOSPI"},
        {"corp_code": "00000004", "stock_code": "000004", "market": "KOSPI"},
    ]


def test_reconstructs_delisted_future_listed_and_pre_history_members_without_fake_dates():
    from kreports.maintenance.krx_historical_listing_normalizer import (
        HistoricalListingReceipt,
    )

    result = _normalize_complete(
        current_listing_bytes=_current([
            ["현재기업", "유가", "000001", "2020-01-02"],
            ["신규기업", "유가", "000003", "2022-04-01"],
            ["현재기업2", "유가", "000004", "1995-01-02"],
        ]),
        listing_receipts=[
            HistoricalListingReceipt(
                market="KOSPI",
                payload=_events([
                    ["현재기업", "000001", "2020-01-02", "신규상장"],
                    ["신규기업", "000003", "2022-04-01", "신규상장"],
                ]),
            ),
        ],
        delisting_receipts=[
            HistoricalListingReceipt(
                market="KOSDAQ",
                payload=_delistings([
                    ["1", "과거기업", "000002", "2022-06-30", "합병", ""],
                ]),
            ),
        ],
        companies=_companies(),
        years=[2021, 2022],
        event_history_from=date(1999, 1, 1),
        as_of=date(2026, 8, 10),
    )

    assert result.rows == [
        {
            "corp_code": "00000001", "stock_code": "000001",
            "bsns_year": "2021", "market": "KOSPI", "status": "verified",
            "evidence_basis": "current_open_interval", "as_of": "2026-08-10",
        },
        {
            "corp_code": "00000002", "stock_code": "000002",
            "bsns_year": "2021", "market": "KOSDAQ", "status": "verified",
            "evidence_basis": "pre_1999_listed_delisted_after_window_start",
            "as_of": "2026-08-10",
        },
        {
            "corp_code": "00000004", "stock_code": "000004",
            "bsns_year": "2021", "market": "KOSPI", "status": "verified",
            "evidence_basis": "current_open_interval", "as_of": "2026-08-10",
        },
        {
            "corp_code": "00000001", "stock_code": "000001",
            "bsns_year": "2022", "market": "KOSPI", "status": "verified",
            "evidence_basis": "current_open_interval", "as_of": "2026-08-10",
        },
        {
            "corp_code": "00000003", "stock_code": "000003",
            "bsns_year": "2022", "market": "KOSPI", "status": "verified",
            "evidence_basis": "current_open_interval", "as_of": "2026-08-10",
        },
        {
            "corp_code": "00000004", "stock_code": "000004",
            "bsns_year": "2022", "market": "KOSPI", "status": "verified",
            "evidence_basis": "current_open_interval", "as_of": "2026-08-10",
        },
    ]
    assert result.summary["year_market_counts"] == {
        "2021": {"KOSDAQ": 1, "KOSPI": 2},
        "2022": {"KOSDAQ": 0, "KOSPI": 3},
    }
    assert result.summary["pre_event_history_member_count"] == 1
    assert result.summary["transformation_version"] == (
        "krx-year-end-listing-membership-v1"
    )


def test_market_transfer_switches_year_end_market_without_duplicate_membership():
    from kreports.maintenance.krx_historical_listing_normalizer import (
        HistoricalListingReceipt,
    )

    result = _normalize_complete(
        current_listing_bytes=_current([
            ["이전기업", "유가", "000001", "2024-01-02"],
        ]),
        listing_receipts=[
            HistoricalListingReceipt(
                market="KOSDAQ",
                payload=_events([["이전기업", "000001", "2010-01-02", "신규상장"]]),
            ),
            HistoricalListingReceipt(
                market="KOSPI",
                payload=_events([["이전기업", "000001", "2024-01-02", "이전상장"]]),
            ),
        ],
        delisting_receipts=[
            HistoricalListingReceipt(
                market="KOSDAQ",
                payload=_delistings([["1", "이전기업", "000001", "2024-01-02", "이전상장", ""]]),
            ),
        ],
        companies=[_companies()[0]],
        years=[2023, 2024],
        event_history_from=date(1999, 1, 1),
        as_of=date(2026, 8, 10),
    )

    assert [(row["bsns_year"], row["market"]) for row in result.rows] == [
        ("2023", "KOSDAQ"),
        ("2024", "KOSPI"),
    ]
    assert result.summary["duplicate_company_year_count"] == 0


def test_reconstruction_fails_closed_for_unbound_relevant_delisting_or_overlap():
    from kreports.maintenance.krx_historical_listing_normalizer import (
        HistoricalListingNormalizationError,
        HistoricalListingReceipt,
    )

    kwargs = {
        "current_listing_bytes": _current([["현재기업", "유가", "000001", "2020-01-02"]]),
        "listing_receipts": [],
        "companies": [_companies()[0]],
        "years": [2021],
        "event_history_from": date(1999, 1, 1),
        "as_of": date(2026, 8, 10),
    }
    with pytest.raises(HistoricalListingNormalizationError, match="does not bind to company master"):
        _normalize_complete(
            **kwargs,
            delisting_receipts=[HistoricalListingReceipt(
                market="KOSDAQ",
                payload=_delistings([["1", "누락기업", "999999", "2022-01-02", "합병", ""]]),
            )],
        )

    with pytest.raises(HistoricalListingNormalizationError, match="multiple markets"):
        _normalize_complete(
            current_listing_bytes=_current([["현재기업", "유가", "000001", "2020-01-02"]]),
            listing_receipts=[HistoricalListingReceipt(
                market="KOSDAQ",
                payload=_events([["현재기업", "000001", "2010-01-02", "신규상장"]]),
            )],
            delisting_receipts=[HistoricalListingReceipt(
                market="KOSDAQ",
                payload=_delistings([["1", "현재기업", "000001", "2022-01-02", "합병", ""]]),
            )],
            companies=[_companies()[0]],
            years=[2021],
            event_history_from=date(1999, 1, 1),
            as_of=date(2026, 8, 10),
        )


def test_official_empty_history_receipt_is_accepted_without_weakening_other_tables():
    from kreports.maintenance.krx_historical_listing_normalizer import (
        HistoricalListingReceipt,
    )

    empty = (
        "<html><head><meta charset='EUC-KR'></head><body><table>"
        "<tr><th>회사명</th><th>종목코드</th><th>상장일</th><th>상장유형</th></tr>"
        "<tr><td colspan='4'>결과값이 없습니다.</td></tr>"
        "</table></body></html>"
    ).encode("euc-kr")
    result = _normalize_complete(
        current_listing_bytes=_current([["현재기업", "유가", "000001", "2020-01-02"]]),
        listing_receipts=[HistoricalListingReceipt("KOSPI", empty)],
        delisting_receipts=[],
        companies=[_companies()[0]],
        years=[2021],
        event_history_from=date(1999, 1, 1),
        as_of=date(2026, 8, 10),
    )

    assert result.summary["year_market_counts"]["2021"] == {
        "KOSDAQ": 0,
        "KOSPI": 1,
    }


def test_current_company_added_after_latest_requested_year_does_not_block_history():
    result = _normalize_complete(
        current_listing_bytes=_current([
            ["현재기업", "유가", "000001", "2020-01-02"],
            ["미동기화신규기업", "유가", "999999", "2026-03-02"],
        ]),
        listing_receipts=[],
        delisting_receipts=[],
        companies=[_companies()[0]],
        years=[2025],
        event_history_from=date(1999, 1, 1),
        as_of=date(2026, 8, 10),
    )

    assert result.summary["year_market_counts"]["2025"] == {
        "KOSDAQ": 0,
        "KOSPI": 1,
    }


def test_manifest_binds_every_normalizer_receipt_and_normalized_artifact(tmp_path):
    from dataclasses import replace

    from kreports.maintenance.krx_historical_listing_normalizer import (
        HistoricalListingReceipt,
        RawReceiptProvenance,
        build_historical_membership_manifest,
        normalize_krx_year_end_memberships,
    )

    current = _current([["현재기업", "유가", "000001", "2020-01-02"]])
    event = _events([["현재기업", "000001", "2020-01-02", "신규상장"]])
    empty_event = _events([])
    empty_delisting = _delistings([])
    payloads = {
        "current": current,
        "listing-KOSPI": event,
        "listing-KOSDAQ": empty_event,
        "delisting-KOSPI": empty_delisting,
        "delisting-KOSDAQ": empty_delisting,
    }
    paths = {}
    for name, payload in payloads.items():
        path = tmp_path / f"{name}.xls"
        path.write_bytes(payload)
        paths[name] = path
    window_from = date(1999, 1, 1)
    window_to = date(2026, 8, 10)
    result = normalize_krx_year_end_memberships(
        current_listing_bytes=current,
        listing_receipts=[
            HistoricalListingReceipt("KOSPI", event, window_from, window_to),
            HistoricalListingReceipt("KOSDAQ", empty_event, window_from, window_to),
        ],
        delisting_receipts=[
            HistoricalListingReceipt("KOSPI", empty_delisting, window_from, window_to),
            HistoricalListingReceipt("KOSDAQ", empty_delisting, window_from, window_to),
        ],
        companies=[_companies()[0]],
        years=[2021],
        event_history_from=date(1999, 1, 1),
        as_of=date(2026, 8, 10),
    )

    from tests.historical_membership_fixture import write_request_receipt_ledger

    raw_receipts = [
            RawReceiptProvenance(
                path=paths["current"],
                uri="https://kind.krx.co.kr/corpgeneral/corpList.do",
                retrieved_at=datetime(2026, 8, 10, 1, tzinfo=UTC),
                role="current_listing",
            ),
            RawReceiptProvenance(
                path=paths["listing-KOSPI"],
                uri="https://kind.krx.co.kr/listinvstg/listingcompany.do",
                retrieved_at=datetime(2026, 8, 10, 1, tzinfo=UTC),
                role="listing_event",
                market="KOSPI",
                window_from=window_from,
                window_to=window_to,
            ),
            RawReceiptProvenance(
                path=paths["listing-KOSDAQ"],
                uri="https://kind.krx.co.kr/listinvstg/listingcompany.do",
                retrieved_at=datetime(2026, 8, 10, 1, tzinfo=UTC),
                role="listing_event", market="KOSDAQ",
                window_from=window_from, window_to=window_to,
            ),
            *[
                RawReceiptProvenance(
                    path=paths[f"delisting-{market}"],
                    uri="https://kind.krx.co.kr/investwarn/delcompany.do",
                    retrieved_at=datetime(2026, 8, 10, 1, tzinfo=UTC),
                    role="delisting_event", market=market,
                    window_from=window_from, window_to=window_to,
                )
                for market in ("KOSPI", "KOSDAQ")
            ],
        ]
    ledger_path = write_request_receipt_ledger(
        tmp_path / "request-receipts.json",
        [
            {
                "path": receipt.path,
                "role": receipt.role,
                "market": receipt.market,
                "window_from": receipt.window_from,
                "window_to": receipt.window_to,
                "retrieved_at": receipt.retrieved_at,
            }
            for receipt in raw_receipts
        ],
    )
    manifest_bytes = build_historical_membership_manifest(
        result,
        raw_receipts=[
            replace(receipt, request_ledger_path=ledger_path)
            for receipt in raw_receipts
        ],
    )
    manifest = json.loads(manifest_bytes)

    assert manifest["schema_version"] == (
        "krx-year-end-listing-membership-manifest-v2"
    )
    assert manifest["normalized_checksum"] == hashlib.sha256(
        result.csv_bytes
    ).hexdigest()
    assert len(manifest["raw_receipts"]) == 5
    assert manifest["reconstruction"]["year_market_counts"] == {
        "2021": {"KOSDAQ": 0, "KOSPI": 1}
    }


def test_reconstruction_rejects_partial_market_or_time_window_receipts():
    from kreports.maintenance.krx_historical_listing_normalizer import (
        HistoricalListingNormalizationError,
        normalize_krx_year_end_memberships,
    )

    with pytest.raises(HistoricalListingNormalizationError, match="complete listing_event coverage"):
        normalize_krx_year_end_memberships(
            current_listing_bytes=_current([["현재기업", "유가", "000001", "2020-01-02"]]),
            listing_receipts=[],
            delisting_receipts=[],
            companies=[_companies()[0]],
            years=[2021],
            event_history_from=date(1999, 1, 1),
            as_of=date(2026, 8, 10),
        )


def test_cli_rebuilds_manifests_and_imports_verified_year_population(
    temp_engine, tmp_path, monkeypatch,
):
    from kreports.cli.main import app
    from kreports.db.engine import get_session
    from kreports.db.models import Company
    from sqlalchemy import text
    from typer.testing import CliRunner

    with get_session() as session:
        session.add_all([
            Company(
                corp_code="00000001", stock_code="000001",
                corp_name="현재기업", market="KOSPI",
            ),
            Company(
                corp_code="00000002", stock_code="000002",
                corp_name="과거기업", market=None,
            ),
        ])
    current_path = tmp_path / "current.xls"
    current_path.write_bytes(_current([["현재기업", "유가", "000001", "2000-01-02"]]))
    listing_dirs = {}
    for market in ("KOSPI", "KOSDAQ"):
        directory = tmp_path / f"listing-{market}"
        directory.mkdir()
        rows = (
            [["과거기업", "000002", "2000-01-02", "신규상장"]]
            if market == "KOSDAQ" else []
        )
        (directory / "1999-2026.xls").write_bytes(_events(rows))
        listing_dirs[market] = directory
    delisting_paths = {}
    for market in ("KOSPI", "KOSDAQ"):
        path = tmp_path / f"delisting-{market}.xls"
        rows = (
            [["1", "과거기업", "000002", "2022-06-30", "합병", ""]]
            if market == "KOSDAQ" else []
        )
        path.write_bytes(_delistings(rows))
        delisting_paths[market] = path
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    from tests.historical_membership_fixture import write_request_receipt_ledger

    retrieved_at = datetime(2026, 8, 10, 1, tzinfo=UTC)
    ledger_path = write_request_receipt_ledger(
        tmp_path / "request-receipts.json",
        [
            {
                "path": current_path,
                "role": "current_listing",
                "retrieved_at": retrieved_at,
            },
            *[
                {
                    "path": listing_dirs[market] / "1999-2026.xls",
                    "role": "listing_event",
                    "market": market,
                    "window_from": date(1999, 1, 1),
                    "window_to": date(2026, 8, 10),
                    "retrieved_at": retrieved_at,
                }
                for market in ("KOSPI", "KOSDAQ")
            ],
            *[
                {
                    "path": delisting_paths[market],
                    "role": "delisting_event",
                    "market": market,
                    "window_from": date(2021, 1, 1),
                    "window_to": date(2026, 8, 10),
                    "retrieved_at": retrieved_at,
                }
                for market in ("KOSPI", "KOSDAQ")
            ],
        ],
    )
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")

    result = CliRunner().invoke(app, [
        "import-krx-year-memberships",
        "--current-path", str(current_path),
        "--listing-kospi-dir", str(listing_dirs["KOSPI"]),
        "--listing-kosdaq-dir", str(listing_dirs["KOSDAQ"]),
        "--delisting-kospi-path", str(delisting_paths["KOSPI"]),
        "--delisting-kosdaq-path", str(delisting_paths["KOSDAQ"]),
        "--receipt-ledger", str(ledger_path),
        "--output-dir", str(output_dir),
        "--year-from", "2021", "--year-to", "2021",
        "--event-history-from", "1999-01-01",
        "--as-of", "2026-08-10",
    ])

    assert result.exit_code == 0, result.output
    assert "inserted=2" in result.output
    with get_session() as session:
        assert session.execute(
            text("SELECT COUNT(*) FROM company_year_listing_memberships")
        ).scalar_one() == 2


def test_capture_ledger_replays_request_envelope_before_binding_response(tmp_path):
    import httpx

    from kreports.maintenance.krx_request_receipt_ledger import (
        RequestReceiptSpec,
        capture_verified_request_receipt_ledger,
        load_verified_request_receipt_ledger,
    )

    current_path = tmp_path / "current.xls"
    listing_path = tmp_path / "listing.xls"
    current_path.write_bytes(b"current-response")
    listing_path.write_bytes(b"listing-response")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            assert request.url.params["method"] == "download"
            return httpx.Response(200, content=current_path.read_bytes())
        assert b"forward=listingtype_down" in request.content
        assert b"marketType=1" in request.content
        return httpx.Response(200, content=listing_path.read_bytes())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        summary = capture_verified_request_receipt_ledger(
            [
                RequestReceiptSpec(current_path, "current_listing"),
                RequestReceiptSpec(
                    listing_path,
                    "listing_event",
                    market="KOSPI",
                    window_from=date(2024, 1, 1),
                    window_to=date(2025, 12, 31),
                ),
            ],
            output_path=tmp_path / "ledger.json",
            client=client,
        )

    assert summary["receipt_count"] == 2
    verified = load_verified_request_receipt_ledger(tmp_path / "ledger.json")
    assert verified[listing_path.resolve().as_uri()].request_params["marketType"] == "1"
