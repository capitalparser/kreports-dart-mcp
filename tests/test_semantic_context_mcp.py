from __future__ import annotations


def test_semantic_context_tool_is_typed_read_only_and_catalogued(temp_engine):
    from kreports.db.engine import get_session
    from kreports.db.models import Company, ReportSection, SourceDocument
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
        ])

    raw = legacy_result(
        "get_semantic_company_context",
        {"company": "00000001", "year": 2024, "topics": ["risks"]},
    )
    envelope = dispatch_tool(
        "get_semantic_company_context",
        {"company": "00000001", "year": 2024, "topics": ["risks"]},
    )

    assert raw["read_only"] is True
    assert raw["business_report"][0]["section_key"] == "risk_management"
    assert raw["audit_report"] == []
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
