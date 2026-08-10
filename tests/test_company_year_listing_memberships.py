"""Tests for provenance-bound historical year-end listing membership."""
from __future__ import annotations

from datetime import date, datetime
import hashlib
import json

import pytest
from sqlalchemy import inspect

from kreports.db.engine import get_session
from kreports.db.models import Company
from kreports.maintenance.krx_request_receipt_ledger import canonical_request
from tests.historical_membership_fixture import write_request_receipt_ledger


RETRIEVED_AT = "2026-08-10T00:00:00+00:00"
TRANSFORMATION_VERSION = "krx-year-end-listing-membership-v1"


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _seed_companies():
    with get_session() as session:
        session.add_all([
            Company(corp_code="00000001", stock_code="000001", corp_name="Listed", market="KOSPI"),
            Company(corp_code="00000002", stock_code="000002", corp_name="Delisted", market=None),
        ])


def _xls(headers, rows):
    body = "".join(
        "<tr>" + "".join(f"<td>{value}</td>" for value in row) + "</tr>"
        for row in rows
    )
    return (
        "<html><head><meta charset='EUC-KR'></head><body><table><tr>"
        + "".join(f"<th>{header}</th>" for header in headers)
        + f"</tr>{body}</table></body></html>"
    ).encode("euc-kr")


def _raw_fixture_payloads(rows: str):
    parsed = [line.split(",") for line in rows.strip().splitlines() if line]
    current_rows = []
    listing = {"KOSPI": [], "KOSDAQ": []}
    delisting = {"KOSPI": [], "KOSDAQ": []}
    for corp_code, stock_code, year, market, status, basis, _as_of in parsed:
        if (
            corp_code not in {"00000001", "00000002"}
            or stock_code not in {"000001", "000002"}
            or market not in {"KOSPI", "KOSDAQ"}
            or status != "verified"
        ):
            continue
        company_name = "Listed" if corp_code == "00000001" else "Delisted"
        if basis == "current_open_interval":
            current_rows.append([
                company_name,
                "유가" if market == "KOSPI" else "코스닥",
                stock_code,
                "2000-01-02",
            ])
        elif basis == "krx_event_interval":
            listing[market].append([company_name, stock_code, "2000-01-02", "신규상장"])
            delisting[market].append(["1", company_name, stock_code, "2022-06-30", "합병", ""])
        elif basis == "pre_1999_listed_delisted_after_window_start":
            delisting[market].append(["1", company_name, stock_code, "2022-06-30", "합병", ""])
    if not current_rows:
        current_rows.append(["Listed", "유가", "000001", "2022-01-02"])
    return {
        "current": _xls(["회사명", "시장구분", "종목코드", "상장일"], current_rows),
        **{
            f"listing-{market}": _xls(
                ["회사명", "종목코드", "상장일", "상장유형"], listing[market]
            )
            for market in ("KOSPI", "KOSDAQ")
        },
        **{
            f"delisting-{market}": _xls(
                ["번호", "회사명", "종목코드", "폐지일자", "폐지사유", "비고"],
                delisting[market],
            )
            for market in ("KOSPI", "KOSDAQ")
        },
    }


