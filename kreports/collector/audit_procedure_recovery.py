"""Bounded DART recovery for historically listed audit-procedure gaps."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import text

from kreports.collector.report_document_collector import (
    collect_report_sections_for_disclosure,
    index_audit_procedures_from_sections,
    rebuild_kam_items_for_receipts,
)
from kreports.config import settings
from kreports.db.engine import get_session
from kreports.db.models import BackfillRun
from kreports.maintenance.backfill_runs import BackfillLease
from kreports.processor.audit_parser import parse_bsns_year
from kreports.quality.company_year import rebuild_company_year_quality


TASK_TYPE = "audit_procedure_recovery"
SELECTOR_VERSION = 5
_EXPLICIT_BUSINESS_YEAR = re.compile(r"(?<!\d)(20\d{2})\s*사업연도")
_FALLBACK_RESOLUTION_REASON = "audit_report_attachment_not_found"


def recovery_backfill_params(*, year: int, market: str) -> dict[str, object]:
    """The stable logical scope shared by successful and retried batches."""
    return {
        "selector": "historical_audit_procedure_gap",
        "selector_version": SELECTOR_VERSION,
        "year": int(year),
        "market": _market(market),
    }


def _market(value: str) -> str:
    market = str(value or "").strip().upper()
    if not market:
        raise ValueError("market is required")
    return market


def _cursor(value: Mapping[str, Any] | None) -> tuple[str, str] | None:
    if value is None:
        return None
    corp_code = value.get("corp_code")
    rcept_no = value.get("rcept_no")
    if not isinstance(corp_code, str) or not isinstance(rcept_no, str):
        raise ValueError("cursor requires string corp_code and rcept_no")
    if not corp_code or not rcept_no:
        raise ValueError("cursor requires non-empty corp_code and rcept_no")
    return corp_code, rcept_no


def _matches_target_business_year(report_nm: object, year: int) -> bool:
    """Accept an unmarked report or one explicitly marked for this year only."""
    markers = {int(value) for value in _EXPLICIT_BUSINESS_YEAR.findall(str(report_nm or ""))}
    return not markers or markers == {int(year)}


def _is_target_recovery_root(*, report_nm: object, rcept_no: object, year: int) -> bool:
    """Keep target-year business-report roots without admitting adjacent years."""
    name = str(report_nm or "")
    if "사업보고서" in name:
        return parse_bsns_year(name, str(rcept_no)) == int(year)
    return _matches_target_business_year(name, int(year))


def select_audit_procedure_recovery_targets(
    *,
    year: int,
    market: str,
    limit: int,
    after: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    """Select inadequate audit-report receipts from verified year/market facts.

    A current ``companies.market`` value is intentionally not an eligibility
    predicate. A receipt is adequate only when it has both a full-body KAM item
    and at least one nonblank procedure item, so a stored short KAM section is
    still a recovery target.
    """
    if not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    normalized_market = _market(market)
    cursor = _cursor(after)
    params: dict[str, object] = {
        "year": int(year),
        "market": normalized_market,
        "start_date": f"{int(year) + 1}-01-01",
        "end_date": f"{int(year) + 1}-12-31",
        "standalone_audit_marker": f"%감사보고서 ({int(year)}.%",
        "limit": limit,
    }
    sql = """
        SELECT d.rcept_no, m.corp_code, COALESCE(c.corp_name, d.corp_name) AS corp_name
             , d.report_nm, d.disc_date
        FROM company_year_listing_memberships AS m
        JOIN disclosures AS d ON d.corp_code=m.corp_code
        LEFT JOIN companies AS c ON c.corp_code=m.corp_code
        WHERE m.bsns_year=:year
          AND m.market=:market
          AND m.status='verified'
          AND length(d.rcept_no)=14
          AND d.rcept_no GLOB '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]'
          AND substr(d.rcept_no, 1, 4)=strftime('%Y', d.disc_date)
          AND (
              (
                  (
                      d.report_nm LIKE '%감사보고서제출%'
                      OR d.report_nm LIKE :standalone_audit_marker
                  )
                  AND d.disc_date BETWEEN :start_date AND :end_date
              )
              OR d.report_nm LIKE '%사업보고서%'
          )
          AND d.report_nm NOT LIKE '%자회사의 주요경영사항%'
          AND d.report_nm NOT LIKE '%제출 지연%'
          AND d.report_nm NOT LIKE '%제출지연%'
          AND d.report_nm NOT LIKE '%제출기한연장%'
          AND d.report_nm NOT LIKE '%해외증권%'
    """
    with get_session() as session:
        rows = session.execute(text(sql), params).mappings().all()
        full_kams = session.execute(text("""
            SELECT rcept_no, corp_code FROM kam_items
            WHERE bsns_year=:year AND source_type='audit_report'
              AND quality_status='full_body' AND full_body_length > 0
        """), {"year": int(year)}).mappings().all()
        procedures = session.execute(text("""
            SELECT rcept_no, corp_code FROM audit_procedure_items
            WHERE bsns_year=:year AND source_type='audit_report'
              AND length(trim(procedure_text)) > 0
        """), {"year": int(year)}).mappings().all()
        fallback_resolutions = session.execute(text("""
            SELECT direct_rcept_no, corp_code, bsns_year, fallback_rcept_no,
                   resolution_reason
            FROM audit_procedure_recovery_fallbacks
            WHERE bsns_year=:year
        """), {"year": int(year)}).mappings().all()

    canonical: dict[str, dict[str, object]] = {}
    business_fallbacks: dict[str, dict[str, object]] = {}
    for row in rows:
        if not _is_target_recovery_root(
            report_nm=row["report_nm"],
            rcept_no=row["rcept_no"],
            year=int(year),
        ):
            continue
        corp_code = str(row["corp_code"])
        if "사업보고서" in str(row["report_nm"] or ""):
            existing_business = business_fallbacks.get(corp_code)
            candidate_order = (str(row["disc_date"]), str(row["rcept_no"]))
            existing_business_order = (
                (
                    str(existing_business["disc_date"]),
                    str(existing_business["rcept_no"]),
                )
                if existing_business is not None else None
            )
            if (
                existing_business_order is None
                or candidate_order > existing_business_order
            ):
                business_fallbacks[corp_code] = dict(row)
        existing = canonical.get(corp_code)
        candidate_priority = 0 if "사업보고서" in str(row["report_nm"] or "") else 1
        candidate_order = (str(row["disc_date"]), str(row["rcept_no"]))
        existing_priority = -1
        if existing is not None:
            existing_priority = 0 if "사업보고서" in str(existing["report_nm"] or "") else 1
        existing_order = (
            (str(existing["disc_date"]), str(existing["rcept_no"]))
            if existing is not None else None
        )
        if (
            existing_order is None
            or candidate_priority > existing_priority
            or (candidate_priority == existing_priority and candidate_order > existing_order)
        ):
            canonical[corp_code] = dict(row)

    def _has_attachment_evidence(receipt: str, corp_code: str, evidence_rows) -> bool:
        return any(
            str(row["corp_code"]) == corp_code
            and (
                str(row["rcept_no"]) == receipt
                or str(row["rcept_no"]).startswith(f"{receipt}_")
            )
            for row in evidence_rows
        )

    candidates = sorted(canonical.values(), key=lambda row: (str(row["corp_code"]), str(row["rcept_no"])))

    def _has_complete_audit_evidence(receipt: str, corp_code: str) -> bool:
        return (
            _has_attachment_evidence(receipt, corp_code, full_kams)
            and _has_attachment_evidence(receipt, corp_code, procedures)
        )

    resolutions_by_direct = {
        str(row["direct_rcept_no"]): row for row in fallback_resolutions
    }

    def _selected_resolved_fallback(row: Mapping[str, object]) -> str | None:
        """Return a fallback only when it remains this root's annual target."""
        direct_receipt = str(row["rcept_no"])
        corp_code = str(row["corp_code"])
        resolution = resolutions_by_direct.get(direct_receipt)
        fallback = business_fallbacks.get(corp_code)
        if resolution is None or fallback is None:
            return None
        if (
            str(resolution["corp_code"]) != corp_code
            or int(resolution["bsns_year"]) != int(year)
            or str(resolution["resolution_reason"]) != _FALLBACK_RESOLUTION_REASON
            or str(resolution["fallback_rcept_no"]) != str(fallback["rcept_no"])
        ):
            return None
        return str(fallback["rcept_no"])

    # Older annual evidence does not itself establish that this direct root
    # was recovered.  It is adequate only through a matching durable
    # direct-to-current-annual resolution.
    inadequate = [
        row for row in candidates
        if not _has_complete_audit_evidence(
            str(row["rcept_no"]), str(row["corp_code"])
        )
        and not (
            (fallback_receipt := _selected_resolved_fallback(row)) is not None
            and _has_complete_audit_evidence(
                fallback_receipt, str(row["corp_code"])
            )
        )
    ]
    if cursor is not None:
        inadequate = [
            row for row in inadequate
            if (str(row["corp_code"]), str(row["rcept_no"])) > cursor
        ]
    targets = [
        {
            "corp_code": str(row["corp_code"]),
            "rcept_no": str(row["rcept_no"]),
            "corp_name": str(row["corp_name"] or ""),
        }
        for row in inadequate[:limit]
    ]
    return {
        "year": int(year),
        "market": normalized_market,
        "after": (
            {"corp_code": cursor[0], "rcept_no": cursor[1]}
            if cursor is not None else None
        ),
        "canonical_roots": len(candidates),
        "inadequate_roots": len(inadequate),
        "targets": targets,
    }


