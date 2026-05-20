"""Backfill detailed audit-report bodies from business-report attachment pages.

The canonical company-year anchor is the annual business report. This script
uses existing disclosure rows, opens each business-report DART page, discovers
attached audit reports by dcmNo, and persists detailed audit-report sections.
It does not fetch business-report document.xml, so it can continue even when a
DART OpenAPI key is not configured in the shell.
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime

from sqlalchemy import text

from kreports.collector.report_document_collector import collect_attached_audit_reports_for_disclosure
from kreports.db.engine import get_session, init_db


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-year", type=int, default=2020)
    parser.add_argument("--end-year", type=int, default=2024)
    parser.add_argument("--market", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--include-existing", action="store_true")
    return parser.parse_args()


def _targets(start_year: int, end_year: int, market: str | None, limit: int | None, missing_only: bool) -> list[dict]:
    stmt = """
        SELECT d.rcept_no, d.corp_code, c.corp_name, c.stock_code, c.market, d.disc_date, d.report_nm
        FROM disclosures d
        JOIN companies c ON c.corp_code=d.corp_code
        WHERE c.stock_code IS NOT NULL
          AND d.report_nm LIKE '%사업보고서%'
          AND d.report_nm NOT LIKE '%제출기한연장%'
          AND d.report_nm NOT LIKE '%해외증권%'
          AND d.disc_date BETWEEN :start_date AND :end_date
          AND NOT EXISTS (
            SELECT 1 FROM disclosures d2
            WHERE d2.corp_code=d.corp_code
              AND d2.report_nm LIKE '%사업보고서%'
              AND d2.report_nm NOT LIKE '%제출기한연장%'
              AND d2.report_nm NOT LIKE '%해외증권%'
              AND d2.disc_date BETWEEN :start_date AND :end_date
              AND d2.disc_date > d.disc_date
          )
    """
    params: dict[str, object] = {
        "start_date": f"{start_year + 1}-01-01",
        "end_date": f"{end_year + 1}-12-31",
    }
    if market:
        stmt += " AND c.market=:market"
        params["market"] = market
    if missing_only:
        stmt += """
          AND NOT EXISTS (
            SELECT 1 FROM report_sections rs
            WHERE (rs.rcept_no=d.rcept_no OR rs.rcept_no LIKE d.rcept_no || '_%')
              AND rs.source_type='audit_report'
              AND rs.section_key='kam'
          )
        """
    stmt += " ORDER BY d.disc_date, c.market, c.corp_name"
    if limit:
        stmt += " LIMIT :limit"
        params["limit"] = limit

    with get_session() as session:
        return [dict(row) for row in session.execute(text(stmt), params).mappings().all()]


def main() -> int:
    args = _parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    init_db()
    targets = _targets(
        args.start_year,
        args.end_year,
        args.market,
        args.limit,
        missing_only=not args.include_existing,
    )
    logging.info(
        "business-report audit attachment backfill start start_year=%s end_year=%s market=%s targets=%s",
        args.start_year,
        args.end_year,
        args.market or "ALL",
        len(targets),
    )

    ok = failed = documents = sections = 0
    for idx, target in enumerate(targets, 1):
        started = datetime.utcnow()
        try:
            result = collect_attached_audit_reports_for_disclosure(target["rcept_no"])
            if result.get("ok"):
                ok += 1
                documents += int(result.get("documents") or 0)
                sections += int(result.get("sections") or 0)
                status = "ok"
            else:
                failed += 1
                status = f"failed:{result.get('error')}"
        except Exception as exc:
            failed += 1
            status = f"error:{type(exc).__name__}:{exc}"
        elapsed = (datetime.utcnow() - started).total_seconds()
        logging.info(
            "[%s/%s] %s %s %s rcept_no=%s docs=%s sections=%s elapsed=%.2fs",
            idx,
            len(targets),
            status,
            target.get("market"),
            target.get("corp_name"),
            target.get("rcept_no"),
            documents,
            sections,
            elapsed,
        )

    logging.info(
        "business-report audit attachment backfill done targets=%s ok=%s failed=%s documents=%s sections=%s",
        len(targets),
        ok,
        failed,
        documents,
        sections,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
