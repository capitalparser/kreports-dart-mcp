from datetime import datetime
from pathlib import Path
import re

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


def test_parse_outcome_distinguishes_structured_title_from_plain_ambiguity():
    from kreports.processor.kam_parser import (
        extract_kam_items,
        parse_kam_items,
    )

    structured = """
    <TITLE>Key Audit Matters</TITLE>
    <TITLE>Revenue recognition</TITLE>
    <P>Why the matter was determined to be a key audit matter</P>
    <P>Contract cut-off requires significant judgment.</P>
    <P>How the matter was addressed in the audit</P>
    <P>We inspected contract samples.</P>
    <TITLE>Classification of leases</TITLE>
    <P>Why the matter was determined to be a key audit matter</P>
    <P>Lease classification requires significant judgment.</P>
    <P>How the matter was addressed in the audit</P>
    <P>We reviewed management's classification.</P>
    """
    plain = re.sub(r"</?(?:TITLE|P)>", "", structured)

    structured_outcome = parse_kam_items(structured)
    plain_outcome = parse_kam_items(plain)

    assert structured_outcome.status == "complete"
    assert [item.title for item in structured_outcome.items] == [
        "Revenue recognition",
        "Classification of leases",
    ]
    assert structured_outcome.limitations == []
    assert plain_outcome.status == "ambiguous"
    assert plain_outcome.items == []
    assert "ambiguous_boundary" in plain_outcome.limitations
    assert extract_kam_items(plain) == []


def test_parse_outcome_reports_no_kam_and_incomplete_structure():
    from kreports.processor.kam_parser import parse_kam_items

    no_kam = parse_kam_items("일반 감사보고서 본문")
    incomplete = parse_kam_items(
        "핵심감사사항\n수익인식\n핵심감사사항으로 선정한 이유\n위험 본문"
    )

    assert no_kam.status == "no_kam"
    assert no_kam.items == []
    assert no_kam.limitations == []
    assert incomplete.status == "error"
    assert incomplete.items == []
    assert "incomplete_kam_structure" in incomplete.limitations


def test_parse_collapsed_audit_report_recovers_explicit_kam_boundaries():
    from kreports.processor.kam_parser import parse_collapsed_kam_items

    collapsed = (
        "감사의견근거 우리는 대한민국의 회계감사기준에 따라 감사를 "
        "수행하였습니다. 핵 심감사사항 핵심감사사항은 우리의 전문가적 "
        "판단에 따라 당기 재무제표감사에서 가장 유의적인 사항들입니다. "
        "해당 사항들은 재무제표 전체에 대한 감사의 관점에서 다루어졌으며, "
        "우리는 이런 사항에 대하여 별도의 의견을 제공하지는 않습니다. "
        "(1) 생명과학 현금창출단위에 대한 영업권 손상평가 "
        "핵심감사사항으로 결정된 이유 영업권 금액과 회수가능액 추정에 "
        "유의적인 경영진의 판단이 포함됩니다. "
        "핵심감사사항이 감사에서 다루어진 방법 우리는 가치평가 모델과 "
        "할인율을 검토했습니다. 연결재무제표감사에 대한 감사인의 책임 "
        "우리의 목적은 합리적인 확신을 얻는 것입니다."
    )

    outcome = parse_collapsed_kam_items(collapsed)

    assert outcome.status == "complete"
    assert [item.title for item in outcome.items] == [
        "생명과학 현금창출단위에 대한 영업권 손상평가"
    ]
    assert outcome.items[0].reason_text == (
        "영업권 금액과 회수가능액 추정에 유의적인 경영진의 판단이 포함됩니다."
    )
    assert outcome.items[0].audit_response_text == (
        "우리는 가치평가 모델과 할인율을 검토했습니다."
    )


def test_parse_collapsed_audit_report_accepts_inline_dwaeojin_response_heading():
    """Catch the Korean 다뤄진 response-heading variant leaving a complete KAM as error."""
    from kreports.processor.audit_procedure_parser import extract_procedure_steps
    from kreports.processor.kam_parser import parse_collapsed_kam_items

    collapsed = (
        "핵심감사사항\n"
        "특수관계자 거래 및 잔액 공시의 적정성\n"
        "핵심감사사항으로 결정한 이유\n"
        "관련 거래와 잔액 공시에는 유의적인 왜곡표시위험이 포함됩니다.\n"
        "핵심감사사항이 감사에서 다뤄진 방법 감사절차는 다음과 같습니다.\n"
        "- 관련 계약서를 검사하였습니다.\n"
        "- 거래 내역을 대사하였습니다.\n"
        "- 잔액 확인서를 검토하였습니다.\n"
        "- 공시 자료를 확인하였습니다.\n"
        "- 승인 문서를 검사하였습니다.\n"
        "- 재무제표 표시를 검토하였습니다.\n"
        "재무제표감사에 대한 감사인의 책임"
    )

    outcome = parse_collapsed_kam_items(collapsed)

    assert outcome.status == "complete"
    assert [item.title for item in outcome.items] == [
        "특수관계자 거래 및 잔액 공시의 적정성",
    ]
    assert outcome.items[0].quality_status == "full_body"
    assert len(extract_procedure_steps(outcome.items[0])) == 6


def test_parser_cuts_full_management_and_governance_responsibility_heading():
    """Catch a full management/governance heading leaking into an audit response."""
    from kreports.processor.kam_parser import extract_kam_items

    body = """
    핵심감사사항
    특수관계자 거래 공시
    핵심감사사항으로 결정한 이유
    관련 거래 공시에는 유의적인 판단이 포함됩니다.
    핵심감사사항이 감사에서 다뤄진 방법
    - 계약서를 검사하였습니다.
    재무제표에 대한 경영진과 지배기구의 책임
    경영진 책임 일반 설명입니다.
    """

    items = extract_kam_items(body)

    assert len(items) == 1
    assert "계약서를 검사" in items[0].audit_response_text
    assert "경영진 책임 일반 설명" not in items[0].audit_response_text


def test_parse_collapsed_audit_report_ignores_numbered_field_labels():
    from kreports.processor.kam_parser import parse_collapsed_kam_items

    collapsed = (
        "핵심감사사항 핵심감사사항은 당기 감사에서 가장 유의적인 "
        "사항들입니다. 1. 현금창출단위 손상검사 (1) "
        "핵심감사사항으로 결정된 이유 회수가능액 추정에는 유의적인 "
        "판단이 포함됩니다. (2) 핵심감사사항이 감사에서 다루어진 방법 "
        "가치평가 모델과 할인율을 검토했습니다. "
        "재무제표감사에 대한 감사인의 책임"
    )

    outcome = parse_collapsed_kam_items(collapsed)

    assert outcome.status == "complete"
    assert [item.title for item in outcome.items] == [
        "현금창출단위 손상검사"
    ]


def test_parse_collapsed_audit_report_ignores_separate_numbered_field_label():
    from kreports.processor.kam_parser import parse_collapsed_kam_items

    collapsed = (
        "핵심감사사항 핵심감사사항은 당기 감사에서 가장 유의적인 "
        "사항들입니다.\n"
        "재고자산의 실재성 및 평가\n"
        "(1) 핵심감사사항으로 결정한 이유\n"
        "재고자산의 실재성과 평가에는 유의적인 판단이 포함됩니다.\n"
        "(2) 핵심감사사항이 감사에서 다루어진 방법\n"
        "재고실사와 순실현가능가치를 검토했습니다.\n"
        "재무제표감사에 대한 감사인의 책임"
    )

    outcome = parse_collapsed_kam_items(collapsed)

    assert outcome.status == "complete"
    assert [item.title for item in outcome.items] == [
        "재고자산의 실재성 및 평가"
    ]


def test_parse_collapsed_audit_report_accepts_spaced_korean_matter_marker():
    from kreports.processor.kam_parser import parse_collapsed_kam_items

    collapsed = (
        "핵심감사사항 핵심감사사항은 당기 감사에서 가장 유의적인 "
        "사항들입니다. 우리는 이런 사항에 대하여 별도의 의견을 "
        "제공하지는 않습니다. 가 . 현금창출단위 손상평가\n"
        "(1) 핵심감사사항으로 결정된 이유\n"
        "미래현금흐름과 할인율에는 경영진의 유의적인 판단이 포함됩니다.\n"
        "핵심감사사항이 감사에서 다루어진 방법\n"
        "가치평가모델과 주요 가정을 검토했습니다.\n"
        "연결재무제표감사에 대한 감사인의 책임"
    )

    outcome = parse_collapsed_kam_items(collapsed)

    assert outcome.status == "complete"
    assert [item.title for item in outcome.items] == [
        "현금창출단위 손상평가"
    ]


def test_parse_collapsed_audit_report_ignores_separate_korean_field_label():
    from kreports.processor.kam_parser import parse_collapsed_kam_items

    collapsed = (
        "핵심감사사항 핵심감사사항은 당기 감사에서 가장 유의적인 "
        "사항들입니다.\n"
        "현금창출단위 손상검사\n"
        "가. 핵심감사사항으로 결정한 이유\n"
        "회수가능액에는 경영진의 유의적인 판단이 포함됩니다.\n"
        "나. 핵심감사사항이 감사에서 다루어진 방법\n"
        "미래현금흐름과 할인율을 검토했습니다.\n"
        "재무제표감사에 대한 감사인의 책임"
    )

    outcome = parse_collapsed_kam_items(collapsed)

    assert outcome.status == "complete"
    assert [item.title for item in outcome.items] == [
        "현금창출단위 손상검사"
    ]


def test_parse_collapsed_audit_report_accepts_exact_source_reason_heading_typo():
    from kreports.processor.kam_parser import parse_collapsed_kam_items

    collapsed = (
        "핵심감사사항 핵심감사사항은 당기 감사에서 가장 유의적인 "
        "사항들입니다. 우리는 이런 사항에 대하여 별도의 의견을 "
        "제공하지는 않습니다. 종속기업투자자산의 손상검사 "
        "핵심감사항으로 결정한 이유 회수가능액에는 경영진의 유의적인 "
        "판단이 포함됩니다. 핵심감사사항이 감사에서 다루어진 방법 "
        "주요 가정과 할인율을 검토했습니다. "
        "재무제표감사에 대한 감사인의 책임"
    )

    outcome = parse_collapsed_kam_items(collapsed)

    assert outcome.status == "complete"
    assert [item.title for item in outcome.items] == [
        "종속기업투자자산의 손상검사"
    ]


def test_parse_collapsed_audit_report_recognizes_business_combination_title():
    from kreports.processor.kam_parser import parse_collapsed_kam_items

    collapsed = (
        "핵심감사사항 핵심감사사항은 당기 감사에서 가장 유의적인 "
        "사항들입니다.\n"
        "매출인식 및 대손설정\n"
        "핵심감사사항으로 결정된 이유 매출채권 평가에 유의적인 판단이 "
        "포함됩니다.\n"
        "핵심감사사항이 감사에서 다루어진 방법 수익인식과 대손설정을 "
        "검토했습니다.\n"
        "사업결합\n"
        "핵심감사사항으로 결정된 이유 취득자산과 인수부채의 공정가치 "
        "평가에 유의적인 판단이 포함됩니다.\n"
        "핵심감사사항이 감사에서 다루어진 방법 계약과 공정가치 평가를 "
        "검토했습니다.\n"
        "재무제표감사에 대한 감사인의 책임"
    )

    outcome = parse_collapsed_kam_items(collapsed)

    assert outcome.status == "complete"
    assert [item.title for item in outcome.items] == [
        "매출인식 및 대손설정",
        "사업결합",
    ]


def test_parse_collapsed_audit_report_recovers_omitted_reason_heading_only_with_explicit_title():
    from kreports.processor.kam_parser import parse_collapsed_kam_items

    collapsed = (
        "핵심감사사항 핵심감사사항은 당기 감사에서 가장 유의적인 "
        "사항들입니다. 우리는 이런 사항에 대하여 별도의 의견을 "
        "제공하지는 않습니다.\n"
        "영업권 손상검사\n"
        "영업권 금액은 재무제표에서 유의적입니다.\n"
        "회수가능액 추정에는 미래 현금흐름과 할인율에 대한 경영진의 "
        "유의적인 판단이 포함되므로 핵심감사사항에 포함하였습니다.\n"
        "핵심감사사항이 감사에서 다루어진 방법\n"
        "핵심감사사항에 대응하기 위하여 가치평가 모델과 할인율을 "
        "검토했습니다.\n"
        "재무제표감사에 대한 감사인의 책임"
    )

    outcome = parse_collapsed_kam_items(collapsed)

    assert outcome.status == "complete"
    assert [item.title for item in outcome.items] == ["영업권 손상검사"]
    assert outcome.items[0].reason_text == (
        "영업권 금액은 재무제표에서 유의적입니다.\n"
        "회수가능액 추정에는 미래 현금흐름과 할인율에 대한 경영진의 "
        "유의적인 판단이 포함되므로 핵심감사사항에 포함하였습니다."
    )