def _canonical_business_report_fallback(
    *,
    year: int,
    target: Mapping[str, str],
) -> dict[str, str] | None:
    """Return the newest same-company annual business-report root, if any."""
    corp_code = str(target["corp_code"])
    with get_session() as session:
        rows = session.execute(text("""
            SELECT d.rcept_no, d.corp_code,
                   COALESCE(c.corp_name, d.corp_name) AS corp_name,
                   d.report_nm, d.disc_date
            FROM disclosures AS d
            LEFT JOIN companies AS c ON c.corp_code=d.corp_code
            WHERE d.corp_code=:corp_code
              AND d.report_nm LIKE '%사업보고서%'
              AND length(d.rcept_no)=14
              AND d.rcept_no GLOB '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]'
              AND d.report_nm NOT LIKE '%자회사의 주요경영사항%'
              AND d.report_nm NOT LIKE '%제출 지연%'
              AND d.report_nm NOT LIKE '%제출지연%'
              AND d.report_nm NOT LIKE '%제출기한연장%'
              AND d.report_nm NOT LIKE '%해외증권%'
        """), {"corp_code": corp_code}).mappings().all()
    candidates = [
        row
        for row in rows
        if _is_target_recovery_root(
            report_nm=row["report_nm"],
            rcept_no=row["rcept_no"],
            year=int(year),
        )
    ]
    if not candidates:
        return None
    selected = max(
        candidates,
        key=lambda row: (str(row["disc_date"]), str(row["rcept_no"])),
    )
    return {
        "corp_code": str(selected["corp_code"]),
        "rcept_no": str(selected["rcept_no"]),
        "corp_name": str(selected["corp_name"] or ""),
    }


