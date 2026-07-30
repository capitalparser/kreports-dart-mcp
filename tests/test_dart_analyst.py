"""
test_kreports.py — dart_analyst 공개 API 검증.

Phase 0 pure analytics 레이어 완결성 확인:
  - headless import (streamlit warning 없음)
  - search/get_company JSON-safe 반환
  - resolve_corp_code 3가지 식별자 형태 지원
  - get_financial_snapshot / score_going_concern / get_audit_history 실데이터
  - NaN/inf → None 변환
"""
import math

import pandas as pd
import pytest

import kreports
from kreports.analysis.api import _clean_value, _clean_dict, _df_to_records


# ---------------------------------------------------------------------------
# 1. 공개 API 노출 확인
# ---------------------------------------------------------------------------

class TestPublicAPI:
    def test_exported_symbols(self):
        expected = {
            "search_company", "get_company",
            "get_financial_snapshot", "score_going_concern",
            "detect_restatement", "get_accounting_policy",
            "get_audit_history", "get_subsidiary_auditors",
            "resolve_corp_code",
        }
        assert expected.issubset(set(kreports.__all__))

    def test_version_defined(self):
        assert isinstance(kreports.__version__, str)
        assert len(kreports.__version__) > 0


# ---------------------------------------------------------------------------
# 2. _clean_value JSON-safety
# ---------------------------------------------------------------------------

class TestCleanValue:
    def test_none_stays_none(self):
        assert _clean_value(None) is None

    def test_nan_becomes_none(self):
        assert _clean_value(float("nan")) is None

    def test_inf_becomes_none(self):
        assert _clean_value(float("inf")) is None
        assert _clean_value(float("-inf")) is None

    def test_int_passthrough(self):
        assert _clean_value(42) == 42

    def test_float_passthrough(self):
        assert _clean_value(3.14) == 3.14

    def test_pd_na_becomes_none(self):
        assert _clean_value(pd.NA) is None

    def test_timestamp_to_iso(self):
        ts = pd.Timestamp("2026-01-01")
        assert _clean_value(ts) == "2026-01-01T00:00:00"

    def test_numpy_int_unwrapped(self):
        import numpy as np
        assert _clean_value(np.int64(42)) == 42
        assert isinstance(_clean_value(np.int64(42)), int)

    def test_numpy_nan_becomes_none(self):
        import numpy as np
        assert _clean_value(np.float64("nan")) is None


class TestCleanDict:
    def test_nested_nan_cleaned(self):
        d = {
            "a": float("nan"),
            "b": {"c": float("inf")},
            "d": [1, float("nan"), 3],
        }
        result = _clean_dict(d)
        assert result["a"] is None
        assert result["b"]["c"] is None
        assert result["d"] == [1, None, 3]


class TestDfToRecords:
    def test_empty_df(self):
        assert _df_to_records(pd.DataFrame()) == []

    def test_none_input(self):
        assert _df_to_records(None) == []

    def test_basic_conversion(self):
        df = pd.DataFrame([{"a": 1, "b": 2.5}, {"a": 3, "b": 4.0}])
        result = _df_to_records(df)
        assert result == [{"a": 1, "b": 2.5}, {"a": 3, "b": 4.0}]

    def test_nan_cleaned(self):
        df = pd.DataFrame([{"a": 1, "b": float("nan")}])
        result = _df_to_records(df)
        assert result[0]["b"] is None


# ---------------------------------------------------------------------------
# 3. resolve_corp_code
# ---------------------------------------------------------------------------

class TestResolveCorpCode:
    def test_none_returns_none(self):
        assert kreports.resolve_corp_code(None) is None

    def test_empty_string_returns_none(self):
        # 공백 → search 후 빈 결과 → None
        assert kreports.resolve_corp_code("") is None

    def test_corp_code_passthrough(self):
        """삼성전자 corp_code 00126380이 DB에 있으면 그대로 반환."""
        result = kreports.resolve_corp_code("00126380")
        # Samsung이 DB에 없으면 None. 있으면 그대로.
        if result is not None:
            assert result == "00126380"

    def test_stock_code_resolution(self):
        """종목코드 005930 → corp_code 변환."""
        result = kreports.resolve_corp_code("005930")
        if result is not None:
            assert len(result) == 8 and result.isdigit()

    def test_invalid_corp_code_returns_none(self):
        """존재하지 않는 8자리 숫자."""
        assert kreports.resolve_corp_code("99999999") is None


# ---------------------------------------------------------------------------
# 4. 실데이터 통합 스모크 (삼성전자 DB 존재 시)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def samsung_corp_code():
    cc = kreports.resolve_corp_code("005930")
    if cc is None:
        pytest.skip("삼성전자 DB 미등록")
    return cc


class TestSamsungIntegration:
    def test_search_company_finds_samsung(self):
        results = kreports.search_company("삼성전자", limit=5)
        if not results:
            pytest.skip("삼성전자 DB 미등록")
        names = [r["corp_name"] for r in results]
        assert any("삼성전자" in n for n in names)

    def test_get_company_returns_induty_code_key(self, samsung_corp_code):
        result = kreports.get_company("005930")
        assert result is not None
        assert "induty_code" in result  # 값은 None일 수 있음 (enrich-market 미실행 시)

    def test_financial_snapshot_has_annual_rows(self, samsung_corp_code):
        snap = kreports.get_financial_snapshot(samsung_corp_code, years=3)
        assert snap["corp_code"] == samsung_corp_code
        assert snap["unit"] == "억원"
        assert snap["row_count"] >= 1
        assert len(snap["rows"]) == snap["row_count"]

    def test_financial_snapshot_json_serializable(self, samsung_corp_code):
        """반환값이 JSON 직렬화 가능해야 한다 (MCP 필수 요건)."""
        import json
        snap = kreports.get_financial_snapshot(samsung_corp_code, years=3)
        json.dumps(snap)  # raises if not serializable

    def test_going_concern_samsung_stable(self, samsung_corp_code):
        result = kreports.score_going_concern(samsung_corp_code)
        assert result["has_data"] is True
        assert 0 <= result["score"] <= 100
        assert result["grade"] in ("안정", "주의", "경고", "위험")
        assert result["risk"] in ("ok", "warn", "bad")
        # 삼성전자는 안정 등급이어야 함
        assert result["grade"] == "안정"
        # 6개 factor가 모두 있어야 함
        assert len(result["factors"]) == 6

    def test_going_concern_json_serializable(self, samsung_corp_code):
        import json
        result = kreports.score_going_concern(samsung_corp_code)
        json.dumps(result)

    def test_audit_history_has_entries(self, samsung_corp_code):
        hist = kreports.get_audit_history(samsung_corp_code)
        assert hist["corp_code"] == samsung_corp_code
        assert hist["count"] >= 1
        assert len(hist["history"]) == hist["count"]
        # 각 엔트리에 핵심 필드
        for row in hist["history"]:
            assert "year" in row
            assert "auditor_nm" in row
            assert "audit_opinion" in row

    def test_audit_history_json_serializable(self, samsung_corp_code):
        import json
        hist = kreports.get_audit_history(samsung_corp_code)
        json.dumps(hist)
