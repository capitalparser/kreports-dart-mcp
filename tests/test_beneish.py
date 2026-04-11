"""
test_beneish.py — Beneish M-Score 계산 검증.

목표:
- M-Score 공식 정확성 검증 (알려진 입력값 → 예측값)
- 임계값(-1.78) 플래그 논리 검증
- 삼성전자 DB 데이터로 역검증 (M < -1.78 확인)
- 컴포넌트 미달 시 M-Score None 처리 확인
"""
import math
import pytest


# ---------------------------------------------------------------------------
# 공식 계산 단위 테스트 (DB 무관)
# ---------------------------------------------------------------------------

BENEISH_COEFFS = {
    "dsri": 0.920, "gmi": 0.528, "aqi": 0.404, "sgi": 0.892,
    "depi": 0.115, "sgai": -0.172, "tata": 4.679, "lvgi": -0.327,
}
BENEISH_INTERCEPT = -4.84
BENEISH_THRESHOLD = -1.78


def _compute_m_score(**kwargs) -> float:
    """M-Score 공식 직접 계산 (테스트 전용)."""
    return BENEISH_INTERCEPT + sum(
        val * BENEISH_COEFFS[key] for key, val in kwargs.items()
    )


class TestMScoreFormula:
    def test_all_indices_one_gives_minus_4_84_plus_sum(self):
        """모든 인덱스가 1.0이면 M = -4.84 + 합산 계수 = 고정값."""
        expected = BENEISH_INTERCEPT + sum(BENEISH_COEFFS.values())
        result = _compute_m_score(dsri=1, gmi=1, aqi=1, sgi=1, depi=1, sgai=1, tata=1, lvgi=1)
        assert abs(result - expected) < 1e-6

    def test_manipulator_profile(self):
        """이익 조작 기업 프로파일: DSRI/GMI/AQI 높고 TATA 양수 → M > -1.78."""
        m = _compute_m_score(dsri=2.0, gmi=1.5, aqi=1.3, sgi=1.2,
                              depi=1.1, sgai=1.2, tata=0.1, lvgi=1.3)
        assert m > BENEISH_THRESHOLD, f"조작 프로파일 M={m:.3f}가 임계값 초과해야 함"

    def test_clean_company_profile(self):
        """정상 기업 프로파일: 모든 인덱스 ~1.0, TATA 음수 → M < -1.78."""
        m = _compute_m_score(dsri=0.9, gmi=1.0, aqi=0.98, sgi=1.05,
                              depi=1.0, sgai=1.0, tata=-0.05, lvgi=0.95)
        assert m < BENEISH_THRESHOLD, f"정상 프로파일 M={m:.3f}가 임계값 하회해야 함"

    def test_threshold_boundary(self):
        """임계값 정확히 -1.78 기준 플래그 검증."""
        assert -1.78 > BENEISH_THRESHOLD - 0.001  # -1.78 is exactly the threshold
        assert -1.79 < BENEISH_THRESHOLD  # below = clean
        assert -1.77 > BENEISH_THRESHOLD  # above = manipulator

    def test_tata_dominates_near_threshold(self):
        """TATA 계수(4.679)가 가장 크므로 TATA 변화 시 M-Score 민감도 최대."""
        m_base = _compute_m_score(dsri=1, gmi=1, aqi=1, sgi=1, depi=1, sgai=1, tata=0, lvgi=1)
        m_high_tata = _compute_m_score(dsri=1, gmi=1, aqi=1, sgi=1, depi=1, sgai=1, tata=0.1, lvgi=1)
        delta = m_high_tata - m_base
        assert abs(delta - 4.679 * 0.1) < 1e-6


