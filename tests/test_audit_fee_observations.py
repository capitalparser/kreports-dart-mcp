from dataclasses import replace
from datetime import datetime, timezone
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from kreports.collector.audit_fee_sources import (
    AuditFeeObservation,
    canonical_observation_payload,
    observation_hash,
    observations_json,
    source_slot_hash,
)
from kreports.collector.audit_fee_collector import upsert_audit_fee_observations
from kreports.db.audit_fee_observation_store import (
    AuditFeeObservationWriteResult,
    load_current_audit_fee_observations,
    persist_audit_fee_observations,
)
from kreports.db.models import AuditFee, AuditFeeObservationRecord, Base
from kreports.maintenance.audit_fee_observation_backfill import (
    backfill_audit_fee_observations,
    renormalize_audit_fee_observations,
)


def _observation(**changes):
    values = {
        "corp_code": "00126380",
        "bsns_year": 2025,
        "source_class": "cached_business_report",
        "actual_fee_m": 1_000,
        "actual_hours": 2_000,
        "source_rcept_no": "20260310002820",
        "source_period": "2025",
        "raw_values": {"hours": "2,000", "fee": "1,000"},
        "limitations": ("second", "first", "first"),
    }
    values.update(changes)
    return AuditFeeObservation(**values)


def test_observation_hash_is_semantic_and_order_independent():
    left = _observation()
    right = replace(
        left,
        raw_values={"fee": "1,000", "hours": "2,000"},
        limitations=("first", "second"),
    )

    assert canonical_observation_payload(left) == canonical_observation_payload(right)
    assert observation_hash(left) == observation_hash(right)
    assert len(observation_hash(left)) == 64


def test_amount_change_changes_observation_identity_not_source_slot():
    left = _observation()
    right = replace(left, actual_fee_m=1_001)

    assert observation_hash(left) != observation_hash(right)
    assert source_slot_hash(left) == source_slot_hash(right)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"raw_values": {str(number): "x" for number in range(33)}}, "raw values"),
        ({"raw_values": {"x" * 81: "value"}}, "raw value key"),
        ({"raw_values": {"key": "x" * 501}}, "raw value"),
        ({"limitations": tuple(str(number) for number in range(21))}, "limitations"),
        ({"limitations": ("x" * 301,)}, "limitation"),
        ({"source_message": "x" * 501}, "source message"),
    ],
)
def test_observation_identity_rejects_oversized_source_fields(changes, message):
    with pytest.raises(ValueError, match=message):
        observation_hash(_observation(**changes))


def test_observation_identity_rejects_payload_over_32_kib():
    observation = _observation(auditor_nm="x" * (33 * 1024))

    with pytest.raises(ValueError, match="32 KiB"):
        observation_hash(observation)


@pytest.mark.parametrize(
    "changes",
    [
        {"corp_code": "   "},
        {"source_class": "  "},
        {"bsns_year": 0},
    ],
)
def test_source_slot_identity_rejects_unbounded_identity(changes):
    with pytest.raises(ValueError):
        source_slot_hash(_observation(**changes))


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"corp_code": "1" * 9}, "corp_code"),
        ({"source_class": "s" * 41}, "source_class"),
        ({"source_rcept_no": "r" * 81}, "source_rcept_no"),
        ({"source_period": "p" * 81}, "source_period"),
        ({"corp_code": 126380}, "corp_code"),
        ({"source_class": 123}, "source_class"),
        ({"source_rcept_no": 20260310}, "source_rcept_no"),
        ({"source_period": 2025}, "source_period"),
    ],
)
def test_source_slot_identity_enforces_schema_aligned_strings(changes, message):
    with pytest.raises(ValueError, match=message):
        source_slot_hash(_observation(**changes))


def test_source_slot_identity_rejects_oversized_serialized_payload():
    observation = _observation(
        corp_code="😀" * 8,
        source_class="😀" * 40,
        source_rcept_no="😀" * 80,
        source_period="😀" * 80,
    )

    with pytest.raises(ValueError, match="source slot payload"):
        source_slot_hash(observation)


def test_raw_value_keys_reject_conversion_collisions_in_any_order():
    left_first = _observation(raw_values={1: "left", "1": "right"})
    right_first = _observation(raw_values={"1": "right", 1: "left"})

    for observation in (left_first, right_first):
        with pytest.raises(ValueError, match="raw value keys must be strings"):
            canonical_observation_payload(observation)