def test_parse_collapsed_audit_report_recovers_risk_only_title_with_omitted_reason_heading():
    from kreports.processor.audit_procedure_parser import extract_procedure_steps
    from kreports.processor.kam_parser import parse_collapsed_kam_items

    collapsed = (
        "핵심감사사항 핵심감사사항은 우리의 전문가적 판단에 따라 당기 "
        "연결재무제표감사에서 가장 유의적인 사항들입니다. 우리는 이런 사항에 "
        "대하여 별도의 의견을 제공하지는 않습니다.\n"
        "DY AUTO INDIA Pvt.의 현금창출단위 손상평가\n"
        "연결회사는 해당 현금창출단위에 손상징후가 존재한다고 판단하고 "
        "손상검사를 수행하였습니다.\n"
        "우리는 미래 현금흐름과 할인율에 대한 경영진의 유의적인 판단을 고려하여 "
        "회수가능가액 검토를 핵심감사사항에 포함하였습니다.\n"
        "핵심감사사항이 감사에서 다루어진 방법\n"
        "- 외부 전문가의 적격성 및 독립성 평가\n"
        "- 가치평가 모델의 적절성을 평가\n"
        "- 주요 가정의 합리성 검토\n"
        "연결재무제표감사에 대한 감사인의 책임"
    )

    outcome = parse_collapsed_kam_items(collapsed)

    assert outcome.status == "complete"
    assert [item.title for item in outcome.items] == [
        "DY AUTO INDIA Pvt.의 현금창출단위 손상평가"
    ]
    assert len(extract_procedure_steps(outcome.items[0])) == 3


def test_parse_collapsed_audit_report_recovers_omitted_reason_heading_after_intro_tail_title():
    from kreports.processor.kam_parser import parse_collapsed_kam_items

    collapsed = (
        "핵심감사사항 핵심감사사항은 당기 감사에서 가장 유의적인 "
        "사항들입니다. 우리는 이런 사항에 대하여 별도의 의견을 "
        "제공하지는 않습니다. 종속기업투자주식 및 대여금 손상검토\n"
        "종속기업투자주식의 금액은 재무제표에서 유의적입니다.\n"
        "회수가능액 평가에는 경영진의 유의적인 판단이 포함되므로 "
        "핵심감사사항에 포함하였습니다.\n"
        "핵심감사사항이 감사에서 다루어진 방법\n"
        "손상징후와 회수가능액을 검토했습니다.\n"
        "재무제표감사에 대한 감사인의 책임"
    )

    outcome = parse_collapsed_kam_items(collapsed)

    assert outcome.status == "complete"
    assert [item.title for item in outcome.items] == [
        "종속기업투자주식 및 대여금 손상검토"
    ]


def test_parse_collapsed_audit_report_recovers_inline_reason_after_explicit_title():
    from kreports.processor.kam_parser import parse_collapsed_kam_items

    collapsed = (
        "핵심감사사항 핵심감사사항은 당기 감사에서 가장 유의적인 "
        "사항들입니다. 우리는 이런 사항에 대하여 별도의 의견을 "
        "제공하지는 않습니다.\n"
        "매각예정자산 인식 및 측정 연결회사는 호텔 영업손실이 증가하여 "
        "해당 자산의 매각을 결정하였습니다.\n"
        "회수가능가액과 부채의 귀속시기 결정에 경영진의 판단이 포함되므로 "
        "핵심감사사항에 포함하였습니다.\n"
        "핵심감사사항이 감사에서 다루어진 방법\n"
        "자산 분류시기와 회수가능가액을 검토했습니다.\n"
        "연결재무제표감사에 대한 감사인의 책임"
    )

    outcome = parse_collapsed_kam_items(collapsed)

    assert outcome.status == "complete"
    assert [item.title for item in outcome.items] == [
        "매각예정자산 인식 및 측정"
    ]
    assert (outcome.items[0].reason_text or "").startswith(
        "연결회사는 호텔 영업손실이 증가하여"
    )


def test_parse_collapsed_audit_report_does_not_infer_title_from_reason_narrative():
    from kreports.processor.kam_parser import parse_collapsed_kam_items

    collapsed = (
        "핵심감사사항 핵심감사사항은 당기 감사에서 가장 유의적인 "
        "사항들입니다.\n"
        "관계기업투자주식은 금액이 유의적이고 손상평가에 경영진의 "
        "판단이 포함되므로 이를 핵심감사사항으로 판단하였습니다.\n"
        "핵심감사사항이 감사에서 다루어진 방법\n"
        "회수가능액과 손상징후를 검토했습니다.\n"
        "재무제표감사에 대한 감사인의 책임"
    )

    outcome = parse_collapsed_kam_items(collapsed)

    assert outcome.status == "error"
    assert outcome.items == []


def test_parse_collapsed_audit_report_excludes_embedded_emphasis_before_response():
    from kreports.processor.kam_parser import parse_collapsed_kam_items

    collapsed = (
        "핵심감사사항 핵심감사사항은 당기 감사에서 가장 유의적인 "
        "사항들입니다. 우리는 이런 사항에 대하여 별도의 의견을 "
        "제공하지는 않습니다. (1) 투입법에 따른 수익인식\n"
        "핵심감사사항으로 결정된 이유\n"
        "진행률 측정에는 경영진의 유의적인 판단이 포함됩니다.\n"
        "강조사항 감사의견에는 영향을 미치지 않는 사항으로서 COVID-19로 "
        "인한 불확실성에 주의를 기울여야 합니다.\n"
        "핵심감사사항이 감사에서 다루어진 방법\n"
        "계약원가와 진행률을 검토했습니다.\n"
        "재무제표감사에 대한 감사인의 책임"
    )

    outcome = parse_collapsed_kam_items(collapsed)

    assert outcome.status == "complete"
    assert [item.title for item in outcome.items] == [
        "투입법에 따른 수익인식"
    ]
    assert "COVID-19" not in (outcome.items[0].reason_text or "")
    assert outcome.items[0].audit_response_text == (
        "계약원가와 진행률을 검토했습니다."
    )


@pytest.mark.parametrize(
    "title_fragment",
    [
        "(1)",
        (
            "핵심감사사항은 우리의 전문가적 판단에 따라 당기 재무제표감사에서 "
            "가장 유의적인 사항들입니다. 우리는 이런 사항에 대하여 별도의 "
            "의견을 제공하지는 않습니다."
        ),
    ],
)
def test_parse_collapsed_audit_report_rejects_non_title_artifacts(
    title_fragment,
):
    from kreports.processor.kam_parser import parse_collapsed_kam_items

    collapsed = (
        "핵심감사사항 핵심감사사항은 당기 감사에서 가장 유의적인 "
        f"사항들입니다. {title_fragment} "
        "핵심감사사항으로 결정된 이유 유의적인 판단이 포함됩니다. "
        "핵심감사사항이 감사에서 다루어진 방법 관련 증거를 검토했습니다. "
        "재무제표감사에 대한 감사인의 책임"
    )

    outcome = parse_collapsed_kam_items(collapsed)

    assert outcome.status == "error"
    assert outcome.items == []


@pytest.mark.parametrize(
    "title",
    [
        "연결범위의 적정성",
        "종속기업투자주식의 회수가능성",
        "매출채권 회수가능성 검토",
        "Revenue recognition and performance obligations",
        "Goodwill impairment testing",
        "Classification of leases",
    ],
)
def test_parse_outcome_requires_structure_for_ambiguous_title_grammar(title):
    from kreports.processor.kam_parser import parse_kam_items

    structured = f"""
    <TITLE>Key Audit Matters</TITLE>
    <TITLE>Revenue recognition</TITLE>
    <P>Why the matter was determined to be a key audit matter</P>
    <P>Contract cut-off requires significant judgment.</P>
    <P>How the matter was addressed in the audit</P>
    <P>We inspected contract samples.</P>
    <TITLE>{title}</TITLE>
    <P>Why the matter was determined to be a key audit matter</P>
    <P>The matter requires significant judgment.</P>
    <P>How the matter was addressed in the audit</P>
    <P>We inspected relevant evidence.</P>
    """
    plain = re.sub(r"</?(?:TITLE|P)>", "", structured)

    structured_outcome = parse_kam_items(structured)
    plain_outcome = parse_kam_items(plain)

    assert structured_outcome.status == "complete"
    assert structured_outcome.items[1].title == title
    assert plain_outcome.status == "ambiguous"
    assert plain_outcome.items == []


@pytest.mark.parametrize(
    "procedure",
    [
        "2. 재고자산 평가",
        "2. 매출채권 표본 추출",
        "II. Goodwill impairment assessment",
    ],
)
def test_parse_outcome_rejects_plain_nominal_procedure_as_fake_title(procedure):
    from kreports.processor.kam_parser import extract_kam_items, parse_kam_items

    body = f"""
    핵심감사사항
    1. 수익인식
    핵심감사사항으로 선정한 이유
    기간귀속 판단 위험
    감사에서 다루어진 방법
    {procedure}
    핵심감사사항으로 선정한 이유
    페이지 반복 위험 본문
    감사에서 다루어진 방법
    추가 감사절차
    """

    outcome = parse_kam_items(body)

    assert outcome.status == "ambiguous"
    assert outcome.items == []
    assert extract_kam_items(body) == []


def test_parse_outcome_rejects_three_page_mixed_nominal_procedures():
    from kreports.processor.kam_parser import parse_kam_items

    body = """
    핵심감사사항
    1. 수익인식
    핵심감사사항으로 선정한 이유
    위험 본문 1
    감사에서 다루어진 방법
    1. 계약 검사
    핵심감사사항으로 선정한 이유
    위험 본문 2
    감사에서 다루어진 방법
    2. 재고자산 평가
    핵심감사사항으로 선정한 이유
    위험 본문 3
    감사에서 다루어진 방법
    II. Goodwill impairment assessment
    """

    outcome = parse_kam_items(body)

    assert outcome.status == "ambiguous"
    assert outcome.items == []


@pytest.mark.parametrize(
    "container",
    [
        pytest.param("TITLE", id="title"),
        pytest.param("TH", id="table-header"),
        pytest.param('TD role="heading"', id="explicit-heading-cell"),
        pytest.param("tItLe", id="mixed-case-title"),
        pytest.param('tH data-kind="title"', id="mixed-case-table-header"),
        pytest.param(
            "tD RoLe = ' HeAdInG ' class=\"not-title\"",
            id="normalized-exact-heading-role",
        ),
    ],
)
@pytest.mark.parametrize("nested", [False, True], ids=["direct", "nested-p"])
def test_parse_outcome_preserves_strong_heading_ancestry(container, nested):
    from kreports.processor.kam_parser import parse_kam_items

    def heading(value):
        content = f"<P>{value}</P>" if nested else value
        tag = container.split()[0]
        return f"<{container}>{content}</{tag}>"

    body = f"""
    <TITLE>Key Audit Matters</TITLE>
    {heading("Revenue recognition")}
    <P>Why the matter was determined to be a key audit matter</P>
    <P>Contract cut-off requires significant judgment.</P>
    <P>How the matter was addressed in the audit</P>
    <P>We inspected contract samples.</P>
    {heading("Classification of leases")}
    <P>Why the matter was determined to be a key audit matter</P>
    <P>Lease classification requires significant judgment.</P>
    <P>How the matter was addressed in the audit</P>
    <P>We reviewed management's classification.</P>
    """

    outcome = parse_kam_items(body)

    assert outcome.status == "complete"
    assert [item.title for item in outcome.items] == [
        "Revenue recognition",
        "Classification of leases",
    ]


@pytest.mark.parametrize(
    "procedure",
    [
        "2. 재고자산 평가",
        "2. 매출채권 표본 추출",
        "II. Goodwill impairment assessment",
    ],
)
def test_parse_outcome_does_not_promote_generic_td_procedure(procedure):
    from kreports.processor.kam_parser import parse_kam_items

    body = f"""
    <TITLE>핵심감사사항</TITLE>
    <TABLE>
    <TR><TH>1. 수익인식</TH></TR>
    <TR><TD>핵심감사사항으로 선정한 이유</TD></TR>
    <TR><TD>기간귀속 판단 위험</TD></TR>
    <TR><TD>감사에서 다루어진 방법</TD></TR>
    <TR><TD>{procedure}</TD></TR>
    <TR><TD>핵심감사사항으로 선정한 이유</TD></TR>
    <TR><TD>페이지 반복 위험 본문</TD></TR>
    <TR><TD>감사에서 다루어진 방법</TD></TR>
    <TR><TD>추가 감사절차</TD></TR>
    </TABLE>
    """

    outcome = parse_kam_items(body)

    assert outcome.status == "ambiguous"
    assert outcome.items == []


