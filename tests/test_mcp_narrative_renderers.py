import json
from copy import deepcopy

import pytest

from kreports.mcp.renderers import render_answer, render_audit_matter_search


def test_render_audit_matter_search_returns_korean_paragraph():
    payload = {
        "total_companies": 1,
        "total_sections": 1,
        "companies": [{
            "corp_name": "A",
            "matter_counts": {"emphasis": 1},
            "sections": [{"section_key": "emphasis", "body_excerpt": "계속기업 관련 중요한 불확실성"}],
        }],
        "data_quality": {"status": "usable", "source": "audit_matter_items"},
    }

    text = render_audit_matter_search(payload)

    assert "확인됩니다" in text
    assert "데이터 한계" in text


def test_public_kam_surfaces_replace_topic_enum_keys_with_korean_labels():
    from kreports.mcp.contracts import enrich_answer_response
    from kreports.mcp.resources import read_resource

    out = enrich_answer_response("compare_peer_kam_topics", {
        "subject": {"corp_name": "A"},
        "year": 2025,
        "kam_topics": {"revenue_recognition": 1},
        "subject_sections": [{
            "section_key": "kam",
            "bsns_year": 2025,
            "rcept_no": "20260311000013",
            "kam_analysis": {
                "topics": ["revenue_recognition"],
                "has_reason_hint": True,
                "has_procedure_hint": True,
            },
        }],
        "audit_report_sections": {
            "topic_coverage": {"available": 1, "total": 1, "status": "usable"},
            "reason_coverage": {"available": 1, "total": 1, "status": "usable"},
            "procedure_coverage": {"available": 1, "total": 1, "status": "usable"},
            "source_coverage": {"available": 1, "total": 1, "status": "usable"},
        },
        "data_quality": {"status": "usable"},
    })

    public_text = " ".join([
        out["answer"],
        str(out["answer_pack"]["tables"]),
        read_resource(out["answer_pack"]["resource_uri"])["text"],
    ])
    assert "수익인식" in public_text
    assert "revenue_recognition" not in public_text


def test_public_matter_surfaces_replace_category_enum_keys_with_korean_labels():
    from kreports.mcp.contracts import enrich_answer_response
    from kreports.mcp.resources import read_resource

    out = enrich_answer_response("search_audit_report_matters", {
        "query": {"year": 2025},
        "total_companies": 1,
        "total_sections": 1,
        "companies": [{
            "corp_name": "A",
            "matter_counts": {"other_matter": 1},
            "sections": [{
                "section_key": "other_matter",
                "matter_category": "other_matter",
                "acceptance_signal": True,
                "rcept_no": "20260311000014",
            }],
        }],
        "data_quality": {"status": "usable"},
    })

    public_text = " ".join([
        out["answer"],
        str(out["answer_pack"]["tables"]),
        read_resource(out["answer_pack"]["resource_uri"])["text"],
    ])
    assert "기타사항" in public_text
    assert "other_matter" not in public_text


def test_public_kam_lifecycle_maps_topic_and_state_labels_everywhere():
    from kreports.mcp.contracts import enrich_answer_response
    from kreports.mcp.resources import read_resource

    out = enrich_answer_response("get_kam_lifecycle", {
        "subject": {"corp_name": "A"},
        "start_year": 2024,
        "end_year": 2025,
        "events": [{
            "year": 2025,
            "topic": "revenue_recognition",
            "status": "new",
            "title": "수익인식",
        }],
        "data_quality": {"status": "usable"},
    })

    assert out["events"][0]["topic"] == "수익인식"
    assert out["events"][0]["status"] == "신규"
    public_envelope = json.dumps(out, ensure_ascii=False)
    resource_text = read_resource(out["answer_pack"]["resource_uri"])["text"]
    for public_text in (
        public_envelope,
        out["answer"],
        str(out["answer_pack"]["tables"]),
        resource_text,
    ):
        assert "수익인식" in public_text
        assert "신규" in public_text
        assert "revenue_recognition" not in public_text
        assert '"new"' not in public_text


@pytest.mark.parametrize(
    ("topic", "expected_topic", "status", "expected_status"),
    [
        ("ITSystem", "기타 핵심감사사항", "NEWLYRepeated", "상태 미분류"),
        ("KAMLifecycle", "기타 핵심감사사항", "NEWLYRepeated", "상태 미분류"),
        ("REVENUERecognition", "수익인식", "NEWLYRepeated", "상태 미분류"),
        ("materiality", "materiality", "stable", "stable"),
        ("중요성", "중요성", "후속 검토", "후속 검토"),
    ],
)
def test_auditor_public_tokenization_distinguishes_machine_and_reader_labels(
    topic,
    expected_topic,
    status,
    expected_status,
):
    from kreports.mcp.auditor_public import (
        public_kam_lifecycle_label,
        public_kam_topic_label,
    )

    assert public_kam_topic_label(topic) == expected_topic
    assert public_kam_lifecycle_label(status) == expected_status


