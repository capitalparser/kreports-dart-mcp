from kreports.analysis.dcf_inputs import dcf_input_candidates


def test_dcf_input_candidates_separates_actuals_and_assumptions(monkeypatch):
    monkeypatch.setattr("kreports.analysis.dcf_inputs._financial_series", lambda *args, **kwargs: [
        {"bsns_year": 2022, "revenue": 100, "operating_profit": 10, "operating_cf": 9, "net_income": 8},
        {"bsns_year": 2023, "revenue": 110, "operating_profit": 11, "operating_cf": 10, "net_income": 9},
        {"bsns_year": 2024, "revenue": 121, "operating_profit": 12, "operating_cf": 11, "net_income": 10},
    ])

    out = dcf_input_candidates("001", start_year=2022, end_year=2024)

    assert "revenue_growth" in out["candidate_assumptions"]
    assert out["candidate_assumptions"]["revenue_growth"]["value"] == 0.1
    assert out["historical_actuals"][0]["year"] == 2022
    assert "wacc" in out["missing_inputs"]
    assert out["limitations"]
