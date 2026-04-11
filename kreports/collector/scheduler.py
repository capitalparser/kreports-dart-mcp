"""
일별 증분 수집 스케줄러 (APScheduler 기반).

사용법:
    # 백그라운드 서비스로 단독 실행
    python -m dart_platform.collector.scheduler

    # FastAPI 서버 시작 시 함께 실행 (api/main.py 내 start_scheduler() 호출)
"""
import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)

# 설정 상수
_DAILY_BATCH_LIMIT = 9_000      # DART 일일 API 한도(10,000) 여유분
_RETRY_LIMIT = 3                # fetch_log 실패 재시도 최대 횟수


# ---------------------------------------------------------------------------
# 개별 증분 작업
# ---------------------------------------------------------------------------

def _job_sync_new_disclosures() -> None:
    """어제 공시된 신규 공시를 전체 상장사 대상으로 수집한다."""
    from kreports.db.engine import get_session
    from kreports.db.models import Company, Disclosure
    from kreports.collector.disc_collector import collect_disclosures

    yesterday = date.today() - timedelta(days=1)
    logger.info("[스케줄] 신규 공시 수집 시작: %s", yesterday)

    with get_session() as session:
        corp_codes = [
            r[0] for r in
            session.query(Company.corp_code).filter(Company.stock_code.isnot(None)).all()
        ]

    yyyymmdd = yesterday.strftime("%Y%m%d")
    saved_total = 0
    for corp_code in corp_codes:
        result = collect_disclosures(corp_code, start_date=yyyymmdd, end_date=yyyymmdd)
        saved_total += result.get("saved", 0)

    logger.info("[스케줄] 공시 수집 완료: 신규 %d건", saved_total)


def _job_retry_failed_financials() -> None:
    """fetch_log에서 최근 실패(error) 건을 재시도한다."""
    from kreports.db.engine import get_session
    from kreports.db.models import FetchLog, Company
    from kreports.collector.fin_collector import collect_financial

    logger.info("[스케줄] 실패 건 재시도 시작")

    with get_session() as session:
        failed = (
            session.query(FetchLog)
            .filter(FetchLog.task_type == "financial", FetchLog.status == "error")
            .order_by(FetchLog.fetched_at.desc())
            .limit(_RETRY_LIMIT * 50)
            .all()
        )
        retry_list = [
            (r.corp_code, r.year, r.quarter)
            for r in failed
            if r.corp_code and r.year and r.quarter
        ]

    # corp_code → stock_code 변환 (collect_financial은 stock_code 기반)
    with get_session() as session:
        code_map = {
            r.corp_code: r.stock_code
            for r in session.query(Company).filter(
                Company.corp_code.in_([cc for cc, _, _ in retry_list])
            ).all()
        }

    retried, succeeded = 0, 0
    for corp_code, year, quarter in retry_list:
        stock_code = code_map.get(corp_code)
        if not stock_code:
            continue
        for fs_div in ["CFS", "OFS"]:
            status = collect_financial(stock_code, year, quarter, fs_div)
            retried += 1
            if status == "success":
                succeeded += 1

    logger.info("[스케줄] 재시도 완료: %d건 시도, %d건 성공", retried, succeeded)


def _job_compute_flags_incremental() -> None:
    """최근 수집된 기업의 판단 플래그를 재계산한다."""
    from datetime import datetime
    from kreports.db.engine import get_session
    from kreports.db.models import FetchLog
    from kreports.judge.flags import compute_all_gap_flags, compute_all_trend_cf_flags
    from kreports.judge.beneish import compute_all_beneish

    cutoff = datetime.utcnow() - timedelta(hours=25)

    with get_session() as session:
        recent = (
            session.query(FetchLog.corp_code)
            .filter(
                FetchLog.task_type == "financial",
                FetchLog.status == "success",
                FetchLog.fetched_at >= cutoff,
            )
            .distinct()
            .all()
        )
        corp_codes = [r[0] for r in recent if r[0]]

    logger.info("[스케줄] 플래그 재계산 대상: %d개사", len(corp_codes))
    for corp_code in corp_codes:
        compute_all_gap_flags(corp_code)
        compute_all_trend_cf_flags(corp_code)
        compute_all_beneish(corp_code)

    logger.info("[스케줄] 플래그 재계산 완료")


# ---------------------------------------------------------------------------
# 스케줄러 설정 및 제어
# ---------------------------------------------------------------------------

def create_scheduler():
    """APScheduler BackgroundScheduler를 생성하고 반환한다 (시작 전)."""
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = BackgroundScheduler(timezone="Asia/Seoul")

    # 매일 오전 7시: 어제 신규 공시 수집
    scheduler.add_job(
        _job_sync_new_disclosures,
        CronTrigger(hour=7, minute=0),
        id="sync_disclosures",
        name="신규 공시 수집 (일별)",
        max_instances=1,
        replace_existing=True,
    )

    # 매일 오전 7시 30분: 실패 재시도
    scheduler.add_job(
        _job_retry_failed_financials,
        CronTrigger(hour=7, minute=30),
        id="retry_financials",
        name="재무 수집 실패 재시도",
        max_instances=1,
        replace_existing=True,
    )

    # 매일 오전 8시: 최근 수집 기업 플래그 재계산
    scheduler.add_job(
        _job_compute_flags_incremental,
        CronTrigger(hour=8, minute=0),
        id="compute_flags",
        name="판단 플래그 재계산 (증분)",
        max_instances=1,
        replace_existing=True,
    )

    return scheduler


def start_scheduler():
    """스케줄러를 시작하고 반환한다. FastAPI lifespan 또는 단독 실행에서 호출."""
    scheduler = create_scheduler()
    scheduler.start()
    logger.info(
        "[스케줄러] 시작됨 — %d개 작업 등록",
        len(scheduler.get_jobs()),
    )
    return scheduler


def list_jobs(scheduler) -> list[dict]:
    """등록된 작업 목록을 반환한다."""
    return [
        {
            "id": job.id,
            "name": job.name,
            "next_run": str(job.next_run_time),
        }
        for job in scheduler.get_jobs()
    ]


# ---------------------------------------------------------------------------
# 단독 실행
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import time
    import signal

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    sched = start_scheduler()

    def _shutdown(signum, frame):
        logger.info("스케줄러 종료 중...")
        sched.shutdown()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    logger.info("스케줄러 실행 중. Ctrl+C로 종료하세요.")
    try:
        while True:
            time.sleep(60)
    except (SystemExit, KeyboardInterrupt):
        pass
