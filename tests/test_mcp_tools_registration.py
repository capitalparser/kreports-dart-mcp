"""MCP tools registration smoke tests for compare_to_industry_multi /
get_industry_audit_landscape (Task 8 of peer-tier-s)."""
from __future__ import annotations

import json

from kreports.mcp.tools import ALL_TOOLS, HANDLERS, call_tool


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


def test_search_dataset_registered():
    names = [t.name for t in ALL_TOOLS]
    assert "search_dataset" in names
    assert "search_dataset" in HANDLERS


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
