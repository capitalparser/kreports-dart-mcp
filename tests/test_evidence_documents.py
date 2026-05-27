import json
from datetime import datetime

from kreports.db.models import (
    AccountingNoteChapter,
    AuditProcedureItem,
    Company,
    EvidenceDocument,
    ReportSection,
)
from kreports.mcp.tools import call_tool


def test_rebuild_evidence_documents_from_normalized_tables(temp_engine):
    from kreports.db.engine import get_session
    from kreports.maintenance.evidence_documents import rebuild_evidence_documents

    with get_session() as session:
        session.add(Company(
            corp_code="00000001",
            stock_code="000001",
            corp_name="근거문서테스트",
            market="KOSPI",
            induty_code="264",
        ))
        session.add(ReportSection(
            rcept_no="20250311000001",
            dcm_no="100",
            corp_code="00000001",
            bsns_year=2024,
            source_type="audit_report",
            section_key="kam",
            section_title="수익인식",
            body_text="수익인식은 핵심감사사항입니다.",
            body_hash="x",
            body_length=20,
            ordinal=0,
            fetched_at=datetime.utcnow(),
        ))
        session.add(AuditProcedureItem(
            rcept_no="20250311000001",
            dcm_no="100",
            corp_code="00000001",
            bsns_year=2024,
            source_type="audit_report",
            kam_topic="revenue",
            procedure_type="substantive",
            procedure_text="매출 거래 표본에 대해 증빙 대사를 수행하였습니다.",
            procedure_hash="y",
            procedure_length=30,
            section_ordinal=0,
            procedure_ordinal=0,
            fetched_at=datetime.utcnow(),
        ))

    result = rebuild_evidence_documents(year=2024, corp_code="00000001")

    assert result["documents"] == 1
    assert result["rows_used"] == 2
    with get_session() as session:
        doc = session.query(EvidenceDocument).one()
        assert doc.source_type == "audit_report"
        assert "## report_section/kam: 수익인식" in doc.normalized_text
        assert "## audit_procedure/revenue/substantive" in doc.normalized_text
        assert doc.text_length == len(doc.normalized_text)


def test_search_dataset_evidence_documents_uses_compact_cache(temp_engine):
    from kreports.db.engine import get_session
    from kreports.maintenance.evidence_documents import rebuild_evidence_documents

    with get_session() as session:
        session.add(Company(
            corp_code="00000002",
            stock_code="000002",
            corp_name="정책근거테스트",
            market="KOSPI",
            induty_code="264",
        ))
        session.add(AccountingNoteChapter(
            corp_code="00000002",
            bsns_year=2024,
            fs_div="CFS",
            rcept_no="20250311000002",
            dcm_no="200",
            source_type="business_report",
            note_no="3",
            note_title="중요한 회계정책",
            section_type="policy",
            body="수익은 수행의무가 이행되는 시점에 인식합니다.",
            body_hash="x",
            body_length=30,
            fetched_at=datetime.utcnow(),
        ))

    rebuild_evidence_documents(year=2024, corp_code="00000002")

    out = json.loads(call_tool(
        "search_dataset",
        {
            "dataset": "evidence_documents",
            "company": "000002",
            "year": 2024,
            "keyword": "수행의무",
            "limit": 5,
        },
    ))

    assert out["query"]["dataset"] == "evidence_documents"
    assert out["data_quality"]["source"] == "evidence_documents"
    assert "raw DART XML/HTML" in out["data_quality"]["interpretation"]
    record = out["companies"][0]["records"][0]
    assert record["source_type"] == "business_report"
    assert record["source_count"] == 1
    assert "수행의무" in record["body_excerpt"]


def test_search_dataset_returns_externalized_evidence_metadata(temp_engine):
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(
            corp_code="00000005",
            stock_code="000005",
            corp_name="외부근거테스트",
            market="KOSPI",
            induty_code="264",
        ))
        session.add(EvidenceDocument(
            corp_code="00000005",
            bsns_year=2024,
            source_type="business_report",
            rcept_no="20250331000005",
            evidence_scope="accounting_policy",
            title="회계정책",
            normalized_text="수익인식 회계정책 excerpt",
            text_hash="hash",
            text_length=12,
            source_count=1,
            full_text_uri="gs://bucket/evidence.txt.gz",
            full_text_hash="fullhash",
            full_text_length=5000,
            full_text_compressed_length=100,
            full_text_storage_status="externalized",
            generated_at=datetime.utcnow(),
        ))

    out = json.loads(call_tool(
        "search_dataset",
        {
            "dataset": "evidence_documents",
            "company": "000005",
            "year": 2024,
            "keyword": "수익인식",
            "limit": 1,
        },
    ))

    record = out["companies"][0]["records"][0]
    assert record["body_excerpt"] == "수익인식 회계정책 excerpt"
    assert record["full_text_uri"] == "gs://bucket/evidence.txt.gz"
    assert record["full_text_available"] is True
    assert record["full_text_length"] == 5000
    assert record["text_storage_status"] == "externalized"


