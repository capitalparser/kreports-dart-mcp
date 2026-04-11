import logging
import time
from datetime import datetime, date

from kreports.config import settings
from kreports.collector.fetcher import fetch_disclosure_list
from kreports.db.engine import get_session
from kreports.db.models import Company, Disclosure, FetchLog
from kreports.processor.disc_parser import parse_disclosure

logger = logging.getLogger(__name__)


def collect_disclosures(
    corp_code: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """
    단일 기업의 공시 목록을 수집하여 DB에 저장한다.

    Args:
        start_date / end_date: YYYYMMDD. 기본값은 5개년 범위.

    Returns:
        {"saved": int, "skipped": int, "error": int}
    """
    if end_date is None:
        end_date = date.today().strftime("%Y%m%d")
    if start_date is None:
        start_y = date.today().year - settings.collect_years + 1
        start_date = f"{start_y}0101"

    try:
        items = fetch_disclosure_list(corp_code, start_date, end_date)
        time.sleep(settings.request_delay)
    except Exception as e:
        logger.error("공시 목록 수집 실패 [%s]: %s", corp_code, e)
        _log_fetch(corp_code, "error", str(e))
        return {"saved": 0, "skipped": 0, "error": 1}

    saved = skipped = 0
    for raw in items:
        parsed = parse_disclosure(raw)
        if parsed is None or parsed["disc_date"] is None:
            skipped += 1
            continue
        _upsert_disclosure(parsed)
        saved += 1

    _log_fetch(corp_code, "success", None)
    return {"saved": saved, "skipped": skipped, "error": 0}


def collect_all_disclosures(progress_callback=None) -> dict:
    """전체 상장사 공시 목록을 배치 수집한다."""
    with get_session() as session:
        companies = (
            session.query(Company.corp_code, Company.corp_name)
            .filter(Company.stock_code.isnot(None))
            .order_by(Company.corp_name)
            .all()
        )
        companies = list(companies)

    total = len(companies)
    totals = {"saved": 0, "skipped": 0, "error": 0}

    for idx, (corp_code, corp_name) in enumerate(companies, 1):
        if progress_callback:
            progress_callback(idx, total, corp_name)
        result = collect_disclosures(corp_code)
        for k, v in result.items():
            totals[k] += v

    return totals


def _upsert_disclosure(data: dict) -> None:
    from sqlalchemy import text
    sql = text("""
        INSERT INTO disclosures
            (rcept_no, corp_code, corp_name, disc_date, disc_type, report_nm, flr_nm, fetched_at)
        VALUES
            (:rcept_no, :corp_code, :corp_name, :disc_date, :disc_type, :report_nm, :flr_nm, :fetched_at)
        ON CONFLICT(rcept_no) DO NOTHING
    """)
    with get_session() as session:
        session.execute(sql, {**data, "fetched_at": datetime.utcnow().isoformat()})


def _log_fetch(corp_code, status, error_msg):
    with get_session() as session:
        session.add(FetchLog(
            task_type="disclosure",
            corp_code=corp_code,
            status=status,
            error_msg=error_msg,
            fetched_at=datetime.utcnow(),
        ))
