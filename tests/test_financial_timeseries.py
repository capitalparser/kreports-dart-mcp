from kreports.analysis.financial_timeseries import get_financial_timeseries_quality
from kreports.db.models import Company, Financial


def test_financial_timeseries_quality_requires_five_year_cfs(temp_engine):
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(corp_code="00126380", stock_code="005930", corp_name="삼성전자", market="KOSPI"))
        for year in [2021, 2022, 2023, 2024, 2025]:
            session.add(Financial(
                corp_code="00126380",
                year=year,
                quarter=4,
                fs_div="CFS",
                revenue=1000 + year,
                operating_profit=100,
                net_income=80,
                total_assets=5000,
                total_debt=2000,
                total_equity=3000,
            ))

    out = get_financial_timeseries_quality("00126380", year=2025, years_back=5)

    assert out["verdict"] == "pass"
    assert out["years"] == [2021, 2022, 2023, 2024, 2025]
    assert out["fs_div_used"] == "CFS"
    assert out["missing_years"] == []
