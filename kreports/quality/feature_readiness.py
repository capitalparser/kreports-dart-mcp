"""Feature-oriented evidence readiness for audit/investor MCP surfaces."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any


def _count(connection: sqlite3.Connection, table: str) -> int:
    try:
        return int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
    except sqlite3.Error:
        return 0


def evaluate_feature_readiness(
    db_path: str | Path,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    """Return evidence-bundle readiness, not merely row-volume metrics."""
    database = Path(db_path).expanduser().resolve(strict=True)
    manifest = (
        Path(manifest_path).expanduser().resolve(strict=True)
        if manifest_path
        else database.with_suffix(database.suffix + ".release.json")
    )
    with manifest.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    gate = payload.get("release_gate") or {}
    coverage = gate.get("feature_coverage") or {}
    with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as connection:
        counts = {
            table: _count(connection, table)
            for table in (
                "financial_facts_compact", "company_year_quality",
                "accounting_policy_items", "accounting_note_chapters",
                "audit_matter_items", "audit_procedure_items",
                "report_documents", "report_sections", "audit_fees",
                "audit_fee_observations", "subsidiary_auditor_matrix",
            )
        }
    features = {
        "investor_core_3y": {
            "coverage": coverage.get("investor_core_3y"),
            "evidence_counts": {"financial_facts_compact": counts["financial_facts_compact"], "company_year_quality": counts["company_year_quality"]},
            "next_action": "backfill citation-linked annual facts for missing three-year companies",
        },
        "investor_timeseries_5y": {
            "coverage": coverage.get("investor_timeseries_5y"),
            "evidence_counts": {"financial_facts_compact": counts["financial_facts_compact"]},
            "next_action": "complete five annual company-year metric bundles",
        },
        "accounting_policy_and_notes": {
            "coverage": coverage.get("accounting_policy"),
            "evidence_counts": {"accounting_policy_items": counts["accounting_policy_items"], "accounting_note_chapters": counts["accounting_note_chapters"]},
            "next_action": "retain full-body policy sections with company/year/receipt linkage",
        },
        "kam_and_audit_procedure": {
            "coverage": coverage.get("audit_procedure"),
            "evidence_counts": {"audit_matter_items": counts["audit_matter_items"], "audit_procedure_items": counts["audit_procedure_items"], "report_documents": counts["report_documents"], "report_sections": counts["report_sections"]},
            "next_action": "collect full audit-report KAM bodies and persist receipt-linked procedure steps",
        },
        "materiality_and_audit_hours": {
            "coverage": coverage.get("materiality_benchmark"),
            "evidence_counts": {"audit_fees": counts["audit_fees"], "audit_fee_observations": counts["audit_fee_observations"], "financial_facts_compact": counts["financial_facts_compact"]},
            "next_action": "complete three-year financial plus fee/hour source bundles",
        },
        "group_audit": {
            "coverage": None,
            "evidence_counts": {"subsidiary_auditor_matrix": counts["subsidiary_auditor_matrix"]},
            "next_action": "collect and link subsidiary auditor schedules for representative groups",
        },
    }
    for item in features.values():
        result = item.get("coverage") or {}
        item["status"] = "usable" if float(result.get("coverage_pct", 0)) >= float(result.get("threshold_pct", 95)) else "limited"
    return {
        "schema": "feature_readiness_report", "schema_version": "1",
        "db_path": str(database),
        "dataset_version": (payload.get("dataset") or {}).get("version"),
        "release_blockers": gate.get("blockers") or [], "features": features,
    }
