from datetime import date
from decimal import Decimal
import re

import pytest

from kreports.mcp.tools import _attach_meta


def test_peer_answer_pack_omits_cohort_metadata_without_metric_results():
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

    assert pack["summary"]["status"] == "missing"
    assert [table["id"] for table in pack["tables"]] == ["availability"]


def test_policy_change_pack_keeps_proven_receipt_in_table_and_sources_only():
    """A limited mixed result keeps the row audit trail without citing bad receipts."""
    from kreports.mcp.answer_pack import build_answer_pack

    pack = build_answer_pack("get_accounting_policy_changes", {
        "subject": {"corp_name": "A"},
        "changed_items": [
            {
                "year": 2024, "fs_div": "CFS", "note_no": "2",
                "note_title": "중요한 회계정책", "section_type": "policy",
                "change_type": "changed", "similarity_to_previous": 0.5,
                "rcept_no": "20250301000001",
                "provenance_status": "proven_annual_filing",
            },
            {
                "year": 2023, "fs_div": "CFS", "note_no": "2",
                "note_title": "중요한 회계정책", "section_type": "policy",
                "change_type": "changed", "similarity_to_previous": 0.4,
                "rcept_no": "bad-receipt",
                "provenance_status": "invalid_receipt",
            },
        ],
        "confirmed_facts": [{
            "statement": "2024년 주석 2 텍스트 변경 후보가 확인되었습니다.",
            "source": {
                "corp_name": "A", "rcept_no": "20250301000001",
                "report_nm": "사업보고서 (2024.12)", "section_title": "주석 2",
            },
        }],
        "data_quality": {"status": "limited"},
    })

    table = next(
        table for table in pack["tables"]
        if table["id"] == "accounting_policy_changes"
    )
    assert table["rows"] == [
        {
            "year": 2024, "fs_div": "CFS", "note_no": "2",
            "note_title": "중요한 회계정책", "section_type": "policy",
            "change_type": "changed", "similarity_to_previous": 0.5,
            "rcept_no": "20250301000001",
            "provenance_status": "proven_annual_filing",
        },
        {
            "year": 2023, "fs_div": "CFS", "note_no": "2",
            "note_title": "중요한 회계정책", "section_type": "policy",
            "change_type": "changed", "similarity_to_previous": 0.4,
            "rcept_no": "bad-receipt",
            "provenance_status": "invalid_receipt",
        },
    ]
    assert [source["rcept_no"] for source in pack["sources"]] == [
        "20250301000001",
    ]


def test_note_comparison_answer_pack_does_not_link_an_unbound_cached_receipt():
    from kreports.mcp.answer_pack import build_answer_pack

    pack = build_answer_pack("compare_peer_accounting_policies", {
        "subject": {"corp_name": "A"},
        "note_comparison": {
            "year": 2024,
            "topics": [{
                "topic": "leases",
                "rows": [{
                    "company": {"corp_name": "A"},
                    "rcept_no": "20250301000001",
                    "provenance_status": "unproven_source_binding",
                }],
            }],
        },
    })

    assert pack["sources"] == []


def test_policy_presentation_answer_pack_does_not_link_status_only_receipt():
    from kreports.mcp.answer_pack import build_answer_pack

    pack = build_answer_pack("compare_peer_accounting_policies", {
        "subject": {"corp_name": "A"},
        "year": 2024,
        "note_presentations": [{
            "corp_code": "00000001",
            "corp_name": "A",
            "rcept_no": "20250301000001",
            "provenance_status": "proven_annual_filing",
            "canonical_source_binding": True,
        }],
    })

    assert pack["sources"] == []


