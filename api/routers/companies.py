"""기업 검색 · 조회 라우터."""
from typing import Optional
from fastapi import APIRouter, HTTPException, Query

from kreports.db.engine import get_session
from kreports.db.models import Company
from api.schemas import CompanyOut

router = APIRouter(prefix="/companies", tags=["companies"])


@router.get("", response_model=list[CompanyOut])
def search_companies(
    q: str = Query("", description="회사명 또는 종목코드"),
    limit: int = Query(30, ge=1, le=200),
    market: Optional[str] = Query(None, description="KOSPI / KOSDAQ / KONEX"),
):
    """기업 목록 검색."""
    with get_session() as session:
        query = session.query(Company).filter(Company.stock_code.isnot(None))
        if q:
            query = query.filter(
                Company.corp_name.ilike(f"%{q}%") | (Company.stock_code == q.strip())
            )
        if market:
            query = query.filter(Company.market == market.upper())
        rows = query.order_by(Company.market, Company.corp_name).limit(limit).all()
        return [CompanyOut.model_validate(r) for r in rows]


@router.get("/{stock_code}", response_model=CompanyOut)
def get_company(stock_code: str):
    """종목코드로 기업 정보 조회."""
    with get_session() as session:
        row = session.query(Company).filter_by(stock_code=stock_code).first()
        if row is None:
            raise HTTPException(status_code=404, detail=f"종목코드 {stock_code}를 찾을 수 없습니다.")
        return CompanyOut.model_validate(row)
