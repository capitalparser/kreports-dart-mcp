from dataclasses import replace

import pytest

from kreports.collector.audit_fee_sources import (
    AuditFeeObservation,
    canonical_observation_payload,
    observation_hash,
    source_slot_hash,
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
