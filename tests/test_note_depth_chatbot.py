from __future__ import annotations

from sqlalchemy.orm import Session

from kreports.db.models import AccountingNoteChapter, Company


def _seed_search_note(temp_engine) -> None:
    body = (
        "회사는 Alpha SPC의 3,000억원 프로젝트금융 대출약정과 관련하여 "
        "자금보충약정을 제공하고 있습니다. 상환재원이 부족한 경우 부족액을 "
        "대여 또는 출자 방식으로 보충하며 약정기간은 2032년까지입니다. "
        "당기말 현재 실행된 금액은 없고 관련 지급보증도 제공하고 있습니다."
    )
    with Session(temp_engine) as session:
        session.add(
            Company(
                corp_code="00000001",
                stock_code="000001",
                corp_name="Alpha",
                market="KOSPI",
                induty_code="35110",
            )
        )
        session.add(
            AccountingNoteChapter(
                corp_code="00000001",
                bsns_year=2024,
                fs_div="CFS",
                rcept_no="20250318000001",
                source_type="business_report",
                note_no="31",
                note_title="자금보충약정",
                section_type="other_note",
                body=body,
                body_length=len(body),
            )
        )
        session.commit()


def test_search_answer_leads_with_actual_expression_and_source_text(
    temp_engine,
    monkeypatch,
):
    from kreports.analysis import note_evidence
    from kreports.mcp import contracts
    from kreports.mcp.catalog_extensions import (
        EnhancedSearchDatasetInput,
        install_catalog_extensions,
    )
    from kreports.mcp.handlers.search import (
        handle_search_dataset,
    )

    def grading_must_not_run(*_args, **_kwargs):
        raise AssertionError("default source-first path must not grade notes")

    monkeypatch.setattr(
        note_evidence,
        "assess_disclosure_depth",
        grading_must_not_run,
    )
    _seed_search_note(temp_engine)
    install_catalog_extensions()
    raw = handle_search_dataset(
        EnhancedSearchDatasetInput(
            dataset="accounting_note_chapters",
            keyword="자금보충약정",
            year=2024,
            fs_div="CFS",
            limit=5,
            search_mode="exact",
        )
    )
    record = raw["companies"][0]["records"][0]
    assert raw["note_evidence"]["projection"] == "source_first"
    assert (
        raw["note_evidence"]["optional_facet_assessment_performed"]
        is False
    )
    assert record["note_ref"].startswith("n1-")
    assert record["text_completeness"] == "complete"
    assert "disclosure_level" not in record
    assert "observed_disclosure_items" not in record

    enriched = contracts.enrich_answer_response(
        "search_dataset",
        raw,
    )
    answer = enriched["answer"]

    assert "Alpha" in answer
    assert "실제 사용 표현" in answer
    assert "실제 공시 문구" in answer
    assert "원문 확인 범위" in answer
    assert "자금보충약정" in answer
    assert "Alpha SPC의 3,000억원 프로젝트금융 대출약정" in answer
    assert "회사의 표현을 표준 문장으로 다시 작성하지 않습니다" in answer
    assert "전체 주석에서 발췌" in answer
    assert (
        "[공시 보기](https://dart.fss.or.kr/"
        "dsaf001/main.do?rcpNo=20250318000001)"
        in answer
    )

    for prohibited in (
        "공시 수준",
        "구체적",
        "보통",
        "간략",
        "한도·대상 금액",
        "의무 발생 조건",
        "note_ref",
        "kreports://note/",
        "disclosure_level",
        "note_evidence",
        "assessment_scope",
        "summary_only",
    ):
        assert prohibited not in answer

    layout = enriched["_meta"]["presentation_layout"]
    actions = layout["noteResources"]
    assert len(actions) == 1
    action = actions[0]
    assert action["company"] == "Alpha"
    assert action["relatedParagraph"]["resourceUri"].endswith(
        "/paragraph"
    )
    assert action["fullNote"]["resourceUri"].endswith(
        "/page/1"
    )
    assert action["filing"]["url"].endswith(
        "20250318000001"
    )
    assert action["noteRef"].startswith("n1-")