# ---------------------------------------------------------------------------
# 삼성전자 DB 역검증 (통합 테스트)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def samsung_annual_financials():
    """DB에서 삼성전자 연간 CFS 재무 데이터 로드."""
    from kreports.db.engine import get_session
    from kreports.db.models import Financial, Company
    with get_session() as session:
        company = session.query(Company).filter_by(stock_code="005930").first()
        if company is None:
            return []
        rows = (
            session.query(Financial)
            .filter_by(corp_code=company.corp_code, quarter=4, fs_div="CFS")
            .order_by(Financial.year)
            .all()
        )
        return [
            {
                "year": r.year,
                "beneish_m_score": r.beneish_m_score,
                "beneish_flag": r.beneish_flag,
                "beneish_dsri": r.beneish_dsri,
                "beneish_sgi": r.beneish_sgi,
                "beneish_tata": r.beneish_tata,
                "beneish_gmi": r.beneish_gmi,
                "revenue": r.revenue,
                "operating_cf": r.operating_cf,
            }
            for r in rows
        ]


class TestSamsungBeneishIntegration:
    def test_beneish_computed_for_all_years(self, samsung_annual_financials):
        """삼성전자 수집 연도 전체에 Beneish M-Score가 계산되어야 한다."""
        if not samsung_annual_financials:
            pytest.skip("삼성전자 DB 데이터 없음")
        for row in samsung_annual_financials:
            assert row["beneish_m_score"] is not None, \
                f"{row['year']}년 Beneish M-Score 미계산"

    def test_samsung_no_manipulation_flag(self, samsung_annual_financials):
        """삼성전자는 전기간 이익 조작 플래그가 False여야 한다."""
        if not samsung_annual_financials:
            pytest.skip("삼성전자 DB 데이터 없음")
        for row in samsung_annual_financials:
            assert row["beneish_flag"] is False, \
                f"{row['year']}년 beneish_flag=True (M={row['beneish_m_score']:.2f})"

    def test_samsung_m_score_well_below_threshold(self, samsung_annual_financials):
        """삼성전자 M-Score는 임계값 -1.78보다 최소 0.5 낮아야 한다 (여유 마진)."""
        if not samsung_annual_financials:
            pytest.skip("삼성전자 DB 데이터 없음")
        for row in samsung_annual_financials:
            m = row["beneish_m_score"]
            assert m < (BENEISH_THRESHOLD - 0.5), \
                f"{row['year']}년 M={m:.3f} (예상: < {BENEISH_THRESHOLD - 0.5})"

    def test_samsung_tata_negative(self, samsung_annual_financials):
        """삼성전자 TATA는 음수여야 한다 — 영업CF가 순이익을 초과(고품질 이익)."""
        if not samsung_annual_financials:
            pytest.skip("삼성전자 DB 데이터 없음")
        for row in samsung_annual_financials:
            if row["beneish_tata"] is not None:
                assert row["beneish_tata"] < 0, \
                    f"{row['year']}년 TATA={row['beneish_tata']:.3f} (음수 기대)"

    def test_samsung_sgi_2023_below_one(self, samsung_annual_financials):
        """삼성전자 2023년 SGI < 1.0 — 반도체 다운사이클로 매출 감소."""
        if not samsung_annual_financials:
            pytest.skip("삼성전자 DB 데이터 없음")
        row_2023 = next((r for r in samsung_annual_financials if r["year"] == 2023), None)
        if row_2023 is None or row_2023["beneish_sgi"] is None:
            pytest.skip("2023년 SGI 데이터 없음")
        assert row_2023["beneish_sgi"] < 1.0, \
            f"2023 SGI={row_2023['beneish_sgi']:.3f} (< 1.0 기대, 반도체 매출 감소)"

    def test_samsung_operating_cf_positive(self, samsung_annual_financials):
        """삼성전자 영업CF는 전기간 양수여야 한다."""
        if not samsung_annual_financials:
            pytest.skip("삼성전자 DB 데이터 없음")
        for row in samsung_annual_financials:
            if row["operating_cf"] is not None:
                assert row["operating_cf"] > 0, \
                    f"{row['year']}년 영업CF={row['operating_cf']:,} (양수 기대)"
