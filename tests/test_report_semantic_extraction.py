from __future__ import annotations


def test_business_report_parser_exposes_additive_semantic_profile():
    from kreports.processor.report_section_parser import extract_report_semantic_profile

    xml = """
    <TITLE>사업의 내용</TITLE><P>주요 제품은 반도체 장비이며 해외 고객에게 판매합니다.</P>
    <TITLE>위험관리</TITLE><P>원재료 가격과 환율 변동 위험이 있습니다.</P>
    """

    profile = extract_report_semantic_profile(
        xml,
        corp_code="00000001",
        bsns_year=2024,
        source_document_id=9,
        rcept_no="20250301000001",
    )

    assert {item.topic for item in profile.evidence} >= {
        "products_services",
        "customers_markets",
        "raw_materials",
        "risks",
    }
    assert all(item.parser_version == "semantic-v1" for item in profile.evidence)


def test_audit_report_parser_exposes_additive_semantic_evidence():
    from kreports.processor.audit_report_parser import extract_audit_semantic_evidence

    xml = """
    <TITLE>핵심감사사항</TITLE><P>수익인식의 적정성 검토</P>
    <TITLE>감사의견</TITLE><P>적정의견을 표명합니다.</P>
    """

    evidence = extract_audit_semantic_evidence(
        xml,
        corp_code="00000001",
        bsns_year=2024,
        source_document_id=10,
        rcept_no="20250301000002",
    )

    assert {item.topic for item in evidence} == {"kam", "audit_opinion"}
