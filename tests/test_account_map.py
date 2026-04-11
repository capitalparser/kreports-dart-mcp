"""
test_account_map.py — 계정과목 별칭 매핑 정확성 검증.

목표:
- 6개 핵심 계정 모두 커버되는지
- 알려진 별칭 30종+ 정확히 매핑되는지
- 미등록 계정명은 None 반환하는지
"""
import pytest
from kreports.processor.account_map import normalize_account, ACCOUNT_ALIASES


CORE_FIELDS = ["revenue", "operating_profit", "net_income",
               "total_assets", "total_debt", "total_equity"]


class TestCoverage:
    def test_all_core_fields_defined(self):
        """6개 핵심 계정이 모두 ACCOUNT_ALIASES에 정의되어 있어야 한다."""
        for field in CORE_FIELDS:
            assert field in ACCOUNT_ALIASES, f"{field} 누락"

    def test_each_field_has_minimum_aliases(self):
        """각 핵심 계정은 최소 2개 이상의 별칭을 가져야 한다."""
        for field in CORE_FIELDS:
            count = len(ACCOUNT_ALIASES[field])
            assert count >= 2, f"{field}: 별칭 {count}개 (최소 2개 필요)"


class TestNormalize:
    @pytest.mark.parametrize("alias,expected", [
        # revenue
        ("매출액", "revenue"),
        ("영업수익", "revenue"),
        ("수익(매출액)", "revenue"),
        ("매출", "revenue"),
        ("순매출액", "revenue"),
        # operating_profit
        ("영업이익", "operating_profit"),
        ("영업이익(손실)", "operating_profit"),
        ("영업손익", "operating_profit"),
        # net_income
        ("당기순이익", "net_income"),
        ("당기순이익(손실)", "net_income"),
        ("분기순이익", "net_income"),
        ("반기순이익", "net_income"),
        ("당기순손익", "net_income"),
        # total_assets
        ("자산총계", "total_assets"),
        ("자산 총계", "total_assets"),
        # total_debt
        ("부채총계", "total_debt"),
        ("부채 총계", "total_debt"),
        # total_equity
        ("자본총계", "total_equity"),
        ("자본 총계", "total_equity"),
        ("자본합계", "total_equity"),
    ])
    def test_known_aliases(self, alias, expected):
        assert normalize_account(alias) == expected, f"'{alias}' → {expected} 실패"

    def test_unknown_alias_returns_none(self):
        assert normalize_account("선급금") is None
        assert normalize_account("재고자산") is None
        assert normalize_account("") is None

    def test_leading_trailing_whitespace(self):
        """공백 포함 계정명도 정상 매핑되어야 한다."""
        assert normalize_account("  매출액  ") == "revenue"
        assert normalize_account(" 자산총계") == "total_assets"

    def test_all_defined_aliases_are_reversible(self):
        """ACCOUNT_ALIASES에 정의된 모든 별칭이 normalize_account로 역조회 가능해야 한다."""
        for field, aliases in ACCOUNT_ALIASES.items():
            for alias in aliases:
                result = normalize_account(alias)
                assert result == field, f"'{alias}' → {result} (expected {field})"
