from __future__ import annotations

import json
import math
import re

import pytest
from pydantic import ValidationError

from kreports.mcp.visual_contracts import (
    ChartSpecV1,
    DiagramSpecV1,
    TableSpecV1,
    VisualizationPackV1,
    build_group_diagram,
    build_visualization_pack,
    render_visualization_html,
    render_visualization_markdown,
)


def _table(**overrides):
    payload = {
        "id": "facts",
        "title": "확인된 수치",
        "columns": [
            {"key": "year", "label": "연도"},
            {"key": "value", "label": "값", "unit": "백만원"},
        ],
        "rows": [{"year": 2024, "value": 100}],
        "status": "usable",
    }
    payload.update(overrides)
    return payload


def _pack(**overrides):
    payload = {
        "tables": [_table()],
        "charts": [{
            "id": "trend",
            "type": "line",
            "title": "추이",
            "data_ref": "facts",
            "encodings": {
                "x": {"field": "year"},
                "y": {"field": "value"},
            },
        }],
        "diagrams": [],
        "status": "usable",
        "limitations": [],
    }
    payload.update(overrides)
    return payload


def test_contracts_are_strict_and_enforce_reference_and_column_integrity():
    with pytest.raises(ValidationError):
        TableSpecV1.model_validate({**_table(), "unexpected": True})
    with pytest.raises(ValidationError, match="duplicate column"):
        TableSpecV1.model_validate(_table(columns=[
            {"key": "year", "label": "연도"},
            {"key": "year", "label": "중복"},
        ]))
    with pytest.raises(ValidationError, match="unknown column"):
        TableSpecV1.model_validate(_table(rows=[{"year": 2024, "other": 1}]))
    with pytest.raises(ValidationError, match="duplicate table"):
        VisualizationPackV1.model_validate(_pack(tables=[_table(), _table()]))
    with pytest.raises(ValidationError, match="unknown table"):
        VisualizationPackV1.model_validate(_pack(charts=[{
            "id": "trend",
            "type": "line",
            "title": "추이",
            "data_ref": "missing",
            "encodings": {"x": {"field": "year"}},
        }]))
    with pytest.raises(ValidationError, match="undeclared column"):
        VisualizationPackV1.model_validate(_pack(charts=[{
            "id": "trend",
            "type": "line",
            "title": "추이",
            "data_ref": "facts",
            "encodings": {"x": {"field": "missing"}},
        }]))
    with pytest.raises(ValidationError):
        ChartSpecV1.model_validate({
            "id": "bad",
            "type": "pie",
            "title": "금지 차트",
            "data_ref": "facts",
            "encodings": {},
        })
    with pytest.raises(ValidationError):
        DiagramSpecV1.model_validate({
            "id": "group",
            "type": "mermaid",
            "title": "구조",
            "table_ref": "facts",
            "definition": "flowchart TD",
            "raw_html": "<script>",
        })


def test_chart_contract_rejects_mixed_units_on_one_quantitative_channel():
    mixed_table = _table(
        columns=[
            {"field": "year", "label": "연도"},
            {"field": "amount", "label": "금액", "unit": "KRW"},
            {"field": "margin", "label": "마진", "unit": "ratio"},
        ],
        rows=[{"year": 2024, "amount": 100, "margin": 0.1}],
    )
    with pytest.raises(ValidationError, match="mixed units"):
        VisualizationPackV1.model_validate(_pack(
            tables=[mixed_table],
            charts=[{
                "id": "mixed",
                "type": "line",
                "title": "금지 혼합축",
                "data_ref": "facts",
                "encodings": {
                    "x": {"field": "year"},
                    "y": {"fields": ["amount", "margin"]},
                },
            }],
        ))


def test_chart_contract_rejects_mixed_per_row_units_even_when_grouped():
    mixed_table = _table(
        columns=[
            {"field": "metric", "label": "지표"},
            {"field": "value", "label": "값"},
            {"field": "unit", "label": "단위"},
        ],
        rows=[
            {"metric": "매출", "value": 100, "unit": "KRW"},
            {"metric": "마진", "value": 0.1, "unit": "ratio"},
        ],
    )
    with pytest.raises(ValidationError, match="mixed row units"):
        VisualizationPackV1.model_validate(_pack(
            tables=[mixed_table],
            charts=[{
                "id": "mixed",
                "type": "bar",
                "title": "금지 혼합 비교",
                "data_ref": "facts",
                "encodings": {
                    "x": {"field": "metric"},
                    "y": {"field": "value"},
                    "color": {"field": "unit"},
                },
            }],
        ))


def test_chart_contract_requires_homogeneous_row_unit_to_be_visible():
    homogeneous_table = _table(
        columns=[
            {"field": "metric", "label": "지표"},
            {"field": "value", "label": "값"},
            {"field": "unit", "label": "단위"},
        ],
        rows=[
            {"metric": "ROE", "value": 0.1, "unit": "ratio"},
            {"metric": "ROA", "value": 0.05, "unit": "ratio"},
        ],
    )
    with pytest.raises(ValidationError, match="unit grouping must be visible"):
        VisualizationPackV1.model_validate(_pack(
            tables=[homogeneous_table],
            charts=[{
                "id": "hidden_unit",
                "type": "bar",
                "title": "단위 없는 비교",
                "data_ref": "facts",
                "encodings": {
                    "x": {"field": "metric"},
                    "y": {"field": "value"},
                },
            }],
        ))

    pack = VisualizationPackV1.model_validate(_pack(
        tables=[homogeneous_table],
        charts=[{
            "id": "visible_unit",
            "type": "bar",
            "title": "동일 단위 비교 (ratio)",
            "data_ref": "facts",
            "encodings": {
                "x": {"field": "metric"},
                "y": {"field": "value"},
                "color": {"field": "unit"},
            },
        }],
    ))
    assert pack.charts[0].encodings.color.field == "unit"


