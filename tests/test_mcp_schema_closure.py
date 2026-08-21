from __future__ import annotations


def test_all_public_mcp_schemas_are_self_contained():
    from kreports.mcp.dispatch import list_mcp_tools
    from kreports.mcp.schema_utils import (
        find_json_schema_refs,
    )

    tools = list_mcp_tools()

    assert len(tools) == 34
    for tool in tools:
        assert "$defs" not in tool.inputSchema
        assert find_json_schema_refs(tool.inputSchema) == []


def test_nested_peer_profile_is_visible_on_wire():
    from kreports.mcp.dispatch import list_mcp_tools

    tools = {
        tool.name: tool
        for tool in list_mcp_tools()
    }
    schema = tools[
        "compare_to_industry_multi"
    ].inputSchema
    peer_criteria = schema["properties"]["peer_criteria"]
    serialized = str(peer_criteria)

    for field in (
        "mode",
        "industry_basis",
        "prefix_len",
        "size_metric",
        "required_features",
        "weights",
    ):
        assert field in serialized

    assert "peer_limit" in schema["properties"]


def test_note_search_and_fs_policy_fields_are_visible_on_wire():
    from kreports.mcp.dispatch import list_mcp_tools

    tools = {
        tool.name: tool
        for tool in list_mcp_tools()
    }
    search_properties = tools[
        "search_dataset"
    ].inputSchema["properties"]
    note_properties = tools[
        "compare_peer_accounting_notes"
    ].inputSchema["properties"]

    assert {
        "offset",
        "search_mode",
        "synonyms",
    }.issubset(search_properties)
    assert "fs_basis_policy" in note_properties
