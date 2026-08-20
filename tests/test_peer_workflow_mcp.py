from __future__ import annotations


def test_catalog_extensions_preserve_tool_count_and_add_peer_fields():
    from kreports.mcp.catalog import TOOL_CATALOG
    from kreports.mcp.catalog_extensions import install_catalog_extensions

    original_count = len(TOOL_CATALOG)
    install_catalog_extensions()

    assert len(TOOL_CATALOG) == original_count == 34
    assert "year" in TOOL_CATALOG["select_peer_group"].input_model.model_fields
    assert "year" in TOOL_CATALOG["compare_to_industry_multi"].input_model.model_fields
    assert "peer_criteria" in TOOL_CATALOG["compare_to_industry_multi"].input_model.model_fields
    for name in (
        "compare_peer_audit_fees",
        "compare_peer_risk_profile",
        "compare_peer_accounting_policies",
        "compare_peer_kam_topics",
        "compare_peer_audit_report_matters",
        "compare_peer_audit_procedures",
    ):
        assert "peer_criteria" in TOOL_CATALOG[name].input_model.model_fields


def test_search_dataset_routes_note_keyword_to_company_search(monkeypatch):
    from kreports.mcp.handlers import search as handlers
    from kreports.mcp.input_models import SearchDatasetInput

    captured = {}

    def fake_search(keyword, **kwargs):
        captured["keyword"] = keyword
        captured.update(kwargs)
        return {"total_companies": 1, "companies": [{"corp_code": "00000001"}]}

    monkeypatch.setattr(handlers, "search_note_disclosing_companies", fake_search)

    args = SearchDatasetInput(
        dataset="accounting_note_chapters",
        keyword="자금보충약정",
        year=2024,
        market="KOSPI",
    )
    out = handlers.handle_search_dataset(args)

    assert captured["keyword"] == "자금보충약정"
    assert captured["year"] == 2024
    assert out["total_companies"] == 1


def test_compare_to_industry_multi_routes_custom_profile(monkeypatch):
    from kreports.mcp.catalog import TOOL_CATALOG
    from kreports.mcp.catalog_extensions import install_catalog_extensions
    from kreports.mcp.handlers import search as handlers

    install_catalog_extensions()
    model = TOOL_CATALOG["compare_to_industry_multi"].input_model
    args = model(
        company="00000001",
        year=2024,
        peer_criteria={"mode": "strict", "prefix_len": 3},
        metrics=["영업이익률"],
    )
    monkeypatch.setattr(handlers, "resolve_company", lambda value: value)
    captured = {}

    def fake_compare(**kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(handlers, "compare_custom_peer_financials", fake_compare)
    out = handlers.handle_compare_to_industry_multi(args)

    assert out == {"ok": True}
    assert captured["year"] == 2024
    assert captured["peer_criteria"].mode == "strict"
    assert captured["metrics"] == ["영업이익률"]


def test_custom_peer_handler_builds_one_requested_group(monkeypatch):
    from kreports.mcp.catalog import TOOL_CATALOG
    from kreports.mcp.catalog_extensions import install_catalog_extensions
    from kreports.mcp.handlers import auditor

    install_catalog_extensions()
    model = TOOL_CATALOG["compare_peer_risk_profile"].input_model
    args = model(
        company="00000001",
        year=2024,
        peer_criteria={"mode": "strict", "prefix_len": 3},
    )

    group = {
        "subject": {"corp_code": "00000001"},
        "selection_policy": {"fs_div_used": "CFS"},
        "peers": [{"corp_code": "00000002"}],
    }
    monkeypatch.setattr(auditor, "resolve_company", lambda value: value)
    monkeypatch.setattr(auditor, "select_peer_group", lambda **kwargs: group)
    monkeypatch.setattr(
        auditor,
        "compare_peer_risk_profile",
        lambda **kwargs: {"peer_group": kwargs.get("_peer_group")},
    )

    out = auditor.handle_compare_peer_risk_profile(args)
    assert out["peer_group"] is group
