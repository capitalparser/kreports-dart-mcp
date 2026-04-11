"""
회계 판단 플래그 계산.

compute_record_flags(): 단일 파싱 결과에서 즉시 계산 가능한 플래그
compute_gap_flags():    DB 내 CFS/OFS 비교가 필요한 플래그 (수집 후 별도 실행)
compute_trend_flags():  전기 비교 및 공시 카운트 플래그 (수집 후 별도 실행)
compute_cf_flags():     영업CF 추출 및 발생주의 지표 (FinancialFact → Financial)
"""
import logging
from datetime import date

from kreports.db.engine import get_session
from kreports.db.models import Financial

logger = logging.getLogger(__name__)

_CORE_ACCOUNTS = [
    "revenue", "operating_profit", "net_income",
    "total_assets", "total_debt", "total_equity",
]

# 영업활동현금흐름 XBRL 계정 ID (우선순위 순)
_CF_XBRL_IDS = [
    "ifrs-full_CashFlowsFromUsedInOperatingActivities",
    "dart_OperatingActivitiesCashFlow",
    "ifrs-full_CashFlowsFromOperatingActivities",
]
_CF_ACCOUNT_NMS = [
    "영업활동현금흐름",
    "영업활동으로 인한 현금흐름",
    "영업활동으로인한현금흐름",
    "영업활동으로 인한 현금유입액(유출액)",
]


def compute_record_flags(parsed: dict) -> dict:
    """
    parse_financials() / compute_summary_from_facts() 결과에서
    단일 레코드 기준으로 즉시 계산 가능한 플래그를 반환한다.
    """
    mapped = sum(1 for k in _CORE_ACCOUNTS if parsed.get(k) is not None)
    confidence = round(mapped / len(_CORE_ACCOUNTS), 3)

    op = parsed.get("operating_profit")
    net = parsed.get("net_income")
    equity = parsed.get("total_equity")

    op_net_divergence = None
    if op is not None and net is not None:
        op_net_divergence = bool(op > 0 and net < 0)

    equity_negative = None
    if equity is not None:
        equity_negative = bool(equity < 0)

    return {
        "account_map_confidence": confidence,
        "cfs_ofs_gap_flag": None,
        "accrual_ratio": None,
        "op_cf_divergence_flag": None,
        "op_net_divergence_flag": op_net_divergence,
        "equity_negative_flag": equity_negative,
        "going_concern_flag": None,
        "revenue_yoy": None,
        "revenue_vol_flag": None,
        "amendment_count_annual": None,
        "operating_cf": None,
        "beneish_dsri": None,
        "beneish_gmi": None,
        "beneish_aqi": None,
        "beneish_sgi": None,
        "beneish_depi": None,
        "beneish_sgai": None,
        "beneish_lvgi": None,
        "beneish_tata": None,
        "beneish_m_score": None,
        "beneish_flag": None,
    }


def compute_gap_flags(corp_code: str, year: int, quarter: int) -> None:
    """
    DB에서 동일 기업·기간의 CFS/OFS 순이익을 비교하여 cfs_ofs_gap_flag를 갱신한다.
    괴리율 = |CFS순이익 - OFS순이익| / |OFS순이익| > 20% 이면 True.
    """
    with get_session() as session:
        rows = (
            session.query(Financial)
            .filter_by(corp_code=corp_code, year=year, quarter=quarter)
            .all()
        )
        by_div = {r.fs_div: r for r in rows}
        cfs = by_div.get("CFS")
        ofs = by_div.get("OFS")

        if cfs is None or ofs is None:
            return
        if cfs.net_income is None or ofs.net_income is None or ofs.net_income == 0:
            return

        gap_ratio = abs(cfs.net_income - ofs.net_income) / abs(ofs.net_income)
        flag = gap_ratio > 0.20

        for row in [cfs, ofs]:
            row.cfs_ofs_gap_flag = flag


def compute_all_gap_flags(corp_code: str) -> int:
    """corp_code 전체 기간의 gap flag를 일괄 계산한다. 갱신된 레코드 수를 반환."""
    with get_session() as session:
        periods = (
            session.query(Financial.year, Financial.quarter)
            .filter_by(corp_code=corp_code)
            .distinct()
            .all()
        )

    updated = 0
    for year, quarter in periods:
        compute_gap_flags(corp_code, year, quarter)
        updated += 1

    return updated


