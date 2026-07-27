from __future__ import annotations

import asyncio
from dataclasses import replace
import json

from kreports.mcp.contracts import AnswerEnvelopeV1


EXPECTED_TOOL_NAMES = (
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
)


def _fixture_arguments(tool_name, model) -> dict:
    values = {}
    for name, field in model.model_fields.items():
        if not field.is_required():
            continue
        if name in {"year", "bsns_year", "base_year"}:
            values[name] = 2025
        elif name == "dataset":
            values[name] = "financials"
        else:
            values[name] = "005930"
    if tool_name == "get_industry_audit_landscape":
        values["induty_code"] = "264"
    if tool_name in {
        "compare_to_industry",
        "search_audit_report_matters",
        "search_audit_procedures",
        "search_disclosure_events",
    }:
        values["company"] = "005930"
    if tool_name == "fetch_disclosure_on_demand":
        values["rcept_no"] = "20250101000001"
    if tool_name == "build_dcf_model_pack":
        values.update(
            revenue_growth=0.03,
            operating_margin=0.1,
            tax_rate=0.22,
            da_to_revenue=0.03,
            capex_to_revenue=0.04,
            nwc_to_revenue=0.1,
            wacc=0.09,
            terminal_growth=0.02,
        )
    return values


def test_all_tool_contract_is_derived_from_catalog_and_covers_all_32_tools(
    temp_engine,
):
    from kreports.release_artifact import (
        FROZEN_TOOL_WIRE_SHA256,
        run_all_tool_contract,
    )

    result = run_all_tool_contract()

    assert result == {"passed": True, "checks": 32}
    assert FROZEN_TOOL_WIRE_SHA256 == (
        "055f54993bf45f2e4a1388642871d09c1e2f45fc0b5fde1e83228bb910b38339"
    )


def test_all_32_catalog_tools_have_strict_inputs_and_answer_envelopes(
    temp_engine,
):
    from sqlalchemy.orm import Session

    from kreports.db.models import Company
    from kreports.mcp.catalog import TOOL_CATALOG
    from kreports.mcp.dispatch import dispatch_tool

    with Session(temp_engine) as session:
        session.add(
            Company(
                corp_code="00126380",
                stock_code="005930",
                corp_name="삼성전자",
                market="KOSPI",
                induty_code="264",
            )
        )
        session.commit()

    assert tuple(TOOL_CATALOG) == EXPECTED_TOOL_NAMES
    for name, spec in TOOL_CATALOG.items():
        arguments = _fixture_arguments(name, spec.input_model)
        strict = dispatch_tool(name, {**arguments, "task17_unknown": True})
        assert strict.data_quality.status == "error"
        assert "task17_unknown" in strict.answer

        result = dispatch_tool(name, arguments)
        assert isinstance(result, AnswerEnvelopeV1)
        assert result.tool_name == name
        assert result.answer.strip()
        if name == "fetch_disclosure_on_demand":
            assert result.data_quality.status == "error"
            assert (
                "user_dart_api_key is required"
                in result.data_quality.limitations
            )
            continue
        assert result.data_quality.status in {
            "usable",
            "limited",
            "missing",
        }, name
        if spec.professional:
            assert (
                any(ref.source_url.startswith("https://dart.fss.or.kr/")
                    for ref in result.evidence)
                or result.data_quality.limitations
                or result.warnings
            )


def test_api_key_canary_never_crosses_any_public_or_manifest_surface(
    temp_engine,
    monkeypatch,
):
    from kreports.mcp.catalog import TOOL_CATALOG
    from kreports.mcp.dispatch import dispatch_tool
    from kreports.mcp.server import handle_call_tool
    from kreports.mcp.tools import call_tool

    secret = "task17-network-api-key-canary"
    original = TOOL_CATALOG["fetch_disclosure_on_demand"]

    def echo_failure(validated):
        raise RuntimeError(validated.user_dart_api_key.get_secret_value())

    monkeypatch.setitem(
        TOOL_CATALOG,
        "fetch_disclosure_on_demand",
        replace(original, handler=echo_failure),
    )
    arguments = {
        "rcept_no": "20260727000001",
        "user_dart_api_key": secret,
    }
    surfaces = (
        dispatch_tool(
            "fetch_disclosure_on_demand",
            arguments,
        ).model_dump_json(),
        call_tool("fetch_disclosure_on_demand", arguments),
        json.dumps(
            asyncio.run(
                handle_call_tool("fetch_disclosure_on_demand", arguments)
            ),
            ensure_ascii=False,
            default=str,
        ),
        json.dumps(
            {
                "tool_contract": {
                    "version": "1.0",
                    "tool_count": 32,
                }
            }
        ),
    )
    assert all(secret not in surface for surface in surfaces)
    assert all(
        "[REDACTED]" in surface
        for surface in surfaces[:3]
    )


def test_all_tool_contract_fails_when_any_valid_fixture_invocation_errors(
    tmp_path,
    monkeypatch,
):
    from dataclasses import replace

    from sqlalchemy import create_engine

    from kreports import release_artifact
    from kreports.db.models import Base
    from kreports.mcp.catalog import TOOL_CATALOG

    db_path = tmp_path / "runtime.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    original = TOOL_CATALOG["search_company"]
    invoked = False

    def fail(_validated):
        nonlocal invoked
        invoked = True
        raise RuntimeError("fixture invocation failed")

    monkeypatch.setitem(
        TOOL_CATALOG,
        "search_company",
        replace(original, handler=fail),
    )

    passed = release_artifact._run_catalog_dispatch_contract(db_path)

    assert invoked is True
    assert passed is False