@pytest.mark.parametrize(
    "columns,rows,fields",
    [
        (
            [
                {"field": "metric", "label": "지표"},
                {"field": "value", "label": "값", "unit": "KRW"},
                {"field": "unit", "label": "행 단위"},
            ],
            [
                {"metric": "매출", "value": 100, "unit": "KRW"},
                {"metric": "마진", "value": 0.1, "unit": "ratio"},
            ],
            ["value"],
        ),
        (
            [
                {"field": "metric", "label": "지표"},
                {"field": "revenue", "label": "매출", "unit": "KRW"},
                {"field": "margin", "label": "마진", "unit": "KRW"},
                {"field": "unit", "label": "행 단위"},
            ],
            [{"metric": "혼합", "revenue": 100, "margin": 0.1, "unit": "ratio"}],
            ["revenue", "margin"],
        ),
    ],
)
def test_chart_contract_rejects_row_units_that_contradict_static_units(
    columns,
    rows,
    fields,
):
    with pytest.raises(ValidationError, match="unit"):
        VisualizationPackV1.model_validate(_pack(
            tables=[_table(columns=columns, rows=rows)],
            charts=[{
                "id": "contradictory_units",
                "type": "bar",
                "title": "단위 모순",
                "data_ref": "facts",
                "encodings": {
                    "x": {"field": "metric"},
                    "y": {"fields": fields},
                },
            }],
        ))


def test_chart_contract_rejects_unitless_or_empty_quantitative_axes():
    unitless = _table(
        columns=[
            {"field": "metric", "label": "지표"},
            {"field": "value", "label": "값"},
        ],
        rows=[{"metric": "ROE", "value": 0.1}],
    )
    with pytest.raises(ValidationError, match="unit"):
        VisualizationPackV1.model_validate(_pack(
            tables=[unitless],
            charts=[{
                "id": "unitless",
                "type": "bar",
                "title": "무단위 축",
                "data_ref": "facts",
                "encodings": {
                    "x": {"field": "metric"},
                    "y": {"field": "value"},
                },
            }],
        ))

    empty = _table(
        columns=[
            {"field": "metric", "label": "지표"},
            {"field": "value", "label": "값"},
            {"field": "unit", "label": "단위"},
        ],
        rows=[{"metric": "ROE", "value": None, "unit": "ratio"}],
    )
    with pytest.raises(ValidationError, match="numeric"):
        VisualizationPackV1.model_validate(_pack(
            tables=[empty],
            charts=[{
                "id": "empty_axis",
                "type": "bar",
                "title": "빈 축",
                "data_ref": "facts",
                "encodings": {
                    "x": {"field": "metric"},
                    "y": {"field": "value"},
                    "color": {"field": "unit"},
                },
            }],
        ))


@pytest.mark.parametrize(
    "bad_value",
    [
        math.nan,
        math.inf,
        -math.inf,
        object(),
        "x" * 2_001,
        "control\x00value",
    ],
)
def test_table_rows_reject_unbounded_or_non_json_values(bad_value):
    with pytest.raises((ValidationError, ValueError, TypeError)):
        TableSpecV1.model_validate(_table(rows=[
            {"year": 2024, "value": bad_value}
        ]))


@pytest.mark.parametrize(
    "empty_row",
    [
        {},
        {"year": None, "value": None},
        {"year": " \t", "value": ""},
        {"year": [], "value": {}},
        {"year": [None, " "], "value": {"nested": []}},
    ],
)
def test_typed_tables_reject_rows_without_any_declared_fact(empty_row):
    with pytest.raises(ValidationError, match="fact"):
        TableSpecV1.model_validate(_table(rows=[empty_row]))


def test_typed_tables_preserve_zero_and_false_but_missing_tables_carry_no_rows():
    zero = TableSpecV1.model_validate(_table(rows=[{
        "year": 0,
        "value": False,
    }]))
    assert zero.rows == [{"year": 0, "value": False}]

    with pytest.raises(ValidationError, match="missing table"):
        TableSpecV1.model_validate(_table(
            status="missing",
            rows=[{"year": 2024, "value": 0}],
        ))


def test_ids_text_rows_and_total_serialized_payload_are_bounded():
    with pytest.raises(ValidationError):
        TableSpecV1.model_validate(_table(id="../facts"))
    with pytest.raises(ValidationError):
        TableSpecV1.model_validate(_table(title="제목" * 200))
    with pytest.raises(ValidationError):
        TableSpecV1.model_validate(_table(rows=[
            {"year": year, "value": year} for year in range(300)
        ]))
    with pytest.raises(ValidationError, match="payload"):
        VisualizationPackV1.model_validate(_pack(
            limitations=["한계" * 1_000 for _ in range(40)]
        ))


def test_status_requires_explicit_limitations_and_forbids_empty_usable_pack():
    with pytest.raises(ValidationError, match="limitation"):
        VisualizationPackV1.model_validate(_pack(
            status="limited",
            limitations=[],
        ))
    with pytest.raises(ValidationError, match="usable"):
        VisualizationPackV1.model_validate(_pack(
            tables=[],
            charts=[],
            status="usable",
        ))
    limited = VisualizationPackV1.model_validate(_pack(
        tables=[_table(rows=[], status="missing", note="캐시 미확보")],
        charts=[],
        status="missing",
        limitations=["로컬 캐시에 확인 가능한 데이터가 없습니다."],
    ))
    assert limited.tables[0].rows == []


