"""
공통 pytest fixture.
"""
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


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
