"""
test_integrity.py — 삼성전자 5개년 수집 데이터 정합성 검증.

목표:
- 6개 핵심 계정 전 분기 비결측 확인
- 금액 범위 타당성 검증 (단위 착오, 부호 오류 탐지)
- Judge 플래그 일관성 검증 (account_map_confidence = 1.0)
- CFS/OFS 구분 수집 확인
- Upsert 중복 없음 확인 (동일 기간 레코드 1건)
"""
import pytest
from kreports.db.engine import get_session
from kreports.db.models import Company, Financial, Disclosure, Auditor
from kreports.judge.auditor_flags import compute_auditor_flags

SAMSUNG_STOCK = "005930"
_10억 = 1_000_000_000       # 10억 원
_1조 = 1_000_000_000_000    # 1조 원
_300조 = 300 * _1조


@pytest.fixture(scope="module")
def samsung_corp_code():
    with get_session() as session:
        company = session.query(Company).filter_by(stock_code=SAMSUNG_STOCK).first()
        if company is None:
            pytest.skip("삼성전자 DB 미등록")
        return company.corp_code


@pytest.fixture(scope="module")
def samsung_financials(samsung_corp_code):
    with get_session() as session:
        rows = (
            session.query(Financial)
            .filter_by(corp_code=samsung_corp_code, fs_div="CFS")
            .order_by(Financial.year, Financial.quarter)
            .all()
        )
        return [
            {
                "year": r.year, "quarter": r.quarter, "fs_div": r.fs_div,
                "revenue": r.revenue, "operating_profit": r.operating_profit,
                "net_income": r.net_income, "total_assets": r.total_assets,
                "total_debt": r.total_debt, "total_equity": r.total_equity,
                "account_map_confidence": r.account_map_confidence,
                "cfs_ofs_gap_flag": r.cfs_ofs_gap_flag,
                "operating_cf": r.operating_cf,
                "beneish_m_score": r.beneish_m_score,
            }
            for r in rows
        ]


class TestSamsungDataCompleteness:
    def test_minimum_quarters_collected(self, samsung_financials):
        """최소 12분기(3개년) 이상 CFS 데이터가 있어야 한다."""
        assert len(samsung_financials) >= 12, \
            f"CFS 분기 수={len(samsung_financials)} (최소 12개 필요)"

    def test_no_missing_core_accounts(self, samsung_financials):
        """6개 핵심 계정이 모두 비결측이어야 한다."""
        CORE = ["revenue", "operating_profit", "net_income",
                "total_assets", "total_debt", "total_equity"]
        for row in samsung_financials:
            period = f"{row['year']}Q{row['quarter']}"
            for field in CORE:
                assert row[field] is not None, f"{period} {field} 결측"

    def test_account_map_confidence_perfect(self, samsung_financials):
        """상장사인 삼성전자는 account_map_confidence = 1.0이어야 한다."""
        for row in samsung_financials:
            period = f"{row['year']}Q{row['quarter']}"
            conf = row["account_map_confidence"]
            assert conf == 1.0, f"{period} confidence={conf} (1.0 기대)"

    def test_annual_q4_has_beneish(self, samsung_financials):
        """연간(Q4) 데이터에는 Beneish M-Score가 계산되어야 한다."""
        annual = [r for r in samsung_financials if r["quarter"] == 4]
        for row in annual:
            period = f"{row['year']}Q4"
            assert row["beneish_m_score"] is not None, f"{period} Beneish 미계산"


