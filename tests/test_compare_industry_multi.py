"""
test_compare_industry_multi.py — compare_to_industry 회귀 + 멀티 변형 테스트.

Task 5: refactor get_industry_aggregates → peer.resolve_peers 위임 후에도
기존 응답 키가 유지되는지 회귀 검증.
Task 6: compare_to_industry_multi (다지표·다년도) 통합 테스트.
"""
from __future__ import annotations

from kreports.analysis.api import compare_to_industry, compare_to_industry_multi


def test_compare_to_industry_samsung_legacy_shape():
    """기존 compare_to_industry 응답 키가 유지된다 (회귀)."""
    out = compare_to_industry(company="005930", metric="영업이익률")
    assert "induty_code" in out
    assert "match_prefix" in out
    assert "metric" in out
    assert "year" in out
    assert "n" in out
    assert "quantiles" in out
    assert "peers" in out


def test_compare_multi_samsung_default_8metrics_5years():
    out = compare_to_industry_multi(company="005930")
    assert out["subject"]["corp_name"] == "삼성전자"
    assert out["sector_group"] == "general"
    assert "matched_prefix_len" in out
    assert "confidence" in out
    assert isinstance(out["results"], dict)
    years = list(out["results"].keys())
    assert len(years) >= 1
    sample = out["results"][years[0]]
    assert "영업이익률" in sample
    assert "ROE" in sample
    inner = sample["영업이익률"]
    assert "p50" in inner
    assert "subject_value" in inner


def test_compare_multi_explicit_metrics_and_years():
    out = compare_to_industry_multi(
        company="005930", metrics=["ROE", "ROA"], years_back=3
    )
    years = sorted(out["results"].keys())
    assert len(years) <= 3
    sample = out["results"][years[-1]]
    assert set(sample.keys()) == {"ROE", "ROA"}


def test_compare_multi_unknown_company_returns_error():
    out = compare_to_industry_multi(company="존재하지않는회사명12345")
    assert "error" in out


def test_compare_multi_invalid_metric_returns_error():
    out = compare_to_industry_multi(company="005930", metrics=["BadMetric"])
    assert "error" in out
