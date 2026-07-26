from datetime import datetime
from pathlib import Path

import pytest
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


def test_parser_merges_duplicate_wrapped_title_and_keeps_numbered_procedures():
    from kreports.processor.kam_parser import extract_kam_items

    body = """
    핵심감사사항
    2. 매출 및 수익 인식
    2. 매출 및
    수익 인식
    핵심감사사항으로 선정한 이유
    복합 계약의 기간귀속 판단에 유의적인 위험이 있습니다.
    감사인이 수행한 주요 절차
    1. 표본 계약서의 수행의무를 검사했습니다.
    2. 보고기간 전후 매출의 기간귀속을 재수행했습니다.
    3. 재고자산 평가
    핵심감사사항으로 결정한 이유
    순실현가능가치 추정에 유의적인 판단이 포함됩니다.
    감사에서 다루어진 방법
    1. 표본 재고의 예상판매가격을 검사했습니다.
    """

    items = extract_kam_items(body)

    assert len(items) == 2
    assert [item.ordinal for item in items] == [1, 2]
    assert items[0].title == "매출 및 수익 인식"
    assert "1. 표본 계약서" in items[0].audit_response_text
    assert "2. 보고기간 전후" in items[0].audit_response_text
    assert items[1].title == "재고자산 평가"
    assert "표본 재고" in items[1].audit_response_text


def test_parser_keeps_numbered_response_step_before_unnumbered_next_matter():
    from kreports.processor.kam_parser import extract_kam_items

    body = """
    핵심감사사항
    수익인식
    핵심감사사항으로 선정한 이유
    복합 계약의 기간귀속 판단에 유의적인 위험이 있습니다.
    감사인이 수행한 주요 절차
    1. 계약 표본 검사
    재고자산 평가
    핵심감사사항으로 결정한 이유
    순실현가능가치 추정에 유의적인 판단이 포함됩니다.
    감사에서 다루어진 방법
    표본 재고의 예상판매가격을 검사했습니다.
    """

    items = extract_kam_items(body)

    assert [item.title for item in items] == ["수익인식", "재고자산 평가"]
    assert "1. 계약 표본 검사" in items[0].audit_response_text
    assert "표본 재고" in items[1].audit_response_text


def test_parser_joins_wrapped_numbered_next_matter_inside_response_state():
    from kreports.processor.kam_parser import extract_kam_items

    body = """
    핵심감사사항
    1. 수익인식
    핵심감사사항으로 선정한 이유
    복합 계약의 기간귀속 판단에 유의적인 위험이 있습니다.
    감사인이 수행한 주요 절차
    1. 계약 표본 검사
    2. 재고자산
    평가
    핵심감사사항으로 결정한 이유
    순실현가능가치 추정에 유의적인 판단이 포함됩니다.
    감사에서 다루어진 방법
    표본 재고의 예상판매가격을 검사했습니다.
    """

    items = extract_kam_items(body)

    assert [item.title for item in items] == ["수익인식", "재고자산 평가"]
    assert items[0].audit_response_text == "1. 계약 표본 검사"
    assert "표본 재고" in items[1].audit_response_text


def test_parser_deduplicates_numbered_title_followed_by_same_unnumbered_title():
    from kreports.processor.kam_parser import extract_kam_items

    body = """
    핵심감사사항
    1. 수익인식
    수익인식
    핵심감사사항으로 선정한 이유
    기간귀속 판단에 중요한 왜곡표시위험이 있습니다.
    감사에서 다루어진 방법
    표본 계약서와 세금계산서를 대사했습니다.
    """

    items = extract_kam_items(body)

    assert len(items) == 1
    assert items[0].title == "수익인식"


def test_parser_separates_consecutive_unnumbered_matters():
    from kreports.processor.kam_parser import extract_kam_items

    body = """
    핵심감사사항
    수익인식
    핵심감사사항으로 선정한 이유
    기간귀속 판단에 중요한 왜곡표시위험이 있습니다.
    감사에서 다루어진 방법
    표본 계약서를 검사했습니다.
    재고자산 평가
    핵심감사사항으로 결정한 이유
    순실현가능가치 추정에 유의적인 판단이 포함됩니다.
    감사인이 수행한 주요 절차
    예상판매가격을 검사했습니다.
    """

    items = extract_kam_items(body)

    assert [item.title for item in items] == ["수익인식", "재고자산 평가"]
    assert "표본 계약서" in items[0].audit_response_text
    assert "예상판매가격" in items[1].audit_response_text