def test_store_accepts_exact_schema_boundaries(temp_engine):
    observation = _observation(
        corp_code="c" * 8,
        source_class="s" * 40,
        source_rcept_no="r" * 80,
        source_period="p" * 80,
    )

    with Session(temp_engine) as session:
        result = persist_audit_fee_observations(session, [observation])
        session.commit()
        record = session.query(AuditFeeObservationRecord).one()

    assert result.inserted == 1
    assert record.corp_code == "c" * 8
    assert record.source_class == "s" * 40
    assert record.source_rcept_no == "r" * 80
    assert record.source_period == "p" * 80


@pytest.mark.parametrize(
    "changes",
    [
        {"corp_code": "1" * 9},
        {"source_class": "s" * 41},
        {"source_rcept_no": "r" * 81},
        {"source_period": "p" * 81},
        {"corp_code": 126380},
        {"source_class": 123},
        {"source_rcept_no": 20260310},
        {"source_period": 2025},
        {
            "corp_code": "😀" * 8,
            "source_class": "😀" * 40,
            "source_rcept_no": "😀" * 80,
            "source_period": "😀" * 80,
        },
        {"raw_values": {1: "left", "1": "right"}},
    ],
)
def test_store_rejects_invalid_identity_before_sqlite_persistence(
    temp_engine,
    changes,
):
    with Session(temp_engine) as session:
        with pytest.raises(ValueError):
            persist_audit_fee_observations(session, [_observation(**changes)])
        assert session.query(AuditFeeObservationRecord).count() == 0


def test_immutable_store_keeps_correction_history_and_independent_slots(temp_engine):
    first_claim = _observation()
    correction_claim = replace(first_claim, actual_fee_m=1_200)
    separate_slot_claim = replace(
        first_claim,
        source_rcept_no="20260410002820",
        actual_fee_m=1_300,
    )

    with Session(temp_engine) as session:
        first = persist_audit_fee_observations(session, [first_claim])
        session.commit()
        second = persist_audit_fee_observations(session, [first_claim])
        session.commit()
        correction = persist_audit_fee_observations(session, [correction_claim])
        separate = persist_audit_fee_observations(session, [separate_slot_claim])
        session.commit()

        historical = session.get(AuditFeeObservationRecord, observation_hash(first_claim))
        current = session.get(AuditFeeObservationRecord, observation_hash(correction_claim))
        records = session.query(AuditFeeObservationRecord).all()
        loaded = load_current_audit_fee_observations(
            session,
            corp_code=first_claim.corp_code,
            bsns_year=first_claim.bsns_year,
        )

    assert first == AuditFeeObservationWriteResult(
        inserted=1, unchanged=0, superseded=0
    )
    assert second == AuditFeeObservationWriteResult(
        inserted=0, unchanged=1, superseded=0
    )
    assert correction == AuditFeeObservationWriteResult(
        inserted=1, unchanged=0, superseded=1
    )
    assert separate == AuditFeeObservationWriteResult(
        inserted=1, unchanged=0, superseded=0
    )
    assert historical is not None and historical.is_current is False
    assert current is not None and current.is_current is True
    assert current.supersedes_hash == observation_hash(first_claim)
    assert sum(record.is_current for record in records) == 2
    assert [item.actual_fee_m for item in loaded] == [1_200, 1_300]


def test_collector_promotes_legacy_claims_before_projecting_current_claims(
    temp_engine,
):
    legacy_claim = _observation(
        source_rcept_no="20260210002820",
        actual_fee_m=900,
        actual_hours=1_900,
    )
    new_claim = _observation(
        source_class="opendart_ds002",
        source_rcept_no=None,
        actual_fee_m=1_000,
        actual_hours=2_000,
    )
    with Session(temp_engine) as session:
        session.add(
            AuditFee(
                corp_code=legacy_claim.corp_code,
                bsns_year=legacy_claim.bsns_year,
                audit_fee_m=900,
                audit_hours=1_900,
                actual_fee_m=900,
                actual_hours=1_900,
                source_observations_json=observations_json([legacy_claim]),
                non_audit_fee_m=77,
                fetched_at=datetime.now(timezone.utc),
            )
        )
        session.commit()

    upsert_audit_fee_observations([new_claim])

    with Session(temp_engine) as session:
        row = session.query(AuditFee).one()
        normalized_count = session.query(AuditFeeObservationRecord).count()
        current = load_current_audit_fee_observations(
            session,
            corp_code=legacy_claim.corp_code,
            bsns_year=legacy_claim.bsns_year,
        )

    assert normalized_count == 2
    assert row.audit_fee_m == 900
    assert row.audit_hours == 1_900
    assert row.non_audit_fee_m == 77
    assert len(json.loads(row.source_observations_json)) == 2
    assert {item.actual_fee_m for item in current} == {900, 1_000}


