#!/usr/bin/env python3
"""Retry financial fetch_log rows that failed for environment/transient reasons.

This is intentionally separate from no_data retry. no_data usually means DART
has no matching filing for the endpoint. error rows here are mostly quota,
invalid-key, or network failures and should be retried only after the collector
environment is known-good.
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter

from sqlalchemy import text

from kreports.collector.fin_collector import DartApiAuthError, DartApiLimitExceeded, collect_financial
from kreports.config import settings
from kreports.db.engine import get_session, init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("backfill-error-financials")

RETRYABLE_ERROR_MARKERS = (
    "사용한도",
    "초과",
    "등록되지 않은 인증키",
    "nodename nor servname",
    "name or service not known",
    "temporary failure",
    "timed out",
    "timeout",
    "connection",
)


def _error_bucket(message: str | None) -> str:
    msg = (message or "").lower()
    if "사용한도" in msg or "limit" in msg or "초과" in msg:
        return "dart_limit"
    if "등록되지 않은 인증키" in msg or "invalid key" in msg or "invalid api key" in msg:
        return "auth_key"
    if "nodename nor servname" in msg or "name or service not known" in msg:
        return "dns"
    if "timed out" in msg or "timeout" in msg or "connection" in msg or "temporary failure" in msg:
        return "network"
    if not msg:
        return "empty_error"
    return "other"


def _is_retryable(message: str | None) -> bool:
    msg = (message or "").lower()
    return any(marker.lower() in msg for marker in RETRYABLE_ERROR_MARKERS)


def list_targets(
    *,
    market: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    quarter: int | None = None,
    include_other_errors: bool = False,
) -> list:
    sql = """
        WITH latest_error AS (
          SELECT fl.corp_code, fl.year, fl.quarter, MAX(fl.fetched_at) latest_at
          FROM fetch_log fl
          WHERE fl.task_type='financial'
            AND fl.status='error'
          GROUP BY fl.corp_code, fl.year, fl.quarter
        )
        SELECT le.corp_code, le.year, le.quarter, c.stock_code, c.market, c.corp_name,
               fl.error_msg, le.latest_at
        FROM latest_error le
        JOIN fetch_log fl
          ON fl.corp_code=le.corp_code
         AND fl.year=le.year
         AND fl.quarter=le.quarter
         AND fl.fetched_at=le.latest_at
         AND fl.task_type='financial'
         AND fl.status='error'
        JOIN companies c ON c.corp_code=le.corp_code
        WHERE c.stock_code IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM financials f
              WHERE f.corp_code=le.corp_code
                AND f.year=le.year
                AND f.quarter=le.quarter
          )
    """
    params: dict[str, object] = {}
    if market:
        sql += " AND c.market=:market"
        params["market"] = market.upper()
    if year_from:
        sql += " AND le.year >= :year_from"
        params["year_from"] = int(year_from)
    if year_to:
        sql += " AND le.year <= :year_to"
        params["year_to"] = int(year_to)
    if quarter:
        sql += " AND le.quarter = :quarter"
        params["quarter"] = int(quarter)
    sql += " ORDER BY c.market, c.corp_name, le.year, le.quarter"

    with get_session() as session:
        rows = session.execute(text(sql), params).fetchall()
    if include_other_errors:
        return rows
    return [row for row in rows if _is_retryable(row.error_msg)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Retry retryable financial error rows")
    parser.add_argument("--market", help="KOSPI|KOSDAQ|KONEX")
    parser.add_argument("--year-from", type=int)
    parser.add_argument("--year-to", type=int)
    parser.add_argument("--quarter", type=int, choices=[1, 2, 3, 4])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--include-other-errors", action="store_true")
    parser.add_argument("--max-consecutive-errors", type=int, default=20)
    args = parser.parse_args()

    if not args.dry_run and not settings.dart_api_key:
        logger.error("DART_API_KEY 미설정")
        return 1

    init_db()
    targets = list_targets(
        market=args.market,
        year_from=args.year_from,
        year_to=args.year_to,
        quarter=args.quarter,
        include_other_errors=args.include_other_errors,
    )
    if args.limit:
        targets = targets[: args.limit]

    by_bucket = Counter(_error_bucket(row.error_msg) for row in targets)
    logger.info("retry targets=%d buckets=%s", len(targets), dict(by_bucket))
    if args.dry_run:
        by_market = Counter(row.market for row in targets)
        by_year = Counter(row.year for row in targets)
        logger.info("by_market=%s", dict(by_market))
        logger.info("by_year=%s", dict(sorted(by_year.items())))
        return 0

    counts: Counter = Counter()
    consecutive_errors = 0
    for idx, row in enumerate(targets, 1):
        if idx % 50 == 0:
            logger.info("progress %d/%d counts=%s", idx, len(targets), dict(counts))
        try:
            status = collect_financial(row.stock_code, row.year, row.quarter)
        except (DartApiAuthError, DartApiLimitExceeded) as exc:
            logger.error("stopping on non-retryable collector state: %s", exc)
            counts["stopped"] += 1
            break
        except Exception as exc:  # keep transient retry batches alive
            logger.warning("exception [%s %s Q%s]: %s", row.corp_name, row.year, row.quarter, exc)
            status = "error"

        counts[status] += 1
        if status == "error":
            consecutive_errors += 1
            if consecutive_errors >= args.max_consecutive_errors:
                logger.error("stopping after consecutive errors=%d", consecutive_errors)
                break
        else:
            consecutive_errors = 0

    logger.info("done counts=%s", dict(counts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
