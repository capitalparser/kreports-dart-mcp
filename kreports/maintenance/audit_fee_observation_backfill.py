"""Explicit, local-only promotion of legacy audit-fee provenance JSON."""
from __future__ import annotations

import json

from kreports.collector.audit_fee_sources import (
    AuditFeeObservation,
    merge_audit_fee_observations,
    observation_from_dict,
    observation_hash,
    renormalize_ds002_observation,
    source_slot_hash,
)
from kreports.db.audit_fee_observation_store import (
    load_current_audit_fee_observations,
    persist_audit_fee_observations,
)
from kreports.db.engine import get_session
from kreports.db.models import AuditFee
from kreports.runtime import require_collector_mode

_MAX_LEGACY_OBSERVATIONS = 20
_VERIFIED_VALUE_FIELDS = (
    "contract_fee_m",
    "contract_hours",
    "actual_fee_m",
    "actual_hours",
    "audit_fee_m",
    "audit_hours",
)


def _parse_legacy_observations(
    row: AuditFee,
) -> list[AuditFeeObservation]:
    try:
        raw_values = json.loads(row.source_observations_json or "[]")
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("legacy source observations are not valid JSON") from exc
    if not isinstance(raw_values, list) or len(raw_values) > _MAX_LEGACY_OBSERVATIONS:
        raise ValueError("legacy source observations must be a bounded list")
    observations: list[AuditFeeObservation] = []
    for raw_value in raw_values:
        if not isinstance(raw_value, dict):
            raise ValueError("legacy source observation must be an object")
        observation = observation_from_dict(raw_value)
        if observation is None:
            raise ValueError("legacy source observation is not typed")
        if (
            observation.corp_code != row.corp_code
            or observation.bsns_year != row.bsns_year
        ):
            raise ValueError("legacy source observation identity mismatches row")
        # Validate all canonical bounds before any write and preserve stored
        # caller order for same-slot corrections.
        observation_hash(observation)
        source_slot_hash(observation)
        observations.append(observation)
    return observations


def _previous_values(row: AuditFee) -> dict[str, object]:
    return {
        name: getattr(row, name)
        for name in (
            "contract_fee_m",
            "contract_hours",
            "actual_fee_m",
            "actual_hours",
            "audit_fee_m",
            "audit_hours",
            "compatibility_basis",
            "source_class",
            "source_rcept_no",
            "source_period",
            "availability_status",
            "quality_status",
            "source_observations_json",
        )
    }


def _project_current_claims(row: AuditFee, observations: list[AuditFeeObservation]) -> None:
    merged = merge_audit_fee_observations(
        observations,
        previous=_previous_values(row),
    )
    values = merged.to_record()
    if str(row.quality_status or "") == "verified":
        for field in _VERIFIED_VALUE_FIELDS:
            if getattr(row, field) is not None and values[field] is None:
                raise ValueError("backfill would erase a verified audit fee value")
    for field, value in values.items():
        if value is not None or getattr(row, field) is None:
            setattr(row, field, value)
    if row.source_observations_json != merged.source_observations_json:
        raise ValueError("normalized and compatibility projections disagree")