@pytest.mark.parametrize(
    ("family", "result", "table_id"),
    [
        ("financial_trend", {
            "historical_actuals": [{"year": 2024, "revenue": 100}],
        }, "historical_actuals"),
        ("dcf", {
            "projections": [{"year": 2025, "revenue": "100", "ufcf": "10"}],
            "sensitivity": [{
                "wacc": "0.10",
                "terminal_growth": "0.02",
                "enterprise_value": "120",
                "status": "valid",
            }],
        }, "dcf_projections"),
        ("peer_distribution", {
            "results": {2024: {"ROE": {
                "subject_value": 0.1,
                "p25": 0.05,
                "p50": 0.08,
                "p75": 0.12,
                "percentile": 65,
                "n": 20,
            }}},
        }, "peer_metric_matrix"),
        ("group_graph", {
            "entity_name": "A",
            "entities": [{
                "name": "B",
                "relation": "종속",
                "ownership_pct": 80,
            }],
        }, "group_entities"),
        ("audit_fee", {
            "history": [{"year": 2024, "audit_fee_m": 100, "audit_hours": 500}],
        }, "audit_fee_trend"),
        ("kam_lifecycle", {
            "events": [{"year": 2024, "topic": "수익인식", "status": "new"}],
        }, "kam_lifecycle"),
        ("disclosure_timeline", {
            "events": [{
                "event_date": "2025-01-01",
                "corp_name": "A",
                "event_type": "capital_raise",
                "event_title": "유상증자",
                "rcept_no": "20250101000001",
            }],
        }, "disclosure_events"),
    ],
)
def test_all_seven_families_have_canonical_markdown_tables(
    family,
    result,
    table_id,
):
    pack = build_visualization_pack({
        **result,
        "_visual_family": family,
        "_visual_status": "usable",
    })
    markdown = render_visualization_markdown(pack, mermaid=False)

    assert any(table.id == table_id for table in pack.tables)
    assert "| " in markdown
    assert all(chart.data_ref in {table.id for table in pack.tables}
               for chart in pack.charts)


def test_group_diagram_escapes_injection_and_references_its_table():
    diagram = build_group_diagram('A|B\n"C"')

    assert diagram.table_ref == "group_entities"
    assert 'A/B<br/>\\"C\\"' in diagram.definition
    assert "A|B" not in diagram.definition
    assert "\n\"C\"" not in diagram.definition


def test_markdown_mermaid_structured_and_html_have_fact_parity():
    pack = VisualizationPackV1.model_validate(_pack(
        tables=[_table(rows=[{"year": 2024, "value": "123.45"}])],
    ))
    plain = render_visualization_markdown(pack, mermaid=False)
    mermaid = render_visualization_markdown(pack, mermaid=True)
    structured = pack.model_dump(mode="json")
    rich = render_visualization_html(pack)

    for rendered in (plain, mermaid, rich):
        assert "2024" in rendered
        assert "123.45" in rendered
        assert "백만원" in rendered
    assert structured["tables"][0]["rows"][0]["value"] == "123.45"
    assert "<script" not in rich.lower()
    assert "http://" not in rich and "https://" not in rich
    assert len(rich.encode()) < 200_000


def test_renderer_boundaries_escape_markdown_mermaid_and_html_injection():
    attack = '</td><script>alert(1)</script>|# x\nclick X "https://evil"'
    pack = build_visualization_pack({
        "_visual_family": "group_graph",
        "_visual_status": "limited",
        "entity_name": attack,
        "entities": [{"name": attack, "relation": attack}],
        "limitations": [attack],
    })
    markdown = render_visualization_markdown(pack, mermaid=True)
    rich = render_visualization_html(pack)

    assert "<script" not in rich.lower()
    assert "https://evil" not in rich
    assert "</td><script" not in rich.lower()
    assert "&lt;/td&gt;&lt;script&gt;" in rich
    assert "\n# x" not in markdown
    assert "click X" not in pack.diagrams[0].definition
    json.dumps(pack.model_dump(mode="json"), allow_nan=False)


def test_direct_group_family_keeps_root_parent_and_ownership_as_canonical_facts():
    pack = build_visualization_pack({
        "_visual_family": "group_graph",
        "_visual_status": "usable",
        "entity_name": "A",
        "entities": [
            {
                "entity_key": "b",
                "parent_is_root": True,
                "name": "B",
                "relation": "종속기업",
                "ownership_pct": 80,
            },
            {
                "entity_key": "c",
                "parent_entity_key": "b",
                "name": "C",
                "relation": "손자회사",
                "ownership_pct": 60,
            },
        ],
    })

    table = next(item for item in pack.tables if item.id == "group_entities")
    assert table.rows == [
        {
            "row_id": "root",
            "parent_row_id": None,
            "name": "A",
            "relation": "root",
            "ownership_pct": None,
        },
        {
            "row_id": "b",
            "parent_row_id": "root",
            "name": "B",
            "relation": "종속기업",
            "ownership_pct": 80,
        },
        {
            "row_id": "c",
            "parent_row_id": "b",
            "name": "C",
            "relation": "손자회사",
            "ownership_pct": 60,
        },
    ]
    definition = pack.diagrams[0].definition
    assert 'P["A"]' in definition
    assert 'N1["B"]' in definition
    assert 'N2["C"]' in definition
    assert 'P -->|"종속기업 / 지분율 80%"| N1' in definition
    assert 'N1 -->|"손자회사 / 지분율 60%"| N2' in definition
    markdown = render_visualization_markdown(pack, mermaid=False)
    for fact in ("A", "B", "C", "종속기업", "손자회사", "80", "60", "%"):
        assert fact in markdown