@pytest.mark.parametrize(
    ("raw_topic", "raw_status", "expected_topic", "expected_status"),
    [
        (" IT_SYSTEM_CONVERSION ", " NEWLY_REPEATED ", "기타 핵심감사사항", "상태 미분류"),
        ("it-system-conversion", "newly-repeated", "기타 핵심감사사항", "상태 미분류"),
        ("itSystemConversion", "newlyRepeated", "기타 핵심감사사항", "상태 미분류"),
        ("it.system.conversion", "newly.repeated", "기타 핵심감사사항", "상태 미분류"),
        ("ITSystem", "NEWLYRepeated", "기타 핵심감사사항", "상태 미분류"),
        ("KAMLifecycle", "NEWLYRepeated", "기타 핵심감사사항", "상태 미분류"),
        ("REVENUERecognition", "NEWLYRepeated", "수익인식", "상태 미분류"),
    ],
)
def test_public_kam_lifecycle_fails_closed_for_unknown_enum_values(
    raw_topic,
    raw_status,
    expected_topic,
    expected_status,
):
    from kreports.mcp.contracts import enrich_answer_response
    from kreports.mcp.resources import read_resource

    payload = {
        "subject": {"corp_name": "A"},
        "start_year": 2024,
        "end_year": 2025,
        "events": [
            {
                "year": 2025,
                "topic": raw_topic,
                "status": raw_status,
                "title": "정보시스템 전환",
            },
            {
                "year": 2024,
                "topic": "Information system conversion",
                "status": "Follow up review",
                "title": "Reader-facing text",
            },
            {
                "year": 2023,
                "topic": "materiality",
                "status": "stable",
                "title": "Single-word reader labels",
            },
        ],
        "data_quality": {"status": "usable"},
    }
    original = deepcopy(payload)

    direct_answer = render_answer("get_kam_lifecycle", payload)
    out = enrich_answer_response("get_kam_lifecycle", payload)
    repeated = enrich_answer_response("get_kam_lifecycle", out)

    assert payload == original
    assert out["events"][0] == {
        "year": 2025,
        "topic": expected_topic,
        "status": expected_status,
        "title": "정보시스템 전환",
    }
    assert out["events"][1]["topic"] == "Information system conversion"
    assert out["events"][1]["status"] == "Follow up review"
    assert out["events"][2]["topic"] == "materiality"
    assert out["events"][2]["status"] == "stable"
    assert repeated["events"] == out["events"]
    resource_text = read_resource(out["answer_pack"]["resource_uri"])["text"]
    for public_text in (
        direct_answer,
        json.dumps(out, ensure_ascii=False),
        out["answer"],
        str(out["answer_pack"]["tables"]),
        resource_text,
    ):
        assert expected_topic in public_text
        assert expected_status in public_text
        assert raw_topic.strip() not in public_text
        assert raw_status.strip() not in public_text


def test_render_new_tools_have_answers():
    cases = [
        ("get_kam_lifecycle", {"events": [{"year": 2024, "topic": "revenue", "status": "new"}], "start_year": 2021, "end_year": 2025, "data_quality": {"status": "usable"}}),
        ("get_accounting_policy_changes", {"changed_items": [{"year": 2024, "note_no": "2", "section_type": "policy", "similarity_to_previous": 0.5}], "start_year": 2021, "end_year": 2025, "data_quality": {"status": "usable"}}),
        ("get_quality_of_earnings_pack", {"verdict": "monitor", "investment_question": "질문", "signals": [{"signal": "low_cash_conversion", "meaning": "현금전환 낮음"}], "limitations": ["한계"]}),
        ("get_dcf_input_candidates", {"candidate_assumptions": {"revenue_growth": {"value": 0.1, "basis": "historical_median"}}, "missing_inputs": ["wacc"], "limitations": ["한계"], "data_quality": {"status": "usable"}}),
        ("search_disclosure_events", {"query": {"start_date": "2025-01-01", "end_date": "2025-12-31"}, "total_events": 1, "events": [{"event_date": "2025-01-01", "corp_name": "A", "event_type": "capital_raise", "event_title": "유상증자"}], "data_quality": {"status": "usable"}}),
    ]
    for tool_name, payload in cases:
        text = render_answer(tool_name, payload)
        assert text
        assert "판정" in text


