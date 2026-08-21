from __future__ import annotations


def _note_search_result() -> dict:
    return {
        "query": {
            "dataset": "accounting_note_chapters",
            "keyword": "자금보충약정",
            "search_mode": "normalized",
        },
        "matched_company_count": 1,
        "matched_record_count": 1,
        "returned_company_count": 1,
        "returned_record_count": 1,
        "companies": [{
            "corp_code": "00000001",
            "corp_name": "Alpha",
            "records": [{
                "year": 2024,
                "fs_div": "CFS",
                "note_title": "약정사항",
                "matched_term": "자금보충약정",
                "match_type": "normalized",
                "body_excerpt": "회사는 자금보충약정을 체결했습니다.",
                "rcept_no": "20250318000001",
            }],
        }],
        "confirmed_facts": [{
            "statement": "Alpha 주석에서 관련 문구가 확인됩니다.",
            "source": {
                "rcept_no": "20250318000001",
                "section_title": "약정사항",
            },
        }],
        "data_quality": {
            "status": "usable",
            "source": "accounting_note_chapters",
            "limitations": [
                "cache_miss_is_not_disclosure_absence"
            ],
        },
        "next_checks": [
            "원 공시 문맥을 확인하세요."
        ],
    }


def test_note_search_chatbot_view_is_summary_first_and_source_linked():
    from kreports.mcp.chatbot_contracts import (
        build_chatbot_view,
        render_chatbot_markdown,
    )

    view = build_chatbot_view(
        "search_dataset",
        _note_search_result(),
    )
    assert view is not None
    markdown = render_chatbot_markdown(view)

    assert markdown.index("한눈에 보기") < markdown.index(
        "공시회사와 일치 근거"
    )
    assert "Alpha" in markdown
    assert "자금보충약정" in markdown
    assert "20250318000001" in markdown
    assert "dart.fss.or.kr" in markdown
    assert view.raw_text_default_collapsed is True


def test_chatbot_visual_pack_uses_existing_strict_contract():
    from kreports.mcp.chatbot_contracts import (
        build_chatbot_view,
        build_chatbot_visualization_pack,
    )
    from kreports.mcp.visual_contracts import (
        VisualizationPackV1,
    )

    result = _note_search_result()
    view = build_chatbot_view(
        "search_dataset",
        result,
    )
    assert view is not None
    pack = build_chatbot_visualization_pack(
        view,
        result,
    )
    validated = VisualizationPackV1.model_validate(pack)

    assert validated.summary.subject == "자금보충약정"
    assert validated.tables[0].id == "note_search_results"
    assert validated.sources[0].rcept_no == "20250318000001"
    assert validated.status == "usable"


def test_enrichment_preserves_raw_facts_and_adds_user_first_answer_pack():
    from kreports.mcp import contracts
    from kreports.mcp.chatbot_integration import (
        install_chatbot_enrichment,
    )

    install_chatbot_enrichment()
    raw = _note_search_result()
    enriched = contracts.enrich_answer_response(
        "search_dataset",
        raw,
    )

    assert enriched["companies"] == raw["companies"]
    assert enriched["answer"].startswith("## ")
    assert enriched["answer_pack"]["tables"]
    assert (
        enriched["_meta"]["presentation_contract"]
        == "kreports.chatbot.user-first.v1"
    )
    layout = enriched["_meta"]["presentation_layout"]
    assert layout["raw_text_default_collapsed"] is True
    assert layout["company_page_size"] == 5
    assert layout["company_pages"]["page_size"] == 5
    assert "normalized" not in enriched["answer"]
    assert "공시 보기" in enriched["answer"]


def test_peer_benchmark_view_exposes_full_denominator_and_cell_quality():
    from kreports.mcp.chatbot_contracts import (
        build_chatbot_view,
        build_chatbot_visualization_pack,
    )
    from kreports.mcp.visual_contracts import (
        VisualizationPackV1,
    )

    result = {
        "subject": {
            "corp_name": "Subject",
        },
        "peer_count": 12,
        "returned_peer_count": 5,
        "resolved_year": 2024,
        "years": [2024],
        "metrics": ["영업이익률"],
        "results": {
            2024: {
                "영업이익률": {
                    "subject_value": 12.0,
                    "unit": "%",
                    "percentile": 62.5,
                    "midrank_percentile": 62.5,
                    "p25": 8.0,
                    "p50": 10.0,
                    "p75": 15.0,
                    "n": 12,
                    "coverage_pct": 100.0,
                    "confidence": "sufficient_n",
                }
            }
        },
        "data_quality": {
            "status": "limited",
            "sufficient_cell_pct": 100.0,
            "limitations": [
                "chatbot_peer_table_is_truncated_but_statistics_use_full_cohort"
            ],
        },
    }

    view = build_chatbot_view(
        "compare_to_industry_multi",
        result,
    )
    assert view is not None
    assert view.metrics[0].value == 12
    assert any(
        "전체" in warning or "full" in warning
        for warning in view.warnings
    )
    pack = build_chatbot_visualization_pack(
        view,
        result,
    )
    validated = VisualizationPackV1.model_validate(pack)
    assert validated.status == "limited"
    assert validated.charts[0].type == "heatmap"
