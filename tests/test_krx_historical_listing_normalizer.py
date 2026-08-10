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
        normalize_krx_year_end_memberships,
    )

    result = normalize_krx_year_end_memberships(
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
        normalize_krx_year_end_memberships,
    )

    result = normalize_krx_year_end_memberships(
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
        normalize_krx_year_end_memberships,
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
        normalize_krx_year_end_memberships(
            **kwargs,
            delisting_receipts=[HistoricalListingReceipt(
                market="KOSDAQ",
                payload=_delistings([["1", "누락기업", "999999", "2022-01-02", "합병", ""]]),
            )],
        )

    with pytest.raises(HistoricalListingNormalizationError, match="multiple markets"):
        normalize_krx_year_end_memberships(
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
        normalize_krx_year_end_memberships,
    )

    empty = (
        "<html><head><meta charset='EUC-KR'></head><body><table>"
        "<tr><th>회사명</th><th>종목코드</th><th>상장일</th><th>상장유형</th></tr>"
        "<tr><td colspan='4'>결과값이 없습니다.</td></tr>"
        "</table></body></html>"
    ).encode("euc-kr")
    result = normalize_krx_year_end_memberships(
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
    from kreports.maintenance.krx_historical_listing_normalizer import (
        normalize_krx_year_end_memberships,
    )

    result = normalize_krx_year_end_memberships(
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
    from kreports.maintenance.krx_historical_listing_normalizer import (
        HistoricalListingReceipt,
        RawReceiptProvenance,
        build_historical_membership_manifest,
        normalize_krx_year_end_memberships,
    )

    current = _current([["현재기업", "유가", "000001", "2020-01-02"]])
    event = _events([["현재기업", "000001", "2020-01-02", "신규상장"]])
    current_path = tmp_path / "current.xls"
    event_path = tmp_path / "events.xls"
    current_path.write_bytes(current)
    event_path.write_bytes(event)
    result = normalize_krx_year_end_memberships(
        current_listing_bytes=current,
        listing_receipts=[HistoricalListingReceipt("KOSPI", event)],
        delisting_receipts=[],
        companies=[_companies()[0]],
        years=[2021],
        event_history_from=date(1999, 1, 1),
        as_of=date(2026, 8, 10),
    )

    manifest_bytes = build_historical_membership_manifest(
        result,
        raw_receipts=[
            RawReceiptProvenance(
                path=current_path,
                uri="https://kind.krx.co.kr/corpgeneral/corpList.do?method=download",
                retrieved_at=datetime(2026, 8, 10, 1, tzinfo=UTC),
                role="current_listing",
            ),
            RawReceiptProvenance(
                path=event_path,
                uri="https://kind.krx.co.kr/listinvstg/listingcompany.do",
                retrieved_at=datetime(2026, 8, 10, 1, tzinfo=UTC),
                role="listing_event",
                market="KOSPI",
            ),
        ],
    )
    manifest = json.loads(manifest_bytes)

    assert manifest["schema_version"] == (
        "krx-year-end-listing-membership-manifest-v1"
    )
    assert manifest["normalized_checksum"] == hashlib.sha256(
        result.csv_bytes
    ).hexdigest()
    assert len(manifest["raw_receipts"]) == 2
    assert manifest["reconstruction"]["year_market_counts"] == {
        "2021": {"KOSDAQ": 0, "KOSPI": 1}
    }