def _has_usable_fallback_audit_evidence(result: Mapping[str, object]) -> bool:
    """Require audit attachment sections rather than a business-body success."""
    try:
        return int(result.get("audit_report_sections") or 0) > 0
    except (TypeError, ValueError):
        return False


def _record_fallback_resolution(
    *,
    year: int,
    target: Mapping[str, str],
    fallback: Mapping[str, str],
) -> None:
    """Upsert the proven fallback lineage only after derived evidence succeeds."""
    with get_session() as session:
        session.execute(text("""
            INSERT INTO audit_procedure_recovery_fallbacks (
              direct_rcept_no, corp_code, bsns_year, fallback_rcept_no,
              resolution_reason, resolved_at
            ) VALUES (
              :direct_rcept_no, :corp_code, :bsns_year, :fallback_rcept_no,
              :resolution_reason, :resolved_at
            )
            ON CONFLICT(direct_rcept_no) DO UPDATE SET
              corp_code=excluded.corp_code,
              bsns_year=excluded.bsns_year,
              fallback_rcept_no=excluded.fallback_rcept_no,
              resolution_reason=excluded.resolution_reason,
              resolved_at=excluded.resolved_at
        """), {
            "direct_rcept_no": str(target["rcept_no"]),
            "corp_code": str(target["corp_code"]),
            "bsns_year": int(year),
            "fallback_rcept_no": str(fallback["rcept_no"]),
            "resolution_reason": _FALLBACK_RESOLUTION_REASON,
            "resolved_at": datetime.now(timezone.utc),
        })