def test_parse_outcome_rejects_three_page_generic_td_procedures():
    from kreports.processor.kam_parser import parse_kam_items

    body = """
    <TITLE>핵심감사사항</TITLE>
    <TABLE>
    <TR><TH>1. 수익인식</TH></TR>
    <TR><TD>핵심감사사항으로 선정한 이유</TD></TR>
    <TR><TD>위험 본문 1</TD></TR>
    <TR><TD>감사에서 다루어진 방법</TD></TR>
    <TR><TD>1. 계약 검사</TD></TR>
    <TR><TD>핵심감사사항으로 선정한 이유</TD></TR>
    <TR><TD>위험 본문 2</TD></TR>
    <TR><TD>감사에서 다루어진 방법</TD></TR>
    <TR><TD>2. 재고자산 평가</TD></TR>
    <TR><TD>핵심감사사항으로 선정한 이유</TD></TR>
    <TR><TD>위험 본문 3</TD></TR>
    <TR><TD>감사에서 다루어진 방법</TD></TR>
    <TR><TD>II. Goodwill impairment assessment</TD></TR>
    </TABLE>
    """

    outcome = parse_kam_items(body)

    assert outcome.status == "ambiguous"
    assert outcome.items == []


def test_parse_outcome_requires_explicit_role_for_generic_td_title():
    from kreports.processor.kam_parser import parse_kam_items

    body = """
    <TITLE>핵심감사사항</TITLE>
    <TABLE>
    <TR><TH>1. 수익인식</TH></TR>
    <TR><TD>핵심감사사항으로 선정한 이유</TD></TR>
    <TR><TD>기간귀속 판단 위험</TD></TR>
    <TR><TD>감사에서 다루어진 방법</TD></TR>
    <TR><TD>계약 표본 검사</TD></TR>
    <TR><TD>2. 연결범위의 적정성</TD></TR>
    <TR><TD>핵심감사사항으로 결정한 이유</TD></TR>
    <TR><TD>연결대상 판단 위험</TD></TR>
    <TR><TD>감사인의 대응</TD></TR>
    <TR><TD>지배력 판단 검토</TD></TR>
    </TABLE>
    """

    outcome = parse_kam_items(body)

    assert outcome.status == "ambiguous"
    assert outcome.items == []


@pytest.mark.parametrize(
    "attribute",
    [
        pytest.param('class="not-title"', id="negated-title-class"),
        pytest.param('class="data-title-value"', id="data-title-class"),
        pytest.param('class="non-heading"', id="negated-heading-class"),
        pytest.param('CLASS="NoT-TiTlE"', id="mixed-case-negated-class"),
        pytest.param('id="section-heading"', id="heading-id"),
        pytest.param('ID="TITLE"', id="mixed-case-title-id"),
        pytest.param('role="not-heading"', id="nonexact-heading-role"),
    ],
)
def test_parse_outcome_does_not_infer_td_heading_from_generic_attributes(
    attribute,
):
    from kreports.processor.kam_parser import parse_kam_items

    body = f"""
    <TITLE>핵심감사사항</TITLE>
    <TABLE>
    <TR><TH>1. 수익인식</TH></TR>
    <TR><TD>핵심감사사항으로 선정한 이유</TD></TR>
    <TR><TD>기간귀속 판단 위험</TD></TR>
    <TR><TD>감사에서 다루어진 방법</TD></TR>
    <TR><TD>계약 표본 검사</TD></TR>
    <TR><TD {attribute}>2. 재고자산 평가</TD></TR>
    <TR><TD>핵심감사사항으로 선정한 이유</TD></TR>
    <TR><TD>순실현가능가치 추정 위험</TD></TR>
    <TR><TD>감사에서 다루어진 방법</TD></TR>
    <TR><TD>예상판매가격 검사</TD></TR>
    </TABLE>
    """

    outcome = parse_kam_items(body)

    assert outcome.status == "ambiguous"
    assert outcome.items == []


def test_parse_outcome_recovers_malformed_nested_title_deterministically():
    from kreports.processor.kam_parser import parse_kam_items

    body = """
    <TITLE>Key Audit Matters</TITLE>
    <TITLE><P>Revenue recognition</TITLE>
    <P>Why the matter was determined to be a key audit matter</P>
    <P>Contract cut-off requires significant judgment.</P>
    <P>How the matter was addressed in the audit</P>
    <P>We inspected contract samples.<BR/></P>
    """

    first = parse_kam_items(body)
    second = parse_kam_items(body)

    assert first == second
    assert first.status == "complete"
    assert [item.title for item in first.items] == ["Revenue recognition"]


@pytest.mark.parametrize(
    "broken_title",
    [
        pytest.param("<TITLE>핵심감사사항", id="unclosed-title"),
        pytest.param("<TITLE>핵심감사사항</TH>", id="mismatched-title-close"),
    ],
)
def test_parse_outcome_does_not_leak_malformed_title_ancestry(broken_title):
    from kreports.processor.kam_parser import parse_kam_items

    body = f"""
    {broken_title}
    <P>수익인식</P>
    <P>핵심감사사항으로 선정한 이유</P>
    <P>기간귀속 판단 위험</P>
    <P>감사에서 다루어진 방법</P>
    <P>계약 표본 검사</P>
    <P>재고자산 평가</P>
    <P>핵심감사사항으로 선정한 이유</P>
    <P>복합계약 판단 위험</P>
    <P>감사에서 다루어진 방법</P>
    <P>예상판매가격 검사</P>
    """

    outcome = parse_kam_items(body)

    assert outcome.status == "ambiguous"
    assert outcome.items == []


def test_parse_outcome_sanitizes_multiple_mixed_case_malformed_blocks():
    from kreports.processor.kam_parser import parse_kam_items

    body = """
    <tItLe>핵심감사사항</tH>
    <TITLE/>
    <P>수익인식</P>
    <P>핵심감사사항으로 선정한 이유</P>
    <P>기간귀속 판단 위험</P>
    <P>감사에서 다루어진 방법</P>
    <P>계약 표본 검사</P>
    <tH data-kind="decorative"><P></P></TiTlE>
    <P>재고자산 평가</P>
    <P>핵심감사사항으로 선정한 이유</P>
    <P>순실현가능가치 추정 위험</P>
    <P>감사에서 다루어진 방법</P>
    <P>예상판매가격 검사</P>
    """

    first = parse_kam_items(body)
    second = parse_kam_items(body)

    assert first == second
    assert first.status == "ambiguous"
    assert first.items == []


@pytest.mark.parametrize(
    "page_container",
    [
        pytest.param("TITLE", id="title"),
        pytest.param("TH", id="table-header"),
        pytest.param('TD role="heading"', id="explicit-heading-cell"),
    ],
)
def test_parse_outcome_does_not_assign_unrelated_strong_page_heading(
    page_container,
):
    from kreports.processor.kam_parser import parse_kam_items

    tag = page_container.split()[0]
    body = f"""
    <TITLE>핵심감사사항</TITLE>
    <TITLE>1. 수익인식</TITLE>
    <P>핵심감사사항으로 선정한 이유</P>
    <P>기간귀속 판단 위험</P>
    <P>감사에서 다루어진 방법</P>
    <P>계약 표본 검사</P>
    <{page_container}>Page 2</{tag}>
    <P>2. 매출채권 표본 추출</P>
    <P>핵심감사사항으로 선정한 이유</P>
    <P>복합계약 판단 위험</P>
    <P>감사에서 다루어진 방법</P>
    <P>추가 감사절차</P>
    """

    outcome = parse_kam_items(body)

    assert outcome.status == "ambiguous"
    assert outcome.items == []


@pytest.mark.parametrize(
    "attributes",
    [
        pytest.param(
            'role="heading" title="1 > 0"',
            id="double-quoted-role-first",
        ),
        pytest.param(
            'title="1 > 0" role="heading"',
            id="double-quoted-role-last",
        ),
        pytest.param(
            "role='heading' title='1 > 0'",
            id="single-quoted-role-first",
        ),
        pytest.param(
            "title='1 > 0' role='heading'",
            id="single-quoted-role-last",
        ),
    ],
)
def test_parse_outcome_preserves_quoted_greater_than_in_heading_attributes(
    attributes,
):
    from kreports.processor.kam_parser import parse_kam_items

    def heading(value):
        return f"<TD {attributes}>{value}</TD>"

    body = f"""
    <TITLE>Key Audit Matters</TITLE>
    {heading("Revenue recognition")}
    <P>Why the matter was determined to be a key audit matter</P>
    <P>Contract cut-off requires significant judgment.</P>
    <P>How the matter was addressed in the audit</P>
    <P>We inspected contract samples.</P>
    {heading("Classification of leases")}
    <P>Why the matter was determined to be a key audit matter</P>
    <P>Lease classification requires significant judgment.</P>
    <P>How the matter was addressed in the audit</P>
    <P>We reviewed management's classification.</P>
    """

    outcome = parse_kam_items(body)

    assert outcome.status == "complete"
    assert [item.title for item in outcome.items] == [
        "Revenue recognition",
        "Classification of leases",
    ]


@pytest.mark.parametrize(
    "malformed_self_close",
    [
        pytest.param("<TITLE/ >", id="slash-space"),
        pytest.param("<TITLE / >", id="space-slash-space"),
        pytest.param("<tItLe / >", id="mixed-case"),
    ],
)
def test_parse_outcome_does_not_open_malformed_self_closing_title(
    malformed_self_close,
):
    from kreports.processor.kam_parser import parse_kam_items

    body = f"""
    <TITLE>핵심감사사항</TITLE>
    <TITLE>1. 수익인식</TITLE>
    <P>핵심감사사항으로 선정한 이유</P>
    <P>기간귀속 판단 위험</P>
    <P>감사에서 다루어진 방법</P>
    <P>계약 표본 검사</P>
    {malformed_self_close}2. 매출채권 표본 추출
    <P>핵심감사사항으로 선정한 이유</P>
    <P>복합계약 판단 위험</P>
    <P>감사에서 다루어진 방법</P>
    <P>추가 감사절차</P>
    """

    first = parse_kam_items(body)
    second = parse_kam_items(body)

    assert first == second
    assert first.status == "ambiguous"
    assert first.items == []


@pytest.mark.parametrize(
    "stray_close",
    [
        pytest.param("</DIV>", id="div"),
        pytest.param("</SPAN>", id="span"),
        pytest.param("</sPaN>", id="mixed-case-span"),
    ],
)
def test_parse_outcome_clears_strong_ancestry_on_any_stray_close(stray_close):
    from kreports.processor.kam_parser import parse_kam_items

    body = f"""
    <TITLE>핵심감사사항</TITLE>
    <TITLE>1. 수익인식</TITLE>
    <P>핵심감사사항으로 선정한 이유</P>
    <P>기간귀속 판단 위험</P>
    <P>감사에서 다루어진 방법</P>
    <P>계약 표본 검사</P>
    <TITLE>{stray_close}
    <P>2. 매출채권 표본 추출</P>
    <P>핵심감사사항으로 선정한 이유</P>
    <P>복합계약 판단 위험</P>
    <P>감사에서 다루어진 방법</P>
    <P>추가 감사절차</P>
    """

    outcome = parse_kam_items(body)

    assert outcome.status == "ambiguous"
    assert outcome.items == []


@pytest.mark.parametrize(
    "duplicate_roles",
    [
        pytest.param(
            'role="heading" role="cell"',
            id="heading-then-cell",
        ),
        pytest.param(
            'role="cell" role="heading"',
            id="cell-then-heading",
        ),
        pytest.param(
            'ROLE="HEADING" role="cell"',
            id="mixed-case-heading-then-cell",
        ),
        pytest.param(
            "role=cell ROLE=HEADING",
            id="unquoted-cell-then-heading",
        ),
        pytest.param(
            "role=heading ROLE=heading",
            id="duplicate-unquoted-heading",
        ),
    ],
)
def test_parse_outcome_rejects_duplicate_role_heading_evidence(
    duplicate_roles,
):
    from kreports.processor.kam_parser import parse_kam_items

    body = f"""
    <TITLE>핵심감사사항</TITLE>
    <TABLE>
    <TR><TH>1. 수익인식</TH></TR>
    <TR><TD>핵심감사사항으로 선정한 이유</TD></TR>
    <TR><TD>기간귀속 판단 위험</TD></TR>
    <TR><TD>감사에서 다루어진 방법</TD></TR>
    <TR><TD>계약 표본 검사</TD></TR>
    <TR><TD {duplicate_roles}>2. 매출채권 표본 추출</TD></TR>
    <TR><TD>핵심감사사항으로 선정한 이유</TD></TR>
    <TR><TD>복합계약 판단 위험</TD></TR>
    <TR><TD>감사에서 다루어진 방법</TD></TR>
    <TR><TD>추가 감사절차</TD></TR>
    </TABLE>
    """

    outcome = parse_kam_items(body)

    assert outcome.status == "ambiguous"
    assert outcome.items == []