def test_source_first_adapter_keeps_resources_out_of_visible_text():
    from kreports.mcp.chatbot_company_pagination import (
        render_first_page_markdown,
    )
    from kreports.mcp.chatbot_contracts import (
        ChatbotColumnV1,
        ChatbotTableV1,
        ChatbotViewV1,
    )
    from kreports.mcp.chatbot_note_depth import (
        note_resource_actions,
        polish_note_depth_view,
    )

    view = ChatbotViewV1(
        tool_name="compare_peer_accounting_notes",
        title="기준회사 동종기업 주석 비교",
        subject="기준회사",
        status="usable",
        summary=(
            "기준회사와 비교회사 1곳의 리스 주석에서 문구 차이가 "
            "확인됐습니다."
        ),
        tables=[
            ChatbotTableV1(
                id="note_comparison_page_1",
                title="회사별 주석 비교 · 1~1",
                columns=[
                    ChatbotColumnV1(
                        key="company",
                        label="회사",
                    ),
                    ChatbotColumnV1(
                        key="different_topics",
                        label="문구가 다른 주제",
                    ),
                    ChatbotColumnV1(
                        key="available_topics",
                        label="확인된 범위",
                    ),
                    ChatbotColumnV1(
                        key="basis",
                        label="재무제표 기준",
                    ),
                    ChatbotColumnV1(
                        key="source",
                        label="원 공시",
                    ),
                ],
                rows=[{
                    "company": "비교회사",
                    "different_topics": "리스",
                    "available_topics": "1개 주제 확인",
                    "basis": "요청 기준과 일치",
                    "source": (
                        "[공시 보기](https://dart.fss.or.kr/"
                        "dsaf001/main.do?rcpNo=20250318000002)"
                    ),
                }],
            )
        ],
    )
    result = {
        "subject": {
            "corp_code": "00000001",
            "corp_name": "기준회사",
        },
        "topics": [{
            "topic": "leases",
            "rows": [{
                "company": {
                    "corp_code": "00000002",
                    "corp_name": "비교회사",
                },
                "note_title": "리스",
                "related_paragraph": (
                    "회사는 계약기간과 연장선택권을 고려하여 리스기간을 "
                    "결정하고 있습니다."
                ),
                "text_completeness": "complete",
                "note_ref": "n1-2-0123456789abcdef0123",
                "paragraph_resource_uri": (
                    "kreports://note/"
                    "n1-2-0123456789abcdef0123/paragraph"
                ),
                "full_note_resource_uri": (
                    "kreports://note/"
                    "n1-2-0123456789abcdef0123/page/1"
                ),
                "rcept_no": "20250318000002",
                "source_url": (
                    "https://dart.fss.or.kr/dsaf001/"
                    "main.do?rcpNo=20250318000002"
                ),
            }],
        }],
    }

    polished = polish_note_depth_view(
        "compare_peer_accounting_notes",
        view,
        result,
    )
    answer = render_first_page_markdown(
        polished,
        result,
    )
    actions = note_resource_actions(
        "compare_peer_accounting_notes",
        result,
    )

    assert "실제 공시 문구" in answer
    assert "회사는 계약기간과 연장선택권을 고려하여" in answer
    assert "전체 주석에서 발췌" in answer
    assert "기준회사와 표현 차이" in answer
    assert "공시 수준" not in answer
    assert "보통" not in answer
    assert "리스기간 판단" not in answer
    assert "kreports://note/" not in answer
    assert "note_ref" not in answer
    assert actions[0]["fullNote"]["label"] == "주석 전체"
    assert actions[0]["relatedParagraph"]["label"] == "관련 문단"


def test_partial_note_text_is_qualified_without_claiming_omission():
    from kreports.mcp.chatbot_company_pagination import (
        render_first_page_markdown,
    )
    from kreports.mcp.chatbot_contracts import (
        ChatbotColumnV1,
        ChatbotTableV1,
        ChatbotViewV1,
    )
    from kreports.mcp.chatbot_note_depth import polish_note_depth_view

    view = ChatbotViewV1(
        tool_name="search_dataset",
        title="관련 공시회사",
        subject="자금보충약정",
        status="limited",
        summary="관련 표현이 확인된 회사를 보여드립니다.",
        tables=[
            ChatbotTableV1(
                id="note_search_page_1",
                title="관련 회사 1~1",
                columns=[
                    ChatbotColumnV1(key="company", label="회사"),
                    ChatbotColumnV1(key="year", label="연도"),
                    ChatbotColumnV1(key="fs_div", label="재무제표"),
                    ChatbotColumnV1(key="note_title", label="주석"),
                    ChatbotColumnV1(key="matched_term", label="확인된 표현"),
                    ChatbotColumnV1(key="excerpt", label="관련 문구"),
                    ChatbotColumnV1(key="source", label="원 공시"),
                ],
                rows=[{
                    "company": "일부본문회사",
                    "year": 2024,
                    "fs_div": "연결",
                    "note_title": "약정사항",
                    "matched_term": "자금보충약정",
                    "excerpt": "회사는 자금보충약정을 제공하고 있습니다.",
                    "source": "-",
                }],
            )
        ],
    )
    result = {
        "query": {
            "dataset": "accounting_note_chapters",
        },
        "companies": [{
            "corp_name": "일부본문회사",
            "records": [{
                "matched_term": "자금보충약정",
                "related_paragraph": (
                    "회사는 자금보충약정을 제공하고 있습니다."
                ),
                "text_completeness": "partial",
            }],
        }],
    }

    polished = polish_note_depth_view("search_dataset", view, result)
    answer = render_first_page_markdown(polished, result)

    assert "일부 저장 문구에서 발췌 · 전체 주석 확인 필요" in answer
    assert "공시하지 않" not in answer
    assert "누락" not in answer
