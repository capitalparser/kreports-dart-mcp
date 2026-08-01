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
            ),
            ReportSection(
                rcept_no="20250301000002", corp_code="00000001", bsns_year=2024,
                source_type="audit_report", section_key="kam",
                section_title="핵심감사사항", body_text="수익인식 검토", ordinal=0,
            ),
            AccountingNoteChapter(
                corp_code="00000001", bsns_year=2024, fs_div="CFS", rcept_no="20250301000001",
                source_type="business_report", note_no="10", note_title="리스", section_type="policy",
                body="리스부채를 측정합니다.",
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
    assert context["business_report"][0]["source_document_id"] is not None
    assert context["audit_report"][0]["section_key"] == "kam"
    assert context["notes"][0]["topic"] == "leases"
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
