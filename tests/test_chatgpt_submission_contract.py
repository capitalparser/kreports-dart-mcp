"""Public ChatGPT Apps catalog must be read-only and credential-free."""
from __future__ import annotations

import json
from pathlib import Path


def test_public_catalog_has_33_explicitly_annotated_output_schemas(monkeypatch):
    from kreports.mcp.dispatch import list_mcp_tools

    monkeypatch.delenv("KREPORTS_MCP_ENABLE_CREDENTIAL_TOOLS", raising=False)
    tools = list_mcp_tools()

    assert len(tools) == 33
    assert "fetch_disclosure_on_demand" not in {tool.name for tool in tools}
    for tool in tools:
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.openWorldHint is False
        assert tool.annotations.destructiveHint is False
        assert tool.outputSchema is not None
        assert tool.outputSchema["type"] == "object"
        assert "answer" in tool.outputSchema["properties"]


def test_public_descriptors_do_not_recommend_the_hidden_credential_tool(monkeypatch):
    from kreports.mcp.dispatch import list_mcp_tools

    monkeypatch.delenv("KREPORTS_MCP_ENABLE_CREDENTIAL_TOOLS", raising=False)

    assert all(
        "fetch_disclosure_on_demand" not in tool.description
        for tool in list_mcp_tools()
    )


def test_hidden_credential_tool_cannot_be_called_by_name(monkeypatch):
    from dataclasses import replace

    from kreports.mcp.catalog import TOOL_CATALOG
    from kreports.mcp.dispatch import dispatch_tool

    monkeypatch.delenv("KREPORTS_MCP_ENABLE_CREDENTIAL_TOOLS", raising=False)
    called = False
    original = TOOL_CATALOG["fetch_disclosure_on_demand"]

    def forbidden(_validated):
        nonlocal called
        called = True
        return {"unexpected": True}

    monkeypatch.setitem(
        TOOL_CATALOG,
        "fetch_disclosure_on_demand",
        replace(original, handler=forbidden),
    )
    result = dispatch_tool(
        "fetch_disclosure_on_demand",
        {"rcept_no": "20250101000001", "user_dart_api_key": "secret"},
    )

    assert called is False
    assert result.verdict == "error"
    assert "Unknown tool" in result.answer


def test_credential_tool_requires_explicit_operator_opt_in(monkeypatch):
    from kreports.mcp.dispatch import dispatch_tool, list_mcp_tools

    monkeypatch.setenv("KREPORTS_MCP_ENABLE_CREDENTIAL_TOOLS", "1")

    assert "fetch_disclosure_on_demand" in {
        tool.name for tool in list_mcp_tools()
    }
    result = dispatch_tool(
        "fetch_disclosure_on_demand",
        {"rcept_no": "20250101000001"},
    )
    assert result.tool_name == "fetch_disclosure_on_demand"
    assert "user_dart_api_key is required" in result.data_quality.limitations


def test_submission_json_covers_exact_public_catalog_and_review_cases(monkeypatch):
    from kreports.mcp.dispatch import list_mcp_tools

    monkeypatch.delenv("KREPORTS_MCP_ENABLE_CREDENTIAL_TOOLS", raising=False)
    payload = json.loads(
        Path("chatgpt-app-submission.json").read_text(encoding="utf-8")
    )
    public_names = {tool.name for tool in list_mcp_tools()}

    assert payload["schema_version"] == 1
    assert payload["app_info"]["display_name"] == "KReports"
    assert payload["app_info"]["category"] == "FINANCE"
    assert len(payload["app_info"]["subtitle"]) <= 30
    assert set(payload["tools"]) == public_names
    assert len(payload["test_cases"]) == 5
    assert len(payload["negative_test_cases"]) == 3
    assert all(
        case["tools_triggered"] in public_names
        for case in payload["test_cases"]
    )
    assert all(
        case["tools_triggered"] is None
        for case in payload["negative_test_cases"]
    )
    for entry in payload["tools"].values():
        assert entry["annotations"] == {
            "readOnlyHint": True,
            "openWorldHint": False,
            "destructiveHint": False,
        }
        assert all(
            len(value.splitlines()) == 1
            for value in entry["justifications"].values()
        )
