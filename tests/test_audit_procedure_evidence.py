from datetime import datetime

from kreports.analysis.audit_procedure_evidence import (
    build_audit_procedure_evidence_map,
    classify_audit_procedure_linkages,
)
from kreports.db.engine import get_session
from kreports.db.models import AuditProcedureItem, Company, ReportSection


def test_classify_audit_procedure_linkages_maps_procedure_to_accounts_and_notes():
    rows = classify_audit_procedure_linkages(
        "매출 관련 내부통제 이해 및 평가, 계약서 문서검사, 기간귀속 테스트를 수행하였습니다.",
        kam_topic="revenue",
    )

    keys = {(row["category"], row["key"]) for row in rows}
    assert ("audit_report_kam", "revenue") in keys
    assert ("financial_statement_account", "revenue") in keys
    assert ("accounting_note", "revenue_policy") in keys


def test_classify_audit_procedure_linkages_maps_impairment_to_valuation_evidence():
    rows = classify_audit_procedure_linkages(
        "손상검사에 사용된 미래현금흐름과 할인율의 합리성을 평가하고 민감도 분석을 수행하였습니다.",
        kam_topic="impairment",
    )

    keys = {(row["category"], row["key"]) for row in rows}
    assert ("audit_report_kam", "impairment") in keys
    assert ("financial_statement_account", "impairment") in keys
    assert ("accounting_note", "impairment_assumption") in keys


def test_build_audit_procedure_evidence_map_reports_short_kam_and_linkages(temp_engine):
    with get_session() as session:
        session.add(Company(corp_code="00126380", stock_code="005930", corp_name="삼성전자", market="KOSPI"))
        session.add(
            ReportSection(
                corp_code="00126380",
                bsns_year=2025,
                rcept_no="20260301000001_100",
                dcm_no="100",
                source_type="audit_report",
                section_key="kam",
                section_title="핵심감사사항",
                body_text="핵심감사사항은 우리의 전문가적 판단에 따라 당기",
                body_length=26,
                ordinal=1,
                fetched_at=datetime(2026, 3, 1),
            )
        )
        session.add(
            AuditProcedureItem(
                corp_code="00126380",
                bsns_year=2025,
                rcept_no="20260301000001_100",
                dcm_no="100",
                source_type="audit_report",
                section_ordinal=1,
                kam_topic="revenue",
                procedure_type="substantive_test",
                procedure_text="매출 계약서 문서검사와 기간귀속 테스트를 수행하였습니다.",
                procedure_hash="p1",
                procedure_length=32,
                procedure_ordinal=1,
                fetched_at=datetime(2026, 3, 1),
            )
        )

    result = build_audit_procedure_evidence_map(year=2025, company="005930", limit=10)

    assert result["verdict"] == "fail"
    assert result["counts"]["kam_sections"] == 1
    assert result["counts"]["short_kam_sections"] == 1
    assert result["counts"]["procedure_items"] == 1
    assert result["samples"][0]["linkages"][0]["category"] == "audit_report_kam"
    assert "short_kam_body" in result["required_gaps"]


def test_build_audit_procedure_evidence_map_separates_totals_from_sample_limit(temp_engine):
    with get_session() as session:
        session.add(Company(corp_code="00126380", stock_code="005930", corp_name="삼성전자", market="KOSPI"))
        session.add(Company(corp_code="00164779", stock_code="000660", corp_name="SK하이닉스", market="KOSPI"))
        for idx, corp_code in enumerate(["00126380", "00164779"], start=1):
            session.add(
                ReportSection(
                    corp_code=corp_code,
                    bsns_year=2025,
                    rcept_no=f"2026030100000{idx}_100",
                    dcm_no="100",
                    source_type="audit_report",
                    section_key="kam",
                    section_title="핵심감사사항",
                    body_text="핵심감사사항은 우리의 전문가적 판단에 따라 당기",
                    body_length=26,
                    ordinal=1,
                    fetched_at=datetime(2026, 3, 1),
                )
            )
        for ordinal, topic in enumerate(["revenue", "inventory", "impairment"], start=1):
            session.add(
                AuditProcedureItem(
                    corp_code="00126380",
                    bsns_year=2025,
                    rcept_no="20260301000001_100",
                    dcm_no="100",
                    source_type="audit_report",
                    section_ordinal=1,
                    kam_topic=topic,
                    procedure_type="substantive_test",
                    procedure_text="매출 계약서 문서검사와 기간귀속 테스트를 수행하였습니다.",
                    procedure_hash=f"p{ordinal}",
                    procedure_length=32,
                    procedure_ordinal=ordinal,
                    fetched_at=datetime(2026, 3, 1),
                )
            )

    result = build_audit_procedure_evidence_map(year=2025, limit=1)

    assert result["counts"]["kam_sections"] == 2
    assert result["counts"]["procedure_items"] == 3
    assert result["sample"]["kam_sections"] == 1