def test_policy_presentation_answer_pack_rechecks_exact_source_document_binding(
    temp_engine,
):
    """A forged source-document id must not borrow a receipt-level DART link."""
    from kreports.db.engine import get_session
    from kreports.db.models import Disclosure, SourceDocument
    from kreports.mcp.answer_pack import build_answer_pack

    with get_session() as session:
        source = SourceDocument(
            rcept_no="20250301000001",
            corp_code="00000001",
            bsns_year=2024,
            source_type="business_report",
            report_nm="사업보고서 (2024.12)",
            raw_content="<xml/>",
            doc_hash="a" * 40,
        )
        session.add(source)
        session.flush()
        source_document_id = source.id
        session.add(
            Disclosure(
                rcept_no="20250301000001",
                corp_code="00000001",
                corp_name="A",
                disc_date=date(2025, 3, 1),
                disc_type="A",
                report_nm="사업보고서 (2024.12)",
            )
        )

    base_row = {
        "corp_code": "00000001",
        "corp_name": "A",
        "data_year": 2024,
        "rcept_no": "20250301000001",
        "source_type": "business_report",
        "provenance_status": "proven_annual_filing",
        "canonical_source_binding": True,
    }
    forged = build_answer_pack("compare_peer_accounting_policies", {
        "subject": {"corp_name": "A"},
        "year": 2024,
        "note_presentations": [{
            **base_row,
            "source_document_id": source_document_id + 100,
        }],
    })
    valid = build_answer_pack("compare_peer_accounting_policies", {
        "subject": {"corp_name": "A"},
        "year": 2024,
        "note_presentations": [{
            **base_row,
            "source_document_id": source_document_id,
        }],
    })

    assert forged["sources"] == []
    assert [source["rcept_no"] for source in valid["sources"]] == [
        "20250301000001",
    ]


@pytest.mark.parametrize("missing_field", ["source_document_id", "source_type"])
def test_note_comparison_answer_pack_requires_explicit_source_identity(
    temp_engine, missing_field,
):
    from kreports.db.engine import get_session
    from kreports.db.models import Disclosure, SourceDocument
    from kreports.mcp.answer_pack import build_answer_pack

    with get_session() as session:
        session.add_all([
            SourceDocument(
                rcept_no="20250301000001", corp_code="00000001", bsns_year=2024,
                source_type="business_report", report_nm="사업보고서 (2024.12)",
                raw_content="<xml/>", doc_hash="a" * 40,
            ),
            Disclosure(
                rcept_no="20250301000001", corp_code="00000001", corp_name="A",
                disc_date=date(2025, 3, 1), disc_type="A", report_nm="사업보고서 (2024.12)",
            ),
        ])
    row = {
        "company": {"corp_code": "00000001", "corp_name": "A"},
        "rcept_no": "20250301000001",
        "source_document_id": 1,
        "source_type": "business_report",
        "provenance_status": "proven_annual_filing",
    }
    row.pop(missing_field)

    pack = build_answer_pack("compare_peer_accounting_policies", {
        "subject": {"corp_name": "A"},
        "note_comparison": {
            "year": 2024,
            "topics": [{"topic": "leases", "rows": [row]}],
        },
    })

    assert pack["sources"] == []


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
    assert any(table["id"] == "dcf_candidates" for table in pack["tables"])
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
        if table["id"] == "dcf_candidates"
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
        if table["id"] == "dcf_candidates"
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
        if table["id"] == "dcf_candidates"
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
        if table["id"] == "dcf_candidates"
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
    assert pack["limitations"]
    assert all(re.search(r"[가-힣]", limitation) for limitation in pack["limitations"])
    assert not any(
        re.fullmatch(r"[a-z][a-z0-9_]*(?::[a-z0-9_,.-]+)+", limitation)
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
        if table["id"] == "industry_metrics"
    )
    assert [
        (row["metric"], row["unit"]) for row in table["rows"]
    ] == [
        ("자기자본이익률(ROE)", "ratio"),
        ("영업이익률", "ratio"),
        ("감사보수", "KRW"),
    ]
    columns = {
        column["field"]: column for column in table["columns"]
    }
    assert columns["subject_value"]["label"] == "대상회사 값"
    assert columns["p25"]["label"] == "비교군 P25 값"
    assert columns["p50"]["label"] == "비교군 중앙값 P50"
    assert columns["p75"]["label"] == "비교군 P75 값"
    assert columns["percentile"]["unit"] == "%"
    assert columns["n"]["unit"] == "개"
    assert any(chart["id"] == "peer_percentile_matrix" and chart["type"] == "heatmap" for chart in pack["charts"])
    assert not any(chart["id"] == "peer_band" for chart in pack["charts"])
    assert "표시 가능한 수치 또는 일관된 단위를 확보하지 못해 시각화를 제공하지 않습니다." in pack["limitations"]


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
    assert "표시 가능한 수치 또는 일관된 단위를 확보하지 못해 시각화를 제공하지 않습니다." in pack["limitations"]


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
        if table.id == "peer_audit_fee_benchmark"
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