def _latest_resume_cursor(*, year: int, market: str, params: dict[str, object]) -> dict[str, str] | None:
    """Recover the last verified prefix from this exact terminal run scope.

    A failed run can still have a successfully persisted prefix.  Its
    checkpoint cursor points at that prefix while ``last_error`` names the next
    receipt, so resuming after the cursor retries the failure without refetching
    the successful work before it.
    """
    canonical_params = json.dumps(params, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with get_session() as session:
        runs = (
            session.query(BackfillRun)
            .filter(
                BackfillRun.task_type == TASK_TYPE,
                BackfillRun.year == year,
                BackfillRun.market == market,
                BackfillRun.status != "running",
                BackfillRun.params_json == canonical_params,
            )
            .order_by(BackfillRun.finished_at.desc(), BackfillRun.id.desc())
            .all()
        )
        checkpoints = [str(run.checkpoint_json or "{}") for run in runs]
    for checkpoint_json in checkpoints:
        try:
            checkpoint = json.loads(checkpoint_json)
        except json.JSONDecodeError:
            continue
        raw_cursor = checkpoint.get("next_cursor")
        try:
            parsed = _cursor(raw_cursor)
        except ValueError:
            continue
        if parsed is not None:
            return {"corp_code": parsed[0], "rcept_no": parsed[1]}
    return None


def _lease_counts(lease: BackfillLease) -> tuple[int, int, int, int]:
    with get_session() as session:
        row = session.get(BackfillRun, lease.id)
        if row is None:
            raise RuntimeError(f"backfill run {lease.id} is unavailable")
        return (
            int(row.attempted_count or 0),
            int(row.saved_count or 0),
            int(row.no_data_count or 0),
            int(row.error_count or 0),
        )


def _checkpoint(
    lease: BackfillLease,
    *,
    base_counts: tuple[int, int, int, int],
    cursor_start: dict[str, str] | None,
    next_cursor: dict[str, str] | None,
    totals: dict[str, object],
    exhausted: bool,
    error: dict[str, str] | None = None,
) -> None:
    base_attempted, base_saved, base_no_data, base_errors = base_counts
    lease.checkpoint(
        {
            "selector": "historical_audit_procedure_gap",
            "selector_version": SELECTOR_VERSION,
            "cursor_start": cursor_start,
            "next_cursor": next_cursor,
            "exhausted": exhausted,
            "last_error": error,
            "batch": {
                "processed": int(totals["processed"]),
                "success": int(totals["ok"]),
                "failed": int(totals["failed"]),
                "sections": int(totals["sections"]),
                "api_receipt_fetches": int(totals["api_receipt_fetches"]),
                "storage_backend": settings.raw_storage_backend,
            },
        },
        attempted=base_attempted + int(totals["processed"]),
        saved=base_saved + int(totals["ok"]),
        no_data=base_no_data,
        errors=base_errors + int(totals["failed"]),
    )


def _rebuild_derived_receipt(*, year: int, target: Mapping[str, str]) -> dict[str, object]:
    """Finish all derived evidence for one root receipt before cursor advance."""
    receipt = str(target["rcept_no"])
    corp_code = str(target["corp_code"])
    return {
        "rcept_no": receipt,
        "corp_code": corp_code,
        "kam": rebuild_kam_items_for_receipts(year=year, rcept_nos=[receipt]),
        "procedures": index_audit_procedures_from_sections(
            year=year,
            rcept_nos=[receipt],
        ),
        "quality": rebuild_company_year_quality(
            year_from=year,
            year_to=year,
            corp_codes=[corp_code],
        ),
    }


def run_audit_procedure_recovery_batch(
    lease: BackfillLease,
    *,
    year: int,
    market: str,
    limit: int,
    progress_callback=None,
) -> dict[str, object]:
    """Fetch one deterministic batch and rebuild only its derived rows.

    On a receipt failure the cursor remains before that receipt and processing
    stops, making the failed target retryable rather than silently skipped.
    """
    normalized_market = _market(market)
    params = recovery_backfill_params(year=year, market=normalized_market)
    cursor_start = _latest_resume_cursor(
        year=int(year), market=normalized_market, params=params,
    )
    selected = select_audit_procedure_recovery_targets(
        year=year,
        market=normalized_market,
        limit=limit,
        after=cursor_start,
    )
    targets = list(selected["targets"])
    base_counts = _lease_counts(lease)
    totals: dict[str, object] = {
        "total": len(targets),
        "processed": 0,
        "ok": 0,
        "failed": 0,
        "sections": 0,
        "api_receipt_fetches": 0,
        "errors": [],
    }
    next_cursor = cursor_start
    derived_receipts: list[dict[str, object]] = []
    for index, target in enumerate(targets, 1):
        receipt = str(target["rcept_no"])
        if progress_callback:
            progress_callback(index, len(targets), str(target["corp_name"]), receipt)
        effective_target = target
        resolved_fallback: Mapping[str, str] | None = None
        result = collect_report_sections_for_disclosure(receipt)
        totals["api_receipt_fetches"] = int(totals["api_receipt_fetches"]) + 1
        if (
            not result.get("ok")
            and result.get("error") == "audit report attachment not found"
        ):
            fallback = _canonical_business_report_fallback(
                year=int(year),
                target=target,
            )
            if fallback is not None:
                fallback_result = collect_report_sections_for_disclosure(
                    fallback["rcept_no"]
                )
                totals["api_receipt_fetches"] = (
                    int(totals["api_receipt_fetches"]) + 1
                )
                if (
                    fallback_result.get("ok")
                    and _has_usable_fallback_audit_evidence(fallback_result)
                ):
                    result = fallback_result
                    effective_target = fallback
                    resolved_fallback = fallback
                else:
                    fallback_error = str(fallback_result.get("error") or "").strip()
                    suffix = (
                        f"fallback failed: {fallback_error}"
                        if fallback_error else "fallback yielded no audit report sections"
                    )
                    result = {
                        "ok": 0,
                        "documents": 0,
                        "sections": 0,
                        "error": f"audit report attachment not found; annual business report {suffix}",
                    }
        totals["processed"] = int(totals["processed"]) + 1
        if not result.get("ok"):
            totals["failed"] = int(totals["failed"]) + 1
            error = {
                "corp_code": str(target["corp_code"]),
                "rcept_no": receipt,
                "message": str(result.get("error") or "audit report collection failed")[:1000],
            }
            totals["errors"] = [error]
            _checkpoint(
                lease,
                base_counts=base_counts,
                cursor_start=cursor_start,
                next_cursor=next_cursor,
                totals=totals,
                exhausted=False,
                error=error,
            )
            break
        totals["sections"] = int(totals["sections"]) + int(result.get("sections") or 0)
        # Do not let a successfully fetched raw receipt become resumably
        # skipped until KAM, procedures, and quality have all been persisted.
        try:
            derived = _rebuild_derived_receipt(year=year, target=effective_target)
            if resolved_fallback is not None:
                _record_fallback_resolution(
                    year=int(year),
                    target=target,
                    fallback=resolved_fallback,
                )
            derived_receipts.append(derived)
        except Exception as exc:  # noqa: BLE001 - receipt is retryable after durable checkpoint
            totals["failed"] = int(totals["failed"]) + 1
            error = {
                "corp_code": str(target["corp_code"]),
                "rcept_no": receipt,
                "message": str(exc)[:1000],
            }
            totals["errors"] = [error]
            _checkpoint(
                lease,
                base_counts=base_counts,
                cursor_start=cursor_start,
                next_cursor=next_cursor,
                totals=totals,
                exhausted=False,
                error=error,
            )
            break
        totals["ok"] = int(totals["ok"]) + 1
        next_cursor = {"corp_code": str(target["corp_code"]), "rcept_no": receipt}
        _checkpoint(
            lease,
            base_counts=base_counts,
            cursor_start=cursor_start,
            next_cursor=next_cursor,
            totals=totals,
            exhausted=False,
        )

    exhausted = not targets
    if not targets:
        _checkpoint(
            lease,
            base_counts=base_counts,
            cursor_start=cursor_start,
            next_cursor=next_cursor,
            totals=totals,
            exhausted=True,
        )
    return {
        **totals,
        "year": int(year),
        "market": normalized_market,
        "cursor_start": cursor_start,
        "next_cursor": next_cursor,
        "exhausted": exhausted,
        "targets": targets,
        "api_receipt_fetches": int(totals["api_receipt_fetches"]),
        "storage_backend": settings.raw_storage_backend,
        "derived": {"receipts": derived_receipts},
    }
