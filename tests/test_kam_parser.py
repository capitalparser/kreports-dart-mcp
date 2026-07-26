from pathlib import Path
from datetime import datetime

from typer.testing import CliRunner

from kreports.cli.main import app
from kreports.db.models import (
    Company,
    EvidenceDocument,
    KamItem,
    ReportDocument,
    ReportSection,
    SourceDocument,
)


FIXTURE = Path(__file__).parent / "fixtures" / "audit_report_multi_kam.xml"


def test_multi_kam_parser_separates_reason_response_and_notes():
    from kreports.processor.kam_parser import extract_kam_items

    items = extract_kam_items(FIXTURE.read_text(encoding="utf-8"))

    assert len(items) == 2
    assert items[0].ordinal == 1
    assert items[0].title == "수익인식"
    assert "핵심감사사항으로 결정" in items[0].reason_text
    assert "표본" in items[0].audit_response_text
    assert items[0].related_note_references == ["주석 25"]
    assert items[0].quality_status == "full_body"
    assert "감사인의 책임" not in items[-1].full_body


def test_parser_handles_whitespace_split_and_english_response_heading():
    from kreports.processor.kam_parser import extract_kam_items

    body = """
    핵 심 감 사 사 항
    1. 매 출 인 식
    핵 심 감 사 사 항 으 로 선 정 한 이 유
    주 석 1 5의 기간귀속 판단 때문에 핵심감사사항으로 결정하였습니다.
    How the matter was addressed in the audit
    표본 계약을 검사하고 매출 기간귀속을 재수행하였습니다.
    재 무 제 표 감 사 에 대 한 감 사 인 의 책 임
    일반적인 감사인의 책임 문단입니다.
    """

    items = extract_kam_items(body)

    assert len(items) == 1
    assert items[0].title == "매출인식"
    assert items[0].normalized_topic == "revenue"
    assert "기간귀속 판단" in items[0].reason_text
    assert "표본 계약" in items[0].audit_response_text
    assert items[0].related_note_references == ["주석 15"]
    assert "일반적인 감사인의 책임" not in items[0].full_body


def test_parser_recognizes_unnumbered_english_child_heading():
    from kreports.processor.kam_parser import extract_kam_items

    body = """
    <TITLE>Key Audit Matters</TITLE>
    <TITLE>Goodwill impairment</TITLE>
    <P>Why the matter was considered significant</P>
    <P>The valuation depends on material cash-flow and discount-rate assumptions.</P>
    <P>Audit response</P>
    <P>We tested the model and compared the discount rate with market evidence.</P>
    <TITLE>Auditor's Responsibilities for the Audit of the Financial Statements</TITLE>
    <P>Generic responsibilities follow.</P>
    """

    items = extract_kam_items(body)

    assert len(items) == 1
    assert items[0].title == "Goodwill impairment"
    assert items[0].normalized_topic == "impairment"
    assert "cash-flow" in items[0].reason_text
    assert "market evidence" in items[0].audit_response_text


def test_rebuild_prefers_exact_receipt_source_document_and_dry_run_writes_nothing(
    temp_engine,
):
    from kreports.collector.report_document_collector import rebuild_kam_items
    from kreports.db.engine import get_session

    raw_body = FIXTURE.read_text(encoding="utf-8")
    with get_session() as session:
        session.add(
            Company(
                corp_code="00126380",
                stock_code="005930",
                corp_name="원문회사",
                market="KOSPI",
            )
        )
        session.add(
            SourceDocument(
                rcept_no="20250318000001",
                dcm_no="100",
                corp_code="00126380",
                bsns_year=2024,
                source_type="audit_report",
                report_nm="감사보고서",
                content_type="xml",
                raw_content=raw_body,
                doc_hash="1" * 40,
                storage_status="inline",
            )
        )
        session.add(
            EvidenceDocument(
                corp_code="00126380",
                bsns_year=2024,
                source_type="audit_report",
                rcept_no="20250318000001",
                dcm_no="100",
                evidence_scope="auditor_view",
                title="낮은 우선순위 증거",
                normalized_text=raw_body.replace("수익인식", "영업권 손상"),
                source_count=1,
            )
        )

    result = rebuild_kam_items(year=2024, market="KOSPI", dry_run=True)

    assert result["total"] == 1
    assert result["full_body"] == 1
    assert result["summary_only"] == 0
    assert result["missing"] == 0
    assert result["error"] == 0
    assert result["receipt_counts"]["full_body"] == 1
    assert result["item_counts"]["full_body"] == 2
    assert result["items_total"] == 2
    assert result["rows_written"] == 0
    assert result["receipts"][0]["rcept_no"] == "20250318000001"
    assert result["receipts"][0]["source_basis"] == "source_documents.raw_body"
    assert result["receipts"][0]["titles"] == ["수익인식", "재고자산 평가"]
    with get_session() as session:
        assert session.query(KamItem).count() == 0