def test_audit_fee_answer_pack_exposes_three_year_scale_before_peer_distribution():
    from kreports.mcp.answer_pack import build_answer_pack

    pack = build_answer_pack("compare_peer_audit_fees", {
        "subject": {"corp_name": "대상회사"},
        "subject_scale_history": [
            {
                "year": 2024,
                "fs_div": "CFS",
                "total_assets_100m": 40_000.0,
                "revenue_100m": 20_000.0,
                "audit_fee_m": 800,
                "audit_hours": 8_000,
                "audit_hours_per_trillion_assets": 2_000.0,
                "audit_hours_per_trillion_revenue": 4_000.0,
                "audit_source_rcept_no": "20250318000123",
            },
            {
                "year": 2023,
                "fs_div": "CFS",
                "total_assets_100m": 30_000.0,
                "revenue_100m": 15_000.0,
                "audit_fee_m": 600,
                "audit_hours": 6_000,
                "audit_hours_per_trillion_assets": 2_000.0,
                "audit_hours_per_trillion_revenue": 4_000.0,
                "audit_source_rcept_no": "20240318000456",
            },
            {
                "year": 2022,
                "fs_div": "CFS",
                "missing_fields": [
                    "total_assets",
                    "revenue",
                    "audit_fee_m",
                    "audit_hours",
                ],
                "missing_fields_label": "총자산, 매출액, 감사보수, 감사시간",
            },
        ],
        "subject_metrics": {
            "corp_name": "대상회사",
            "audit_fee_m": 800,
            "audit_hours": 8_000,
        },
        "peers": [{
            "corp_name": "비교회사",
            "audit_fee_m": 200,
            "audit_hours": 2_000,
        }],
        "data_quality": {"status": "limited"},
    })

    assert [table["id"] for table in pack["tables"]][:2] == [
        "subject_scale_history",
        "peer_audit_fee_benchmark",
    ]
    scale = pack["tables"][0]
    assert [column["field"] for column in scale["columns"]] == [
        "year",
        "fs_div",
        "total_assets_100m",
        "revenue_100m",
        "audit_fee_m",
        "audit_hours",
        "auditor_nm",
        "missing_fields_label",
    ]
    assert scale["rows"][0]["total_assets_100m"] == 40_000.0
    assert scale["rows"][2]["missing_fields_label"] == (
        "총자산, 매출액, 감사보수, 감사시간"
    )
    assert [source["rcept_no"] for source in pack["sources"]] == [
        "20250318000123",
        "20240318000456",
    ]


