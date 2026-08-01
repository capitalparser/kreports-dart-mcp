from __future__ import annotations

from datetime import date


def test_company_context_composes_local_evidence_buckets_read_only(temp_engine):
    from kreports.analysis.semantic_index import build_company_context
    from kreports.db.engine import get_session
    from kreports.db.models import (
        AccountingNoteChapter,
        Company,
        Disclosure,
        EvidenceDocument,
        Financial,
        ReportSection,
        SourceDocument,
    )

    with get_session() as session:
        session.add_all([
            Company(corp_code="00000001", stock_code="000001", corp_name="Context Corp", induty_code="26410"),
            SourceDocument(
                rcept_no="20250301000001", corp_code="00000001", bsns_year=2024,
                source_type="business_report", report_nm="사업보고서", raw_content="<xml/>", doc_hash="a" * 40,
            ),
            SourceDocument(
                rcept_no="20250301000002", corp_code="00000001", bsns_year=2024,
                source_type="audit_report", report_nm="감사보고서", raw_content="<xml/>", doc_hash="b" * 40,
            ),
            ReportSection(
                rcept_no="20250301000001", corp_code="00000001", bsns_year=2024,
                source_type="business_report", section_key="business_description",
                section_title="사업의 내용", body_text="반도체 장비를 공급합니다.", ordinal=0,
                full_text_uri="raw://reports/business/1", full_text_hash="c" * 40,
                full_text_length=500, full_text_storage_status="externalized",
            ),
            ReportSection(
                rcept_no="20250301000002", corp_code="00000001", bsns_year=2024,
                source_type="audit_report", section_key="kam",
                section_title="핵심감사사항", body_text="수익인식 검토", ordinal=0,
            ),
            AccountingNoteChapter(
                corp_code="00000001", bsns_year=2024, fs_div="CFS", rcept_no="20250301000001",
                source_type="business_report", note_no="10", note_title="리스", section_type="policy",
                body="리스부채를 측정합니다.", full_text_uri="raw://notes/10",
                full_text_hash="d" * 40, full_text_length=400, full_text_storage_status="externalized",
            ),
            EvidenceDocument(
                corp_code="00000001", bsns_year=2024, source_type="business_report",
                rcept_no="20250301000001", evidence_scope="auditor_view", normalized_text="정규화 증빙", source_count=1,
            ),
            Disclosure(
                rcept_no="20250401000001", corp_code="00000001", corp_name="Context Corp",
                disc_date=date(2024, 4, 1), disc_type="A", report_nm="사업보고서", flr_nm="Context Corp",
            ),
            Financial(corp_code="00000001", year=2024, quarter=4, fs_div="CFS", revenue=100, total_assets=200),
        ])

    context = build_company_context("00000001", 2024, read_engine=temp_engine)

    assert context["subject"]["corp_code"] == "00000001"
    assert context["read_only"] is True
    assert context["availability"] == {
        "business_report": "available",
        "audit_report": "available",
        "notes": "available",
        "evidence_documents": "available",
        "disclosures": "available",
        "financials": "available",
    }
    business = context["business_report"][0]
    assert business["source_document_id"] is not None
    assert business["full_text_uri"] == "raw://reports/business/1"
    assert business["full_text_hash"] == "c" * 40
    assert business["full_text_length"] == 500
    assert business["full_text_storage_status"] == "externalized"
    assert business["availability"] == "summary_only"
    assert business["source_locator"].startswith("report_sections:")
    assert ":20250301000001:business_description:0" in business["source_locator"]
    assert context["audit_report"][0]["section_key"] == "kam"
    note = context["notes"][0]
    assert note["topic"] == "leases"
    assert note["full_text_uri"] == "raw://notes/10"
    assert note["availability"] == "summary_only"
    assert context["financials"][0]["source_locator"] == "financials:00000001:2024:CFS:Q4"


def test_company_context_returns_explicit_unavailable_buckets(temp_engine):
    from kreports.analysis.semantic_index import build_company_context
    from kreports.db.engine import get_session
    from kreports.db.models import Company

    with get_session() as session:
        session.add(Company(corp_code="00000001", corp_name="Empty Corp", induty_code="26410"))

    context = build_company_context("00000001", 2024, topics=["risks"], read_engine=temp_engine)

    assert all(status == "unavailable" for status in context["availability"].values())
    assert context["business_report"] == []
    assert context["topics_requested"] == ["risks"]


def test_company_context_accounting_policy_topic_does_not_include_other_notes(temp_engine):
    from kreports.analysis.semantic_index import build_company_context
    from kreports.db.engine import get_session
    from kreports.db.models import AccountingNoteChapter, Company

    with get_session() as session:
        session.add_all([
            Company(corp_code="00000001", corp_name="Notes Corp", induty_code="26410"),
            AccountingNoteChapter(
                corp_code="00000001", bsns_year=2024, fs_div="CFS", rcept_no="20250301000001",
                source_type="business_report", note_no="1", note_title="중요한 회계정책",
                section_type="policy", body="수익 인식 회계정책입니다.",
            ),
            AccountingNoteChapter(
                corp_code="00000001", bsns_year=2024, fs_div="CFS", rcept_no="20250301000001",
                source_type="business_report", note_no="2", note_title="유형자산",
                section_type="other_note", body="유형자산을 설명합니다.",
            ),
        ])

    context = build_company_context(
        "00000001", 2024, topics=["accounting_policies"], read_engine=temp_engine
    )

    assert [item["note_no"] for item in context["notes"]] == ["1"]
    assert context["notes"][0]["topic"] == "accounting_policies"