def test_compare_to_industry_multi_renderer_prints_peer_benchmark_table():
    text = render_answer("compare_to_industry_multi", {
        "subject": {"corp_name": "A"},
        "years": [2025],
        "metrics": ["ROE", "영업이익률"],
        "n_peers": 30,
        "confidence": "high",
        "results": {
            2025: {
                "ROE": {"subject_value": 0.12, "percentile": 70, "p25": 0.05, "p50": 0.1, "p75": 0.15, "n": 30},
                "영업이익률": {"subject_value": 0.2, "percentile": 90, "p25": 0.03, "p50": 0.08, "p75": 0.11, "n": 30},
            }
        },
    })

    assert "Peer 벤치마크" in text
    assert "| 연도 | 지표 | 대상회사 | 백분위 | P25 | P50 | P75 | Peer 수 |" in text
    assert "| 2025 | ROE | 0.12 | 70" in text
    assert "Generic" not in text


def test_subsidiary_renderer_prints_contribution_table_and_diagram():
    text = render_answer("get_subsidiary_auditors", {
        "corp_code": "01817081",
        "parent_rcept_no": "20250317000875",
        "bsns_year": 2024,
        "consolidated_totals": {
            "fs_div": "CFS",
            "assets_amount_m": 731694.7,
            "revenue_amount_m": 332151.7,
            "source": "financial_facts_compact",
        },
        "qsc_criterion": {
            "threshold_pct": 10.0,
            "basis": "asset_share_pct >= 10.0 OR revenue_share_pct >= 10.0",
        },
        "subsidiaries": [{
            "name": "진도산월태양광발전㈜",
            "relation": "종속",
            "ownership_pct": 100.0,
            "asset_amount_m": 521.0,
            "asset_share_pct": 0.1,
            "revenue_amount_m": None,
            "revenue_share_pct": None,
            "qsc_status": "undetermined",
            "is_qsc": None,
            "qsc_basis": [],
            "auditor": {"auditor_nm": "삼일회계법인"},
        }],
        "total": 1,
        "count": 1,
        "truncated": False,
        "data_quality": {
            "status": "usable",
            "coverage_note": "개별 실체 매출은 매칭된 회사 재무정보가 있는 경우만 산출합니다.",
        },
    })

    assert "```mermaid" in text
    assert "QSC 기준은 연결 총자산 또는 연결 총매출 대비 10.0% 이상입니다." in text
    assert "| 회사 | 관계 | 지분율 | 자산(백만원) | 자산비중 | 매출(백만원) | 매출비중 | QSC | 감사인 |" in text
    assert "진도산월태양광발전㈜" in text
    assert "| 진도산월태양광발전㈜ | 종속 | 100.0% | 521 | 0.1% | 미확보 | 미확보 | 미판정 | 삼일회계법인 |" in text
    assert "미확보" in text
    assert "공시 링크: https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250317000875" in text


def test_generic_renderer_prints_confirmed_facts_with_source_lines():
    text = render_answer("get_business_overview", {
        "corp_name": "SK이터닉스",
        "data_quality": {"status": "usable"},
        "confirmed_facts": [{
            "statement": "SK이터닉스는 태양광, 풍력, 연료전지 및 ESS를 주요 사업으로 설명합니다.",
            "source": {
                "corp_name": "SK이터닉스",
                "report_nm": "사업보고서 (2025.12)",
                "section_title": "II. 사업의 내용",
                "rcept_no": "20260316001520",
            },
        }],
        "analysis": [{
            "perspective": "auditor",
            "statement": "EPC와 장기 프로젝트 매출은 진행률과 총공사원가 추정 검토가 필요합니다.",
        }],
        "next_checks": ["감사보고서 KAM 본문과 감사절차를 추가 확인하세요."],
    })

    assert "공시에서 확인되는 내용" in text
    assert "SK이터닉스는 태양광" in text
    assert "출처: SK이터닉스 사업보고서 (2025.12), II. 사업의 내용, 접수번호 20260316001520" in text
    assert "공시 링크: https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260316001520" in text
    assert "감사인 관점 해석" in text
    assert "1번 근거" not in text
    assert "[Fact" not in text


