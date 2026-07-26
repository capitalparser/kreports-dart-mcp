"""Contract tests for the single typed MCP catalog."""
from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from pydantic import SecretStr

from kreports.mcp.contracts import AnswerEnvelopeV1


EXPECTED_TOOL_NAMES = [
    "search_company",
    "get_financial_snapshot",
    "score_going_concern",
    "detect_restatement",
    "get_accounting_policy",
    "get_audit_history",
    "get_subsidiary_auditors",
    "compare_to_industry",
    "get_business_overview",
    "get_investor_signals",
    "select_peer_group",
    "compare_to_industry_multi",
    "compare_peer_audit_fees",
    "compare_peer_risk_profile",
    "compare_peer_accounting_policies",
    "compare_peer_kam_topics",
    "compare_peer_audit_report_matters",
    "search_dataset",
    "fetch_disclosure_on_demand",
    "search_audit_report_matters",
    "search_audit_procedures",
    "compare_peer_audit_procedures",
    "get_kam_lifecycle",
    "get_accounting_policy_changes",
    "get_quality_of_earnings_pack",
    "get_dcf_input_candidates",
    "search_disclosure_events",
    "get_audit_report_sections",
    "estimate_audit_hours_proxy",
    "build_audit_acceptance_pack",
    "get_industry_audit_landscape",
]


MINIMAL_ARGUMENTS = {
    "search_company": {"query": "__task7_no_such_company__"},
    "get_financial_snapshot": {"company": "__task7_no_such_company__"},
    "score_going_concern": {"company": "__task7_no_such_company__"},
    "detect_restatement": {"company": "__task7_no_such_company__"},
    "get_accounting_policy": {
        "company": "__task7_no_such_company__",
        "bsns_year": 2025,
    },
    "get_audit_history": {"company": "__task7_no_such_company__"},
    "get_subsidiary_auditors": {"company": "__task7_no_such_company__"},
    "compare_to_industry": {},
    "get_business_overview": {"company": "__task7_no_such_company__"},
    "get_investor_signals": {"company": "__task7_no_such_company__"},
    "select_peer_group": {"company": "__task7_no_such_company__"},
    "compare_to_industry_multi": {"company": "__task7_no_such_company__"},
    "compare_peer_audit_fees": {"company": "__task7_no_such_company__"},
    "compare_peer_risk_profile": {"company": "__task7_no_such_company__"},
    "compare_peer_accounting_policies": {"company": "__task7_no_such_company__"},
    "compare_peer_kam_topics": {"company": "__task7_no_such_company__"},
    "compare_peer_audit_report_matters": {"company": "__task7_no_such_company__"},
    "search_dataset": {"dataset": "financials", "company": "__task7_no_such_company__"},
    "fetch_disclosure_on_demand": {"rcept_no": "__task7_missing_receipt__"},
    "search_audit_report_matters": {"company": "__task7_no_such_company__"},
    "search_audit_procedures": {"company": "__task7_no_such_company__"},
    "compare_peer_audit_procedures": {"company": "__task7_no_such_company__"},
    "get_kam_lifecycle": {"company": "__task7_no_such_company__"},
    "get_accounting_policy_changes": {"company": "__task7_no_such_company__"},
    "get_quality_of_earnings_pack": {"company": "__task7_no_such_company__"},
    "get_dcf_input_candidates": {"company": "__task7_no_such_company__"},
    "search_disclosure_events": {"company": "__task7_no_such_company__"},
    "get_audit_report_sections": {"company": "__task7_no_such_company__"},
    "estimate_audit_hours_proxy": {"company": "__task7_no_such_company__"},
    "build_audit_acceptance_pack": {"company": "__task7_no_such_company__"},
    "get_industry_audit_landscape": {"induty_code": "__task7_no_such_industry__"},
}


def test_catalog_is_complete_ordered_and_immutable():
    from kreports.mcp.catalog import TOOL_CATALOG
    from kreports.mcp.dispatch import list_mcp_tools

    assert list(TOOL_CATALOG) == EXPECTED_TOOL_NAMES
    assert [tool.name for tool in list_mcp_tools()] == EXPECTED_TOOL_NAMES
    assert len({id(spec.input_model) for spec in TOOL_CATALOG.values()}) == 31
    assert all(
        spec.input_model.model_config.get("extra") == "forbid"
        for spec in TOOL_CATALOG.values()
    )
    with pytest.raises(FrozenInstanceError):
        TOOL_CATALOG["search_company"].name = "changed"


def test_user_api_key_is_secret_and_not_disclosed():
    from kreports.mcp.input_models import FetchDisclosureOnDemandInput

    raw_secret = "task7-super-secret-key"
    model = FetchDisclosureOnDemandInput(
        rcept_no="20250711000001",
        user_dart_api_key=raw_secret,
    )
    assert isinstance(model.user_dart_api_key, SecretStr)
    assert raw_secret not in repr(model)
    assert raw_secret not in str(model)
    assert model.user_dart_api_key.get_secret_value() == raw_secret


def test_user_api_key_is_redacted_even_when_fetch_dependency_echoes_it(monkeypatch):
    from dataclasses import replace

    from kreports.mcp.catalog import TOOL_CATALOG
    from kreports.mcp.dispatch import dispatch_tool

    raw_secret = "task7-dependency-echo-secret"
    original = TOOL_CATALOG["fetch_disclosure_on_demand"]

    def echoing_failure(_args):
        raise RuntimeError(f"upstream rejected {raw_secret}")

    monkeypatch.setitem(
        TOOL_CATALOG,
        "fetch_disclosure_on_demand",
        replace(original, handler=echoing_failure),
    )
    result = dispatch_tool(
        "fetch_disclosure_on_demand",
        {"rcept_no": "20250711000001", "user_dart_api_key": raw_secret},
    )
    serialized = result.model_dump_json()
    assert raw_secret not in serialized
    assert "[REDACTED]" in serialized


def test_extra_and_invalid_arguments_return_bounded_validation_envelope():
    from kreports.mcp.dispatch import dispatch_tool

    extra = dispatch_tool(
        "search_company",
        {"query": "삼성전자", "unexpected": "task7-super-secret-key"},
    )
    invalid = dispatch_tool("search_company", {"query": "삼성전자", "limit": 0})
    for result in (extra, invalid):
        assert isinstance(result, AnswerEnvelopeV1)
        assert result.data_quality.status == "error"
        assert "Traceback" not in result.answer
        assert len(result.answer) <= 500
        assert "task7-super-secret-key" not in result.model_dump_json()


def test_unknown_tool_returns_bounded_error_envelope():
    from kreports.mcp.dispatch import dispatch_tool

    result = dispatch_tool("does_not_exist", {})
    assert isinstance(result, AnswerEnvelopeV1)
    assert result.data_quality.status == "error"
    assert "Unknown tool" in result.answer
    assert len(result.answer) <= 500


@pytest.mark.parametrize("tool_name", EXPECTED_TOOL_NAMES)
def test_all_tools_accept_their_minimal_interface(tool_name):
    from kreports.mcp.dispatch import dispatch_tool

    result = dispatch_tool(tool_name, MINIMAL_ARGUMENTS[tool_name])
    assert isinstance(result, AnswerEnvelopeV1)
    assert result.tool_name == tool_name
    assert result.data_quality.status in {"usable", "limited", "missing", "error"}