def test_acceptance_answer_pack_exposes_subject_scale_history():
    from kreports.mcp.answer_pack import build_answer_pack

    pack = build_answer_pack("build_audit_acceptance_pack", {
        "subject": {"corp_name": "대상회사"},
        "year": 2024,
        "subject_scale_history": [{
            "year": 2024,
            "fs_div": "CFS",
            "total_assets_100m": 40_000.0,
            "revenue_100m": 20_000.0,
            "audit_fee_m": 800,
            "audit_hours": 8_000,
            "audit_hours_per_trillion_assets": 2_000.0,
            "audit_hours_per_trillion_revenue": 4_000.0,
            "audit_source_rcept_no": "20250318000123",
            "missing_fields": [],
        }],
        "acceptance_signals": [{
            "area": "audit_report_matters",
            "severity": "info",
            "signal": "audit_report_other_matter_paragraph_present",
        }],
        "data_quality": {"status": "limited"},
        "limitations": [
            "This pack supports acceptance/continuance screening only.",
        ],
    })

    assert pack["summary"]["title"] == "대상회사 감사 검토 근거"
    assert [table["id"] for table in pack["tables"]][:2] == [
        "subject_scale_history",
        "acceptance_requirements",
    ]
    assert pack["tables"][0]["rows"][0]["audit_hours"] == 8_000
    assert pack["sources"][0]["rcept_no"] == "20250318000123"


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
            "dcf_candidates",
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
            "peer_audit_fee_benchmark",
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
            "industry_metrics",
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


def test_answer_pack_rejects_arbitrary_top_level_url_from_another_tool():
    """A safe URL is not evidence unless the current tool binds it to a fact."""
    from kreports.mcp.answer_pack import build_answer_pack
    from kreports.mcp.resources import read_resource

    arbitrary_url = "https://example.com/unbound-reference"
    arbitrary_label = "임의 상위 출처"
    pack = build_answer_pack("get_kam_lifecycle", {
        "subject": {"corp_name": "A"},
        "events": [{"year": 2025, "topic": "수익인식", "status": "new"}],
        "sources": [{
            "source_label": arbitrary_label,
            "source_url": arbitrary_url,
        }],
        "data_quality": {"status": "usable"},
    })

    assert pack is not None
    assert arbitrary_url not in {source["url"] for source in pack["sources"]}
    assert arbitrary_label not in read_resource(pack["resource_uri"])["text"]


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
    assert validated.tables[0].id == "kam_timeline"
    assert validated.charts[0].data_ref == "kam_timeline"


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


def test_audit_history_uses_dedicated_history_columns_not_audit_fee_title():
    from kreports.mcp.answer_pack import build_answer_pack

    pack = build_answer_pack("get_audit_history", {
        "subject": {"corp_name": "A"},
        "history": [{
            "year": 2025, "fs_div": "CFS", "auditor_nm": "감사법인",
            "audit_opinion": "적정", "auditor_changed": False,
            "consecutive_years": 3, "rcept_no": "20260310002820",
        }],
        "data_quality": {"status": "usable"},
    })

    table = next(table for table in pack["tables"] if table["id"] == "audit_history")
    assert table["title"] == "감사인 이력"
    assert [column["label"] for column in table["columns"]] == [
        "연도", "FS", "감사인", "감사의견", "변경 여부", "연속감사연수", "접수번호",
    ]
    assert "감사보수" not in str(pack)


def test_acceptance_pack_has_exactly_seven_public_review_rows_without_internal_signal_keys():
    from kreports.mcp.answer_pack import build_answer_pack

    sections = {
        key: {
            "status": "limited", "required": True, "applicability": "applicable",
            "coverage": {}, "blockers": ["audit_effort_helper_not_integrated"], "sources": [],
        }
        for key in (
            "peer_group", "audit_effort", "financial_risk", "audit_history",
            "accounting_policy", "kam", "audit_report_matters",
        )
    }
    pack = build_answer_pack("build_audit_acceptance_pack", {
        "subject": {"corp_name": "A"},
        "risk_summary": {"benchmarks": {"accrual_ratio": {"n": 5}}},
        "data_quality": {"status": "limited", "section_statuses": sections, "kam_body": {"status": "usable"}},
        "acceptance_signals": [{"signal": "audit_report_other_matter_paragraph_present"}],
    })

    table = next(table for table in pack["tables"] if table["id"] == "acceptance_requirements")
    assert len(table["rows"]) == 7
    assert [column["label"] for column in table["columns"]] == [
        "검토영역", "상태", "확인 사실", "값/coverage", "접수번호", "필수 후속 확인",
    ]
    assert "kam_body" not in str(table)
    assert "audit_report_other_matter_paragraph_present" not in str(table)


