import json
from datetime import datetime

from kreports.analysis.api import (
    build_audit_acceptance_pack,
    compare_peer_accounting_policies,
    compare_peer_audit_fees,
    compare_peer_audit_procedures,
    compare_peer_audit_report_matters,
    compare_peer_kam_topics,
    compare_peer_risk_profile,
    estimate_audit_hours_proxy,
    search_dataset,
    search_audit_procedures,
    search_audit_report_matters,
)
from kreports.db.models import AccountingNoteChapter, AuditProcedureItem, Company, Financial, SourceDocument
from kreports.mcp.tools import call_tool


def test_compare_peer_audit_fees_real_db_shape():
    out = compare_peer_audit_fees("005930", year=2025, peer_limit=20)
    assert out["subject"]["corp_code"] == "00126380"
    assert out["year"] == 2025
    assert out["peer_count"] > 0
    assert "audit_fee_m" in out["subject_metrics"]
    assert "audit_fee_to_assets_bps" in out["benchmarks"]


def test_compare_peer_audit_fees_mcp_dispatch():
    out = json.loads(call_tool("compare_peer_audit_fees", {"company": "005930", "year": 2025}))
    assert out["_meta"]["tool"] == "compare_peer_audit_fees"
    assert out["peer_count"] > 0


def test_compare_peer_risk_profile_shape():
    out = compare_peer_risk_profile("005930", year=2025, peer_limit=20)
    assert out["subject"]["corp_code"] == "00126380"
    assert "receivables_to_revenue" in out["benchmarks"]
    assert "disclosure_event_counts" in out


def test_compare_peer_risk_profile_mcp_dispatch():
    out = json.loads(call_tool("compare_peer_risk_profile", {"company": "005930", "year": 2025}))
    assert out["_meta"]["tool"] == "compare_peer_risk_profile"
    assert out["peer_count"] > 0


def test_compare_peer_accounting_policies_shape():
    out = compare_peer_accounting_policies("005930", year=2025, peer_limit=20)
    assert out["subject"]["corp_code"] == "00126380"
    assert "subject_policy_count" in out
    assert "peer_item_coverage" in out
    assert "coverage_note" in out


def test_compare_peer_accounting_policies_mcp_dispatch():
    out = json.loads(call_tool("compare_peer_accounting_policies", {"company": "005930", "year": 2025}))
    assert out["_meta"]["tool"] == "compare_peer_accounting_policies"
    assert "peer_item_coverage" in out


def test_compare_peer_kam_topics_shape():
    out = compare_peer_kam_topics("005930", year=2025, peer_limit=20)
    assert out["subject"]["corp_code"] == "00126380"
    assert "audit_report_events" in out
    assert "kam_topics" in out
    assert out["limitations"]


def test_compare_peer_kam_topics_mcp_dispatch():
    out = json.loads(call_tool("compare_peer_kam_topics", {"company": "005930", "year": 2025}))
    assert out["_meta"]["tool"] == "compare_peer_kam_topics"
    assert "audit_report_events" in out


def test_compare_peer_audit_report_matters_shape():
    out = compare_peer_audit_report_matters("005930", year=2024, peer_limit=20)
    assert out["subject"]["corp_code"] == "00126380"
    assert "matter_counts" in out
    assert "other_matter" in out["matter_counts"]
    assert "emphasis" in out["matter_counts"]
    assert "going_concern" in out["matter_counts"]
    assert "subject_matters" in out
    assert "data_quality" in out


def test_compare_peer_audit_report_matters_mcp_dispatch():
    out = json.loads(call_tool("compare_peer_audit_report_matters", {"company": "005930", "year": 2024}))
    assert out["_meta"]["tool"] == "compare_peer_audit_report_matters"
    assert "matter_counts" in out


def test_search_audit_report_matters_company_question_shape():
    out = search_audit_report_matters(company="005930", year=2024, section_keys=["other_matter"], limit=20)
    assert out["query"]["company"] == "005930"
    assert out["query"]["year"] == 2024
    assert out["data_quality"]["source"] == "report_sections.audit_report"
    assert "companies" in out
    assert out["total_companies"] >= 0
    if out["companies"]:
        first = out["companies"][0]
        assert first["corp_code"] == "00126380"
        assert "matter_counts" in first
        assert "sections" in first