def test_renderer_dedupes_repeated_confirmed_facts_by_parent_receipt():
    text = render_answer("get_audit_report_sections", {
        "subject": {"corp_name": "A"},
        "year": 2025,
        "section_key": "kam",
        "section_count": 2,
        "sections": [],
        "data_quality": {"status": "usable", "source": "evidence_documents"},
        "confirmed_facts": [
            {
                "statement": "2025년 감사보고서 핵심감사사항 본문에서 반복 내용이 확인됩니다.",
                "source": {
                    "corp_name": "A",
                    "report_nm": "감사보고서",
                    "section_title": "핵심감사사항",
                    "rcept_no": "20260311000001_001_xml",
                },
                "excerpt": "반복 내용",
            },
            {
                "statement": "2025년 감사보고서 핵심감사사항 본문에서 반복 내용이 확인됩니다.",
                "source": {
                    "corp_name": "A",
                    "report_nm": "감사보고서",
                    "section_title": "핵심감사사항",
                    "rcept_no": "20260311000001_002_xml",
                },
                "excerpt": "반복 내용",
            },
        ],
    })

    assert text.count("2025년 감사보고서 핵심감사사항 본문") == 1
    assert text.count("공시 링크: https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260311000001") == 1


def test_audit_report_section_renderer_dedupes_repeated_section_lines():
    text = render_answer("get_audit_report_sections", {
        "subject": {"corp_name": "A"},
        "year": 2025,
        "section_key": "kam",
        "section_count": 2,
        "sections": [
            {
                "section_title": "핵심감사사항",
                "body_excerpt": "핵심감사사항은 우리의 전문가적 판단에 따라 당기 감사에서 가장 유의적인 사항입니다.",
            },
            {
                "section_title": "핵심감사사항",
                "body_excerpt": "핵심감사사항은 우리의 전문가적 판단에 따라 당기 감사에서 가장 유의적인 사항입니다.",
            },
        ],
        "data_quality": {"status": "usable", "source": "evidence_documents"},
    })

    assert text.count("핵심감사사항: 핵심감사사항은 우리의 전문가적 판단") == 1


def test_investor_renderers_print_confirmed_facts_with_source_lines():
    fact = {
        "statement": "2024년 연결 재무요약 기준 매출과 영업현금흐름이 이익의 질 점검에 사용되었습니다.",
        "source": {
            "corp_name": "A",
            "report_nm": "사업보고서 (2024.12)",
            "section_title": "재무제표",
            "rcept_no": "20250318001234",
            "source_table": "financial_facts_compact",
        },
    }
    cases = [
        ("get_quality_of_earnings_pack", {
            "verdict": "stable",
            "investment_question": "보고이익이 현금흐름으로 뒷받침되는가?",
            "metrics": {"years": 3, "low_cash_conversion_years": 0},
            "limitations": ["한계"],
            "confirmed_facts": [fact],
            "analysis": [{"perspective": "investor", "statement": "현금전환율은 이익 지속성 검토의 출발점입니다."}],
            "next_checks": ["주석에서 일회성 손익을 확인하세요."],
        }),
        ("get_dcf_input_candidates", {
            "candidate_assumptions": {"revenue_growth": {"value": 0.1, "basis": "historical_median"}},
            "missing_inputs": ["wacc"],
            "limitations": ["한계"],
            "data_quality": {"status": "usable"},
            "confirmed_facts": [fact],
            "analysis": [{"perspective": "investor", "statement": "과거 중앙값은 모델 입력 후보일 뿐입니다."}],
            "next_checks": ["할인율과 터미널 성장률은 별도로 산정하세요."],
        }),
        ("search_disclosure_events", {
            "query": {"start_date": "2025-01-01", "end_date": "2025-12-31"},
            "total_events": 1,
            "events": [{"event_date": "2025-01-01", "corp_name": "A", "event_type": "capital_raise", "event_title": "유상증자"}],
            "data_quality": {"status": "usable"},
            "confirmed_facts": [fact],
            "analysis": [{"perspective": "investor", "statement": "자본조달 공시는 희석 가능성을 확인해야 합니다."}],
            "next_checks": ["접수번호 기준 원문을 확인하세요."],
        }),
    ]

    for tool_name, payload in cases:
        text = render_answer(tool_name, payload)
        assert "공시에서 확인되는 내용" in text
        assert "공시 링크: https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250318001234" in text
        assert "투자자 관점 해석" in text


