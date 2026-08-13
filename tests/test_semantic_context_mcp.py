from __future__ import annotations


def test_semantic_context_tool_is_typed_read_only_and_catalogued(temp_engine):
    from kreports.db.engine import get_session
    from kreports.db.models import (
        AccountingNoteChapter,
        Company,
        ReportSection,
        SourceDocument,
    )
    from kreports.mcp.dispatch import dispatch_tool, legacy_result

    with get_session() as session:
        session.add_all([
            Company(corp_code="00000001", stock_code="000001", corp_name="Semantic Corp", induty_code="26410"),
            SourceDocument(
                rcept_no="20250301000001", corp_code="00000001", bsns_year=2024,
                source_type="business_report", report_nm="사업보고서", raw_content="<xml/>", doc_hash="a" * 40,
            ),
            ReportSection(
                rcept_no="20250301000001", corp_code="00000001", bsns_year=2024,
                source_type="business_report", section_key="risk_management",
                section_title="위험관리", body_text="환율 위험", ordinal=0,
            ),
            AccountingNoteChapter(
                corp_code="00000001", bsns_year=2024, fs_div="CFS",
                rcept_no="20250301000001", source_type="business_report",
                note_no="10", note_title="리스", section_type="other_note",
                body="리스부채를 측정합니다.",
            ),
        ])

    raw = legacy_result(
        "get_semantic_company_context",
        {
            "company": "00000001",
            "year": 2024,
            "topics": ["risks"],
            "note_topics": ["leases"],
        },
    )
    envelope = dispatch_tool(
        "get_semantic_company_context",
        {
            "company": "00000001",
            "year": 2024,
            "topics": ["risks"],
            "note_topics": ["leases"],
        },
    )

    assert raw["read_only"] is True
    assert raw["business_report"][0]["section_key"] == "risk_management"
    assert raw["audit_report"] == []
    assert raw["note_topics_requested"] == ["leases"]
    assert [item["topic"] for item in raw["notes"]] == ["leases"]
    assert raw["note_comparison_summary"]["topic_coverage"] == [{
        "topic": "leases",
        "subject_availability": "available",
        "peer_availability": "not_requested",
    }]
    assert raw["note_comparison_summary"]["source_locators"] == [
        "accounting_note_chapters:1",
    ]
    assert raw["_meta"]["tool"] == "get_semantic_company_context"
    assert envelope.tool_name == "get_semantic_company_context"
    assert envelope.data_quality.status == "limited"


def test_semantic_context_input_rejects_unknown_topic():
    from pydantic import ValidationError

    from kreports.mcp.input_models import GetSemanticCompanyContextInput

    try:
        GetSemanticCompanyContextInput(
            company="00000001", year=2024, topics=["not_a_semantic_topic"]
        )
    except ValidationError as exc:
        assert "topics" in str(exc)
    else:
        raise AssertionError("unknown semantic topic must be rejected")
