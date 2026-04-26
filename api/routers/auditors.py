"""감사인 이력 · 감사보수 라우터."""
from fastapi import APIRouter, HTTPException

from kreports.db.engine import get_session
from kreports.db.models import Company, Auditor, AuditFee
from api.schemas import AuditorOut, AuditFeeOut

router = APIRouter(prefix="/auditors", tags=["auditors"])


def _company_or_404(session, stock_code: str) -> Company:
    row = session.query(Company).filter_by(stock_code=stock_code).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"종목코드 {stock_code}를 찾을 수 없습니다.")
    return row


@router.get("/{stock_code}", response_model=list[AuditorOut])
def get_auditors(stock_code: str):
    """감사인 이력 조회 (CFS/OFS 구분 포함)."""
    with get_session() as session:
        company = _company_or_404(session, stock_code)
        rows = (
            session.query(Auditor)
            .filter_by(corp_code=company.corp_code)
            .order_by(Auditor.fs_div, Auditor.bsns_year)
            .all()
        )
        return [AuditorOut.model_validate(r) for r in rows]


@router.get("/{stock_code}/fees", response_model=list[AuditFeeOut])
def get_audit_fees(stock_code: str):
    """DS002 감사보수 · 비감사보수 이력 조회."""
    with get_session() as session:
        company = _company_or_404(session, stock_code)
        rows = (
            session.query(AuditFee)
            .filter_by(corp_code=company.corp_code)
            .order_by(AuditFee.bsns_year)
            .all()
        )
        return [AuditFeeOut.model_validate(r) for r in rows]
