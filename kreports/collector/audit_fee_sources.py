"""Typed source observations for audit fee and audit-hour evidence."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
import re
from typing import Any, Iterable


_MISSING_TOKENS = {"", "-", "n/a", "na", "해당없음", "없음"}
_DS002_OFFICIAL_AVAILABLE_FROM_YEAR = 2015


def ds002_source_supported(year: int) -> bool:
    """Return the documented OpenDART DS002 year eligibility policy."""
    return year >= _DS002_OFFICIAL_AVAILABLE_FROM_YEAR


@dataclass(frozen=True)
class AuditFeeObservation:
    """One source's claim for a company-year audit fee/hour record.

    Fees are normalized to KRW millions and hours to whole hours.  Contract
    and actual values remain independent so callers can explain precedence.
    """

    corp_code: str
    bsns_year: int
    source_class: str
    contract_fee_m: int | None = None
    contract_hours: int | None = None
    actual_fee_m: int | None = None
    actual_hours: int | None = None
    auditor_nm: str | None = None
    source_rcept_no: str | None = None
    source_period: str | None = None
    availability_status: str = "available"
    quality_status: str = "verified"
    displayed_unit: str | None = None
    raw_values: dict[str, str | None] = field(default_factory=dict)
    source_status: str | None = None
    source_message: str | None = None
    source_eligibility: str = "unknown"
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["limitations"] = list(self.limitations)
        return value


@dataclass(frozen=True)
class AuditFeeMergeResult:
    contract_fee_m: int | None
    contract_hours: int | None
    actual_fee_m: int | None
    actual_hours: int | None
    audit_fee_m: int | None
    audit_hours: int | None
    compatibility_basis: str
    source_class: str | None
    source_rcept_no: str | None
    source_period: str | None
    availability_status: str
    quality_status: str
    conflict_status: str
    conflicts: tuple[dict[str, Any], ...]
    source_observations_json: str

    def to_record(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("conflicts", None)
        return value


def _parse_number(value: object) -> Decimal | None:
    if value is None:
        return None
    cleaned = str(value).strip().lower().replace(",", "").replace(" ", "")
    if cleaned in _MISSING_TOKENS:
        return None
    cleaned = cleaned.replace("원", "").replace("시간", "")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def normalize_fee_m(value: object, unit: str = "백만원") -> int | None:
    """Normalize an explicitly-unit-labelled fee to KRW millions."""
    number = _parse_number(value)
    if number is None:
        return None
    normalized_unit = str(unit or "").replace(" ", "").lower()
    multipliers = {
        "원": Decimal("0.000001"),
        "천원": Decimal("0.001"),
        "백만원": Decimal("1"),
        "millionkrw": Decimal("1"),
        "krwmillion": Decimal("1"),
        "억원": Decimal("100"),
    }
    multiplier = multipliers.get(normalized_unit)
    if multiplier is None:
        return None
    return int((number * multiplier).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def normalize_hours(value: object) -> int | None:
    number = _parse_number(value)
    if number is None:
        return None
    return int(number.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _current_row(rows: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    materialized = list(rows)
    for row in materialized:
        period = str(row.get("se") or row.get("bsns_year") or "").strip()
        if "당기" in period:
            return row
    return materialized[0] if materialized else None


def normalize_endpoint_result(
    *,
    year: int,
    status: str | None,
    rows: Iterable[dict[str, Any]] | None,
    corp_code: str = "",
    message: str | None = None,
    source_supported: bool | None = None,
) -> AuditFeeObservation:
    """Adapt one OpenDART DS002 response without collapsing source fields."""
    status_value = str(status or "")
    row = _current_row(rows or ())
    if status_value == "013" and source_supported is False:
        return AuditFeeObservation(
            corp_code=corp_code,
            bsns_year=year,
            source_class="opendart_ds002",
            source_period=str(year),
            source_eligibility="not_eligible",
            availability_status="not_available_from_endpoint",
            quality_status="missing",
            source_status=status_value,
            source_message=message,
            limitations=("DS002 endpoint does not provide this source period",),
        )
    if status_value == "013":
        explicitly_supported = source_supported is True
        return AuditFeeObservation(
            corp_code=corp_code,
            bsns_year=year,
            source_class="opendart_ds002",
            source_period=str(year),
            source_eligibility=(
                "eligible" if explicitly_supported else "unknown"
            ),
            availability_status=(
                "partial" if explicitly_supported else "missing"
            ),
            quality_status="missing",
            source_status=status_value,
            source_message=message,
            limitations=(
                message or "DS002 supported period returned no current-period row",
            ),
        )
    if status_value != "000":
        return AuditFeeObservation(
            corp_code=corp_code,
            bsns_year=year,
            source_class="opendart_ds002",
            source_period=str(year),
            source_eligibility=(
                "eligible"
                if source_supported is True
                else "not_eligible"
                if source_supported is False
                else "unknown"
            ),
            availability_status="transport_error",
            quality_status="error",
            source_status=status_value,
            source_message=message,
            limitations=(message or f"OpenDART status={status_value}",),
        )
    if row is None:
        unsupported = source_supported is False
        return AuditFeeObservation(
            corp_code=corp_code,
            bsns_year=year,
            source_class="opendart_ds002",
            source_period=str(year),
            source_eligibility=(
                "not_eligible"
                if unsupported
                else "eligible"
                if source_supported is True
                else "unknown"
            ),
            availability_status=(
                "not_available_from_endpoint"
                if unsupported
                else "partial"
                if source_supported is True
                else "missing"
            ),
            quality_status="missing",
            source_status=status_value,
            source_message=message,
            limitations=(
                (
                    "DS002 endpoint does not provide this source period"
                    if unsupported
                    else "DS002 returned no current-period row"
                ),
            ),
        )

    raw = {
        "contract_fee": row.get("adt_cntrct_dtls_mendng"),
        "contract_hours": row.get("adt_cntrct_dtls_time"),
        "actual_fee": row.get("real_exc_dtls_mendng"),
        "actual_hours": row.get("real_exc_dtls_time"),
        "legacy_fee": row.get("adt_fee"),
        "legacy_hours": row.get("adt_time"),
        "legacy_official_fee": row.get("mendng"),
        "legacy_official_hours": row.get("tot_reqre_time"),
    }
    contract_fee = normalize_fee_m(raw["contract_fee"], "백만원")
    contract_hours = normalize_hours(raw["contract_hours"])
    actual_fee = normalize_fee_m(raw["actual_fee"], "백만원")
    actual_hours = normalize_hours(raw["actual_hours"])
    # The historical DS002 shape exposed only one unlabeled compatibility pair.
    if contract_fee is None and actual_fee is None:
        contract_fee = normalize_fee_m(
            (
                raw["legacy_official_fee"]
                if raw["legacy_official_fee"] is not None
                else raw["legacy_fee"]
            ),
            "백만원",
        )
    if contract_hours is None and actual_hours is None:
        contract_hours = normalize_hours(
            (
                raw["legacy_official_hours"]
                if raw["legacy_official_hours"] is not None
                else raw["legacy_hours"]
            )
        )

    populated = (contract_fee, contract_hours, actual_fee, actual_hours)
    available_count = sum(value is not None for value in populated)
    availability = "available" if available_count == 4 else "partial"
    quality = "verified" if available_count else "missing"
    limitations = () if available_count == 4 else ("DS002 fields are incomplete",)
    return AuditFeeObservation(
        corp_code=corp_code,
        bsns_year=year,
        source_class="opendart_ds002",
        contract_fee_m=contract_fee,
        contract_hours=contract_hours,
        actual_fee_m=actual_fee,
        actual_hours=actual_hours,
        auditor_nm=(
            row.get("adtor") or row.get("nm") or row.get("auditor_nm") or None
        ),
        source_period=str(row.get("bsns_year") or row.get("se") or year),
        source_eligibility="eligible",
        availability_status=availability,
        quality_status=quality,
        displayed_unit="백만원",
        raw_values={key: None if value is None else str(value) for key, value in raw.items()},
        source_status=status_value,
        source_message=message,
        limitations=limitations,
    )


def observations_json(observations: Iterable[AuditFeeObservation], limit: int = 20) -> str:
    """Serialize a bounded, deterministic provenance set."""
    deduped_by_fingerprint: dict[str, AuditFeeObservation] = {}
    for item in observations:
        fingerprint = json.dumps(
            item.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        deduped_by_fingerprint[fingerprint] = item
    deduped = list(deduped_by_fingerprint.values())
    prioritized = sorted(
        deduped,
        key=_provenance_priority,
    )
    anchors = sorted(
        latest_audit_fee_observations_by_source(deduped).values(),
        key=_provenance_priority,
    )
    anchor_ids = {id(item) for item in anchors}
    selected = [
        *anchors,
        *(item for item in prioritized if id(item) not in anchor_ids),
    ][: max(1, limit)]
    selected_ids = {id(item) for item in selected}
    # Retain input order so equal-period sequential corrections remain ordered.
    bounded = [item for item in deduped if id(item) in selected_ids]
    return json.dumps(
        [item.to_dict() for item in bounded],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def observation_from_dict(value: dict[str, Any]) -> AuditFeeObservation | None:
    """Rehydrate bounded persisted provenance, ignoring unknown future keys."""
    allowed = set(AuditFeeObservation.__dataclass_fields__)
    payload = {key: item for key, item in value.items() if key in allowed}
    if not {"corp_code", "bsns_year", "source_class"}.issubset(payload):
        return None
    payload["limitations"] = tuple(payload.get("limitations") or ())
    payload["raw_values"] = dict(payload.get("raw_values") or {})
    try:
        return AuditFeeObservation(**payload)
    except (TypeError, ValueError):
        return None


def _temporal_number(value: str | None, digits: int) -> int:
    matches = re.findall(r"\d+", str(value or ""))
    if not matches:
        return 0
    joined = "".join(matches)
    return int(joined[:digits] or 0)


def _source_priority(observation: AuditFeeObservation) -> tuple[object, ...]:
    """Lower tuple wins; verified newer same-source observations win."""
    return (
        0 if observation.source_class == "cached_business_report" else 1,
        0 if observation.quality_status == "verified" else 1,
        -_temporal_number(observation.source_period, 8),
        -_temporal_number(observation.source_rcept_no, 20),
        observation.source_period or "",
        observation.source_rcept_no or "",
        observation.availability_status,
        json.dumps(observation.raw_values, ensure_ascii=False, sort_keys=True),
    )


def _source_recency(observation: AuditFeeObservation) -> tuple[object, ...]:
    return (
        _temporal_number(observation.source_period, 8),
        _temporal_number(observation.source_rcept_no, 20),
        observation.source_period or "",
        observation.source_rcept_no or "",
    )


def latest_audit_fee_observations_by_source(
    observations: Iterable[AuditFeeObservation],
) -> dict[str, AuditFeeObservation]:
    """Resolve the current observation for each source using merge recency."""
    latest: dict[str, AuditFeeObservation] = {}
    for observation in observations:
        current = latest.get(observation.source_class)
        if current is None or _source_recency(observation) >= _source_recency(
            current
        ):
            latest[observation.source_class] = observation
    return latest


def _provenance_priority(
    observation: AuditFeeObservation,
) -> tuple[object, ...]:
    return (
        -_temporal_number(observation.source_period, 8),
        -_temporal_number(observation.source_rcept_no, 20),
        observation.source_class,
        0 if observation.quality_status == "verified" else 1,
        observation.source_period or "",
        observation.source_rcept_no or "",
        observation.availability_status,
        json.dumps(observation.raw_values, ensure_ascii=False, sort_keys=True),
    )


def _pick_value(
    observations: list[AuditFeeObservation],
    field_name: str,
) -> tuple[int | None, AuditFeeObservation | None]:
    candidates = [
        observation
        for observation in observations
        if getattr(observation, field_name) is not None
        and observation.quality_status not in {"error", "missing"}
    ]
    if not candidates:
        return None, None
    selected = sorted(candidates, key=_source_priority)[0]
    return int(getattr(selected, field_name)), selected


def _actual_conflicts(
    observations: list[AuditFeeObservation],
) -> tuple[dict[str, Any], ...]:
    conflicts: list[dict[str, Any]] = []
    for field_name in ("actual_fee_m", "actual_hours"):
        claims = [
            (int(getattr(item, field_name)), item)
            for item in observations
            if getattr(item, field_name) is not None
            and item.quality_status not in {"error", "missing"}
        ]
        for index, (left, left_source) in enumerate(claims):
            for right, right_source in claims[index + 1 :]:
                absolute = abs(left - right)
                percentage = absolute / max(abs(left), abs(right), 1)
                if percentage <= 0.05:
                    continue
                conflicts.append(
                    {
                        "metric": field_name,
                        "left_value": left,
                        "right_value": right,
                        "absolute_variance": absolute,
                        "percentage_variance": round(percentage, 6),
                        "denominator": "max(abs(left), abs(right), 1)",
                        "left_source": left_source.source_class,
                        "right_source": right_source.source_class,
                        "left_rcept_no": left_source.source_rcept_no,
                        "right_rcept_no": right_source.source_rcept_no,
                    }
                )
    return tuple(
        sorted(
            conflicts,
            key=lambda item: (
                item["metric"],
                str(item["left_source"]),
                str(item["right_source"]),
                item["left_value"],
                item["right_value"],
            ),
        )
    )


def merge_audit_fee_observations(
    observations: Iterable[AuditFeeObservation],
    *,
    previous: dict[str, Any] | None = None,
) -> AuditFeeMergeResult:
    """Select compatibility values while retaining bounded source provenance."""
    materialized = list(observations)
    latest_by_source = latest_audit_fee_observations_by_source(materialized)
    current_observations = list(latest_by_source.values())
    active_observations = [
        item
        for item in current_observations
        if item.source_eligibility != "not_eligible"
    ]
    all_ineligible = bool(current_observations) and not active_observations
    state_observations = active_observations
    contract_fee, contract_fee_source = _pick_value(
        active_observations,
        "contract_fee_m",
    )
    contract_hours, contract_hours_source = _pick_value(
        active_observations,
        "contract_hours",
    )
    actual_fee, actual_fee_source = _pick_value(
        active_observations,
        "actual_fee_m",
    )
    actual_hours, actual_hours_source = _pick_value(
        active_observations,
        "actual_hours",
    )

    previous = previous or {}
    contract_fee = contract_fee if contract_fee is not None else previous.get("contract_fee_m")
    contract_hours = (
        contract_hours if contract_hours is not None else previous.get("contract_hours")
    )
    actual_fee = actual_fee if actual_fee is not None else previous.get("actual_fee_m")
    actual_hours = actual_hours if actual_hours is not None else previous.get("actual_hours")

    compatibility_fee = actual_fee if actual_fee is not None else contract_fee
    compatibility_hours = actual_hours if actual_hours is not None else contract_hours
    if actual_fee is not None and actual_hours is not None:
        basis = "actual"
    elif actual_fee is not None or actual_hours is not None:
        basis = "actual_preferred_partial"
    elif contract_fee is not None or contract_hours is not None:
        basis = "contract"
    elif previous.get("audit_fee_m") is not None or previous.get("audit_hours") is not None:
        compatibility_fee = previous.get("audit_fee_m")
        compatibility_hours = previous.get("audit_hours")
        basis = previous.get("compatibility_basis") or "legacy_inferred"
    else:
        basis = "unavailable"

    source = (
        actual_fee_source
        or actual_hours_source
        or contract_fee_source
        or contract_hours_source
    )
    conflicts = _actual_conflicts(active_observations)
    statuses = {
        item.availability_status for item in state_observations
    }
    blockers = sorted(
        (
            item
            for item in state_observations
            if item.availability_status in {"transport_error", "parse_error"}
            or item.quality_status == "error"
        ),
        key=_source_priority,
    )
    if all_ineligible:
        availability = "not_available_from_endpoint"
        quality = "missing"
    elif blockers:
        availability = blockers[0].availability_status
        quality = "error"
    elif conflicts:
        availability = "conflict"
        quality = "conflict"
    elif compatibility_fee is not None and compatibility_hours is not None:
        availability = "available"
        quality = "verified"
    elif compatibility_fee is not None or compatibility_hours is not None:
        availability = "partial"
        quality = "partial"
    elif "transport_error" in statuses:
        availability = "transport_error"
        quality = "error"
    elif "parse_error" in statuses:
        availability = "parse_error"
        quality = "error"
    elif "not_available_from_endpoint" in statuses:
        availability = "not_available_from_endpoint"
        quality = "missing"
    elif "not_found_in_cached_report" in statuses:
        availability = "not_found_in_cached_report"
        quality = "missing"
    else:
        availability = previous.get("availability_status") or "partial"
        quality = previous.get("quality_status") or "missing"

    provenance = observations_json(materialized)
    if not materialized and previous.get("source_observations_json"):
        provenance = str(previous["source_observations_json"])
    return AuditFeeMergeResult(
        contract_fee_m=contract_fee,
        contract_hours=contract_hours,
        actual_fee_m=actual_fee,
        actual_hours=actual_hours,
        audit_fee_m=compatibility_fee,
        audit_hours=compatibility_hours,
        compatibility_basis=basis,
        source_class=source.source_class if source else previous.get("source_class"),
        source_rcept_no=(
            source.source_rcept_no if source else previous.get("source_rcept_no")
        ),
        source_period=source.source_period if source else previous.get("source_period"),
        availability_status=availability,
        quality_status=quality,
        conflict_status="conflict" if conflicts else "none",
        conflicts=conflicts,
        source_observations_json=provenance,
    )
