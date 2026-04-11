"""
test_industry_aggregates.py — 업종 벤치마킹 함수 및 MCP 도구 검증.

세 계층:
  1. 수학 유틸 (_quantile)
  2. kreports.get_industry_aggregates (함수 레이어)
  3. compare_to_industry MCP 도구 (handler + Tool schema)
"""
from __future__ import annotations

import json

import pytest

import kreports
from kreports.analysis.api import _quantile
from kreports.mcp.tools import ALL_TOOLS, HANDLERS, call_tool


# ---------------------------------------------------------------------------
# 1. _quantile 수학 유틸
# ---------------------------------------------------------------------------

class TestQuantile:
    def test_empty_returns_none(self):
        assert _quantile([], 0.5) is None

    def test_single_value(self):
        assert _quantile([42.0], 0.5) == 42.0
        assert _quantile([42.0], 0.25) == 42.0

    def test_two_values_p50(self):
        # linear interp: (10+20)/2 = 15
        assert _quantile([10.0, 20.0], 0.5) == 15.0

    def test_exact_quantiles_5_values(self):
        vals = [10.0, 20.0, 30.0, 40.0, 50.0]
        # P25 = index (5-1)*0.25 = 1 → 20
        assert _quantile(vals, 0.25) == 20.0
        # P50 = index 2 → 30
        assert _quantile(vals, 0.5) == 30.0
        # P75 = index 3 → 40
        assert _quantile(vals, 0.75) == 40.0

    def test_interpolation(self):
        vals = [10.0, 20.0, 30.0, 40.0]
        # P50: (4-1)*0.5 = 1.5 → (20+30)/2 = 25
        assert _quantile(vals, 0.5) == 25.0

    def test_unsorted_input_handled(self):
        assert _quantile([50.0, 10.0, 30.0, 20.0, 40.0], 0.5) == 30.0


# ---------------------------------------------------------------------------
# 2. get_industry_aggregates 함수
# ---------------------------------------------------------------------------

class TestGetIndustryAggregatesInput:
    def test_unsupported_metric_returns_error(self):
        r = kreports.get_industry_aggregates("264", metric="XYZ")
        assert "error" in r
        assert "지원하지 않는" in r["error"]

    def test_empty_induty_code_returns_error(self):
        r = kreports.get_industry_aggregates("", metric="영업이익률")
        assert "error" in r

    def test_invalid_prefix_len_returns_error(self):
        r = kreports.get_industry_aggregates("264", prefix_len=0)
        assert "error" in r
        r2 = kreports.get_industry_aggregates("264", prefix_len=6)
        assert "error" in r2

    def test_unknown_industry_returns_empty(self):
        """존재하지 않는 업종 → n=0, quantiles=None, note 포함."""
        r = kreports.get_industry_aggregates("99999", metric="영업이익률")
        assert r["n"] == 0
        assert r["quantiles"] is None
        assert "note" in r
        assert r["peers"] == []


