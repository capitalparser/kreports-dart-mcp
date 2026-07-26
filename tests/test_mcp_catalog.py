"""Contract tests for the single typed MCP catalog."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
import hashlib
import json

import pytest
from pydantic import SecretStr, TypeAdapter, ValidationError

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
    "build_dcf_model_pack",
]
EXPECTED_INTERFACE_SHA256 = "f72fa64c26aada05aecb18c45ea6f0a6484073c69acc5300ba3bb07d5f1e55f1"


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
    "build_dcf_model_pack": {
        "company": "__task7_no_such_company__",
        "base_year": 2025,
    },
}


def test_catalog_is_complete_ordered_and_immutable():
    from kreports.mcp.catalog import TOOL_CATALOG
    from kreports.mcp.dispatch import list_mcp_tools

    assert list(TOOL_CATALOG) == EXPECTED_TOOL_NAMES
    assert [tool.name for tool in list_mcp_tools()] == EXPECTED_TOOL_NAMES
    assert len({id(spec.input_model) for spec in TOOL_CATALOG.values()}) == 32
    assert all(
        spec.input_model.model_config.get("extra") == "forbid"
        for spec in TOOL_CATALOG.values()
    )
    with pytest.raises(FrozenInstanceError):
        TOOL_CATALOG["search_company"].name = "changed"


def test_generated_tool_interface_keeps_the_approved_32_tool_snapshot_hash():
    from kreports.mcp.dispatch import list_mcp_tools

    snapshot = []
    for tool in list_mcp_tools():
        snapshot.append(
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.inputSchema,
                "annotations": (
                    tool.annotations.model_dump(mode="json", exclude_none=False)
                    if tool.annotations
                    else None
                ),
            }
        )
    payload = (
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()
    assert hashlib.sha256(payload).hexdigest() == EXPECTED_INTERFACE_SHA256


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


@pytest.mark.parametrize("input_type", ["raw", "secret_str"])
@pytest.mark.parametrize("padded", [False, True])
@pytest.mark.parametrize("exception_type", [ValueError, RuntimeError])
def test_user_api_key_is_redacted_on_every_compatibility_surface(
    monkeypatch,
    input_type,
    padded,
    exception_type,
):
    from dataclasses import replace
    import asyncio

    from kreports.mcp.catalog import TOOL_CATALOG
    from kreports.mcp.dispatch import dispatch_tool
    from kreports.mcp.server import handle_call_tool
    from kreports.mcp.tools import call_tool

    normalized_secret = "task7-dependency-echo-secret"
    supplied_secret = (
        f"  {normalized_secret}  " if padded else normalized_secret
    )
    supplied_secret = (
        SecretStr(supplied_secret) if input_type == "secret_str" else supplied_secret
    )
    original = TOOL_CATALOG["fetch_disclosure_on_demand"]

    def echoing_failure(validated_args):
        handler_secret = validated_args.user_dart_api_key.get_secret_value()
        assert handler_secret == normalized_secret
        raise exception_type(f"upstream rejected {handler_secret}")

    monkeypatch.setitem(
        TOOL_CATALOG,
        "fetch_disclosure_on_demand",
        replace(original, handler=echoing_failure),
    )
    arguments = {
        "rcept_no": "20250711000001",
        "user_dart_api_key": supplied_secret,
    }
    envelope_json = dispatch_tool(
        "fetch_disclosure_on_demand", arguments
    ).model_dump_json()
    legacy_json = call_tool("fetch_disclosure_on_demand", arguments)
    stdio_result = asyncio.run(
        handle_call_tool("fetch_disclosure_on_demand", arguments)
    )
    stdio_json = json.dumps(stdio_result, ensure_ascii=False, default=str)
    for serialized in (envelope_json, legacy_json, stdio_json):
        assert normalized_secret not in serialized
        if padded:
            assert f"  {normalized_secret}  " not in serialized
        assert "[REDACTED]" in serialized


@pytest.mark.parametrize(
    ("secret", "message"),
    [
        ("", "database unavailable"),
        ("x", "database unavailable"),
    ],
)
def test_secret_sanitizer_does_not_over_redact_empty_or_short_unrelated_text(
    monkeypatch,
    secret,
    message,
):
    from dataclasses import replace

    from kreports.mcp.catalog import TOOL_CATALOG
    from kreports.mcp.dispatch import dispatch_tool

    original = TOOL_CATALOG["fetch_disclosure_on_demand"]

    def unrelated_failure(_validated_args):
        raise RuntimeError(message)

    monkeypatch.setitem(
        TOOL_CATALOG,
        "fetch_disclosure_on_demand",
        replace(original, handler=unrelated_failure),
    )
    serialized = dispatch_tool(
        "fetch_disclosure_on_demand",
        {"rcept_no": "20250711000001", "user_dart_api_key": secret},
    ).model_dump_json()
    assert message in serialized
    assert "[REDACTED]" not in serialized


def test_compat_handlers_return_raw_domain_result_and_trim_required_strings():
    from kreports.mcp.tools import HANDLERS, call_tool

    raw = HANDLERS["search_company"]({"query": "  __task7_no_such_company__  "})
    assert raw == {
        "query": "__task7_no_such_company__",
        "count": 0,
        "results": [],
    }
    assert "_meta" not in raw
    assert "answer" not in raw
    assert "answer_pack" not in raw

    enriched = json.loads(
        call_tool("search_company", {"query": "  __task7_no_such_company__  "})
    )
    assert enriched["query"] == "__task7_no_such_company__"
    assert enriched["_meta"]["tool"] == "search_company"


def test_required_string_rejects_blank_after_trimming():
    from kreports.mcp.tools import HANDLERS, call_tool

    with pytest.raises(ValueError, match="query"):
        HANDLERS["search_company"]({"query": "   "})
    result = json.loads(call_tool("search_company", {"query": "   "}))
    assert "error" in result
    assert "query" in result["error"]


def test_legacy_schema_aliases_keep_their_public_types_and_constraints():
    from typing import get_args

    from kreports.mcp.schemas import BsnsYear, CompanyIdent, COMPARE_METRICS

    assert get_args(COMPARE_METRICS) == (
        "영업이익률",
        "순이익률",
        "부채비율",
        "ROE",
        "ROA",
        "자기자본비율",
        "매출성장률",
        "Beneish_M",
    )
    assert TypeAdapter(CompanyIdent).validate_python("005930") == "005930"
    with pytest.raises(ValidationError):
        TypeAdapter(CompanyIdent).validate_python("")
    assert TypeAdapter(BsnsYear).validate_python(2025) == 2025
    with pytest.raises(ValidationError):
        TypeAdapter(BsnsYear).validate_python(1999)


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


def test_very_long_unknown_tool_name_is_bounded_on_all_surfaces():
    from kreports.mcp.dispatch import dispatch_tool
    from kreports.mcp.tools import call_tool

    long_name = "x" * 10_000
    envelope = dispatch_tool(long_name, {})
    envelope_json = envelope.model_dump_json()
    legacy_json = call_tool(long_name, {})

    assert len(envelope.tool_name) <= 120
    assert len(envelope_json) < 2_000
    assert len(legacy_json) < 2_000
    assert long_name not in envelope_json
    assert long_name not in legacy_json


@pytest.mark.parametrize("tool_name", EXPECTED_TOOL_NAMES)
def test_all_tools_accept_their_minimal_interface(tool_name):
    from kreports.mcp.dispatch import dispatch_tool

    result = dispatch_tool(tool_name, MINIMAL_ARGUMENTS[tool_name])
    assert isinstance(result, AnswerEnvelopeV1)
    assert result.tool_name == tool_name
    assert result.data_quality.status in {"usable", "limited", "missing", "error"}