@pytest.mark.parametrize(
    "self_closing_boundaries",
    [
        pytest.param("<TITLE/>", id="title"),
        pytest.param("<TITLE / >", id="malformed-spaced-title"),
        pytest.param("<tAbLe/>", id="mixed-case-table"),
        pytest.param("<P/>", id="paragraph"),
        pytest.param(
            '<TD role="heading"/>',
            id="no-content-explicit-heading-cell",
        ),
        pytest.param(
            "<TITLE/><TABLE/><P/>",
            id="multiple-blocks",
        ),
        pytest.param(
            "<DiV/><tItLe / ><SPAN/>",
            id="unknown-and-malformed-mixed-case",
        ),
    ],
)
def test_parse_outcome_self_closing_blocks_clear_prior_empty_title(
    self_closing_boundaries,
):
    from kreports.processor.kam_parser import parse_kam_items

    body = f"""
    <TITLE>핵심감사사항</TITLE>
    <TITLE>1. 수익인식</TITLE>
    <P>핵심감사사항으로 선정한 이유</P>
    <P>기간귀속 판단 위험</P>
    <P>감사에서 다루어진 방법</P>
    <P>계약 표본 검사</P>
    <TITLE>{self_closing_boundaries}
    <P>2. 매출채권 표본 추출</P>
    <P>핵심감사사항으로 선정한 이유</P>
    <P>복합계약 판단 위험</P>
    <P>감사에서 다루어진 방법</P>
    <P>추가 감사절차</P>
    """

    first = parse_kam_items(body)
    second = parse_kam_items(body)

    assert first == second
    assert first.status == "ambiguous"
    assert first.items == []


def test_parse_outcome_preserves_br_as_a_line_boundary():
    from kreports.processor.kam_parser import parse_kam_items

    body = """
    <TITLE>Key Audit Matters</TITLE>
    <TITLE>Revenue recognition</TITLE>
    <P>Why the matter was determined to be a key audit matter</P>
    <P>Contract cut-off requires significant judgment.</P>
    <P>How the matter was addressed in the audit</P>
    <P>We inspected contract samples.<BR/>We recalculated cut-off.</P>
    """

    outcome = parse_kam_items(body)

    assert outcome.status == "complete"
    assert len(outcome.items) == 1
    assert outcome.items[0].audit_response_text == (
        "We inspected contract samples.\nWe recalculated cut-off."
    )


@pytest.mark.parametrize(
    "container",
    [
        pytest.param("TITLE", id="title"),
        pytest.param("TH", id="table-header"),
        pytest.param('TD role="heading"', id="explicit-heading-cell"),
    ],
)
@pytest.mark.parametrize(
    "inline_markup",
    [
        pytest.param("<SPAN/>{value}", id="before"),
        pytest.param("{first} <IMG/>{rest}", id="middle"),
        pytest.param(
            "<sPaN/><WBR/><x-kam-inline/>{value}",
            id="multiple-mixed-case",
        ),
    ],
)
def test_parse_outcome_preserves_inline_self_closing_tags_in_strong_heading(
    container,
    inline_markup,
):
    from kreports.processor.kam_parser import parse_kam_items

    def heading(value):
        first, rest = value.split(" ", 1)
        content = inline_markup.format(
            value=value,
            first=first,
            rest=rest,
        )
        tag = container.split()[0]
        return f"<{container}>{content}</{tag}>"

    body = f"""
    <TITLE>Key Audit Matters</TITLE>
    {heading("Revenue recognition")}
    <P>Why the matter was determined to be a key audit matter</P>
    <P>Contract cut-off requires significant judgment.</P>
    <P>How the matter was addressed in the audit</P>
    <P>We inspected contract samples.</P>
    {heading("Classification of leases")}
    <P>Why the matter was determined to be a key audit matter</P>
    <P>Lease classification requires significant judgment.</P>
    <P>How the matter was addressed in the audit</P>
    <P>We reviewed management's classification.</P>
    """

    outcome = parse_kam_items(body)

    assert outcome.status == "complete"
    assert [item.title for item in outcome.items] == [
        "Revenue recognition",
        "Classification of leases",
    ]


def test_parse_outcome_reads_cdata_inside_title_ancestry():
    from kreports.processor.kam_parser import parse_kam_items

    body = """
    <TITLE>Key Audit Matters</TITLE>
    <TITLE><![CDATA[Revenue recognition]]></TITLE>
    <P>Why the matter was determined to be a key audit matter</P>
    <P>Contract cut-off requires significant judgment.</P>
    <P>How the matter was addressed in the audit</P>
    <P>We inspected contract samples.</P>
    """

    outcome = parse_kam_items(body)

    assert outcome.status == "complete"
    assert len(outcome.items) == 1
    assert outcome.items[0].title == "Revenue recognition"


def test_parse_outcome_reads_full_plain_audit_body_from_cdata():
    from kreports.processor.kam_parser import parse_kam_items

    body = """
    <![CDATA[
    Key Audit Matters
    1. Revenue recognition
    Why the matter was determined to be a key audit matter
    Contract cut-off requires significant judgment.
    How the matter was addressed in the audit
    We inspected contract samples.
    ]]>
    """

    outcome = parse_kam_items(body)

    assert outcome.status == "complete"
    assert [item.title for item in outcome.items] == ["Revenue recognition"]


def test_parse_outcome_rejects_silent_partial_from_unterminated_cdata():
    from kreports.processor.kam_parser import parse_kam_items

    body = """
    <TITLE>Key Audit Matters</TITLE>
    <TITLE>Revenue recognition</TITLE>
    <P>Why the matter was determined to be a key audit matter</P>
    <P>Contract cut-off requires significant judgment.</P>
    <P>How the matter was addressed in the audit</P>
    <P>We inspected contract samples.</P>
    <TITLE><![CDATA[Inventory valuation
    <P>Why the matter was determined to be a key audit matter</P>
    <P>Inventory estimates require significant judgment.</P>
    """

    first = parse_kam_items(body)
    second = parse_kam_items(body)

    assert first == second
    assert first.status == "error"
    assert first.items == []
    assert "malformed_cdata" in first.limitations


def test_parse_outcome_rejects_silent_partial_explicit_second_matter():
    from kreports.processor.kam_parser import parse_kam_items

    body = """
    <TITLE>Key Audit Matters</TITLE>
    <TITLE>Revenue recognition</TITLE>
    <P>Why the matter was determined to be a key audit matter</P>
    <P>Contract cut-off requires significant judgment.</P>
    <P>How the matter was addressed in the audit</P>
    <P>We inspected contract samples.</P>
    <TITLE>Inventory valuation</TITLE>
    <P>Why the matter was determined to be a key audit matter</P>
    <P>Inventory estimates require significant judgment.</P>
    """

    outcome = parse_kam_items(body)

    assert outcome.status == "error"
    assert outcome.items == []
    assert "incomplete_kam_structure" in outcome.limitations


def test_parse_outcome_ignores_metadata_and_preserves_entities():
    from kreports.processor.kam_parser import parse_kam_items

    body = """
    <!DOCTYPE audit-report>
    <?xml version="1.0"?>
    <TITLE>Key Audit Matters</TITLE>
    <!-- page metadata -->
    <TITLE>Revenue <!-- inline comment --><?page 2?> recognition</TITLE>
    <P>Why the matter was determined to be a key audit matter</P>
    <P>Contract &amp; cut-off requires significant judgment.</P>
    <P>How the matter was addressed in the audit</P>
    <P>We inspected contract samples &amp; recalculated cut-off.</P>
    """

    outcome = parse_kam_items(body)

    assert outcome.status == "complete"
    assert len(outcome.items) == 1
    assert outcome.items[0].title == "Revenue recognition"
    assert outcome.items[0].reason_text == (
        "Contract & cut-off requires significant judgment."
    )
    assert outcome.items[0].audit_response_text == (
        "We inspected contract samples & recalculated cut-off."
    )


def test_parse_outcome_reports_bounded_input_truncation():
    from kreports.processor.kam_parser import parse_kam_items

    outcome = parse_kam_items("x" * 2_000_001)

    assert outcome.status == "error"
    assert outcome.items == []
    assert "input_truncated" in outcome.limitations


def test_htmlparser_markup_adapter_avoids_unbounded_suffix_copies_per_tag():
    from kreports.processor.kam_parser import _htmlparser_safe_markup

    class SourceWithoutUnboundedSuffixCopy(str):
        def __getitem__(self, key):
            if isinstance(key, slice) and key.stop is None:
                raise AssertionError("markup adapter must use bounded indexes")
            return super().__getitem__(key)

    source = SourceWithoutUnboundedSuffixCopy("<P>x" * 100_000)

    markup, limitations = _htmlparser_safe_markup(source)

    assert markup == source
    assert limitations == []


@pytest.mark.parametrize(
    "invalid_matter",
    [
        pytest.param(
            """
            <TITLE>Inventory valuation</TITLE>
            <P>Why the matter was determined to be a key audit matter</P>
            <P>How the matter was addressed in the audit</P>
            <P>We inspected inventory samples.</P>
            """,
            id="empty-reason",
        ),
        pytest.param(
            """
            <TITLE>Inventory valuation</TITLE>
            <P>Why the matter was determined to be a key audit matter</P>
            <P>Inventory estimates require significant judgment.</P>
            <P>How the matter was addressed in the audit</P>
            """,
            id="empty-response",
        ),
        pytest.param(
            """
            <TITLE>Inventory valuation</TITLE>
            <P>Why the matter was determined to be a key audit matter</P>
            <P>How the matter was addressed in the audit</P>
            """,
            id="both-empty",
        ),
        pytest.param(
            """
            <TITLE>Inventory valuation</TITLE>
            <P>Why the matter was determined to be a key audit matter</P>
            <P>Inventory estimates require significant judgment.</P>
            <P>How the matter was addressed in the audit</P>
            <TITLE>Auditor's Responsibilities for the Audit of the Financial Statements</TITLE>
            """,
            id="empty-response-before-trailing-responsibilities",
        ),
    ],
)
def test_parse_outcome_rejects_any_incomplete_explicit_frame(invalid_matter):
    from kreports.processor.kam_parser import parse_kam_items

    body = f"""
    <TITLE>Key Audit Matters</TITLE>
    <TITLE>Revenue recognition</TITLE>
    <P>Why the matter was determined to be a key audit matter</P>
    <P>Contract cut-off requires significant judgment.</P>
    <P>How the matter was addressed in the audit</P>
    <P>We inspected contract samples.</P>
    {invalid_matter}
    """

    outcome = parse_kam_items(body)

    assert outcome.status == "error"
    assert outcome.items == []
    assert "incomplete_kam_structure" in outcome.limitations


@pytest.mark.parametrize(
    ("reason_body", "response_body"),
    [
        pytest.param(
            "Cash-flow forecasts require significant judgment.",
            "We tested management's forecasts.",
            id="complete-body",
        ),
        pytest.param(
            "   ",
            "We tested management's forecasts.",
            id="empty-reason",
        ),
        pytest.param(
            "Cash-flow forecasts require significant judgment.",
            "   ",
            id="empty-response",
        ),
        pytest.param("   ", "   ", id="both-empty"),
    ],
)
def test_parse_outcome_preserves_ambiguous_generic_td_boundary(
    reason_body,
    response_body,
):
    from kreports.processor.kam_parser import parse_kam_items

    body = f"""
    <TITLE>Key Audit Matters</TITLE>
    <TITLE>Revenue recognition</TITLE>
    <P>Why the matter was determined to be a key audit matter</P>
    <P>Contract cut-off requires significant judgment.</P>
    <P>How the matter was addressed in the audit</P>
    <P>We inspected contract samples.</P>
    <TABLE>
    <TR><TD>Goodwill impairment</TD></TR>
    <TR><TD>Why the matter was determined to be a key audit matter</TD></TR>
    <TR><TD>{reason_body}</TD></TR>
    <TR><TD>How the matter was addressed in the audit</TD></TR>
    <TR><TD>{response_body}</TD></TR>
    </TABLE>
    """

    outcome = parse_kam_items(body)

    assert outcome.status == "ambiguous"
    assert outcome.items == []
    assert outcome.limitations == ["ambiguous_boundary"]


