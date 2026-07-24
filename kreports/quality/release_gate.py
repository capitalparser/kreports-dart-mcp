"""Conservative, read-only release gate for the public MCP runtime.

Task 3's versioned manifest and company-year quality ledger are not present in
this repository yet.  This module therefore reports unknown versions rather
than pretending that a table count or an unvalidated row is a release manifest.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import inspect, text

from kreports.analysis.readiness import auditor_feature_readiness_snapshot, investor_dataset_readiness_snapshot
from kreports.db.engine import get_session
from kreports.runtime import is_readonly_mode


PROFILE_PUBLIC_RUNTIME = "public_runtime"
EXPECTED_TOOL_COUNT = 31
STALE_BACKFILL_AGE = timedelta(hours=1)
REQUIRED_TABLES = (
    "companies",
    "disclosures",
    "financials",
    "financial_facts_compact",
    "report_sections",
    "backfill_runs",
)


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _runtime_schema_state() -> tuple[list[str], str, str, list[datetime], bool]:
    """Read table accessibility and the only safely derivable dataset freshness."""
    failures: list[str] = []
    dataset_timestamps: list[datetime] = []
    with get_session() as session:
        bind = session.get_bind()
        table_names = set(inspect(bind).get_table_names())
        for table_name in REQUIRED_TABLES:
            if table_name not in table_names:
                failures.append(f"missing_table:{table_name}")
                continue
            try:
                session.execute(text(f"SELECT 1 FROM {table_name} LIMIT 1"))
            except Exception:
                failures.append(f"unreadable_table:{table_name}")

        # A manifest schema is not part of this task's starting state.  A pair
        # of empty tables is not a manifest: both a recorded schema revision
        # and a non-empty dataset version must exist and agree.
        schema_version = "unknown"
        dataset_version = "unknown"
        manifest_available = False
        if {"schema_migrations", "dataset_manifest"}.issubset(table_names):
            try:
                migration_version = session.execute(
                    text("SELECT revision FROM schema_migrations WHERE trim(revision) != '' ORDER BY revision DESC LIMIT 1")
                ).scalar()
                manifest_row = session.execute(
                    text(
                        "SELECT schema_version, dataset_version FROM dataset_manifest "
                        "WHERE trim(schema_version) != '' AND trim(dataset_version) != '' "
                        "AND generated_at IS NOT NULL "
                        "ORDER BY generated_at DESC LIMIT 1"
                    )
                ).mappings().first()
                if manifest_row and migration_version and manifest_row["schema_version"] == migration_version:
                    schema_version = str(manifest_row["schema_version"])
                    dataset_version = str(manifest_row["dataset_version"])
                    manifest_available = True
            except Exception:
                failures.append("unreadable_release_manifest")
        for table_name, column_name in (("disclosures", "fetched_at"), ("financials", "fetched_at"), ("report_sections", "fetched_at")):
            if table_name not in table_names:
                continue
            try:
                value = session.execute(text(f"SELECT MAX({column_name}) FROM {table_name}")).scalar()
                timestamp = _as_utc(value)
                if timestamp:
                    dataset_timestamps.append(timestamp)
            except Exception:
                # Table accessibility was checked above; a missing optional
                # freshness column means no honest dataset version is known.
                continue
        if dataset_timestamps and not manifest_available:
            dataset_version = "db-max-fetched-at:" + max(dataset_timestamps).isoformat().replace("+00:00", "Z")

        stale_rows: list[datetime] = []
        if "backfill_runs" in table_names:
            try:
                started_at_rows = session.execute(
                    text("SELECT started_at FROM backfill_runs WHERE status='running'")
                ).scalars()
                stale_rows = [value for value in (_as_utc(row) for row in started_at_rows) if value]
            except Exception:
                if "unreadable_table:backfill_runs" not in failures:
                    failures.append("unreadable_table:backfill_runs")
    return failures, schema_version, dataset_version, stale_rows, manifest_available


def runtime_db_unavailable_report(profile: str = PROFILE_PUBLIC_RUNTIME) -> dict[str, Any]:
    """Stable fail-closed response for readiness inspection failures."""
    try:
        from kreports.mcp.tools import ALL_TOOLS
        tool_count = len(ALL_TOOLS)
    except Exception:
        tool_count = 0
    return {
        "ok": False,
        "profile": profile,
        "schema_version": "unknown",
        "dataset_version": "unknown",
        "required_failures": ["runtime_db_unavailable"],
        "degraded_features": [],
        "tool_count": tool_count,
    }


def evaluate_release_gate(profile: str = PROFILE_PUBLIC_RUNTIME) -> dict[str, Any]:
    """Evaluate a no-write public runtime gate without repairing live state."""
    if profile != PROFILE_PUBLIC_RUNTIME:
        raise ValueError(f"unsupported release gate profile: {profile}")

    try:
        required_failures, schema_version, dataset_version, running_started_at, manifest_available = _runtime_schema_state()
    except Exception:
        return runtime_db_unavailable_report(profile)
    if not manifest_available:
        required_failures.append("release_manifest_unavailable")
    if not is_readonly_mode():
        required_failures.append("runtime_not_readonly")

    from kreports.mcp.tools import ALL_TOOLS

    tool_count = len(ALL_TOOLS)
    if tool_count != EXPECTED_TOOL_COUNT:
        required_failures.append("unexpected_tool_count")

    cutoff = datetime.now(timezone.utc) - STALE_BACKFILL_AGE
    if any(started_at <= cutoff for started_at in running_started_at):
        required_failures.append("stale_backfill_run")

    degraded_features: list[str] = []
    try:
        snapshot = investor_dataset_readiness_snapshot()
        if snapshot.get("required_gaps"):
            required_failures.append("investor_core_coverage")
    except Exception:
        required_failures.append("investor_dataset_readiness_unavailable")

    try:
        audit_snapshot = auditor_feature_readiness_snapshot()
        feature_status = audit_snapshot.get("feature_status") or {}
        optional_features = {
            "audit_procedure_items": "audit_procedure",
            "accounting_policy_items": "accounting_policy",
            "kam_procedure_hints": "audit_procedure",
        }
        for source_key, public_key in optional_features.items():
            if feature_status.get(source_key) in {"missing", "degraded"}:
                degraded_features.append(public_key)
    except Exception:
        degraded_features.append("auditor_feature_readiness")

    return {
        "ok": not required_failures,
        "profile": profile,
        "schema_version": schema_version,
        "dataset_version": dataset_version,
        "required_failures": sorted(set(required_failures)),
        "degraded_features": sorted(set(degraded_features)),
        "tool_count": tool_count,
    }
