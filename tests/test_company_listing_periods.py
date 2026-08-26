"""Tests for provenance-bound historical listing-period evidence."""
from __future__ import annotations

from datetime import UTC, date, datetime
import hashlib
from pathlib import Path
import sqlite3

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from kreports.db.engine import get_session
from kreports.db.models import Company, CompanyListingPeriod


RAW_SOURCE_URI = "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd"
RETRIEVED_AT = datetime(2026, 8, 5, tzinfo=UTC)
TRANSFORMATION_VERSION = "krx-listing-normalize-v1"


def _write_snapshot(tmp_path, rows: str):
    path = tmp_path / "normalized-listing-periods.csv"
    path.write_text(
        "corp_code,stock_code,market,listed_from,listed_to,status\n" + rows,
        encoding="utf-8",
    )
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def _write_raw_source(tmp_path, payload: bytes = b"official KRX source receipt\n"):
    path = tmp_path / "krx-raw-source.bin"
    path.write_bytes(payload)
    return path, hashlib.sha256(payload).hexdigest()


def _import_snapshot(tmp_path, rows: str, **overrides):
    from kreports.maintenance.listing_periods import import_listing_period_snapshot

    snapshot_path, normalized_checksum = _write_snapshot(tmp_path, rows)
    raw_source_path, raw_source_checksum = _write_raw_source(tmp_path)
    arguments = {
        "raw_source_path": raw_source_path,
        "raw_source_uri": RAW_SOURCE_URI,
        "raw_source_checksum": raw_source_checksum,
        "raw_source_retrieved_at": RETRIEVED_AT,
        "normalized_checksum": normalized_checksum,
        "transformation_version": TRANSFORMATION_VERSION,
        "as_of": date(2026, 8, 5),
    }
    arguments.update(overrides)
    return import_listing_period_snapshot(snapshot_path, **arguments)


def _seed_companies() -> None:
    with get_session() as session:
        session.add_all([
            Company(corp_code="00000001", stock_code="000001", corp_name="Verified Corp", market="KOSPI"),
            Company(corp_code="00000002", stock_code="000002", corp_name="Unknown Corp", market="KOSDAQ"),
            Company(corp_code="00000003", stock_code="000003", corp_name="Conflict Corp", market="KOSDAQ"),
        ])


def test_imported_listing_periods_preserve_unknown_and_conflict_without_shrinking_denominator(temp_engine, tmp_path):
    from kreports.maintenance.listing_periods import listing_eligibility_snapshot

    _seed_companies()
    result = _import_snapshot(
        tmp_path,
        "00000001,000001,KOSPI,2020-01-02,,verified\n"
        "00000002,000002,KOSDAQ,,,unknown\n"
        "00000003,000003,KOSDAQ,,,conflict\n",
    )

    assert result["inserted"] == 3
    assert result["source_type"] == "normalized_listing_period_csv"
    assert result["transformation_version"] == TRANSFORMATION_VERSION
    assert listing_eligibility_snapshot(2025) == {
        "policy": "diagnostic_only_current_core_denominator",
        "full_year_rule": "verified_as_of_on_or_after_year_end_and_period_covers_jan1_through_dec31",
        "coverage_year": 2025,
        "current_core_population": 3,
        "verified_full_year": 1,
        "verified_partial_year": 0,
        "unknown": 1,
        "conflict": 1,
        "uncovered": 0,
        "raw_receipt_available": 3,
        "raw_receipt_unavailable": 0,
        "normalized_artifact_available": 3,
        "normalized_artifact_unavailable": 0,
        "source_types": ["normalized_listing_period_csv"],
        "source_table_available": True,
    }