def test_parse_outcome_gives_incomplete_frame_precedence_over_unrelated_ambiguity():
    from kreports.processor.kam_parser import parse_kam_items

    revenue = """
    <TITLE>Revenue recognition</TITLE>
    <P>Why the matter was determined to be a key audit matter</P>
    <P>Contract cut-off requires significant judgment.</P>
    <P>How the matter was addressed in the audit</P>
    <P>We inspected contract samples.</P>
    """
    incomplete_inventory = """
    <TITLE>Inventory valuation</TITLE>
    <P>Why the matter was determined to be a key audit matter</P>
    <P>How the matter was addressed in the audit</P>
    <P>We inspected inventory samples.</P>
    """
    ambiguous_goodwill = """
    <TABLE>
    <TR><TD>Goodwill impairment</TD></TR>
    <TR><TD>Why the matter was determined to be a key audit matter</TD></TR>
    <TR><TD>Cash-flow forecasts require significant judgment.</TD></TR>
    <TR><TD>How the matter was addressed in the audit</TD></TR>
    <TR><TD>We tested management's forecasts.</TD></TR>
    </TABLE>
    """

    def outcome_for(*matters):
        return parse_kam_items(
            "<TITLE>Key Audit Matters</TITLE>" + "".join(matters)
        )

    incomplete_control = outcome_for(revenue, incomplete_inventory)
    ambiguous_control = outcome_for(revenue, ambiguous_goodwill)

    assert incomplete_control.status == "error"
    assert incomplete_control.items == []
    assert incomplete_control.limitations == ["incomplete_kam_structure"]
    assert ambiguous_control.status == "ambiguous"
    assert ambiguous_control.items == []
    assert ambiguous_control.limitations == ["ambiguous_boundary"]

    for outcome in (
        outcome_for(revenue, incomplete_inventory, ambiguous_goodwill),
        outcome_for(revenue, ambiguous_goodwill, incomplete_inventory),
    ):
        assert outcome.status == "error"
        assert outcome.items == []
        assert outcome.limitations == ["incomplete_kam_structure"]


@pytest.mark.parametrize(
    "cdata_name",
    [
        pytest.param("cdata", id="lowercase"),
        pytest.param("CdAtA", id="mixed-case"),
    ],
)
def test_parse_outcome_handles_cdata_case_consistently(cdata_name):
    from kreports.processor.kam_parser import parse_kam_items

    body = f"""
    <TITLE>Key Audit Matters</TITLE>
    <TITLE><![{cdata_name}[Revenue recognition]]></TITLE>
    <P>Why the matter was determined to be a key audit matter</P>
    <P>Contract cut-off requires significant judgment.</P>
    <P>How the matter was addressed in the audit</P>
    <P>We inspected contract samples.</P>
    """

    outcome = parse_kam_items(body)

    assert outcome.status == "complete"
    assert len(outcome.items) == 1
    assert outcome.items[0].title == "Revenue recognition"


def test_parse_outcome_rejects_spaced_malformed_cdata_opener():
    from kreports.processor.kam_parser import parse_kam_items

    body = """
    <TITLE>Key Audit Matters</TITLE>
    <TITLE><![CDATA [Revenue recognition]]></TITLE>
    <P>Why the matter was determined to be a key audit matter</P>
    <P>Contract cut-off requires significant judgment.</P>
    <P>How the matter was addressed in the audit</P>
    <P>We inspected contract samples.</P>
    """

    first = parse_kam_items(body)
    second = parse_kam_items(body)

    assert first == second
    assert first.status == "error"
    assert first.items == []
    assert "malformed_cdata" in first.limitations


def test_parse_outcome_concatenates_adjacent_cdata_title_payloads():
    from kreports.processor.kam_parser import parse_kam_items

    body = """
    <TITLE>Key Audit Matters</TITLE>
    <TITLE><![CDATA[Revenue ]]><![CDATA[recognition]]></TITLE>
    <P>Why the matter was determined to be a key audit matter</P>
    <P>Contract cut-off requires significant judgment.</P>
    <P>How the matter was addressed in the audit</P>
    <P>We inspected contract samples.</P>
    """

    outcome = parse_kam_items(body)

    assert outcome.status == "complete"
    assert len(outcome.items) == 1
    assert outcome.items[0].title == "Revenue recognition"


def test_parse_outcome_reads_split_full_body_cdata_payloads():
    from kreports.processor.kam_parser import parse_kam_items

    body = """
    <![CDATA[
    Key Audit Matters
    1. Revenue recognition
    Why the matter was determined to be a key audit matter
    ]]><![CDATA[
    Contract cut-off requires significant judgment.
    How the matter was addressed in the audit
    We inspected contract samples.
    ]]>
    """

    outcome = parse_kam_items(body)

    assert outcome.status == "complete"
    assert len(outcome.items) == 1
    assert outcome.items[0].title == "Revenue recognition"


@pytest.mark.parametrize(
    (
        "middle_reason_heading",
        "middle_reason",
        "middle_response_heading",
        "expected_status",
        "expected_titles",
    ),
    [
        pytest.param(
            "Why the matter was determined to be a key audit matter",
            "   ",
            "How the matter was addressed in the audit",
            "error",
            [],
            id="empty-reason-with-repeated-headings-fails-closed",
        ),
        pytest.param(
            "핵심감사사항으로 선정한 이유",
            "   ",
            "감사에서 다루어진 방법",
            "error",
            [],
            id="empty-reason-fails-closed",
        ),
        pytest.param(
            "핵심감사사항으로 선정한 이유",
            "재고 추정에는 유의적인 판단이 필요합니다.",
            "감사에서 다루어진 방법",
            "complete",
            [
                "Revenue recognition",
                "Inventory valuation",
                "Goodwill impairment",
            ],
            id="body-valid-preserves-all-three-items",
        ),
    ],
)
def test_parse_outcome_validates_every_discovered_matter_frame(
    middle_reason_heading,
    middle_reason,
    middle_response_heading,
    expected_status,
    expected_titles,
):
    from kreports.processor.kam_parser import parse_kam_items

    body = f"""
    <TITLE>Key Audit Matters</TITLE>
    <TITLE>Revenue recognition</TITLE>
    <P>Why the matter was determined to be a key audit matter</P>
    <P>Contract cut-off requires significant judgment.</P>
    <P>How the matter was addressed in the audit</P>
    <P>We inspected contract samples.</P>
    <P>Inventory valuation</P>
    <P>{middle_reason_heading}</P>
    <P>{middle_reason}</P>
    <P>{middle_response_heading}</P>
    <P>We inspected inventory samples.</P>
    <TITLE>Goodwill impairment</TITLE>
    <P>Why the matter was determined to be a key audit matter</P>
    <P>Cash-flow forecasts require significant judgment.</P>
    <P>How the matter was addressed in the audit</P>
    <P>We tested management's forecasts.</P>
    """

    outcome = parse_kam_items(body)

    assert outcome.status == expected_status
    assert [item.title for item in outcome.items] == expected_titles
    if expected_status == "error":
        assert "incomplete_kam_structure" in outcome.limitations


@pytest.mark.parametrize(
    "empty_title",
    [
        pytest.param("<![CDATA[]]>", id="empty-cdata"),
        pytest.param(" \n\t ", id="whitespace"),
        pytest.param(
            "<SPAN><![CDATA[]]></SPAN>",
            id="nested-empty-cdata",
        ),
        pytest.param(
            "<SPAN> \n </SPAN>",
            id="nested-whitespace",
        ),
    ],
)
def test_parse_outcome_rejects_empty_explicit_title_with_heading_pair(
    empty_title,
):
    from kreports.processor.kam_parser import parse_kam_items

    body = f"""
    <TITLE>Key Audit Matters</TITLE>
    <TITLE>Revenue recognition</TITLE>
    <P>Why the matter was determined to be a key audit matter</P>
    <P>Contract cut-off requires significant judgment.</P>
    <P>How the matter was addressed in the audit</P>
    <P>We inspected contract samples.</P>
    <TITLE>{empty_title}</TITLE>
    <P>Why the matter was determined to be a key audit matter</P>
    <P>Inventory estimates require significant judgment.</P>
    <P>How the matter was addressed in the audit</P>
    <P>We inspected inventory samples.</P>
    """

    outcome = parse_kam_items(body)

    assert outcome.status == "error"
    assert outcome.items == []
    assert "incomplete_kam_structure" in outcome.limitations


def test_parse_outcome_uses_nearest_nonempty_adjacent_explicit_title():
    from kreports.processor.kam_parser import parse_kam_items

    body = """
    <TITLE>Key Audit Matters</TITLE>
    <TITLE><![CDATA[]]></TITLE>
    <TITLE>Revenue recognition</TITLE>
    <P>Why the matter was determined to be a key audit matter</P>
    <P>Contract cut-off requires significant judgment.</P>
    <P>How the matter was addressed in the audit</P>
    <P>We inspected contract samples.</P>
    """

    outcome = parse_kam_items(body)

    assert outcome.status == "complete"
    assert [item.title for item in outcome.items] == [
        "Revenue recognition",
    ]


@pytest.mark.parametrize(
    "unrelated_empty_container",
    [
        pytest.param("<TH></TH>", id="table-header"),
        pytest.param("<TD></TD>", id="table-cell"),
        pytest.param(
            '<TD role="heading"><SPAN></SPAN></TD>',
            id="nested-heading-cell",
        ),
    ],
)
def test_parse_outcome_ignores_unrelated_empty_table_container(
    unrelated_empty_container,
):
    from kreports.processor.kam_parser import parse_kam_items

    body = f"""
    <TITLE>Key Audit Matters</TITLE>
    <TABLE><TR>{unrelated_empty_container}</TR></TABLE>
    <TITLE>Revenue recognition</TITLE>
    <P>Why the matter was determined to be a key audit matter</P>
    <P>Contract cut-off requires significant judgment.</P>
    <P>How the matter was addressed in the audit</P>
    <P>We inspected contract samples.</P>
    """

    outcome = parse_kam_items(body)

    assert outcome.status == "complete"
    assert [item.title for item in outcome.items] == [
        "Revenue recognition",
    ]


@pytest.mark.parametrize(
    "metadata",
    [
        pytest.param(
            "<!-- literal <![CDATA[ is documentation -->",
            id="comment",
        ),
        pytest.param(
            "<?page literal <![CDATA[ marker ?>",
            id="processing-instruction",
        ),
        pytest.param(
            '<!DOCTYPE audit SYSTEM "<![CDATA[literal">',
            id="doctype",
        ),
        pytest.param(
            '<!DOCTYPE audit SYSTEM "urn:x > <![CDATA[ literal">',
            id="doctype-quoted-greater-than",
        ),
        pytest.param(
            '<!DOCTYPE audit [ <!ENTITY sample "urn:x > <![CDATA[ literal"> ]>',
            id="doctype-internal-subset",
        ),
        pytest.param(
            "<!DOCTYPE audit [ <!-- literal [ ] \"' > bracket --> <!ELEMENT audit ANY> ]>",
            id="doctype-internal-comment",
        ),
        pytest.param(
            '<!DOCTYPE audit [ <?metadata literal ] > ?> <!ELEMENT audit ANY> ]>',
            id="doctype-internal-processing-instruction",
        ),
        pytest.param(
            '<SCRIPT>const marker = "<![CDATA[not closed";</SCRIPT>',
            id="script",
        ),
        pytest.param(
            '<DIV data-marker="literal <![CDATA[ not a declaration">x</DIV>',
            id="attribute-with-spaced-literal",
        ),
        pytest.param(
            '<P title="<![CDATA[ docs">metadata</P>',
            id="attribute-with-literal",
        ),
        pytest.param(
            '<STYLE>.x:before{content:"<![CDATA["}</STYLE>',
            id="style",
        ),
    ],
)
def test_parse_outcome_ignores_cdata_literals_outside_declaration_context(
    metadata,
):
    from kreports.processor.kam_parser import parse_kam_items

    body = f"""
    {metadata}
    <TITLE>Key Audit Matters</TITLE>
    <TITLE>Revenue recognition</TITLE>
    <P>Why the matter was determined to be a key audit matter</P>
    <P>Contract cut-off requires significant judgment.</P>
    <P>How the matter was addressed in the audit</P>
    <P>We inspected contract samples.</P>
    """

    outcome = parse_kam_items(body)

    assert outcome.status == "complete"
    assert [item.title for item in outcome.items] == [
        "Revenue recognition",
    ]


def test_parse_outcome_fails_closed_for_unclosed_doctype_before_kam():
    from kreports.processor.kam_parser import parse_kam_items

    body = """
    <!DOCTYPE audit SYSTEM "unterminated
    <TITLE>Key Audit Matters</TITLE>
    <TITLE>Revenue recognition</TITLE>
    <P>Why the matter was determined to be a key audit matter</P>
    <P>Contract cut-off requires significant judgment.</P>
    <P>How the matter was addressed in the audit</P>
    <P>We inspected contract samples.</P>
    """

    outcome = parse_kam_items(body)

    assert outcome.status == "error"
    assert outcome.items == []
    assert "malformed_doctype" in outcome.limitations


@pytest.mark.parametrize(
    "metadata",
    [
        pytest.param(
            "<!DOCTYPE audit [ <!-- unterminated",
            id="unterminated-internal-comment",
        ),
        pytest.param(
            "<!DOCTYPE audit [ <?metadata unterminated",
            id="unterminated-internal-processing-instruction",
        ),
    ],
)
def test_parse_outcome_fails_closed_for_unclosed_doctype_metadata(metadata):
    from kreports.processor.kam_parser import parse_kam_items

    body = f"""
    {metadata}
    <TITLE>Key Audit Matters</TITLE>
    <TITLE>Revenue recognition</TITLE>
    <P>Why the matter was determined to be a key audit matter</P>
    <P>Contract cut-off requires significant judgment.</P>
    <P>How the matter was addressed in the audit</P>
    <P>We inspected contract samples.</P>
    """

    outcome = parse_kam_items(body)

    assert outcome.status == "error"
    assert outcome.items == []
    assert "malformed_doctype" in outcome.limitations


