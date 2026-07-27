from decimal import Decimal

import pytest

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


def test_dcf_candidate_registry_preserves_semantic_labels_and_mixed_units():
    from kreports.mcp.answer_pack import build_answer_pack

    pack = build_answer_pack("get_dcf_input_candidates", {
        "subject": {"corp_name": "A"},
        "candidate_assumptions": {
            "revenue_growth": {"value": 0.1, "basis": "historical_median"},
            "operating_margin": {"value": 0.12, "basis": "historical_median"},
            "cash_conversion": {"value": 1.1, "basis": "historical_median"},
            "tax_rate": {"value": 0.2, "basis": "historical_median"},
            "capex_to_revenue": {"value": 0.05, "basis": "historical_median"},
            "da_to_revenue": {"value": 0.04, "basis": "historical_median"},
            "nwc_to_revenue": {"value": 0.15, "basis": "historical_median"},
            "wacc": {"value": 0.1, "basis": "analyst_input"},
            "terminal_growth": {"value": 0.03, "basis": "analyst_input"},
            "normalized_revenue": {
                "value": 1_000_000,
                "basis": "analyst_input",
            },
        },
        "data_quality": {"status": "usable"},
    })
    table = next(
        table for table in pack["tables"]
        if table["id"] == "candidate_assumptions"
    )
    rows = {row["metric"]: row for row in table["rows"]}
    expected = {
        "매출 성장률": "ratio",
        "영업이익률": "ratio",
        "현금전환율": "ratio",
        "세율": "ratio",
        "매출 대비 CAPEX 비율": "ratio",
        "매출 대비 감가상각비 비율": "ratio",
        "매출 대비 운전자본 비율": "ratio",
        "가중평균자본비용 WACC": "ratio",
        "영구성장률": "ratio",
        "정규화 매출": "KRW",
    }
    assert {
        key: row["unit"]
        for key, row in rows.items()
    } == expected
    assert all(row["metric"] != "기타 입력값" for row in rows.values())
    value_column = next(
        column for column in table["columns"]
        if column["field"] == "value"
    )
    assert value_column.get("unit") is None
    assert not any(
        chart["id"] == "dcf_input_bridge"
        for chart in pack["charts"]
    )
    assert "dcf_candidate_chart_suppressed:mixed_units:KRW,ratio" in (
        pack["limitations"]
    )


def test_homogeneous_dcf_candidates_use_static_and_visible_ratio_unit():
    from kreports.mcp.answer_pack import build_answer_pack

    pack = build_answer_pack("get_dcf_input_candidates", {
        "candidate_assumptions": {
            "revenue_growth": {"value": 0.1},
            "tax_rate": {"value": 0.2},
            "capex_to_revenue": {"value": 0.05},
        },
        "data_quality": {"status": "usable"},
    })
    table = next(
        table for table in pack["tables"]
        if table["id"] == "candidate_assumptions"
    )
    value_column = next(
        column for column in table["columns"]
        if column["field"] == "value"
    )
    assert value_column["unit"] == "ratio"
    chart = next(
        chart for chart in pack["charts"]
        if chart["id"] == "dcf_input_bridge"
    )
    assert chart["title"].endswith("(ratio)")
    assert chart["encodings"]["color"]["field"] == "unit"


def test_registered_dcf_candidate_unit_cannot_be_overridden_by_caller():
    from kreports.mcp.answer_pack import build_answer_pack

    pack = build_answer_pack("get_dcf_input_candidates", {
        "candidate_assumptions": {
            "revenue_growth": {"value": 0.1, "unit": "KRW"},
            "tax_rate": {"value": 0.2, "unit": "KRW"},
        },
        "data_quality": {"status": "usable"},
    })
    table = next(
        table for table in pack["tables"]
        if table["id"] == "candidate_assumptions"
    )

    assert {row["unit"] for row in table["rows"]} == {"ratio"}
    value_column = next(
        column for column in table["columns"]
        if column["field"] == "value"
    )
    assert value_column["unit"] == "ratio"
    assert next(
        chart for chart in pack["charts"]
        if chart["id"] == "dcf_input_bridge"
    )["title"].endswith("(ratio)")


def test_unknown_dcf_candidate_uses_public_fallback_without_key_leak():
    from kreports.mcp.answer_pack import build_answer_pack

    pack = build_answer_pack("get_dcf_input_candidates", {
        "candidate_assumptions": {
            "custom_review_metric": {"value": 123},
        },
        "data_quality": {"status": "usable"},
    })
    table = next(
        table for table in pack["tables"]
        if table["id"] == "candidate_assumptions"
    )
    assert table["rows"][0] == {
        "metric": "사용자 정의 입력 후보",
        "value": 123,
        "unit": None,
        "basis": "산정 근거 미확보",
    }
    assert "custom_review_metric" not in str(pack)
    assert not any(
        chart["id"] == "dcf_input_bridge"
        for chart in pack["charts"]
    )
    assert "dcf_candidate_chart_suppressed:missing_units" in (
        pack["limitations"]
    )


