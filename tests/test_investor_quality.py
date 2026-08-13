from kreports.analysis.investor_quality import quality_of_earnings_pack


def test_quality_of_earnings_pack_returns_low_cash_conversion_signal(monkeypatch):
    def fake_series(company, start_year, end_year, fs_div="CFS"):
        return [
            {"bsns_year": 2023, "revenue": 100, "operating_profit": 10, "net_income": 8, "operating_cf": 2},
            {"bsns_year": 2024, "revenue": 120, "operating_profit": 12, "net_income": 9, "operating_cf": 3},
        ]

    monkeypatch.setattr("kreports.analysis.investor_quality._financial_series", fake_series)
    monkeypatch.setattr("kreports.analysis.investor_quality._audit_matter_flags", lambda *args: [])

    out = quality_of_earnings_pack("001", start_year=2023, end_year=2024)

    assert out["signals"][0]["signal"] == "low_cash_conversion"
    assert out["verdict"] == "monitor"
    assert out["data_quality"]["status"] == "limited"