def backfill_audit_fee_observations(
    *,
    year_from: int | None = None,
    year_to: int | None = None,
    dry_run: bool = False,
) -> dict[str, int | bool | None]:
    """Promote bounded legacy JSON claims without any network access.

    Each company-year has its own transaction. A malformed row is left exactly
    as found and does not affect the next company-year.
    """
    require_collector_mode("backfill-audit-fee-observations")
    if year_from is not None and year_to is not None and year_from > year_to:
        raise ValueError("year_from must not exceed year_to")
    with get_session() as session:
        query = session.query(AuditFee.corp_code, AuditFee.bsns_year)
        if year_from is not None:
            query = query.filter(AuditFee.bsns_year >= year_from)
        if year_to is not None:
            query = query.filter(AuditFee.bsns_year <= year_to)
        identities = query.order_by(AuditFee.corp_code, AuditFee.bsns_year).all()

    counters: dict[str, int | bool | None] = {
        "year_from": year_from,
        "year_to": year_to,
        "dry_run": dry_run,
        "processed_company_years": 0,
        "inserted_observations": 0,
        "unchanged_observations": 0,
        "superseded_observations": 0,
        "malformed_company_years": 0,
        "failed_company_years": 0,
        "semantic_changes": 0,
    }
    for corp_code, bsns_year in identities:
        counters["processed_company_years"] += 1
        try:
            with get_session() as session:
                row = (
                    session.query(AuditFee)
                    .filter_by(corp_code=corp_code, bsns_year=bsns_year)
                    .one()
                )
                observations = _parse_legacy_observations(row)
                if dry_run:
                    continue
                result = persist_audit_fee_observations(session, observations)
                current = load_current_audit_fee_observations(
                    session,
                    corp_code=corp_code,
                    bsns_year=bsns_year,
                )
                _project_current_claims(row, current)
                counters["inserted_observations"] += result.inserted
                counters["unchanged_observations"] += result.unchanged
                counters["superseded_observations"] += result.superseded
        except ValueError:
            counters["malformed_company_years"] += 1
            counters["failed_company_years"] += 1
        except Exception:
            counters["failed_company_years"] += 1
    counters["semantic_changes"] = (
        int(counters["inserted_observations"])
        + int(counters["superseded_observations"])
    )
    return counters


def _project_renormalized_claims(
    row: AuditFee,
    observations: list[AuditFeeObservation],
) -> None:
    """Replace v1 projections with raw-unit-proven claims, never a fallback."""
    merged = merge_audit_fee_observations(observations, previous={})
    for field, value in merged.to_record().items():
        setattr(row, field, value)
    # v1 never retained raw non-audit service amounts in the typed observation.
    # Keeping those projections would leave an irrecoverably unit-ambiguous NAS.
    row.non_audit_fee_m = None
    row.non_audit_hours = None
    row.nas_ratio = None
    row.independence_risk_flag = None


def renormalize_audit_fee_observations(
    *,
    year_from: int | None = None,
    year_to: int | None = None,
    dry_run: bool = False,
) -> dict[str, int | bool | None]:
    """Offline v1 DS002 repair from persisted raw values; no network access."""
    require_collector_mode("renormalize-audit-fee-observations")
    if year_from is not None and year_to is not None and year_from > year_to:
        raise ValueError("year_from must not exceed year_to")
    with get_session() as session:
        query = session.query(AuditFee.corp_code, AuditFee.bsns_year)
        if year_from is not None:
            query = query.filter(AuditFee.bsns_year >= year_from)
        if year_to is not None:
            query = query.filter(AuditFee.bsns_year <= year_to)
        identities = query.order_by(AuditFee.corp_code, AuditFee.bsns_year).all()

    counters: dict[str, int | bool | None] = {
        "year_from": year_from,
        "year_to": year_to,
        "dry_run": dry_run,
        "processed_company_years": 0,
        "renormalized_company_years": 0,
        "unit_unproven_company_years": 0,
        "inserted_observations": 0,
        "superseded_observations": 0,
        "malformed_company_years": 0,
        "failed_company_years": 0,
    }
    for corp_code, bsns_year in identities:
        counters["processed_company_years"] += 1
        try:
            with get_session() as session:
                row = session.query(AuditFee).filter_by(
                    corp_code=corp_code, bsns_year=bsns_year,
                ).one()
                observations = _parse_legacy_observations(row)
                normalized = [
                    renormalize_ds002_observation(observation)
                    for observation in observations
                ]
                if any("fee_unit_unproven" in item.limitations for item in normalized):
                    counters["unit_unproven_company_years"] += 1
                if normalized == observations:
                    continue
                counters["renormalized_company_years"] += 1
                if dry_run:
                    continue
                result = persist_audit_fee_observations(session, normalized)
                current = load_current_audit_fee_observations(
                    session, corp_code=corp_code, bsns_year=bsns_year,
                )
                _project_renormalized_claims(row, current)
                counters["inserted_observations"] += result.inserted
                counters["superseded_observations"] += result.superseded
        except ValueError:
            counters["malformed_company_years"] += 1
            counters["failed_company_years"] += 1
        except Exception:
            counters["failed_company_years"] += 1
    return counters
