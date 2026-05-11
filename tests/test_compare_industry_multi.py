"""
test_compare_industry_multi.py — compare_to_industry 회귀 + 멀티 변형 테스트.

Task 5: refactor get_industry_aggregates → peer.resolve_peers 위임 후에도
기존 응답 키가 유지되는지 회귀 검증. (Task 6에서 다중 metric/sector 케이스 확장 예정.)
"""
from __future__ import annotations

from kreports.analysis.api import compare_to_industry


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
