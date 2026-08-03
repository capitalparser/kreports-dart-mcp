from __future__ import annotations

import json


def test_extraction_gap_audit_is_fail_closed_for_external_raw_and_finds_derived_gaps(temp_engine):
    from kreports.analysis.extraction_gap_audit import build_extraction_gap_audit
    from kreports.db.engine import get_session
    from kreports.db.models import (
        AccountingNoteChapter,
        EvidenceDocument,
        ReportSection,
        SourceDocument,
    )

    with get_session() as session:
        session.add_all([
            SourceDocument(
                rcept_no="20250331000001", corp_code="00000001", bsns_year=2024,
                source_type="business_report", report_nm="사업보고서", content_type="xml",
                raw_content="<DOCUMENT><P>1. 리스</P><P>리스부채입니다.</P></DOCUMENT>",
                doc_hash="a" * 40,
            ),
            SourceDocument(
                rcept_no="20250331000002", corp_code="00000002", bsns_year=2024,
                source_type="business_report", report_nm="사업보고서", content_type="xml",
                raw_content="", doc_hash="b" * 40,
                storage_uri="gs://example/external.xml.gz", storage_status="externalized",
            ),
            SourceDocument(
                rcept_no="20250331000003", corp_code="00000003", bsns_year=2024,
                source_type="audit_report", report_nm="감사보고서", content_type="xml",
                raw_content=(
                    "<DOCUMENT><TITLE>연결재무제표 주석</TITLE>"
                    "<P>1. 금융상품</P><P>공정가치입니다.</P></DOCUMENT>"
                ),
                doc_hash="c" * 40,
            ),
            SourceDocument(
                rcept_no="20250331000004", corp_code="00000004", bsns_year=2024,
                source_type="business_report", report_nm="사업보고서", content_type="xml",
                raw_content=(
                    "<DOCUMENT><TITLE>별도재무제표 주석</TITLE>"
                    "<P>1. 특수관계자 거래</P><P>특수관계자 거래입니다.</P></DOCUMENT>"
                ),
                doc_hash="d" * 40,
            ),
            AccountingNoteChapter(
                corp_code="00000001", bsns_year=2024, fs_div="CFS",
                rcept_no="20250331000001", source_type="business_report",
                note_no="1", note_title="리스", section_type="other_note", body="리스부채입니다.",
            ),
            AccountingNoteChapter(
                corp_code="00000004", bsns_year=2024, fs_div="OFS",
                rcept_no="20250331000004", source_type="business_report",
                note_no="1", note_title="특수관계자 거래", section_type="other_note", body="특수관계자 거래입니다.",
            ),
            ReportSection(
                rcept_no="20250331000001", corp_code="00000001", bsns_year=2024,
                source_type="business_report", section_key="business_overview",
                section_title="사업의 개요", body_text="사업 설명", ordinal=0,
            ),
            ReportSection(
                rcept_no="20250331000003", corp_code="00000003", bsns_year=2024,
                source_type="audit_report", section_key="audit_opinion",
                section_title="감사의견", body_text="적정", ordinal=0,
            ),
            EvidenceDocument(
                corp_code="00000001", bsns_year=2024, source_type="business_report",
                rcept_no="20250331000001", evidence_scope="company_view",
                normalized_text="사업 증거", source_count=1,
            ),
        ])

    result = build_extraction_gap_audit(
        year=2024, company_limit=10, _read_engine=temp_engine,
    )

    assert result["mode"] == "read_only_extraction_gap_audit"
    assert result["write_boundary"]["writes_performed"] is False
    assert result["parser_coverage"]["raw_availability"] == {
        "inline_readable": 3,
        "external_uri_unverified": 1,
        "unavailable": 0,
    }
    assert result["note_topic_gaps"]["financial_instruments"] == {"missing_derived": 1}
    assert result["note_topic_gaps"]["leases"]["available"] == 1
    assert result["note_topic_gaps"]["related_parties"]["available"] == 1
    assert {tuple(row[key] for key in ("source_type", "fs_div", "topic")) for row in result["note_topic_gap_breakdown"]} >= {
        ("business_report", "CFS", "leases"),
        ("business_report", "OFS", "related_parties"),
        ("audit_report", "CFS", "financial_instruments"),
    }
    assert result["report_section_gaps"]["business_overview"]["available"] == 1
    assert result["report_section_gaps"]["risks"]["unverified"] == 3
    assert result["report_section_gaps"]["kam"]["unverified"] == 1
    assert result["evidence_document_gaps"] == {
        "available": 1,
        "missing_derived": 3,
    }
    external_sample = next(
        row for row in result["company_year_source_page"]["rows"]
        if row["rcept_no"] == "20250331000002"
    )
    assert external_sample["raw_status"] == "external_raw_unverified"
    assert external_sample["missing_note_topics"] == []
    assert external_sample["parsed_note_candidates"] == []
    assert external_sample["unverified_report_sections"] == [
        "business_overview", "risks", "shareholders_board",
    ]


def test_extraction_gap_audit_never_cross_matches_same_receipt_across_company_or_year(temp_engine):
    from kreports.analysis.extraction_gap_audit import build_extraction_gap_audit
    from kreports.db.engine import get_session
    from kreports.db.models import AccountingNoteChapter, EvidenceDocument, ReportSection, SourceDocument

    with get_session() as session:
        session.add_all([
            SourceDocument(
                rcept_no="20250331000009", corp_code="00000009", bsns_year=2024,
                source_type="business_report", report_nm="사업보고서", content_type="xml",
                raw_content="<DOCUMENT><P>1. 리스</P><P>리스부채입니다.</P></DOCUMENT>",
                doc_hash="e" * 40,
            ),
            # These rows deliberately reuse the receipt number but belong to a
            # different company/year.  They must not satisfy the 2024 source.
            AccountingNoteChapter(
                corp_code="00000008", bsns_year=2023, fs_div="CFS",
                rcept_no="20250331000009", source_type="business_report",
                note_no="1", note_title="리스", section_type="other_note", body="리스부채입니다.",
            ),
            ReportSection(
                rcept_no="20250331000009", corp_code="00000008", bsns_year=2023,
                source_type="business_report", section_key="business_overview",
                section_title="사업의 개요", body_text="다른 회사", ordinal=0,
            ),
            EvidenceDocument(
                corp_code="00000008", bsns_year=2023, source_type="business_report",
                rcept_no="20250331000009", evidence_scope="company_view",
                normalized_text="다른 회사", source_count=1,
            ),
        ])

    result = build_extraction_gap_audit(year=2024, _read_engine=temp_engine)

    assert result["note_topic_gaps"]["leases"] == {"missing_derived": 1}
    assert result["report_section_gaps"]["business_overview"] == {"unverified": 1}
    assert result["evidence_document_gaps"] == {"available": 0, "missing_derived": 1}


def test_extraction_gap_audit_cli_exposes_read_only_scope(monkeypatch):
    from typer.testing import CliRunner

    import kreports.analysis.extraction_gap_audit as audit_module
    from kreports.cli.main import app

    monkeypatch.setattr(audit_module, "build_extraction_gap_audit", lambda **kwargs: {
        "mode": "read_only_extraction_gap_audit",
        "arguments": kwargs,
    })

    result = CliRunner().invoke(
        app,
        ["audit-extraction-gaps", "--year", "2024", "--company-offset", "2", "--company-limit", "5"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["arguments"] == {
        "year": 2024,
        "source_type": None,
        "company_offset": 2,
        "company_limit": 5,
    }
