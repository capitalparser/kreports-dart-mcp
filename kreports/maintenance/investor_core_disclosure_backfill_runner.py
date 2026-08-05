"""Bounded disclosure-metadata remediation for investor-core backfill targets."""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from datetime import UTC, date, datetime
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Callable

from kreports.analysis.filing_provenance import valid_annual_filing_receipt
from kreports.annual_filing_identity import annual_report_name_matches_business_year
from kreports.config import settings
from kreports.collector.fetcher import (
    fetch_disclosure_list,
    request_budget,
)
from kreports.maintenance.investor_core_backfill_plan import _open_readonly_database
from kreports.maintenance.investor_core_backfill_runner import (
    MIN_FREE_SPACE_BYTES,
    InvestorCoreBackfillError,
    _capture_database_identity,
    _checkpoint_wal,
    _default_free_space_probe,
    _exclusive_execution_guard,
    _fail,
    _force_bounded_retries,
    _generic_stop,
    _open_verified_sqlite_connection,
    _planner_summary,
    _revalidate_database_identity,
    _resolve_regular_database,
    _sha256_file,
    _validate_expected_hash,
    _validate_free_space,
    _validate_process_binding,
)
from kreports.processor.disc_parser import parse_disclosure
from kreports.runtime import require_collector_mode


def metadata_targets_from_plan(
    plan: dict[str, Any],
    *,
    as_of_date: date,
) -> list[dict[str, Any]]:
    """Return the disclosure metadata targets selected by the investor plan."""
    selected_companies = plan.get("selected_companies")
    if not isinstance(selected_companies, list):
        raise _fail(
            "invalid_planner_output",
            "planner selected_companies is invalid",
        )
    if not isinstance(as_of_date, date):
        raise _fail("invalid_as_of_date", "metadata cutoff date is invalid")
    targets: list[dict[str, Any]] = []
    seen_corp_codes: set[str] = set()
    for candidate in selected_companies:
        if not isinstance(candidate, dict):
            raise _fail("invalid_planner_output", "planner company is invalid")
        corp_code = candidate.get("corp_code")
        stock_code = candidate.get("stock_code")
        corp_name = candidate.get("corp_name")
        if (
            not isinstance(corp_code, str)
            or len(corp_code) != 8
            or not corp_code.isdigit()
            or not isinstance(stock_code, str)
            or len(stock_code) != 6
            or not stock_code.isalnum()
            or not isinstance(corp_name, str)
            or not corp_name
            or not isinstance(candidate.get("source_ready"), bool)
        ):
            raise _fail("invalid_planner_output", "planner company fields are invalid")
        if corp_code in seen_corp_codes:
            raise _fail(
                "duplicate_planner_target",
                "planner contains a duplicate company",
            )
        seen_corp_codes.add(corp_code)

        year_lists: dict[str, list[int]] = {}
        for field in (
            "selected_years",
            "invalid_annual_anchor_years",
            "missing_disclosure_metadata_years",
        ):
            values = candidate.get(field)
            if not isinstance(values, list) or any(
                isinstance(year, bool) or not isinstance(year, int) or year <= 0
                for year in values
            ):
                raise _fail("invalid_planner_output", f"planner {field} is invalid")
            if len(values) != len(set(values)):
                raise _fail("invalid_planner_output", f"planner {field} has duplicates")
            year_lists[field] = values

        if candidate.get("source_ready") is True:
            continue
        selected_years = set(year_lists["selected_years"])
        refresh_years = sorted({
            *year_lists["invalid_annual_anchor_years"],
            *year_lists["missing_disclosure_metadata_years"],
        })
        if not set(refresh_years) <= selected_years:
            raise _fail(
                "invalid_planner_output",
                "metadata refresh years exceed selected planner scope",
            )
        if not refresh_years:
            continue
        start_date = f"{refresh_years[0]:04d}0101"
        end_date = as_of_date.strftime("%Y%m%d")
        if end_date < start_date:
            raise _fail(
                "invalid_as_of_date",
                "metadata cutoff precedes the selected business year",
            )
        targets.append({
            "corp_code": corp_code,
            "stock_code": stock_code,
            "corp_name": corp_name,
            "refresh_years": refresh_years,
            "start_date": start_date,
            "end_date": end_date,
        })
    return sorted(targets, key=lambda target: target["corp_code"])