@pytest.mark.parametrize("entity_count", [9, 10])
def test_direct_group_family_keeps_omission_as_a_bound_canonical_sentinel(
    entity_count,
):
    pack = build_visualization_pack({
        "_visual_family": "group_graph",
        "_visual_status": "usable",
        "entity_name": "A",
        "entities": [
            {
                "entity_key": f"child-{index}",
                "parent_is_root": True,
                "name": f"B{index}",
                "relation": "종속기업",
                "ownership_pct": 80,
            }
            for index in range(entity_count)
        ],
    })
    table = next(item for item in pack.tables if item.id == "group_entities")
    assert len(table.rows) == 10
    assert table.rows[-1] == {
        "row_id": "omitted",
        "parent_row_id": "root",
        "name": f"{entity_count - 8}개 노드는 가독성을 위해 생략",
        "relation": "omitted",
        "ownership_pct": None,
    }
    diagram = pack.diagrams[0]
    assert f"{entity_count - 8}개 노드는 가독성을 위해 생략" in (
        diagram.definition
    )
    assert set(diagram.row_refs) == {
        str(row["row_id"]) for row in table.rows
    }


def test_direct_group_all_orphan_preserves_requested_root_and_never_promotes_sentinel():
    pack = build_visualization_pack({
        "_visual_family": "group_graph",
        "_visual_status": "usable",
        "entity_name": "A",
        "entities": [{
            "entity_key": "orphan",
            "parent_entity_key": "missing-parent",
            "name": "고아 실체",
            "relation": "종속기업",
            "ownership_pct": 80,
        }],
    })

    table = next(item for item in pack.tables if item.id == "group_entities")
    assert table.rows == [{
        "row_id": "root",
        "parent_row_id": None,
        "name": "A",
        "relation": "root",
        "ownership_pct": None,
    }]
    assert pack.status == "limited"
    assert "unresolved_parent_entities:1" in pack.limitations
    diagram = pack.diagrams[0]
    assert diagram.row_refs == ["root"]
    assert 'P["A"]' in diagram.definition
    assert "고아 실체" not in diagram.definition
    assert "missing-parent" not in diagram.definition
    assert "-->" not in diagram.definition
    assert "생략" not in diagram.definition


def test_direct_group_mixed_valid_and_orphan_keeps_only_bound_edges():
    pack = build_visualization_pack({
        "_visual_family": "group_graph",
        "_visual_status": "usable",
        "entity_name": "A",
        "entities": [
            {
                "entity_key": "b",
                "parent_is_root": True,
                "name": "B",
                "relation": "종속기업",
                "ownership_pct": 80,
            },
            {
                "entity_key": "orphan",
                "parent_entity_key": "missing-parent",
                "name": "고아 실체",
                "relation": "종속기업",
                "ownership_pct": 70,
            },
        ],
    })

    table = next(item for item in pack.tables if item.id == "group_entities")
    assert [row["name"] for row in table.rows] == ["A", "B"]
    assert pack.status == "limited"
    assert "unresolved_parent_entities:1" in pack.limitations
    definition = pack.diagrams[0].definition
    assert 'P["A"]' in definition and 'N1["B"]' in definition
    assert 'P -->|"종속기업 / 지분율 80%"| N1' in definition
    assert "고아 실체" not in definition
    assert "생략" not in definition


def test_raw_dcf_family_uses_ratio_inputs_and_krw_valuation_units():
    pack = build_visualization_pack({
        "_visual_family": "dcf",
        "_visual_status": "usable",
        "projections": [{"year": 2025, "revenue": 100, "ufcf": 10}],
        "sensitivity": [{
            "wacc": 0.10,
            "terminal_growth": 0.03,
            "enterprise_value": 1_000,
            "status": "valid",
        }],
    })
    table = next(item for item in pack.tables if item.id == "dcf_sensitivity")
    units = {column.key: column.unit for column in table.columns}
    assert units == {
        "wacc": "ratio",
        "terminal_growth": "ratio",
        "enterprise_value": "KRW",
        "status": None,
    }
    markdown = render_visualization_markdown(pack, mermaid=False)
    assert "WACC (ratio)" in markdown
    assert "영구성장률 (ratio)" in markdown
    assert "기업가치 (KRW)" in markdown
    assert "0.1" in markdown and "0.03" in markdown
    assert "10%" not in markdown and "3%" not in markdown


def test_direct_peer_distribution_preserves_mixed_metric_units_per_row():
    pack = build_visualization_pack({
        "_visual_family": "peer_distribution",
        "_visual_status": "usable",
        "results": {
            2025: {
                "ROE": {
                    "subject_value": 0.12,
                    "p25": 0.05,
                    "p50": 0.10,
                    "p75": 0.15,
                    "percentile": 70,
                    "n": 30,
                    "unit": "ratio",
                },
                "감사보수": {
                    "subject_value": 1_200_000,
                    "p25": 900_000,
                    "p50": 1_100_000,
                    "p75": 1_400_000,
                    "percentile": 60,
                    "n": 30,
                    "unit": "KRW",
                },
            },
        },
    })
    table = next(item for item in pack.tables if item.id == "peer_metric_matrix")
    assert [
        (row["metric"], row["unit"])
        for row in table.rows
    ] == [("ROE", "ratio"), ("감사보수", "KRW")]
    columns = {column.key: column for column in table.columns}
    assert columns["subject_value"].label == "대상회사 값"
    assert columns["p25"].label == "Peer P25 값"
    assert columns["p50"].label == "Peer 중앙값 P50"
    assert columns["p75"].label == "Peer P75 값"
    for field in ("subject_value", "p25", "p50", "p75"):
        assert columns[field].unit is None
    assert columns["percentile"].unit == "%"
    assert columns["n"].unit == "개"

    for rendered in (
        render_visualization_markdown(pack, mermaid=False),
        render_visualization_html(pack),
    ):
        for value in ("0.12", "0.05", "1200000", "900000", "ratio", "KRW"):
            assert value in rendered


