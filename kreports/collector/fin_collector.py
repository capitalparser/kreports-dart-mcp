"""
재무데이터 수집 모듈.

상장사 (KOSPI/KOSDAQ/KONEX):
  fnlttSinglAcntAll → 전체 계정 → FinancialFact 저장
  → FinancialFact에서 6개 요약 계산 → Financial 저장

비상장사 (감사보고서만 있는 기업):
  fnlttSinglAcntAll → 6개 요약만 → Financial 저장
"""
import logging
import time
from datetime import datetime

from sqlalchemy import case

from kreports.config import settings
from kreports.collector import fetcher as _fetcher
from kreports.collector.fetcher import (
    DartApiAuthError,
    DartApiLimitExceeded,
    DartBoundedStop,
    fetch_financial_statements,
    fetch_financial_summary,
)
from kreports.collector.corp_sync import get_corp_code
from kreports.db.engine import get_session
from kreports.db.models import Company, Financial, FinancialFact, FetchLog
from kreports.processor.fin_parser import (
    parse_all_accounts,
    compute_summary_from_facts,
    parse_financials,
    parse_summary_response,
    REPRT_TO_QUARTER,
)
from kreports.judge.flags import (
    compute_record_flags, compute_gap_flags,
    compute_trend_flags, compute_cf_flags,
)

logger = logging.getLogger(__name__)

DartRequestBudgetExceeded = _fetcher.DartRequestBudgetExceeded
DartTransportError = _fetcher.DartTransportError

QUARTER_TO_REPRT = {v: k for k, v in REPRT_TO_QUARTER.items()}

_LISTED_MARKETS = {"KOSPI", "KOSDAQ", "KONEX"}
_DART_NO_DATA_STATUS = "013"
_DART_LIMIT_MARKERS = ("사용한도", "초과", "limit", "quota")
_DART_AUTH_MARKERS = (
    "등록되지 않은 인증키",
    "인증키",
    "invalid api key",
    "invalid key",
    "authentication",
)


def _is_dart_limit_response(response: dict | None) -> bool:
    if not response:
        return False
    message = str(response.get("message") or "").lower()
    status = str(response.get("status") or "")
    return status != "000" and any(marker in message for marker in _DART_LIMIT_MARKERS)


def _is_dart_auth_response(response: dict | None) -> bool:
    if not response:
        return False
    message = str(response.get("message") or "").lower()
    status = str(response.get("status") or "")
    return status != "000" and any(marker in message for marker in _DART_AUTH_MARKERS)


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------

def collect_financial(
    stock_code: str,
    year: int,
    quarter: int,
    fs_div: str = "CFS",
) -> str:
    """단일 종목 단일 분기 재무데이터 수집."""
    corp_code = get_corp_code(stock_code)
    if not corp_code:
        logger.error("종목코드 %s의 corp_code를 찾을 수 없습니다.", stock_code)
        return "error"

    reprt_code = QUARTER_TO_REPRT.get(quarter)
    if not reprt_code:
        return "error"

    listed = _is_listed(corp_code)

    try:
        response = fetch_financial_statements(corp_code, year, reprt_code, fs_div)
        time.sleep(settings.request_delay)
    except DartBoundedStop:
        raise
    except Exception as e:
        _log_fetch(corp_code, year, quarter, "error", str(e))
        return "error"

    if _is_dart_limit_response(response):
        message = response.get("message") or "DART API limit exceeded"
        _log_fetch(corp_code, year, quarter, "error", message)
        raise DartApiLimitExceeded(str(message))
    if _is_dart_auth_response(response):
        message = response.get("message") or "DART API key rejected"
        _log_fetch(corp_code, year, quarter, "error", message)
        raise DartApiAuthError(str(message))

    # CFS 없으면 OFS 폴백
    if response.get("status") != "000" and fs_div == "CFS":
        try:
            response = fetch_financial_statements(corp_code, year, reprt_code, "OFS")
            fs_div = "OFS"
            time.sleep(settings.request_delay)
        except DartBoundedStop:
            raise
        except Exception as e:
            _log_fetch(corp_code, year, quarter, "error", str(e))
            return "error"

        if _is_dart_limit_response(response):
            message = response.get("message") or "DART API limit exceeded"
            _log_fetch(corp_code, year, quarter, "error", message)
            raise DartApiLimitExceeded(str(message))
        if _is_dart_auth_response(response):
            message = response.get("message") or "DART API key rejected"
            _log_fetch(corp_code, year, quarter, "error", message)
            raise DartApiAuthError(str(message))

    # acntall 양쪽 실패 시 acnt(주요계정 요약) 폴백 — KOSDAQ 소형주 갭 보완
    if response.get("status") != "000":
        if response.get("status") not in (_DART_NO_DATA_STATUS, None):
            _log_fetch(corp_code, year, quarter, "error", response.get("message"))
            return "error"
        status = _try_summary_fallback(corp_code, year, reprt_code, quarter)
        if status == "success":
            _log_fetch(corp_code, year, quarter, "success", None)
            return "success"
        if status == "error":
            _log_fetch(corp_code, year, quarter, "error", None)
            return "error"
        _log_fetch(corp_code, year, quarter, "no_data", None)
        return "no_data"

    quarter = REPRT_TO_QUARTER.get(reprt_code, 0)

    if listed:
        status = _process_listed(response, corp_code, year, reprt_code, fs_div, quarter)
    else:
        status = _process_unlisted(response, corp_code, year, reprt_code, fs_div, quarter)

    _log_fetch(corp_code, year, quarter, status, None)
    return status


