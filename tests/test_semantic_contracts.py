from __future__ import annotations


def test_business_profile_keeps_tags_and_receipt_provenance():
    from kreports.processor.semantic_contracts import build_business_semantic_profile

    profile = build_business_semantic_profile(
        {
            "business_description": {
                "title": "주요 제품 및 서비스",
                "body_text": "반도체 장비와 유지보수 서비스를 국내외 고객에게 공급합니다.",
            },
            "risk_management": {
                "title": "위험관리",
                "body_text": "원재료 가격 변동과 환율 위험을 관리합니다.",
            },
        },
        corp_code="00000001",
        bsns_year=2024,
        source_document_id=17,
        rcept_no="20250301000001",
    )

    assert profile.corp_code == "00000001"
    assert profile.tags["products_services"] == ["products_services"]
    assert profile.tags["customers_markets"] == ["customers_markets"]
    assert profile.tags["raw_materials"] == ["raw_materials"]
    assert profile.tags["risks"] == ["risks"]
    assert all(item.rcept_no == "20250301000001" for item in profile.evidence)
    assert all(item.source_document_id == 17 for item in profile.evidence)
    assert all(item.availability == "available" for item in profile.evidence)


def test_note_normalization_and_summary_only_status_are_explicit():
    from kreports.processor.semantic_contracts import NoteSemanticItem, normalize_note_topic

    item = NoteSemanticItem(
        corp_code="00000001",
        bsns_year=2024,
        source_document_id=None,
        rcept_no="20250301000001",
        section_key="policy",
        source_locator="accounting_note_chapters:11",
        parser_version="semantic-v1",
        confidence=0.6,
        availability="summary_only",
        extraction_method="chapter_title_keyword",
        topic=normalize_note_topic("리스부채와 사용권자산", ""),
        excerpt="리스부채를 현재가치로 측정합니다.",
    )

    assert item.topic == "leases"
    assert item.availability == "summary_only"
    assert item.source_document_id is None


def test_audit_kam_evidence_is_typed_and_preserves_topic():
    from kreports.processor.semantic_contracts import build_audit_semantic_evidence

    evidence = build_audit_semantic_evidence(
        {"kam": {"title": "핵심감사사항", "body_text": "수익인식 관련 핵심감사사항"}},
        corp_code="00000001",
        bsns_year=2024,
        source_document_id=2,
        rcept_no="20250301000002",
    )

    assert len(evidence) == 1
    assert evidence[0].topic == "kam"
    assert evidence[0].section_key == "kam"
    assert evidence[0].source_locator == "report_sections:20250301000002:kam"
