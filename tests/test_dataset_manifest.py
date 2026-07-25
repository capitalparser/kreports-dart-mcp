from datetime import date
import json

import pytest
from sqlalchemy import select

from tests.factories import (
    company_factory,
    disclosure_factory,
    evidence_document_factory,
)


def _apply_contract(temp_engine) -> None:
    from kreports.db.migrations import apply_schema_migrations

    with temp_engine.begin() as conn:
        apply_schema_migrations(conn)


def test_dataset_manifest_records_schema_version_counts_and_year_range(temp_engine):
    from kreports.db.engine import get_session, write_dataset_manifest
    from kreports.db.migrations import MIGRATIONS
    from kreports.db.models import DatasetManifest

    _apply_contract(temp_engine)
    with get_session() as session:
        session.add_all(
            [
                company_factory(),
                company_factory(corp_code="00164779", corp_name="한국전력"),
                disclosure_factory(
                    rcept_no="20220318000001",
                    disc_date=date(2022, 3, 18),
                ),
                disclosure_factory(
                    rcept_no="20250318000001",
                    disc_date=date(2025, 3, 18),
                ),
                evidence_document_factory(
                    bsns_year=2021,
                    rcept_no="20220318000001",
                ),
                evidence_document_factory(
                    bsns_year=2024,
                    rcept_no="20250318000001",
                ),
            ]
        )

    result = write_dataset_manifest("compact-2025.07.25", notes="release candidate")

    assert result == {
        "manifest_id": "compact-2025.07.25",
        "schema_version": MIGRATIONS[-1].revision,
        "dataset_version": "compact-2025.07.25",
        "generated_at": result["generated_at"],
        "year_from": 2021,
        "year_to": 2025,
        "company_count": 2,
        "disclosure_count": 2,
        "evidence_document_count": 2,
        "quality_snapshot_json": "{}",
        "notes": "release candidate",
    }

    with get_session() as session:
        stored = session.scalars(select(DatasetManifest)).one()
        stored_values = {
            "manifest_id": stored.manifest_id,
            "schema_version": stored.schema_version,
            "company_count": stored.company_count,
            "disclosure_count": stored.disclosure_count,
            "evidence_document_count": stored.evidence_document_count,
            "year_from": stored.year_from,
            "year_to": stored.year_to,
            "quality_snapshot_json": stored.quality_snapshot_json,
        }

    assert stored_values["manifest_id"] == "compact-2025.07.25"
    assert stored_values["schema_version"] == MIGRATIONS[-1].revision
    assert stored_values["company_count"] == 2
    assert stored_values["disclosure_count"] == 2
    assert stored_values["evidence_document_count"] == 2
    assert (stored_values["year_from"], stored_values["year_to"]) == (2021, 2025)
    assert json.loads(stored_values["quality_snapshot_json"]) == {}


def test_dataset_manifest_allows_empty_dataset_with_unknown_year_range(temp_engine):
    from kreports.db.engine import write_dataset_manifest

    _apply_contract(temp_engine)

    result = write_dataset_manifest("empty-v1")

    assert result["year_from"] is None
    assert result["year_to"] is None
    assert result["company_count"] == 0
    assert result["disclosure_count"] == 0
    assert result["evidence_document_count"] == 0


def test_dataset_manifest_rejects_duplicate_manifest_id_without_overwrite(temp_engine):
    from kreports.db.engine import get_session, write_dataset_manifest
    from kreports.db.models import DatasetManifest

    _apply_contract(temp_engine)
    first = write_dataset_manifest("compact-v1", notes="first")

    with pytest.raises(ValueError, match="already exists"):
        write_dataset_manifest("compact-v1", notes="replacement")

    with get_session() as session:
        stored = session.get(DatasetManifest, "compact-v1")
        stored_values = (
            stored.notes if stored is not None else None,
            stored.generated_at.isoformat() if stored is not None else None,
        )

    assert stored is not None
    assert stored_values[0] == "first"
    assert first["generated_at"].startswith(stored_values[1])


def test_dataset_manifest_writer_is_rejected_in_readonly_mode(
    temp_engine,
    monkeypatch,
):
    from kreports.db.engine import write_dataset_manifest

    _apply_contract(temp_engine)
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    with pytest.raises(RuntimeError, match="requires collector mode"):
        write_dataset_manifest("readonly-v1")