def _write_artifacts(tmp_path, rows: str, *, manifest_override=None):
    payloads = _raw_fixture_payloads(rows)
    raw_paths = {}
    for name, payload in payloads.items():
        path = tmp_path / f"{name}.xls"
        path.write_bytes(payload)
        raw_paths[name] = path
    normalized_path = tmp_path / "year-end-memberships.csv"
    normalized_path.write_text(
        "corp_code,stock_code,bsns_year,market,status,evidence_basis,as_of\n" + rows,
        encoding="utf-8",
    )
    normalized_checksum = _sha256(normalized_path)
    parsed_rows = [line.split(",") for line in rows.strip().splitlines() if line]
    counts = {"KOSPI": 0, "KOSDAQ": 0}
    for row in parsed_rows:
        counts[row[3] if row[3] in counts else "KOSPI"] += 1
    receipts = []
    receipt_specs = []
    endpoints = {
        "current": "https://kind.krx.co.kr/corpgeneral/corpList.do",
        "listing": "https://kind.krx.co.kr/listinvstg/listingcompany.do",
        "delisting": "https://kind.krx.co.kr/investwarn/delcompany.do",
    }
    for name, path in raw_paths.items():
        if name == "current":
            role = "current_listing"
            market = None
            window_from = window_to = None
        else:
            role_name, market = name.split("-")
            role = f"{role_name}_event"
            window_from, window_to = "1999-01-01", "2026-08-10"
        window_from_date = date.fromisoformat(window_from) if window_from else None
        window_to_date = date.fromisoformat(window_to) if window_to else None
        _uri, request_method, request_params = canonical_request(
            role,
            market=market,
            window_from=window_from_date,
            window_to=window_to_date,
        )
        receipt_specs.append({
            "path": path,
            "role": role,
            "market": market,
            "window_from": window_from_date,
            "window_to": window_to_date,
            "retrieved_at": datetime.fromisoformat(RETRIEVED_AT),
        })
        receipts.append({
            "uri": endpoints[name.split("-")[0]],
            "storage_uri": path.resolve().as_uri(),
            "checksum": _sha256(path),
            "size_bytes": path.stat().st_size,
            "retrieved_at": RETRIEVED_AT,
            "role": role,
            "market": market,
            "window_from": window_from,
            "window_to": window_to,
            "request_method": request_method,
            "request_params": request_params,
        })
    ledger_path = write_request_receipt_ledger(
        tmp_path / "request-receipts.json", receipt_specs
    )
    ledger_payload = ledger_path.read_bytes()
    for receipt in receipts:
        receipt.update({
            "request_ledger_storage_uri": ledger_path.resolve().as_uri(),
            "request_ledger_checksum": hashlib.sha256(ledger_payload).hexdigest(),
            "request_ledger_size_bytes": len(ledger_payload),
        })
    manifest = {
        "schema_version": "krx-year-end-listing-membership-manifest-v2",
        "as_of": "2026-08-10",
        "raw_receipts": receipts,
        "normalized_checksum": normalized_checksum,
        "transformation_version": TRANSFORMATION_VERSION,
        "reconstruction": {
            "event_history_from": "1999-01-01",
            "years": [2021],
            "row_count": len(parsed_rows),
            "year_market_counts": {"2021": counts},
            "pre_1999_membership_count": sum(
                row[5] == "pre_1999_listed_delisted_after_window_start"
                for row in parsed_rows
            ),
            "duplicate_company_year_count": 0,
        },
    }
    if manifest_override:
        manifest.update(manifest_override)
    manifest_path = tmp_path / "year-end-memberships.manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    return (
        normalized_path,
        manifest_path,
        normalized_checksum,
        _sha256(manifest_path),
        raw_paths["current"],
    )


def _import(tmp_path, rows: str, *, replace_existing: bool = False, **kwargs):
    from kreports.maintenance.company_year_listing_memberships import (
        import_company_year_listing_membership_snapshot,
    )

    normalized_path, manifest_path, normalized_checksum, manifest_checksum, raw_path = _write_artifacts(
        tmp_path, rows, **kwargs
    )
    result = import_company_year_listing_membership_snapshot(
        normalized_path,
        manifest_path=manifest_path,
        manifest_checksum=manifest_checksum,
        normalized_checksum=normalized_checksum,
        transformation_version=TRANSFORMATION_VERSION,
        replace_existing=replace_existing,
    )
    return result, normalized_path, manifest_path, raw_path


def test_import_persists_a_delisted_company_in_its_historical_year_end_population(temp_engine, tmp_path):
    """A company delisted today remains eligible when verified at that year end."""
    from kreports.db.models import CompanyYearListingMembership

    _seed_companies()
    result, normalized_path, manifest_path, raw_path = _import(
        tmp_path,
        "00000002,000002,2021,KOSDAQ,verified,krx_event_interval,2026-08-10\n",
    )

    assert result == {
        "inserted": 1,
        "deleted": 0,
        "reused": 0,
        "manifest_checksum": _sha256(manifest_path),
        "normalized_checksum": _sha256(normalized_path),
        "transformation_version": TRANSFORMATION_VERSION,
        "year_market_counts": {"2021": {"KOSDAQ": 1}},
    }
    with get_session() as session:
        row = session.query(CompanyYearListingMembership).one()
        assert (row.corp_code, row.stock_code, row.bsns_year, row.market, row.status) == (
            "00000002", "000002", 2021, "KOSDAQ", "verified",
        )
        assert row.manifest_storage_uri == manifest_path.resolve().as_uri()
        assert row.normalized_storage_uri == normalized_path.resolve().as_uri()
        assert row.source_row_no == 2
        assert row.manifest_raw_receipt_count == 5
        assert row.as_of.isoformat() == "2026-08-10"