class TestGetIndustryAggregatesSchema:
    """반환 dict의 스키마 검증 (실데이터 무관)."""

    def test_expected_keys_present(self):
        r = kreports.get_industry_aggregates("264", metric="영업이익률")
        expected = {
            "induty_code", "match_prefix", "prefix_len",
            "metric", "unit", "year", "fs_div",
            "n", "quantiles", "peers", "peer_limit",
            "truncated", "note",
        }
        assert expected.issubset(set(r.keys()))

    def test_match_prefix_respects_prefix_len(self):
        r = kreports.get_industry_aggregates(
            "26199", metric="영업이익률", prefix_len=3
        )
        assert r["match_prefix"] == "261"

    def test_unit_for_percentage_metrics(self):
        for m in ("영업이익률", "순이익률", "부채비율", "ROE", "ROA"):
            r = kreports.get_industry_aggregates("264", metric=m)
            assert r["unit"] == "%"

    def test_json_serializable(self):
        r = kreports.get_industry_aggregates("264", metric="영업이익률")
        # 모든 값이 JSON 직렬화 가능해야 함
        json.dumps(r, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 3. 실데이터 (삼성전자 / SK하이닉스 prefix 26)
# ---------------------------------------------------------------------------

def _samsung_available() -> bool:
    cc = kreports.resolve_corp_code("005930")
    return cc is not None


class TestRealDataSemiconductor:
    """
    prefix=26에 삼성전자·SK하이닉스가 있어야 하므로 둘 다 DB에 존재해야 한다.
    없으면 skip.
    """

    def test_samsung_found_in_prefix_26(self):
        if not _samsung_available():
            pytest.skip("Samsung DB 미등록")
        r = kreports.get_industry_aggregates(
            "264", metric="영업이익률", prefix_len=2
        )
        if r["n"] == 0:
            pytest.skip("반도체 업종 재무데이터 없음")
        peer_names = {p["corp_name"] for p in r["peers"]}
        assert "삼성전자" in peer_names

    def test_peers_sorted_descending(self):
        if not _samsung_available():
            pytest.skip("Samsung DB 미등록")
        r = kreports.get_industry_aggregates(
            "264", metric="영업이익률", prefix_len=2
        )
        if r["n"] < 2:
            pytest.skip("peer 2개 미만")
        values = [p["value"] for p in r["peers"]]
        assert values == sorted(values, reverse=True)

    def test_quantiles_min_max_correct(self):
        if not _samsung_available():
            pytest.skip("Samsung DB 미등록")
        r = kreports.get_industry_aggregates(
            "264", metric="영업이익률", prefix_len=2
        )
        if r["n"] == 0:
            pytest.skip("데이터 없음")
        values = [p["value"] for p in r["peers"]]
        assert r["quantiles"]["min"] == min(values)
        assert r["quantiles"]["max"] == max(values)

    def test_p25_p75_null_when_sparse(self):
        """n<3이면 P25/P75는 null."""
        if not _samsung_available():
            pytest.skip("Samsung DB 미등록")
        r = kreports.get_industry_aggregates(
            "264", metric="영업이익률", prefix_len=2
        )
        if r["n"] >= 3:
            pytest.skip("peer 3개 이상 (희소성 분기 미적용)")
        if r["n"] == 0:
            pytest.skip("데이터 없음")
        assert r["quantiles"]["p25"] is None
        assert r["quantiles"]["p75"] is None
        assert r["quantiles"]["p50"] is not None


# ---------------------------------------------------------------------------
# 4. compare_to_industry MCP 도구
# ---------------------------------------------------------------------------

class TestCompareToIndustryTool:
    def test_tool_registered(self):
        names = {t.name for t in ALL_TOOLS}
        assert "compare_to_industry" in names

    def test_handler_registered(self):
        assert "compare_to_industry" in HANDLERS

    def test_missing_both_company_and_induty_code(self):
        result = json.loads(call_tool("compare_to_industry", {}))
        assert "error" in result

    def test_nonexistent_company(self):
        result = json.loads(call_tool(
            "compare_to_industry",
            {"company": "절대없는기업ABCXYZ"},
        ))
        assert "error" in result

    def test_company_with_induty_code(self):
        if not _samsung_available():
            pytest.skip("Samsung DB 미등록")
        result = json.loads(call_tool(
            "compare_to_industry",
            {"company": "삼성전자", "metric": "영업이익률"},
        ))
        # subject 필드 존재
        assert "subject" in result
        assert result["subject"]["corp_name"] == "삼성전자"
        assert result["subject"]["found_in_peers"] is True

    def test_induty_code_direct(self):
        """company 없이 induty_code 직접 지정."""
        result = json.loads(call_tool(
            "compare_to_industry",
            {"induty_code": "264", "metric": "ROE"},
        ))
        # subject 없이 순수 집계
        assert "subject" not in result or result.get("subject") is None
        assert "quantiles" in result

    def test_percentile_calculation(self):
        """subject의 percentile이 올바르게 계산되는지."""
        if not _samsung_available():
            pytest.skip("Samsung DB 미등록")
        result = json.loads(call_tool(
            "compare_to_industry",
            {"company": "삼성전자", "metric": "영업이익률"},
        ))
        if result["n"] < 2:
            pytest.skip("peer 1개 이하")
        pct = result["subject"].get("percentile")
        # percentile은 0~100 사이 float
        assert pct is not None
        assert 0 <= pct <= 100

    def test_prefix_len_3(self):
        """prefix_len=3 지정 시 match_prefix 3자리."""
        if not _samsung_available():
            pytest.skip("Samsung DB 미등록")
        result = json.loads(call_tool(
            "compare_to_industry",
            {"company": "삼성전자", "metric": "영업이익률", "prefix_len": 3},
        ))
        # Samsung induty_code="264" → prefix 3자리 = "264"
        assert result["match_prefix"] == "264"

    def test_response_json_serializable(self):
        if not _samsung_available():
            pytest.skip("Samsung DB 미등록")
        resp = call_tool("compare_to_industry", {"company": "삼성전자"})
        # call_tool은 이미 JSON str 반환
        parsed = json.loads(resp)
        json.dumps(parsed, ensure_ascii=False)