def _try_summary_fallback(
    corp_code: str, year: int, reprt_code: str, quarter: int,
) -> str:
    """
    acntall(CFS/OFS) 모두 실패 시 acnt(주요계정) 엔드포인트 폴백.
    CFS → OFS 순으로 시도. 성공 시 Financial만 저장 (financial_facts 없음).
    """
    saw_error = False
    for fs_div in ("CFS", "OFS"):
        try:
            response = fetch_financial_summary(corp_code, year, reprt_code, fs_div)
            time.sleep(settings.request_delay)
        except DartBoundedStop:
            raise
        except Exception as e:
            logger.warning("acnt 폴백 실패 [%s %s %s]: %s", corp_code, year, fs_div, e)
            _log_fetch(corp_code, year, quarter, "error", str(e))
            saw_error = True
            continue

        if _is_dart_limit_response(response):
            message = response.get("message") or "DART API limit exceeded"
            raise DartApiLimitExceeded(str(message))
        if _is_dart_auth_response(response):
            message = response.get("message") or "DART API key rejected"
            raise DartApiAuthError(str(message))

        if response.get("status") not in ("000", _DART_NO_DATA_STATUS):
            logger.warning(
                "acnt 폴백 오류 [%s %s %s]: status=%s message=%s",
                corp_code, year, fs_div, response.get("status"), response.get("message"),
            )
            _log_fetch(
                corp_code,
                year,
                quarter,
                "error",
                response.get("message") or f"DART status {response.get('status')}",
            )
            saw_error = True
            continue

        parsed = parse_summary_response(response, corp_code, year, reprt_code, fs_div)
        if parsed is None:
            continue

        flags = compute_record_flags(parsed)
        _upsert_financial({**parsed, **flags})
        compute_gap_flags(corp_code, year, quarter)
        compute_trend_flags(corp_code, year, quarter, fs_div)
        logger.info(
            "acnt 폴백 성공 [%s %s Q%s %s]: confidence=%.2f",
            corp_code, year, quarter, fs_div, parsed.get("account_map_confidence") or 0.0,
        )
        return "success"

    return "error" if saw_error else "no_data"


def collect_financial_range(
    stock_code: str,
    year_from: int | None = None,
    year_to: int | None = None,
    *,
    force: bool = False,
) -> dict:
    """단일 종목의 연도 범위 전체 분기를 수집한다."""
    from datetime import date
    current_year = date.today().year

    if year_to is None:
        year_to = current_year
    if year_from is None:
        year_from = year_to - settings.collect_years + 1

    corp_code = get_corp_code(stock_code)
    if not corp_code:
        logger.error("종목코드 %s의 corp_code를 찾을 수 없습니다.", stock_code)
        return {"success": 0, "no_data": 0, "error": 1, "skipped": 0}

    counts: dict = {"success": 0, "no_data": 0, "error": 0, "skipped": 0}
    for year in range(year_from, year_to + 1):
        for quarter in [1, 2, 3, 4]:
            if year == current_year and quarter > _current_quarter():
                continue
            if not force and _financial_quarter_cached(corp_code, year, quarter):
                counts["skipped"] += 1
                continue
            status = collect_financial(stock_code, year, quarter)
            counts[status] = counts.get(status, 0) + 1

    return counts


