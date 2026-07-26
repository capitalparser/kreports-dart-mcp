"""
일별 증분 수집 스케줄러 (APScheduler 기반).

사용법:
    # 백그라운드 서비스로 단독 실행
    python -m dart_platform.collector.scheduler

    # FastAPI 서버 시작 시 함께 실행 (api/main.py 내 start_scheduler() 호출)
"""
import logging
from datetime import date, timedelta
import os
import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kreports.maintenance.backfill_runs import BackfillLease

logger = logging.getLogger(__name__)

# 설정 상수
_DAILY_BATCH_LIMIT = 9_000      # DART 일일 API 한도(10,000) 여유분
_RETRY_LIMIT = 3                # fetch_log 실패 재시도 최대 횟수


def run_resumable_financial_backfill(
    lease: "BackfillLease",
    *,
    year_from: int | None,
    year_to: int | None,
    market: str | None,
    force: bool = False,
    progress_callback=None,
) -> dict[str, int]:
    """Collect companies in stable order and checkpoint after every company."""
    from kreports.collector.fin_collector import (
        _LISTED_MARKETS,
        collect_financial_range,
    )
    from kreports.db.engine import get_session
    from kreports.db.models import BackfillRun, Company, FetchLog
    from kreports.maintenance.backfill_runs import (
        FAILURE_OUTCOMES,
        BackfillLease,
        BackfillRunError,
        classify_backfill_error,
    )

    state = BackfillLease.resume_point(lease.id)
    last_corp_code = state.get("last_corp_code")
    with get_session() as session:
        run = session.get(BackfillRun, lease.id)
        if run is None:
            raise KeyError(f"backfill run not found: {lease.id}")
        attempted = run.attempted_count or 0
        saved = run.saved_count or 0
        no_data = run.no_data_count or 0
        errors = run.error_count or 0
        query = session.query(
            Company.corp_code,
            Company.stock_code,
            Company.corp_name,
        ).filter(Company.stock_code.isnot(None))
        if market:
            query = query.filter(Company.market == market.upper())
        else:
            query = query.filter(Company.market.in_(_LISTED_MARKETS))
        if last_corp_code:
            query = query.filter(Company.corp_code > str(last_corp_code))
        companies = query.order_by(Company.corp_code.asc()).all()

    skipped = _non_negative_int(state.get("skipped"), default=0)
    total = attempted + len(companies)

    for corp_code, stock_code, corp_name in companies:
        result = collect_financial_range(
            stock_code,
            year_from,
            year_to,
            force=force,
        )
        for failure_outcome in sorted(FAILURE_OUTCOMES):
            if _non_negative_int(result.get(failure_outcome), default=0):
                raise BackfillRunError(
                    failure_outcome,
                    f"financial backfill failed for {corp_code}: "
                    f"{failure_outcome}",
                )
        company_errors = _non_negative_int(result.get("error"), default=0)
        if company_errors:
            with get_session() as session:
                error_query = session.query(FetchLog.error_msg).filter(
                    FetchLog.task_type == "financial",
                    FetchLog.corp_code == corp_code,
                    FetchLog.status == "error",
                    FetchLog.error_msg.isnot(None),
                    FetchLog.error_msg != "",
                )
                if year_from is not None:
                    error_query = error_query.filter(FetchLog.year >= year_from)
                if year_to is not None:
                    error_query = error_query.filter(FetchLog.year <= year_to)
                latest_message = (
                    error_query.order_by(
                        FetchLog.fetched_at.desc(),
                        FetchLog.id.desc(),
                    )
                    .limit(1)
                    .scalar()
                )
            detail = latest_message or (
                f"{company_errors} unclassified financial collection errors"
            )
            raise BackfillRunError(
                classify_backfill_error(RuntimeError(detail)),
                f"financial backfill failed for {corp_code}: {detail}",
            )
        attempted += 1
        saved += _non_negative_int(result.get("success"), default=0)
        no_data += _non_negative_int(result.get("no_data"), default=0)
        errors += _non_negative_int(result.get("error"), default=0)
        skipped += _non_negative_int(result.get("skipped"), default=0)
        lease.checkpoint(
            {
                "attempted": attempted,
                "errors": errors,
                "last_corp_code": corp_code,
                "no_data": no_data,
                "saved": saved,
                "skipped": skipped,
            },
            attempted=attempted,
            saved=saved,
            no_data=no_data,
            errors=errors,
        )
        if progress_callback:
            progress_callback(attempted, total, corp_name)

    return {
        "attempted": attempted,
        "saved": saved,
        "no_data": no_data,
        "errors": errors,
        "skipped": skipped,
    }


def _non_negative_int(value, *, default: int) -> int:
    if isinstance(value, int) and value >= 0:
        return value
    return default