def test_peer_pack_suppresses_charts_without_numeric_encoded_facts():
    from kreports.mcp.answer_pack import build_answer_pack

    pack = build_answer_pack("compare_to_industry_multi", {
        "subject": {"corp_name": "A"},
        "results": {
            2024: {
                "ROE": {
                    "subject_value": None,
                    "percentile": None,
                    "p25": None,
                    "p50": None,
                    "p75": None,
                    "n": 30,
                    "unit": "ratio",
                },
            },
        },
        "data_quality": {"status": "usable"},
    })

    assert not pack["charts"]
    assert any(
        limitation.startswith("peer_chart_suppressed:no_numeric_facts")
        for limitation in pack["limitations"]
    )


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


@pytest.mark.parametrize(
    "audit_fee",
    ["123.5", pytest.param(Decimal("123.5"), id="decimal")],
)
def test_audit_fee_chart_accepts_contract_numeric_types(audit_fee):
    from kreports.mcp.answer_pack import build_answer_pack

    pack = build_answer_pack(
        "compare_peer_audit_fees",
        {
            "subject": {"corp_name": "A"},
            "subject_metrics": {
                "corp_name": "A",
                "audit_fee_m": audit_fee,
            },
            "peers": [],
            "data_quality": {"status": "usable"},
        },
    )

    assert any(
        chart["id"] == "audit_fee_peer_chart"
        for chart in pack["charts"]
    )


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
    assert pack["timelines"][0]["table_ref"] == "disclosure_events"
    assert "events" not in pack["timelines"][0]
    event_table = next(
        table for table in pack["tables"]
        if table["id"] == "disclosure_events"
    )
    assert event_table["rows"][0]["rcept_no"] == "20250301000001"
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
                "감사보수": {"p25": 900_000, "p50": 1_100_000, "p75": 1_400_000, "subject_value": 1_200_000, "percentile": 60, "n": 30, "unit": "KRW"},
            }
        },
    }

    out = _attach_meta("compare_to_industry_multi", result)

    pack = out["answer_pack"]
    assert pack["summary"]["title"] == "A Peer 벤치마크"
    table = next(
        table for table in pack["tables"]
        if table["id"] == "peer_metric_matrix"
    )
    assert [
        (row["metric"], row["unit"]) for row in table["rows"]
    ] == [
        ("ROE", "ratio"),
        ("영업이익률", "ratio"),
        ("감사보수", "KRW"),
    ]
    columns = {
        column["field"]: column for column in table["columns"]
    }
    assert columns["subject_value"]["label"] == "대상회사 값"
    assert columns["p25"]["label"] == "Peer P25 값"
    assert columns["p50"]["label"] == "Peer 중앙값 P50"
    assert columns["p75"]["label"] == "Peer P75 값"
    assert columns["percentile"]["unit"] == "%"
    assert columns["n"]["unit"] == "개"
    assert any(chart["id"] == "peer_percentile_matrix" and chart["type"] == "heatmap" for chart in pack["charts"])
    assert not any(chart["id"] == "peer_band" for chart in pack["charts"])
    assert "peer_band_suppressed:mixed_units:KRW,ratio" in pack["limitations"]


@pytest.mark.parametrize("unit", ["ratio", "KRW"])
def test_real_peer_homogeneous_units_keep_unit_visible_band(unit):
    from kreports.mcp.answer_pack import build_answer_pack

    pack = build_answer_pack("compare_to_industry_multi", {
        "subject": {"corp_name": "A"},
        "results": {
            2025: {
                "metric-a": {
                    "p25": 1,
                    "p50": 2,
                    "p75": 3,
                    "subject_value": 2,
                    "percentile": 50,
                    "n": 30,
                    "unit": unit,
                },
                "metric-b": {
                    "p25": 2,
                    "p50": 3,
                    "p75": 4,
                    "subject_value": 3,
                    "percentile": 50,
                    "n": 30,
                    "unit": unit,
                },
            },
        },
        "data_quality": {"status": "usable"},
    })
    band = next(chart for chart in pack["charts"] if chart["id"] == "peer_band")
    assert band["title"].endswith(f"({unit})")
    assert band["encodings"]["color"]["field"] == "unit"


def test_real_peer_missing_units_suppress_raw_value_band():
    from kreports.mcp.answer_pack import build_answer_pack

    pack = build_answer_pack("compare_to_industry_multi", {
        "subject": {"corp_name": "A"},
        "results": {
            2025: {
                "ROE": {
                    "p25": 0.05,
                    "p50": 0.10,
                    "p75": 0.15,
                    "subject_value": 0.12,
                    "percentile": 70,
                    "n": 30,
                },
            },
        },
        "data_quality": {"status": "usable"},
    })
    assert any(
        chart["id"] == "peer_percentile_matrix"
        for chart in pack["charts"]
    )
    assert not any(chart["id"] == "peer_band" for chart in pack["charts"])
    assert "peer_band_suppressed:missing_units" in pack["limitations"]


