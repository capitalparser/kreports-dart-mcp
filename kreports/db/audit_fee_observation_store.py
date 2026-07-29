"""Immutable persistence and current-claim reads for audit-fee evidence."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import json

from sqlalchemy.orm import Session

from kreports.collector.audit_fee_sources import (
    AuditFeeObservation,
    observation_from_dict,
    observation_hash,
    source_slot_hash,
)
from kreports.db.models import AuditFeeObservationRecord


@dataclass(frozen=True)
class AuditFeeObservationWriteResult:
    """Counts from one caller-owned atomic audit observation write."""

    inserted: int = 0
    unchanged: int = 0
    superseded: int = 0


def _record_fields(observation: AuditFeeObservation) -> dict[str, object]:
    return {
        "corp_code": observation.corp_code.strip(),
        "bsns_year": int(observation.bsns_year),
        "source_class": observation.source_class.strip(),
        "source_rcept_no": observation.source_rcept_no,
        "source_period": observation.source_period,
        "contract_fee_m": observation.contract_fee_m,
        "contract_hours": observation.contract_hours,
        "actual_fee_m": observation.actual_fee_m,
        "actual_hours": observation.actual_hours,
        "auditor_nm": observation.auditor_nm,
        "availability_status": observation.availability_status,
        "quality_status": observation.quality_status,
        "displayed_unit": observation.displayed_unit,
        "raw_values_json": json.dumps(
            observation.raw_values,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        "source_status": observation.source_status,
        "source_message": observation.source_message,
        "source_eligibility": observation.source_eligibility,
        "limitations_json": json.dumps(
            sorted(set(observation.limitations)),
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "parser_version": observation.parser_version,
    }


def persist_audit_fee_observations(
    session: Session,
    observations: Sequence[AuditFeeObservation],
    *,
    observed_at: datetime | None = None,
) -> AuditFeeObservationWriteResult:
    """Append source claims and atomically advance each affected source slot.

    The caller owns the company-year transaction. This helper never commits.
    """
    inserted = unchanged = superseded = 0
    claim_time = observed_at or datetime.now(timezone.utc)
    for observation in observations:
        semantic_hash = observation_hash(observation)
        slot_hash = source_slot_hash(observation)
        if session.get(AuditFeeObservationRecord, semantic_hash) is not None:
            unchanged += 1
            continue
        current = (
            session.query(AuditFeeObservationRecord)
            .filter_by(source_slot_hash=slot_hash, is_current=True)
            .one_or_none()
        )
        if current is not None:
            current.is_current = False
            session.flush()
            superseded += 1
        session.add(
            AuditFeeObservationRecord(
                observation_hash=semantic_hash,
                source_slot_hash=slot_hash,
                supersedes_hash=(
                    current.observation_hash if current is not None else None
                ),
                is_current=True,
                observed_at=claim_time,
                **_record_fields(observation),
            )
        )
        inserted += 1
    return AuditFeeObservationWriteResult(
        inserted=inserted,
        unchanged=unchanged,
        superseded=superseded,
    )


def _observation_from_record(
    record: AuditFeeObservationRecord,
) -> AuditFeeObservation:
    try:
        raw_values = json.loads(record.raw_values_json)
        limitations = json.loads(record.limitations_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("stored audit fee observation is malformed") from exc
    observation = observation_from_dict(
        {
            "corp_code": record.corp_code,
            "bsns_year": record.bsns_year,
            "source_class": record.source_class,
            "source_rcept_no": record.source_rcept_no,
            "source_period": record.source_period,
            "contract_fee_m": record.contract_fee_m,
            "contract_hours": record.contract_hours,
            "actual_fee_m": record.actual_fee_m,
            "actual_hours": record.actual_hours,
            "auditor_nm": record.auditor_nm,
            "availability_status": record.availability_status,
            "quality_status": record.quality_status,
            "displayed_unit": record.displayed_unit,
            "raw_values": raw_values,
            "source_status": record.source_status,
            "source_message": record.source_message,
            "source_eligibility": record.source_eligibility,
            "limitations": limitations,
            "parser_version": record.parser_version,
        }
    )
    if observation is None:
        raise ValueError("stored audit fee observation cannot be rehydrated")
    return observation


def load_current_audit_fee_observations(
    session: Session,
    *,
    corp_code: str,
    bsns_year: int,
) -> list[AuditFeeObservation]:
    """Load deterministic current source claims for one company-year."""
    records = (
        session.query(AuditFeeObservationRecord)
        .filter_by(
            corp_code=corp_code.strip(),
            bsns_year=int(bsns_year),
            is_current=True,
        )
        .order_by(
            AuditFeeObservationRecord.source_class,
            AuditFeeObservationRecord.source_period,
            AuditFeeObservationRecord.source_rcept_no,
            AuditFeeObservationRecord.observation_hash,
        )
        .all()
    )
    return [_observation_from_record(record) for record in records]