def orchestrate_complete_backfill(
    lease: "BackfillLease",
    *,
    year_from: int = 2021,
    year_to: int = 2025,
    disclosure_end_year: int = 2026,
) -> dict[str, int]:
    """Run the complete dataset workflow under one durable Python owner.

    Individual collector commands retain their own feature-specific leases.
    This outer lease checkpoints the last completed pipeline step and keeps a
    heartbeat alive while a child command is running.
    """
    from kreports.db.engine import get_session
    from kreports.db.models import BackfillRun, Disclosure
    from kreports.maintenance.backfill_runs import BackfillLease

    state = BackfillLease.resume_point(lease.id)
    completed_steps = {
        str(item)
        for item in state.get("completed_steps", [])
        if isinstance(item, str)
    }
    with get_session() as session:
        run = session.get(BackfillRun, lease.id)
        if run is None:
            raise KeyError(f"backfill run not found: {lease.id}")
        disclosure_rows = session.query(Disclosure).count()
        attempted = run.attempted_count or 0
        saved = run.saved_count or 0
        errors = run.error_count or 0

    steps: list[tuple[str, list[str], bool]] = []
    if disclosure_rows < 100_000:
        for market in ("KOSPI", "KOSDAQ"):
            steps.append(
                (
                    f"initial disclosure list {market}",
                    [
                        "collect-disclosures",
                        "--market",
                        market,
                        "--start-date",
                        f"{year_from}0101",
                        "--end-date",
                        f"{disclosure_end_year}1231",
                    ],
                    True,
                )
            )

    if os.getenv("KREPORTS_ENABLE_RAW_BACKFILL", "0") == "1":
        from kreports.runtime import (
            raw_storage_policy,
            require_raw_backfill_mode,
        )

        raw_backend, raw_keep_inline, _raw_bucket = raw_storage_policy()
        require_raw_backfill_mode(
            "complete dataset raw report backfill",
            raw_storage_backend=raw_backend,
            raw_storage_keep_inline=raw_keep_inline,
        )
        for year, market in _raw_gap_targets(year_from, year_to):
            steps.extend(
                [
                    (
                        f"business report sections {year} {market}",
                        [
                            "collect-business-report-sections",
                            "--year",
                            str(year),
                            "--market",
                            market,
                        ],
                        True,
                    ),
                    (
                        f"audit report sections {year} {market}",
                        [
                            "collect-audit-report-sections",
                            "--year",
                            str(year),
                            "--market",
                            market,
                        ],
                        True,
                    ),
                    (
                        f"business-report attached audit reports {year} {market}",
                        [
                            "__python_script__",
                            "scripts/backfill_business_report_audit_attachments.py",
                            "--start-year",
                            str(year),
                            "--end-year",
                            str(year),
                            "--market",
                            market,
                        ],
                        True,
                    ),
                    (
                        f"audit-submission sections {year} {market}",
                        [
                            "__python_script__",
                            "scripts/backfill_audit_submission_sections.py",
                            "--start-year",
                            str(year),
                            "--end-year",
                            str(year),
                            "--market",
                            market,
                        ],
                        True,
                    ),
                ]
            )

    steps.append(
        (
            "financial facts",
            [
                "collect-all",
                "--year-from",
                str(year_from),
                "--year-to",
                str(year_to),
            ],
            True,
        )
    )
    steps.append(
        (
            "rebuild compact financial facts",
            [
                "rebuild-financial-facts-compact",
                "--year-from",
                str(year_from),
                "--year-to",
                str(year_to),
            ],
            False,
        )
    )
    for market in ("KOSPI", "KOSDAQ"):
        steps.append(
            (
                f"disclosure list {market}",
                [
                    "collect-disclosures",
                    "--market",
                    market,
                    "--start-date",
                    f"{year_from}0101",
                    "--end-date",
                    f"{disclosure_end_year}1231",
                ],
                True,
            )
        )
    for year in range(year_from, disclosure_end_year + 1):
        for market in ("KOSPI", "KOSDAQ"):
            steps.append(
                (
                    f"disclosure event index {year} {market}",
                    [
                        "rebuild-disclosure-events",
                        "--year",
                        str(year),
                        "--market",
                        market,
                    ],
                    False,
                )
            )
    for year in range(year_from, year_to + 1):
        for source_type in ("business_report", "audit_report"):
            steps.append(
                (
                    f"document extractors {year} {source_type}",
                    [
                        "run-document-extractors",
                        "--year",
                        str(year),
                        "--source-type",
                        source_type,
                    ],
                    False,
                )
            )
    steps.extend(
        [
            ("rebuild audit matters", ["rebuild-audit-matter-items"], False),
            ("rebuild audit procedures", ["index-audit-procedures"], False),
            (
                "rebuild evidence documents",
                [
                    "rebuild-evidence-documents",
                    "--year-from",
                    str(year_from),
                    "--year-to",
                    str(year_to),
                    "--max-text-chars",
                    "12000",
                ],
                False,
            ),
            ("auditors all", ["collect-auditors"], True),
        ]
    )
    for market in ("KOSPI", "KOSDAQ"):
        steps.append(
            (
                f"audit fees {market}",
                [
                    "collect-audit-fees",
                    "--year-from",
                    str(year_from),
                    "--year-to",
                    str(year_to),
                    "--market",
                    market,
                ],
                True,
            )
        )
    steps.extend(
        [
            (
                "raw annual report coverage",
                [
                    "raw-annual-report-coverage",
                    "--start-filing-year",
                    str(year_from + 1),
                    "--end-filing-year",
                    str(disclosure_end_year),
                ],
                False,
            ),
            (
                "evidence document readiness",
                ["evidence-document-readiness"],
                False,
            ),
            (
                "investor dataset readiness",
                [
                    "investor-dataset-readiness",
                    "--year",
                    str(year_to),
                    "--years-back",
                    str(year_to - year_from + 1),
                ],
                False,
            ),
            (
                "auditor feature readiness",
                ["auditor-feature-readiness", "--year", str(year_to)],
                False,
            ),
            (
                "auditor dataset readiness",
                [
                    "dataset-auditor-readiness",
                    "--year",
                    str(year_to),
                    "--years-back",
                    str(year_to - year_from + 1),
                ],
                False,
            ),
            ("dataset audit", ["dataset-audit", "--top", "20"], False),
        ]
    )

    from kreports.maintenance.backfill_runs import BackfillRunError

    api_failure: BackfillRunError | None = None
    for step_index, (name, args, uses_api) in enumerate(steps):
        if name in completed_steps:
            continue
        attempted += 1
        if uses_api and api_failure is not None:
            outcome = "skipped_after_api_failure"
        else:
            try:
                _run_cli_with_heartbeat(lease, args)
            except BackfillRunError as exc:
                errors += 1
                outcome = f"failed:{exc.outcome}"
                if uses_api and api_failure is None:
                    api_failure = exc
                elif not uses_api:
                    raise
            else:
                saved += 1
                outcome = "success"
                completed_steps.add(name)
        lease.checkpoint(
            {
                "completed_steps": sorted(completed_steps),
                "last_step": step_index,
                "last_step_name": name,
                "last_step_outcome": outcome,
            },
            attempted=attempted,
            saved=saved,
            no_data=0,
            errors=errors,
        )

    if api_failure is not None:
        raise api_failure
    return {
        "attempted": attempted,
        "saved": saved,
        "no_data": 0,
        "errors": 0,
        "error_attempts": errors,
    }


