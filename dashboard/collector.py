"""대시보드 내 온디맨드 수집 헬퍼."""
import sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
from kreports.config import settings


def _check_api_key() -> bool:
    return bool(settings.dart_api_key)


def collect_financials(corp_code: str, stock_code: str) -> dict:
    from kreports.collector.fin_collector import collect_financial_range
    return collect_financial_range(stock_code)


def collect_disclosures(corp_code: str) -> dict:
    from kreports.collector.disc_collector import collect_disclosures as _cd
    return _cd(corp_code)


def collect_auditors(corp_code: str) -> dict:
    from kreports.collector.audit_collector import collect_auditors as _ca
    return _ca(corp_code)


def collect_audit_fees(corp_code: str) -> dict:
    from kreports.collector.audit_fee_collector import collect_audit_fees as _caf
    return _caf(corp_code)


def has_business_report(corp_code: str) -> bool:
    """Return whether we already have at least one business-report disclosure."""
    from kreports.db.engine import get_session
    from kreports.db.models import Disclosure

    with get_session() as session:
        return (
            session.query(Disclosure.rcept_no)
            .filter_by(corp_code=corp_code)
            .filter(Disclosure.report_nm.like("%사업보고서%"))
            .first()
            is not None
        )


def collect_business_reports(corp_code: str, years: int | None = None) -> dict:
    """
    Collect only regular disclosure metadata needed by the business overview page.

    This avoids the much heavier full-company collection path, which fetches
    quarterly financial statements, auditor history, and audit fees.
    """
    from kreports.collector.disc_collector import _upsert_disclosure
    from kreports.collector.fetcher import fetch_disclosure_list
    from kreports.processor.disc_parser import parse_disclosure

    end_date = date.today().strftime("%Y%m%d")
    lookback_years = years or (settings.collect_years + 1)
    start_y = date.today().year - lookback_years + 1
    start_date = f"{start_y}0101"

    try:
        items = fetch_disclosure_list(corp_code, start_date, end_date, disc_type="A")
    except Exception as e:
        return {"saved": 0, "skipped": 0, "error": 1, "message": str(e)}

    saved = skipped = 0
    for raw in items:
        if "사업보고서" not in (raw.get("report_nm") or ""):
            skipped += 1
            continue
        parsed = parse_disclosure(raw)
        if parsed is None or parsed["disc_date"] is None:
            skipped += 1
            continue
        _upsert_disclosure(parsed)
        saved += 1

    return {"saved": saved, "skipped": skipped, "error": 0}


def collect_all_for_company(corp_code: str, stock_code: str) -> None:
    """
    종목 선택 시 필요한 모든 데이터를 수집한다.
    Streamlit spinner 내에서 호출한다.
    """
    from kreports.db.engine import get_session
    from kreports.db.models import Financial, Disclosure, Auditor, AuditFee

    with get_session() as session:
        has_fin = session.query(Financial).filter_by(corp_code=corp_code).count() > 0
        has_disc = session.query(Disclosure).filter_by(corp_code=corp_code).count() > 0
        has_aud = session.query(Auditor).filter_by(corp_code=corp_code).count() > 0
        has_fee = session.query(AuditFee).filter_by(corp_code=corp_code).count() > 0

    if not has_fin:
        st.write("재무 데이터 수집 중...")
        collect_financials(corp_code, stock_code)

    if not has_disc:
        st.write("공시 목록 수집 중...")
        collect_disclosures(corp_code)

    if not has_aud:
        st.write("감사인 이력 수집 중...")
        collect_auditors(corp_code)

    if not has_fee:
        st.write("감사보수 / 감사시간 수집 중...")
        collect_audit_fees(corp_code)


def render_collect_button(corp_code: str, stock_code: str, label: str = "데이터 수집") -> bool:
    """
    수집 버튼을 렌더링하고, 수집이 완료되면 True를 반환한다.
    이미 데이터가 있으면 아무것도 하지 않고 False를 반환한다.
    """
    if not _check_api_key():
        st.error("DART_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요.")
        return False

    if st.button(f"🔄 {label}", type="primary", use_container_width=True):
        with st.spinner(f"{label} 진행 중... (전체 수집은 수 분 걸릴 수 있습니다)"):
            collect_all_for_company(corp_code, stock_code)
        st.success("수집 완료!")
        st.rerun()
        return True
    return False


def render_business_report_collect_button(
    corp_code: str,
    label: str = "사업보고서 빠른 수집",
) -> bool:
    """Render a lightweight collect button for business-report pages."""
    if not _check_api_key():
        st.error("DART_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요.")
        return False

    if st.button(f"🔄 {label}", type="primary", use_container_width=True):
        with st.spinner(f"{label} 진행 중... (공시 목록만 수집합니다)"):
            result = collect_business_reports(corp_code)
        if result.get("error"):
            st.error(f"수집 실패: {result.get('message', '알 수 없는 오류')}")
            return False
        if result.get("saved", 0) == 0:
            st.warning("사업보고서를 찾지 못했습니다. DART 공시 여부를 확인하세요.")
            return False
        st.success(f"사업보고서 {result['saved']}건 확인 완료")
        st.rerun()
        return True
    return False