def test_direct_peer_mixed_units_suppress_quantitative_chart_with_limitation():
    pack = build_visualization_pack({
        "_visual_family": "peer_distribution",
        "_visual_status": "usable",
        "results": {
            2025: {
                "ROE": {
                    "subject_value": 0.12,
                    "p25": 0.05,
                    "p50": 0.10,
                    "p75": 0.15,
                    "percentile": 70,
                    "n": 30,
                    "unit": "ratio",
                },
                "감사보수": {
                    "subject_value": 1_200_000,
                    "p25": 900_000,
                    "p50": 1_100_000,
                    "p75": 1_400_000,
                    "percentile": 60,
                    "n": 30,
                    "unit": "KRW",
                },
            },
        },
    })

    assert not pack.charts
    assert "peer_chart_suppressed:mixed_units:KRW,ratio" in pack.limitations
    table = next(item for item in pack.tables if item.id == "peer_metric_matrix")
    assert {row["unit"] for row in table.rows} == {"ratio", "KRW"}


@pytest.mark.parametrize(
    ("unit", "subject_value"),
    [("ratio", 0.12), ("KRW", 1_200_000)],
)
def test_direct_peer_homogeneous_units_keep_one_unit_visible_chart(
    unit,
    subject_value,
):
    pack = build_visualization_pack({
        "_visual_family": "peer_distribution",
        "_visual_status": "usable",
        "results": {
            2025: {
                "metric-a": {
                    "subject_value": subject_value,
                    "p25": subject_value,
                    "p50": subject_value,
                    "p75": subject_value,
                    "percentile": 50,
                    "n": 30,
                    "unit": unit,
                },
                "metric-b": {
                    "subject_value": subject_value,
                    "p25": subject_value,
                    "p50": subject_value,
                    "p75": subject_value,
                    "percentile": 50,
                    "n": 30,
                    "unit": unit,
                },
            },
        },
    })
    assert len(pack.charts) == 1
    chart = pack.charts[0]
    assert chart.title.endswith(f"({unit})")
    assert chart.encodings.color is not None
    assert chart.encodings.color.field == "unit"
    assert not any(
        item.startswith("peer_chart_suppressed:")
        for item in pack.limitations
    )


def test_direct_peer_missing_units_suppress_quantitative_chart():
    pack = build_visualization_pack({
        "_visual_family": "peer_distribution",
        "_visual_status": "usable",
        "results": {
            2025: {
                "ROE": {
                    "subject_value": 0.12,
                    "p25": 0.05,
                    "p50": 0.10,
                    "p75": 0.15,
                    "percentile": 70,
                    "n": 30,
                },
            },
        },
    })
    assert not pack.charts
    assert "peer_chart_suppressed:missing_units" in pack.limitations


def test_direct_peer_suppresses_chart_without_numeric_encoded_facts():
    pack = build_visualization_pack({
        "_visual_family": "peer_distribution",
        "_visual_status": "usable",
        "results": {
            2025: {
                "ROE": {
                    "subject_value": None,
                    "p25": None,
                    "p50": None,
                    "p75": None,
                    "percentile": None,
                    "n": 30,
                    "unit": "ratio",
                },
            },
        },
    })

    assert not pack.charts
    assert "peer_chart_suppressed:no_numeric_facts" in pack.limitations


def test_all_direct_visual_families_keep_numeric_units_and_chart_channels_safe():
    payloads = [
        {
            "_visual_family": "financial_trend",
            "_visual_status": "usable",
            "historical_actuals": [{"year": 2024, "revenue": 100}],
        },
        {
            "_visual_family": "dcf",
            "_visual_status": "usable",
            "projections": [{"year": 2025, "revenue": 100, "ufcf": 10}],
            "sensitivity": [{
                "wacc": 0.10,
                "terminal_growth": 0.03,
                "enterprise_value": 1_000,
                "status": "valid",
            }],
        },
        {
            "_visual_family": "peer_distribution",
            "_visual_status": "usable",
            "results": {
                2025: {
                    "ROE": {
                        "subject_value": 0.12,
                        "p25": 0.05,
                        "p50": 0.10,
                        "p75": 0.15,
                        "percentile": 70,
                        "n": 30,
                        "unit": "ratio",
                    },
                },
            },
        },
        {
            "_visual_family": "group_graph",
            "_visual_status": "usable",
            "entity_name": "A",
            "entities": [{
                "name": "B",
                "relation": "종속기업",
                "ownership_pct": 80,
            }],
        },
        {
            "_visual_family": "audit_fee",
            "_visual_status": "usable",
            "history": [{
                "year": 2024,
                "audit_fee_m": 100,
                "audit_hours": 500,
            }],
        },
        {
            "_visual_family": "kam_lifecycle",
            "_visual_status": "usable",
            "events": [{"year": 2024, "topic": "수익인식", "status": "new"}],
        },
        {
            "_visual_family": "disclosure_timeline",
            "_visual_status": "usable",
            "events": [{
                "event_date": "2025-01-01",
                "corp_name": "A",
                "event_type": "capital_raise",
                "event_title": "유상증자",
                "rcept_no": "20250101000001",
            }],
        },
    ]
    per_row_unit_fields = {"subject_value", "p25", "p50", "p75"}
    dimension_fields = {"year"}

    for payload in payloads:
        pack = build_visualization_pack(payload)
        assert pack.status == "usable"
        tables = {table.id: table for table in pack.tables}
        for table in pack.tables:
            columns = {column.key: column for column in table.columns}
            for row in table.rows:
                for field, value in row.items():
                    if (
                        isinstance(value, bool)
                        or not isinstance(value, (int, float))
                        or field in dimension_fields
                    ):
                        continue
                    if field in per_row_unit_fields:
                        assert row.get("unit"), (
                            payload["_visual_family"],
                            table.id,
                            field,
                        )
                    else:
                        assert columns[field].unit, (
                            payload["_visual_family"],
                            table.id,
                            field,
                        )
        for chart in pack.charts:
            table = tables[chart.data_ref]
            columns = {column.key: column for column in table.columns}
            encodings = chart.encodings.model_dump(exclude_none=True)
            for channel_name in ("y", "color", "band"):
                channel = encodings.get(channel_name)
                if channel is None:
                    continue
                fields = (
                    [channel["field"]]
                    if channel.get("field")
                    else channel["fields"]
                )
                numeric_fields = [
                    field
                    for field in fields
                    if any(
                        isinstance(row.get(field), (int, float))
                        and not isinstance(row.get(field), bool)
                        for row in table.rows
                    )
                ]
                if not numeric_fields:
                    continue
                static_units = {
                    columns[field].unit
                    for field in numeric_fields
                }
                assert len(static_units) == 1, (
                    payload["_visual_family"],
                    chart.id,
                    channel_name,
                    static_units,
                )
                if static_units != {None}:
                    continue
                assert "unit" in columns, (
                    payload["_visual_family"],
                    chart.id,
                    channel_name,
                    "unit metadata lost",
                )
                row_units = {
                    row.get("unit")
                    for row in table.rows
                    if any(
                        row.get(field) is not None
                        for field in numeric_fields
                    )
                }
                assert len(row_units) == 1 and None not in row_units, (
                    payload["_visual_family"],
                    chart.id,
                    channel_name,
                    row_units,
                )
                assert (
                    encodings.get("color", {}).get("field") == "unit"
                ), (
                    payload["_visual_family"],
                    chart.id,
                    "row unit not visibly encoded",
                )


