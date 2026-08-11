"""Read-only, profile-aware release gates for prepared KReports datasets."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from collections.abc import Iterable
from typing import Any, TypedDict

from sqlalchemy import inspect, text

from kreports.analysis.readiness import (
    auditor_feature_readiness_snapshot,
    investor_dataset_readiness_snapshot,
)
from kreports.db.engine import get_session
from kreports.db.migrations import MIGRATIONS, _checksum
from kreports.db.quality_snapshot import (
    QUALITY_CONTENT_FIELDS,
    QUALITY_VERSION,
    QualitySnapshotError,
    quality_content_digest,
)
from kreports.maintenance.listing_periods import listing_eligibility_snapshot
from kreports.runtime import is_readonly_mode


PROFILE_PUBLIC_RUNTIME = "public_runtime"
PROFILE_AUDITOR_FULL = "auditor_full"
SUPPORTED_PROFILES = (PROFILE_PUBLIC_RUNTIME, PROFILE_AUDITOR_FULL)
EXPECTED_TOOL_COUNT = 33
FEATURE_COVERAGE_THRESHOLD_PCT = 95.0
FEATURE_COVERAGE_THRESHOLD_BASIS_POINTS = 9500
INVESTOR_CORE_3Y = "investor_core_3y"
INVESTOR_TIMESERIES_5Y = "investor_timeseries_5y"
INVESTOR_CORE_COMPATIBILITY_ALIAS = "investor_core"
MATERIALITY_BENCHMARK_WINDOW_YEARS = 3
MATERIALITY_BENCHMARK_METRICS = (
    "profit_before_tax",
    "revenue",
    "assets",
    "equity",
)
_MATERIALITY_CITATION_BASIS = "company_year_annual_filing_match"
_MATERIALITY_COVERAGE_METADATA = {
    "window_years": MATERIALITY_BENCHMARK_WINDOW_YEARS,
    "metric_keys": list(MATERIALITY_BENCHMARK_METRICS),
    "fs_div_policy": "one_of_CFS_or_OFS_per_metric",
    "unit": "KRW",
    "citation_basis": _MATERIALITY_CITATION_BASIS,
    "receipt_policy": "exact_canonical_company_year_annual_filing",
    "annual_source_policy": "latest_company_year_fs_annual_filing",
    "duplicate_policy": "value_and_provenance_identical_only",
    "amount_policy": "finite_sqlite_integer_or_real",
    "pbt_policy": "direct_or_profit_loss_plus_tax_expense",
}
_INVESTOR_CORE_3Y_COVERAGE_METADATA = {
    "window_years": 5,
    "minimum_available_years": 3,
    "current_year_financial_core_required": True,
    "current_year_disclosure_list_required": False,
    "annual_core_source": "exact_company_year_annual_filing",
    "grade_policy": "A_or_B",
}
_INVESTOR_TIMESERIES_5Y_COVERAGE_METADATA = {
    "window_years": 5,
    "minimum_available_years": 5,
    "current_year_financial_core_required": True,
    "current_year_disclosure_list_required": False,
    "annual_core_source": "exact_company_year_annual_filing",
    "grade_policy": "A_only",
}
_INVESTOR_CORE_COMPATIBILITY_ALIAS_METADATA = {
    "compatibility_alias_for": INVESTOR_CORE_3Y,
}
STALE_BACKFILL_AGE = timedelta(hours=1)
CORE_MARKETS = ("KOSPI", "KOSDAQ")
_HISTORICAL_MEMBERSHIP_TABLE = "company_year_listing_memberships"
_HISTORICAL_MEMBERSHIP_REQUIRED_COLUMNS = {
    "corp_code",
    "bsns_year",
    "market",
    "status",
}
REQUIRED_TABLES = (
    "companies",
    "disclosures",
    "financials",
    "financial_facts_compact",
    "report_sections",
    "evidence_documents",
    "backfill_runs",
    "company_year_quality",
)


class CoverageResult(TypedDict):
    numerator: int
    denominator: int
    coverage_pct: float
    threshold_pct: float


class BlockerGuidance(TypedDict):
    blocker: str
    owner: str
    action: str


class ReleaseGateReport(TypedDict):
    ok: bool
    profile: str
    schema_version: str
    dataset_version: str
    required_failures: list[str]
    degraded_features: list[str]
    tool_count: int
    coverage_year: int | None
    coverage: dict[str, CoverageResult]
    coverage_metadata: dict[str, dict[str, Any]]
    denominators: dict[str, int]
    excluded_populations: dict[str, dict[str, int]]
    blocker_guidance: list[BlockerGuidance]


_EXACT_BLOCKER_GUIDANCE: dict[str, tuple[str, str]] = {
    "investor_core_3y_coverage": (
        "dataset_backfill_maintainer",
        "backfill and validate three-year investor-core coverage before release",
    ),
    "investor_core_coverage": (
        "dataset_backfill_maintainer",
        "backfill and validate investor-core company-year coverage before release",
    ),
    "release_manifest_unavailable": (
        "dataset_release_maintainer",
        "write a validated dataset manifest from the prepared runtime DB",
    ),
    "quality_input_stale": (
        "dataset_backfill_maintainer",
        "rebuild company-year quality after newer policy or note evidence",
    ),
    "schema_migration_contract_mismatch": (
        "database_schema_maintainer",
        "migrate the release DB to the approved schema revision before release",
    ),
    "unexpected_tool_count": (
        "mcp_contract_maintainer",
        "restore the approved 33-tool public catalog before release",
    ),
    "runtime_not_readonly": (
        "runtime_operator",
        "run release verification with KREPORTS_RUNTIME_MODE=readonly",
    ),
}


def describe_release_blockers(
    blockers: Iterable[str],
) -> list[BlockerGuidance]:
    """Return deterministic owner/action guidance without altering readiness."""
    guidance: list[BlockerGuidance] = []
    for blocker in sorted(set(str(item) for item in blockers)):
        owner, action = _EXACT_BLOCKER_GUIDANCE.get(
            blocker,
            (
                "dataset_release_maintainer",
                "inspect the named release blocker and rebuild proof only after remediation",
            ),
        )
        if blocker.startswith("missing_required_index:"):
            owner = "database_schema_maintainer"
            action = "create the required index in a prepared release DB before release"
        guidance.append({
            "blocker": blocker,
            "owner": owner,
            "action": action,
        })
    return guidance


def _empty_quality_contract() -> tuple[
    int | None,
    dict[str, CoverageResult],
    dict[str, dict[str, Any]],
    dict[str, int],
    dict[str, dict[str, int]],
]:
    return None, {}, {}, {}, {}


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


def _timestamp_is_newer_than_ledger(
    input_timestamp: Any,
    ledger_timestamp: Any,
) -> bool:
    """Compare persisted timestamps after explicitly normalizing to UTC."""
    input_at = _as_utc(input_timestamp)
    ledger_at = _as_utc(ledger_timestamp)
    return bool(input_at and ledger_at and input_at > ledger_at)


def _source_rows_are_newer_than_ledger(
    session: Any,
    query: str,
) -> bool:
    """Read source/ledger timestamp pairs without dialect-dependent coercion."""
    for row in session.execute(text(query)).mappings():
        if _timestamp_is_newer_than_ledger(
            row["input_fetched_at"],
            row["quality_updated_at"],
        ):
            return True
    return False


def _quality_inputs_are_newer_than_ledger(
    session: Any,
    *,
    table_names: set[str],
) -> bool:
    """Detect policy inputs written after their derived quality rows.

    The ledger fingerprint proves persisted quality content, while source
    receipt timestamps prove whether that content was derived after the policy
    evidence it summarizes.  The source families mirror ``_policy_status``:
    policy items, policy note chapters, and policy fetch outcomes.
    """
    quality_columns = {
        str(column["name"])
        for column in inspect(session.get_bind()).get_columns(
            "company_year_quality"
        )
    }
    if "updated_at" not in quality_columns:
        return False

    sources: list[str] = []
    if "accounting_policy_items" in table_names:
        policy_columns = {
            str(column["name"])
            for column in inspect(session.get_bind()).get_columns(
                "accounting_policy_items"
            )
        }
        if {"corp_code", "bsns_year", "fetched_at"} <= policy_columns:
            sources.append(
                """
                SELECT q.updated_at AS quality_updated_at,
                       p.fetched_at AS input_fetched_at
                FROM company_year_quality AS q
                JOIN accounting_policy_items AS p
                  ON p.corp_code=q.corp_code
                 AND p.bsns_year=q.bsns_year
                """
            )
    if "accounting_note_chapters" in table_names:
        note_columns = {
            str(column["name"])
            for column in inspect(session.get_bind()).get_columns(
                "accounting_note_chapters"
            )
        }
        if {
            "corp_code",
            "bsns_year",
            "section_type",
            "fetched_at",
        } <= note_columns:
            sources.append(
                """
                SELECT q.updated_at AS quality_updated_at,
                       n.fetched_at AS input_fetched_at
                FROM company_year_quality AS q
                JOIN accounting_note_chapters AS n
                  ON n.corp_code=q.corp_code
                 AND n.bsns_year=q.bsns_year
                WHERE n.section_type='policy'
                """
            )
    if "fetch_log" in table_names:
        fetch_columns = {
            str(column["name"])
            for column in inspect(session.get_bind()).get_columns("fetch_log")
        }
        if {
            "task_type",
            "corp_code",
            "year",
            "fetched_at",
        } <= fetch_columns:
            sources.append(
                """
                SELECT q.updated_at AS quality_updated_at,
                       f.fetched_at AS input_fetched_at
                FROM company_year_quality AS q
                JOIN fetch_log AS f
                  ON f.corp_code=q.corp_code
                 AND f.year=q.bsns_year
                WHERE f.task_type IN (
                    'policy', 'policy_items', 'accounting_policy'
                )
                """
            )
    if not sources:
        return False
    return any(
        _source_rows_are_newer_than_ledger(session, source)
        for source in sources
    )


def _runtime_schema_state(
    session_scope=get_session,
) -> tuple[
    list[str],
    str,
    str,
    list[datetime],
    bool,
    int | None,
]:
    """Read schema, immutable manifest, and stale-run state without mutation."""
    failures: list[str] = []
    with session_scope() as session:
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

        schema_version = "unknown"
        dataset_version = "unknown"
        manifest_available = False
        coverage_year: int | None = None
        if {"schema_migrations", "dataset_manifest"}.issubset(table_names):
            try:
                recorded_migrations = [
                    (str(row["revision"]), str(row["checksum"]))
                    for row in session.execute(
                        text(
                            "SELECT revision, checksum "
                            "FROM schema_migrations ORDER BY revision"
                        )
                    ).mappings()
                ]
                expected_migrations = [
                    (migration.revision, _checksum(migration))
                    for migration in MIGRATIONS
                ]
                migration_contract_valid = (
                    recorded_migrations == expected_migrations
                )
                if not migration_contract_valid:
                    failures.append(
                        "schema_migration_contract_mismatch"
                    )
                migration_version = session.execute(
                    text(
                        "SELECT revision FROM schema_migrations "
                        "WHERE trim(revision) != '' "
                        "ORDER BY revision DESC LIMIT 1"
                    )
                ).scalar()
                manifest_row = session.execute(
                    text(
                        "SELECT manifest_id, schema_version, dataset_version, "
                        "generated_at, year_to, company_count, "
                        "disclosure_count, evidence_document_count, "
                        "quality_snapshot_json "
                        "FROM dataset_manifest "
                        "WHERE trim(schema_version) != '' "
                        "AND trim(dataset_version) != '' "
                        "AND generated_at IS NOT NULL "
                        "ORDER BY generated_at DESC LIMIT 1"
                    )
                ).mappings().first()
                generated_at = (
                    _as_utc(manifest_row["generated_at"])
                    if manifest_row
                    else None
                )
                live_counts = (
                    int(
                        session.execute(
                            text("SELECT COUNT(*) FROM companies")
                        ).scalar()
                        or 0
                    ),
                    int(
                        session.execute(
                            text("SELECT COUNT(*) FROM disclosures")
                        ).scalar()
                        or 0
                    ),
                    int(
                        session.execute(
                            text(
                                "SELECT COUNT(*) FROM evidence_documents"
                            )
                        ).scalar()
                        or 0
                    ),
                )
                manifest_counts = (
                    (
                        int(manifest_row["company_count"]),
                        int(manifest_row["disclosure_count"]),
                        int(manifest_row["evidence_document_count"]),
                    )
                    if manifest_row
                    else None
                )
                counts_valid = manifest_counts == live_counts
                if manifest_row and not counts_valid:
                    failures.append("release_manifest_counts_mismatch")
                quality_rows = list(
                    session.execute(
                        text(
                            "SELECT "
                            + ", ".join(QUALITY_CONTENT_FIELDS)
                            + " FROM company_year_quality "
                            "ORDER BY corp_code, bsns_year"
                        )
                    ).mappings()
                )
                quality_row_count = len(quality_rows)
                live_coverage_year_value = session.execute(
                    text(
                        "SELECT MAX(bsns_year) "
                        "FROM company_year_quality"
                    )
                ).scalar()
                live_coverage_year = (
                    int(live_coverage_year_value)
                    if live_coverage_year_value is not None
                    else None
                )
                coverage_year_row_count = (
                    int(
                        session.execute(
                            text(
                                "SELECT COUNT(*) "
                                "FROM company_year_quality "
                                "WHERE bsns_year=:year"
                            ),
                            {"year": live_coverage_year},
                        ).scalar()
                        or 0
                    )
                    if live_coverage_year is not None
                    else 0
                )
                quality_versions = sorted(
                    {
                        str(row["quality_version"])
                        for row in quality_rows
                    }
                )
                live_quality_version = (
                    quality_versions[0]
                    if len(quality_versions) == 1
                    else QUALITY_VERSION
                    if not quality_versions
                    else None
                )
                quality_content_valid = True
                try:
                    content_digest = quality_content_digest(quality_rows)
                except QualitySnapshotError:
                    content_digest = None
                    quality_content_valid = False
                    failures.append("quality_snapshot_invalid")
                live_quality_snapshot = {
                    "content_digest": content_digest,
                    "coverage_year": live_coverage_year,
                    "coverage_year_row_count": coverage_year_row_count,
                    "quality_version": live_quality_version,
                    "row_count": quality_row_count,
                }
                try:
                    manifest_quality_snapshot = (
                        json.loads(
                            str(manifest_row["quality_snapshot_json"])
                        )
                        if manifest_row
                        else None
                    )
                except (TypeError, ValueError, json.JSONDecodeError):
                    manifest_quality_snapshot = None
                quality_version_supported = (
                    (
                        not quality_versions
                        or quality_versions == [QUALITY_VERSION]
                    )
                    and isinstance(manifest_quality_snapshot, dict)
                    and manifest_quality_snapshot.get("quality_version")
                    == QUALITY_VERSION
                )
                if manifest_row and not quality_version_supported:
                    failures.append("quality_version_unsupported")
                quality_snapshot_valid = (
                    quality_content_valid
                    and quality_version_supported
                    and manifest_quality_snapshot == live_quality_snapshot
                )
                if manifest_row and not quality_snapshot_valid:
                    failures.append("quality_snapshot_mismatch")
                if _quality_inputs_are_newer_than_ledger(
                    session,
                    table_names=table_names,
                ):
                    failures.append("quality_input_stale")
                manifest_year = (
                    int(manifest_row["year_to"])
                    if manifest_row
                    and manifest_row["year_to"] is not None
                    else None
                )
                coverage_year_aligned = (
                    manifest_year == live_coverage_year
                )
                if manifest_row and not coverage_year_aligned:
                    failures.append("release_manifest_year_mismatch")
                if (
                    manifest_row
                    and migration_version
                    and migration_contract_valid
                    and counts_valid
                    and quality_snapshot_valid
                    and coverage_year_aligned
                    and generated_at is not None
                    and generated_at <= datetime.now(timezone.utc)
                    and manifest_row["schema_version"] == migration_version
                    and manifest_row["manifest_id"]
                    == manifest_row["dataset_version"]
                ):
                    schema_version = str(manifest_row["schema_version"])
                    dataset_version = str(manifest_row["dataset_version"])
                    coverage_year = live_coverage_year
                    manifest_available = True
            except Exception:
                failures.append("unreadable_release_manifest")

        stale_rows: list[datetime] = []
        if "backfill_runs" in table_names:
            try:
                started_at_rows = session.execute(
                    text(
                        "SELECT started_at FROM backfill_runs "
                        "WHERE status='running'"
                    )
                ).scalars()
                stale_rows = [
                    value
                    for value in (
                        _as_utc(row) for row in started_at_rows
                    )
                    if value
                ]
            except Exception:
                if "unreadable_table:backfill_runs" not in failures:
                    failures.append("unreadable_table:backfill_runs")
    return (
        failures,
        schema_version,
        dataset_version,
        stale_rows,
        manifest_available,
        coverage_year,
    )


def _coverage_result(numerator: int, denominator: int) -> CoverageResult:
    coverage_pct = (
        round(100.0 * numerator / denominator, 2)
        if denominator
        else 0.0
    )
    return {
        "numerator": numerator,
        "denominator": denominator,
        "coverage_pct": coverage_pct,
        "threshold_pct": FEATURE_COVERAGE_THRESHOLD_PCT,
    }


def _verified_membership_population(
    session: Any,
    *,
    table_names: set[str],
    required_years: tuple[int, ...],
) -> tuple[list[str], dict[str, Any], dict[str, int]]:
    """Return only companies proved listed at every required year end.

    Current ``companies.market`` and ``companies.stock_code`` values are
    intentionally absent from the eligibility predicate: they cannot establish
    whether a company was listed in an earlier fiscal year. The company join
    only rejects orphaned membership rows. Historical membership rows are
    written only after their KRX provenance is validated by the importer, and
    the release gate accepts only ``verified`` KOSPI/KOSDAQ observations.
    """
    years = tuple(sorted(set(int(year) for year in required_years)))
    unavailable = {
        "historical_membership_evidence_unavailable": 1,
        "missing_required_membership_year": 0,
        "missing_market_year": len(years) * len(CORE_MARKETS),
        "unverified_membership_observation": 0,
    }
    metadata: dict[str, Any] = {
        "population_source": "verified_company_year_listing_memberships",
        "membership_status": "verified",
        "membership_market_scope": list(CORE_MARKETS),
        "membership_required_years": list(years),
        "membership_evidence_available": False,
        "membership_rule": "company_must_be_member_in_every_required_year",
    }
    if not years or _HISTORICAL_MEMBERSHIP_TABLE not in table_names:
        return [], metadata, unavailable
    columns = {
        str(column["name"])
        for column in inspect(session.get_bind()).get_columns(
            _HISTORICAL_MEMBERSHIP_TABLE
        )
    }
    if not _HISTORICAL_MEMBERSHIP_REQUIRED_COLUMNS.issubset(columns):
        return [], metadata, unavailable

    year_params = {
        f"membership_year_{index}": year
        for index, year in enumerate(years)
    }
    year_bindings = ", ".join(f":{key}" for key in year_params)
    verified_rows = session.execute(
        text(
            f"""
            SELECT m.corp_code
            FROM {_HISTORICAL_MEMBERSHIP_TABLE} AS m
            JOIN companies AS c ON c.corp_code=m.corp_code
            WHERE m.bsns_year IN ({year_bindings})
              AND m.market IN ('KOSPI', 'KOSDAQ')
              AND m.status='verified'
            GROUP BY m.corp_code
            HAVING COUNT(DISTINCT m.bsns_year)=:membership_year_count
            ORDER BY m.corp_code
            """
        ),
        {**year_params, "membership_year_count": len(years)},
    ).scalars().all()
    verified_year_count = int(
        session.execute(
            text(
                f"""
                SELECT COUNT(DISTINCT m.bsns_year)
                FROM {_HISTORICAL_MEMBERSHIP_TABLE} AS m
                WHERE m.bsns_year IN ({year_bindings})
                  AND m.market IN ('KOSPI', 'KOSDAQ')
                  AND m.status='verified'
                """
            ),
            year_params,
        ).scalar()
        or 0
    )
    candidate_count = int(
        session.execute(
            text(
                f"""
                SELECT COUNT(DISTINCT m.corp_code)
                FROM {_HISTORICAL_MEMBERSHIP_TABLE} AS m
                JOIN companies AS c ON c.corp_code=m.corp_code
                WHERE m.bsns_year IN ({year_bindings})
                  AND m.market IN ('KOSPI', 'KOSDAQ')
                  AND m.status='verified'
                """
            ),
            year_params,
        ).scalar()
        or 0
    )
    verified_market_years = {
        (int(row["bsns_year"]), str(row["market"]))
        for row in session.execute(
            text(
                f"""
                SELECT DISTINCT m.bsns_year, m.market
                FROM {_HISTORICAL_MEMBERSHIP_TABLE} AS m
                WHERE m.bsns_year IN ({year_bindings})
                  AND m.market IN ('KOSPI', 'KOSDAQ')
                  AND m.status='verified'
                """
            ),
            year_params,
        ).mappings()
    }
    missing_market_year = sum(
        (year, market) not in verified_market_years
        for year in years
        for market in CORE_MARKETS
    )
    unverified_count = int(
        session.execute(
            text(
                f"""
                SELECT COUNT(DISTINCT m.corp_code)
                FROM {_HISTORICAL_MEMBERSHIP_TABLE} AS m
                WHERE m.bsns_year IN ({year_bindings})
                  AND m.market IN ('KOSPI', 'KOSDAQ')
                  AND m.status!='verified'
                """
            ),
            year_params,
        ).scalar()
        or 0
    )
    evidence_available = (
        verified_year_count == len(years)
        and missing_market_year == 0
    )
    metadata["membership_evidence_available"] = evidence_available
    metadata["eligible_company_count"] = (
        len(verified_rows) if evidence_available else 0
    )
    return (
        [str(corp_code) for corp_code in verified_rows] if evidence_available else [],
        metadata,
        {
            "historical_membership_evidence_unavailable": int(
                not evidence_available
            ),
            "missing_required_membership_year": max(
                candidate_count - len(verified_rows), 0
            ),
            "missing_market_year": missing_market_year,
            "unverified_membership_observation": unverified_count,
        },
    )


def _quality_population_count(
    session: Any,
    *,
    corp_codes: list[str],
    coverage_year: int,
    condition: str,
) -> int:
    """Count coverage-year quality rows within a historical population."""
    if not corp_codes:
        return 0
    corp_bindings = ", ".join(
        f":quality_corp_{index}" for index in range(len(corp_codes))
    )
    return int(
        session.execute(
            text(
                "SELECT COUNT(*) FROM company_year_quality q "
                f"WHERE q.corp_code IN ({corp_bindings}) "
                "AND q.bsns_year=:quality_coverage_year "
                f"AND ({condition})"
            ),
            {
                "quality_coverage_year": coverage_year,
                **{
                    f"quality_corp_{index}": corp_code
                    for index, corp_code in enumerate(corp_codes)
                },
            },
        ).scalar()
        or 0
    )


def _materiality_benchmark_coverage(
    session: Any,
    *,
    table_names: set[str],
    coverage_year: int,
    eligible_corp_codes: list[str],
) -> tuple[CoverageResult, dict[str, int]]:
    """Count only one-metric, one-statement three-year proven series.

    This is deliberately a bounded SQL aggregation rather than a loop of MCP
    calls.  A compact row is counted only when its exact (not parent/child)
    receipt equals a matching annual disclosure for the same company and year.
    """
    excluded = {
        "zero_proven_years": len(eligible_corp_codes),
        "one_proven_year": 0,
        "two_proven_years": 0,
    }
    required_tables = {"companies", "disclosures", "financial_facts_compact"}
    required_columns = {
        "corp_code",
        "bsns_year",
        "fs_div",
        "metric_key",
        "amount",
        "source_account_id",
        "source_table",
        "unit",
        "period_type",
        "citation_rcept_no",
        "citation_report_nm",
        "citation_basis",
        "quality_status",
    }
    if not required_tables.issubset(table_names):
        return _coverage_result(0, len(eligible_corp_codes)), excluded
    columns = {
        str(column["name"])
        for column in inspect(session.get_bind()).get_columns(
            "financial_facts_compact"
        )
    }
    if not required_columns.issubset(columns):
        return _coverage_result(0, len(eligible_corp_codes)), excluded
    if not eligible_corp_codes:
        return _coverage_result(0, 0), excluded

    start_year = coverage_year - MATERIALITY_BENCHMARK_WINDOW_YEARS + 1
    metrics = ", ".join(
        f":materiality_metric_{index}"
        for index in range(len(MATERIALITY_BENCHMARK_METRICS) + 2)
    )
    eligible_values = ", ".join(
        f"(:materiality_corp_{index})"
        for index in range(len(eligible_corp_codes))
    )
    result = session.execute(
        text(
            f"""
            WITH eligible(corp_code) AS (
                VALUES {eligible_values}
            ),
            fact_scopes AS (
                SELECT DISTINCT
                       f.corp_code, f.bsns_year, f.fs_div
                FROM financial_facts_compact AS f
                JOIN eligible AS l ON l.corp_code=f.corp_code
                WHERE f.bsns_year BETWEEN :materiality_start_year
                    AND :materiality_coverage_year
                  AND f.fs_div IN ('CFS', 'OFS')
                  AND f.metric_key IN ({metrics})
            ),
            annual_ranked AS (
                SELECT s.corp_code, s.bsns_year, s.fs_div,
                       d.rcept_no,
                       d.disc_date,
                       d.report_nm,
                       ROW_NUMBER() OVER (
                           PARTITION BY
                               s.corp_code, s.bsns_year, s.fs_div
                           ORDER BY d.disc_date DESC, d.rcept_no DESC
                       ) AS source_rank
                FROM fact_scopes AS s
                JOIN disclosures AS d ON d.corp_code=s.corp_code
                WHERE d.report_nm LIKE
                      ('%사업보고서 (' || s.bsns_year || '.%')
            ),
            latest_annual AS (
                SELECT corp_code, bsns_year, fs_div,
                       rcept_no, disc_date, report_nm
                FROM annual_ranked
                WHERE source_rank=1
                  AND LENGTH(rcept_no)=14
                  AND rcept_no NOT GLOB '*[^0-9]*'
                  AND CAST(SUBSTR(rcept_no, 1, 4) AS INTEGER)
                      BETWEEN bsns_year AND bsns_year + 10
                  AND SUBSTR(rcept_no, 1, 8)=
                      STRFTIME('%Y%m%d', disc_date)
            ),
            candidate_rows AS (
                SELECT f.corp_code, f.bsns_year, f.fs_div, f.metric_key,
                       f.amount, f.source_account_id, f.source_table,
                       f.unit, f.period_type, f.quality_status,
                       f.citation_rcept_no, f.citation_report_nm,
                       f.citation_basis,
                       CASE WHEN
                           TYPEOF(f.amount) IN ('integer', 'real')
                           AND CAST(f.amount AS TEXT) NOT IN ('Inf', '-Inf')
                           AND TRIM(COALESCE(f.source_account_id, '')) != ''
                           AND f.source_table IN (
                               'financial_facts', 'financials'
                           )
                           AND f.unit='KRW'
                           AND f.quality_status='usable'
                           AND f.citation_basis=
                               :materiality_citation_basis
                           AND LENGTH(f.citation_rcept_no)=14
                           AND f.citation_rcept_no NOT GLOB '*[^0-9]*'
                           AND f.citation_rcept_no=
                               a.rcept_no
                           AND f.citation_report_nm=a.report_nm
                           AND (
                               (f.metric_key IN (
                                   'profit_before_tax', 'revenue',
                                   'profit_loss', 'tax_expense'
                                ) AND f.period_type='duration')
                               OR
                               (f.metric_key IN ('assets', 'equity')
                                AND f.period_type='instant')
                           )
                           THEN 1 ELSE 0
                       END AS row_admissible
                FROM financial_facts_compact AS f
                JOIN eligible AS l ON l.corp_code=f.corp_code
                LEFT JOIN latest_annual AS a
                  ON a.corp_code=f.corp_code
                 AND a.bsns_year=f.bsns_year
                 AND a.fs_div=f.fs_div
                WHERE f.bsns_year BETWEEN :materiality_start_year
                    AND :materiality_coverage_year
                  AND f.fs_div IN ('CFS', 'OFS')
                  AND f.metric_key IN ({metrics})
            ),
            identity_checked AS (
                SELECT corp_code, bsns_year, fs_div, metric_key,
                       MIN(amount) AS amount,
                       MIN(citation_rcept_no) AS citation_rcept_no
                FROM candidate_rows
                GROUP BY corp_code, bsns_year, fs_div, metric_key
                HAVING SUM(row_admissible)=COUNT(*)
                   AND MIN(amount)=MAX(amount)
                   AND COUNT(DISTINCT source_account_id)=1
                   AND COUNT(DISTINCT source_table)=1
                   AND COUNT(DISTINCT unit)=1
                   AND COUNT(DISTINCT period_type)=1
                   AND COUNT(DISTINCT quality_status)=1
                   AND COUNT(DISTINCT citation_rcept_no)=1
                   AND COUNT(DISTINCT citation_report_nm)=1
                   AND COUNT(DISTINCT citation_basis)=1
            ),
            direct_benchmarks AS (
                SELECT corp_code, bsns_year, fs_div, metric_key
                FROM identity_checked
                WHERE metric_key IN (
                    'profit_before_tax', 'revenue', 'assets', 'equity'
                )
            ),
            derived_pbt AS (
                SELECT p.corp_code, p.bsns_year, p.fs_div,
                       'profit_before_tax' AS metric_key
                FROM identity_checked AS p
                JOIN identity_checked AS t
                  ON t.corp_code=p.corp_code
                 AND t.bsns_year=p.bsns_year
                 AND t.fs_div=p.fs_div
                 AND t.metric_key='tax_expense'
                WHERE p.metric_key='profit_loss'
                  AND p.citation_rcept_no=t.citation_rcept_no
            ),
            qualified AS (
                SELECT corp_code, bsns_year, fs_div, metric_key
                FROM direct_benchmarks
                UNION
                SELECT corp_code, bsns_year, fs_div, metric_key
                FROM derived_pbt
            ),
            metric_support AS (
                SELECT corp_code, fs_div, metric_key,
                       COUNT(DISTINCT bsns_year) AS proven_year_count
                FROM qualified
                GROUP BY corp_code, fs_div, metric_key
            ),
            company_support AS (
                SELECT l.corp_code,
                       COALESCE(MAX(m.proven_year_count), 0)
                           AS proven_year_count
                FROM eligible AS l
                LEFT JOIN metric_support AS m ON m.corp_code=l.corp_code
                GROUP BY l.corp_code
            )
            SELECT
                COALESCE(SUM(CASE
                    WHEN proven_year_count >= :materiality_window_years
                    THEN 1 ELSE 0 END), 0) AS numerator,
                COALESCE(SUM(CASE
                    WHEN proven_year_count=0 THEN 1 ELSE 0 END), 0)
                    AS zero_proven_years,
                COALESCE(SUM(CASE
                    WHEN proven_year_count=1 THEN 1 ELSE 0 END), 0)
                    AS one_proven_year,
                COALESCE(SUM(CASE
                    WHEN proven_year_count=2 THEN 1 ELSE 0 END), 0)
                    AS two_proven_years
            FROM company_support
            """
        ),
        {
            "materiality_start_year": start_year,
            "materiality_coverage_year": coverage_year,
            "materiality_citation_basis": _MATERIALITY_CITATION_BASIS,
            "materiality_window_years": MATERIALITY_BENCHMARK_WINDOW_YEARS,
            **{
                f"materiality_metric_{index}": metric
                for index, metric in enumerate(
                    (*MATERIALITY_BENCHMARK_METRICS, "profit_loss", "tax_expense")
                )
            },
            **{
                f"materiality_corp_{index}": corp_code
                for index, corp_code in enumerate(eligible_corp_codes)
            },
        },
    ).mappings().one()
    numerator = int(result["numerator"] or 0)
    excluded = {
        "zero_proven_years": int(result["zero_proven_years"] or 0),
        "one_proven_year": int(result["one_proven_year"] or 0),
        "two_proven_years": int(result["two_proven_years"] or 0),
    }
    return _coverage_result(numerator, len(eligible_corp_codes)), excluded


def _quality_coverage(
    manifest_year: int | None,
    session_scope=get_session,
) -> tuple[
    int | None,
    dict[str, CoverageResult],
    dict[str, dict[str, Any]],
    dict[str, int],
    dict[str, dict[str, int]],
]:
    """Calculate exact current-population coverage and exclusions."""
    with session_scope() as session:
        table_names = set(inspect(session.get_bind()).get_table_names())
        if "company_year_quality" not in table_names:
            return _empty_quality_contract()
        coverage_year = manifest_year
        if coverage_year is None:
            value = session.execute(
                text("SELECT MAX(bsns_year) FROM company_year_quality")
            ).scalar()
            coverage_year = int(value) if value is not None else None
        if coverage_year is None:
            return _empty_quality_contract()

        core_3y_population, core_3y_metadata, core_3y_exclusions = (
            _verified_membership_population(
                session,
                table_names=table_names,
                required_years=(coverage_year - 2, coverage_year - 1, coverage_year),
            )
        )
        timeseries_population, timeseries_metadata, timeseries_exclusions = (
            _verified_membership_population(
                session,
                table_names=table_names,
                required_years=tuple(range(coverage_year - 4, coverage_year + 1)),
            )
        )
        current_population, current_metadata, current_exclusions = (
            _verified_membership_population(
                session,
                table_names=table_names,
                required_years=(coverage_year,),
            )
        )
        investor_core_3y_numerator = _quality_population_count(
            session,
            corp_codes=core_3y_population,
            coverage_year=coverage_year,
            condition=(
                "q.investor_grade IN ('A', 'B') "
                "AND q.financial_core_status='available'"
            ),
        )
        investor_timeseries_5y_numerator = _quality_population_count(
            session,
            corp_codes=timeseries_population,
            coverage_year=coverage_year,
            condition="q.investor_grade='A'",
        )
        policy_numerator = _quality_population_count(
            session,
            corp_codes=current_population,
            coverage_year=coverage_year,
            condition="q.policy_status IN ('full_body', 'summary_only')",
        )
        procedure_not_applicable = _quality_population_count(
            session,
            corp_codes=current_population,
            coverage_year=coverage_year,
            condition="q.audit_procedure_status='not_applicable'",
        )
        procedure_denominator = max(
            len(current_population) - procedure_not_applicable,
            0,
        )
        procedure_numerator = _quality_population_count(
            session,
            corp_codes=current_population,
            coverage_year=coverage_year,
            condition="q.audit_procedure_status='available'",
        )
        materiality_coverage, materiality_exclusions = (
            _materiality_benchmark_coverage(
                session,
                table_names=table_names,
                coverage_year=coverage_year,
                eligible_corp_codes=core_3y_population,
            )
        )

    coverage = {
        INVESTOR_CORE_3Y: _coverage_result(
            investor_core_3y_numerator,
            len(core_3y_population),
        ),
        INVESTOR_TIMESERIES_5Y: _coverage_result(
            investor_timeseries_5y_numerator,
            len(timeseries_population),
        ),
        # The historical key is an explicit alias rather than a five-year
        # claim. New consumers must use the named windows above.
        INVESTOR_CORE_COMPATIBILITY_ALIAS: _coverage_result(
            investor_core_3y_numerator,
            len(core_3y_population),
        ),
        "accounting_policy": _coverage_result(
            policy_numerator,
            len(current_population),
        ),
        "audit_procedure": _coverage_result(
            procedure_numerator,
            procedure_denominator,
        ),
        "materiality_benchmark": materiality_coverage,
    }
    denominators = {
        feature: result["denominator"]
        for feature, result in coverage.items()
    }
    excluded_populations = {
        INVESTOR_CORE_3Y: dict(core_3y_exclusions),
        INVESTOR_TIMESERIES_5Y: dict(timeseries_exclusions),
        INVESTOR_CORE_COMPATIBILITY_ALIAS: dict(core_3y_exclusions),
        "accounting_policy": dict(current_exclusions),
        "audit_procedure": {
            **current_exclusions,
            "explicit_no_kam": procedure_not_applicable,
        },
        "materiality_benchmark": {
            **core_3y_exclusions,
            **materiality_exclusions,
        },
    }
    listing_metadata = listing_eligibility_snapshot(
        coverage_year,
        session_scope=session_scope,
    )
    return (
        coverage_year,
        coverage,
        {
            INVESTOR_CORE_3Y: {
                **_INVESTOR_CORE_3Y_COVERAGE_METADATA,
                **core_3y_metadata,
            },
            INVESTOR_TIMESERIES_5Y: {
                **_INVESTOR_TIMESERIES_5Y_COVERAGE_METADATA,
                **timeseries_metadata,
            },
            INVESTOR_CORE_COMPATIBILITY_ALIAS: dict(
                _INVESTOR_CORE_COMPATIBILITY_ALIAS_METADATA
            ),
            "accounting_policy": dict(current_metadata),
            "audit_procedure": dict(current_metadata),
            "listing_eligibility": listing_metadata,
            "materiality_benchmark": {
                **_MATERIALITY_COVERAGE_METADATA,
                **core_3y_metadata,
            },
        },
        denominators,
        excluded_populations,
    )


def _tool_count() -> int:
    try:
        from kreports.mcp.tools import ALL_TOOLS

        return len(ALL_TOOLS)
    except Exception:
        return 0


def runtime_db_unavailable_report(
    profile: str = PROFILE_PUBLIC_RUNTIME,
) -> ReleaseGateReport:
    """Stable fail-closed response for readiness inspection failures."""
    coverage_year, coverage, coverage_metadata, denominators, exclusions = (
        _empty_quality_contract()
    )
    return {
        "ok": False,
        "profile": profile,
        "schema_version": "unknown",
        "dataset_version": "unknown",
        "required_failures": ["runtime_db_unavailable"],
        "degraded_features": [],
        "tool_count": _tool_count(),
        "coverage_year": coverage_year,
        "coverage": coverage,
        "coverage_metadata": coverage_metadata,
        "denominators": denominators,
        "excluded_populations": exclusions,
        "blocker_guidance": describe_release_blockers([
            "runtime_db_unavailable",
        ]),
    }


def _below_threshold(result: CoverageResult | None) -> bool:
    return (
        result is None
        or result["denominator"] <= 0
        or (
            result["numerator"] * 10_000
            < result["denominator"]
            * FEATURE_COVERAGE_THRESHOLD_BASIS_POINTS
        )
    )


def evaluate_release_gate(
    profile: str = PROFILE_PUBLIC_RUNTIME,
    *,
    session_scope=get_session,
    include_legacy_diagnostics: bool = True,
) -> ReleaseGateReport:
    """Evaluate a no-write release profile without repairing live state."""
    if profile not in SUPPORTED_PROFILES:
        raise ValueError(f"unsupported release gate profile: {profile}")

    try:
        (
            required_failures,
            schema_version,
            dataset_version,
            running_started_at,
            manifest_available,
            manifest_year,
        ) = _runtime_schema_state(session_scope)
        (
            coverage_year,
            coverage,
            coverage_metadata,
            denominators,
            excluded_populations,
        ) = _quality_coverage(manifest_year, session_scope)
    except Exception:
        return runtime_db_unavailable_report(profile)

    if not manifest_available:
        required_failures.append("release_manifest_unavailable")
    if not is_readonly_mode():
        required_failures.append("runtime_not_readonly")

    tool_count = _tool_count()
    if tool_count != EXPECTED_TOOL_COUNT:
        required_failures.append("unexpected_tool_count")

    cutoff = datetime.now(timezone.utc) - STALE_BACKFILL_AGE
    if any(started_at <= cutoff for started_at in running_started_at):
        required_failures.append("stale_backfill_run")

    investor_coverage = coverage.get(INVESTOR_CORE_3Y)
    if _below_threshold(investor_coverage):
        required_failures.append("investor_core_3y_coverage")

    degraded_features: list[str] = []
    for coverage_key, public_key in (
        (INVESTOR_TIMESERIES_5Y, INVESTOR_TIMESERIES_5Y),
        ("accounting_policy", "accounting_policy"),
        ("audit_procedure", "audit_procedure"),
        ("materiality_benchmark", "materiality_benchmark"),
    ):
        if _below_threshold(coverage.get(coverage_key)):
            degraded_features.append(public_key)

    # Older prepared databases may not have the ledger revision. Keep the
    # previous read-only diagnostics visible while failing closed on the
    # missing ledger table.
    if not coverage and include_legacy_diagnostics:
        try:
            snapshot = investor_dataset_readiness_snapshot()
            if snapshot.get("required_gaps"):
                required_failures.append("investor_core_3y_coverage")
        except Exception:
            required_failures.append(
                "investor_dataset_readiness_unavailable"
            )
        try:
            audit_snapshot = auditor_feature_readiness_snapshot()
            feature_status = audit_snapshot.get("feature_status") or {}
            if feature_status.get("accounting_policy_items") in {
                "missing",
                "degraded",
            }:
                degraded_features.append("accounting_policy")
            if (
                feature_status.get("audit_procedure_items")
                in {"missing", "degraded"}
                or feature_status.get("kam_procedure_hints")
                in {"missing", "degraded"}
            ):
                degraded_features.append("audit_procedure")
        except Exception:
            degraded_features.append("auditor_feature_readiness")

    if profile == PROFILE_AUDITOR_FULL:
        if "accounting_policy" in degraded_features:
            required_failures.append("accounting_policy_coverage")
        if "audit_procedure" in degraded_features:
            required_failures.append("audit_procedure_coverage")
        if "materiality_benchmark" in degraded_features:
            required_failures.append("materiality_benchmark_coverage")

    required_failures = sorted(set(required_failures))
    degraded_features = sorted(set(degraded_features))
    return {
        "ok": not required_failures,
        "profile": profile,
        "schema_version": schema_version,
        "dataset_version": dataset_version,
        "required_failures": required_failures,
        "degraded_features": degraded_features,
        "tool_count": tool_count,
        "coverage_year": coverage_year,
        "coverage": coverage,
        "coverage_metadata": coverage_metadata,
        "denominators": denominators,
        "excluded_populations": excluded_populations,
        "blocker_guidance": describe_release_blockers(required_failures),
    }
