from kreports.analysis.business_insights import (
    build_business_report_focus,
    generate_audit_focus,
    generate_business_insights,
    generate_investment_focus,
)


def _sample_sections():
    return {
        "business_overview": {
            "title": "사업의 개요",
            "body_text": "회사는 플랫폼 서비스와 솔루션을 제공하며 주요 고객에게 제품을 판매합니다.",
            "length": 42,
        },
        "business_description": {
            "title": "사업의 내용",
            "body_text": (
                "제품과 서비스 매출은 고객 검수 후 인식합니다. 수주와 계약자산이 있으며 "
                "주요 고객 매출 비중과 재고자산 손상 징후를 관리합니다. 해외 시장 확대를 추진합니다."
            ),
            "length": 90,
        },
        "risk_management": {
            "title": "시장위험과 위험관리",
            "body_text": (
                "유동성 위험, 차입금 만기, 환율 변동, 외환 위험, 파생상품 평가 위험을 관리합니다. "
                "유동성 위험은 자금조달 계획과 차입금 만기 분석으로 통제하며, 환율과 외환 변동은 "
                "파생상품과 헤지 정책으로 관리합니다."
            ),
            "length": 120,
        },
        "rd_activities": {
            "title": "연구개발비용",
            "body_text": "연구개발비 / 매출액 비율은 12.5%이며 개발비 자본화와 기술개발 활동이 있습니다.",
            "length": 58,
        },
        "key_contracts": {
            "title": "경영상의 주요계약",
            "body_text": "계약상대방 ABC와 공급계약을 체결했습니다.",
            "length": 25,
        },
    }


def test_audit_focus_cards_include_source_and_evidence():
    cards = generate_audit_focus(_sample_sections())

    assert cards
    assert any(card["title"] == "매출인식 검토 후보" for card in cards)
    for card in cards:
        assert card["source_section"]
        assert card["evidence"]
        assert "source_label" in card
        assert "accounts" in card
        assert "assertions" in card


def test_investment_focus_is_keyword_based_without_company_specific_rule():
    cards = generate_investment_focus(_sample_sections())

    assert cards
    assert any(card["title"] == "비즈니스 모델 키워드" for card in cards)
    assert "포커스에이아이" not in str(cards)
    for card in cards:
        assert card["source_section"]
        assert card["evidence"]


def test_business_report_focus_package_and_legacy_insights_shape():
    package = build_business_report_focus(_sample_sections(), induty_code="")
    insights = generate_business_insights(_sample_sections(), induty_code="")

    assert package["audit_focus"]
    assert package["investment_focus"]
    assert package["risk_distribution"]
    assert insights
    for item in insights:
        assert {"title", "value", "detail", "audience", "risk_level", "source_section", "evidence"} <= set(item)