def test_rebuild_continues_from_failed_raw_read_to_normalized_evidence(
    temp_engine,
):
    from kreports.collector.report_document_collector import rebuild_kam_items
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(
            Company(
                corp_code="00164779",
                stock_code="035420",
                corp_name="폴백회사",
                market="KOSPI",
            )
        )
        session.add(
            SourceDocument(
                rcept_no="20250318000002",
                dcm_no="200",
                corp_code="00164779",
                bsns_year=2024,
                source_type="audit_report",
                report_nm="감사보고서",
                content_type="xml",
                raw_content="",
                doc_hash="2" * 40,
                storage_uri="raw://missing/20250318000002.xml.gz",
                storage_status="externalized",
            )
        )
        session.add(
            EvidenceDocument(
                corp_code="00164779",
                bsns_year=2024,
                source_type="audit_report",
                rcept_no="20250318000002",
                dcm_no="200",
                evidence_scope="auditor_view",
                title="정규화 증거",
                normalized_text=FIXTURE.read_text(encoding="utf-8"),
                source_count=1,
            )
        )

    result = rebuild_kam_items(year=2024, dry_run=True)

    receipt = result["receipts"][0]
    assert receipt["source_basis"] == "evidence_documents.normalized_text"
    assert receipt["item_count"] == 2
    assert any(
        limitation.startswith("source_documents.raw_body:read_error:")
        for limitation in receipt["limitations"]
    )


def test_rebuild_fails_closed_when_exact_receipt_candidates_disagree(temp_engine):
    from kreports.collector.report_document_collector import rebuild_kam_items
    from kreports.db.engine import get_session

    body = FIXTURE.read_text(encoding="utf-8")
    with get_session() as session:
        session.add(
            Company(
                corp_code="00000041",
                stock_code="000041",
                corp_name="불일치회사",
                market="KOSPI",
            )
        )
        session.add(
            SourceDocument(
                rcept_no="20250318000041",
                dcm_no="410",
                corp_code="00000041",
                bsns_year=2024,
                source_type="audit_report",
                report_nm="감사보고서",
                content_type="xml",
                raw_content=body,
                doc_hash="e" * 40,
                storage_status="inline",
            )
        )
        session.add(
            EvidenceDocument(
                corp_code="00000041",
                bsns_year=2024,
                source_type="audit_report",
                rcept_no="20250318000041",
                dcm_no="DIFFERENT",
                evidence_scope="auditor_view",
                title="불일치 증거",
                normalized_text=body,
                source_count=1,
            )
        )

    result = rebuild_kam_items(year=2024, dry_run=True)

    assert result["full_body"] == 0
    assert result["error"] == 1
    receipt = result["receipts"][0]
    assert receipt["quality_status"] == "error"
    assert receipt["source_basis"] == "none"
    assert any(
        limitation.startswith("receipt_consistency_error:dcm_no:")
        for limitation in receipt["limitations"]
    )


