"""
test_fin_collector_fallback.py — acntall 실패 시 acnt 폴백 체인 검증.

목표:
- acntall CFS/OFS 모두 status≠000 → acnt CFS → 성공
- acnt CFS도 실패 → acnt OFS → 성공
- 전부 실패 → no_data
- 폴백 성공 시 Financial.source='acnt' 기록
- 폴백 경로는 financial_facts에 행을 추가하지 않음
"""
import pytest
from unittest.mock import patch
import os

from kreports.collector import fin_collector
from kreports.db.engine import init_db, get_session, engine
from kreports.db.models import Base, Company, Financial, FinancialFact


CORP_CODE = "00946030"   # 로보티즈
STOCK_CODE = "108490"
YEAR = 2024
QUARTER = 4   # 사업보고서

NO_DATA = {"status": "013", "message": "조회된 데이터가 없습니다."}


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """테스트마다 격리된 in-memory(파일) SQLite를 사용한다."""
    db_path = tmp_path / "test_fallback.db"
    original_db_url = os.environ.get("DB_URL")
    monkeypatch.setenv("DB_URL", f"sqlite:///{db_path}")

    # engine 모듈 재초기화: settings 캐시 우회 + 새 engine 바인딩
    from importlib import reload
    from kreports import config as _cfg
    reload(_cfg)
    from kreports.db import engine as _eng
    reload(_eng)
    # 테이블 생성
    _eng.init_db()

    # 모듈 레벨 의존 재바인딩
    from kreports.collector import fin_collector as _fc
    reload(_fc)
    from kreports.collector import corp_sync as _cs
    reload(_cs)

    # 회사 레코드 시드
    with _eng.get_session() as session:
        session.add(Company(
            corp_code=CORP_CODE,
            stock_code=STOCK_CODE,
            corp_name="로보티즈",
            market="KOSDAQ",
            induty_code="29299",
        ))

    yield _eng, _fc, _cs

    if original_db_url is None:
        os.environ.pop("DB_URL", None)
    else:
        os.environ["DB_URL"] = original_db_url
    reload(_cfg)
    reload(_eng)
    import kreports.analysis.api as _api
    import kreports.analysis.peer as _peer
    import kreports.analysis.readiness as _readiness
    _api._engine = _eng.engine
    _peer.engine = _eng.engine
    _readiness.engine = _eng.engine


def _make_acntall_response_full():
    """acntall 정상 응답 (6개 요약 추출 가능)."""
    return {
        "status": "000",
        "list": [
            {"sj_div": "IS", "account_id": "ifrs-full_Revenue", "account_nm": "매출액",
             "thstrm_amount": "75,000,000,000", "ord": "1"},
            {"sj_div": "IS", "account_id": "dart_OperatingIncomeLoss", "account_nm": "영업이익",
             "thstrm_amount": "8,000,000,000", "ord": "2"},
            {"sj_div": "IS", "account_id": "ifrs-full_ProfitLoss", "account_nm": "당기순이익",
             "thstrm_amount": "6,000,000,000", "ord": "3"},
            {"sj_div": "BS", "account_id": "ifrs-full_Assets", "account_nm": "자산총계",
             "thstrm_amount": "150,000,000,000", "ord": "1"},
            {"sj_div": "BS", "account_id": "ifrs-full_Liabilities", "account_nm": "부채총계",
             "thstrm_amount": "40,000,000,000", "ord": "2"},
            {"sj_div": "BS", "account_id": "ifrs-full_Equity", "account_nm": "자본총계",
             "thstrm_amount": "110,000,000,000", "ord": "3"},
        ],
    }


def _make_acnt_summary_response():
    """fnlttSinglAcnt 정상 응답 (account_id 없음)."""
    return {
        "status": "000",
        "list": [
            {"sj_div": "BS", "account_nm": "자산총계",
             "thstrm_amount": "150,000,000,000", "ord": "1"},
            {"sj_div": "BS", "account_nm": "부채총계",
             "thstrm_amount": "40,000,000,000", "ord": "2"},
            {"sj_div": "BS", "account_nm": "자본총계",
             "thstrm_amount": "110,000,000,000", "ord": "3"},
            {"sj_div": "IS", "account_nm": "매출액",
             "thstrm_amount": "75,000,000,000", "ord": "1"},
            {"sj_div": "IS", "account_nm": "영업이익",
             "thstrm_amount": "8,000,000,000", "ord": "2"},
            {"sj_div": "IS", "account_nm": "당기순이익",
             "thstrm_amount": "6,000,000,000", "ord": "3"},
        ],
    }


