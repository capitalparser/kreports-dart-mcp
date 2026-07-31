#!/usr/bin/env python3
"""
backfill_no_data_financials.py — fetch_log status='no_data' 재시도 백필 스크립트.

목적:
  acntall 단일 엔드포인트로 수집 실패한 (corp_code, year, quarter) 조합을
  새로운 fnlttSinglAcnt 폴백 체인으로 재시도한다.
  KOSDAQ 소형주 갭 (현재 ~74%) 보완용.

사용:
  # dry-run (대상 건수만 표시)
  python scripts/backfill_no_data_financials.py --dry-run

  # 특정 시장만
  python scripts/backfill_no_data_financials.py --market KOSDAQ

  # 단일 종목 재시도 (검증용)
  python scripts/backfill_no_data_financials.py --stock 108490

  # 최근 1년만 (DART 쿼터 절약)
  python scripts/backfill_no_data_financials.py --year-from 2024 --year-to 2024

  # 사업보고서(Q4)만 우선 백필
  python scripts/backfill_no_data_financials.py --market KOSDAQ --year-from 2024 --quarter 4

  # 실제 실행
  python scripts/backfill_no_data_financials.py --market KOSDAQ --year-from 2024

전제:
  - DART 쿼터 여유 있을 때 실행 (status=020 시 자동 중단됨)
  - DB_URL, DART_API_KEY 환경변수 설정
"""
import argparse
import logging
import sys
from collections import Counter

from sqlalchemy import text

from kreports.config import settings
from kreports.db.engine import get_session, init_db
from kreports.collector.fin_collector import collect_financial

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("backfill")


def _list_targets(market: str | None, stock: str | None,
                  year_from: int | None, year_to: int | None,
                  quarter: int | None) -> list[tuple[str, int, int]]:
    """no_data로 기록된 (corp_code, year, quarter) 후보를 stock_code로 매핑하여 반환."""
    sql = """
        SELECT DISTINCT fl.corp_code, fl.year, fl.quarter, c.stock_code, c.market, c.corp_name
        FROM fetch_log fl
        JOIN companies c ON fl.corp_code = c.corp_code
        WHERE fl.task_type = 'financial'
          AND fl.status = 'no_data'
          AND c.stock_code IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM financials f
              WHERE f.corp_code = fl.corp_code
                AND f.year = fl.year
                AND f.quarter = fl.quarter
          )
    """
    params: dict = {}
    if market:
        sql += " AND c.market = :market"
        params["market"] = market
    if stock:
        sql += " AND c.stock_code = :stock"
        params["stock"] = stock
    if year_from:
        sql += " AND fl.year >= :y_from"
        params["y_from"] = year_from
    if year_to:
        sql += " AND fl.year <= :y_to"
        params["y_to"] = year_to
    if quarter:
        sql += " AND fl.quarter = :quarter"
        params["quarter"] = quarter
    sql += " ORDER BY c.market, c.corp_name, fl.year DESC, fl.quarter"

    with get_session() as session:
        rows = session.execute(text(sql), params).fetchall()
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="no_data 재무 데이터 백필")
    ap.add_argument("--market", help="KOSPI|KOSDAQ|KONEX 필터")
    ap.add_argument("--stock", help="단일 종목코드 (검증용)")
    ap.add_argument("--year-from", type=int, dest="year_from")
    ap.add_argument("--year-to", type=int, dest="year_to")
    ap.add_argument("--quarter", type=int, choices=[1, 2, 3, 4],
                    help="특정 분기만 재시도 (4=사업보고서)")
    ap.add_argument("--dry-run", action="store_true",
                    help="대상 건수만 확인하고 실행 안 함")
    ap.add_argument("--limit", type=int, default=None,
                    help="최대 처리 건수 (쿼터 보호)")
    ap.add_argument("--max-consecutive-errors", type=int, default=20,
                    help="연속 error가 이 값을 넘으면 중단")
    args = ap.parse_args()

    if not settings.dart_api_key:
        logger.error("DART_API_KEY 미설정")
        return 1

    init_db()

    targets = _list_targets(
        args.market,
        args.stock,
        args.year_from,
        args.year_to,
        args.quarter,
    )
    logger.info("대상 건수: %d", len(targets))

    if args.dry_run:
        # 시장별 분포 요약
        by_market = Counter(r.market for r in targets)
        for m, n in by_market.most_common():
            logger.info("  %s: %d건", m or "(unknown)", n)
        return 0

    if args.limit:
        targets = targets[: args.limit]
        logger.info("limit 적용 → 실제 처리: %d건", len(targets))

    counts: Counter = Counter()
    consecutive_errors = 0
    for idx, row in enumerate(targets, 1):
        if idx % 50 == 0:
            logger.info("진행 %d/%d (success=%d, no_data=%d, error=%d)",
                        idx, len(targets), counts["success"],
                        counts["no_data"], counts["error"])
        try:
            status = collect_financial(row.stock_code, row.year, row.quarter)
        except Exception as e:
            logger.warning("예외 [%s %s Q%s]: %s",
                           row.corp_name, row.year, row.quarter, e)
            counts["error"] += 1
            consecutive_errors += 1
            # DART 쿼터 소진 (status=020) 같은 영구 오류면 중단
            if "사용한도" in str(e) or consecutive_errors >= args.max_consecutive_errors:
                logger.error("DART 쿼터 소진 — 중단")
                break
            continue
        counts[status] += 1
        if status == "error":
            consecutive_errors += 1
            if consecutive_errors >= args.max_consecutive_errors:
                logger.error("연속 error %d건 — 중단", consecutive_errors)
                break
        else:
            consecutive_errors = 0

    logger.info("완료: success=%d, no_data=%d, error=%d",
                counts["success"], counts["no_data"], counts["error"])
    if counts["success"]:
        logger.info("source='acnt' 신규 행 수: SELECT COUNT(*) FROM financials WHERE source='acnt'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