@pytest.mark.parametrize(
    "reason_heading",
    [
        "Why the matter was determined to be a key audit matter",
        (
            "Why the matter was considered to be one of the most "
            "significant matters in the audit"
        ),
    ],
)
def test_parser_supports_standard_english_reason_headings(reason_heading):
    from kreports.processor.kam_parser import extract_kam_items

    body = f"""
    Key Audit Matters
    1. Revenue recognition
    {reason_heading}
    Contract cut-off requires significant judgment.
    How the matter was addressed in the audit
    We tested a sample of contracts around year end.
    """

    items = extract_kam_items(body)

    assert len(items) == 1
    assert items[0].title == "Revenue recognition"
    assert "significant judgment" in items[0].reason_text
    assert "sample of contracts" in items[0].audit_response_text


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


def test_rebuild_falls_back_after_structured_raw_body_parse_error(temp_engine):
    from kreports.collector.report_document_collector import rebuild_kam_items
    from kreports.db.engine import get_session

    malformed = (
        "핵심감사사항\n1. 수익인식\n핵심감사사항으로 선정한 이유\n"
        "중요한 위험 설명만 있고 감사 대응 제목과 본문은 없습니다."
    )
    with get_session() as session:
        session.add_all(
            [
                Company(
                    corp_code="00000081",
                    stock_code="000081",
                    corp_name="원문파싱오류회사",
                    market="KOSPI",
                ),
                SourceDocument(
                    rcept_no="20250318000081",
                    corp_code="00000081",
                    bsns_year=2024,
                    source_type="audit_report",
                    report_nm="감사보고서",
                    content_type="xml",
                    raw_content=malformed,
                    doc_hash="1" * 40,
                    storage_status="inline",
                ),
                EvidenceDocument(
                    corp_code="00000081",
                    bsns_year=2024,
                    source_type="audit_report",
                    rcept_no="20250318000081",
                    evidence_scope="auditor_view",
                    title="정상 정규화 증거",
                    normalized_text=FIXTURE.read_text(encoding="utf-8"),
                    source_count=1,
                ),
            ]
        )

    result = rebuild_kam_items(year=2024, dry_run=True)

    receipt = result["receipts"][0]
    assert receipt["quality_status"] == "full_body"
    assert receipt["source_basis"] == "evidence_documents.normalized_text"
    assert "source_documents.raw_body:parse_error" in receipt["limitations"]


def test_rebuild_falls_back_after_structured_evidence_uri_parse_error(temp_engine):
    import kreports.collector.report_document_collector as collector_module
    from kreports.db.engine import get_session

    malformed = (
        "핵심감사사항\n1. 수익인식\n핵심감사사항으로 선정한 이유\n"
        "중요한 위험 설명만 있고 감사 대응 제목과 본문은 없습니다."
    )
    stored = collector_module.RawDocumentStore().write(
        corp_code="00000082",
        bsns_year=2024,
        source_type="audit_report",
        rcept_no="20250318000082",
        content_type="xml",
        content=malformed,
    )
    with get_session() as session:
        session.add_all(
            [
                Company(
                    corp_code="00000082",
                    stock_code="000082",
                    corp_name="URI파싱오류회사",
                    market="KOSPI",
                ),
                EvidenceDocument(
                    corp_code="00000082",
                    bsns_year=2024,
                    source_type="audit_report",
                    rcept_no="20250318000082",
                    evidence_scope="auditor_view",
                    title="URI 파싱 오류 후 정상 정규화",
                    normalized_text=FIXTURE.read_text(encoding="utf-8"),
                    full_text_uri=stored.storage_uri,
                    full_text_hash=stored.doc_hash,
                    full_text_length=stored.content_length,
                    source_count=1,
                ),
            ]
        )

    result = collector_module.rebuild_kam_items(year=2024, dry_run=True)

    receipt = result["receipts"][0]
    assert receipt["quality_status"] == "full_body"
    assert receipt["source_basis"] == "evidence_documents.normalized_text"
    assert "evidence_documents.full_text_uri:parse_error" in receipt["limitations"]


