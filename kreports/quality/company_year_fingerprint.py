"""Canonical evidence summaries for company-year quality rows."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping

QUALITY_STATUS_KEYS = (
    "financial_core",
    "auditor",
    "audit_fee",
    "policy",
    "kam",
    "audit_procedure",
    "group_audit",
)
QUALITY_GRADE_KEYS = (
    "investor_core",
    "auditor_full",
    "group_audit",
)


def _ordered_values(
    values: Mapping[str, str],
    required: tuple[str, ...],
    label: str,
) -> dict[str, str]:
    if set(values) != set(required):
        raise ValueError(f"{label} keys must equal {required}")
    return {key: str(values[key]) for key in required}


def build_quality_evidence_summary(
    *,
    statuses: Mapping[str, str],
    grades: Mapping[str, str],
    blockers: Iterable[str],
    quality_version: str,
) -> dict[str, object]:
    """Return the bounded, deterministic inputs to a quality computation."""
    return {
        "statuses": _ordered_values(
            statuses,
            QUALITY_STATUS_KEYS,
            "status",
        ),
        "grades": _ordered_values(grades, QUALITY_GRADE_KEYS, "grade"),
        "blockers": sorted({str(value) for value in blockers}),
        "quality_version": str(quality_version),
    }


def quality_input_fingerprint(summary: Mapping[str, object]) -> str:
    """Hash a quality evidence summary as canonical UTF-8 JSON."""
    payload = json.dumps(
        summary,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