def test_investor_signals_renderer_uses_user_facing_language():
    text = render_answer("get_investor_signals", {
        "subject": {"corp_name": "A"},
        "has_data": True,
        "quality_snapshot": {"passed_checks": 4, "total_checks": 6, "latest_year": 2024},
        "accounting_risk": {"score": 20, "verdict": "watch"},
        "recent_events": [{
            "disc_date": "2025-01-15",
            "rcept_no": "20250115000001",
            "report_nm": "유상증자결정",
            "flr_nm": "A",
            "category": "capital_raise",
            "label": "유상증자",
            "stance": "dilution_watch",
        }],
        "event_counts": {"capital_raise": 1},
        "takeaways": ["quality_profile_supportive"],
        "confirmed_facts": [{
            "statement": "2024년 연간 재무 스냅샷 기준 ROE=12.5가 확인됩니다.",
            "source": {
                "corp_name": "A",
                "report_nm": "사업보고서 (2024.12)",
                "section_title": "재무제표",
                "rcept_no": "20250318001234",
            },
        }],
        "analysis": [{"perspective": "investor", "statement": "재무 품질과 공시 이벤트를 함께 봐야 합니다."}],
        "next_checks": ["최근 이벤트 원문을 확인하세요."],
    })

    assert "투자자 신호 요약" in text
    assert "`_meta`" not in text
    assert "공시에서 확인되는 내용" in text


def test_auditor_renderers_print_confirmed_facts_with_source_lines():
    fact = {
        "statement": "감사보고서 핵심감사사항 본문에서 수익인식 관련 감사절차가 확인되었습니다.",
        "source": {
            "corp_name": "A",
            "report_nm": "감사보고서",
            "section_title": "핵심감사사항",
            "rcept_no": "20260311000001",
        },
    }
    cases = [
        ("get_audit_report_sections", {
            "subject": {"corp_name": "A"},
            "year": 2025,
            "section_key": "kam",
            "section_count": 1,
            "sections": [{"section_title": "핵심감사사항", "body_excerpt": "수익인식은 핵심감사사항입니다."}],
            "data_quality": {"status": "usable", "source": "report_sections"},
            "confirmed_facts": [fact],
            "analysis": [{"perspective": "auditor", "statement": "KAM은 감사위험 식별과 감사절차 설계의 출발점입니다."}],
            "next_checks": ["감사절차 문단과 회계정책 주석을 대조하세요."],
        }),
        ("search_audit_report_matters", {
            "query": {"year": 2025},
            "total_companies": 1,
            "total_sections": 1,
            "companies": [{"corp_name": "A", "matter_counts": {"emphasis": 1}, "sections": []}],
            "data_quality": {"status": "usable", "source": "audit_matter_items"},
            "confirmed_facts": [fact],
            "analysis": [{"perspective": "auditor", "statement": "강조사항은 감사의견 변형은 아니지만 수임위험 검토 대상입니다."}],
            "next_checks": ["강조사항 원문을 확인하세요."],
        }),
        ("search_audit_procedures", {
            "subject": {"corp_name": "A"},
            "total_procedures": 1,
            "procedure_type_counts": {"substantive_test": 1},
            "companies": [{"corp_name": "A", "records": [{"procedure_type": "substantive_test", "procedure_excerpt": "문서검사 수행"}]}],
            "data_quality": {"status": "usable", "source": "audit_procedure_items"},
            "confirmed_facts": [fact],
            "analysis": [{"perspective": "auditor", "statement": "절차 유형 분포는 peer 감사접근 비교에 사용됩니다."}],
            "next_checks": ["절차가 위험요인과 대응되는지 확인하세요."],
        }),
    ]

    for tool_name, payload in cases:
        text = render_answer(tool_name, payload)
        assert "공시에서 확인되는 내용" in text
        assert "공시 링크: https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260311000001" in text
        assert "감사인 관점 해석" in text


def test_render_answer_adds_the_same_canonical_visual_table_for_plain_clients():
    text = render_answer("get_kam_lifecycle", {
        "subject": {"corp_name": "A"},
        "events": [{"year": 2024, "topic": "수익인식", "status": "new"}],
        "data_quality": {"status": "usable"},
    })

    assert "| 연도 | 주제 | 상태 |" in text
    assert "| 2024 | 수익인식 | 신규 |" in text


def test_render_answer_escapes_markdown_structure_from_visual_cells():
    text = render_answer("get_kam_lifecycle", {
        "subject": {"corp_name": "A"},
        "events": [{
            "year": 2024,
            "topic": "x|y\n# injected",
            "status": "new",
        }],
        "data_quality": {"status": "usable"},
    })

    assert "x\\|y<br/># injected" in text
    assert "\n# injected" not in text
