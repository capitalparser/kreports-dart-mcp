import logging
import time
from datetime import datetime

from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from kreports.collector.fetcher import fetch_corp_code_xml, fetch_company_info
from kreports.config import settings
from kreports.db.engine import get_session
from kreports.db.models import Company

logger = logging.getLogger(__name__)


def sync_companies(market_filter: list[str] | None = None) -> int:
    """
    DART 기업코드 목록을 다운로드하고 companies 테이블에 upsert한다.

    Args:
        market_filter: 수집할 시장 목록 (예: ["KOSPI", "KOSDAQ"])
                       None이면 상장사 전체 (stock_code가 있는 기업)

    Returns:
        upsert된 기업 수
    """
    companies = fetch_corp_code_xml()

    # 상장사만 필터링 (stock_code가 있는 기업)
    # corpCode.xml에는 시장 구분(corp_cls)이 없으므로 stock_code 유무로만 판단
    listed = [c for c in companies if c["stock_code"]]

    logger.info("동기화 대상: %d개사", len(listed))

    now = datetime.utcnow()
    with get_session() as session:
        for chunk_start in range(0, len(listed), 500):
            chunk = listed[chunk_start:chunk_start + 500]
            stmt = sqlite_insert(Company).values([
                {
                    "corp_code": c["corp_code"],
                    "stock_code": c["stock_code"],
                    "corp_name": c["corp_name"],
                    "market": c["market"],
                    "updated_at": now,
                }
                for c in chunk
            ])
            stmt = stmt.on_conflict_do_update(
                index_elements=["corp_code"],
                set_={
                    "stock_code": stmt.excluded.stock_code,
                    "corp_name": stmt.excluded.corp_name,
                    "market": stmt.excluded.market,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
            session.execute(stmt)

    logger.info("기업 동기화 완료: %d개", len(listed))
    return len(listed)


def _fetch_company_info_with_retry(corp_code: str, max_attempts: int = 2) -> dict | None:
    """
    fetch_company_info를 재시도 로직과 함께 호출.
    완결성(completeness) 우선: 일시적 네트워크·rate limit 오류에 대비해
    실패 시 긴 backoff 후 1회 재시도한다.
    """
    for attempt in range(1, max_attempts + 1):
        info = fetch_company_info(corp_code)
        if info is not None:
            return info
        if attempt < max_attempts:
            # 일시적 오류 대비: 2초 후 재시도
            logger.info("company.json 재시도 [%s] attempt=%d", corp_code, attempt + 1)
            time.sleep(2.0)
    return None


def enrich_market(
    progress_callback=None,
    corp_codes: list[str] | None = None,
    limit: int | None = None,
    request_delay: float | None = None,
) -> dict:
    """
    market 또는 induty_code가 NULL인 상장사에 대해 DART company.json으로
    시장구분(market)과 업종코드(induty_code)를 보완한다.
    corp_codes를 지정하면 해당 기업만 대상으로 재실행할 수 있다.

    Returns:
        {"updated": int, "skipped": int, "error": int, "total": int,
         "induty_filled": int}
    """
    from sqlalchemy import or_

    with get_session() as session:
        query = session.query(Company.corp_code).filter(Company.stock_code.isnot(None))
        if corp_codes:
            query = query.filter(Company.corp_code.in_(corp_codes))
        else:
            query = query.filter(or_(Company.market.is_(None), Company.induty_code.is_(None)))
        if limit is not None:
            query = query.limit(limit)
        target_codes = [r[0] for r in query.order_by(Company.corp_code).all()]

    total = len(target_codes)
    updated = skipped = error = 0
    induty_filled = 0
    delay = settings.request_delay if request_delay is None else request_delay

    for idx, corp_code in enumerate(target_codes, 1):
        if progress_callback:
            progress_callback(idx, total, corp_code)

        info = _fetch_company_info_with_retry(corp_code)
        time.sleep(delay)

        if info is None:
            error += 1
            continue

        market = info.get("market")              # KOSPI / KOSDAQ / KONEX / None(기타)
        induty_code = info.get("induty_code")    # KSIC 업종코드 (5자리)

        update_fields: dict[str, str] = {}
        if market:
            update_fields["market"] = market
        if induty_code:
            update_fields["induty_code"] = induty_code[:5]
            induty_filled += 1

        if not update_fields:
            skipped += 1
            continue

        with get_session() as session:
            session.query(Company).filter_by(corp_code=corp_code).update(update_fields)

        updated += 1

    logger.info(
        "시장구분·업종코드 보완 완료: 업데이트 %d, 기타 %d, 오류 %d, induty 채움 %d",
        updated, skipped, error, induty_filled,
    )
    return {
        "updated": updated,
        "skipped": skipped,
        "error": error,
        "total": total,
        "induty_filled": induty_filled,
    }


def get_corp_code(stock_code: str) -> str | None:
    """종목코드로 corp_code 조회."""
    with get_session() as session:
        company = session.query(Company).filter_by(stock_code=stock_code).first()
        return company.corp_code if company else None


def get_all_listed(market_filter: list[str] | None = None) -> list[dict]:
    """상장사 목록 전체 반환 (dict 리스트)."""
    with get_session() as session:
        q = session.query(Company).filter(Company.stock_code.isnot(None))
        if market_filter:
            q = q.filter(Company.market.in_(market_filter))
        return [
            {"corp_code": c.corp_code, "stock_code": c.stock_code,
             "corp_name": c.corp_name, "market": c.market}
            for c in q.all()
        ]
