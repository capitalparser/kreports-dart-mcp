from __future__ import annotations

import pytest


def test_source_separated_answer_contract_keeps_facts_claims_context_and_analysis_distinct():
    from kreports.analysis.context_pack import build_context_pack
    from kreports.mcp.answer_contracts import build_source_separated_answer

    pack = build_context_pack(
        {
            "subject": {"corp_code": "00000001", "corp_name": "Context Corp"},
            "year": 2024,
            "availability": {"business_report": "available"},
            "business_report": [
                {
                    "source_locator": "report_sections:1",
                    "section_title": "사업의 내용",
                    "section_key": "business_overview",
                    "excerpt": "DART 기준 사실",
                    "full_text_hash": "a" * 40,
                    "claim_key": "revenue_growth",
                }
            ],
        },
        company_ir=[
            {
                "source_class": "company_ir",
                "source_id": "ir-1",
                "excerpt": "경영진의 성장 목표",
                "claim_key": "revenue_growth",
            }
        ],
        web_news=[
            {
                "source_class": "web_news",
                "source_id": "news-1",
                "excerpt": "외부 기사 맥락",
            }
        ],
        missing_evidence=[{"evidence_type": "earnings_call", "reason": "not_supplied"}],
    )

    answer = build_source_separated_answer(
        pack,
        analysis=[
            {
                "statement": "성장 목표는 DART 기재와 별도로 검증이 필요합니다.",
                "source_ids": ["report_sections:1", "ir-1"],
            }
        ],
        counterpoints=[
            {
                "statement": "IR 목표는 경영진 자기설명입니다.",
                "source_ids": ["ir-1"],
            }
        ],
    )

    assert answer.schema_version == "source_separated_answer.v1"
    assert [item.source_id for item in answer.confirmed_facts] == ["report_sections:1"]
    assert [item.source_id for item in answer.management_claims] == ["ir-1"]
    assert [item.source_id for item in answer.external_context] == ["news-1"]
    assert answer.analysis[0].source_ids == ["ir-1", "report_sections:1"]
    assert answer.counterpoints[0].source_ids == ["ir-1"]
    assert {item.evidence_type for item in answer.missing_evidence} >= {"earnings_call"}
    assert any(item.claim_key == "revenue_growth" for item in answer.conflicts)
    assert answer.sources[0].source_id == "report_sections:1"


def test_source_separated_answer_rejects_analysis_without_a_known_source():
    from kreports.analysis.context_pack import build_context_pack
    from kreports.mcp.answer_contracts import build_source_separated_answer

    pack = build_context_pack({"subject": {"corp_code": "00000001"}, "year": 2024})

    with pytest.raises(ValueError, match="unknown source_ids"):
        build_source_separated_answer(
            pack,
            analysis=[{"statement": "근거 없는 해석", "source_ids": ["unknown"]}],
        )