def test_search_audit_report_matters_industry_question_mcp_dispatch():
    out = json.loads(call_tool(
        "search_audit_report_matters",
        {
            "year": 2024,
            "market": "KOSPI",
            "induty_prefix": "26",
            "section_keys": ["emphasis", "other_matter"],
            "limit": 10,
        },
    ))
    assert out["_meta"]["tool"] == "search_audit_report_matters"
    assert out["query"]["year"] == 2024
    assert out["query"]["induty_prefix"] == "26"
    assert "companies" in out
    assert out["total_companies"] >= 0


def test_search_dataset_report_sections_shape():
    out = search_dataset(
        dataset="report_sections",
        company="005930",
        year=2024,
        source_type="audit_report",
        section_keys=["kam", "other_matter"],
        limit=10,
    )
    assert out["query"]["dataset"] == "report_sections"
    assert out["data_quality"]["source"] == "report_sections"
    assert "companies" in out
    if out["companies"]:
        assert out["companies"][0]["corp_code"] == "00126380"
        assert "records" in out["companies"][0]


def test_search_dataset_policy_and_structured_mcp_dispatch():
    policy = json.loads(call_tool(
        "search_dataset",
        {
            "dataset": "accounting_policies",
            "company": "005930",
            "year": 2025,
            "keyword": "수익",
            "limit": 5,
        },
    ))
    assert policy["_meta"]["tool"] == "search_dataset"
    assert policy["query"]["dataset"] == "accounting_policies"
    assert "companies" in policy

    fees = json.loads(call_tool(
        "search_dataset",
        {
            "dataset": "audit_fees",
            "year": 2025,
            "market": "KOSPI",
            "limit": 5,
        },
    ))
    assert fees["_meta"]["tool"] == "search_dataset"
    assert fees["query"]["dataset"] == "audit_fees"
    assert "companies" in fees


def test_search_dataset_accounting_note_chapters(temp_engine):
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(
            corp_code="00000001",
            stock_code="000001",
            corp_name="정책테스트",
            market="KOSPI",
            induty_code="264",
        ))
        session.add(AccountingNoteChapter(
            corp_code="00000001",
            bsns_year=2024,
            fs_div="CFS",
            rcept_no="20250311000001",
            source_type="business_report",
            note_no="3",
            note_title="중요한 회계정책",
            section_type="policy",
            body="수익은 고객과의 계약에서 수행의무가 이행될 때 인식한다.",
            body_hash="x",
            body_length=34,
            fetched_at=datetime.utcnow(),
        ))

    out = json.loads(call_tool(
        "search_dataset",
        {
            "dataset": "accounting_note_chapters",
            "company": "000001",
            "year": 2024,
            "section_type": "policy",
            "keyword": "수익",
            "limit": 5,
        },
    ))

    assert out["_meta"]["tool"] == "search_dataset"
    assert out["query"]["dataset"] == "accounting_note_chapters"
    assert out["query"]["section_type"] == "policy"
    assert out["data_quality"]["source"] == "accounting_note_chapters"
    assert out["companies"][0]["corp_code"] == "00000001"
    record = out["companies"][0]["records"][0]
    assert record["note_no"] == "3"
    assert record["note_title"] == "중요한 회계정책"
    assert record["section_type"] == "policy"
    assert "수익" in record["body_excerpt"]


def test_search_dataset_source_documents_marks_derived_evidence(temp_engine):
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(
            corp_code="00000001",
            stock_code="000001",
            corp_name="근거테스트",
            market="KOSPI",
            induty_code="264",
        ))
        session.add(SourceDocument(
            rcept_no="20250311000001",
            corp_code="00000001",
            bsns_year=2024,
            source_type="audit_report",
            report_nm="derived from report_sections",
            content_type="derived_report_sections",
            raw_content="DERIVED FROM report_sections\n## kam | 핵심감사사항\n수익인식 관련 핵심감사사항입니다.",
            doc_hash="x",
            fetched_at=datetime.utcnow(),
        ))

    out = json.loads(call_tool(
        "search_dataset",
        {
            "dataset": "source_documents",
            "company": "000001",
            "year": 2024,
            "source_type": "audit_report",
            "keyword": "수익인식",
            "limit": 5,
        },
    ))

    assert out["query"]["dataset"] == "source_documents"
    assert out["data_quality"]["source"] == "source_documents"
    assert "derived" in out["data_quality"]["interpretation"]
    record = out["companies"][0]["records"][0]
    assert record["content_type"] == "derived_report_sections"
    assert "수익인식" in record["body_excerpt"]