def test_diagram_may_only_summarize_rows_in_referenced_table():
    with pytest.raises(ValidationError, match="diagram"):
        VisualizationPackV1.model_validate(_pack(diagrams=[{
            "id": "group",
            "type": "mermaid",
            "title": "구조도",
            "table_ref": "facts",
            "definition": 'flowchart TD\n X["invented"]',
            "row_refs": ["missing-row"],
        }]))


def test_timeline_has_one_canonical_fact_source_and_rejects_a_second_event_copy():
    typed = _pack(timelines=[{
        "id": "timeline",
        "title": "이벤트",
        "table_ref": "facts",
        "events": [{"year": 2025, "value": 999}],
    }])
    with pytest.raises(ValidationError, match="events"):
        VisualizationPackV1.model_validate(typed)

    legacy = build_visualization_pack({
        **typed,
        "kind": "answer_pack",
    })
    assert legacy.timelines[0].table_ref == "facts"
    assert not hasattr(legacy.timelines[0], "events")
    assert legacy.tables[0].rows == [{"year": 2024, "value": 100}]


def test_rich_resource_uri_is_content_bound_and_html_only_uses_validated_pack():
    from kreports.mcp.resources import render_visualization_resource

    with pytest.raises(ValidationError, match="content digest"):
        VisualizationPackV1.model_validate({
            **_pack(),
            "resource_uri": f"kreports://visualization/{'0' * 64}",
        })

    pack = VisualizationPackV1.model_validate(_pack())
    resource = render_visualization_resource(pack)

    assert resource["uri"] == pack.resource_uri
    assert resource["mimeType"] == "text/html; charset=utf-8"
    assert "<table>" in resource["text"]


def test_mutated_model_instances_are_revalidated_at_every_render_and_publish_boundary():
    from kreports.mcp.resources import publish_visualization_resource

    def valid_pack():
        return VisualizationPackV1.model_validate(_pack(sources=[{
            "label": "DART 공시",
            "rcept_no": "20250101000001",
            "url": (
                "https://dart.fss.or.kr/dsaf001/main.do?"
                "rcpNo=20250101000001"
            ),
        }]))

    poisoned = []
    pack = valid_pack()
    pack.resource_uri = None
    pack.tables[0].rows[0]["undeclared"] = 1
    poisoned.append(pack)
    pack = valid_pack()
    pack.resource_uri = None
    pack.tables[0].rows[0]["value"] = "poison\x00"
    poisoned.append(pack)
    pack = valid_pack()
    pack.resource_uri = None
    pack.sources[0].url = "javascript:alert(1)"
    poisoned.append(pack)
    pack = valid_pack()
    pack.resource_uri = None
    pack.sources[0].label = "poison\x00"
    poisoned.append(pack)

    for pack in poisoned:
        for boundary in (
            lambda: render_visualization_markdown(pack, mermaid=False),
            lambda: render_visualization_html(pack),
            lambda: publish_visualization_resource(pack),
        ):
            with pytest.raises(ValidationError):
                boundary()


def test_poisoned_visual_resource_is_removed_without_cache_accounting_drift():
    from kreports.mcp import resources

    resources._clear_visualization_resources_for_test()
    pack = VisualizationPackV1.model_validate(_pack())
    uri = resources.publish_visualization_resource(pack)
    digest = uri.rsplit("/", 1)[-1]
    assert resources._VISUALIZATION_CACHE_BYTES == sum(
        int(entry["size"])
        for entry in resources._VISUALIZATION_RESOURCES.values()
    )

    resources._VISUALIZATION_RESOURCES[digest]["pack"]["tables"][0]["rows"][0][
        "value"
    ] = "poison\x00"
    with pytest.raises(
        resources.ResourceRequestError,
        match="visualization_resource_unavailable",
    ):
        resources.read_resource(uri)
    assert not resources._VISUALIZATION_RESOURCES
    assert resources._VISUALIZATION_CACHE_BYTES == 0

    for _ in range(3):
        with pytest.raises(
            resources.ResourceRequestError,
            match="visualization_resource_unavailable",
        ):
            resources.read_resource(uri)
        assert resources._VISUALIZATION_CACHE_BYTES == sum(
            int(entry["size"])
            for entry in resources._VISUALIZATION_RESOURCES.values()
        )
    resources._clear_visualization_resources_for_test()
    assert resources._VISUALIZATION_CACHE_BYTES == 0