@pytest.fixture
def file_audit_fee_db(tmp_path, monkeypatch):
    database = create_engine(f"sqlite:///{tmp_path / 'audit-fee-backfill.db'}")
    Base.metadata.create_all(database)
    import kreports.db.engine as engine_module

    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    monkeypatch.setattr(engine_module, "engine", database)
    monkeypatch.setattr(engine_module, "SessionLocal", sessionmaker(bind=database))
    return database


def test_explicit_backfill_is_dry_run_safe_and_idempotent(file_audit_fee_db):
    first = _observation(actual_fee_m=800, actual_hours=1_800)
    correction = replace(first, actual_fee_m=900, actual_hours=1_900)
    with Session(file_audit_fee_db) as session:
        session.add(
            AuditFee(
                corp_code=first.corp_code,
                bsns_year=first.bsns_year,
                audit_fee_m=900,
                audit_hours=1_900,
                actual_fee_m=900,
                actual_hours=1_900,
                source_observations_json=json.dumps(
                    [first.to_dict(), correction.to_dict()]
                ),
                fetched_at=datetime.now(timezone.utc),
            )
        )
        session.commit()

    dry_run = backfill_audit_fee_observations(dry_run=True)
    with Session(file_audit_fee_db) as session:
        normalized_count_after_dry_run = session.query(
            AuditFeeObservationRecord
        ).count()
    write = backfill_audit_fee_observations()
    rerun = backfill_audit_fee_observations()

    assert dry_run["dry_run"] is True
    assert normalized_count_after_dry_run == 0
    assert write["processed_company_years"] == 1
    assert write["inserted_observations"] == 2
    assert rerun["inserted_observations"] == 0
    assert rerun["semantic_changes"] == 0


def test_offline_renormalization_uses_raw_unit_and_clears_ambiguous_nas(file_audit_fee_db):
    legacy = AuditFeeObservation(
        corp_code="00126380",
        bsns_year=2025,
        source_class="opendart_ds002",
        contract_fee_m=240_000,
        contract_hours=1_543,
        actual_fee_m=303_000_000,
        actual_hours=1_553,
        displayed_unit="백만원",
        raw_values={
            "contract_fee": "240,000천원",
            "contract_hours": "1,543",
            "actual_fee": "303,000,000원",
            "actual_hours": "1,553",
        },
        parser_version="v1",
    )
    with Session(file_audit_fee_db) as session:
        session.add(AuditFee(
            corp_code=legacy.corp_code, bsns_year=legacy.bsns_year,
            audit_fee_m=303_000_000, audit_hours=1_553,
            actual_fee_m=303_000_000, actual_hours=1_553,
            non_audit_fee_m=80_000_000, nas_ratio=0.264,
            source_observations_json=observations_json([legacy]),
            fetched_at=datetime.now(timezone.utc),
        ))
        session.commit()
    with Session(file_audit_fee_db) as session:
        persist_audit_fee_observations(session, [legacy])
        session.commit()

    dry_run = renormalize_audit_fee_observations(year_from=2025, year_to=2025, dry_run=True)
    result = renormalize_audit_fee_observations(year_from=2025, year_to=2025)

    with Session(file_audit_fee_db) as session:
        row = session.query(AuditFee).one()
        current = session.query(AuditFeeObservationRecord).filter_by(is_current=True).one()
    assert dry_run["renormalized_company_years"] == 1
    assert result["superseded_observations"] == 1
    assert row.actual_fee_m == 303
    assert row.contract_fee_m == 240
    assert row.non_audit_fee_m is None
    assert row.nas_ratio is None
    assert current.parser_version == "v2"


def test_explicit_backfill_leaves_malformed_company_year_unchanged(file_audit_fee_db):
    with Session(file_audit_fee_db) as session:
        session.add(
            AuditFee(
                corp_code="malformed",
                bsns_year=2025,
                audit_fee_m=700,
                audit_hours=1_700,
                source_observations_json='{"not": "a list"}',
                fetched_at=datetime.now(timezone.utc),
            )
        )
        session.commit()

    result = backfill_audit_fee_observations()

    with Session(file_audit_fee_db) as session:
        row = session.query(AuditFee).one()
        normalized_count = session.query(AuditFeeObservationRecord).count()
    assert result["malformed_company_years"] == 1
    assert result["failed_company_years"] == 1
    assert row.audit_fee_m == 700
    assert row.source_observations_json == '{"not": "a list"}'
    assert normalized_count == 0