def test_acceptance_answer_pack_keeps_peer_and_metric_denominator_detail_with_public_labels():
    from kreports.mcp.answer_pack import build_answer_pack

    sections = {
        key: {
            "status": "usable", "required": True, "applicability": "applicable",
            "coverage": {}, "blockers": [], "sources": [],
        }
        for key in (
            "peer_group", "audit_effort", "financial_risk", "audit_history",
            "accounting_policy", "kam", "audit_report_matters",
        )
    }
    pack = build_answer_pack("build_audit_acceptance_pack", {
        "subject": {"corp_name": "A"},
        "peer_group": {
            "peer_count": 6,
            "sample_peers": [
                {
                    "corp_name": f"Peer {index}",
                    "stock_code": f"00000{index}",
                    "include_reasons": ["same_ksic_prefix", "audit_fee_available"],
                }
                for index in range(1, 7)
            ],
        },
        "risk_summary": {
            "metric_rows": [{
                "metric": "receivables_to_revenue",
                "peer_n": 6,
                "p25": 0.1,
                "p50": 0.2,
                "p75": 0.3,
                "subject_value": 0.25,
                "limitation": None,
            }],
            "benchmarks": {"receivables_to_revenue": {"n": 6}},
        },
        "data_quality": {"status": "usable", "section_statuses": sections},
    })

    peer_table = next(
        table for table in pack["tables"]
        if table["id"] == "audit_acceptance_peer_group"
    )
    metric_table = next(
        table for table in pack["tables"]
        if table["id"] == "audit_acceptance_risk_metrics"
    )
    assert len(peer_table["rows"]) == 6
    assert peer_table["rows"][0]["peer_n"] == 6
    assert peer_table["rows"][0]["selection_basis"] == "동일 업종 분류, 감사보수 확인"
    assert metric_table["rows"][0]["metric"] == "매출채권/매출"
    assert metric_table["rows"][0]["peer_n"] == 6
    assert "receivables_to_revenue" not in str(pack)
    assert "same_ksic_prefix" not in str(pack)


@pytest.mark.parametrize(
    ("tool_name", "result", "required_table_id"),
    [
        (
            "get_kam_lifecycle",
            {"events": [{"year": 2025, "topic": "수익인식", "status": "new"}]},
            "kam_timeline",
        ),
        (
            "get_quality_of_earnings_pack",
            {
                "metrics": {"cash_conversion": 1.1},
                "signals": [{"signal": "cash_conversion", "severity": "low"}],
            },
            "quality_of_earnings",
        ),
        (
            "compare_peer_audit_report_matters",
            {"subject_matters": [{"section_key": "emphasis", "rcept_no": "20260310002820"}]},
            "peer_audit_report_matters",
        ),
    ],
)
def test_priority_pack_uses_exact_public_table_id(
    tool_name,
    result,
    required_table_id,
):
    from kreports.mcp.answer_pack import build_answer_pack

    pack = build_answer_pack(tool_name, result)

    assert any(table["id"] == required_table_id for table in pack["tables"])


def test_audit_report_sections_pack_keeps_classified_non_kam_rows():
    """Dropping non-KAM rows behind the KAM-only adapter breaks the public table."""
    from kreports.mcp.answer_pack import build_answer_pack

    pack = build_answer_pack("get_audit_report_sections", {
        "subject": {"corp_name": "대상회사"},
        "sections": [{
            "bsns_year": 2025,
            "section_key": "audit_opinion",
            "section_title": "감사의견",
            "source_type": "audit_report",
            "body_excerpt": "재무제표는 중요성의 관점에서 적정하게 표시되어 있습니다.",
            "rcept_no": "20260310002820_001_xml",
        }],
        "data_quality": {"status": "limited"},
    })

    assert pack is not None
    table = next(
        table for table in pack["tables"]
        if table["id"] == "audit_report_sections"
    )
    assert table["rows"] == [{
        "year": 2025,
        "section_type": "감사의견",
        "section_title": "감사의견",
        "source_type": "감사보고서",
        "rcept_no": "20260310002820",
    }]
    assert all(table["id"] != "availability" for table in pack["tables"])


