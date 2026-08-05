from __future__ import annotations

from datetime import date


def test_business_overview_exposes_typed_read_only_semantic_context(temp_engine):
    from kreports.db.engine import get_session
    from kreports.db.models import (
        AccountingNoteChapter,
        Company,
        Disclosure,
        ReportSection,
        SourceDocument,
    )
    from kreports.mcp.dispatch import dispatch_tool, legacy_result

    with get_session() as session:
        session.add_all([
            Company(corp_code="00000001", stock_code="000001", corp_name="Semantic Corp", induty_code="26410"),
            SourceDocument(
                rcept_no="20250301000001", corp_code="00000001", bsns_year=2024,
                source_type="business_report", report_nm="사업보고서 (2024.12)", raw_content="<xml/>", doc_hash="a" * 40,
            ),
            Disclosure(
                rcept_no="20250301000001", corp_code="00000001", corp_name="Semantic Corp",
                disc_date=date(2025, 3, 1), disc_type="A", report_nm="사업보고서 (2024.12)",
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
        "get_business_overview",
        {
            "company": "00000001",
            "bsns_year": 2024,
            "include_semantic_context": True,
            "semantic_topics": ["risks"],
            "note_topics": ["leases"],
        },
    )
    envelope = dispatch_tool(
        "get_business_overview",
        {
            "company": "00000001",
            "bsns_year": 2024,
            "include_semantic_context": True,
            "semantic_topics": ["risks"],
            "note_topics": ["leases"],
        },
    )

    context = raw["semantic_context"]
    assert context["read_only"] is True
    assert context["business_report"][0]["section_key"] == "risk_management"
    assert context["audit_report"] == []
    assert context["note_topics_requested"] == ["leases"]
    assert [item["topic"] for item in context["notes"]] == ["leases"]
    assert context["note_comparison_summary"]["topic_coverage"] == [{
        "topic": "leases",
        "subject_availability": "available",
        "peer_availability": "not_requested",
    }]
    assert context["note_comparison_summary"]["source_locators"] == [
        "accounting_note_chapters:1",
    ]
    assert raw["_meta"]["tool"] == "get_business_overview"
    assert envelope.tool_name == "get_business_overview"
    assert envelope.data_quality.status == "usable"


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


def test_semantic_context_and_note_comparison_are_consolidated_into_34_tools():
    from kreports.mcp.catalog import TOOL_CATALOG

    assert len(TOOL_CATALOG) == 34
    assert "get_semantic_company_context" not in TOOL_CATALOG
    assert "compare_peer_accounting_notes" not in TOOL_CATALOG
    assert "include_semantic_context" in TOOL_CATALOG[
        "get_business_overview"
    ].input_model.model_fields
    assert "include_note_comparison" in TOOL_CATALOG[
        "compare_peer_accounting_policies"
    ].input_model.model_fields
    assert "include_note_disclosure_matrix" in TOOL_CATALOG[
        "compare_peer_accounting_policies"
    ].input_model.model_fields
