"""Deterministic content contract for the company-year quality ledger."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
import hashlib
import json
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
)


def quality_content_digest(
    rows: Iterable[Mapping[str, Any]],
) -> str:
    """Hash every non-volatile persisted quality field in stable row order."""
    canonical_rows = sorted(
        (
            {
                field: row[field]
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
