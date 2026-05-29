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
