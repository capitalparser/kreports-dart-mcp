from kreports.analysis.dcf_inputs import dcf_input_candidates


def test_dcf_input_candidates_separates_actuals_and_assumptions(monkeypatch):
    monkeypatch.setattr("kreports.analysis.dcf_inputs._financial_series", lambda *args, **kwargs: [
        {"bsns_year": 2022, "revenue": 100, "operating_profit": 10, "operating_cf": 9, "net_income": 8, "tax_expense": 2, "purchase_ppe": 4},
        {"bsns_year": 2023, "revenue": 110, "operating_profit": 11, "operating_cf": 10, "net_income": 9, "tax_expense": 2, "purchase_ppe": 5},
        {"bsns_year": 2024, "revenue": 121, "operating_profit": 12, "operating_cf": 11, "net_income": 10, "tax_expense": 3, "purchase_ppe": 6},
    ])

    out = dcf_input_candidates("001", start_year=2022, end_year=2024)

    assert "revenue_growth" in out["candidate_assumptions"]
    assert out["candidate_assumptions"]["revenue_growth"]["value"] == 0.1
    assert out["candidate_assumptions"]["operating_margin"]["value"] == 0.1
    assert out["candidate_assumptions"]["capex_to_revenue"]["value"] == 0.0455
    assert out["historical_actuals"][0]["year"] == 2022
    assert "wacc" in out["missing_inputs"]
    assert "tax_rate" not in out["missing_inputs"]
    assert "capex" not in out["missing_inputs"]
    assert out["limitations"]


def test_dcf_requests_registry_support_metrics_and_uses_tax_expense(monkeypatch):
    """Removing the DCF registry query or tax expense use breaks the tax-rate candidate."""
    from kreports.analysis import dcf_inputs
    from kreports.semantic.metrics import DCF_SUPPORT_METRICS

    def series(*args, metric_keys, **kwargs):
        assert metric_keys == DCF_SUPPORT_METRICS
        return [
            {"bsns_year": 2024, "revenue": 100, "operating_profit": 10,
             "operating_cf": 9, "net_income": 8, "tax_expense": 2,
             "purchase_ppe": 4},
        ]

    monkeypatch.setattr(dcf_inputs, "_financial_series", series)

    out = dcf_inputs.dcf_input_candidates("001", start_year=2024, end_year=2024)

    assert out["candidate_assumptions"]["tax_rate"]["value"] == 0.2


def test_dcf_input_candidates_marks_missing_operating_profit_as_incomplete(monkeypatch):
    monkeypatch.setattr("kreports.analysis.dcf_inputs._financial_series", lambda *args, **kwargs: [
        {"bsns_year": 2022, "revenue": 100, "operating_cf": 9, "net_income": 8},
        {"bsns_year": 2023, "revenue": 110, "operating_cf": 10, "net_income": 9},
        {"bsns_year": 2024, "revenue": 121, "operating_cf": 11, "net_income": 10},
    ])

    out = dcf_input_candidates("001", start_year=2022, end_year=2024)

    assert out["data_quality"]["status"] == "incomplete_core_metrics"
    assert out["data_quality"]["readiness"] == "partial"
    assert "operating_profit" in out["missing_inputs"]
    assert out["candidate_assumptions"]["operating_margin"]["value"] is None