def test_published_visual_resource_is_fetchable_through_actual_mcp_read_path():
    import asyncio

    from kreports.mcp.answer_pack import build_answer_pack
    from kreports.mcp.resources import (
        ResourceRequestError,
        list_resource_templates,
        read_resource,
    )
    from kreports.mcp.server import handle_read_resource

    raw = build_answer_pack("get_kam_lifecycle", {
        "subject": {"corp_name": "A"},
        "events": [{"year": 2024, "topic": "수익인식", "status": "new"}],
        "data_quality": {"status": "usable"},
    })
    pack = VisualizationPackV1.model_validate(raw)
    assert any(
        item.uri_template == "kreports://visualization/{digest}"
        for item in list_resource_templates()
    )

    payload = read_resource(pack.resource_uri)
    assert payload["uri"] == pack.resource_uri
    assert payload["mimeType"] == "text/html; charset=utf-8"
    assert "<table>" in payload["text"]
    stdio = asyncio.run(handle_read_resource(pack.resource_uri))
    assert stdio[0].mime_type == "text/html; charset=utf-8"
    assert stdio[0].content == payload["text"]

    unknown = f"kreports://visualization/{'f' * 64}"
    with pytest.raises(
        ResourceRequestError,
        match="visualization_resource_unavailable",
    ):
        read_resource(unknown)


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
        ("SUSTAINABILITY", "PENDING", "기타 핵심감사사항", "상태 미분류"),
    ],
)
def test_raw_kam_lifecycle_visual_fallback_maps_public_labels(
    raw_topic,
    raw_status,
    expected_topic,
    expected_status,
):
    pack = build_visualization_pack({
        "_visual_family": "kam_lifecycle",
        "entity_name": "A",
        "events": [
            {
                "year": 2025,
                "topic": raw_topic,
                "status": raw_status,
            },
            {
                "year": 2024,
                "topic": "materiality",
                "status": "stable",
            },
        ],
    })

    row = pack.tables[0].rows[0]
    assert row["topic"] == expected_topic
    assert row["status"] == expected_status
    assert pack.tables[0].rows[1]["topic"] == "materiality"
    assert pack.tables[0].rows[1]["status"] == "stable"
    html = render_visualization_html(pack)
    assert expected_topic in html
    assert expected_status in html
    assert raw_topic.strip() not in html
    assert raw_status.strip() not in html


def test_visual_resource_lru_eviction_and_restart_are_stable_fail_closed():
    from kreports.mcp import resources
    from kreports.mcp.answer_pack import build_answer_pack

    resources._clear_visualization_resources_for_test()
    uris = []
    for index in range(resources.MAX_VISUALIZATION_RESOURCES + 1):
        raw = build_answer_pack("get_kam_lifecycle", {
            "subject": {"corp_name": f"A{index}"},
            "events": [{"year": 2024, "topic": f"topic-{index}", "status": "new"}],
            "data_quality": {"status": "usable"},
        })
        uris.append(raw["resource_uri"])

    with pytest.raises(
        resources.ResourceRequestError,
        match="visualization_resource_unavailable",
    ):
        resources.read_resource(uris[0])
    assert resources.read_resource(uris[-1])["uri"] == uris[-1]

    resources._clear_visualization_resources_for_test()
    with pytest.raises(
        resources.ResourceRequestError,
        match="visualization_resource_unavailable",
    ):
        resources.read_resource(uris[-1])


def test_legacy_mermaid_is_regenerated_from_fully_bound_canonical_rows():
    attack = (
        "%%{init: {'themeCSS':'url(https://evil)'}}%%\n"
        "flowchart TD\nclick X \"javascript:alert(1)\"\n"
        "classDef bad fill:url(https://evil)\n"
        "X[\"<script>alert(1)</script>\"]"
    )
    pack = build_visualization_pack({
        "kind": "answer_pack",
        "summary": {
            "title": "A 연결실체 구조",
            "status": "usable",
            "subject": "A",
        },
        "tables": [{
            "id": "subsidiary_contribution",
            "title": "연결실체",
            "columns": [
                {"field": "name", "label": "회사"},
                {"field": "relation", "label": "관계"},
            ],
            "rows": [{"name": "B", "relation": "종속"}],
        }],
        "charts": [],
        "diagrams": [{
            "id": "subsidiary_structure",
            "type": "mermaid",
            "title": "구조",
            "definition": attack,
        }],
        "timelines": [],
        "data_quality": {"status": "usable"},
    })

    diagram = pack.diagrams[0]
    table = next(item for item in pack.tables if item.id == diagram.table_ref)
    assert diagram.row_refs
    assert set(diagram.row_refs) == {
        str(row["row_id"]) for row in table.rows
    }
    assert "A" in diagram.definition and "B" in diagram.definition
    assert not re.search(
        r"(?i)click|https?://|javascript:|%%\{|classdef|linkstyle|"
        r"<script|<style|<svg|on\\w+=",
        diagram.definition,
    )
    assert attack not in diagram.definition


def test_diagram_cannot_use_complete_refs_with_an_invented_definition():
    with pytest.raises(ValidationError, match="canonical table"):
        VisualizationPackV1.model_validate(_pack(diagrams=[{
            "id": "group",
            "type": "mermaid",
            "title": "구조",
            "table_ref": "facts",
            "definition": 'flowchart TD\n X["invented"]',
            "row_refs": ["0"],
        }]))


