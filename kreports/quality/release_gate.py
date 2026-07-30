"""Read-only, profile-aware release gates for prepared KReports datasets."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
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
from kreports.runtime import is_readonly_mode


PROFILE_PUBLIC_RUNTIME = "public_runtime"
PROFILE_AUDITOR_FULL = "auditor_full"
SUPPORTED_PROFILES = (PROFILE_PUBLIC_RUNTIME, PROFILE_AUDITOR_FULL)
EXPECTED_TOOL_COUNT = 34
FEATURE_COVERAGE_THRESHOLD_PCT = 95.0
FEATURE_COVERAGE_THRESHOLD_BASIS_POINTS = 9500
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
STALE_BACKFILL_AGE = timedelta(hours=1)
CORE_MARKETS = ("KOSPI", "KOSDAQ")
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


def _materiality_benchmark_coverage(
    session: Any,
    *,
    table_names: set[str],
    coverage_year: int,
    core_denominator: int,
) -> tuple[CoverageResult, dict[str, int]]:
    """Count only one-metric, one-statement three-year proven series.

    This is deliberately a bounded SQL aggregation rather than a loop of MCP
    calls.  A compact row is counted only when its exact (not parent/child)
    receipt equals a matching annual disclosure for the same company and year.
    """
    excluded = {
        "zero_proven_years": core_denominator,
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
        return _coverage_result(0, core_denominator), excluded
    columns = {
        str(column["name"])
        for column in inspect(session.get_bind()).get_columns(
            "financial_facts_compact"
        )
    }
    if not required_columns.issubset(columns):
        return _coverage_result(0, core_denominator), excluded

    start_year = coverage_year - MATERIALITY_BENCHMARK_WINDOW_YEARS + 1
    metrics = ", ".join(
        f":materiality_metric_{index}"
        for index in range(len(MATERIALITY_BENCHMARK_METRICS) + 2)
    )
    result = session.execute(
        text(
            f"""
            WITH listed AS (
                SELECT corp_code
                FROM companies
                WHERE stock_code IS NOT NULL
                  AND market IN ('KOSPI', 'KOSDAQ')
            ),
            fact_scopes AS (
                SELECT DISTINCT
                       f.corp_code, f.bsns_year, f.fs_div
                FROM financial_facts_compact AS f
                JOIN listed AS l ON l.corp_code=f.corp_code
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
                JOIN listed AS l ON l.corp_code=f.corp_code
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
                FROM listed AS l
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
        },
    ).mappings().one()
    numerator = int(result["numerator"] or 0)
    excluded = {
        "zero_proven_years": int(result["zero_proven_years"] or 0),
        "one_proven_year": int(result["one_proven_year"] or 0),
        "two_proven_years": int(result["two_proven_years"] or 0),
    }
    return _coverage_result(numerator, core_denominator), excluded


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

        core_denominator = int(
            session.execute(
                text(
                    "SELECT COUNT(*) FROM companies "
                    "WHERE stock_code IS NOT NULL "
                    "AND market IN ('KOSPI', 'KOSDAQ')"
                )
            ).scalar()
            or 0
        )
        not_listed = int(
            session.execute(
                text(
                    "SELECT COUNT(*) FROM companies "
                    "WHERE stock_code IS NULL"
                )
            ).scalar()
            or 0
        )
        outside_core_markets = int(
            session.execute(
                text(
                    "SELECT COUNT(*) FROM companies "
                    "WHERE stock_code IS NOT NULL "
                    "AND (market IS NULL "
                    "OR market NOT IN ('KOSPI', 'KOSDAQ'))"
                )
            ).scalar()
            or 0
        )
        investor_numerator = int(
            session.execute(
                text(
                    "SELECT COUNT(*) FROM companies c "
                    "JOIN company_year_quality q "
                    "ON q.corp_code=c.corp_code "
                    "AND q.bsns_year=:year "
                    "WHERE c.stock_code IS NOT NULL "
                    "AND c.market IN ('KOSPI', 'KOSDAQ') "
                    "AND q.investor_grade IN ('A', 'B')"
                ),
                {"year": coverage_year},
            ).scalar()
            or 0
        )
        policy_numerator = int(
            session.execute(
                text(
                    "SELECT COUNT(*) FROM companies c "
                    "JOIN company_year_quality q "
                    "ON q.corp_code=c.corp_code "
                    "AND q.bsns_year=:year "
                    "WHERE c.stock_code IS NOT NULL "
                    "AND c.market IN ('KOSPI', 'KOSDAQ') "
                    "AND q.policy_status IN ('full_body', 'summary_only')"
                ),
                {"year": coverage_year},
            ).scalar()
            or 0
        )
        procedure_not_applicable = int(
            session.execute(
                text(
                    "SELECT COUNT(*) FROM companies c "
                    "JOIN company_year_quality q "
                    "ON q.corp_code=c.corp_code "
                    "AND q.bsns_year=:year "
                    "WHERE c.stock_code IS NOT NULL "
                    "AND c.market IN ('KOSPI', 'KOSDAQ') "
                    "AND q.audit_procedure_status='not_applicable'"
                ),
                {"year": coverage_year},
            ).scalar()
            or 0
        )
        procedure_denominator = max(
            core_denominator - procedure_not_applicable,
            0,
        )
        procedure_numerator = int(
            session.execute(
                text(
                    "SELECT COUNT(*) FROM companies c "
                    "JOIN company_year_quality q "
                    "ON q.corp_code=c.corp_code "
                    "AND q.bsns_year=:year "
                    "WHERE c.stock_code IS NOT NULL "
                    "AND c.market IN ('KOSPI', 'KOSDAQ') "
                    "AND q.audit_procedure_status='available'"
                ),
                {"year": coverage_year},
            ).scalar()
            or 0
        )
        materiality_coverage, materiality_exclusions = (
            _materiality_benchmark_coverage(
                session,
                table_names=table_names,
                coverage_year=coverage_year,
                core_denominator=core_denominator,
            )
        )

    common_exclusions = {
        "not_listed": not_listed,
        "outside_core_markets": outside_core_markets,
    }
    coverage = {
        "investor_core": _coverage_result(
            investor_numerator,
            core_denominator,
        ),
        "accounting_policy": _coverage_result(
            policy_numerator,
            core_denominator,
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
        "investor_core": dict(common_exclusions),
        "accounting_policy": dict(common_exclusions),
        "audit_procedure": {
            **common_exclusions,
            "explicit_no_kam": procedure_not_applicable,
        },
        "materiality_benchmark": {
            **common_exclusions,
            **materiality_exclusions,
        },
    }
    return (
        coverage_year,
        coverage,
        {"materiality_benchmark": dict(_MATERIALITY_COVERAGE_METADATA)},
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

    investor_coverage = coverage.get("investor_core")
    if _below_threshold(investor_coverage):
        required_failures.append("investor_core_coverage")

    degraded_features: list[str] = []
    for coverage_key, public_key in (
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
                required_failures.append("investor_core_coverage")
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
    }