def test_audit_peer_nas_ratio_is_explicitly_a_ratio_in_all_views():
    from kreports.mcp.answer_pack import build_answer_pack
    from kreports.mcp.visual_contracts import (
        VisualizationPackV1,
        render_visualization_html,
        render_visualization_markdown,
    )

    pack_dict = build_answer_pack("compare_peer_audit_fees", {
        "subject_metrics": {
            "corp_name": "A",
            "audit_fee_m": 100,
            "audit_hours": 500,
            "non_audit_fee_m": 20,
            "nas_ratio": 0.2,
        },
        "peers": [{
            "corp_name": "B",
            "audit_fee_m": 90,
            "audit_hours": 450,
            "non_audit_fee_m": 9,
            "nas_ratio": 0.1,
        }],
        "data_quality": {"status": "usable"},
    })
    pack = VisualizationPackV1.model_validate(pack_dict)
    table = next(
        table for table in pack.tables
        if table.id == "audit_fee_peer_distribution"
    )
    nas = next(column for column in table.columns if column.key == "nas_ratio")
    assert nas.label == "비감사보수 비율"
    assert nas.unit == "ratio"
    for rendered in (
        render_visualization_markdown(pack, mermaid=False),
        render_visualization_html(pack),
    ):
        assert "비감사보수 비율 (ratio)" in rendered
        assert "0.2" in rendered and "0.1" in rendered


def test_answer_pack_ratio_and_percent_column_inventory_is_explicit():
    from kreports.mcp.answer_pack import build_answer_pack

    cases = [
        (
            "get_dcf_input_candidates",
            {
                "candidate_assumptions": {
                    "revenue_growth": {"value": 0.1},
                    "operating_margin": {"value": 0.2},
                },
                "data_quality": {"status": "usable"},
            },
            "candidate_assumptions",
            {"value": "ratio"},
        ),
        (
            "compare_peer_audit_fees",
            {
                "subject_metrics": {
                    "corp_name": "A",
                    "audit_fee_m": 100,
                    "nas_ratio": 0.2,
                },
                "data_quality": {"status": "usable"},
            },
            "audit_fee_peer_distribution",
            {"nas_ratio": "ratio"},
        ),
        (
            "get_subsidiary_auditors",
            {
                "subject": {"corp_name": "A"},
                "subsidiaries": [{
                    "name": "B",
                    "ownership_pct": 80,
                    "asset_share_pct": 12,
                    "revenue_share_pct": 14,
                }],
                "data_quality": {"status": "usable"},
            },
            "subsidiary_contribution",
            {
                "ownership_pct": "%",
                "asset_share_pct": "%",
                "revenue_share_pct": "%",
            },
        ),
        (
            "compare_to_industry_multi",
            {
                "results": {
                    2025: {
                        "ROE": {
                            "subject_value": 0.12,
                            "percentile": 70,
                            "p25": 0.05,
                            "p50": 0.10,
                            "p75": 0.15,
                            "n": 30,
                            "unit": "ratio",
                        },
                    },
                },
                "data_quality": {"status": "usable"},
            },
            "peer_metric_matrix",
            {"percentile": "%"},
        ),
    ]

    for tool_name, result, table_id, expected in cases:
        pack = build_answer_pack(tool_name, result)
        table = next(
            table for table in pack["tables"]
            if table["id"] == table_id
        )
        units = {
            column["field"]: column.get("unit")
            for column in table["columns"]
        }
        assert {
            field: units[field]
            for field in expected
        } == expected


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


def test_answer_pack_is_validated_visual_contract_for_all_capabilities():
    from kreports.mcp.answer_pack import build_answer_pack
    from kreports.mcp.visual_contracts import VisualizationPackV1

    pack = build_answer_pack("get_kam_lifecycle", {
        "subject": {"corp_name": "A"},
        "events": [{"year": 2024, "topic": "수익인식", "status": "new"}],
        "data_quality": {"status": "usable"},
    })

    validated = VisualizationPackV1.model_validate(pack)
    assert validated.version == "visualization_pack.v1"
    assert validated.tables[0].id == "kam_lifecycle"
    assert validated.charts[0].data_ref == "kam_lifecycle"


def test_missing_visual_data_returns_explicit_table_and_limitation():
    from kreports.mcp.answer_pack import build_answer_pack
    from kreports.mcp.visual_contracts import VisualizationPackV1

    pack = build_answer_pack("compare_to_industry_multi", {
        "subject": {"corp_name": "A"},
        "results": {},
        "data_quality": {"status": "missing"},
        "limitations": ["Peer 표본을 확보하지 못했습니다."],
    })

    validated = VisualizationPackV1.model_validate(pack)
    assert validated.status == "missing"
    assert validated.tables
    assert validated.tables[0].status == "missing"
    assert validated.limitations
    assert not validated.charts
