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
                raw_content="<DOCUMENT><P>1. 금융상품</P><P>공정가치입니다.</P></DOCUMENT>",
                doc_hash="c" * 40,
            ),
            AccountingNoteChapter(
                corp_code="00000001", bsns_year=2024, fs_div="CFS",
                rcept_no="20250331000001", source_type="business_report",
                note_no="1", note_title="리스", section_type="other_note", body="리스부채입니다.",
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
        "inline_readable": 2,
        "external_uri_unverified": 1,
        "unavailable": 0,
    }
    assert result["note_topic_gaps"]["financial_instruments"]["missing_derived"] == 2
    assert result["note_topic_gaps"]["financial_instruments"]["external_raw_unverified"] == 1
    assert result["note_topic_gaps"]["leases"]["available"] == 1
    assert result["report_section_gaps"]["business_overview"]["available"] == 1
    assert result["report_section_gaps"]["risks"]["missing_derived"] == 1
    assert result["report_section_gaps"]["kam"]["missing_derived"] == 1
    assert result["evidence_document_gaps"] == {
        "available": 1,
        "missing_derived": 2,
    }
    external_sample = next(
        row for row in result["company_year_source_page"]["rows"]
        if row["rcept_no"] == "20250331000002"
    )
    assert external_sample["raw_status"] == "external_raw_unverified"
    assert external_sample["missing_note_topics"] == []
    assert external_sample["raw_unverified_note_topics"] == [
        "accounting_policies", "financial_instruments", "impairment", "leases",
        "provisions_contingencies", "related_parties", "subsequent_events", "subsidiaries",
    ]


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
