from datetime import date, datetime

from kreports.db.models import (
    AuditProcedureItem,
    Company,
    Disclosure,
    DisclosureEvent,
    FinancialFact,
    FinancialFactCompact,
    ReportSection,
)


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
    _add_compact_fact(session, 2024)


def _add_compact_fact(session, year: int) -> None:
    session.add(FinancialFactCompact(
        corp_code="001",
        bsns_year=year,
        fs_div="CFS",
        metric_key="revenue",
        metric_name="매출액",
        amount=100,
        source_account_id="ifrs-full_Revenue",
        source_account_nm="매출액",
    ))


def _add_annual_financial_fact(session, year: int) -> None:
    session.add(FinancialFact(
        corp_code="001",
        bsns_year=year,
        reprt_code="11011",
        fs_div="CFS",
        sj_div="IS",
        account_id="ifrs-full_Revenue",
        account_nm="매출액",
        thstrm_amount=100,
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


def test_annual_financial_evidence_does_not_cite_a_different_fiscal_year(temp_engine, monkeypatch):
    """A missing requested annual report is a provenance gap, not a latest-report citation."""
    from kreports.analysis import investor_quality
    from kreports.analysis.api import get_quality_of_earnings_pack
    from kreports.db.engine import get_session

    monkeypatch.setattr(investor_quality, "quality_of_earnings_pack", lambda *args, **kwargs: {
        "company": "001", "start_year": 2022, "end_year": 2022, "fs_div": "CFS",
        "metrics": {"years": 1}, "evidence": [{"year": 2022}], "signals": [],
        "data_quality": {"status": "usable"},
    })
    with get_session() as session:
        session.add(Company(corp_code="001", corp_name="A", stock_code="000001", market="KOSPI"))
        _add_compact_fact(session, 2022)
        session.add(Disclosure(
            rcept_no="20260318001234", corp_code="001", corp_name="A",
            disc_date=date(2026, 3, 18), disc_type="A", report_nm="사업보고서 (2025.12)", flr_nm="A",
        ))

    out = get_quality_of_earnings_pack("001", start_year=2022, end_year=2022)
    source = out["confirmed_facts"][0]["source"]

    assert source["rcept_no"] is None
    assert source["provenance_status"] == "requested_annual_report_not_cached"
    assert "2022" in source["provenance_gap"]
    assert "20260318001234" not in str(out["confirmed_facts"])
    assert out["data_quality"]["status"] == "limited"
    assert source["provenance_gap"] in out["data_quality"]["limitations"]


def test_annual_financial_evidence_cites_matching_non_december_fiscal_year(temp_engine, monkeypatch):
    from kreports.analysis import investor_quality
    from kreports.analysis.api import get_quality_of_earnings_pack
    from kreports.db.engine import get_session

    monkeypatch.setattr(investor_quality, "quality_of_earnings_pack", lambda *args, **kwargs: {
        "company": "001", "start_year": 2022, "end_year": 2022, "fs_div": "CFS",
        "metrics": {"years": 1}, "evidence": [{"year": 2022}], "signals": [],
        "data_quality": {"status": "usable"},
    })
    with get_session() as session:
        session.add(Company(corp_code="001", corp_name="A", stock_code="000001", market="KOSPI"))
        _add_compact_fact(session, 2022)
        session.add(Disclosure(
            rcept_no="20230318001234", corp_code="001", corp_name="A",
            disc_date=date(2023, 3, 18), disc_type="A", report_nm="사업보고서 (2022.03)", flr_nm="A",
        ))

    out = get_quality_of_earnings_pack("001", start_year=2022, end_year=2022)

    assert out["confirmed_facts"][0]["source"]["rcept_no"] == "20230318001234"


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
    from kreports.analysis import financial_analysis
    from kreports.analysis.api import get_investor_signals
    from kreports.db.engine import get_session

    monkeypatch.setattr(financial_analysis, "get_financial_snapshot", lambda *args, **kwargs: {
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
    monkeypatch.setattr(financial_analysis._queries, "get_risk_summary", lambda corp_code: {"has_data": True, "non_clean_opinion_count": 0})
    monkeypatch.setattr(financial_analysis, "_recent_investor_events", lambda *args: ([
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
        _add_annual_financial_fact(session, 2024)

    out = get_investor_signals("000001", years=1, window_days=365)

    assert out["confirmed_facts"]
    assert out["confirmed_facts"][0]["source"]["rcept_no"] == "20250318001234"
    assert any(fact["source"].get("rcept_no") == "20250101000001" for fact in out["confirmed_facts"])
    assert out["analysis"][0]["perspective"] == "investor"


def test_audit_report_sections_api_adds_confirmed_facts(temp_engine):
    from kreports.analysis.api import get_audit_report_sections
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(corp_code="001", corp_name="A", stock_code="000001", market="KOSPI"))
        session.add(ReportSection(
            rcept_no="20260311000001",
            corp_code="001",
            bsns_year=2025,
            source_type="audit_report",
            section_key="kam",
            section_title="핵심감사사항",
            body_text="수익인식은 핵심감사사항입니다. 우리는 매출 거래 문서검사를 수행하였습니다.",
            body_hash="x",
            body_length=50,
            ordinal=0,
            fetched_at=datetime.utcnow(),
        ))

    out = get_audit_report_sections("000001", year=2025, section_key="kam")

    assert out["confirmed_facts"][0]["source"]["rcept_no"] == "20260311000001"
    assert out["confirmed_facts"][0]["source"]["section_title"] == "핵심감사사항"
    assert out["analysis"][0]["perspective"] == "auditor"


def test_audit_report_sections_dedupes_repeated_attachment_facts(temp_engine):
    from kreports.analysis.api import get_audit_report_sections
    from kreports.db.engine import get_session

    repeated_body = "핵심감사사항은 우리의 전문가적 판단에 따라 당기 감사에서 가장 유의적인 사항입니다."
    with get_session() as session:
        session.add(Company(corp_code="001", corp_name="A", stock_code="000001", market="KOSPI"))
        session.add_all([
            ReportSection(
                rcept_no="20260311000001_001_xml",
                corp_code="001",
                bsns_year=2025,
                source_type="audit_report",
                section_key="kam",
                section_title="핵심감사사항",
                body_text=repeated_body,
                body_hash="same",
                body_length=len(repeated_body),
                ordinal=0,
                fetched_at=datetime.utcnow(),
            ),
            ReportSection(
                rcept_no="20260311000001_002_xml",
                corp_code="001",
                bsns_year=2025,
                source_type="audit_report",
                section_key="kam",
                section_title="핵심감사사항",
                body_text=repeated_body,
                body_hash="same",
                body_length=len(repeated_body),
                ordinal=0,
                fetched_at=datetime.utcnow(),
            ),
        ])

    out = get_audit_report_sections("000001", year=2025, section_key="kam")

    assert out["section_count"] == 2
    assert len(out["confirmed_facts"]) == 1
    assert out["confirmed_facts"][0]["source"]["rcept_no"] == "20260311000001"


def test_audit_matters_api_adds_confirmed_facts(temp_engine):
    from kreports.analysis.api import search_audit_report_matters
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(corp_code="001", corp_name="A", stock_code="000001", market="KOSPI"))
        session.add(ReportSection(
            rcept_no="20260311000002",
            corp_code="001",
            bsns_year=2025,
            source_type="audit_report",
            section_key="emphasis",
            section_title="강조사항",
            body_text="계속기업 존속능력에 중요한 불확실성이 존재합니다.",
            body_hash="x",
            body_length=40,
            ordinal=0,
            fetched_at=datetime.utcnow(),
        ))

    out = search_audit_report_matters(company="000001", year=2025, section_keys=["emphasis"], limit=5)

    assert out["confirmed_facts"][0]["source"]["rcept_no"] == "20260311000002"
    assert out["confirmed_facts"][0]["source"]["section_title"] == "강조사항"
    assert out["analysis"][0]["perspective"] == "auditor"


def test_audit_procedures_api_adds_confirmed_facts(temp_engine):
    from kreports.analysis.api import search_audit_procedures
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(corp_code="001", corp_name="A", stock_code="000001", market="KOSPI"))
        session.add(AuditProcedureItem(
            corp_code="001",
            bsns_year=2025,
            rcept_no="20260311000003",
            dcm_no=None,
            source_type="audit_report",
            kam_topic="revenue",
            procedure_type="substantive_test",
            procedure_text="매출 거래 표본에 대해 문서검사를 수행하였습니다.",
            procedure_hash="x",
            procedure_length=30,
            section_ordinal=0,
            procedure_ordinal=0,
            fetched_at=datetime.utcnow(),
        ))

    out = search_audit_procedures(company="000001", year=2025, procedure_type="substantive_test", limit=5)

    assert out["confirmed_facts"][0]["source"]["rcept_no"] == "20260311000003"
    assert out["confirmed_facts"][0]["source"]["section_title"] == "KAM 감사절차"
    assert out["analysis"][0]["perspective"] == "auditor"


def test_kam_section_answer_pack_keeps_semantic_coverage_table(temp_engine):
    from kreports.analysis.api import get_audit_report_sections
    from kreports.db.engine import get_session
    from kreports.mcp.contracts import enrich_answer_response

    with get_session() as session:
        session.add(Company(corp_code="001", corp_name="A", stock_code="000001", market="KOSPI"))
        session.add(ReportSection(
            rcept_no="20260311000004",
            corp_code="001",
            bsns_year=2025,
            source_type="audit_report",
            section_key="kam",
            section_title="핵심감사사항",
            body_text="감사인과 지배기구에 커뮤니케이션한 사항입니다.",
            body_hash="kam-semantic-pack",
            body_length=25,
            ordinal=0,
            fetched_at=datetime.utcnow(),
        ))

    out = enrich_answer_response(
        "get_audit_report_sections",
        get_audit_report_sections("000001", year=2025, section_key="kam"),
    )

    assert out["quality_status"] == "limited"
    assert {table["id"] for table in out["answer_pack"]["tables"]} >= {
        "audit_report_kam_items",
        "audit_report_kam_coverage",
    }


def test_public_kam_handler_never_claims_business_or_all_summary_semantics(
    temp_engine,
):
    import json

    from kreports.db.engine import get_session
    from kreports.mcp.tools import call_tool

    with get_session() as session:
        session.add(Company(
            corp_code="001",
            corp_name="A",
            stock_code="000001",
            market="KOSPI",
        ))
        for source_type, receipt in (
            ("business_report", "20260311000011"),
            ("audit_report", "20260311000012"),
        ):
            session.add(ReportSection(
                rcept_no=receipt,
                corp_code="001",
                bsns_year=2025,
                source_type=source_type,
                section_key="kam",
                section_title="핵심감사사항",
                body_text=(
                    "수익인식은 핵심감사사항으로 결정했습니다. "
                    "우리는 문서검사를 수행하였습니다."
                ),
                body_hash=f"kam-{source_type}",
                body_length=50,
                ordinal=0,
                fetched_at=datetime.utcnow(),
            ))

    for source_type in ("business_report", "all"):
        out = json.loads(call_tool(
            "get_audit_report_sections",
            {
                "company": "000001",
                "year": 2025,
                "section_key": "kam",
                "source_type": source_type,
            },
        ))
        assert out["data_quality"]["status"] == "limited"
        assert out["data_quality"]["semantic_complete"] is False