class TestSummaryFallbackChain:
    def test_acntall_cfs_success_no_fallback(self, fresh_db):
        eng, fc, _ = fresh_db
        with patch.object(fc, "fetch_financial_statements",
                          return_value=_make_acntall_response_full()) as m_all, \
             patch.object(fc, "fetch_financial_summary") as m_acnt, \
             patch("kreports.config.settings.request_delay", 0):
            status = fc.collect_financial(STOCK_CODE, YEAR, QUARTER)
        assert status == "success"
        assert m_acnt.call_count == 0   # 폴백 호출 안 됨

        with eng.get_session() as s:
            row = s.query(Financial).filter_by(corp_code=CORP_CODE).first()
            assert row is not None
            assert row.source == "acntall"

    def test_acntall_both_fail_acnt_cfs_success(self, fresh_db):
        eng, fc, _ = fresh_db
        with patch.object(fc, "fetch_financial_statements", return_value=NO_DATA), \
             patch.object(fc, "fetch_financial_summary",
                          return_value=_make_acnt_summary_response()) as m_acnt, \
             patch("kreports.config.settings.request_delay", 0):
            status = fc.collect_financial(STOCK_CODE, YEAR, QUARTER)

        assert status == "success"
        # acnt CFS 1회 호출 후 성공이므로 OFS는 호출되지 않음
        assert m_acnt.call_count == 1
        first_call_fs_div = m_acnt.call_args_list[0].args[3]
        assert first_call_fs_div == "CFS"

        with eng.get_session() as s:
            row = s.query(Financial).filter_by(corp_code=CORP_CODE).first()
            assert row is not None
            assert row.source == "acnt"
            assert row.revenue == 75_000_000_000
            assert row.fs_div == "CFS"
            # 폴백 경로는 financial_facts에 행을 추가하지 않는다
            fact_count = s.query(FinancialFact).filter_by(corp_code=CORP_CODE).count()
            assert fact_count == 0

    def test_acnt_cfs_fail_ofs_success(self, fresh_db):
        eng, fc, _ = fresh_db
        # acntall: 둘 다 no_data / acnt: CFS no_data → OFS success
        acnt_responses = iter([NO_DATA, _make_acnt_summary_response()])
        with patch.object(fc, "fetch_financial_statements", return_value=NO_DATA), \
             patch.object(fc, "fetch_financial_summary",
                          side_effect=lambda *a, **kw: next(acnt_responses)), \
             patch("kreports.config.settings.request_delay", 0):
            status = fc.collect_financial(STOCK_CODE, YEAR, QUARTER)

        assert status == "success"
        with eng.get_session() as s:
            row = s.query(Financial).filter_by(corp_code=CORP_CODE).first()
            assert row.source == "acnt"
            assert row.fs_div == "OFS"

    def test_all_fail_returns_no_data(self, fresh_db):
        eng, fc, _ = fresh_db
        with patch.object(fc, "fetch_financial_statements", return_value=NO_DATA), \
             patch.object(fc, "fetch_financial_summary", return_value=NO_DATA), \
             patch("kreports.config.settings.request_delay", 0):
            status = fc.collect_financial(STOCK_CODE, YEAR, QUARTER)

        assert status == "no_data"
        with eng.get_session() as s:
            assert s.query(Financial).filter_by(corp_code=CORP_CODE).count() == 0

    def test_acnt_exception_falls_through(self, fresh_db):
        """폴백 중 예외 발생해도 다음 fs_div를 시도해야 한다 (계속성)."""
        eng, fc, _ = fresh_db
        acnt_calls = iter([RuntimeError("boom"), _make_acnt_summary_response()])

        def acnt_side_effect(*args, **kwargs):
            item = next(acnt_calls)
            if isinstance(item, Exception):
                raise item
            return item

        with patch.object(fc, "fetch_financial_statements", return_value=NO_DATA), \
             patch.object(fc, "fetch_financial_summary", side_effect=acnt_side_effect), \
             patch("kreports.config.settings.request_delay", 0):
            status = fc.collect_financial(STOCK_CODE, YEAR, QUARTER)

        assert status == "success"
        with eng.get_session() as s:
            row = s.query(Financial).filter_by(corp_code=CORP_CODE).first()
            assert row.source == "acnt"
            assert row.fs_div == "OFS"  # CFS 예외 → OFS 성공