def _run_cli_with_heartbeat(
    lease: "BackfillLease",
    args: list[str],
) -> None:
    if args and args[0] == "__python_script__":
        command = [sys.executable, *args[1:]]
    else:
        command = [sys.executable, "-m", "kreports.cli.main", *args]
    process = subprocess.Popen(command)
    while True:
        try:
            return_code = process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            lease.heartbeat()
            continue
        if return_code != 0:
            raise _child_process_failure(
                pid=process.pid,
                return_code=return_code,
                args=args,
            )
        return


def _child_process_failure(
    *,
    pid: int,
    return_code: int,
    args: list[str],
):
    from kreports.db.engine import get_session
    from kreports.db.models import BackfillRun
    from kreports.maintenance.backfill_runs import (
        FAILURE_OUTCOMES,
        BackfillRunError,
        classify_backfill_error,
    )

    with get_session() as session:
        child_run = (
            session.query(BackfillRun.status, BackfillRun.error_msg)
            .filter(
                BackfillRun.pid == pid,
                BackfillRun.status != "running",
            )
            .order_by(BackfillRun.id.desc())
            .first()
        )
    if child_run is not None:
        status, error_message = child_run
        if status in FAILURE_OUTCOMES:
            return BackfillRunError(
                status,
                error_message or f"child command failed with exit {return_code}",
            )
        if error_message:
            return BackfillRunError(
                classify_backfill_error(RuntimeError(error_message)),
                error_message,
            )
    command_name = args[1] if args and args[0] == "__python_script__" else args[0]
    exit_outcomes = {
        75: "quota_exceeded",
        76: "transport_error",
        77: "parse_error",
        78: "storage_error",
    }
    if return_code in exit_outcomes:
        outcome = exit_outcomes[return_code]
    elif "extract" in command_name or "section" in command_name:
        outcome = "parse_error"
    elif command_name.startswith(("collect-", "orchestrate-")):
        outcome = "transport_error"
    else:
        outcome = "storage_error"
    return BackfillRunError(
        outcome,
        f"child command {command_name} failed with exit {return_code}",
    )


def _raw_gap_targets(
    year_from: int,
    year_to: int,
) -> list[tuple[int, str]]:
    years = list(range(year_from, year_to + 1))
    priority = [2023, 2022, 2021, 2024, 2025]
    ordered = [year for year in priority if year in years]
    ordered.extend(year for year in years if year not in ordered)
    return [
        (year, market)
        for year in ordered
        for market in ("KOSDAQ", "KOSPI")
    ]


# ---------------------------------------------------------------------------
# 개별 증분 작업
# ---------------------------------------------------------------------------

def _job_sync_new_disclosures() -> None:
    """어제 공시된 신규 공시를 전체 상장사 대상으로 수집한다."""
    from kreports.db.engine import get_session
    from kreports.db.models import Company
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
