from kreports.mcp.tools import _attach_meta


def test_peer_answer_pack_surfaces_typed_cohort_denominators_conditionally():
    from kreports.mcp.answer_pack import build_answer_pack

    pack = build_answer_pack(
        "compare_to_industry_multi",
        {
            "subject": {"corp_name": "A"},
            "results": {},
            "cohort_metadata": {
                "profile": "investor",
                "requested_year": 2024,
                "fs_div": "CFS",
                "total_candidates": 20,
                "eligible_count": 8,
                "selected_count": 5,
                "exclusion_counts": {"year_unavailable": 4},
            },
        },
    )

    table = next(
        table for table in pack["tables"] if table["id"] == "peer_cohort_metadata"
    )
    assert table["rows"] == [
        {
            "profile": "investor",
            "requested_year": 2024,
            "fs_div": "CFS",
            "total_candidates": 20,
            "eligible_count": 8,
            "selected_count": 5,
            "exclusion_reason": "year_unavailable",
            "exclusion_count": 4,
            "exclusion_scope": "common_eligibility",
        }
    ]


def test_attach_meta_adds_dcf_answer_pack_with_tables_and_charts():
    result = {
        "subject": {"corp_name": "A"},
        "historical_actuals": [
            {"year": 2023, "revenue": 100, "operating_profit": 10, "operating_cf": 8},
            {"year": 2024, "revenue": 120, "operating_profit": 18, "operating_cf": 17},
        ],
        "candidate_assumptions": {
            "revenue_growth": {"value": 0.2, "basis": "historical_median"},
            "operating_margin": {"value": 0.15, "basis": "historical_median"},
        },
        "missing_inputs": ["wacc"],
        "data_quality": {"status": "usable", "source": "financial_facts_compact"},
    }

    out = _attach_meta("get_dcf_input_candidates", result)

    pack = out["answer_pack"]
    assert pack["kind"] == "answer_pack"
    assert pack["summary"]["title"] == "A DCF 입력 후보"
    assert pack["data_quality"]["source"] == "financial_facts_compact"
    assert any(table["id"] == "historical_actuals" for table in pack["tables"])
    assert any(table["id"] == "candidate_assumptions" for table in pack["tables"])
    assert any(chart["id"] == "financial_trend" and chart["type"] == "line" for chart in pack["charts"])
    assert out["answer"]


def test_attach_meta_adds_subsidiary_answer_pack_with_mermaid_and_contribution_table():
    result = {
        "subject": {"corp_name": "A"},
        "bsns_year": 2024,
        "consolidated_totals": {"assets_amount_m": 1000, "revenue_amount_m": 500},
        "qsc_criterion": {"threshold_pct": 10.0},
        "subsidiaries": [
            {
                "name": "B",
                "relation": "종속",
                "ownership_pct": 80.0,
                "asset_amount_m": 120,
                "asset_share_pct": 12.0,
                "revenue_amount_m": 70,
                "revenue_share_pct": 14.0,
                "qsc_status": "qsc",
                "auditor": {"auditor_nm": "삼일회계법인"},
            }
        ],
        "data_quality": {"status": "usable", "source": "local_subsidiary_auditor_matrix"},
    }

    out = _attach_meta("get_subsidiary_auditors", result)

    pack = out["answer_pack"]
    assert pack["summary"]["title"] == "A 연결실체 구조"
    assert any(diagram["type"] == "mermaid" and "B" in diagram["definition"] for diagram in pack["diagrams"])
    table = next(table for table in pack["tables"] if table["id"] == "subsidiary_contribution")
    assert table["rows"][0]["asset_share_pct"] == 12.0
    assert table["rows"][0]["revenue_share_pct"] == 14.0
    assert table["rows"][0]["qsc_status"] == "qsc"


def test_answer_pack_uses_meta_company_when_result_has_no_subject():
    result = {
        "_meta": {"company": {"corp_name": "SK하이닉스", "corp_code": "00164779"}},
        "bsns_year": 2024,
        "subsidiaries": [{"name": "B", "qsc_status": "undetermined"}],
        "data_quality": {"status": "usable", "source": "local_subsidiary_auditor_matrix"},
    }

    out = _attach_meta("get_subsidiary_auditors", result)

    pack = out["answer_pack"]
    assert pack["summary"]["title"] == "SK하이닉스 연결실체 구조"
    assert 'P["SK하이닉스<br/>2024년 연결실체"]' in pack["diagrams"][0]["definition"]
    assert "미판정" in pack["diagrams"][0]["definition"]


