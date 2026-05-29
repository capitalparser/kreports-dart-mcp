from datetime import date

from kreports.db.models import Company, Disclosure, DisclosureEvent


def _seed_company_and_business_report(session):
    session.add(Company(corp_code="001", corp_name="A", stock_code="000001", market="KOSPI"))
    session.add(Disclosure(
        rcept_no="20250318001234",
        corp_code="001",
        corp_name="A",
        disc_date=date(2025, 3, 18),
        disc_type="A",
        report_nm="사업보고서 (2024.12)",
        flr_nm="A",
    ))


def test_quality_of_earnings_api_adds_confirmed_facts(temp_engine, monkeypatch):
    from kreports.analysis import investor_quality
    from kreports.analysis.api import get_quality_of_earnings_pack
    from kreports.db.engine import get_session

    def fake_pack(company, *, start_year, end_year, fs_div="CFS"):
        return {
            "company": company,
            "start_year": start_year,
            "end_year": end_year,
            "fs_div": fs_div,
            "verdict": "stable",
            "investment_question": "보고이익이 현금흐름으로 뒷받침되는가?",
            "signals": [],
            "metrics": {"years": 3, "low_cash_conversion_years": 0, "negative_ocf_years": 0},
            "evidence": [
                {"year": 2022, "revenue": 100, "operating_cf": 20, "cash_conversion": 1.0},
                {"year": 2024, "revenue": 150, "operating_cf": 40, "cash_conversion": 1.2},
            ],
            "limitations": ["한계"],
            "data_quality": {"status": "usable", "source": "financial_facts_compact", "year_count": 3},
        }

    monkeypatch.setattr(investor_quality, "quality_of_earnings_pack", fake_pack)
    with get_session() as session:
        _seed_company_and_business_report(session)

    out = get_quality_of_earnings_pack("001", start_year=2022, end_year=2024)

    assert out["confirmed_facts"]
    assert out["confirmed_facts"][0]["source"]["rcept_no"] == "20250318001234"
    assert out["analysis"][0]["perspective"] == "investor"
    assert out["next_checks"]


def test_dcf_api_adds_confirmed_facts(temp_engine, monkeypatch):
    from kreports.analysis import dcf_inputs
    from kreports.analysis.api import get_dcf_input_candidates
    from kreports.db.engine import get_session

    def fake_dcf(company, *, start_year, end_year, fs_div="CFS"):
        return {
            "company": company,
            "start_year": start_year,
            "end_year": end_year,
            "fs_div": fs_div,
            "historical_actuals": [
                {"year": 2023, "revenue": 100, "revenue_growth": None, "operating_margin": 0.1},
                {"year": 2024, "revenue": 120, "revenue_growth": 0.2, "operating_margin": 0.12},
            ],
            "candidate_assumptions": {
                "revenue_growth": {"basis": "historical_median", "value": 0.2, "observations": [0.2]},
                "operating_margin": {"basis": "historical_median", "value": 0.11, "observations": [0.1, 0.12]},
            },
            "missing_inputs": ["wacc"],
            "limitations": ["한계"],
            "data_quality": {"status": "limited", "source": "financial_facts_compact", "year_count": 2},
        }

    monkeypatch.setattr(dcf_inputs, "dcf_input_candidates", fake_dcf)
    with get_session() as session:
        _seed_company_and_business_report(session)

    out = get_dcf_input_candidates("001", start_year=2023, end_year=2024)

    assert "DCF 입력 후보" in out["confirmed_facts"][0]["statement"]
    assert out["confirmed_facts"][0]["source"]["rcept_no"] == "20250318001234"
    assert any(check.startswith("DCF") for check in out["next_checks"])


def test_disclosure_event_api_adds_confirmed_facts(temp_engine):
    from kreports.analysis.api import search_disclosure_events
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(corp_code="001", corp_name="A", stock_code="000001", market="KOSPI"))
        session.add(DisclosureEvent(
            rcept_no="20250101000001",
            corp_code="001",
            event_date=date(2025, 1, 1),
            event_type="capital_raise",
            event_title="주요사항보고서(유상증자결정)",
            severity_hint="monitor",
            source_report_nm="주요사항보고서(유상증자결정)",
        ))

    out = search_disclosure_events(
        start_date="2025-01-01",
        end_date="2025-12-31",
        event_types=["capital_raise"],
    )

    assert out["confirmed_facts"][0]["source"]["rcept_no"] == "20250101000001"
    assert "공시목록" == out["confirmed_facts"][0]["source"]["section_title"]
    assert out["analysis"][0]["perspective"] == "investor"


def test_investor_signals_api_adds_confirmed_facts(temp_engine, monkeypatch):
    from kreports.analysis import api
    from kreports.analysis.api import get_investor_signals
    from kreports.db.engine import get_session

    monkeypatch.setattr(api, "get_financial_snapshot", lambda *args, **kwargs: {
        "rows": [{
            "연도": 2024,
            "ROE": 12.5,
            "영업이익률": 8.1,
            "매출성장률": 5.0,
            "부채비율": 40.0,
            "FCF": 10,
            "CFO_NI": 1.1,
        }],
    })
    monkeypatch.setattr(api._queries, "get_risk_summary", lambda corp_code: {"has_data": True, "non_clean_opinion_count": 0})
    monkeypatch.setattr(api, "_recent_investor_events", lambda *args: ([
        {
            "disc_date": "2025-01-01",
            "rcept_no": "20250101000001",
            "report_nm": "주요사항보고서(유상증자결정)",
            "category": "capital_raise",
            "label": "유상증자",
            "stance": "monitor",
        }
    ], {"capital_raise": 1}))
    with get_session() as session:
        _seed_company_and_business_report(session)

    out = get_investor_signals("000001", years=1, window_days=365)

    assert out["confirmed_facts"]
    assert out["confirmed_facts"][0]["source"]["rcept_no"] == "20250318001234"
    assert any(fact["source"].get("rcept_no") == "20250101000001" for fact in out["confirmed_facts"])
    assert out["analysis"][0]["perspective"] == "investor"
