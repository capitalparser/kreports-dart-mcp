"""MCP tools registration smoke tests for compare_to_industry_multi /
get_industry_audit_landscape (Task 8 of peer-tier-s)."""
from __future__ import annotations

import json

from kreports.mcp.tools import ALL_TOOLS, HANDLERS, call_tool


def test_compatibility_exports_are_generated_from_the_single_catalog():
    from kreports.mcp.catalog import TOOL_CATALOG
    from kreports.mcp.dispatch import list_mcp_tools

    assert ALL_TOOLS == list_mcp_tools()
    assert list(HANDLERS) == list(TOOL_CATALOG)


def test_compare_to_industry_multi_in_all_tools():
    names = [t.name for t in ALL_TOOLS]
    assert "compare_to_industry_multi" in names


def test_compare_to_industry_multi_in_handlers():
    assert "compare_to_industry_multi" in HANDLERS


def test_get_industry_audit_landscape_in_all_tools():
    names = [t.name for t in ALL_TOOLS]
    assert "get_industry_audit_landscape" in names


def test_get_industry_audit_landscape_in_handlers():
    assert "get_industry_audit_landscape" in HANDLERS


def test_compare_peer_audit_report_matters_registered():
    names = [t.name for t in ALL_TOOLS]
    assert "compare_peer_audit_report_matters" in names
    assert "compare_peer_audit_report_matters" in HANDLERS


def test_search_audit_report_matters_registered():
    names = [t.name for t in ALL_TOOLS]
    assert "search_audit_report_matters" in names
    assert "search_audit_report_matters" in HANDLERS


def test_get_kam_lifecycle_registered():
    names = [t.name for t in ALL_TOOLS]
    assert "get_kam_lifecycle" in names
    assert "get_kam_lifecycle" in HANDLERS


def test_get_accounting_policy_changes_registered():
    names = [t.name for t in ALL_TOOLS]
    assert "get_accounting_policy_changes" in names
    assert "get_accounting_policy_changes" in HANDLERS


def test_get_quality_of_earnings_pack_registered():
    names = [t.name for t in ALL_TOOLS]
    assert "get_quality_of_earnings_pack" in names
    assert "get_quality_of_earnings_pack" in HANDLERS


def test_get_dcf_input_candidates_registered():
    names = [t.name for t in ALL_TOOLS]
    assert "get_dcf_input_candidates" in names
    assert "get_dcf_input_candidates" in HANDLERS


def test_search_disclosure_events_registered():
    names = [t.name for t in ALL_TOOLS]
    assert "search_disclosure_events" in names
    assert "search_disclosure_events" in HANDLERS


def test_search_dataset_registered():
    names = [t.name for t in ALL_TOOLS]
    assert "search_dataset" in names
    assert "search_dataset" in HANDLERS


def test_search_dataset_schema_exposes_evidence_documents():
    tool = next(t for t in ALL_TOOLS if t.name == "search_dataset")
    assert "evidence_documents" in tool.inputSchema["properties"]["dataset"]["enum"]


def test_fetch_disclosure_on_demand_registered():
    names = [t.name for t in ALL_TOOLS]
    assert "fetch_disclosure_on_demand" in names
    assert "fetch_disclosure_on_demand" in HANDLERS


def test_compare_to_industry_multi_call_smoke():
    """smoke: dispatch by name, expect JSON-serializable result."""
    out_str = call_tool(
        "compare_to_industry_multi",
        {"company": "005930", "metrics": ["ROE"], "years_back": 2},
    )
    out = json.loads(out_str)
    assert "subject" in out or "error" in out


def test_get_industry_audit_landscape_call_smoke():
    out_str = call_tool("get_industry_audit_landscape", {"company": "005930"})
    out = json.loads(out_str)
    assert "subject" in out or "error" in out


def test_get_industry_audit_landscape_needs_company_or_induty():
    out_str = call_tool("get_industry_audit_landscape", {})
    out = json.loads(out_str)
    assert "error" in out
