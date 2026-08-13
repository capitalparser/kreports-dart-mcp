import logging
import time
from datetime import datetime, date, timedelta

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


def collect_all_disclosures(
    start_date: str | None = None,
    end_date: str | None = None,
    market: str | None = None,
    progress_callback=None,
) -> dict:
    """전체 상장사 공시 목록을 배치 수집한다."""
    with get_session() as session:
        query = (
            session.query(Company.corp_code, Company.corp_name)
            .filter(Company.stock_code.isnot(None))
        )
        if market:
            query = query.filter(Company.market == market)
        companies = query.order_by(Company.corp_name).all()
        companies = list(companies)

    total = len(companies)
    totals = {"saved": 0, "skipped": 0, "error": 0}

    for idx, (corp_code, corp_name) in enumerate(companies, 1):
        if progress_callback:
            progress_callback(idx, total, corp_name)
        result = collect_disclosures(corp_code, start_date=start_date, end_date=end_date)
        for k, v in result.items():
            totals[k] += v

    return totals


def audit_disclosure_window(
    *,
    start_date: str,
    end_date: str,
    disc_type: str = "",
    report_keyword: str | None = None,
    exclude_keywords: list[str] | None = None,
    chunk_days: int = 31,
    persist_missing: bool = False,
    progress_callback=None,
) -> dict:
    """Compare local disclosure rows against DART list.json for a date window.

    This uses DART's receipt number list as the source of truth. It avoids the
    current-listed-company denominator problem because it audits the filing
    ledger directly.
    """
    if chunk_days < 1:
        raise ValueError("chunk_days must be >= 1")
    exclude_keywords = exclude_keywords or []
    chunks = list(_date_chunks(start_date, end_date, chunk_days=chunk_days))
    totals = {
        "start_date": start_date,
        "end_date": end_date,
        "disc_type": disc_type,
        "report_keyword": report_keyword,
        "exclude_keywords": exclude_keywords,
        "chunks": len(chunks),
        "dart_rows": 0,
        "target_rows": 0,
        "local_rows": 0,
        "missing_rows": 0,
        "saved_missing": 0,
        "parse_skipped": 0,
        "errors": [],
        "chunk_results": [],
        "missing_samples": [],
    }

    for idx, (chunk_start, chunk_end) in enumerate(chunks, 1):
        if progress_callback:
            progress_callback(idx, len(chunks), chunk_start, chunk_end)
        try:
            raw_items = fetch_disclosure_list(
                None,
                chunk_start,
                chunk_end,
                disc_type=disc_type,
            )
        except Exception as exc:
            err = {
                "start_date": chunk_start,
                "end_date": chunk_end,
                "error": str(exc),
            }
            totals["errors"].append(err)
            totals["chunk_results"].append({**err, "status": "error"})
            continue

        parsed_items = []
        parse_skipped = 0
        for raw in raw_items:
            parsed = parse_disclosure(raw)
            if not parsed or parsed["disc_date"] is None:
                parse_skipped += 1
                continue
            report_nm = parsed["report_nm"]
            if report_keyword and report_keyword not in report_nm:
                continue
            if any(keyword in report_nm for keyword in exclude_keywords):
                continue
            parsed_items.append(parsed)

        expected = {item["rcept_no"]: item for item in parsed_items}
        local_rcept_nos = _local_disclosure_rcept_nos(list(expected))
        missing_nos = sorted(set(expected) - local_rcept_nos)
        saved_missing = 0
        if persist_missing:
            for rcept_no in missing_nos:
                _upsert_disclosure(expected[rcept_no])
                saved_missing += 1

        local_count = len(expected) - len(missing_nos)
        chunk_result = {
            "start_date": chunk_start,
            "end_date": chunk_end,
            "status": "ok",
            "dart_rows": len(raw_items),
            "target_rows": len(expected),
            "local_rows": local_count,
            "missing_rows": len(missing_nos),
            "saved_missing": saved_missing,
            "parse_skipped": parse_skipped,
        }
        totals["chunk_results"].append(chunk_result)
        totals["dart_rows"] += len(raw_items)
        totals["target_rows"] += len(expected)
        totals["local_rows"] += local_count
        totals["missing_rows"] += len(missing_nos)
        totals["saved_missing"] += saved_missing
        totals["parse_skipped"] += parse_skipped
        if missing_nos and len(totals["missing_samples"]) < 50:
            for rcept_no in missing_nos[: 50 - len(totals["missing_samples"])]:
                item = expected[rcept_no]
                totals["missing_samples"].append({
                    "rcept_no": rcept_no,
                    "corp_code": item["corp_code"],
                    "corp_name": item["corp_name"],
                    "disc_date": item["disc_date"].isoformat(),
                    "report_nm": item["report_nm"],
                })

    totals["coverage_pct"] = (
        round(totals["local_rows"] * 100.0 / totals["target_rows"], 2)
        if totals["target_rows"]
        else 100.0
    )
    if totals["errors"]:
        totals["verdict"] = "fail"
    elif totals["target_rows"] == 0:
        totals["verdict"] = "empty"
    else:
        totals["verdict"] = "pass" if totals["missing_rows"] == 0 else "fail"
    return totals


def _date_chunks(start_date: str, end_date: str, *, chunk_days: int):
    start = datetime.strptime(start_date, "%Y%m%d").date()
    end = datetime.strptime(end_date, "%Y%m%d").date()
    if start > end:
        raise ValueError("start_date must be <= end_date")
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=chunk_days - 1), end)
        yield cur.strftime("%Y%m%d"), chunk_end.strftime("%Y%m%d")
        cur = chunk_end + timedelta(days=1)


def _local_disclosure_rcept_nos(rcept_nos: list[str]) -> set[str]:
    if not rcept_nos:
        return set()
    found: set[str] = set()
    with get_session() as session:
        for i in range(0, len(rcept_nos), 900):
            chunk = rcept_nos[i:i + 900]
            rows = (
                session.query(Disclosure.rcept_no)
                .filter(Disclosure.rcept_no.in_(chunk))
                .all()
            )
            found.update(row[0] for row in rows)
    return found


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