@pytest.mark.parametrize(
    "family",
    [
        "financial_trend",
        "dcf",
        "peer_distribution",
        "group_graph",
        "audit_fee",
        "kam_lifecycle",
        "disclosure_timeline",
    ],
)
def test_all_families_fail_closed_when_rows_are_empty_or_placeholder(family):
    pack = build_visualization_pack({
        "_visual_family": family,
        "_visual_status": "usable",
        "historical_actuals": [{}],
        "projections": [{}],
        "sensitivity": [{}],
        "results": {},
        "entities": [{}],
        "history": [{}],
        "events": [{}],
    })

    assert pack.status in {"limited", "missing"}
    assert pack.limitations
    assert all(table.status in {"limited", "missing"} for table in pack.tables)
    assert all(row for table in pack.tables for row in table.rows)
    assert not pack.charts
    assert not pack.diagrams
    assert not pack.timelines


def test_domain_status_is_separate_from_visual_data_quality_status():
    pack = build_visualization_pack({
        "kind": "answer_pack",
        "summary": {
            "title": "DCF",
            "status": "complete_model",
            "subject": "A",
        },
        "tables": [_table()],
        "charts": [],
        "diagrams": [],
        "timelines": [],
        "data_quality": {"status": "usable"},
    })

    assert pack.status == "usable"
    assert pack.summary.status == "usable"
    assert pack.summary.domain_status == "complete_model"
    assert pack.data_quality.status == "usable"
    assert pack.tables[0].status == "usable"


def test_pack_rejects_incoherent_summary_quality_and_table_statuses():
    with pytest.raises(ValidationError, match="status"):
        VisualizationPackV1.model_validate({
            **_pack(),
            "summary": {
                "title": "시각화",
                "status": "limited",
                "subject": "A",
            },
            "data_quality": {"status": "usable"},
        })
    with pytest.raises(ValidationError, match="table status"):
        VisualizationPackV1.model_validate({
            **_pack(),
            "tables": [_table(status="limited")],
            "data_quality": {"status": "usable"},
        })


def test_ratio_units_and_quality_metadata_have_markdown_html_fact_parity():
    pack = build_visualization_pack({
        "kind": "answer_pack",
        "summary": {"title": "Peer", "status": "limited", "subject": "A"},
        "tables": [{
            "id": "peer_metric_matrix",
            "title": "Peer",
            "columns": [
                {"field": "metric", "label": "지표"},
                {"field": "subject_value", "label": "대상회사", "unit": "ratio"},
                {"field": "unit", "label": "단위"},
            ],
            "rows": [{
                "metric": "ROE",
                "subject_value": 0.10,
                "unit": "ratio",
            }],
        }],
        "charts": [],
        "diagrams": [],
        "timelines": [],
        "sources": [{
            "label": "DART 공시",
            "rcept_no": "20250101000001",
            "url": (
                "https://dart.fss.or.kr/dsaf001/main.do?"
                "rcpNo=20250101000001"
            ),
        }],
        "data_quality": {
            "status": "limited",
            "source": "financial_facts_compact",
        },
        "limitations": ["표본 제한"],
        "warnings": ["검토 필요"],
    })
    markdown = render_visualization_markdown(pack, mermaid=False)
    rich = render_visualization_html(pack)

    for rendered in (markdown, rich):
        assert "0.1" in rendered
        assert "ratio" in rendered
        assert "10%" not in rendered
        assert "limited" in rendered
        assert "재무 공시 캐시" in rendered
        assert "financial_facts_compact" not in rendered
        assert "DART 공시" in rendered
        assert "20250101000001" in rendered
        assert "표본 제한" in rendered
        assert "검토 필요" in rendered


def test_internal_source_table_provenance_stays_structured_but_renders_public_labels():
    internal_sources = {
        "accounting_note_chapters": "회계정책 주석 캐시",
        "audit_matter_items": "감사보고서 항목 캐시",
        "audit_procedure_items": "감사절차 항목 캐시",
        "evidence_documents": "공시 근거 캐시",
        "financial_facts_compact": "재무 공시 캐시",
        "local_subsidiary_auditor_matrix": "연결실체 감사인 캐시",
        "report_sections": "보고서 섹션 캐시",
        "report_sections.audit_report": "감사보고서 캐시",
        "source_documents": "원문 문서 캐시",
    }
    rows = [
        {
            "source_table": internal,
            "rcept_no": f"20250101{index:06d}",
        }
        for index, internal in enumerate(internal_sources, start=1)
    ]
    pack = VisualizationPackV1.model_validate({
        "tables": [{
            "id": "provenance",
            "title": "원천 근거",
            "columns": [
                {"field": "source_table", "label": "원천 테이블"},
                {"field": "rcept_no", "label": "접수번호"},
            ],
            "rows": rows,
        }],
        "charts": [],
        "diagrams": [],
        "sources": [{
            "label": "DART 공시",
            "rcept_no": "20250101000001",
            "url": (
                "https://dart.fss.or.kr/dsaf001/main.do?"
                "rcpNo=20250101000001"
            ),
        }],
        "data_quality": {
            "status": "usable",
            "source": "financial_facts_compact",
        },
        "status": "usable",
    })
    structured = pack.model_dump(mode="json")
    assert [
        row["source_table"]
        for row in structured["tables"][0]["rows"]
    ] == list(internal_sources)

    for rendered in (
        render_visualization_markdown(pack, mermaid=False),
        render_visualization_html(pack),
    ):
        for internal, public in internal_sources.items():
            assert internal not in rendered
            assert public in rendered
        for row in rows:
            assert row["rcept_no"] in rendered
        assert "20250101000001" in rendered


def test_render_answer_contains_invalid_external_answer_pack():
    from kreports.mcp.renderers import render_answer

    text = render_answer("get_kam_lifecycle", {
        "subject": {"corp_name": "A"},
        "events": [{"year": 2024, "topic": "수익인식", "status": "new"}],
        "data_quality": {"status": "usable"},
        "answer_pack": {
            "kind": "answer_pack",
            "version": "attacker.v9",
            "tables": "not-a-list",
        },
    })

    assert text is not None
    assert "수익인식" in text
    assert "ValidationError" not in text