def test_attach_meta_adds_disclosure_event_timeline_pack():
    result = {
        "query": {"start_date": "2025-01-01", "end_date": "2025-12-31"},
        "events": [
            {
                "event_date": "2025-03-01",
                "corp_name": "A",
                "event_type": "capital_raise",
                "event_title": "유상증자",
                "rcept_no": "20250301000001",
            }
        ],
        "event_type_counts": {"capital_raise": 1},
        "total_events": 1,
        "data_quality": {"status": "usable", "source": "disclosure_events"},
    }

    out = _attach_meta("search_disclosure_events", result)

    pack = out["answer_pack"]
    assert pack["summary"]["title"] == "공시 이벤트 타임라인"
    assert pack["timelines"][0]["events"][0]["rcept_no"] == "20250301000001"
    assert any(chart["id"] == "event_type_distribution" and chart["type"] == "bar" for chart in pack["charts"])
    assert pack["sources"][0]["url"].endswith("20250301000001")


def test_attach_meta_adds_peer_benchmark_pack():
    result = {
        "subject": {"corp_name": "A"},
        "years": [2024, 2025],
        "metrics": ["ROE", "영업이익률"],
        "n_peers": 30,
        "confidence": "high",
        "results": {
            2025: {
                "ROE": {"p25": 0.05, "p50": 0.1, "p75": 0.15, "subject_value": 0.12, "percentile": 70, "n": 30, "unit": "ratio"},
                "영업이익률": {"p25": 0.03, "p50": 0.08, "p75": 0.11, "subject_value": 0.2, "percentile": 90, "n": 30, "unit": "ratio"},
            }
        },
    }

    out = _attach_meta("compare_to_industry_multi", result)

    pack = out["answer_pack"]
    assert pack["summary"]["title"] == "A Peer 벤치마크"
    assert any(table["id"] == "peer_metric_matrix" for table in pack["tables"])
    assert any(chart["id"] == "peer_percentile_matrix" and chart["type"] == "heatmap" for chart in pack["charts"])


def test_answer_pack_normalizes_legacy_quality_through_the_v1_contract():
    from kreports.mcp.answer_pack import build_answer_pack

    pack = build_answer_pack("get_audit_report_sections", {
        "confirmed_facts": [{
            "statement": "감사보고서 본문이 확인되었습니다.",
            "source": {"rcept_no": "20250301000001"},
        }],
        "data_quality": {"status": "usable"},
    })

    assert pack is not None
    assert pack["data_quality"]["schema_version"] == "legacy-result-adapter"
    assert pack["sources"][0]["url"].startswith("https://dart.fss.or.kr/")


def test_audit_procedure_answer_pack_exposes_links_with_sufficiency_warning():
    result = {
        "subject": {"corp_name": "A"},
        "companies": [
            {
                "corp_name": "A",
                "records": [
                    {
                        "year": 2025,
                        "kam_topic": "revenue",
                        "method": "cutoff_test",
                        "procedure_type": "cutoff",
                        "procedure_excerpt": "기간귀속 테스트를 수행하였습니다.",
                        "assertion_hints": ["cutoff"],
                        "linked_metric_keys": ["revenue"],
                        "linked_note_keys": ["revenue_policy"],
                        "linked_event_keys": [],
                        "source_kam": {"id": 7, "rcept_no": "20260301000001"},
                    }
                ],
            }
        ],
        "data_quality": {
            "status": "usable",
            "source": "audit_procedure_items",
        },
    }

    out = _attach_meta("search_audit_procedures", result)

    table = next(
        table
        for table in out["answer_pack"]["tables"]
        if table["id"] == "audit_procedures"
    )
    assert table["rows"][0]["linked_metric_keys"] == ["revenue"]
    assert table["rows"][0]["source_kam"]["id"] == 7
    assert "navigation aid" in table["note"]
    assert "충분" in table["note"]
