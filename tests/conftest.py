"""
공통 pytest fixture.
"""
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def temp_engine(monkeypatch):
    """Isolated in-memory DB for API/analysis tests."""
    import kreports.db.engine as engine_module
    from kreports.db.models import Base

    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=test_engine)
    new_session_maker = sessionmaker(bind=test_engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(engine_module, "engine", test_engine)
    monkeypatch.setattr(engine_module, "SessionLocal", new_session_maker)

    import kreports.analysis.api as api_module
    import kreports.analysis.peer as peer_module
    import kreports.analysis.readiness as readiness_module

    monkeypatch.setattr(api_module, "_engine", test_engine)
    monkeypatch.setattr(peer_module, "engine", test_engine)
    monkeypatch.setattr(readiness_module, "engine", test_engine)
    # Test fixtures seed database rows. Runtime-specific tests override this
    # explicitly to exercise the fail-closed public default.
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    monkeypatch.setenv("KREPORTS_ENABLE_RAW_BACKFILL", "1")
    monkeypatch.setenv("RAW_STORAGE_BACKEND", "file")
    monkeypatch.setenv("RAW_STORAGE_KEEP_INLINE", "false")
    return test_engine


# ---------------------------------------------------------------------------
# DART API 응답 fixture (실제 API 형식 기반)
# ---------------------------------------------------------------------------

@pytest.fixture
def dart_response_samsung_2024():
    """삼성전자 2024 사업보고서 fnlttSinglAcntAll 응답 fixture (핵심 계정 발췌)."""
    return {
        "status": "000",
        "message": "정상",
        "list": [
            # IS
            {"sj_div": "IS", "account_id": "ifrs-full_Revenue", "account_nm": "매출액",
             "thstrm_amount": "300,869,340,000,000", "frmtrm_amount": "258,935,494,000,000",
             "bfefrmtrm_amount": "302,231,360,000,000", "ord": "2"},
            {"sj_div": "IS", "account_id": "dart_OperatingIncomeLoss", "account_nm": "영업이익",
             "thstrm_amount": "32,724,893,000,000", "frmtrm_amount": "6,566,950,000,000",
             "bfefrmtrm_amount": "43,376,652,000,000", "ord": "3"},
            {"sj_div": "IS", "account_id": "ifrs-full_ProfitLoss", "account_nm": "당기순이익",
             "thstrm_amount": "34,468,800,000,000", "frmtrm_amount": "15,487,113,000,000",
             "bfefrmtrm_amount": "55,654,057,000,000", "ord": "4"},
            # BS
            {"sj_div": "BS", "account_id": "ifrs-full_Assets", "account_nm": "자산총계",
             "thstrm_amount": "571,164,152,000,000", "frmtrm_amount": "455,905,004,000,000",
             "ord": "1"},
            {"sj_div": "BS", "account_id": "ifrs-full_Liabilities", "account_nm": "부채총계",
             "thstrm_amount": "188,526,009,000,000", "frmtrm_amount": "159,651,920,000,000",
             "ord": "2"},
            {"sj_div": "BS", "account_id": "ifrs-full_Equity", "account_nm": "자본총계",
             "thstrm_amount": "382,638,143,000,000", "frmtrm_amount": "296,253,084,000,000",
             "ord": "3"},
        ],
    }


@pytest.fixture
def dart_response_alias_accounts():
    """비표준 계정명 별칭을 사용하는 기업 응답 fixture."""
    return {
        "status": "000",
        "message": "정상",
        "list": [
            {"sj_div": "IS", "account_id": "", "account_nm": "영업수익",
             "thstrm_amount": "1,000,000,000", "ord": "1"},
            {"sj_div": "IS", "account_id": "", "account_nm": "영업이익(손실)",
             "thstrm_amount": "100,000,000", "ord": "2"},
            {"sj_div": "IS", "account_id": "", "account_nm": "당기순이익(손실)",
             "thstrm_amount": "80,000,000", "ord": "3"},
            {"sj_div": "BS", "account_id": "", "account_nm": "자산 총계",
             "thstrm_amount": "5,000,000,000", "ord": "1"},
            {"sj_div": "BS", "account_id": "", "account_nm": "부채 총계",
             "thstrm_amount": "2,000,000,000", "ord": "2"},
            {"sj_div": "BS", "account_id": "", "account_nm": "자본합계",
             "thstrm_amount": "3,000,000,000", "ord": "3"},
        ],
    }


@pytest.fixture
def dart_response_no_data():
    """데이터 없음 응답 fixture."""
    return {"status": "013", "message": "조회된 데이터가 없습니다."}


@pytest.fixture
def dart_response_acnt_summary():
    """fnlttSinglAcnt (주요계정 요약) 응답 fixture — KOSDAQ 소형주 표준 케이스.

    account_id 없음 (요약 엔드포인트 특성). sj_div BS/IS만 사용.
    숫자는 로보티즈 2024 추정치 수준 (검증용 더미).
    """
    return {
        "status": "000",
        "message": "정상",
        "list": [
            # BS
            {"sj_div": "BS", "sj_nm": "재무상태표", "account_nm": "유동자산",
             "thstrm_amount": "80,000,000,000", "frmtrm_amount": "60,000,000,000", "ord": "1"},
            {"sj_div": "BS", "sj_nm": "재무상태표", "account_nm": "비유동자산",
             "thstrm_amount": "70,000,000,000", "frmtrm_amount": "55,000,000,000", "ord": "2"},
            {"sj_div": "BS", "sj_nm": "재무상태표", "account_nm": "자산총계",
             "thstrm_amount": "150,000,000,000", "frmtrm_amount": "115,000,000,000", "ord": "3"},
            {"sj_div": "BS", "sj_nm": "재무상태표", "account_nm": "유동부채",
             "thstrm_amount": "30,000,000,000", "ord": "4"},
            {"sj_div": "BS", "sj_nm": "재무상태표", "account_nm": "비유동부채",
             "thstrm_amount": "10,000,000,000", "ord": "5"},
            {"sj_div": "BS", "sj_nm": "재무상태표", "account_nm": "부채총계",
             "thstrm_amount": "40,000,000,000", "frmtrm_amount": "32,000,000,000", "ord": "6"},
            {"sj_div": "BS", "sj_nm": "재무상태표", "account_nm": "자본금",
             "thstrm_amount": "5,000,000,000", "ord": "7"},
            {"sj_div": "BS", "sj_nm": "재무상태표", "account_nm": "이익잉여금",
             "thstrm_amount": "50,000,000,000", "ord": "8"},
            {"sj_div": "BS", "sj_nm": "재무상태표", "account_nm": "자본총계",
             "thstrm_amount": "110,000,000,000", "frmtrm_amount": "83,000,000,000", "ord": "9"},
            # IS
            {"sj_div": "IS", "sj_nm": "손익계산서", "account_nm": "매출액",
             "thstrm_amount": "75,000,000,000", "frmtrm_amount": "60,000,000,000", "ord": "1"},
            {"sj_div": "IS", "sj_nm": "손익계산서", "account_nm": "영업이익",
             "thstrm_amount": "8,000,000,000", "frmtrm_amount": "5,000,000,000", "ord": "2"},
            {"sj_div": "IS", "sj_nm": "손익계산서", "account_nm": "법인세차감전순이익",
             "thstrm_amount": "7,500,000,000", "ord": "3"},
            {"sj_div": "IS", "sj_nm": "손익계산서", "account_nm": "당기순이익",
             "thstrm_amount": "6,000,000,000", "frmtrm_amount": "4,000,000,000", "ord": "4"},
        ],
    }


@pytest.fixture
def dart_response_acnt_partial():
    """일부 계정만 있는 acnt 응답 — 매핑 실패 케이스 (영업이익만)."""
    return {
        "status": "000",
        "message": "정상",
        "list": [
            {"sj_div": "IS", "sj_nm": "손익계산서", "account_nm": "영업이익",
             "thstrm_amount": "1,000,000,000", "ord": "1"},
        ],
    }


@pytest.fixture
def dart_response_cf():
    """현금흐름표 포함 응답 fixture."""
    return {
        "status": "000",
        "message": "정상",
        "list": [
            {"sj_div": "CF", "account_id": "ifrs-full_CashFlowsFromUsedInOperatingActivities",
             "account_nm": "영업활동현금흐름", "thstrm_amount": "72,982,512,000,000",
             "frmtrm_amount": "44,137,458,000,000", "ord": "1"},
        ],
    }
