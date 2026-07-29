"""Deterministic content contract for the company-year quality ledger."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

from kreports.quality.company_year_fingerprint import (
    QUALITY_GRADE_KEYS,
    QUALITY_STATUS_KEYS,
    quality_input_fingerprint,
    validate_quality_evidence_summary,
)

QUALITY_VERSION = "v1"
QUALITY_CONTENT_FIELDS = (
    "corp_code",
    "bsns_year",
    "market",
    "financial_core_status",
    "auditor_status",
    "audit_fee_status",
    "policy_status",
    "kam_status",
    "audit_procedure_status",
    "group_audit_status",
    "investor_grade",
    "auditor_grade",
    "group_audit_grade",
    "blockers_json",
    "quality_version",
    "input_fingerprint",
    "evidence_summary_json",
)


class QualitySnapshotError(ValueError):
    """Raised when persisted quality content cannot be canonicalized."""


def _normalized_blockers(value: Any) -> list[str]:
    try:
        blockers = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise QualitySnapshotError(
            "blockers_json must be a JSON array of strings"
        ) from exc
    if (
        not isinstance(blockers, list)
        or any(not isinstance(blocker, str) for blocker in blockers)
    ):
        raise QualitySnapshotError(
            "blockers_json must be a JSON array of strings"
        )
    return sorted(blockers)


def _normalized_evidence_summary(value: Any) -> dict[str, Any]:
    try:
        summary = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise QualitySnapshotError(
            "evidence_summary_json must be a JSON object"
        ) from exc
    if not isinstance(summary, dict):
        raise QualitySnapshotError(
            "evidence_summary_json must be a JSON object"
        )
    return summary


_STATUS_ROW_FIELDS = dict(
    zip(
        QUALITY_STATUS_KEYS,
        (
            "financial_core_status",
            "auditor_status",
            "audit_fee_status",
            "policy_status",
            "kam_status",
            "audit_procedure_status",
            "group_audit_status",
        ),
        strict=True,
    )
)
_GRADE_ROW_FIELDS = dict(
    zip(
        QUALITY_GRADE_KEYS,
        (
            "investor_grade",
            "auditor_grade",
            "group_audit_grade",
        ),
        strict=True,
    )
)


def validate_quality_row_freshness(
    row: Mapping[str, Any],
) -> tuple[str, dict[str, object], list[str]]:
    """Validate summary schema, row semantics, and canonical fingerprint."""
    blockers = _normalized_blockers(row["blockers_json"])
    raw_summary = _normalized_evidence_summary(row["evidence_summary_json"])
    try:
        summary = validate_quality_evidence_summary(
            raw_summary,
            expected_quality_version=QUALITY_VERSION,
        )
    except (TypeError, ValueError) as exc:
        raise QualitySnapshotError(
            f"quality freshness evidence summary is invalid: {exc}"
        ) from exc
    fingerprint = str(row["input_fingerprint"] or "")
    try:
        expected_fingerprint = quality_input_fingerprint(summary)
    except (TypeError, ValueError) as exc:
        raise QualitySnapshotError(
            f"quality freshness evidence summary is invalid: {exc}"
        ) from exc
    if fingerprint != expected_fingerprint:
        raise QualitySnapshotError(
            "quality freshness input_fingerprint must match the canonical "
            "evidence summary"
        )
    expected_statuses = {
        key: str(row[field])
        for key, field in _STATUS_ROW_FIELDS.items()
    }
    expected_grades = {
        key: str(row[field])
        for key, field in _GRADE_ROW_FIELDS.items()
    }
    if (
        summary["statuses"] != expected_statuses
        or summary["grades"] != expected_grades
        or summary["blockers"] != blockers
        or summary["quality_version"] != str(row["quality_version"])
    ):
        raise QualitySnapshotError(
            "quality freshness evidence summary must match persisted quality "
            "fields"
        )
    return fingerprint, summary, blockers


def _canonical_quality_row(row: Mapping[str, Any]) -> dict[str, Any]:
    fingerprint, summary, blockers = validate_quality_row_freshness(row)
    return {
        field: (
            blockers
            if field == "blockers_json"
            else summary
            if field == "evidence_summary_json"
            else fingerprint
            if field == "input_fingerprint"
            else row[field]
        )
        for field in QUALITY_CONTENT_FIELDS
    }


def quality_content_digest(
    rows: Iterable[Mapping[str, Any]],
) -> str:
    """Hash every non-volatile persisted quality field in stable row order."""
    canonical_rows = sorted(
        (_canonical_quality_row(row) for row in rows),
        key=lambda row: (row["corp_code"], row["bsns_year"]),
    )
    payload = json.dumps(
        canonical_rows,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