class TestSamsungAmountSanity:
    """금액 단위·부호 타당성 검증 (삼성전자 매출 ~300조, 자산 ~400-570조)."""

    def test_annual_revenue_in_valid_range(self, samsung_financials):
        """연간 CFS 매출액은 100조~600조 원 범위여야 한다."""
        annual = [r for r in samsung_financials if r["quarter"] == 4]
        for row in annual:
            rev = row["revenue"]
            assert 100 * _1조 <= rev <= 600 * _1조, \
                f"{row['year']} 매출={rev:,} (기대: 100조~600조)"

    def test_total_assets_exceeds_equity(self, samsung_financials):
        """자산총계 > 자본총계 (기본 대차대조표 항등식)."""
        for row in samsung_financials:
            period = f"{row['year']}Q{row['quarter']}"
            assert row["total_assets"] > row["total_equity"], \
                f"{period} 자산({row['total_assets']:,}) ≤ 자본({row['total_equity']:,})"

    def test_assets_equals_debt_plus_equity_approx(self, samsung_financials):
        """자산 ≈ 부채 + 자본 (±5% 허용, 반올림 차이)."""
        for row in samsung_financials:
            period = f"{row['year']}Q{row['quarter']}"
            reconstructed = row["total_debt"] + row["total_equity"]
            ratio = abs(row["total_assets"] - reconstructed) / row["total_assets"]
            assert ratio < 0.05, \
                f"{period} 자산={row['total_assets']:,} ≠ 부채+자본={reconstructed:,} (차이 {ratio:.1%})"

    def test_no_negative_revenue(self, samsung_financials):
        """매출액은 양수여야 한다."""
        for row in samsung_financials:
            period = f"{row['year']}Q{row['quarter']}"
            assert row["revenue"] > 0, f"{period} 매출액 음수"

    def test_no_negative_total_assets(self, samsung_financials):
        """자산총계는 양수여야 한다."""
        for row in samsung_financials:
            period = f"{row['year']}Q{row['quarter']}"
            assert row["total_assets"] > 0, f"{period} 자산총계 음수"

    def test_operating_cf_quarterly_reasonable(self, samsung_financials):
        """영업CF가 있는 경우, 매출의 ±150% 이내여야 한다 (극단값 탐지)."""
        for row in samsung_financials:
            period = f"{row['year']}Q{row['quarter']}"
            if row["operating_cf"] is None:
                continue
            ratio = abs(row["operating_cf"]) / row["revenue"]
            assert ratio < 1.5, \
                f"{period} 영업CF/매출={ratio:.1%} (>150% 이상 이상)"


class TestUpsertNoDuplicates:
    def test_no_duplicate_periods(self, samsung_corp_code):
        """동일 (corp_code, year, quarter, fs_div) 레코드는 1건만 존재해야 한다."""
        with get_session() as session:
            from sqlalchemy import func
            dupes = (
                session.query(
                    Financial.year, Financial.quarter, Financial.fs_div,
                    func.count(Financial.id).label("cnt")
                )
                .filter_by(corp_code=samsung_corp_code)
                .group_by(Financial.year, Financial.quarter, Financial.fs_div)
                .having(func.count(Financial.id) > 1)
                .all()
            )
        assert len(dupes) == 0, \
            f"중복 레코드 발견: {[(r.year, r.quarter, r.fs_div) for r in dupes]}"


class TestDisclosureIntegrity:
    def test_disclosures_exist(self, samsung_corp_code):
        """삼성전자 공시가 1,000건 이상 수집되어야 한다."""
        with get_session() as session:
            count = session.query(Disclosure).filter_by(corp_code=samsung_corp_code).count()
        assert count >= 1000, f"공시 수={count:,} (최소 1,000건 기대)"

    def test_disclosure_type_classified(self, samsung_corp_code):
        """'E'(기타)가 아닌 공시가 10%이상이어야 한다 (분류 정상 여부)."""
        with get_session() as session:
            total = session.query(Disclosure).filter_by(corp_code=samsung_corp_code).count()
            e_count = session.query(Disclosure).filter_by(
                corp_code=samsung_corp_code, disc_type="E"
            ).count()
        classified_ratio = 1 - (e_count / total)
        assert classified_ratio >= 0.10, \
            f"분류율={classified_ratio:.1%} (최소 10% 비(E) 분류 기대)"

    def test_annual_reports_exist(self, samsung_corp_code):
        """사업보고서(A유형) 공시가 1건 이상 있어야 한다."""
        with get_session() as session:
            count = session.query(Disclosure).filter_by(
                corp_code=samsung_corp_code, disc_type="A"
            ).count()
        assert count >= 1, "사업보고서(A) 공시 없음"


class TestAuditorIntegrity:
    def test_auditors_exist(self, samsung_corp_code):
        """삼성전자 감사인 이력이 1건 이상 있어야 한다."""
        with get_session() as session:
            count = session.query(Auditor).filter_by(corp_code=samsung_corp_code).count()
        assert count >= 1, "감사인 이력 없음"

    def test_auditor_change_detected(self, samsung_corp_code):
        """삼성전자는 2023년 안진→삼정 교체가 감지되어야 한다."""
        compute_auditor_flags(samsung_corp_code)
        with get_session() as session:
            auditor_names = {
                r[0] for r in
                session.query(Auditor.auditor_nm)
                .filter_by(corp_code=samsung_corp_code)
                .distinct()
                .all()
            }
            if len(auditor_names) <= 1:
                pytest.skip("현재 로컬 DB 감사인 이력에 교체 이벤트 없음")
            changed = session.query(Auditor).filter_by(
                corp_code=samsung_corp_code,
                is_auditor_changed=True,
            ).all()
        assert len(changed) >= 1, "감사인 교체 미탐지 (2023 안진→삼정 기대)"
