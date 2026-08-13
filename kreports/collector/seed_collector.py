"""
seed_collector — 업종 벤치마킹용 핵심 기업 재무 자동 수집.

전체 상장사(3,951) 수집은 11시간 소요. 이 모듈은 시장 대표성 있는
핵심 기업만 우선 수집하여 20~30분 내에 벤치마킹 가능 상태를 만든다.

전략:
  small  — KOSPI 상위 200 + KOSDAQ 상위 150 (업종 분산)
  medium — KOSPI 전체 (~950)
  full   — 전체 상장사 (~3,951)

Q4(연간) 우선 수집: 벤치마킹은 연간 데이터만 사용하므로 Q4를 먼저 수집한다.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import date

from kreports.config import settings
from kreports.db.engine import get_session
from kreports.db.models import Company, Financial

logger = logging.getLogger(__name__)

# 사이즈별 설정
SEED_SIZES = {
    "small": {"kospi_limit": 200, "kosdaq_limit": 150, "desc": "KOSPI 200 + KOSDAQ 150"},
    "medium": {"kospi_limit": 1000, "kosdaq_limit": 0, "desc": "KOSPI 전체"},
    "full": {"kospi_limit": None, "kosdaq_limit": None, "desc": "전체 상장사"},
}


def _select_seed_companies(
    size: str = "small",
    ensure_industry_diversity: bool = True,
) -> list[dict]:
    """
    seed 수집 대상 기업 선택. 업종(induty_code) 분산을 보장한다.

    Returns:
        [{"stock_code", "corp_name", "market", "induty_code"}, ...]
    """
    cfg = SEED_SIZES.get(size, SEED_SIZES["small"])
    kospi_limit = cfg["kospi_limit"]
    kosdaq_limit = cfg["kosdaq_limit"]

    with get_session() as session:
        query = (
            session.query(
                Company.stock_code,
                Company.corp_name,
                Company.market,
                Company.induty_code,
            )
            .filter(Company.stock_code.isnot(None))
            .filter(Company.market.isnot(None))
        )

        kospi = (
            query.filter(Company.market == "KOSPI")
            .order_by(Company.corp_name)
            .all()
        )
        kosdaq = (
            query.filter(Company.market == "KOSDAQ")
            .order_by(Company.corp_name)
            .all()
        )

    result = []

    def _add(rows, limit):
        if limit is None:
            return [
                {"stock_code": r[0], "corp_name": r[1], "market": r[2], "induty_code": r[3]}
                for r in rows
            ]
        if not ensure_industry_diversity or limit >= len(rows):
            return [
                {"stock_code": r[0], "corp_name": r[1], "market": r[2], "induty_code": r[3]}
                for r in rows[:limit]
            ]

        # 업종 분산: 각 induty_code prefix 2자리에서 균등하게 선택
        by_industry: dict[str, list] = defaultdict(list)
        for r in rows:
            prefix = (r[3] or "00")[:2]
            by_industry[prefix].append(r)

        selected = []
        industries = sorted(by_industry.keys())
        per_industry = max(1, limit // len(industries))
        remaining = limit

        # 라운드 로빈
        for ind in industries:
            take = min(per_industry, len(by_industry[ind]), remaining)
            for r in by_industry[ind][:take]:
                selected.append(
                    {"stock_code": r[0], "corp_name": r[1], "market": r[2], "induty_code": r[3]}
                )
                remaining -= 1
            if remaining <= 0:
                break

        # 남은 자리 채우기 (큰 업종에서)
        if remaining > 0:
            used = {s["stock_code"] for s in selected}
            for r in rows:
                if r[0] not in used:
                    selected.append(
                        {"stock_code": r[0], "corp_name": r[1], "market": r[2], "induty_code": r[3]}
                    )
                    remaining -= 1
                    if remaining <= 0:
                        break

        return selected

    result.extend(_add(kospi, kospi_limit))
    if kosdaq_limit != 0:
        result.extend(_add(kosdaq, kosdaq_limit))

    return result


def _already_collected(stock_code: str, year: int, quarter: int) -> bool:
    """Financial 레코드가 이미 존재하면 True (중복 수집 방지)."""
    from kreports.collector.corp_sync import get_corp_code
    corp_code = get_corp_code(stock_code)
    if not corp_code:
        return False
    with get_session() as session:
        exists = (
            session.query(Financial.id)
            .filter_by(corp_code=corp_code, year=year, quarter=quarter)
            .first()
        )
    return exists is not None


def collect_seed(
    size: str = "small",
    year_from: int | None = None,
    year_to: int | None = None,
    annual_only: bool = True,
    progress_callback=None,
) -> dict:
    """
    seed 기업의 재무데이터를 수집한다. Q4 우선.

    Args:
        size: small / medium / full
        year_from: 시작 연도 (기본: 최근 3년)
        year_to: 종료 연도 (기본: 올해)
        annual_only: True면 Q4만 수집 (벤치마킹 용도)
        progress_callback: fn(done, total, corp_name, year, quarter) 호출

    Returns:
        {"companies": int, "collected": int, "skipped": int, "error": int,
         "industries_covered": int}
    """
    from kreports.collector.fin_collector import collect_financial

    current_year = date.today().year
    if year_to is None:
        year_to = current_year
    if year_from is None:
        year_from = year_to - 2  # 최근 3년

    companies = _select_seed_companies(size)
    quarters = [4] if annual_only else [4, 1, 2, 3]  # Q4 먼저

    # 수집 대상: (stock_code, corp_name, year, quarter) 튜플
    targets = []
    for c in companies:
        for year in range(year_from, year_to + 1):
            for q in quarters:
                if year == current_year and q > _current_quarter():
                    continue
                targets.append((c["stock_code"], c["corp_name"], year, q))

    total = len(targets)
    collected = skipped = error = 0

    for idx, (stock_code, corp_name, year, q) in enumerate(targets, 1):
        if progress_callback:
            progress_callback(idx, total, corp_name, year, q)

        # 중복 방지
        if _already_collected(stock_code, year, q):
            skipped += 1
            continue

        status = collect_financial(stock_code, year, q)
        time.sleep(settings.request_delay)

        if status == "success":
            collected += 1
        elif status == "no_data":
            skipped += 1
        else:
            error += 1

    # 업종 커버리지 계산
    with get_session() as session:
        from sqlalchemy import func, distinct
        industries = (
            session.query(func.count(distinct(
                func.substr(Company.induty_code, 1, 2)
            )))
            .join(Financial, Company.corp_code == Financial.corp_code)
            .filter(Financial.quarter == 4)
            .scalar()
        ) or 0

    logger.info(
        "seed 수집 완료: %d사 대상, collected=%d skipped=%d error=%d industries=%d",
        len(companies), collected, skipped, error, industries,
    )

    return {
        "companies": len(companies),
        "targets": total,
        "collected": collected,
        "skipped": skipped,
        "error": error,
        "industries_covered": industries,
    }


def _current_quarter() -> int:
    month = date.today().month
    return (month - 1) // 3 + 1