def compute_trend_flags(corp_code: str, year: int, quarter: int, fs_div: str) -> None:
    """
    전기 비교가 필요한 트렌드 플래그와 수정공시 카운트를 갱신한다.

    - going_concern_flag : 영업이익 < 0 AND 순이익 < 0
    - revenue_yoy        : 전년 동분기 대비 매출 성장률
    - revenue_vol_flag   : |revenue_yoy| ≥ 30%
    - amendment_count_annual : 해당연도 정정공시 건수
    """
    from kreports.db.models import Disclosure
    from sqlalchemy import func

    with get_session() as session:
        curr = session.query(Financial).filter_by(
            corp_code=corp_code, year=year, quarter=quarter, fs_div=fs_div,
        ).first()
        if curr is None:
            return

        # going_concern: 영업이익 < 0 AND 순이익 < 0
        going_concern = None
        if curr.operating_profit is not None and curr.net_income is not None:
            going_concern = bool(curr.operating_profit < 0 and curr.net_income < 0)
        curr.going_concern_flag = going_concern

        # revenue_yoy: 전년 동분기 대비
        prev = session.query(Financial).filter_by(
            corp_code=corp_code, year=year - 1, quarter=quarter, fs_div=fs_div,
        ).first()
        if prev and prev.revenue and curr.revenue is not None and prev.revenue != 0:
            yoy = round((curr.revenue - prev.revenue) / abs(prev.revenue), 4)
            curr.revenue_yoy = yoy
            curr.revenue_vol_flag = bool(abs(yoy) >= 0.30)
        else:
            curr.revenue_yoy = None
            curr.revenue_vol_flag = None

        # amendment_count: 해당연도 정정공시 수
        year_start = date(year, 1, 1)
        year_end = date(year, 12, 31)
        amendment_count = session.query(func.count(Disclosure.rcept_no)).filter(
            Disclosure.corp_code == corp_code,
            Disclosure.disc_date.between(year_start, year_end),
            Disclosure.report_nm.like("%정정%"),
        ).scalar() or 0
        curr.amendment_count_annual = amendment_count


def compute_cf_flags(corp_code: str, year: int, quarter: int, fs_div: str) -> None:
    """
    FinancialFact CF 데이터에서 영업활동현금흐름을 추출하고
    operating_cf, accrual_ratio, op_cf_divergence_flag를 갱신한다.
    """
    from kreports.db.models import FinancialFact
    from kreports.processor.fin_parser import REPRT_TO_QUARTER

    quarter_to_reprt = {v: k for k, v in REPRT_TO_QUARTER.items()}
    reprt_code = quarter_to_reprt.get(quarter)
    if not reprt_code:
        return

    with get_session() as session:
        cf_facts = session.query(FinancialFact).filter_by(
            corp_code=corp_code,
            bsns_year=year,
            reprt_code=reprt_code,
            fs_div=fs_div,
            sj_div="CF",
        ).all()

        operating_cf = None
        for fact in cf_facts:
            if fact.account_id in _CF_XBRL_IDS or fact.account_nm.strip() in _CF_ACCOUNT_NMS:
                operating_cf = fact.thstrm_amount
                break

        if operating_cf is None:
            return

        fin = session.query(Financial).filter_by(
            corp_code=corp_code, year=year, quarter=quarter, fs_div=fs_div,
        ).first()
        if fin is None:
            return

        fin.operating_cf = operating_cf

        if fin.net_income and fin.net_income != 0:
            fin.accrual_ratio = round((fin.net_income - operating_cf) / abs(fin.net_income), 4)

        if fin.operating_profit is not None:
            fin.op_cf_divergence_flag = bool(fin.operating_profit > 0 and operating_cf < 0)


def compute_all_trend_cf_flags(corp_code: str) -> int:
    """corp_code 전체 기간의 trend + CF 플래그를 일괄 계산한다."""
    with get_session() as session:
        periods = (
            session.query(Financial.year, Financial.quarter, Financial.fs_div)
            .filter_by(corp_code=corp_code)
            .distinct()
            .all()
        )

    for year, quarter, fs_div in periods:
        compute_trend_flags(corp_code, year, quarter, fs_div)
        compute_cf_flags(corp_code, year, quarter, fs_div)

    return len(periods)