@pytest.mark.parametrize(
    ("tool_name", "result", "table_id"),
    [
        (
            "compare_peer_kam_topics",
            {
                "subject": {"corp_name": "대상회사"},
                "subject_sections": [{
                    "corp_name": "대상회사",
                    "bsns_year": 2025,
                    "section_key": "kam",
                    "rcept_no": "20260310002820_001_xml",
                    "kam_analysis": {
                        "topics": ["revenue"],
                        "has_reason_hint": True,
                        "has_procedure_hint": True,
                    },
                }],
                "peer_section_samples": {
                    "internal-peer-code": [{
                        "corp_name": "비교회사",
                        "bsns_year": 2025,
                        "section_key": "kam",
                        "rcept_no": "20260310002821_001_xml",
                        "kam_analysis": {
                            "topics": ["inventory"],
                            "has_reason_hint": True,
                            "has_procedure_hint": False,
                        },
                    }],
                },
                "audit_report_sections": {},
                "data_quality": {"status": "limited"},
            },
            "peer_kam_topics",
        ),
        (
            "compare_peer_audit_report_matters",
            {
                "subject": {"corp_name": "대상회사"},
                "subject_matters": [{
                    "corp_name": "대상회사",
                    "section_key": "emphasis",
                    "matter_category": "emphasis",
                    "acceptance_signal": True,
                    "rcept_no": "20260310002820_001_xml",
                }],
                "peer_matter_samples": {
                    "internal-peer-code": [{
                        "corp_name": "비교회사",
                        "section_key": "other_matter",
                        "matter_category": "other_matter",
                        "acceptance_signal": False,
                        "rcept_no": "20260310002821_001_xml",
                    }],
                },
                "data_quality": {"status": "limited"},
            },
            "peer_audit_report_matters",
        ),
    ],
)
def test_peer_priority_tables_keep_subject_and_public_peer_evidence_rows(
    tool_name,
    result,
    table_id,
):
    """Ignoring peer sample maps makes an exact-ID peer table materially empty."""
    from kreports.mcp.answer_pack import build_answer_pack

    pack = build_answer_pack(tool_name, result)

    assert pack is not None
    table = next(table for table in pack["tables"] if table["id"] == table_id)
    assert len(table["rows"]) == 2
    assert {row["role"] for row in table["rows"]} == {"대상회사", "비교회사"}
    assert {row["corp_name"] for row in table["rows"]} == {"대상회사", "비교회사"}
    assert {row["rcept_no"] for row in table["rows"]} == {
        "20260310002820",
        "20260310002821",
    }
    assert "internal-peer-code" not in str(pack)


@pytest.mark.parametrize(
    ("missing_input", "basis"),
    [
        ("revenue", "requested_dcf_source_actual"),
        ("wacc", "analyst_input"),
    ],
)
def test_missing_input_only_dcf_pack_emits_material_readiness_blocker(
    missing_input,
    basis,
):
    """Depending only on missing_accounts erases a real missing-input blocker."""
    from kreports.mcp.answer_pack import build_answer_pack

    pack = build_answer_pack("build_dcf_model_pack", {
        "company": "대상회사",
        "base_year": 2025,
        "fs_div": "CFS",
        "enterprise_value": None,
        "calculation_status": "unavailable",
        "domain_verdict": "calculation_unavailable",
        "missing_inputs": [missing_input],
        "missing_accounts": [],
        "data_quality": {"status": "missing"},
    })

    assert pack is not None
    table = next(
        table for table in pack["tables"]
        if table["id"] == "dcf_model_readiness"
    )
    assert table["rows"] == [{
        "field": missing_input,
        "status": "blocked",
        "year": 2025,
        "fs_div": "CFS",
        "basis": basis,
    }]