def collect_all_companies(
    year_from: int | None = None,
    year_to: int | None = None,
    market: str | None = None,
    progress_callback=None,
    *,
    force: bool = False,
) -> dict:
    """DB 등록 상장사 재무데이터 배치 수집."""
    from datetime import date
    current_year = date.today().year

    if year_to is None:
        year_to = current_year
    if year_from is None:
        year_from = year_to - settings.collect_years + 1

    with get_session() as session:
        query = session.query(Company.stock_code, Company.corp_name).filter(
            Company.stock_code.isnot(None)
        )
        if market:
            query = query.filter(Company.market == market.upper())
        else:
            query = query.filter(Company.market.in_(_LISTED_MARKETS))
        market_priority = case(
            (Company.market == "KOSPI", 0),
            (Company.market == "KOSDAQ", 1),
            (Company.market == "KONEX", 2),
            else_=9,
        )
        companies = list(query.order_by(market_priority, Company.corp_name).all())

    total = len(companies)
    totals: dict = {"success": 0, "no_data": 0, "error": 0, "skipped": 0}

    for idx, (stock_code, corp_name) in enumerate(companies, 1):
        if progress_callback:
            progress_callback(idx, total, corp_name)
        result = collect_financial_range(stock_code, year_from, year_to, force=force)
        for k, v in result.items():
            totals[k] = totals.get(k, 0) + v

    return totals


# ---------------------------------------------------------------------------
# 상장사 처리 (FinancialFact + Financial)
# ---------------------------------------------------------------------------

def _process_listed(
    response: dict, corp_code: str, year: int, reprt_code: str, fs_div: str, quarter: int,
) -> str:
    """상장사: 전체 계정 → FinancialFact 저장 후 요약 → Financial 저장."""
    all_facts = parse_all_accounts(response, corp_code, year, reprt_code, fs_div)
    if not all_facts:
        return "no_data"

    _upsert_financial_facts(all_facts)
    logger.info(
        "FinancialFact [%s %s Q%s %s]: %d 계정",
        corp_code, year, quarter, fs_div, len(all_facts),
    )

    summary = compute_summary_from_facts(all_facts, corp_code, year, reprt_code, fs_div)
    summary["source"] = "acntall"
    flags = compute_record_flags(summary)
    _upsert_financial({**summary, **flags})

    compute_gap_flags(corp_code, year, quarter)
    compute_trend_flags(corp_code, year, quarter, fs_div)
    compute_cf_flags(corp_code, year, quarter, fs_div)
    if quarter == 4:
        from kreports.judge.beneish import compute_beneish
        compute_beneish(corp_code, year, fs_div)

    return "success"


# ---------------------------------------------------------------------------
# 비상장사 처리 (Financial만)
# ---------------------------------------------------------------------------

def _process_unlisted(
    response: dict, corp_code: str, year: int, reprt_code: str, fs_div: str, quarter: int,
) -> str:
    """비상장사: 6개 요약 → Financial만 저장."""
    parsed = parse_financials(response, corp_code, year, reprt_code, fs_div)
    if parsed is None:
        return "no_data"

    parsed["source"] = "acntall"
    flags = compute_record_flags(parsed)
    _upsert_financial({**parsed, **flags})
    compute_gap_flags(corp_code, year, quarter)
    compute_trend_flags(corp_code, year, quarter, fs_div)
    return "success"


# ---------------------------------------------------------------------------
# DB 헬퍼
# ---------------------------------------------------------------------------

def _is_listed(corp_code: str) -> bool:
    """
    상장사 여부 판별.
    market 필드가 있으면 시장 구분으로 확인,
    없으면(None) stock_code 보유 여부로 폴백 — corp_sync가 market을 채우지 않은 경우.
    """
    with get_session() as session:
        row = session.query(Company.market, Company.stock_code).filter_by(corp_code=corp_code).first()
        if row is None:
            return False
        market, stock_code = row
        if market:
            return market in _LISTED_MARKETS
        # market 미설정 시 stock_code 보유 = 상장사
        return stock_code is not None