def test_replace_existing_swaps_rederived_year_rows_in_one_import(temp_engine, tmp_path):
    _seed_companies()
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    rows = "00000001,000001,2021,KOSPI,verified,current_open_interval,2026-08-10\n"
    first, *_ = _import(first_dir, rows)
    second, *_ = _import(second_dir, rows, replace_existing=True)

    assert first["inserted"] == 1
    assert second["deleted"] == 1
    assert second["inserted"] == 1
    assert second["reused"] == 0


def test_import_fails_closed_when_manifest_or_any_raw_receipt_is_tampered(temp_engine, tmp_path):
    from kreports.maintenance.company_year_listing_memberships import (
        import_company_year_listing_membership_snapshot,
    )

    _seed_companies()
    normalized_path, manifest_path, normalized_checksum, manifest_checksum, raw_path = _write_artifacts(
        tmp_path,
        "00000001,000001,2021,KOSPI,verified,current_open_interval,2026-08-10\n",
    )
    raw_path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="raw receipt (size|checksum) mismatch"):
        import_company_year_listing_membership_snapshot(
            normalized_path,
            manifest_path=manifest_path,
            manifest_checksum=manifest_checksum,
            normalized_checksum=normalized_checksum,
            transformation_version=TRANSFORMATION_VERSION,
        )

    manifest_path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="manifest checksum mismatch"):
        import_company_year_listing_membership_snapshot(
            normalized_path,
            manifest_path=manifest_path,
            manifest_checksum=manifest_checksum,
            normalized_checksum=normalized_checksum,
            transformation_version=TRANSFORMATION_VERSION,
        )


def test_import_fails_closed_when_a_manifested_raw_receipt_is_missing(temp_engine, tmp_path):
    from kreports.maintenance.company_year_listing_memberships import (
        import_company_year_listing_membership_snapshot,
    )

    _seed_companies()
    normalized_path, manifest_path, normalized_checksum, manifest_checksum, raw_path = _write_artifacts(
        tmp_path,
        "00000001,000001,2021,KOSPI,verified,current_open_interval,2026-08-10\n",
    )
    raw_path.unlink()
    with pytest.raises(ValueError, match="raw receipt artifact is required"):
        import_company_year_listing_membership_snapshot(
            normalized_path,
            manifest_path=manifest_path,
            manifest_checksum=manifest_checksum,
            normalized_checksum=normalized_checksum,
            transformation_version=TRANSFORMATION_VERSION,
        )


def test_import_rejects_roleless_unrelated_official_receipt(temp_engine, tmp_path):
    from kreports.maintenance.company_year_listing_memberships import (
        import_company_year_listing_membership_snapshot,
    )

    _seed_companies()
    normalized_path, manifest_path, normalized_checksum, manifest_checksum, _ = _write_artifacts(
        tmp_path,
        "00000001,000001,2021,KOSPI,verified,current_open_interval,2026-08-10\n",
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["raw_receipts"][0].pop("role")
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    manifest_checksum = _sha256(manifest_path)
    with pytest.raises(ValueError, match="raw receipt role"):
        import_company_year_listing_membership_snapshot(
            normalized_path,
            manifest_path=manifest_path,
            manifest_checksum=manifest_checksum,
            normalized_checksum=normalized_checksum,
            transformation_version=TRANSFORMATION_VERSION,
        )


def test_import_rederives_and_rejects_self_consistent_but_fabricated_csv(temp_engine, tmp_path):
    from kreports.maintenance.company_year_listing_memberships import (
        import_company_year_listing_membership_snapshot,
    )

    _seed_companies()
    normalized_path, manifest_path, _, _, _ = _write_artifacts(
        tmp_path,
        "00000001,000001,2021,KOSPI,verified,current_open_interval,2026-08-10\n",
    )
    normalized_path.write_text(
        "corp_code,stock_code,bsns_year,market,status,evidence_basis,as_of\n"
        "00000001,000001,2021,KOSDAQ,verified,krx_event_interval,2026-08-10\n",
        encoding="utf-8",
    )
    normalized_checksum = _sha256(normalized_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["normalized_checksum"] = normalized_checksum
    manifest["reconstruction"]["year_market_counts"] = {
        "2021": {"KOSPI": 0, "KOSDAQ": 1}
    }
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")

    with pytest.raises(ValueError, match="does not match raw receipt reconstruction"):
        import_company_year_listing_membership_snapshot(
            normalized_path,
            manifest_path=manifest_path,
            manifest_checksum=_sha256(manifest_path),
            normalized_checksum=normalized_checksum,
            transformation_version=TRANSFORMATION_VERSION,
        )


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        (
            "00000001,000001,2021,KOSPI,verified,current_open_interval,2026-08-10\n"
            "00000001,000001,2021,KOSPI,verified,current_open_interval,2026-08-10\n",
            "duplicate company-year",
        ),
        (
            "00000001,000001,2021,KONEX,verified,current_open_interval,2026-08-10\n",
            "market is unsupported",
        ),
        (
            "00000001,000001,1899,KOSPI,verified,current_open_interval,2026-08-10\n",
            "bsns_year is unsupported",
        ),
        (
            "00000001,000001,2021,KOSPI,present,current_open_interval,2026-08-10\n",
            "status is unsupported",
        ),
        (
            "00000001,000001,2021,KOSPI,verified,source_gap,2026-08-10\n",
            "verified status requires verified evidence_basis",
        ),
        (
            "99999999,000001,2021,KOSPI,verified,current_open_interval,2026-08-10\n",
            "stock_code does not bind to corp_code",
        ),
        (
            "00000001,999999,2021,KOSPI,verified,current_open_interval,2026-08-10\n",
            "stock_code does not bind to corp_code",
        ),
    ],
)
def test_import_rejects_invalid_or_ambiguous_membership_rows(temp_engine, tmp_path, rows, message):
    from kreports.maintenance.company_year_listing_memberships import (
        import_company_year_listing_membership_snapshot,
    )

    _seed_companies()
    normalized_path, manifest_path, normalized_checksum, manifest_checksum, _ = _write_artifacts(tmp_path, rows)
    with pytest.raises(ValueError, match=message):
        import_company_year_listing_membership_snapshot(
            normalized_path,
            manifest_path=manifest_path,
            manifest_checksum=manifest_checksum,
            normalized_checksum=normalized_checksum,
            transformation_version=TRANSFORMATION_VERSION,
        )