def test_search_audit_procedures_mcp_dispatch(temp_engine):
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(
            corp_code="00000001",
            stock_code="000001",
            corp_name="절차테스트",
            market="KOSPI",
            induty_code="264",
        ))
        session.add(AuditProcedureItem(
            rcept_no="20250311000001",
            corp_code="00000001",
            bsns_year=2024,
            source_type="audit_report",
            kam_topic="revenue",
            procedure_type="internal_control",
            procedure_text="매출차감 처리 관련 내부통제 이해 및 평가를 수행하였습니다.",
            procedure_hash="x",
            procedure_length=35,
            section_ordinal=0,
            procedure_ordinal=0,
            fetched_at=datetime.utcnow(),
        ))

    out = json.loads(call_tool(
        "search_audit_procedures",
        {
            "company": "000001",
            "year": 2024,
            "kam_topic": "revenue",
            "procedure_type": "internal_control",
            "keyword": "내부통제",
            "limit": 5,
        },
    ))

    assert out["_meta"]["tool"] == "search_audit_procedures"
    assert out["total_procedures"] == 1
    assert out["procedure_type_counts"]["internal_control"] == 1
    assert "내부통제" in out["companies"][0]["records"][0]["procedure_excerpt"]


def test_compare_peer_audit_procedures_shape(temp_engine):
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add_all([
            Company(corp_code="00000001", stock_code="000001", corp_name="대상", market="KOSPI", induty_code="264"),
            Company(corp_code="00000002", stock_code="000002", corp_name="피어", market="KOSPI", induty_code="264"),
        ])
        for cc in ("00000001", "00000002"):
            session.add(Financial(
                corp_code=cc,
                year=2024,
                quarter=4,
                fs_div="CFS",
                revenue=100,
                operating_profit=10,
                net_income=8,
                total_assets=1000,
                total_debt=400,
                total_equity=600,
            ))
            session.add(AuditProcedureItem(
                rcept_no=f"20250331{cc[-6:]}",
                corp_code=cc,
                bsns_year=2024,
                source_type="audit_report",
                kam_topic="revenue",
                procedure_type="substantive_test",
                procedure_text="매출 거래 근거 문서검사를 수행하였습니다.",
                procedure_hash=cc,
                procedure_length=24,
                section_ordinal=0,
                procedure_ordinal=0,
                fetched_at=datetime.utcnow(),
            ))

    out = compare_peer_audit_procedures("000001", year=2024, peer_limit=5)

    assert out["subject"]["corp_code"] == "00000001"
    assert out["data_quality"]["source"] == "audit_procedure_items"
    assert out["subject_procedure_type_counts"]["substantive_test"] == 1
    assert out["peer_procedure_type_counts"]["substantive_test"] == 1


def test_estimate_audit_hours_proxy_shape():
    out = estimate_audit_hours_proxy("005930", year=2025, peer_limit=20)
    assert out["subject"]["corp_code"] == "00126380"
    assert out["peer_count"] > 0
    assert "complexity_score" in out
    assert "drivers" in out
    assert "peer_benchmarks" in out
    assert all("score_after" in d for d in out["drivers"])


def test_estimate_audit_hours_proxy_mcp_dispatch():
    out = json.loads(call_tool("estimate_audit_hours_proxy", {"company": "005930", "year": 2025}))
    assert out["_meta"]["tool"] == "estimate_audit_hours_proxy"
    assert "complexity_score" in out


def test_build_audit_acceptance_pack_shape():
    out = build_audit_acceptance_pack("005930", year=2025, peer_limit=20)
    assert out["subject"]["corp_code"] == "00126380"
    assert "acceptance_signals" in out
    assert "data_quality" in out
    assert "kam_reason_coverage" in out["data_quality"]["kam_body"]
    assert "kam_procedure_coverage" in out["data_quality"]["kam_body"]
    assert "recommended_review_areas" in out
    assert out["scope"] == "external_dart_evidence_pack"
    assert "audit_report_sections" in out["kam_summary"]
    assert "subject_sections" in out["kam_summary"]
    assert "audit_report_matter_summary" in out
    assert "audit_report_matters" in out["data_quality"]


def test_build_audit_acceptance_pack_mcp_dispatch():
    out = json.loads(call_tool("build_audit_acceptance_pack", {"company": "005930", "year": 2025}))
    assert out["_meta"]["tool"] == "build_audit_acceptance_pack"
    assert "acceptance_signals" in out