def _financial_quarter_cached(corp_code: str, year: int, quarter: int) -> bool:
    """Return True when a prior terminal financial collection exists."""
    reprt_code = QUARTER_TO_REPRT.get(quarter)
    if not reprt_code:
        return False

    with get_session() as session:
        has_full_fact = (
            session.query(FinancialFact.id)
            .filter(
                FinancialFact.corp_code == corp_code,
                FinancialFact.bsns_year == year,
                FinancialFact.reprt_code == reprt_code,
            )
            .first()
            is not None
        )
        if has_full_fact:
            return True

        has_summary = (
            session.query(Financial.id)
            .filter(
                Financial.corp_code == corp_code,
                Financial.year == year,
                Financial.quarter == quarter,
                Financial.source.in_(("acntall", "acnt")),
            )
            .first()
            is not None
        )
        if has_summary:
            return True

        latest_outcome = (
            session.query(FetchLog.status)
            .filter(
                FetchLog.task_type == "financial",
                FetchLog.corp_code == corp_code,
                FetchLog.year == year,
                FetchLog.quarter == quarter,
            )
            .order_by(FetchLog.fetched_at.desc(), FetchLog.id.desc())
            .first()
        )
        if latest_outcome is None:
            return False
        return latest_outcome[0] == "no_data"


def _upsert_financial_facts(facts: list[dict]) -> None:
    from sqlalchemy import text
    sql = text("""
        INSERT INTO financial_facts
            (corp_code, bsns_year, reprt_code, fs_div, sj_div,
             account_id, account_nm, ord,
             thstrm_amount, frmtrm_amount, bfefrmtrm_amount, thstrm_add_amount,
             fetched_at)
        VALUES
            (:corp_code, :bsns_year, :reprt_code, :fs_div, :sj_div,
             :account_id, :account_nm, :ord,
             :thstrm_amount, :frmtrm_amount, :bfefrmtrm_amount, :thstrm_add_amount,
             :fetched_at)
        ON CONFLICT(corp_code, bsns_year, reprt_code, fs_div, sj_div, account_id)
        DO UPDATE SET
            account_nm       = excluded.account_nm,
            ord              = excluded.ord,
            thstrm_amount    = excluded.thstrm_amount,
            frmtrm_amount    = excluded.frmtrm_amount,
            bfefrmtrm_amount = excluded.bfefrmtrm_amount,
            thstrm_add_amount = excluded.thstrm_add_amount,
            fetched_at       = excluded.fetched_at
    """)
    now = datetime.utcnow().isoformat()
    with get_session() as session:
        session.execute(sql, [{"fetched_at": now, **f} for f in facts])


