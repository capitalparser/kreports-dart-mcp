"""
test_fin_parser.py — 재무제표 파싱 로직 검증.

목표:
- DART API fixture 기반 parse_all_accounts() 검증
- compute_summary_from_facts() 금액·신뢰도 검증
- 별칭 계정명 폴백 동작 검증
- 데이터 없음(013) 응답 처리 검증
"""
import pytest
from kreports.processor.fin_parser import (
    parse_all_accounts,
    compute_summary_from_facts,
    parse_financials,
    _parse_amount,
    REPRT_TO_QUARTER,
)

CORP_CODE = "00126380"  # 삼성전자
YEAR = 2024
REPRT_CODE = "11011"    # 사업보고서 (Q4)
FS_DIV = "CFS"


class TestParseAmount:
    @pytest.mark.parametrize("raw,expected", [
        ("300,869,340,000,000", 300_869_340_000_000),
        ("-100,000,000", -100_000_000),
        ("0", 0),
        ("", None),
        ("-", None),
        ("－", None),
        (None, None),
    ])
    def test_parse_amount(self, raw, expected):
        assert _parse_amount(raw) == expected


class TestParseAllAccounts:
    def test_returns_list_on_valid_response(self, dart_response_samsung_2024):
        result = parse_all_accounts(dart_response_samsung_2024, CORP_CODE, YEAR, REPRT_CODE, FS_DIV)
        assert result is not None
        assert isinstance(result, list)
        assert len(result) == 6

    def test_returns_none_on_no_data(self, dart_response_no_data):
        result = parse_all_accounts(dart_response_no_data, CORP_CODE, YEAR, REPRT_CODE, FS_DIV)
        assert result is None

    def test_corp_code_propagated(self, dart_response_samsung_2024):
        result = parse_all_accounts(dart_response_samsung_2024, CORP_CODE, YEAR, REPRT_CODE, FS_DIV)
        for row in result:
            assert row["corp_code"] == CORP_CODE

    def test_bsns_year_propagated(self, dart_response_samsung_2024):
        result = parse_all_accounts(dart_response_samsung_2024, CORP_CODE, YEAR, REPRT_CODE, FS_DIV)
        for row in result:
            assert row["bsns_year"] == YEAR

    def test_thstrm_amount_parsed(self, dart_response_samsung_2024):
        result = parse_all_accounts(dart_response_samsung_2024, CORP_CODE, YEAR, REPRT_CODE, FS_DIV)
        revenue_row = next(r for r in result if r["account_id"] == "ifrs-full_Revenue")
        assert revenue_row["thstrm_amount"] == 300_869_340_000_000

    def test_synthetic_id_for_missing_account_id(self, dart_response_alias_accounts):
        result = parse_all_accounts(dart_response_alias_accounts, CORP_CODE, YEAR, REPRT_CODE, FS_DIV)
        account_ids = [r["account_id"] for r in result]
        # account_id 없는 행은 _nm_ 접두어로 합성
        assert any(aid.startswith("_nm_") for aid in account_ids)

    def test_no_duplicates_within_sj_div(self, dart_response_samsung_2024):
        result = parse_all_accounts(dart_response_samsung_2024, CORP_CODE, YEAR, REPRT_CODE, FS_DIV)
        keys = [(r["sj_div"], r["account_id"]) for r in result]
        assert len(keys) == len(set(keys)), "중복 (sj_div, account_id) 존재"


class TestComputeSummaryFromFacts:
    def test_all_six_fields_extracted(self, dart_response_samsung_2024):
        facts = parse_all_accounts(dart_response_samsung_2024, CORP_CODE, YEAR, REPRT_CODE, FS_DIV)
        summary = compute_summary_from_facts(facts, CORP_CODE, YEAR, REPRT_CODE, FS_DIV)

        assert summary["revenue"] == 300_869_340_000_000
        assert summary["operating_profit"] == 32_724_893_000_000
        assert summary["net_income"] == 34_468_800_000_000
        assert summary["total_assets"] == 571_164_152_000_000
        assert summary["total_debt"] == 188_526_009_000_000
        assert summary["total_equity"] == 382_638_143_000_000

    def test_account_map_confidence_perfect(self, dart_response_samsung_2024):
        facts = parse_all_accounts(dart_response_samsung_2024, CORP_CODE, YEAR, REPRT_CODE, FS_DIV)
        summary = compute_summary_from_facts(facts, CORP_CODE, YEAR, REPRT_CODE, FS_DIV)
        assert summary["account_map_confidence"] == 1.0

    def test_quarter_derived_from_reprt_code(self, dart_response_samsung_2024):
        facts = parse_all_accounts(dart_response_samsung_2024, CORP_CODE, YEAR, REPRT_CODE, FS_DIV)
        summary = compute_summary_from_facts(facts, CORP_CODE, YEAR, REPRT_CODE, FS_DIV)
        assert summary["quarter"] == 4  # 11011 → Q4

    def test_alias_fallback_works(self, dart_response_alias_accounts):
        """비표준 계정명 별칭으로도 6개 요약이 추출되어야 한다."""
        facts = parse_all_accounts(dart_response_alias_accounts, CORP_CODE, YEAR, "11011", FS_DIV)
        summary = compute_summary_from_facts(facts, CORP_CODE, YEAR, "11011", FS_DIV)

        assert summary["revenue"] == 1_000_000_000
        assert summary["operating_profit"] == 100_000_000
        assert summary["total_assets"] == 5_000_000_000
        assert summary["account_map_confidence"] == 1.0

    def test_partial_confidence_with_missing_accounts(self):
        """일부 계정이 없으면 confidence < 1.0이어야 한다."""
        partial_facts = [
            {"corp_code": CORP_CODE, "bsns_year": YEAR, "reprt_code": REPRT_CODE,
             "fs_div": FS_DIV, "sj_div": "IS", "account_id": "ifrs-full_Revenue",
             "account_nm": "매출액", "ord": 1,
             "thstrm_amount": 1_000_000, "frmtrm_amount": None,
             "bfefrmtrm_amount": None, "thstrm_add_amount": None},
        ]
        summary = compute_summary_from_facts(partial_facts, CORP_CODE, YEAR, REPRT_CODE, FS_DIV)
        # 6개 중 1개만 매핑 → confidence = 1/6 ≈ 0.167
        assert summary["account_map_confidence"] < 0.5
        assert summary["revenue"] == 1_000_000
        assert summary["operating_profit"] is None


class TestReptCodeMapping:
    @pytest.mark.parametrize("reprt_code,quarter", [
        ("11013", 1),
        ("11012", 2),
        ("11014", 3),
        ("11011", 4),
    ])
    def test_reprt_to_quarter(self, reprt_code, quarter):
        assert REPRT_TO_QUARTER[reprt_code] == quarter