def test_parse_outcome_does_not_accept_kam_like_text_after_doctype_comment():
    from kreports.processor.kam_parser import parse_kam_items

    body = """
    <!DOCTYPE audit [
    <!-- literal ] bracket -->
    Key Audit Matters
    1. Revenue recognition
    Why the matter was determined to be a key audit matter
    Contract cut-off requires significant judgment.
    How the matter was addressed in the audit
    We inspected contract samples.
    ]>
    """

    outcome = parse_kam_items(body)

    assert outcome.status == "no_kam"
    assert outcome.items == []


@pytest.mark.parametrize("raw_tag", ["SCRIPT", "STYLE"])
def test_parse_outcome_rejects_kam_evidence_inside_suppressed_raw_text(raw_tag):
    from kreports.processor.kam_parser import parse_kam_items

    body = f"""
    <{raw_tag}>
    Key Audit Matters
    1. Revenue recognition
    Why the matter was determined to be a key audit matter
    Contract cut-off requires significant judgment.
    How the matter was addressed in the audit
    We inspected contract samples.
    </{raw_tag}>
    """

    outcome = parse_kam_items(body)

    assert outcome.status == "no_kam"
    assert outcome.items == []


@pytest.mark.parametrize("raw_tag", ["SCRIPT", "STYLE"])
def test_parse_outcome_rejects_kam_evidence_inside_unclosed_raw_text(raw_tag):
    from kreports.processor.kam_parser import parse_kam_items

    body = f"""
    <{raw_tag}>
    Key Audit Matters
    1. Revenue recognition
    Why the matter was determined to be a key audit matter
    Contract cut-off requires significant judgment.
    How the matter was addressed in the audit
    We inspected contract samples.
    """

    outcome = parse_kam_items(body)

    assert outcome.status == "no_kam"
    assert outcome.items == []


def test_parse_outcome_does_not_split_or_add_matters_from_script_payload():
    from kreports.processor.kam_parser import parse_kam_items

    body = """
    <TITLE>Key Audit Matters</TITLE>
    <TITLE>Revenue recognition</TITLE>
    <P>Why the matter was determined to be a key audit matter</P>
    <P>Contract cut-off requires significant judgment.</P>
    <P>How the matter was addressed in the audit</P>
    <P>We inspected contract samples.</P>
    <SCRIPT>
    <TITLE>Inventory valuation</TITLE>
    <P>Why the matter was determined to be a key audit matter</P>
    <P>Inventory estimates require significant judgment.</P>
    <P>How the matter was addressed in the audit</P>
    <P>We inspected inventory samples.</P>
    </SCRIPT>
    <TITLE>Classification of leases</TITLE>
    <P>Why the matter was determined to be a key audit matter</P>
    <P>Lease classification requires significant judgment.</P>
    <P>How the matter was addressed in the audit</P>
    <P>We reviewed management's classification.</P>
    """

    outcome = parse_kam_items(body)

    assert outcome.status == "complete"
    assert [item.title for item in outcome.items] == [
        "Revenue recognition",
        "Classification of leases",
    ]


def test_parser_collapses_only_adjacent_exact_full_matter_duplicates():
    from kreports.processor.kam_parser import extract_kam_items

    matter = """
    <TITLE>Revenue recognition</TITLE>
    <P>Why the matter was determined to be a key audit matter</P>
    <P>Contract cut-off requires significant judgment.</P>
    <P>How the matter was addressed in the audit</P>
    <P>We inspected contract samples.</P>
    """
    body = f"<TITLE>Key Audit Matters</TITLE>{matter}{matter}"
    single = extract_kam_items(f"<TITLE>Key Audit Matters</TITLE>{matter}")

    items = extract_kam_items(body)

    assert len(items) == 1
    assert items[0].ordinal == 1
    assert items[0].full_body_hash == single[0].full_body_hash


def test_parser_keeps_same_title_when_matter_body_differs():
    from kreports.processor.kam_parser import extract_kam_items

    body = """
    <TITLE>Key Audit Matters</TITLE>
    <TITLE>Revenue recognition</TITLE>
    <P>Why the matter was determined to be a key audit matter</P>
    <P>Contract cut-off requires significant judgment.</P>
    <P>How the matter was addressed in the audit</P>
    <P>We inspected contract samples.</P>
    <TITLE>Revenue recognition</TITLE>
    <P>Why the matter was determined to be a key audit matter</P>
    <P>Variable consideration requires significant judgment.</P>
    <P>How the matter was addressed in the audit</P>
    <P>We recalculated variable consideration.</P>
    """

    items = extract_kam_items(body)

    assert [item.ordinal for item in items] == [1, 2]
    assert [item.title for item in items] == [
        "Revenue recognition",
        "Revenue recognition",
    ]
    assert items[0].full_body_hash != items[1].full_body_hash


def test_parser_keeps_nonadjacent_exact_matters():
    from kreports.processor.kam_parser import extract_kam_items

    repeated = """
    <TITLE>Revenue recognition</TITLE>
    <P>Why the matter was determined to be a key audit matter</P>
    <P>Contract cut-off requires significant judgment.</P>
    <P>How the matter was addressed in the audit</P>
    <P>We inspected contract samples.</P>
    """
    middle = """
    <TITLE>Goodwill impairment</TITLE>
    <P>Why the matter was determined to be a key audit matter</P>
    <P>Forecast assumptions require significant judgment.</P>
    <P>How the matter was addressed in the audit</P>
    <P>We tested forecast assumptions.</P>
    """
    body = f"<TITLE>Key Audit Matters</TITLE>{repeated}{middle}{repeated}"

    items = extract_kam_items(body)

    assert [item.ordinal for item in items] == [1, 2, 3]
    assert [item.title for item in items] == [
        "Revenue recognition",
        "Goodwill impairment",
        "Revenue recognition",
    ]
    assert items[0].full_body_hash == items[2].full_body_hash


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
    assert "3. 재고자산 평가" not in items[0].audit_response_text
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


@pytest.mark.parametrize(
    "procedure",
    [
        pytest.param("1. 계약 표본 검사", id="arabic"),
        pytest.param("I. 계약 표본 검사", id="roman"),
        pytest.param("가. 계약 표본 검사", id="korean"),
    ],
)
def test_parser_keeps_unpunctuated_procedure_before_unnumbered_matter(procedure):
    from kreports.processor.kam_parser import extract_kam_items

    body = f"""
    핵심감사사항
    1. 수익인식
    핵심감사사항으로 선정한 이유
    기간귀속 판단에 유의적인 위험이 있습니다.
    감사인이 수행한 주요 절차
    {procedure}
    재고자산 평가
    핵심감사사항으로 결정한 이유
    순실현가능가치 추정에 유의적인 판단이 포함됩니다.
    감사에서 다루어진 방법
    표본 재고의 예상판매가격을 검사했습니다.
    """

    items = extract_kam_items(body)

    assert [item.title for item in items] == ["수익인식", "재고자산 평가"]
    assert items[0].audit_response_text == procedure
    assert "표본 재고" in items[1].audit_response_text


@pytest.mark.parametrize(
    "procedure",
    [
        pytest.param("1. 계약 관련", id="arabic"),
        pytest.param("I. 계약 관련", id="roman"),
        pytest.param("가. 계약 관련", id="korean"),
    ],
)
def test_parser_keeps_connector_procedure_before_unnumbered_matter(procedure):
    from kreports.processor.kam_parser import extract_kam_items

    body = f"""
    핵심감사사항
    1. 수익인식
    핵심감사사항으로 선정한 이유
    기간귀속 판단에 유의적인 위험이 있습니다.
    감사인이 수행한 주요 절차
    {procedure}
    재고자산 평가
    핵심감사사항으로 결정한 이유
    순실현가능가치 추정에 유의적인 판단이 포함됩니다.
    감사에서 다루어진 방법
    표본 재고의 예상판매가격을 검사했습니다.
    """

    items = extract_kam_items(body)

    assert [item.title for item in items] == ["수익인식", "재고자산 평가"]
    assert items[0].audit_response_text == procedure
    assert "표본 재고" in items[1].audit_response_text


@pytest.mark.parametrize(
    ("first_title", "next_title_lines", "expected_title"),
    [
        pytest.param(
            "수익인식",
            "2. 영업권 및\n현금창출단위 손상 평가",
            "영업권 및 현금창출단위 손상 평가",
            id="unnumbered-current",
        ),
        pytest.param(
            "1. Revenue recognition",
            "II. Goodwill\nimpairment assessment",
            "Goodwill impairment assessment",
            id="marker-family-switch",
        ),
    ],
)
def test_parser_accepts_distinct_wrapped_title_without_current_marker_family(
    first_title,
    next_title_lines,
    expected_title,
):
    from kreports.processor.kam_parser import extract_kam_items

    body = f"""
    Key Audit Matters
    {first_title}
    Why the matter was determined to be a key audit matter
    Contract cut-off requires significant judgment.
    How the matter was addressed in the audit
    We inspected contract samples.
    {next_title_lines}
    Why the matter was considered to be one of the most significant matters in the audit
    The recoverable amount depends on significant assumptions.
    Audit response
    We tested cash-flow forecasts and the discount rate.
    """

    items = extract_kam_items(body)

    assert len(items) == 2
    assert items[0].audit_response_text == "We inspected contract samples."
    assert items[1].title == expected_title


@pytest.mark.parametrize(
    ("first_title", "next_title_lines", "expected_title"),
    [
        pytest.param(
            "1. Revenue recognition",
            "I. Goodwill\nimpairment assessment",
            "Goodwill impairment assessment",
            id="arabic-to-initial-roman",
        ),
        pytest.param(
            "I. Revenue recognition",
            "가. 영업권\n손상 평가",
            "영업권 손상 평가",
            id="roman-to-initial-korean",
        ),
        pytest.param(
            "가. 수익인식",
            "1. 재고자산\n평가",
            "재고자산 평가",
            id="korean-to-restarted-arabic",
        ),
    ],
)
def test_parser_accepts_title_evidence_across_initial_marker_family_switch(
    first_title,
    next_title_lines,
    expected_title,
):
    from kreports.processor.kam_parser import extract_kam_items

    body = f"""
    Key Audit Matters
    {first_title}
    Why the matter was determined to be a key audit matter
    Contract cut-off requires significant judgment.
    How the matter was addressed in the audit
    We inspected contract samples.
    {next_title_lines}
    Why the matter was determined to be a key audit matter
    The estimate depends on significant assumptions.
    How the matter was addressed in the audit
    We tested the assumptions.
    """

    items = extract_kam_items(body)

    assert len(items) == 2
    assert items[0].audit_response_text == "We inspected contract samples."
    assert items[1].title == expected_title


def test_parser_keeps_initial_numbered_procedure_after_unnumbered_current_matter():
    from kreports.processor.kam_parser import extract_kam_items

    body = """
    핵심감사사항
    수익인식
    핵심감사사항으로 선정한 이유
    기간귀속 판단에 유의적인 위험이 있습니다.
    감사인이 수행한 주요 절차
    1. 계약 관련
    재고자산 평가
    핵심감사사항으로 결정한 이유
    순실현가능가치 추정에 유의적인 판단이 포함됩니다.
    감사에서 다루어진 방법
    표본 재고의 예상판매가격을 검사했습니다.
    """

    items = extract_kam_items(body)

    assert [item.title for item in items] == ["수익인식", "재고자산 평가"]
    assert items[0].audit_response_text == "1. 계약 관련"


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


@pytest.mark.parametrize(
    ("body", "expected_title", "excluded_response_fragment"),
    [
        pytest.param(
            """
            핵심감사사항
            1. 수익인식
            핵심감사사항으로 선정한 이유
            기간귀속 판단에 유의적인 위험이 있습니다.
            감사인이 수행한 주요 절차
            1. 계약 표본 검사
            2. 영업권
            손상 평가
            핵심감사사항으로 결정한 이유
            현금창출단위의 회수가능액 추정에 유의적인 판단이 포함됩니다.
            감사에서 다루어진 방법
            현금흐름과 할인율을 검사했습니다.
            """,
            "영업권 손상 평가",
            "2. 영업권",
            id="korean",
        ),
        pytest.param(
            """
            Key Audit Matters
            1. Revenue recognition
            Why the matter was determined to be a key audit matter
            Contract cut-off requires significant judgment.
            How the matter was addressed in the audit
            1. Inspect contract samples
            2. Goodwill
            impairment assessment
            Why the matter was considered to be one of the most significant matters in the audit
            The recoverable amount depends on significant assumptions.
            Audit response
            We tested cash-flow forecasts and the discount rate.
            """,
            "Goodwill impairment assessment",
            "2. Goodwill",
            id="english",
        ),
    ],
)
def test_parser_joins_multiword_wrapped_numbered_next_matter(
    body,
    expected_title,
    excluded_response_fragment,
):
    from kreports.processor.kam_parser import extract_kam_items

    items = extract_kam_items(body)

    assert len(items) == 2
    assert items[1].title == expected_title
    assert excluded_response_fragment not in items[0].audit_response_text