def _upsert_financial(data: dict) -> None:
    from sqlalchemy import text
    sql = text("""
        INSERT INTO financials
            (corp_code, year, quarter, fs_div,
             revenue, operating_profit, net_income,
             total_assets, total_debt, total_equity,
             account_map_confidence, cfs_ofs_gap_flag,
             accrual_ratio, op_cf_divergence_flag,
             op_net_divergence_flag, equity_negative_flag,
             going_concern_flag, revenue_yoy, revenue_vol_flag, amendment_count_annual,
             operating_cf,
             beneish_dsri, beneish_gmi, beneish_aqi, beneish_sgi,
             beneish_depi, beneish_sgai, beneish_lvgi, beneish_tata,
             beneish_m_score, beneish_flag,
             source,
             fetched_at)
        VALUES
            (:corp_code, :year, :quarter, :fs_div,
             :revenue, :operating_profit, :net_income,
             :total_assets, :total_debt, :total_equity,
             :account_map_confidence, :cfs_ofs_gap_flag,
             :accrual_ratio, :op_cf_divergence_flag,
             :op_net_divergence_flag, :equity_negative_flag,
             :going_concern_flag, :revenue_yoy, :revenue_vol_flag, :amendment_count_annual,
             :operating_cf,
             :beneish_dsri, :beneish_gmi, :beneish_aqi, :beneish_sgi,
             :beneish_depi, :beneish_sgai, :beneish_lvgi, :beneish_tata,
             :beneish_m_score, :beneish_flag,
             :source,
             :fetched_at)
        ON CONFLICT(corp_code, year, quarter, fs_div) DO UPDATE SET
            revenue                = excluded.revenue,
            operating_profit       = excluded.operating_profit,
            net_income             = excluded.net_income,
            total_assets           = excluded.total_assets,
            total_debt             = excluded.total_debt,
            total_equity           = excluded.total_equity,
            account_map_confidence = excluded.account_map_confidence,
            cfs_ofs_gap_flag       = excluded.cfs_ofs_gap_flag,
            accrual_ratio          = excluded.accrual_ratio,
            op_cf_divergence_flag  = excluded.op_cf_divergence_flag,
            op_net_divergence_flag = excluded.op_net_divergence_flag,
            equity_negative_flag   = excluded.equity_negative_flag,
            going_concern_flag     = excluded.going_concern_flag,
            revenue_yoy            = excluded.revenue_yoy,
            revenue_vol_flag       = excluded.revenue_vol_flag,
            amendment_count_annual = excluded.amendment_count_annual,
            operating_cf           = excluded.operating_cf,
            beneish_dsri           = excluded.beneish_dsri,
            beneish_gmi            = excluded.beneish_gmi,
            beneish_aqi            = excluded.beneish_aqi,
            beneish_sgi            = excluded.beneish_sgi,
            beneish_depi           = excluded.beneish_depi,
            beneish_sgai           = excluded.beneish_sgai,
            beneish_lvgi           = excluded.beneish_lvgi,
            beneish_tata           = excluded.beneish_tata,
            beneish_m_score        = excluded.beneish_m_score,
            beneish_flag           = excluded.beneish_flag,
            source                 = excluded.source,
            fetched_at             = excluded.fetched_at
    """)
    with get_session() as session:
        session.execute(sql, {
            "corp_code": data["corp_code"],
            "year": data["year"],
            "quarter": data["quarter"],
            "fs_div": data["fs_div"],
            "revenue": data.get("revenue"),
            "operating_profit": data.get("operating_profit"),
            "net_income": data.get("net_income"),
            "total_assets": data.get("total_assets"),
            "total_debt": data.get("total_debt"),
            "total_equity": data.get("total_equity"),
            "account_map_confidence": data.get("account_map_confidence"),
            "cfs_ofs_gap_flag": data.get("cfs_ofs_gap_flag"),
            "accrual_ratio": data.get("accrual_ratio"),
            "op_cf_divergence_flag": data.get("op_cf_divergence_flag"),
            "op_net_divergence_flag": data.get("op_net_divergence_flag"),
            "equity_negative_flag": data.get("equity_negative_flag"),
            "going_concern_flag": data.get("going_concern_flag"),
            "revenue_yoy": data.get("revenue_yoy"),
            "revenue_vol_flag": data.get("revenue_vol_flag"),
            "amendment_count_annual": data.get("amendment_count_annual"),
            "operating_cf": data.get("operating_cf"),
            "beneish_dsri": data.get("beneish_dsri"),
            "beneish_gmi": data.get("beneish_gmi"),
            "beneish_aqi": data.get("beneish_aqi"),
            "beneish_sgi": data.get("beneish_sgi"),
            "beneish_depi": data.get("beneish_depi"),
            "beneish_sgai": data.get("beneish_sgai"),
            "beneish_lvgi": data.get("beneish_lvgi"),
            "beneish_tata": data.get("beneish_tata"),
            "beneish_m_score": data.get("beneish_m_score"),
            "beneish_flag": data.get("beneish_flag"),
            "source": data.get("source"),
            "fetched_at": datetime.utcnow().isoformat(),
        })


def _current_quarter() -> int:
    from datetime import date
    return (date.today().month - 1) // 3 + 1


def _log_fetch(corp_code, year, quarter, status, error_msg):
    with get_session() as session:
        session.add(FetchLog(
            task_type="financial",
            corp_code=corp_code,
            year=year,
            quarter=quarter,
            status=status,
            error_msg=error_msg,
            fetched_at=datetime.utcnow(),
        ))