def test_policy_pack_renders_consolidated_side_by_side_note_comparison():
    from kreports.mcp.answer_pack import build_answer_pack

    pack = build_answer_pack("compare_peer_accounting_policies", {
        "subject": {"corp_code": "001", "corp_name": "대상회사"},
        "data_quality": {"status": "limited"},
        "note_comparison": {
            "topics": [{
                "topic": "leases",
                "rows": [{
                    "company": {"corp_code": "001", "corp_name": "대상회사"},
                    "value_or_excerpt": "리스부채 측정",
                    "availability": "available",
                    "rcept_no": "20250301000001",
                    "source_locator": "accounting_note_chapters:1",
                }],
            }],
        },
    })

    assert pack is not None
    table = next(
        table for table in pack["tables"]
        if table["id"] == "peer_topic_note_comparison"
    )
    assert table["rows"] == [{
        "topic": "leases",
        "company": "대상회사",
        "note_title": None,
        "matched_keyword": None,
        "match_location": None,
        "match_strength": None,
        "matched_keyword_count": None,
        "excerpt": "리스부채 측정",
        "availability": "available",
        "cache_status": None,
        "receipt": "20250301000001",
        "source_locator": "accounting_note_chapters:1",
    }]


def test_policy_pack_renders_topic_to_company_disclosure_matrix_without_absence_claim():
    from kreports.mcp.answer_pack import build_answer_pack

    pack = build_answer_pack("compare_peer_accounting_policies", {
        "subject": {"corp_code": "001", "corp_name": "대상회사"},
        "data_quality": {"status": "limited"},
        "note_disclosure_matrix": {
            "year": 2024,
            "is_complete": False,
            "omitted_company_topic_rows": 1,
            "rate_scope": "returned_topic_rows",
            "topics": [{
                "topic": "leases",
                "local_evidence_rate": {
                    "numerator": 1, "denominator": 3, "pct": 33.3,
                    "reviewable_denominator": 2, "unavailable_count": 1,
                    "matched_count": 1, "all_company_count": 3,
                    "reviewable_company_count": 2,
                    "matched_within_reviewable_pct": 50.0,
                },
                "companies": [
                    {
                        "company": {"corp_code": "001", "corp_name": "대상회사"},
                        "status": "disclosed",
                        "note_title": "리스",
                        "excerpt": "리스부채 측정",
                        "match_evidence": {"keyword": "리스부채", "location": "body", "strength": "body_single_signal_reference"},
                        "rcept_no": "20250301000001",
                        "provenance_status": "proven_annual_filing",
                    },
                    {
                        "company": {"corp_code": "002", "corp_name": "범위내미일치회사"},
                        "status": "not_found_in_cached_scope",
                        "rcept_no": "20250301000002",
                        "disclosure_assessment": "topic_not_found_in_cached_scope_not_non_disclosure",
                    },
                    {
                        "company": {"corp_code": "002", "corp_name": "미확보회사"},
                        "status": "unavailable_raw",
                        "unavailable_reason": "local_topic_cache_missing",
                    },
                ],
            }],
        },
    })

    assert pack is not None
    table = next(table for table in pack["tables"] if table["id"] == "topic_company_disclosure_matrix")
    assert table["title"] == "주제별 회사 주석 로컬 확인 매트릭스"
    assert table["rows"][1]["status"] == "not_found_in_cached_scope"
    assert table["rows"][1]["disclosure_assessment"] == (
        "topic_not_found_in_cached_scope_not_non_disclosure"
    )
    assert table["rows"][2]["status"] == "unavailable_raw"
    assert table["rows"][2]["disclosure_assessment"] == "not_assessed"
    assert table["rows"][0]["matched_within_reviewable_pct"] == 50.0
    assert "공시 부재 판단이 not_assessed" in table["note"]
    assert "반환된 topic rows" in table["note"]
    assert "생략 회사-주제 행: 1" in table["note"]