def test_trim_evidence_documents_prunes_years_and_caps_text(temp_engine):
    from kreports.db.engine import get_session
    from kreports.maintenance.evidence_documents import trim_evidence_documents

    long_text = "가" * 200
    with get_session() as session:
        session.add(EvidenceDocument(
            corp_code="00000001",
            bsns_year=2023,
            source_type="audit_report",
            rcept_no="20240301000001",
            evidence_scope="auditor_view",
            normalized_text="old",
            text_hash="old",
            text_length=3,
            source_count=1,
        ))
        session.add(EvidenceDocument(
            corp_code="00000001",
            bsns_year=2025,
            source_type="audit_report",
            rcept_no="20260301000001",
            evidence_scope="auditor_view",
            normalized_text=long_text,
            text_hash="x",
            text_length=len(long_text),
            source_count=1,
        ))

    result = trim_evidence_documents(year_from=2024, year_to=2025, max_text_chars=50)

    assert result["deleted"] == 1
    assert result["trimmed"] == 1
    with get_session() as session:
        docs = session.query(EvidenceDocument).all()
        assert len(docs) == 1
        assert docs[0].bsns_year == 2025
        assert len(docs[0].normalized_text) < 70
        assert docs[0].normalized_text.endswith("(truncated)")


def test_rebuild_evidence_documents_preserves_auditor_priority_blocks_when_capped(temp_engine):
    from kreports.db.engine import get_session
    from kreports.maintenance.evidence_documents import rebuild_evidence_documents

    with get_session() as session:
        session.add(Company(
            corp_code="00000003",
            stock_code="000003",
            corp_name="우선순위근거테스트",
            market="KOSPI",
            induty_code="264",
        ))
        session.add(ReportSection(
            rcept_no="20260311000003",
            dcm_no="300",
            corp_code="00000003",
            bsns_year=2025,
            source_type="audit_report",
            section_key="audit_opinion",
            section_title="감사의견",
            body_text="감사의견 일반 문단 " + ("가" * 600),
            body_hash="opinion",
            body_length=610,
            ordinal=0,
            fetched_at=datetime.utcnow(),
        ))
        session.add(ReportSection(
            rcept_no="20260311000003",
            dcm_no="300",
            corp_code="00000003",
            bsns_year=2025,
            source_type="audit_report",
            section_key="kam",
            section_title="수익인식",
            body_text=(
                "수익인식은 핵심감사사항입니다. 핵심감사사항으로 선정한 이유는 거래조건 판단이 중요하기 때문입니다. "
                "우리는 매출 관련 내부통제 이해 및 평가와 표본 문서검사를 수행하였습니다."
            ),
            body_hash="kam",
            body_length=120,
            ordinal=1,
            fetched_at=datetime.utcnow(),
        ))
        session.add(AuditProcedureItem(
            rcept_no="20260311000003",
            dcm_no="300",
            corp_code="00000003",
            bsns_year=2025,
            source_type="audit_report",
            kam_topic="revenue",
            procedure_type="internal_control",
            procedure_text="매출 관련 내부통제 이해 및 평가를 수행하였습니다.",
            procedure_hash="proc",
            procedure_length=30,
            section_ordinal=1,
            procedure_ordinal=0,
            fetched_at=datetime.utcnow(),
        ))

    result = rebuild_evidence_documents(year=2025, corp_code="00000003", max_text_chars=420)

    assert result["documents"] == 1
    with get_session() as session:
        doc = session.query(EvidenceDocument).one()
        assert len(doc.normalized_text) <= 450
        assert "## report_section/kam: 수익인식" in doc.normalized_text
        assert "핵심감사사항으로 선정한 이유" in doc.normalized_text
        assert "## audit_procedure/revenue/internal_control" in doc.normalized_text
        assert "감사의견 일반 문단" not in doc.normalized_text
        assert doc.normalized_text.endswith("(truncated)")


def test_rebuild_evidence_documents_merges_existing_evidence_when_sections_missing(temp_engine):
    from kreports.db.engine import get_session
    from kreports.maintenance.evidence_documents import rebuild_evidence_documents

    with get_session() as session:
        session.add(EvidenceDocument(
            corp_code="00000004",
            bsns_year=2025,
            source_type="audit_report",
            rcept_no="20260311000004_00761",
            dcm_no="00761",
            evidence_scope="auditor_view",
            title="2025 audit_report evidence",
            normalized_text=(
                "# Evidence document\n"
                "- corp_code: 00000004\n"
                "- bsns_year: 2025\n"
                "- source_type: audit_report\n"
                "- rcept_no: 20260311000004_00761\n\n"
                "## report_section/kam: 수익인식\n"
                "수익인식은 핵심감사사항입니다."
            ),
            text_hash="old",
            text_length=160,
            source_count=1,
        ))
        session.add(AuditProcedureItem(
            rcept_no="20260311000004_00761",
            dcm_no="00761",
            corp_code="00000004",
            bsns_year=2025,
            source_type="audit_report",
            kam_topic="revenue",
            procedure_type="substantive_test",
            procedure_text="거래 문서검사와 세금계산서 대사를 수행하였습니다.",
            procedure_hash="proc",
            procedure_length=30,
            section_ordinal=0,
            procedure_ordinal=0,
            fetched_at=datetime.utcnow(),
        ))

    result = rebuild_evidence_documents(year=2025, corp_code="00000004")

    assert result["documents"] == 1
    with get_session() as session:
        doc = session.query(EvidenceDocument).one()
        assert "## report_section/kam: 수익인식" in doc.normalized_text
        assert "## audit_procedure/revenue/substantive_test" in doc.normalized_text
        assert doc.source_count == 2