def validated_annual_disclosures(
    target: dict[str, Any],
    raw_items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Validate filing metadata before it can repair an annual anchor."""
    valid: list[dict[str, Any]] = []
    corp_code = str(target.get("corp_code") or "")
    refresh_years = [int(year) for year in target.get("refresh_years") or []]
    start_date = str(target.get("start_date") or "")
    end_date = str(target.get("end_date") or "")
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        raw_receipt = raw_item.get("rcept_no")
        raw_corp_code = raw_item.get("corp_code")
        raw_date = raw_item.get("rcept_dt")
        if (
            not isinstance(raw_receipt, str)
            or len(raw_receipt) != 14
            or not raw_receipt.isdigit()
            or not isinstance(raw_corp_code, str)
            or raw_corp_code != corp_code
            or not isinstance(raw_date, str)
            or len(raw_date) != 8
            or not raw_date.isdigit()
            or raw_receipt[:8] != raw_date
            or raw_date < start_date
            or raw_date > end_date
        ):
            continue
        parsed = parse_disclosure(raw_item)
        if parsed is None or parsed.get("corp_code") != corp_code:
            continue
        receipt = str(parsed.get("rcept_no") or "")
        disclosure_date = parsed.get("disc_date")
        if (
            len(receipt) != 14
            or not receipt.isdigit()
            or disclosure_date is None
            or receipt[:8] != disclosure_date.strftime("%Y%m%d")
        ):
            continue
        matched_year = next(
            (
                year for year in refresh_years
                if annual_report_name_matches_business_year(
                    parsed.get("report_nm"), year,
                )
            ),
            None,
        )
        if matched_year is None:
            continue
        if valid_annual_filing_receipt(raw_receipt, matched_year) != raw_receipt:
            continue
        valid.append({
            **parsed,
            "disc_date": disclosure_date.isoformat(),
            "bsns_year": matched_year,
        })
    valid.sort(key=lambda row: (row["bsns_year"], row["rcept_no"]))
    return valid, len(raw_items) - len(valid)


def upsert_validated_disclosures(
    connection: sqlite3.Connection,
    *,
    target_corp_code: str,
    rows: list[dict[str, Any]],
    fetched_at: str,
) -> dict[str, int]:
    """Persist validated DART metadata without crossing company identity."""
    counts = {"inserted": 0, "updated": 0, "unchanged": 0, "conflicts": 0}
    mutable_fields = (
        "corp_name", "disc_date", "disc_type", "report_nm", "flr_nm",
    )
    identity_tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name IN ('source_documents', 'report_documents')"
        ).fetchall()
    }
    for row in rows:
        if row.get("corp_code") != target_corp_code:
            counts["conflicts"] += 1
            continue
        cross_table_conflict = any(
            any(
                identity_row[0] != target_corp_code
                for identity_row in connection.execute(
                    f"SELECT DISTINCT corp_code FROM {table} WHERE rcept_no=?",
                    (row["rcept_no"],),
                ).fetchall()
            )
            for table in identity_tables
        )
        if cross_table_conflict:
            counts["conflicts"] += 1
            continue
        existing = connection.execute(
            "SELECT corp_code, corp_name, disc_date, disc_type, report_nm, flr_nm "
            "FROM disclosures WHERE rcept_no=?",
            (row["rcept_no"],),
        ).fetchone()
        if existing is None:
            connection.execute(
                "INSERT INTO disclosures "
                "(rcept_no, corp_code, corp_name, disc_date, disc_type, report_nm, flr_nm, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["rcept_no"], target_corp_code, row["corp_name"],
                    row["disc_date"], row["disc_type"], row["report_nm"],
                    row["flr_nm"], fetched_at,
                ),
            )
            counts["inserted"] += 1
            continue
        if existing["corp_code"] != target_corp_code:
            counts["conflicts"] += 1
            continue
        if all(existing[field] == row[field] for field in mutable_fields):
            counts["unchanged"] += 1
            continue
        connection.execute(
            "UPDATE disclosures SET corp_name=?, disc_date=?, disc_type=?, "
            "report_nm=?, flr_nm=?, fetched_at=? "
            "WHERE rcept_no=? AND corp_code=?",
            (
                row["corp_name"], row["disc_date"], row["disc_type"],
                row["report_nm"], row["flr_nm"], fetched_at,
                row["rcept_no"], target_corp_code,
            ),
        )
        counts["updated"] += 1
    return counts


def _target_digest(targets: list[dict[str, Any]]) -> str:
    payload = json.dumps(
        targets,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _disclosure_row_counts(
    database: Path,
    targets: list[dict[str, Any]],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    with _open_readonly_database(database) as connection:
        for target in targets:
            corp_code = target["corp_code"]
            counts[corp_code] = int(connection.execute(
                "SELECT COUNT(*) FROM disclosures WHERE corp_code=?",
                (corp_code,),
            ).fetchone()[0])
    return counts


@contextmanager
def _unchanged_database_guard(database: Path, before_sha256: str):
    """Recheck content only after the exclusive writer guard is held."""
    if _sha256_file(database) != before_sha256:
        raise _fail(
            "database_changed_before_execution",
            "database changed after preflight and before execution",
        )
    yield


def run_investor_core_disclosure_backfill(
    db_path: str | Path,
    *,
    execute: bool = False,
    expected_db_sha256: str | None = None,
    max_api_calls: int | None = None,
    as_of_date: date,
    coverage_year: int | None = None,
    threshold_pct: float = 95.0,
    planner_fn: Callable[..., dict[str, Any]] | None = None,
    fetch_fn: Callable[..., list[dict[str, Any]]] | None = None,
    disk_probe: Callable[[Path], int] | None = None,
    settings_obj: object | None = None,
) -> dict[str, Any]:
    """Plan or execute a bounded investor-core disclosure metadata repair."""
    database = _resolve_regular_database(db_path)
    identity = _capture_database_identity(database)
    active_settings = settings if settings_obj is None else settings_obj
    _validate_process_binding(database, active_settings)
    before_sha256 = _sha256_file(database)
    _validate_expected_hash(expected_db_sha256, before_sha256, execute=execute)
    if execute:
        if (
            not isinstance(max_api_calls, int)
            or isinstance(max_api_calls, bool)
            or max_api_calls <= 0
        ):
            raise _fail(
                "max_api_calls_required",
                "--max-api-calls must be a positive integer with --execute",
            )
        try:
            require_collector_mode("run-investor-core-disclosure-backfill")
        except RuntimeError as exc:
            raise _fail(
                "collector_mode_required",
                "collector runtime mode is required with --execute",
            ) from exc
        if not getattr(active_settings, "dart_api_key", ""):
            raise _fail(
                "dart_api_key_required",
                "DART API key is required for execute mode",
            )
    if planner_fn is None:
        from kreports.maintenance.investor_core_backfill_plan import (
            plan_investor_core_backfill,
        )

        planner_fn = plan_investor_core_backfill
    plan = planner_fn(
        database,
        coverage_year=coverage_year,
        threshold_pct=threshold_pct,
    )
    if not isinstance(plan, dict):
        raise _fail("invalid_planner_output", "planner did not return an object")
    planner_summary = _planner_summary(plan)
    targets = metadata_targets_from_plan(plan, as_of_date=as_of_date)
    before_rows = _disclosure_row_counts(database, targets)
    active_disk_probe = disk_probe or _default_free_space_probe
    try:
        free_before = int(active_disk_probe(database))
    except Exception as exc:
        raise _fail("free_space_probe_failed", "free-space probe failed") from exc
    outcomes: Counter[str] = Counter()
    outcome_samples: list[dict[str, Any]] = []
    validation_counts: Counter[str] = Counter()
    write_counts: Counter[str] = Counter()
    stop_reason: str | None = None
    stop_message: str | None = None
    budget = None
    wal_checkpointed: bool | None = None
    mutable_transaction_attempted = False
    after_sha256: str | None = None
    after_rows: dict[str, int] | None = None
    free_after: int | None = None

    def record(target: dict[str, Any], outcome: str) -> None:
        outcomes[outcome] += 1
        if len(outcome_samples) < 20:
            outcome_samples.append({**target, "outcome": outcome})

    if not execute:
        for target in targets:
            record(target, "planned")
    else:
        _revalidate_database_identity(identity)
        if _sha256_file(database) != before_sha256:
            raise _fail(
                "database_changed_before_execution",
                "database changed after preflight and before execution",
            )
        _validate_free_space(free_before, disk_probe=active_disk_probe)
        active_fetch = fetch_fn or fetch_disclosure_list
        with (
            _exclusive_execution_guard(identity),
            _unchanged_database_guard(database, before_sha256),
            _force_bounded_retries(active_settings),
            request_budget(max_api_calls) as budget_scope,
        ):
            budget = budget_scope
            for index, target in enumerate(targets):
                try:
                    _revalidate_database_identity(identity)
                    _validate_free_space(
                        int(active_disk_probe(database)),
                        disk_probe=active_disk_probe,
                    )
                    raw_items = active_fetch(
                        target["corp_code"],
                        target["start_date"],
                        target["end_date"],
                        disc_type="A",
                    )
                    if not isinstance(raw_items, list):
                        raise TypeError("DART disclosure payload must be a list")
                    rows, rejected = validated_annual_disclosures(target, raw_items)
                except Exception as exc:
                    if isinstance(exc, InvestorCoreBackfillError):
                        stop_reason, stop_message = exc.code, exc.message
                    else:
                        stop_reason, stop_message = _generic_stop(exc)
                    record(target, "stopped")
                    for pending in targets[index + 1:]:
                        record(pending, "not_run")
                    break

                validation_counts["accepted"] += len(rows)
                validation_counts["rejected"] += rejected
                if not rows:
                    record(target, "not_found")
                else:
                    connection: sqlite3.Connection | None = None
                    try:
                        connection = _open_verified_sqlite_connection(identity)
                        connection.row_factory = sqlite3.Row
                        connection.execute("BEGIN IMMEDIATE")
                        mutable_transaction_attempted = True
                        target_write_counts = upsert_validated_disclosures(
                            connection,
                            target_corp_code=target["corp_code"],
                            rows=rows,
                            fetched_at=datetime.now(UTC).isoformat(),
                        )
                        if target_write_counts["conflicts"]:
                            connection.rollback()
                            write_counts["conflicts"] += target_write_counts["conflicts"]
                            stop_reason = "disclosure_receipt_identity_conflict"
                            stop_message = "DART receipt collides with another company"
                            record(target, "stopped")
                            for pending in targets[index + 1:]:
                                record(pending, "not_run")
                            break
                        connection.commit()
                        write_counts.update(target_write_counts)
                        changed = (
                            target_write_counts["inserted"]
                            + target_write_counts["updated"]
                        )
                        record(target, "repaired" if changed else "already_current")
                    except Exception:
                        if connection is not None:
                            connection.rollback()
                        stop_reason = "metadata_persistence_failed"
                        stop_message = "validated disclosure metadata could not be persisted"
                        record(target, "stopped")
                        for pending in targets[index + 1:]:
                            record(pending, "not_run")
                        break
                    finally:
                        if connection is not None:
                            connection.close()

                try:
                    _revalidate_database_identity(identity)
                    _validate_free_space(
                        int(active_disk_probe(database)),
                        disk_probe=active_disk_probe,
                    )
                except InvestorCoreBackfillError as exc:
                    stop_reason, stop_message = exc.code, exc.message
                    for pending in targets[index + 1:]:
                        record(pending, "not_run")
                    break
                except Exception:
                    stop_reason = "free_space_probe_failed"
                    stop_message = "free-space probe failed"
                    for pending in targets[index + 1:]:
                        record(pending, "not_run")
                    break

            checkpoint_failed = False
            if mutable_transaction_attempted:
                try:
                    wal_checkpointed = _checkpoint_wal(identity)
                except InvestorCoreBackfillError as exc:
                    stop_reason, stop_message = exc.code, exc.message
                    checkpoint_failed = True
            if not checkpoint_failed:
                try:
                    _revalidate_database_identity(identity)
                    after_sha256 = _sha256_file(database)
                    after_rows = _disclosure_row_counts(database, targets)
                    _revalidate_database_identity(identity)
                    free_after = int(active_disk_probe(database))
                except Exception:
                    if stop_reason is None:
                        stop_reason = "evidence_collection_failed"
                        stop_message = "post-run evidence could not be collected"
            else:
                try:
                    free_after = int(active_disk_probe(database))
                except Exception:
                    free_after = None

    if not execute:
        after_sha256 = _sha256_file(database)
        after_rows = _disclosure_row_counts(database, targets)
        free_after = int(active_disk_probe(database))
    return {
        "schema": "investor_core_disclosure_backfill_report",
        "schema_version": "1",
        "db_path": str(database),
        "db_sha256_before": before_sha256,
        "db_sha256_after": after_sha256,
        "planner": planner_summary,
        "as_of_date": as_of_date.isoformat(),
        "target_count": len(targets),
        "target_digest": _target_digest(targets),
        "target_samples": targets[:20],
        "dry_run": not execute,
        "execute": execute,
        "max_api_calls": max_api_calls if execute else None,
        "used_api_calls": budget.used_calls if budget is not None else 0,
        "endpoint_call_counts": (
            dict(sorted(budget.endpoint_counts.items())) if budget is not None else {}
        ),
        "target_outcomes": {
            "total": len(targets),
            "counts": dict(outcomes),
            "samples": outcome_samples,
            "sample_limit": 20,
        },
        "validation_counts": {
            "accepted": validation_counts["accepted"],
            "rejected": validation_counts["rejected"],
        },
        "write_counts": {
            name: write_counts[name]
            for name in ("inserted", "updated", "unchanged", "conflicts")
        },
        "stop_reason": stop_reason,
        "stop_message": stop_message,
        "completed": stop_reason is None,
        "relevant_row_counts": {"before": before_rows, "after": after_rows},
        "free_space_before": free_before,
        "free_space_after": free_after,
        "free_space_minimum": MIN_FREE_SPACE_BYTES,
        "wal_checkpointed": wal_checkpointed,
    }
