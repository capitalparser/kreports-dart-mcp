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
QUALITY_STATUS_VALUES = {
    "financial_core": frozenset(
        {"available", "partial", "not_available", "missing", "error"}
    ),
    "auditor": frozenset(
        {"available", "partial", "not_available", "missing", "error"}
    ),
    "audit_fee": frozenset(
        {"available", "partial", "not_available", "missing", "error"}
    ),
    "policy": frozenset(
        {"full_body", "summary_only", "not_available", "missing", "error"}
    ),
    "kam": frozenset(
        {
            "full_body",
            "summary_only",
            "explicit_no_kam",
            "not_available",
            "missing",
            "error",
        }
    ),
    "audit_procedure": frozenset(
        {"available", "not_applicable", "missing", "error"}
    ),
    "group_audit": frozenset(
        {"available", "partial", "not_available", "missing", "error"}
    ),
}
QUALITY_GRADE_VALUES = {
    "investor_core": frozenset({"A", "B", "D"}),
    "auditor_full": frozenset({"A", "B", "D"}),
    "group_audit": frozenset({"A", "D"}),
}
MAX_QUALITY_BLOCKERS = 32
MAX_QUALITY_BLOCKER_LENGTH = 128
MAX_QUALITY_VERSION_LENGTH = 20


def _ordered_values(
    values: Mapping[str, str],
    required: tuple[str, ...],
    allowed: Mapping[str, frozenset[str]],
    label: str,
) -> dict[str, str]:
    if set(values) != set(required):
        raise ValueError(f"{label} keys must equal {required}")
    ordered: dict[str, str] = {}
    for key in required:
        value = values[key]
        if not isinstance(value, str) or value not in allowed[key]:
            raise ValueError(
                f"{label} {key} must be one of {sorted(allowed[key])}"
            )
        ordered[key] = value
    return ordered


def validate_quality_evidence_summary(
    summary: Mapping[str, object],
    *,
    expected_quality_version: str | None = None,
) -> dict[str, object]:
    """Validate and canonicalize the complete bounded summary contract."""
    required_top_level = {
        "statuses",
        "grades",
        "blockers",
        "quality_version",
    }
    if set(summary) != required_top_level:
        raise ValueError(
            "quality evidence summary keys must equal "
            f"{tuple(sorted(required_top_level))}"
        )
    statuses = summary["statuses"]
    grades = summary["grades"]
    blockers = summary["blockers"]
    quality_version = summary["quality_version"]
    if not isinstance(statuses, Mapping):
        raise TypeError("quality evidence statuses must be an object")
    if not isinstance(grades, Mapping):
        raise TypeError("quality evidence grades must be an object")
    if not isinstance(blockers, list):
        raise TypeError("quality evidence blockers must be an array")
    if (
        len(blockers) > MAX_QUALITY_BLOCKERS
        or any(
            not isinstance(blocker, str)
            or not blocker
            or len(blocker) > MAX_QUALITY_BLOCKER_LENGTH
            for blocker in blockers
        )
        or blockers != sorted(set(blockers))
    ):
        raise ValueError(
            "quality evidence blockers must be sorted unique non-empty "
            f"strings bounded to {MAX_QUALITY_BLOCKERS} items and "
            f"{MAX_QUALITY_BLOCKER_LENGTH} characters"
        )
    if (
        not isinstance(quality_version, str)
        or not quality_version
        or len(quality_version) > MAX_QUALITY_VERSION_LENGTH
        or (
            expected_quality_version is not None
            and quality_version != expected_quality_version
        )
    ):
        raise ValueError("quality evidence version is unsupported or unbounded")
    return {
        "statuses": _ordered_values(
            statuses,
            QUALITY_STATUS_KEYS,
            QUALITY_STATUS_VALUES,
            "status",
        ),
        "grades": _ordered_values(
            grades,
            QUALITY_GRADE_KEYS,
            QUALITY_GRADE_VALUES,
            "grade",
        ),
        "blockers": list(blockers),
        "quality_version": quality_version,
    }


def build_quality_evidence_summary(
    *,
    statuses: Mapping[str, str],
    grades: Mapping[str, str],
    blockers: Iterable[str],
    quality_version: str,
) -> dict[str, object]:
    """Return the bounded, deterministic inputs to a quality computation."""
    blocker_values = list(blockers)
    if any(not isinstance(value, str) for value in blocker_values):
        raise TypeError("quality evidence blockers must be strings")
    summary = {
        "statuses": dict(statuses),
        "grades": dict(grades),
        "blockers": sorted(set(blocker_values)),
        "quality_version": quality_version,
    }
    return validate_quality_evidence_summary(summary)


def quality_input_fingerprint(summary: Mapping[str, object]) -> str:
    """Hash a quality evidence summary as canonical UTF-8 JSON."""
    canonical_summary = validate_quality_evidence_summary(summary)
    payload = json.dumps(
        canonical_summary,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
