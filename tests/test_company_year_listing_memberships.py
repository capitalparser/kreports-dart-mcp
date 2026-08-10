"""Tests for provenance-bound historical year-end listing membership."""
from __future__ import annotations

import hashlib
import json

import pytest
from sqlalchemy import inspect

from kreports.db.engine import get_session
from kreports.db.models import Company


RAW_URI = "https://kind.krx.co.kr/investwarn/delcompany.do"
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


def _write_artifacts(tmp_path, rows: str, *, manifest_override=None):
    raw_path = tmp_path / "kind-delist.xls"
    raw_path.write_bytes(b"official KRX delisting receipt\n")
    normalized_path = tmp_path / "year-end-memberships.csv"
    normalized_path.write_text(
        "corp_code,stock_code,bsns_year,market,status,evidence_basis,as_of\n" + rows,
        encoding="utf-8",
    )
    normalized_checksum = _sha256(normalized_path)
    manifest = {
        "schema_version": "krx-year-end-listing-membership-manifest-v1",
        "as_of": "2026-08-10",
        "raw_receipts": [{
            "uri": RAW_URI,
            "storage_uri": raw_path.resolve().as_uri(),
            "checksum": _sha256(raw_path),
            "size_bytes": raw_path.stat().st_size,
            "retrieved_at": RETRIEVED_AT,
        }],
        "normalized_checksum": normalized_checksum,
        "transformation_version": TRANSFORMATION_VERSION,
    }
    if manifest_override:
        manifest.update(manifest_override)
    manifest_path = tmp_path / "year-end-memberships.manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    return normalized_path, manifest_path, normalized_checksum, _sha256(manifest_path), raw_path


def _import(tmp_path, rows: str, **kwargs):
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
        assert row.manifest_raw_receipt_count == 1
        assert row.as_of.isoformat() == "2026-08-10"


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
