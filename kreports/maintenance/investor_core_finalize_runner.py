"""Fail-closed finalization of scoped investor derived data after backfill."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from contextlib import nullcontext
from dataclasses import dataclass
import hashlib
from pathlib import Path
import sqlite3
from typing import Any

from kreports.config import settings
from kreports.db.engine import write_dataset_manifest
from kreports.maintenance.financial_compact import (
    _normalized_corp_codes,
    rebuild_financial_facts_compact,
)
from kreports.maintenance.investor_core_backfill_runner import (
    MIN_FREE_SPACE_BYTES,
    InvestorCoreBackfillError,
    _bound_financial_writer,
    _capture_database_identity,
    _checkpoint_wal,
    _default_free_space_probe,
    _exclusive_execution_guard,
    _fail,
    _read_only_connection,
    _revalidate_database_identity,
    _resolve_regular_database,
    _sha256_file,
    _validate_expected_hash,
    _validate_free_space,
    _validate_process_binding,
)
from kreports.quality.company_year import rebuild_company_year_quality
from kreports.runtime import require_collector_mode


REPORT_SCHEMA = "investor_core_finalize_report"
REPORT_VERSION = 1


@dataclass(frozen=True)
class _FinalizeScope:
    corp_codes: tuple[str, ...]
    year_from: int
    year_to: int
    quality_year: int
    dataset_version: str


def _validated_scope(
    *,
    corp_codes: Iterable[str],
    year_from: int,
    year_to: int,
    quality_year: int,
    dataset_version: str,
) -> _FinalizeScope:
    """Validate explicit, bounded maintenance input before resolving a DB path."""
    try:
        normalized_corp_codes = _normalized_corp_codes(corp_codes)
    except ValueError as exc:
        raise _fail(
            "invalid_corp_codes",
            "corp_codes must be nonempty exact 8-digit strings",
        ) from exc
    if normalized_corp_codes is None:
        raise _fail(
            "invalid_corp_codes",
            "corp_codes must be nonempty exact 8-digit strings",
        )
    if (
        isinstance(year_from, bool)
        or not isinstance(year_from, int)
        or isinstance(year_to, bool)
        or not isinstance(year_to, int)
        or year_from <= 0
        or year_to <= 0
        or year_from > year_to
    ):
        raise _fail(
            "invalid_year_scope",
            "year_from and year_to must be a positive inclusive range",
        )
    if (
        isinstance(quality_year, bool)
        or not isinstance(quality_year, int)
        or quality_year < year_from
        or quality_year > year_to
    ):
        raise _fail(
            "invalid_quality_year",
            "quality_year must be within the financial year range",
        )
    if not isinstance(dataset_version, str):
        raise _fail(
            "invalid_dataset_version",
            "dataset_version must contain 1 to 80 characters",
        )
    normalized_version = dataset_version.strip()
    if not normalized_version or len(normalized_version) > 80:
        raise _fail(
            "invalid_dataset_version",
            "dataset_version must contain 1 to 80 characters",
        )
    return _FinalizeScope(
        corp_codes=normalized_corp_codes,
        year_from=year_from,
        year_to=year_to,
        quality_year=quality_year,
        dataset_version=normalized_version,
    )


def _scope_digest(scope: _FinalizeScope) -> str:
    value = "|".join(
        (
            *scope.corp_codes,
            str(scope.year_from),
            str(scope.year_to),
            str(scope.quality_year),
        )
    )
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _manifest_notes(scope: _FinalizeScope) -> str:
    """Keep durable manifest provenance bounded and free of runtime secrets."""
    return (
        "investor-core scoped derived-data finalization; "
        f"corp_code_count={len(scope.corp_codes)}; "
        f"scope_sha256={_scope_digest(scope)}; "
        f"financial_years={scope.year_from}-{scope.year_to}; "
        f"quality_year={scope.quality_year}"
    )


def _table_count(
    connection: sqlite3.Connection,
    query: str,
    parameters: tuple[object, ...],
) -> int:
    try:
        row = connection.execute(query, parameters).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return 0
        raise
    return int(row[0]) if row is not None else 0


def _in_predicate(values: tuple[str, ...]) -> str:
    return ", ".join("?" for _ in values)


def _scoped_row_counts(database: Path, scope: _FinalizeScope) -> dict[str, int]:
    """Collect only bounded finalization evidence using a readonly snapshot."""
    companies = _in_predicate(scope.corp_codes)
    with _read_only_connection(database) as connection:
        return {
            "financial_facts_compact": _table_count(
                connection,
                "SELECT COUNT(*) FROM financial_facts_compact "
                f"WHERE corp_code IN ({companies}) "
                "AND bsns_year BETWEEN ? AND ?",
                (*scope.corp_codes, scope.year_from, scope.year_to),
            ),
            "company_year_quality": _table_count(
                connection,
                "SELECT COUNT(*) FROM company_year_quality "
                f"WHERE corp_code IN ({companies}) AND bsns_year = ?",
                (*scope.corp_codes, scope.quality_year),
            ),
            "dataset_manifest": _table_count(
                connection,
                "SELECT COUNT(*) FROM dataset_manifest WHERE dataset_version = ?",
                (scope.dataset_version,),
            ),
        }


def _phase(status: str, result: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"status": status, "result": result}


def _report(
    database: Path,
    scope: _FinalizeScope,
    *,
    execute: bool,
    before_sha256: str,
    after_sha256: str | None,
    free_before: int | None,
    free_after: int | None,
    before_rows: dict[str, int] | None,
    after_rows: dict[str, int] | None,
    phases: dict[str, dict[str, Any]],
    stop_reason: str | None,
    stop_message: str | None,
    wal_checkpointed: bool | None,
) -> dict[str, Any]:
    return {
        "schema": REPORT_SCHEMA,
        "schema_version": str(REPORT_VERSION),
        "version": REPORT_VERSION,
        "db_path": str(database),
        "db_sha256_before": before_sha256,
        "db_sha256_after": after_sha256,
        "before_db_sha256": before_sha256,
        "after_db_sha256": after_sha256,
        "scope": {
            "corp_codes": list(scope.corp_codes),
            "year_from": scope.year_from,
            "year_to": scope.year_to,
            "quality_year": scope.quality_year,
            "dataset_version": scope.dataset_version,
            "scope_sha256": _scope_digest(scope),
        },
        "dry_run": not execute,
        "execute": execute,
        "completed": stop_reason is None,
        "release_ready": False,
        "stop_reason": stop_reason,
        "stop_message": stop_message,
        "phases": phases,
        "relevant_row_counts": {"before": before_rows, "after": after_rows},
        "before_row_counts": before_rows,
        "after_row_counts": after_rows,
        "free_space_before": free_before,
        "free_space_after": free_after,
        "free_space_minimum": MIN_FREE_SPACE_BYTES,
        "wal_checkpointed": wal_checkpointed,
    }


def _safe_probe(
    database: Path,
    disk_probe: Callable[[Path], int],
) -> int:
    try:
        return int(disk_probe(database))
    except Exception as exc:
        raise _fail("free_space_probe_failed", "free-space probe failed") from exc


def run_investor_core_finalize(
    db_path: str | Path,
    *,
    corp_codes: Iterable[str],
    year_from: int,
    year_to: int,
    quality_year: int,
    dataset_version: str,
    expected_db_sha256: str | None = None,
    execute: bool = False,
    disk_probe: Callable[[Path], int] = _default_free_space_probe,
    settings_obj: object = settings,
) -> dict[str, Any]:
    """Plan or safely run exact-company derived-data finalization.

    This deliberately does not evaluate a release gate. A successful run only
    proves the bounded derived writes and durable evidence in this report.
    """
    scope = _validated_scope(
        corp_codes=corp_codes,
        year_from=year_from,
        year_to=year_to,
        quality_year=quality_year,
        dataset_version=dataset_version,
    )
    database = _resolve_regular_database(db_path)
    identity = _capture_database_identity(database)
    _validate_process_binding(database, settings_obj)
    before_sha256 = _sha256_file(database)
    _validate_expected_hash(expected_db_sha256, before_sha256, execute=execute)
    if execute:
        try:
            require_collector_mode("run-investor-core-finalize")
        except RuntimeError as exc:
            raise _fail(
                "collector_mode_required",
                "collector runtime mode is required with --execute",
            ) from exc

    phases = {
        "compact": _phase("planned" if not execute else "not_run"),
        "quality": _phase("planned" if not execute else "not_run"),
        "manifest": _phase("planned" if not execute else "not_run"),
    }
    execution_scope = _exclusive_execution_guard(identity) if execute else nullcontext()
    with execution_scope:
        if execute:
            _revalidate_database_identity(identity)
            if _sha256_file(database) != before_sha256:
                raise _fail(
                    "database_changed_before_execution",
                    "database changed after preflight and before execution",
                )
        free_before = _safe_probe(database, disk_probe)
        before_rows = _scoped_row_counts(database, scope)
        if not execute:
            return _report(
                database,
                scope,
                execute=False,
                before_sha256=before_sha256,
                after_sha256=before_sha256,
                free_before=free_before,
                free_after=free_before,
                before_rows=before_rows,
                after_rows=before_rows,
                phases=phases,
                stop_reason=None,
                stop_message=None,
                wal_checkpointed=None,
            )

        try:
            _validate_free_space(free_before, disk_probe=disk_probe)
        except InvestorCoreBackfillError as exc:
            return _report(
                database,
                scope,
                execute=True,
                before_sha256=before_sha256,
                after_sha256=before_sha256,
                free_before=free_before,
                free_after=free_before,
                before_rows=before_rows,
                after_rows=before_rows,
                phases=phases,
                stop_reason=exc.code,
                stop_message=exc.message,
                wal_checkpointed=None,
            )

        stop_reason: str | None = None
        stop_message: str | None = None
        wal_checkpointed: bool | None = None
        writes_attempted = False
        with _bound_financial_writer(identity):
            try:
                writes_attempted = True
                compact_result = rebuild_financial_facts_compact(
                    corp_codes=scope.corp_codes,
                    year_from=scope.year_from,
                    year_to=scope.year_to,
                )
                phases["compact"] = _phase("completed", compact_result)
            except Exception:
                stop_reason = "compact_failed"
                stop_message = "financial compact rebuild could not be completed"
                phases["compact"] = _phase("failed")

            if stop_reason is None:
                try:
                    quality_result = rebuild_company_year_quality(
                        scope.quality_year,
                        scope.quality_year,
                        corp_codes=scope.corp_codes,
                    )
                    phases["quality"] = _phase("completed", quality_result)
                except Exception:
                    stop_reason = "quality_failed"
                    stop_message = "company-year quality rebuild could not be completed"
                    phases["quality"] = _phase("failed")

            if stop_reason is None:
                try:
                    manifest_result = write_dataset_manifest(
                        scope.dataset_version,
                        notes=_manifest_notes(scope),
                    )
                    phases["manifest"] = _phase("completed", manifest_result)
                except Exception:
                    stop_reason = "manifest_failed"
                    stop_message = "dataset manifest could not be written"
                    phases["manifest"] = _phase("failed")

            if writes_attempted:
                try:
                    wal_checkpointed = _checkpoint_wal(identity)
                except InvestorCoreBackfillError as exc:
                    if stop_reason is None:
                        stop_reason = exc.code
                        stop_message = exc.message
                    wal_checkpointed = False

        after_sha256: str | None = None
        after_rows: dict[str, int] | None = None
        free_after: int | None = None
        if wal_checkpointed is not False:
            try:
                _revalidate_database_identity(identity)
                after_sha256 = _sha256_file(database)
                after_rows = _scoped_row_counts(database, scope)
                _revalidate_database_identity(identity)
                free_after = _safe_probe(database, disk_probe)
                _validate_free_space(free_after, disk_probe=disk_probe)
            except InvestorCoreBackfillError as exc:
                if stop_reason is None:
                    stop_reason = exc.code
                    stop_message = exc.message
            except Exception:
                if stop_reason is None:
                    stop_reason = "evidence_collection_failed"
                    stop_message = "post-run evidence could not be collected"
        else:
            try:
                free_after = _safe_probe(database, disk_probe)
            except InvestorCoreBackfillError:
                free_after = None

        return _report(
            database,
            scope,
            execute=True,
            before_sha256=before_sha256,
            after_sha256=after_sha256,
            free_before=free_before,
            free_after=free_after,
            before_rows=before_rows,
            after_rows=after_rows,
            phases=phases,
            stop_reason=stop_reason,
            stop_message=stop_message,
            wal_checkpointed=wal_checkpointed,
        )