@pytest.mark.parametrize(
    ("body", "expected_title", "excluded_response_fragment", "first_response"),
    [
        pytest.param(
            """
            Key Audit Matters
            I. Revenue recognition
            Why the matter was determined to be a key audit matter
            Contract cut-off requires significant judgment.
            How the matter was addressed in the audit
            1. Inspect contract samples
            II. Goodwill
            impairment assessment
            Why the matter was considered to be one of the most significant matters in the audit
            The recoverable amount depends on significant assumptions.
            Audit response
            We tested cash-flow forecasts and the discount rate.
            """,
            "Goodwill impairment assessment",
            "II. Goodwill",
            "1. Inspect contract samples",
            id="roman",
        ),
        pytest.param(
            """
            핵심감사사항
            가. 수익인식
            핵심감사사항으로 선정한 이유
            기간귀속 판단에 유의적인 위험이 있습니다.
            감사인이 수행한 주요 절차
            1. 계약 표본 검사
            나. 영업권
            손상 평가
            핵심감사사항으로 결정한 이유
            회수가능액 추정에 유의적인 판단이 포함됩니다.
            감사에서 다루어진 방법
            현금흐름과 할인율을 검사했습니다.
            """,
            "영업권 손상 평가",
            "나. 영업권",
            "1. 계약 표본 검사",
            id="korean",
        ),
        pytest.param(
            """
            핵심감사사항
            1. 수익인식
            핵심감사사항으로 선정한 이유
            기간귀속 판단에 유의적인 위험이 있습니다.
            감사인이 수행한 주요 절차
            1. 계약 표본 검사
            3. 영업권
            손상 평가
            핵심감사사항으로 결정한 이유
            회수가능액 추정에 유의적인 판단이 포함됩니다.
            감사에서 다루어진 방법
            현금흐름과 할인율을 검사했습니다.
            """,
            "영업권 손상 평가",
            "3. 영업권",
            "1. 계약 표본 검사",
            id="nonconsecutive-arabic",
        ),
        pytest.param(
            """
            핵심감사사항
            1. 수익인식
            핵심감사사항으로 선정한 이유
            기간귀속 판단에 유의적인 위험이 있습니다.
            감사인이 수행한 주요 절차
            1. 계약 표본 검사
            2. 영업권 및
            현금창출단위의
            손상 평가
            핵심감사사항으로 결정한 이유
            회수가능액 추정에 유의적인 판단이 포함됩니다.
            감사에서 다루어진 방법
            현금흐름과 할인율을 검사했습니다.
            """,
            "영업권 및 현금창출단위의 손상 평가",
            "2. 영업권 및",
            "1. 계약 표본 검사",
            id="three-line-title",
        ),
    ],
)
def test_parser_uses_reason_anchor_for_wrapped_marker_families(
    body,
    expected_title,
    excluded_response_fragment,
    first_response,
):
    from kreports.processor.kam_parser import extract_kam_items

    items = extract_kam_items(body)

    assert len(items) == 2
    assert items[0].audit_response_text == first_response
    assert excluded_response_fragment not in items[0].audit_response_text
    assert items[1].title == expected_title


def test_parser_does_not_promote_numbered_procedures_before_unnumbered_matter():
    from kreports.processor.kam_parser import extract_kam_items

    body = """
    Key Audit Matters
    I. Revenue recognition
    Why the matter was determined to be a key audit matter
    Contract cut-off requires significant judgment.
    How the matter was addressed in the audit
    1. Inspect contract samples.
    2. Recalculate contract cut-off.
    3. Confirm management assumptions.
    Goodwill impairment assessment
    Why the matter was considered to be one of the most significant matters in the audit
    The recoverable amount depends on significant assumptions.
    Audit response
    We tested cash-flow forecasts and the discount rate.
    """

    items = extract_kam_items(body)

    assert len(items) == 2
    assert items[0].title == "Revenue recognition"
    assert "2. Recalculate contract cut-off." in items[0].audit_response_text
    assert "3. Confirm management assumptions." in items[0].audit_response_text
    assert items[1].title == "Goodwill impairment assessment"


def test_parser_does_not_create_matter_without_reason_anchor():
    from kreports.processor.kam_parser import extract_kam_items

    body = """
    핵심감사사항
    1. 수익인식
    핵심감사사항으로 선정한 이유
    기간귀속 판단에 중요한 왜곡표시위험이 있습니다.
    감사에서 다루어진 방법
    1. 계약 표본 검사
    2. 재고자산 평가
    감사에서 다루어진 방법
    예상판매가격을 검사했습니다.
    """

    items = extract_kam_items(body)

    assert len(items) == 1
    assert items[0].title == "수익인식"
    assert "2. 재고자산 평가" in items[0].audit_response_text


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


def test_parser_collapses_duplicate_reason_heading_before_response():
    from kreports.processor.kam_parser import extract_kam_items

    body = """
    핵심감사사항
    1. 수익인식
    핵심감사사항으로 선정한 이유
    기간귀속 판단 위험
    핵심감사사항으로 선정한 이유
    복합계약 판단 위험
    감사에서 다루어진 방법
    계약 표본을 검사했습니다.
    """

    items = extract_kam_items(body)

    assert len(items) == 1
    assert items[0].title == "수익인식"
    assert items[0].reason_text == "기간귀속 판단 위험\n복합계약 판단 위험"
    assert "핵심감사사항으로 선정한 이유" not in items[0].reason_text
    assert items[0].audit_response_text == "계약 표본을 검사했습니다."


@pytest.mark.parametrize(
    "first_response",
    [
        pytest.param("계약 표본 검사", id="unnumbered-response"),
        pytest.param("1. 계약 표본 검사", id="numbered-response"),
    ],
)
def test_parser_merges_repeated_reason_response_pair_across_page(first_response):
    from kreports.processor.kam_parser import extract_kam_items

    body = f"""
    핵심감사사항
    수익인식
    핵심감사사항으로 선정한 이유
    기간귀속 판단 위험
    감사에서 다루어진 방법
    {first_response}
    핵심감사사항으로 선정한 이유
    복합계약 판단 위험
    감사에서 다루어진 방법
    기간귀속 재수행
    """

    items = extract_kam_items(body)

    assert len(items) == 1
    assert items[0].title == "수익인식"
    assert items[0].reason_text == "기간귀속 판단 위험\n복합계약 판단 위험"
    assert items[0].audit_response_text == f"{first_response}\n기간귀속 재수행"
    assert "핵심감사사항으로 선정한 이유" not in items[0].reason_text
    assert "감사에서 다루어진 방법" not in items[0].audit_response_text


def test_parser_reports_ambiguous_unnumbered_matter_with_same_headings():
    from kreports.processor.kam_parser import extract_kam_items, parse_kam_items

    body = """
    핵심감사사항
    수익인식
    핵심감사사항으로 선정한 이유
    기간귀속 판단 위험
    감사에서 다루어진 방법
    계약 표본 검사
    재고자산 평가
    핵심감사사항으로 선정한 이유
    순실현가능가치 추정 위험
    감사에서 다루어진 방법
    예상판매가격 검사
    """

    outcome = parse_kam_items(body)

    assert outcome.status == "ambiguous"
    assert outcome.items == []
    assert "ambiguous_boundary" in outcome.limitations
    assert extract_kam_items(body) == []


@pytest.mark.parametrize(
    ("first_title", "procedure"),
    [
        pytest.param("1. 수익인식", "2. 계약 검사", id="arabic"),
        pytest.param("I. Revenue recognition", "II. Inspect contracts", id="roman"),
        pytest.param("가. 수익인식", "나. 계약 검사", id="korean"),
    ],
)
def test_parser_keeps_noninitial_procedure_before_repeated_page_pair(
    first_title,
    procedure,
):
    from kreports.processor.kam_parser import extract_kam_items

    body = f"""
    핵심감사사항
    {first_title}
    핵심감사사항으로 선정한 이유
    기간귀속 판단 위험
    감사에서 다루어진 방법
    {procedure}
    핵심감사사항으로 선정한 이유
    복합계약 판단 위험
    감사에서 다루어진 방법
    기간귀속 재수행
    """

    items = extract_kam_items(body)

    assert len(items) == 1
    assert items[0].reason_text == "기간귀속 판단 위험\n복합계약 판단 위험"
    assert items[0].audit_response_text == f"{procedure}\n기간귀속 재수행"


def test_parser_merges_three_pages_with_numbered_procedures():
    from kreports.processor.kam_parser import extract_kam_items

    body = """
    핵심감사사항
    1. 수익인식
    핵심감사사항으로 선정한 이유
    위험 본문 1
    감사에서 다루어진 방법
    1. 계약 검사
    핵심감사사항으로 선정한 이유
    위험 본문 2
    감사에서 다루어진 방법
    2. 기간귀속 재수행
    핵심감사사항으로 선정한 이유
    위험 본문 3
    감사에서 다루어진 방법
    3. 전표 검사
    """

    items = extract_kam_items(body)

    assert len(items) == 1
    assert items[0].title == "수익인식"
    assert items[0].reason_text == "위험 본문 1\n위험 본문 2\n위험 본문 3"
    assert items[0].audit_response_text == (
        "1. 계약 검사\n2. 기간귀속 재수행\n3. 전표 검사"
    )


def test_parser_merges_semantically_equivalent_heading_variants_across_page():
    from kreports.processor.kam_parser import extract_kam_items

    body = """
    핵심감사사항
    수익인식
    핵심감사사항으로 선정한 이유
    기간귀속 판단 위험
    감사에서 다루어진 방법
    계약 표본 검사
    핵심감사사항으로 결정한 이유
    복합계약 판단 위험
    감사인이 수행한 주요 절차
    기간귀속 재수행
    """

    items = extract_kam_items(body)

    assert len(items) == 1
    assert items[0].title == "수익인식"
    assert items[0].reason_text == "기간귀속 판단 위험\n복합계약 판단 위험"
    assert items[0].audit_response_text == "계약 표본 검사\n기간귀속 재수행"


def test_parser_removes_repeated_response_heading_separator():
    from kreports.processor.kam_parser import extract_kam_items

    body = """
    핵심감사사항
    수익인식
    핵심감사사항으로 선정한 이유
    기간귀속 판단 위험
    감사에서 다루어진 방법
    계약 표본 검사
    감사에서 다루어진 방법
    기간귀속 재수행
    """

    items = extract_kam_items(body)

    assert len(items) == 1
    assert items[0].audit_response_text == "계약 표본 검사\n기간귀속 재수행"
    assert "감사에서 다루어진 방법" not in items[0].audit_response_text


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


def test_rebuild_normalizes_xml_raw_before_parsing_kam_detail(temp_engine):
    from kreports.collector.report_document_collector import rebuild_kam_items
    from kreports.db.engine import get_session

    raw_xml = """
    <DOCUMENT><TITLE>핵심감사사항</TITLE>
    <P>핵심감사사항은 재무제표감사에서 가장 유의적인 사항입니다.</P>
    <TITLE>수익인식</TITLE><P>핵심감사사항으로 선정한 이유</P>
    <P>기간귀속 판단에는 중요한 위험이 있습니다.</P>
    <P>감사에서 다루어진 방법</P><P>계약서를 검사하고 표본을 대사하였습니다.</P>
    <TITLE>재무제표감사에 대한 감사인의 책임</TITLE><P>감사인의 책임입니다.</P></DOCUMENT>
    """
    with get_session() as session:
        session.add_all([
            Company(corp_code="00164781", stock_code="035422", corp_name="XML원문회사", market="KOSPI"),
            SourceDocument(rcept_no="20250318000004", dcm_no="400", corp_code="00164781", bsns_year=2024, source_type="audit_report", report_nm="감사보고서", content_type="xml", raw_content=raw_xml, doc_hash="4" * 40, storage_status="inline"),
        ])

    result = rebuild_kam_items(year=2024, dry_run=True)

    receipt = result["receipts"][0]
    assert receipt["quality_status"] == "full_body"
    assert receipt["source_basis"] == "source_documents.raw_body"


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


