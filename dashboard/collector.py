"""대시보드 내 온디맨드 수집 헬퍼."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
from dart_platform.config import settings


def _check_api_key() -> bool:
    return bool(settings.dart_api_key)


def collect_financials(corp_code: str, stock_code: str) -> dict:
    from dart_platform.collector.fin_collector import collect_financial_range
    return collect_financial_range(stock_code)


def collect_disclosures(corp_code: str) -> dict:
    from dart_platform.collector.disc_collector import collect_disclosures as _cd
    return _cd(corp_code)


def collect_auditors(corp_code: str) -> dict:
    from dart_platform.collector.audit_collector import collect_auditors as _ca
    return _ca(corp_code)


def collect_all_for_company(corp_code: str, stock_code: str) -> None:
    """
    종목 선택 시 필요한 모든 데이터를 수집한다.
    Streamlit spinner 내에서 호출한다.
    """
    from dart_platform.db.engine import get_session
    from dart_platform.db.models import Financial, Disclosure, Auditor

    with get_session() as session:
        has_fin = session.query(Financial).filter_by(corp_code=corp_code).count() > 0
        has_disc = session.query(Disclosure).filter_by(corp_code=corp_code).count() > 0
        has_aud = session.query(Auditor).filter_by(corp_code=corp_code).count() > 0

    if not has_fin:
        st.write("재무 데이터 수집 중...")
        collect_financials(corp_code, stock_code)

    if not has_disc:
        st.write("공시 목록 수집 중...")
        collect_disclosures(corp_code)

    if not has_aud:
        st.write("감사인 이력 수집 중...")
        collect_auditors(corp_code)


def render_collect_button(corp_code: str, stock_code: str, label: str = "데이터 수집") -> bool:
    """
    수집 버튼을 렌더링하고, 수집이 완료되면 True를 반환한다.
    이미 데이터가 있으면 아무것도 하지 않고 False를 반환한다.
    """
    if not _check_api_key():
        st.error("DART_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요.")
        return False

    if st.button(f"🔄 {label}", type="primary", use_container_width=True):
        with st.spinner(f"{label} 진행 중... (재무 5개년 기준 약 20~30초 소요)"):
            collect_all_for_company(corp_code, stock_code)
        st.success("수집 완료!")
        st.rerun()
        return True
    return False
