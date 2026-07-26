from datetime import datetime

from kreports.analysis.audit_procedure_evidence import (
    build_audit_procedure_evidence_map,
    classify_audit_procedure_linkages,
    link_procedure_evidence,
)
from kreports.db.engine import get_session
from kreports.db.models import AuditProcedureItem, Company, KamItem, ReportSection
from kreports.processor.audit_procedure_parser import ParsedProcedureStep
from kreports.semantic.metrics import METRICS


def test_link_procedure_evidence_has_complete_navigation_metadata():
    step = ParsedProcedureStep(
        ordinal=0,
        procedure_text="매출 계약서를 검사하고 기간귀속을 테스트하였습니다.",
        method="cutoff_test",
        assertion_hints=("cutoff", "occurrence"),
        source_start=0,
        source_end=29,
        source_kam_ordinal=1,
        source_kam_hash="a" * 40,
        procedure_hash="b" * 40,
    )

    links = link_procedure_evidence(step, METRICS)

    metric = next(row for row in links if row.category == "metric")
    assert metric.key == "revenue"
    assert metric.label == "매출액"
    assert metric.matching_phrase in step.procedure_text
    assert metric.confidence_basis == "explicit_keyword_registry_match"
    assert metric.key in METRICS


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
    assert ("financial_statement_account", "assets") in keys
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


def test_evidence_map_uses_full_body_denominator_and_reports_other_gaps(temp_engine):
    with get_session() as session:
        session.add(Company(corp_code="00126380", stock_code="005930", corp_name="삼성전자", market="KOSPI"))
        statuses = ["full_body", "full_body", "summary_only", "missing", "error"]
        for ordinal, status in enumerate(statuses, start=1):
            item = KamItem(
                rcept_no=f"2026030100000{ordinal}_100",
                corp_code="00126380",
                bsns_year=2025,
                source_type="audit_report",
                ordinal=ordinal,
                title=f"KAM {ordinal}",
                normalized_topic="revenue",
                reason_text="위험",
                audit_response_text=(
                    "계약서를 검사하였습니다." if status == "full_body" else None
                ),
                related_note_references_json="[]",
                full_body_hash=str(ordinal) * 40,
                full_body_length=500 if status == "full_body" else 20,
                source_basis="source_documents.full_body",
                parser_version="kam.v1",
                quality_status=status,
                fetched_at=datetime(2026, 3, 1),
            )
            session.add(item)
            session.flush()
            if ordinal == 1:
                session.add(
                    AuditProcedureItem(
                        kam_item_id=item.id,
                        corp_code="00126380",
                        bsns_year=2025,
                        rcept_no=item.rcept_no,
                        source_type="audit_report",
                        section_ordinal=ordinal,
                        kam_topic="revenue",
                        method="inspection",
                        procedure_type="substantive_test",
                        procedure_text="계약서를 검사하였습니다.",
                        procedure_hash="z" * 40,
                        procedure_ordinal=1,
                        parser_version="audit_procedure.v1",
                        quality_status="full_body",
                    )
                )

    result = build_audit_procedure_evidence_map(year=2025, limit=10)

    assert result["counts"]["full_body_kam_items"] == 2
    assert result["counts"]["full_body_kam_items_with_procedures"] == 1
    assert result["rates"]["procedure_coverage"] == 50.0
    assert result["quality_gaps"] == {
        "summary_only": 1,
        "missing": 1,
        "error": 1,
    }
    assert [row["rcept_no"] for row in result["missing_procedure_kams"]] == [
        "20260301000002_100"
    ]


def test_evidence_map_uses_receipt_denominator_and_eighty_percent_target(
    temp_engine,
):
    with get_session() as session:
        session.add(
            Company(
                corp_code="00126380",
                stock_code="005930",
                corp_name="삼성전자",
                market="KOSPI",
            )
        )
        for receipt in range(1, 6):
            for matter in range(1, 3 if receipt == 1 else 2):
                item = KamItem(
                    rcept_no=f"R{receipt}",
                    corp_code="00126380",
                    bsns_year=2025,
                    source_type="audit_report",
                    ordinal=matter,
                    title="수익인식",
                    normalized_topic="revenue",
                    reason_text="위험",
                    audit_response_text="계약서를 검사하였습니다.",
                    related_note_references_json="[]",
                    full_body_hash=f"{receipt}{matter}".ljust(40, "0"),
                    full_body_length=500,
                    source_basis="source_documents.full_body",
                    parser_version="kam.v1",
                    quality_status="full_body",
                    fetched_at=datetime(2026, 3, 1),
                )
                session.add(item)
                session.flush()
                if receipt <= 4:
                    session.add(
                        AuditProcedureItem(
                            kam_item_id=item.id,
                            rcept_no=item.rcept_no,
                            corp_code=item.corp_code,
                            bsns_year=2025,
                            source_type="audit_report",
                            section_ordinal=matter,
                            procedure_ordinal=1,
                            procedure_type="substantive_test",
                            method="inspection",
                            procedure_text="계약서를 검사하였습니다.",
                        )
                    )

    result = build_audit_procedure_evidence_map(year=2025)

    assert result["counts"]["full_body_kam_receipts"] == 5
    assert result["counts"]["full_body_kam_receipts_with_procedures"] == 4
    assert result["rates"]["procedure_coverage"] == 80.0
    assert result["verdict"] == "pass"
