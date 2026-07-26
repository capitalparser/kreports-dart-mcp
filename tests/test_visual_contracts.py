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