def test_rebuild_recovers_same_line_kam_from_persisted_full_text_before_raw_read(
    temp_engine,
    monkeypatch,
):
    """A missing KAM section must not force an external read of complete cached text."""
    from kreports.collector.report_document_collector import rebuild_kam_items
    from kreports.db.engine import get_session
    from kreports.storage.raw_documents import RawDocumentStore

    raw_reads: list[str] = []

    def record_raw_read(_self, storage_uri, **_kwargs):
        raw_reads.append(storage_uri)
        return ""

    full_text = """
    강조사항
    회사는 불확실성을 충분히 반영하지 않았습니다. 핵심감사사항 핵심감사사항은 우리의 전문가적 판단에 따라 당기 재무제표감사에서 가장 유의적인 사항입니다.
    수익인식
    핵심감사사항으로 결정한 이유
    계약 조건과 기간귀속 판단에는 중요한 왜곡표시위험이 존재합니다.
    핵심감사사항에 대응하기 위한 우리의 감사절차는 다음을 포함하고 있습니다.
    ㆍ계약서와 세금계산서 대사를 수행하였습니다.
    ㆍ보고기간 전후 매출의 기간귀속 테스트를 수행하였습니다.
    재무제표감사에 대한 감사인의 책임
    감사인은 감사기준에 따라 감사를 수행합니다.
    """
    monkeypatch.setattr(RawDocumentStore, "read", record_raw_read)
    with get_session() as session:
        session.add(
            Company(
                corp_code="00164782",
                stock_code="035423",
                corp_name="저장본문회사",
                market="KOSPI",
            )
        )
        session.add(
            SourceDocument(
                rcept_no="20250318000005",
                dcm_no="500",
                corp_code="00164782",
                bsns_year=2024,
                source_type="audit_report",
                report_nm="감사보고서",
                content_type="xml",
                raw_content="",
                doc_hash="5" * 40,
                storage_uri="raw://external/20250318000005.xml.gz",
                storage_status="externalized",
            )
        )
        session.add(
            ReportSection(
                rcept_no="20250318000005",
                dcm_no="500",
                corp_code="00164782",
                bsns_year=2024,
                source_type="audit_report",
                section_key="full_text",
                section_title="감사보고서 본문",
                body_text=full_text,
                body_hash="f" * 40,
                body_length=len(full_text),
                ordinal=0,
            )
        )

    result = rebuild_kam_items(year=2024, dry_run=True)

    receipt = result["receipts"][0]
    assert raw_reads == []
    assert receipt["quality_status"] == "full_body"
    assert receipt["source_basis"] == "report_sections.full_text"
    assert receipt["item_count"] == 1
    assert receipt["has_audit_response"] == [True]


def test_rebuild_recovers_hash_verified_gcs_raw(
    temp_engine,
    monkeypatch,
):
    from kreports.collector.report_document_collector import rebuild_kam_items
    from kreports.db.engine import get_session
    from kreports.storage.raw_documents import RawDocumentStore, sha1_text

    raw_body = FIXTURE.read_text(encoding="utf-8")

    def verified_gcs_read(_self, storage_uri, *, expected_hash=None):
        assert storage_uri == "gs://raw-bucket/audit/20250318000003.xml.gz"
        assert expected_hash == sha1_text(raw_body)
        return raw_body

    monkeypatch.setattr(RawDocumentStore, "read", verified_gcs_read)
    with get_session() as session:
        session.add_all(
            [
                Company(
                    corp_code="00164780",
                    stock_code="035421",
                    corp_name="외부원문회사",
                    market="KOSPI",
                ),
                SourceDocument(
                    rcept_no="20250318000003",
                    dcm_no="300",
                    corp_code="00164780",
                    bsns_year=2024,
                    source_type="audit_report",
                    report_nm="감사보고서",
                    content_type="xml",
                    raw_content="",
                    doc_hash=sha1_text(raw_body),
                    storage_uri="gs://raw-bucket/audit/20250318000003.xml.gz",
                    storage_status="externalized",
                ),
            ]
        )

    result = rebuild_kam_items(year=2024, dry_run=True)

    receipt = result["receipts"][0]
    assert receipt["quality_status"] == "full_body"
    assert receipt["source_basis"] == "source_documents.raw_body"
    assert receipt["item_count"] == 2
    assert receipt["limitations"] == []


def test_rebuild_falls_back_from_ambiguous_raw_to_clear_normalized_evidence(
    temp_engine,
):
    from kreports.collector.report_document_collector import rebuild_kam_items
    from kreports.db.engine import get_session

    ambiguous = """
    핵심감사사항
    1. 수익인식
    핵심감사사항으로 선정한 이유
    기간귀속 판단 위험
    감사에서 다루어진 방법
    2. 재고자산 평가
    핵심감사사항으로 선정한 이유
    페이지 반복 위험
    감사에서 다루어진 방법
    추가 절차
    """
    with get_session() as session:
        session.add_all(
            [
                Company(
                    corp_code="00000091",
                    stock_code="000091",
                    corp_name="모호성폴백회사",
                    market="KOSPI",
                ),
                SourceDocument(
                    rcept_no="20250318000091",
                    dcm_no="910",
                    corp_code="00000091",
                    bsns_year=2024,
                    source_type="audit_report",
                    report_nm="감사보고서",
                    content_type="xml",
                    raw_content=ambiguous,
                    doc_hash="9" * 40,
                    storage_status="inline",
                ),
                EvidenceDocument(
                    corp_code="00000091",
                    bsns_year=2024,
                    source_type="audit_report",
                    rcept_no="20250318000091",
                    dcm_no="910",
                    evidence_scope="auditor_view",
                    title="명확한 정규화 증거",
                    normalized_text=FIXTURE.read_text(encoding="utf-8"),
                    source_count=1,
                ),
            ]
        )

    result = rebuild_kam_items(year=2024, dry_run=True)

    receipt = result["receipts"][0]
    assert receipt["quality_status"] == "full_body"
    assert receipt["source_basis"] == "evidence_documents.normalized_text"
    assert (
        "source_documents.raw_body:ambiguous_boundary"
        in receipt["limitations"]
    )
    with get_session() as session:
        assert session.query(KamItem).count() == 0


def test_rebuild_reports_all_ambiguous_sources_as_error_without_writes(
    temp_engine,
):
    from kreports.collector.report_document_collector import rebuild_kam_items
    from kreports.db.engine import get_session

    ambiguous = """
    핵심감사사항
    1. 수익인식
    핵심감사사항으로 선정한 이유
    기간귀속 판단 위험
    감사에서 다루어진 방법
    II. Goodwill impairment assessment
    핵심감사사항으로 선정한 이유
    페이지 반복 위험
    감사에서 다루어진 방법
    추가 절차
    """
    with get_session() as session:
        session.add_all(
            [
                Company(
                    corp_code="00000092",
                    stock_code="000092",
                    corp_name="전부모호회사",
                    market="KOSPI",
                ),
                SourceDocument(
                    rcept_no="20250318000092",
                    corp_code="00000092",
                    bsns_year=2024,
                    source_type="audit_report",
                    report_nm="감사보고서",
                    content_type="xml",
                    raw_content=ambiguous,
                    doc_hash="8" * 40,
                    storage_status="inline",
                ),
            ]
        )

    result = rebuild_kam_items(year=2024, dry_run=True)

    receipt = result["receipts"][0]
    assert receipt["quality_status"] == "error"
    assert receipt["source_basis"] == "none"
    assert receipt["item_count"] == 0
    assert (
        "source_documents.raw_body:ambiguous_boundary"
        in receipt["limitations"]
    )
    assert result["rows_written"] == 0
    with get_session() as session:
        assert session.query(KamItem).count() == 0


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
    assert (
        "evidence_documents.normalized_text:parser_limitation:"
        "incomplete_kam_structure"
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


def test_rebuild_recovers_exact_structured_kam_with_collapsed_reason_boundary(
    temp_engine,
):
    """A complete cached KAM must not stay blocked only because lines collapsed."""
    from kreports.collector.report_document_collector import rebuild_kam_items
    from kreports.db.engine import get_session

    collapsed = (
        "핵심감사사항\n"
        "광고대행 수익인식의 적정성\n"
        "회사는 광고주 및 매체사와의 정산 구조가 복잡하고 수익 금액이 "
        "유의적이므로 광고대행 수익인식을 핵심감사사항으로 "
        "결정하였습니다.\n"
        "회계처리과정에서 발생가능한 오류로 인해 광고대행수익에 대한 "
        "유의적인 왜곡표시위험이 있는 것으로 판단하였습니다.\n"
        "핵심감사사항이 감사에서 다루어진 방법\n"
        "우리가 수행한 주요 감사절차는 다음과 같습니다.\n"
        "- 광고대행 수익인식의 기간귀속 검토\n"
        "- 추출한 표본에 대한 근거문서 대사\n"
        "## audit_procedure/revenue/cutoff\n"
        "광고대행 수익인식의 기간귀속 검토"
    )
    with get_session() as session:
        session.add_all(
            [
                Company(
                    corp_code="00000077",
                    stock_code="000077",
                    corp_name="구조화본문복구회사",
                    market="KOSPI",
                ),
                ReportSection(
                    rcept_no="20250318000077",
                    corp_code="00000077",
                    bsns_year=2024,
                    source_type="audit_report",
                    section_key="kam",
                    section_title="핵심감사사항",
                    body_text=collapsed,
                    body_hash="e" * 40,
                    body_length=len(collapsed),
                    ordinal=0,
                ),
            ]
        )

    result = rebuild_kam_items(year=2024, dry_run=True)

    receipt = result["receipts"][0]
    assert receipt["quality_status"] == "full_body"
    assert receipt["source_basis"] == "report_sections.structured_body"
    assert receipt["titles"] == ["광고대행 수익인식의 적정성"]
    assert receipt["has_reason"] == [True]
    assert receipt["has_audit_response"] == [True]


def test_rebuild_recovers_from_exact_receipt_full_text_after_kam_parse_error(
    temp_engine,
):
    from kreports.collector.report_document_collector import rebuild_kam_items
    from kreports.db.engine import get_session

    malformed_summary = (
        "핵심감사사항이 감사에서 다루어진 방법\n"
        "우리는 가치평가 모델을 검토했습니다."
    )
    collapsed_full_text = (
        "감사의견근거 충분하고 적합한 감사증거를 입수하였습니다. "
        "핵심감사사항 핵심감사사항은 당기 재무제표감사에서 가장 유의적인 "
        "사항들입니다. 우리는 이런 사항에 대하여 별도의 의견을 제공하지는 "
        "않습니다. (1) 영업권 손상평가 핵심감사사항으로 결정된 이유 "
        "회수가능액 추정에는 유의적인 판단이 포함됩니다. "
        "핵심감사사항이 감사에서 다루어진 방법 가치평가 모델과 할인율을 "
        "검토했습니다. 재무제표감사에 대한 감사인의 책임 우리의 목적은 "
        "합리적인 확신을 얻는 것입니다."
    )
    with get_session() as session:
        session.add(
            Company(
                corp_code="00000074",
                stock_code="000074",
                corp_name="전체본문복구회사",
                market="KOSPI",
            )
        )
        session.add_all(
            [
                ReportSection(
                    rcept_no="20250318000074",
                    corp_code="00000074",
                    bsns_year=2024,
                    source_type="audit_report",
                    section_key="kam",
                    section_title="불완전 KAM",
                    body_text=malformed_summary,
                    body_hash="a" * 40,
                    body_length=len(malformed_summary),
                    ordinal=0,
                ),
                ReportSection(
                    rcept_no="20250318000074",
                    corp_code="00000074",
                    bsns_year=2024,
                    source_type="audit_report",
                    section_key="full_text",
                    section_title="감사보고서 본문",
                    body_text=collapsed_full_text,
                    body_hash="b" * 40,
                    body_length=len(collapsed_full_text),
                    ordinal=1,
                ),
            ]
        )

    result = rebuild_kam_items(year=2024, dry_run=True)

    receipt = result["receipts"][0]
    assert receipt["quality_status"] == "full_body"
    assert receipt["source_basis"] == "report_sections.full_text"
    assert receipt["titles"] == ["영업권 손상평가"]
    assert (
        "report_sections.structured_body:parse_error"
        in receipt["limitations"]
    )


def test_rebuild_does_not_promote_full_text_without_kam_to_summary(
    temp_engine,
):
    from kreports.collector.report_document_collector import rebuild_kam_items
    from kreports.db.engine import get_session

    full_text = (
        "감사의견 우리는 재무제표를 감사하였습니다. "
        "재무제표감사에 대한 감사인의 책임 우리의 목적은 합리적인 확신을 "
        "얻는 것입니다."
    )
    with get_session() as session:
        session.add_all(
            [
                Company(
                    corp_code="00000075",
                    stock_code="000075",
                    corp_name="KAM부재회사",
                    market="KOSPI",
                ),
                ReportSection(
                    rcept_no="20250318000075",
                    corp_code="00000075",
                    bsns_year=2024,
                    source_type="audit_report",
                    section_key="full_text",
                    section_title="감사보고서 본문",
                    body_text=full_text,
                    body_hash="c" * 40,
                    body_length=len(full_text),
                    ordinal=0,
                ),
            ]
        )

    result = rebuild_kam_items(year=2024, dry_run=True)

    receipt = result["receipts"][0]
    assert receipt["quality_status"] == "missing"
    assert receipt["item_count"] == 0
    assert receipt["source_basis"] == "none"


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