def test_listing_import_fails_closed_for_missing_raw_receipt_or_normalized_tamper(temp_engine, tmp_path):
    from kreports.maintenance.listing_periods import import_listing_period_snapshot

    _seed_companies()
    rows = "00000001,000001,KOSPI,2020-01-02,,verified\n"
    snapshot_path, normalized_checksum = _write_snapshot(tmp_path, rows)
    raw_source_path, raw_source_checksum = _write_raw_source(tmp_path)

    with pytest.raises(ValueError, match="raw source artifact is required"):
        import_listing_period_snapshot(
            snapshot_path,
            raw_source_path=None,
            raw_source_uri=RAW_SOURCE_URI,
            raw_source_checksum=raw_source_checksum,
            raw_source_retrieved_at=RETRIEVED_AT,
            normalized_checksum=normalized_checksum,
            transformation_version=TRANSFORMATION_VERSION,
            as_of=date(2026, 8, 5),
        )

    snapshot_path.write_text(
        "corp_code,stock_code,market,listed_from,listed_to,status\n"
        "00000001,000001,KOSPI,2020-01-02,,verified\n"
        "00000002,000002,KOSDAQ,,,unknown\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="normalized checksum mismatch"):
        import_listing_period_snapshot(
            snapshot_path,
            raw_source_path=raw_source_path,
            raw_source_uri=RAW_SOURCE_URI,
            raw_source_checksum=raw_source_checksum,
            raw_source_retrieved_at=RETRIEVED_AT,
            normalized_checksum=normalized_checksum,
            transformation_version=TRANSFORMATION_VERSION,
            as_of=date(2026, 8, 5),
        )


def test_listing_import_rejects_future_snapshot_or_period_end(temp_engine, tmp_path):
    _seed_companies()
    rows = "00000001,000001,KOSPI,2020-01-02,2026-08-06,verified\n"
    with pytest.raises(ValueError, match="as_of cannot be after raw_source_retrieved_at date"):
        _import_snapshot(tmp_path, rows, as_of=date(2026, 8, 6))
    with pytest.raises(ValueError, match="listed_to is after as_of"):
        _import_snapshot(tmp_path, rows)


def test_listing_import_rejects_unsupported_transformation_version(temp_engine, tmp_path):
    _seed_companies()
    with pytest.raises(ValueError, match="transformation_version is unsupported"):
        _import_snapshot(
            tmp_path,
            "00000001,000001,KOSPI,2020-01-02,,verified\n",
            transformation_version="experimental-listing-map-v99",
        )


def test_listing_import_rejects_duplicate_company_rows(temp_engine, tmp_path):
    _seed_companies()
    with pytest.raises(ValueError, match="duplicate corp_code"):
        _import_snapshot(
            tmp_path,
            "00000001,000001,KOSPI,2020-01-02,,verified\n"
            "00000001,000001,KOSPI,2020-01-02,,verified\n",
        )


def test_missing_retained_raw_receipt_never_verifies_listing_eligibility(temp_engine, tmp_path):
    from kreports.maintenance.listing_periods import listing_eligibility_snapshot

    _seed_companies()
    _import_snapshot(tmp_path, "00000001,000001,KOSPI,2020-01-02,,verified\n")
    (tmp_path / "krx-raw-source.bin").unlink()

    snapshot = listing_eligibility_snapshot(2025)
    assert snapshot["verified_full_year"] == 0
    assert snapshot["conflict"] == 1
    assert snapshot["raw_receipt_available"] == 0
    assert snapshot["raw_receipt_unavailable"] == 1


def test_missing_normalized_artifact_never_verifies_listing_eligibility(temp_engine, tmp_path):
    from kreports.maintenance.listing_periods import listing_eligibility_snapshot

    _seed_companies()
    _import_snapshot(tmp_path, "00000001,000001,KOSPI,2020-01-02,,verified\n")
    (tmp_path / "normalized-listing-periods.csv").unlink()

    snapshot = listing_eligibility_snapshot(2025)
    assert snapshot["verified_full_year"] == 0
    assert snapshot["conflict"] == 1
    assert snapshot["normalized_artifact_available"] == 0
    assert snapshot["normalized_artifact_unavailable"] == 1


def test_stored_stock_code_tamper_never_verifies_listing_eligibility(temp_engine, tmp_path):
    from sqlalchemy import text
    from kreports.maintenance.listing_periods import listing_eligibility_snapshot

    _seed_companies()
    _import_snapshot(tmp_path, "00000001,000001,KOSPI,2020-01-02,,verified\n")
    with temp_engine.begin() as connection:
        connection.execute(text("""
            UPDATE company_listing_periods
            SET stock_code = '999999'
            WHERE corp_code = '00000001'
        """))

    snapshot = listing_eligibility_snapshot(2025)
    assert snapshot["verified_full_year"] == 0
    assert snapshot["conflict"] == 1


def test_stored_temporal_tamper_never_verifies_listing_eligibility(temp_engine, tmp_path):
    from kreports.maintenance.listing_periods import listing_eligibility_snapshot

    _seed_companies()
    _import_snapshot(tmp_path, "00000001,000001,KOSPI,2020-01-02,,verified\n")
    with get_session() as session:
        row = session.query(CompanyListingPeriod).filter_by(corp_code="00000001").one()
        row.listed_to = date(2026, 8, 6)

    snapshot = listing_eligibility_snapshot(2025)
    assert snapshot["verified_full_year"] == 0
    assert snapshot["conflict"] == 1

    with get_session() as session:
        row = session.query(CompanyListingPeriod).filter_by(corp_code="00000001").one()
        row.listed_to = None
        row.raw_source_retrieved_at = datetime(2026, 8, 4, tzinfo=UTC)

    assert listing_eligibility_snapshot(2025)["conflict"] == 1


def test_migration_only_listing_contract_accepts_orm_import(tmp_path, monkeypatch):
    """Migration SQL, not Base.metadata, is sufficient for the importer."""
    from kreports.db.migrations import MIGRATIONS, _checksum, apply_schema_migrations
    import kreports.db.engine as engine_module

    engine = create_engine(f"sqlite:///{tmp_path / 'migration-only.db'}")
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE schema_migrations (
              revision TEXT PRIMARY KEY, checksum TEXT NOT NULL,
              description TEXT NOT NULL, applied_at TEXT NOT NULL
            )
        """))
        connection.execute(text("""
            CREATE TABLE companies (
              corp_code VARCHAR(8) PRIMARY KEY, stock_code VARCHAR(6),
              corp_name VARCHAR(100), market VARCHAR(10)
            )
        """))
        connection.execute(text("""
            INSERT INTO companies (corp_code, stock_code, corp_name, market)
            VALUES ('00000001', '000001', 'Migrated Corp', 'KOSPI')
        """))
        for migration in MIGRATIONS[:15]:
            connection.execute(text("""
                INSERT INTO schema_migrations (revision, checksum, description, applied_at)
                VALUES (:revision, :checksum, :description, CURRENT_TIMESTAMP)
            """), {
                "revision": migration.revision,
                "checksum": _checksum(migration),
                "description": migration.description,
            })
    with engine.connect() as connection:
        assert apply_schema_migrations(connection) == [
            "20260805_16_company_listing_period_contract",
            "20260805_17_listing_period_named_unique_index",
            "20260810_18_year_listing_membership",
            "20260810_19_year_membership_indexes",
            "20260812_20_audit_procedure_recovery_fallback",
            "20260812_21_source_document_pdf_provenance",
        ]
        columns = {
            column["name"]
            for column in inspect(connection).get_columns("company_listing_periods")
        }
        connection.commit()
    assert {"raw_source_uri", "raw_source_checksum", "raw_source_retrieved_at", "raw_source_storage_uri", "raw_source_size_bytes", "normalized_checksum", "normalized_storage_uri", "normalized_size_bytes", "transformation_version"} <= columns
    assert not {"source_uri", "source_checksum", "retrieved_at"} & columns
    with engine.connect() as connection:
        index_names = {
            row[1]
            for row in connection.exec_driver_sql(
                "PRAGMA index_list(company_listing_periods)"
            )
        }
    assert "uq_listing_period_normalized_row" in index_names

    monkeypatch.setattr(engine_module, "engine", engine)
    monkeypatch.setattr(engine_module, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    snapshot_path, normalized_checksum = _write_snapshot(
        tmp_path, "00000001,000001,KOSPI,2020-01-02,,verified\n"
    )
    raw_source_path, raw_source_checksum = _write_raw_source(tmp_path)
    from kreports.maintenance.listing_periods import import_listing_period_snapshot

    assert import_listing_period_snapshot(
        snapshot_path,
        raw_source_path=raw_source_path,
        raw_source_uri=RAW_SOURCE_URI,
        raw_source_checksum=raw_source_checksum,
        raw_source_retrieved_at=RETRIEVED_AT,
        normalized_checksum=normalized_checksum,
        transformation_version=TRANSFORMATION_VERSION,
        as_of=date(2026, 8, 5),
    )["inserted"] == 1


def test_conflicting_verified_rows_are_diagnosed_never_full_year(temp_engine, tmp_path):
    from kreports.maintenance.listing_periods import listing_eligibility_snapshot

    _seed_companies()
    raw_source_path, raw_source_checksum = _write_raw_source(tmp_path)
    normalized_one = tmp_path / "normalized-one.csv"
    normalized_two = tmp_path / "normalized-two.csv"
    normalized_one.write_bytes(b"normalized one")
    normalized_two.write_bytes(b"normalized two")
    with get_session() as session:
        session.add_all([
            CompanyListingPeriod(
                corp_code="00000001", stock_code="000001", market="KOSPI", listed_from=date(2020, 1, 2),
                listed_to=None, status="verified", as_of=date(2026, 8, 5), raw_source_uri=RAW_SOURCE_URI,
                raw_source_checksum=raw_source_checksum, raw_source_retrieved_at=RETRIEVED_AT,
                raw_source_storage_uri=raw_source_path.resolve().as_uri(), raw_source_size_bytes=raw_source_path.stat().st_size,
                normalized_checksum=hashlib.sha256(normalized_one.read_bytes()).hexdigest(),
                normalized_storage_uri=normalized_one.resolve().as_uri(),
                normalized_size_bytes=normalized_one.stat().st_size,
                transformation_version=TRANSFORMATION_VERSION,
                source_type="normalized_listing_period_csv", source_row_no=2,
            ),
            CompanyListingPeriod(
                corp_code="00000001", stock_code="000001", market="KOSDAQ", listed_from=date(2021, 1, 2),
                listed_to=None, status="verified", as_of=date(2026, 8, 5), raw_source_uri=RAW_SOURCE_URI,
                raw_source_checksum=raw_source_checksum, raw_source_retrieved_at=RETRIEVED_AT,
                raw_source_storage_uri=raw_source_path.resolve().as_uri(), raw_source_size_bytes=raw_source_path.stat().st_size,
                normalized_checksum=hashlib.sha256(normalized_two.read_bytes()).hexdigest(),
                normalized_storage_uri=normalized_two.resolve().as_uri(),
                normalized_size_bytes=normalized_two.stat().st_size,
                transformation_version=TRANSFORMATION_VERSION,
                source_type="normalized_listing_period_csv", source_row_no=2,
            ),
        ])

    snapshot = listing_eligibility_snapshot(2025)
    assert snapshot["verified_full_year"] == 0
    assert snapshot["conflict"] == 1
    assert snapshot["raw_receipt_available"] == 1


def test_listing_snapshot_hashes_each_shared_artifact_once(temp_engine, tmp_path, monkeypatch):
    """A full-population diagnostic caches receipt availability by artifact."""
    from kreports.maintenance.listing_periods import listing_eligibility_snapshot

    _seed_companies()
    raw_source_path, raw_source_checksum = _write_raw_source(tmp_path)
    normalized_path = tmp_path / "shared-normalized.csv"
    normalized_path.write_bytes(b"shared normalized artifact")
    normalized_checksum = hashlib.sha256(normalized_path.read_bytes()).hexdigest()
    with get_session() as session:
        session.add_all([
            CompanyListingPeriod(
                corp_code="00000001", stock_code="000001", market="KOSPI", listed_from=date(2020, 1, 2),
                listed_to=None, status="verified", as_of=date(2026, 8, 5), raw_source_uri=RAW_SOURCE_URI,
                raw_source_checksum=raw_source_checksum, raw_source_retrieved_at=RETRIEVED_AT,
                raw_source_storage_uri=raw_source_path.resolve().as_uri(), raw_source_size_bytes=raw_source_path.stat().st_size,
                normalized_checksum=normalized_checksum, normalized_storage_uri=normalized_path.resolve().as_uri(),
                normalized_size_bytes=normalized_path.stat().st_size, transformation_version=TRANSFORMATION_VERSION,
                source_type="normalized_listing_period_csv", source_row_no=2,
            ),
            CompanyListingPeriod(
                corp_code="00000002", stock_code="000002", market="KOSDAQ", listed_from=None,
                listed_to=None, status="unknown", as_of=date(2026, 8, 5), raw_source_uri=RAW_SOURCE_URI,
                raw_source_checksum=raw_source_checksum, raw_source_retrieved_at=RETRIEVED_AT,
                raw_source_storage_uri=raw_source_path.resolve().as_uri(), raw_source_size_bytes=raw_source_path.stat().st_size,
                normalized_checksum=normalized_checksum, normalized_storage_uri=normalized_path.resolve().as_uri(),
                normalized_size_bytes=normalized_path.stat().st_size, transformation_version=TRANSFORMATION_VERSION,
                source_type="normalized_listing_period_csv", source_row_no=3,
            ),
        ])

    original_read_bytes = Path.read_bytes
    reads: dict[Path, int] = {}

    def counted_read_bytes(path: Path) -> bytes:
        resolved = path.resolve()
        reads[resolved] = reads.get(resolved, 0) + 1
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", counted_read_bytes)
    listing_eligibility_snapshot(2025)

    assert reads[raw_source_path.resolve()] == 1
    assert reads[normalized_path.resolve()] == 1


def test_release_coverage_fails_closed_without_year_membership_evidence(temp_engine, tmp_path):
    from kreports.quality.release_gate import _quality_coverage

    _seed_companies()
    _import_snapshot(
        tmp_path,
        "00000001,000001,KOSPI,2020-01-02,,verified\n"
        "00000002,000002,KOSDAQ,,,unknown\n"
        "00000003,000003,KOSDAQ,,,conflict\n",
    )

    _, coverage, metadata, denominators, _ = _quality_coverage(2025)
    assert denominators["investor_core"] == 0
    assert coverage["investor_core"]["denominator"] == 0
    assert metadata["investor_core_3y"]["membership_evidence_available"] is False
    assert metadata["listing_eligibility"]["verified_full_year"] == 1


def test_absent_listing_contract_reports_every_current_company_as_uncovered(temp_engine):
    from sqlalchemy import text
    from kreports.maintenance.listing_periods import listing_eligibility_snapshot

    _seed_companies()
    with temp_engine.begin() as connection:
        connection.execute(text("DROP TABLE company_listing_periods"))
    assert listing_eligibility_snapshot(2025)["uncovered"] == 3
    assert listing_eligibility_snapshot(2025)["source_table_available"] is False


def test_compact_export_retains_raw_and_normalized_listing_provenance(temp_engine, tmp_path):
    from kreports.maintenance.runtime_export import export_runtime_db

    _seed_companies()
    result = _import_snapshot(tmp_path, "00000001,000001,KOSPI,2020-01-02,,verified\n")
    runtime_db = tmp_path / "compact-runtime.db"
    export_runtime_db(output_path=runtime_db, year_from=2021, year_to=2025)
    with sqlite3.connect(runtime_db) as connection:
        row = connection.execute(
            "SELECT raw_source_uri, raw_source_storage_uri, raw_source_size_bytes, raw_source_checksum, normalized_checksum, normalized_storage_uri, normalized_size_bytes, transformation_version, source_type "
            "FROM company_listing_periods"
        ).fetchone()
    assert row == (
        RAW_SOURCE_URI,
        (tmp_path / "krx-raw-source.bin").resolve().as_uri(),
        len(b"official KRX source receipt\n"),
        result["raw_source_checksum"],
        result["normalized_checksum"],
        (tmp_path / "normalized-listing-periods.csv").resolve().as_uri(),
        (tmp_path / "normalized-listing-periods.csv").stat().st_size,
        TRANSFORMATION_VERSION,
        "normalized_listing_period_csv",
    )
