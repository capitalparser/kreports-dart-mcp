"""재무 데이터 · 위험 신호 라우터."""
from typing import Optional
from fastapi import APIRouter, HTTPException, Query

from dart_platform.db.engine import get_session
from dart_platform.db.models import Company, Financial
from api.schemas import FinancialOut, RiskSummaryOut

router = APIRouter(prefix="/financials", tags=["financials"])

_UNIT = 1e8  # 억원


def _company_or_404(session, stock_code: str) -> Company:
    row = session.query(Company).filter_by(stock_code=stock_code).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"종목코드 {stock_code}를 찾을 수 없습니다.")
    return row


def _to_eok(value) -> Optional[float]:
    """원 → 억원 변환. None 허용."""
    if value is None:
        return None
    return round(value / _UNIT, 2)


def _build_out(r: Financial) -> FinancialOut:
    rev = _to_eok(r.revenue)
    op = _to_eok(r.operating_profit)
    net = _to_eok(r.net_income)
    assets = _to_eok(r.total_assets)
    debt = _to_eok(r.total_debt)
    equity = _to_eok(r.total_equity)

    op_margin = round(op / rev * 100, 2) if rev and op is not None else None
    net_margin = round(net / rev * 100, 2) if rev and net is not None else None
    debt_ratio = round(debt / equity * 100, 2) if equity and debt is not None else None
    roe = round(net / equity * 100, 2) if equity and net is not None else None
    roa = round(net / assets * 100, 2) if assets and net is not None else None

    return FinancialOut(
        year=r.year, quarter=r.quarter, fs_div=r.fs_div,
        revenue=rev, operating_profit=op, net_income=net,
        total_assets=assets, total_debt=debt, total_equity=equity,
        operating_cf=_to_eok(r.operating_cf),
        account_map_confidence=r.account_map_confidence,
        cfs_ofs_gap_flag=r.cfs_ofs_gap_flag,
        accrual_ratio=r.accrual_ratio,
        op_cf_divergence_flag=r.op_cf_divergence_flag,
        op_net_divergence_flag=r.op_net_divergence_flag,
        equity_negative_flag=r.equity_negative_flag,
        going_concern_flag=r.going_concern_flag,
        revenue_yoy=r.revenue_yoy,
        revenue_vol_flag=r.revenue_vol_flag,
        amendment_count_annual=r.amendment_count_annual,
        beneish_m_score=r.beneish_m_score,
        beneish_flag=r.beneish_flag,
        operating_margin=op_margin,
        net_margin=net_margin,
        debt_ratio=debt_ratio,
        roe=roe, roa=roa,
    )


@router.get("/{stock_code}", response_model=list[FinancialOut])
def get_financials(
    stock_code: str,
    fs_div: str = Query("CFS", description="CFS (연결) / OFS (별도)"),
    year_from: Optional[int] = Query(None),
    year_to: Optional[int] = Query(None),
    quarter: Optional[int] = Query(None, ge=1, le=4, description="1~4 (4=연간)"),
):
    """종목코드 기준 재무 데이터 조회. fs_div 없으면 CFS → OFS 자동 폴백."""
    with get_session() as session:
        company = _company_or_404(session, stock_code)
        corp_code = company.corp_code

        def _query(div: str):
            q = session.query(Financial).filter_by(corp_code=corp_code, fs_div=div)
            if year_from:
                q = q.filter(Financial.year >= year_from)
            if year_to:
                q = q.filter(Financial.year <= year_to)
            if quarter:
                q = q.filter(Financial.quarter == quarter)
            return q.order_by(Financial.year, Financial.quarter).all()

        rows = _query(fs_div.upper())
        if not rows and fs_div.upper() == "CFS":
            rows = _query("OFS")

        return [_build_out(r) for r in rows]


@router.get("/{stock_code}/risk", response_model=RiskSummaryOut)
def get_risk_summary(stock_code: str):
    """종목 위험 신호 요약 (판단 플래그 전체 집계)."""
    from dashboard.db import search_companies, get_risk_summary as _risk

    candidates = search_companies(stock_code, limit=1)
    if not candidates:
        raise HTTPException(status_code=404, detail=f"종목코드 {stock_code}를 찾을 수 없습니다.")
    corp_code = candidates[0]["corp_code"]
    summary = _risk(corp_code)
    return RiskSummaryOut(**summary)