def test_import_is_idempotent_but_rejects_a_conflicting_company_year(temp_engine, tmp_path):
    from kreports.maintenance.company_year_listing_memberships import (
        import_company_year_listing_membership_snapshot,
    )

    _seed_companies()
    first, _, _, _ = _import(
        tmp_path,
        "00000001,000001,2021,KOSPI,verified,current_open_interval,2026-08-10\n",
    )
    assert first["inserted"] == 1
    repeated, _, _, _ = _import(
        tmp_path,
        "00000001,000001,2021,KOSPI,verified,current_open_interval,2026-08-10\n",
    )
    assert repeated["inserted"] == 0
    assert repeated["reused"] == 1

    normalized_path, manifest_path, normalized_checksum, manifest_checksum, _ = _write_artifacts(
        tmp_path,
        "00000001,000001,2021,KOSDAQ,verified,krx_event_interval,2026-08-10\n",
    )
    with pytest.raises(ValueError, match="company-year membership conflicts"):
        import_company_year_listing_membership_snapshot(
            normalized_path,
            manifest_path=manifest_path,
            manifest_checksum=manifest_checksum,
            normalized_checksum=normalized_checksum,
            transformation_version=TRANSFORMATION_VERSION,
        )


def test_schema_contract_requires_membership_provenance_and_named_indexes(temp_engine):
    """Release/rehearsal validation rejects a partial historical population table."""
    from kreports.db.schema_contract import (
        REQUIRED_COLUMN_SPECS,
        REQUIRED_INDEX_SPECS,
    )

    assert {
        "corp_code", "stock_code", "bsns_year", "market", "status",
        "evidence_basis", "as_of", "manifest_checksum",
        "manifest_storage_uri", "manifest_raw_receipt_count",
        "normalized_checksum", "transformation_version", "source_row_no",
    }.issubset(REQUIRED_COLUMN_SPECS["company_year_listing_memberships"])
    assert REQUIRED_INDEX_SPECS["uq_company_year_listing_membership_company_year"] == (
        "company_year_listing_memberships", ("corp_code", "bsns_year"), True, None
    )
    from kreports.db.migrations import apply_schema_migrations

    with temp_engine.begin() as connection:
        apply_schema_migrations(connection)
    index_names = {
        item["name"]
        for item in inspect(temp_engine).get_indexes("company_year_listing_memberships")
    }
    assert {
        "idx_company_year_listing_membership_corp_year",
        "idx_company_year_listing_membership_year_market",
        "uq_company_year_listing_membership_company_year",
        "uq_company_year_listing_membership_normalized_row",
    }.issubset(index_names)
