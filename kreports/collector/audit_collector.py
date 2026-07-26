import logging
import time
from datetime import datetime, date

from kreports.config import settings
from kreports.collector.fetcher import fetch_disclosure_list, fetch_document_xml
from kreports.db.engine import get_session
from kreports.db.models import Company, Auditor
from kreports.processor.audit_parser import (
    is_audit_report, is_annual_report,
    is_valid_auditor_name, normalize_auditor_name, parse_bsns_year, parse_fs_div,
    parse_auditor_from_doc_xml,
)
from kreports.judge.auditor_flags import compute_auditor_flags
from kreports.maintenance.backfill_runs import (
    BackfillRunError,
    classify_backfill_error,
)

logger = logging.getLogger(__name__)


def collect_auditors(
    corp_code: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """
    단일 기업의 감사인 이력을 공시 목록에서 추출하여 저장한다.

    Returns:
        {"saved": int, "skipped": int}
    """
    if end_date is None:
        end_date = date.today().strftime("%Y%m%d")
    if start_date is None:
        start_y = date.today().year - settings.collect_years + 1
        start_date = f"{start_y}0101"

    try:
        # 정기공시(A) + 외감법인(F) 필터로 감사보고서 범위 최소화
        items_a = fetch_disclosure_list(corp_code, start_date, end_date, disc_type="A")
        time.sleep(settings.request_delay)
        items_f = fetch_disclosure_list(corp_code, start_date, end_date, disc_type="F")
        time.sleep(settings.request_delay)
        items = items_a + items_f
    except Exception as e:
        logger.error("감사인 공시 수집 실패 [%s]: %s", corp_code, e)
        raise BackfillRunError(
            classify_backfill_error(e),
            f"auditor disclosure collection failed [{corp_code}]: {e}",
        ) from e

    saved = skipped = 0
    for raw in items:
        report_nm = raw.get("report_nm", "")
        rcept_dt = raw.get("rcept_dt", "")
        rcept_no = raw.get("rcept_no", "")

        if is_audit_report(report_nm):
            # 비상장사 경로: 감사인이 직접 제출한 감사보고서
            flr_nm = raw.get("flr_nm", "").strip()
            if not is_valid_auditor_name(flr_nm):
                skipped += 1
                continue
            bsns_year = parse_bsns_year(report_nm, rcept_dt)
            if bsns_year is None:
                skipped += 1
                continue
            _upsert_auditor({
                "corp_code": corp_code,
                "bsns_year": bsns_year,
                "fs_div": parse_fs_div(report_nm),
                "auditor_nm": normalize_auditor_name(flr_nm),
                "audit_opinion": None,
                "rcept_no": rcept_no,
            })
            saved += 1

        elif is_annual_report(report_nm):
            # 상장사 경로: 사업보고서 document.xml에서 감사인 추출
            bsns_year = parse_bsns_year(report_nm, rcept_dt)
            if bsns_year is None:
                skipped += 1
                continue
            if not rcept_no:
                skipped += 1
                continue

            content = fetch_document_xml(rcept_no)
            time.sleep(settings.request_delay)
            if not content:
                skipped += 1
                continue

            auditor_records = parse_auditor_from_doc_xml(content)
            if not auditor_records:
                logger.warning("사업보고서 감사인 파싱 실패 [%s %s]", corp_code, bsns_year)
                skipped += 1
                continue

            for rec in auditor_records:
                _upsert_auditor({
                    "corp_code": corp_code,
                    "bsns_year": bsns_year,
                    "fs_div": rec["fs_div"],
                    "auditor_nm": rec["auditor_nm"],
                    "audit_opinion": rec.get("audit_opinion"),
                    "rcept_no": rcept_no,
                })
            saved += len(auditor_records)

        else:
            skipped += 1

    if saved > 0:
        _delete_invalid_auditor_rows(corp_code)
        compute_auditor_flags(corp_code)

    return {"saved": saved, "skipped": skipped}


def collect_all_auditors(progress_callback=None) -> dict:
    """전체 상장사 감사인 이력을 배치 수집한다."""
    with get_session() as session:
        companies = (
            session.query(Company.corp_code, Company.corp_name)
            .filter(Company.stock_code.isnot(None))
            .order_by(Company.corp_name)
            .all()
        )
        companies = list(companies)

    total = len(companies)
    totals = {"saved": 0, "skipped": 0}

    for idx, (corp_code, corp_name) in enumerate(companies, 1):
        if progress_callback:
            progress_callback(idx, total, corp_name)
        result = collect_auditors(corp_code)
        for k, v in result.items():
            totals[k] += v

    return totals


def _upsert_auditor(data: dict) -> None:
    from sqlalchemy import text
    sql = text("""
        INSERT INTO auditors
            (corp_code, bsns_year, fs_div, auditor_nm, audit_opinion, rcept_no, fetched_at)
        VALUES
            (:corp_code, :bsns_year, :fs_div, :auditor_nm, :audit_opinion, :rcept_no, :fetched_at)
        ON CONFLICT(corp_code, bsns_year, fs_div) DO UPDATE SET
            auditor_nm    = excluded.auditor_nm,
            audit_opinion = excluded.audit_opinion,
            rcept_no      = excluded.rcept_no,
            fetched_at    = excluded.fetched_at
    """)
    with get_session() as session:
        session.execute(sql, {
            "corp_code": data["corp_code"],
            "bsns_year": data["bsns_year"],
            "fs_div": data["fs_div"],
            "auditor_nm": data["auditor_nm"],
            "audit_opinion": data.get("audit_opinion"),
            "rcept_no": data.get("rcept_no"),
            "fetched_at": datetime.utcnow().isoformat(),
        })


def _delete_invalid_auditor_rows(corp_code: str) -> int:
    """Remove placeholder auditor rows created from blank CFS/OFS table cells."""
    with get_session() as session:
        rows = session.query(Auditor).filter_by(corp_code=corp_code).all()
        deleted = 0
        for row in rows:
            if not is_valid_auditor_name(row.auditor_nm):
                session.delete(row)
                deleted += 1
        return deleted
