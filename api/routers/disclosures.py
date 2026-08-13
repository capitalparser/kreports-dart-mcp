"""공시 목록 라우터."""
from typing import Optional
from fastapi import APIRouter, HTTPException, Query

from kreports.db.engine import get_session
from kreports.db.models import Company, Disclosure
from api.schemas import DisclosureOut

router = APIRouter(prefix="/disclosures", tags=["disclosures"])


def _company_or_404(session, stock_code: str) -> Company:
    row = session.query(Company).filter_by(stock_code=stock_code).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"종목코드 {stock_code}를 찾을 수 없습니다.")
    return row


@router.get("/{stock_code}", response_model=list[DisclosureOut])
def get_disclosures(
    stock_code: str,
    limit: int = Query(100, ge=1, le=1000),
    disc_type: Optional[str] = Query(None, description="A~J 공시 유형 필터"),
    keyword: Optional[str] = Query(None, description="공시명 키워드 검색"),
    year: Optional[int] = Query(None, description="특정 연도 필터"),
):
    """종목 공시 목록 조회."""
    with get_session() as session:
        company = _company_or_404(session, stock_code)
        q = (
            session.query(Disclosure)
            .filter_by(corp_code=company.corp_code)
            .order_by(Disclosure.disc_date.desc())
        )
        if disc_type:
            q = q.filter(Disclosure.disc_type == disc_type.upper())
        if keyword:
            q = q.filter(Disclosure.report_nm.ilike(f"%{keyword}%"))
        if year:
            from datetime import date
            q = q.filter(
                Disclosure.disc_date >= date(year, 1, 1),
                Disclosure.disc_date <= date(year, 12, 31),
            )
        rows = q.limit(limit).all()
        return [
            DisclosureOut(
                rcept_no=r.rcept_no,
                disc_date=str(r.disc_date),
                disc_type=r.disc_type,
                report_nm=r.report_nm,
                flr_nm=r.flr_nm,
            )
            for r in rows
        ]