def test_rebuild_reports_structured_normalized_evidence_parse_error(temp_engine):
    from kreports.collector.report_document_collector import rebuild_kam_items
    from kreports.db.engine import get_session

    malformed = (
        "핵심감사사항\n1. 수익인식\n핵심감사사항으로 선정한 이유\n"
        "중요한 위험 설명만 있고 감사 대응 제목과 본문은 없습니다."
    )
    with get_session() as session:
        session.add_all(
            [
                Company(
                    corp_code="00000083",
                    stock_code="000083",
                    corp_name="정규화파싱오류회사",
                    market="KOSPI",
                ),
                EvidenceDocument(
                    corp_code="00000083",
                    bsns_year=2024,
                    source_type="audit_report",
                    rcept_no="20250318000083",
                    evidence_scope="auditor_view",
                    title="불완전 정규화 증거",
                    normalized_text=malformed,
                    source_count=1,
                ),
            ]
        )

    result = rebuild_kam_items(year=2024, dry_run=True)

    receipt = result["receipts"][0]
    assert receipt["quality_status"] == "error"
    assert receipt["source_basis"] == "none"
    assert (
        "evidence_documents.normalized_text:parse_error"
        in receipt["limitations"]
    )


def test_rebuild_treats_plain_empty_raw_body_as_missing(temp_engine):
    from kreports.collector.report_document_collector import rebuild_kam_items
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add_all(
            [
                Company(
                    corp_code="00000084",
                    stock_code="000084",
                    corp_name="본문누락회사",
                    market="KOSPI",
                ),
                SourceDocument(
                    rcept_no="20250318000084",
                    corp_code="00000084",
                    bsns_year=2024,
                    source_type="audit_report",
                    report_nm="감사보고서",
                    content_type="xml",
                    raw_content="",
                    doc_hash="4" * 40,
                    storage_status="inline",
                ),
            ]
        )

    result = rebuild_kam_items(year=2024, dry_run=True)

    receipt = result["receipts"][0]
    assert receipt["quality_status"] == "missing"
    assert not any(
        limitation.endswith(":parse_error")
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
        == "report_sections.structured_body"
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
    assert receipt["source_basis"] == "report_sections.derived_summary"
    assert receipt["titles"] == ["요약 KAM"]
    assert receipt["has_reason"] == [False]
    assert receipt["has_audit_response"] == [False]


def test_rebuild_parses_structured_kam_before_applying_length_heuristics(
    temp_engine,
):
    from kreports.collector.report_document_collector import rebuild_kam_items
    from kreports.db.engine import get_session

    structured = """
    핵심감사사항
    1. 수익인식
    핵심감사사항으로 선정한 이유
    기간귀속 판단에 중요한 왜곡표시위험이 있습니다.
    감사에서 다루어진 방법
    표본 계약서와 세금계산서를 대사했습니다.
    """
    assert len(structured) < 300
    with get_session() as session:
        session.add(
            Company(
                corp_code="00000071",
                stock_code="000071",
                corp_name="짧은구조회사",
                market="KOSPI",
            )
        )
        session.add(
            ReportSection(
                rcept_no="20250318000071",
                corp_code="00000071",
                bsns_year=2024,
                source_type="audit_report",
                section_key="kam",
                section_title="짧은 구조 KAM",
                body_text=structured,
                body_hash="7" * 40,
                body_length=len(structured),
                ordinal=0,
            )
        )

    result = rebuild_kam_items(year=2024, dry_run=True)

    assert result["receipt_counts"]["full_body"] == 1
    assert result["item_counts"]["full_body"] == 1
    assert (
        result["receipts"][0]["source_basis"]
        == "report_sections.structured_body"
    )
    assert result["receipts"][0]["has_reason"] == [True]
    assert result["receipts"][0]["has_audit_response"] == [True]


def test_rebuild_keeps_long_unstructured_derived_text_as_summary_only(
    temp_engine,
):
    from kreports.collector.report_document_collector import rebuild_kam_items
    from kreports.db.engine import get_session

    summary = (
        "수익인식은 회사의 핵심감사사항으로 요약되어 있습니다. "
        "상세 선정 이유와 감사절차는 이 파생 요약에 포함되어 있지 않습니다. "
    ) * 8
    assert len(summary) > 400
    with get_session() as session:
        session.add(
            Company(
                corp_code="00000072",
                stock_code="000072",
                corp_name="긴요약회사",
                market="KOSPI",
            )
        )
        session.add(
            ReportSection(
                rcept_no="20250318000072",
                corp_code="00000072",
                bsns_year=2024,
                source_type="audit_report",
                section_key="kam",
                section_title="긴 파생 요약",
                body_text=summary,
                body_hash="8" * 40,
                body_length=len(summary),
                ordinal=0,
            )
        )

    result = rebuild_kam_items(year=2024, dry_run=True)

    assert result["receipt_counts"]["summary_only"] == 1
    assert result["item_counts"]["summary_only"] == 1
    assert (
        result["receipts"][0]["source_basis"]
        == "report_sections.derived_summary"
    )
    assert result["receipts"][0]["has_reason"] == [False]
    assert result["receipts"][0]["has_audit_response"] == [False]


def test_rebuild_reports_structured_kam_parse_failure_as_error(temp_engine):
    from kreports.collector.report_document_collector import rebuild_kam_items
    from kreports.db.engine import get_session

    malformed = (
        "핵심감사사항\n1. 수익인식\n핵심감사사항으로 선정한 이유\n"
        + ("중요한 위험 설명만 있고 감사 대응 제목과 본문은 없습니다. " * 12)
    )
    assert len(malformed) > 300
    with get_session() as session:
        session.add(
            Company(
                corp_code="00000073",
                stock_code="000073",
                corp_name="파싱오류회사",
                market="KOSPI",
            )
        )
        session.add(
            ReportSection(
                rcept_no="20250318000073",
                corp_code="00000073",
                bsns_year=2024,
                source_type="audit_report",
                section_key="kam",
                section_title="불완전 구조 KAM",
                body_text=malformed,
                body_hash="9" * 40,
                body_length=len(malformed),
                ordinal=0,
            )
        )

    result = rebuild_kam_items(year=2024, dry_run=True)

    assert result["receipt_counts"]["error"] == 1
    assert result["item_counts"]["error"] == 0
    assert any(
        limitation == "report_sections.structured_body:parse_error"
        for limitation in result["receipts"][0]["limitations"]
    )


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


def test_rebuild_upsert_preserves_stable_kam_item_id(temp_engine):
    from kreports.collector.report_document_collector import rebuild_kam_items
    from kreports.db.engine import get_session

    summary = "수익인식은 핵심감사사항입니다."
    with get_session() as session:
        session.add(
            Company(
                corp_code="00000081",
                stock_code="000081",
                corp_name="안정ID회사",
                market="KOSPI",
            )
        )
        session.add(
            ReportSection(
                rcept_no="20250318000081",
                corp_code="00000081",
                bsns_year=2024,
                source_type="audit_report",
                section_key="kam",
                section_title="수익인식",
                body_text=summary,
                body_hash="a" * 40,
                body_length=len(summary),
                ordinal=0,
            )
        )

    rebuild_kam_items(year=2024)
    with get_session() as session:
        stable_id = (
            session.query(KamItem.id)
            .filter_by(rcept_no="20250318000081")
            .scalar()
        )
        session.add(
            KamItem(
                rcept_no="20250318000082",
                corp_code="00000082",
                bsns_year=2024,
                source_type="audit_report",
                ordinal=1,
                title="다른 회사",
                related_note_references_json="[]",
                full_body_hash="b" * 40,
                full_body_length=10,
                source_basis="fixture",
                parser_version="v1",
                quality_status="summary_only",
            )
        )

    rebuild_kam_items(year=2024)

    with get_session() as session:
        rebuilt_id = (
            session.query(KamItem.id)
            .filter_by(rcept_no="20250318000081")
            .scalar()
        )
        assert rebuilt_id == stable_id
        assert session.query(KamItem).count() == 2


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
    assert "database_status=available" in result.stdout
    assert "receipts_total=1" in result.stdout
    assert "receipt_full_body=0" in result.stdout
    assert "receipt_summary_only=1" in result.stdout
    assert "receipt_missing=0" in result.stdout
    assert "receipt_error=0" in result.stdout
    assert "matter_items_total=1" in result.stdout
    assert "item_full_body=0" in result.stdout
    assert "item_summary_only=1" in result.stdout
    assert "item_missing=0" in result.stdout
    assert "item_error=0" in result.stdout
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


def test_rebuild_kam_items_cli_dry_run_missing_db_creates_nothing(
    tmp_path,
    monkeypatch,
):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import kreports.db.engine as engine_module

    db_path = tmp_path / "missing.db"
    isolated = create_engine(f"sqlite:///{db_path}")
    monkeypatch.setattr(engine_module, "engine", isolated)
    monkeypatch.setattr(
        engine_module,
        "SessionLocal",
        sessionmaker(bind=isolated, autocommit=False, autoflush=False),
    )

    result = CliRunner().invoke(
        app,
        ["rebuild-kam-items", "--year", "2024", "--dry-run"],
    )

    assert result.exit_code == 0
    assert "database_status=unavailable" in result.stdout
    assert list(tmp_path.iterdir()) == []


def test_rebuild_kam_items_cli_dry_run_empty_db_changes_no_metadata(
    tmp_path,
    monkeypatch,
):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import kreports.db.engine as engine_module

    db_path = tmp_path / "empty.db"
    db_path.touch()
    before = (db_path.stat().st_size, db_path.stat().st_mtime_ns)
    isolated = create_engine(f"sqlite:///{db_path}")
    monkeypatch.setattr(engine_module, "engine", isolated)
    monkeypatch.setattr(
        engine_module,
        "SessionLocal",
        sessionmaker(bind=isolated, autocommit=False, autoflush=False),
    )

    result = CliRunner().invoke(
        app,
        ["rebuild-kam-items", "--year", "2024", "--dry-run"],
    )

    assert result.exit_code == 0
    assert "database_status=unavailable" in result.stdout
    assert (db_path.stat().st_size, db_path.stat().st_mtime_ns) == before
    assert {path.name for path in tmp_path.iterdir()} == {"empty.db"}


def test_rebuild_kam_items_cli_dry_run_nonempty_wal_fails_without_changes(
    tmp_path,
    monkeypatch,
):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import kreports.db.engine as engine_module
    from kreports.db.models import Base

    db_path = tmp_path / "wal.db"
    isolated = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(isolated)
    isolated.dispose()
    wal_path = tmp_path / "wal.db-wal"
    wal_path.write_bytes(b"uncheckpointed")
    before = {
        path.name: (path.stat().st_size, path.stat().st_mtime_ns)
        for path in tmp_path.iterdir()
    }
    isolated = create_engine(f"sqlite:///{db_path}")
    monkeypatch.setattr(engine_module, "engine", isolated)
    monkeypatch.setattr(
        engine_module,
        "SessionLocal",
        sessionmaker(bind=isolated, autocommit=False, autoflush=False),
    )

    result = CliRunner().invoke(
        app,
        ["rebuild-kam-items", "--year", "2024", "--dry-run"],
    )

    assert result.exit_code == 0
    assert "database_status=unavailable" in result.stdout
    after = {
        path.name: (path.stat().st_size, path.stat().st_mtime_ns)
        for path in tmp_path.iterdir()
    }
    assert after == before


def test_rebuild_kam_items_cli_dry_run_reads_inline_raw_immutably(
    tmp_path,
    monkeypatch,
):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import kreports.db.engine as engine_module
    from kreports.db.models import Base

    db_path = tmp_path / "inline.db"
    isolated = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(isolated)
    isolated_session = sessionmaker(
        bind=isolated,
        autocommit=False,
        autoflush=False,
    )
    with isolated_session() as session:
        session.add(
            Company(
                corp_code="00000091",
                stock_code="000091",
                corp_name="인라인원문회사",
                market="KOSPI",
            )
        )
        session.add(
            SourceDocument(
                rcept_no="20250318000091",
                corp_code="00000091",
                bsns_year=2024,
                source_type="audit_report",
                report_nm="감사보고서",
                content_type="xml",
                raw_content=FIXTURE.read_text(encoding="utf-8"),
                doc_hash="c" * 40,
                storage_status="inline",
            )
        )
        session.commit()
    isolated.dispose()
    before = {
        path.name: (path.stat().st_size, path.stat().st_mtime_ns)
        for path in tmp_path.iterdir()
    }
    isolated = create_engine(f"sqlite:///{db_path}")
    monkeypatch.setattr(engine_module, "engine", isolated)
    monkeypatch.setattr(
        engine_module,
        "SessionLocal",
        sessionmaker(bind=isolated, autocommit=False, autoflush=False),
    )

    result = CliRunner().invoke(
        app,
        ["rebuild-kam-items", "--year", "2024", "--dry-run"],
    )

    assert result.exit_code == 0
    assert "receipt_full_body=1" in result.stdout
    assert "matter_items_total=2" in result.stdout
    after = {
        path.name: (path.stat().st_size, path.stat().st_mtime_ns)
        for path in tmp_path.iterdir()
    }
    assert after == before
