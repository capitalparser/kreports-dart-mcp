"""Deterministic content contract for the company-year quality ledger."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

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


def quality_content_digest(
    rows: Iterable[Mapping[str, Any]],
) -> str:
    """Hash every non-volatile persisted quality field in stable row order."""
    canonical_rows = sorted(
        (
            {
                field: (
                    _normalized_blockers(row[field])
                    if field == "blockers_json"
                    else _normalized_evidence_summary(row[field])
                    if field == "evidence_summary_json"
                    else row[field]
                )
                for field in QUALITY_CONTENT_FIELDS
            }
            for row in rows
        ),
        key=lambda row: (row["corp_code"], row["bsns_year"]),
    )
    payload = json.dumps(
        canonical_rows,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