def test_rebuild_prefers_evidence_full_text_uri_then_long_report_section(
    temp_engine,
):
    import kreports.collector.report_document_collector as collector_module
    from kreports.db.engine import get_session

    body = FIXTURE.read_text(encoding="utf-8")
    stored = collector_module.RawDocumentStore().write(
        corp_code="00000051",
        bsns_year=2024,
        source_type="audit_report",
        rcept_no="20250318000051",
        content_type="xml",
        content=body,
    )
    with get_session() as session:
        session.add_all(
            [
                Company(
                    corp_code="00000051",
                    stock_code="000051",
                    corp_name="URI회사",
                    market="KOSPI",
                ),
                Company(
                    corp_code="00000052",
                    stock_code="000052",
                    corp_name="긴섹션회사",
                    market="KOSPI",
                ),
                EvidenceDocument(
                    corp_code="00000051",
                    bsns_year=2024,
                    source_type="audit_report",
                    rcept_no="20250318000051",
                    evidence_scope="auditor_view",
                    title="URI 증거",
                    normalized_text=body.replace("수익인식", "낮은 우선순위"),
                    full_text_uri=stored.storage_uri,
                    full_text_hash=stored.doc_hash,
                    full_text_length=stored.content_length,
                    source_count=1,
                ),
                ReportSection(
                    rcept_no="20250318000051",
                    corp_code="00000051",
                    bsns_year=2024,
                    source_type="audit_report",
                    section_key="kam",
                    section_title="더 낮은 우선순위",
                    body_text=body.replace("수익인식", "섹션 제목"),
                    body_hash="f" * 40,
                    body_length=len(body),
                    ordinal=0,
                ),
                ReportSection(
                    rcept_no="20250318000052",
                    corp_code="00000052",
                    bsns_year=2024,
                    source_type="audit_report",
                    section_key="kam",
                    section_title="긴 KAM 본문",
                    body_text=body,
                    body_hash="0" * 40,
                    body_length=len(body),
                    ordinal=0,
                ),
            ]
        )

    result = collector_module.rebuild_kam_items(year=2024, dry_run=True)

    by_receipt = {row["rcept_no"]: row for row in result["receipts"]}
    assert (
        by_receipt["20250318000051"]["source_basis"]
        == "evidence_documents.full_text_uri"
    )
    assert by_receipt["20250318000051"]["titles"][0] == "수익인식"
    assert (
        by_receipt["20250318000052"]["source_basis"]
        == "report_sections.long_body"
    )
    assert result["receipt_counts"]["full_body"] == 2
    assert result["item_counts"]["full_body"] == 4


def test_rebuild_marks_short_derived_kam_summary_without_inferred_detail(
    temp_engine,
):
    from kreports.collector.report_document_collector import rebuild_kam_items
    from kreports.db.engine import get_session

    summary = "수익인식은 회사의 핵심감사사항입니다."
    with get_session() as session:
        session.add(
            Company(
                corp_code="00293886",
                stock_code="000660",
                corp_name="요약회사",
                market="KOSPI",
            )
        )
        session.add(
            ReportSection(
                rcept_no="20250318000003",
                dcm_no="300",
                corp_code="00293886",
                bsns_year=2024,
                source_type="audit_report",
                section_key="kam",
                section_title="요약 KAM",
                body_text=summary,
                body_hash="3" * 40,
                body_length=len(summary),
                ordinal=0,
            )
        )

    result = rebuild_kam_items(year=2024, dry_run=True)

    assert result["full_body"] == 0
    assert result["summary_only"] == 1
    receipt = result["receipts"][0]
    assert receipt["quality_status"] == "summary_only"
    assert receipt["source_basis"] == "report_sections.short_summary"
    assert receipt["titles"] == ["요약 KAM"]
    assert receipt["has_reason"] == [False]
    assert receipt["has_audit_response"] == [False]


def test_rebuild_reports_unreadable_receipt_as_error_and_absent_body_as_missing(
    temp_engine,
):
    from kreports.collector.report_document_collector import rebuild_kam_items
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add_all(
            [
                Company(
                    corp_code="00000011",
                    stock_code="000011",
                    corp_name="오류회사",
                    market="KOSPI",
                ),
                Company(
                    corp_code="00000012",
                    stock_code="000012",
                    corp_name="누락회사",
                    market="KOSPI",
                ),
                SourceDocument(
                    rcept_no="20250318000011",
                    corp_code="00000011",
                    bsns_year=2024,
                    source_type="audit_report",
                    report_nm="감사보고서",
                    content_type="xml",
                    raw_content="",
                    doc_hash="a" * 40,
                    storage_uri="raw://missing/error.xml.gz",
                    storage_status="externalized",
                ),
                ReportDocument(
                    rcept_no="20250318000012",
                    corp_code="00000012",
                    bsns_year=2024,
                    source_type="audit_report",
                    report_nm="감사보고서",
                    doc_hash="b" * 40,
                ),
            ]
        )

    result = rebuild_kam_items(year=2024, dry_run=True)

    assert result["error"] == 1
    assert result["missing"] == 1
    by_receipt = {row["rcept_no"]: row for row in result["receipts"]}
    assert by_receipt["20250318000011"]["quality_status"] == "error"
    assert by_receipt["20250318000012"]["quality_status"] == "missing"


