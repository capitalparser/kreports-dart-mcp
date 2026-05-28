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
