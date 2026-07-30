from kreports.analysis.investor_quality import quality_of_earnings_pack


def test_quality_of_earnings_pack_returns_low_cash_conversion_signal(monkeypatch):
    def fake_series(company, start_year, end_year, *, fs_div="CFS"):
        series = [
            {
                "bsns_year": 2023, "revenue": 100, "operating_profit": 10,
                "net_income": 8, "operating_cf": 2,
                "source": {"rcept_no": "20240318000001"}, "units": {},
            },
            {
                "bsns_year": 2024, "revenue": 120, "operating_profit": 12,
                "net_income": 9, "operating_cf": 3,
                "source": {"rcept_no": "20250318000001"}, "units": {},
            },
        ]
        observations = [
            {
                "year": row["bsns_year"],
                "source": row["source"],
                "provenance_status": "proven_company_year_annual_filing",
            }
            for row in series
        ]
        return series, observations

    monkeypatch.setattr(
        "kreports.analysis.investor_quality._qoe_provenance_series",
        fake_series,
    )
    monkeypatch.setattr("kreports.analysis.investor_quality._audit_matter_flags", lambda *args: [])

    out = quality_of_earnings_pack("001", start_year=2023, end_year=2024)

    assert out["signals"][0]["signal"] == "low_cash_conversion"
    assert out["verdict"] == "monitor"
    assert out["data_quality"]["status"] == "limited"