def test_rebuild_persists_exact_provenance_idempotently(temp_engine):
    from kreports.collector.report_document_collector import rebuild_kam_items
    from kreports.db.engine import get_session

    body = FIXTURE.read_text(encoding="utf-8")
    with get_session() as session:
        session.add(
            Company(
                corp_code="00000021",
                stock_code="000021",
                corp_name="영속회사",
                market="KOSDAQ",
            )
        )
        session.add(
            SourceDocument(
                rcept_no="20250318000021",
                dcm_no="210",
                corp_code="00000021",
                bsns_year=2024,
                source_type="audit_report",
                report_nm="감사보고서",
                content_type="xml",
                raw_content=body,
                doc_hash="c" * 40,
                storage_status="inline",
                fetched_at=datetime(2025, 3, 18, 12, 34, 56),
            )
        )

    first = rebuild_kam_items(year=2024, market="KOSDAQ")
    second = rebuild_kam_items(year=2024, market="KOSDAQ")

    assert first["rows_written"] == 2
    assert second["rows_written"] == 2
    with get_session() as session:
        rows = session.query(KamItem).order_by(KamItem.ordinal).all()
        assert len(rows) == 2
        assert rows[0].rcept_no == "20250318000021"
        assert rows[0].dcm_no == "210"
        assert rows[0].corp_code == "00000021"
        assert rows[0].bsns_year == 2024
        assert rows[0].source_basis == "source_documents.raw_body"
        assert rows[0].fetched_at == datetime(2025, 3, 18, 12, 34, 56)
        assert rows[0].quality_status == "full_body"
        assert rows[0].reason_text
        assert rows[0].audit_response_text
        assert rows[0].related_note_references_json == '["주석 25"]'


def test_rebuild_kam_items_cli_dry_run_reports_quality_without_writes(temp_engine):
    from kreports.db.engine import get_session

    summary = "재고자산 평가는 핵심감사사항입니다."
    with get_session() as session:
        session.add(
            Company(
                corp_code="00000031",
                stock_code="000031",
                corp_name="CLI회사",
                market="KOSPI",
            )
        )
        session.add(
            ReportSection(
                rcept_no="20250318000031",
                corp_code="00000031",
                bsns_year=2024,
                source_type="audit_report",
                section_key="kam",
                section_title="재고자산 평가",
                body_text=summary,
                body_hash="d" * 40,
                body_length=len(summary),
                ordinal=0,
            )
        )

    result = CliRunner().invoke(
        app,
        ["rebuild-kam-items", "--year", "2024", "--dry-run"],
    )

    assert result.exit_code == 0
    assert "full_body=0" in result.stdout
    assert "summary_only=1" in result.stdout
    assert "missing=0" in result.stdout
    assert "error=0" in result.stdout
    with get_session() as session:
        assert session.query(KamItem).count() == 0


def test_rebuild_kam_items_cli_dry_run_does_not_create_schema_or_sidecars(
    tmp_path,
    monkeypatch,
):
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.orm import sessionmaker

    import kreports.db.engine as engine_module
    from kreports.db.models import Base

    db_path = tmp_path / "readonly-dry-run.db"
    isolated = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(isolated)
    isolated_session = sessionmaker(
        bind=isolated,
        autocommit=False,
        autoflush=False,
    )
    monkeypatch.setattr(engine_module, "engine", isolated)
    monkeypatch.setattr(engine_module, "SessionLocal", isolated_session)
    with isolated_session() as session:
        session.add(
            Company(
                corp_code="00000061",
                stock_code="000061",
                corp_name="읽기전용회사",
                market="KOSPI",
            )
        )
        session.add(
            ReportSection(
                rcept_no="20250318000061",
                corp_code="00000061",
                bsns_year=2024,
                source_type="audit_report",
                section_key="kam",
                section_title="읽기전용 요약",
                body_text="수익인식은 핵심감사사항입니다.",
                body_hash="6" * 40,
                body_length=17,
                ordinal=0,
            )
        )
        session.commit()
    with isolated.begin() as connection:
        connection.execute(text("DROP TABLE kam_items"))
    before_files = {path.name for path in tmp_path.iterdir()}

    result = CliRunner().invoke(
        app,
        ["rebuild-kam-items", "--year", "2024", "--dry-run"],
    )

    assert result.exit_code == 0
    assert "summary_only=1" in result.stdout
    assert "kam_items" not in inspect(isolated).get_table_names()
    assert {path.name for path in tmp_path.iterdir()} == before_files
