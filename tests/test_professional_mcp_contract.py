"""Public MCP response contracts for professional decision surfaces."""
from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import replace

import pytest


def _source(year: int) -> dict[str, str]:
    receipt = f"{year + 1}0310002820"
    return {
        "rcept_no": receipt,
        "source_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}",
    }


def _prepared_hours_result() -> dict:
    return {
        "subject": {"corp_code": "00126380", "corp_name": "삼성전자"},
        "rows": [
            {
                "year": year,
                "fs_div": "CFS",
                "total_assets_100m": 1000,
                "revenue_100m": 800,
                "audit_fee_m": 200,
                "audit_hours": 1000,
                "hours_basis": "actual",
                "input_status": "complete",
                "missing_fields": [],
                "financial_source": _source(year),
                "audit_source": _source(year),
            }
            for year in (2023, 2024, 2025)
        ],
        "confirmed_facts": [{
            "statement": "최근 3개년 공개자료 입력을 확인했습니다.",
            "source": _source(2025),
        }],
        "data_quality": {
            "status": "usable",
            "limitations": ["합성 입력의 출처를 추가 확인하세요."],
            "section_statuses": {
                "audit_effort": {
                    "status": "usable",
                    "required": True,
                    "applicability": "applicable",
                    "coverage": {"requested_years": 3, "complete_years": 3},
                    "sources": [_source(2025)],
                    "blockers": [],
                },
            },
        },
    }


PRIORITY_TABLES = {
    "prepare_standard_audit_hours_inputs": "standard_audit_hours_inputs",
    "compare_peer_audit_fees": "peer_audit_fee_benchmark",
    "estimate_audit_hours_proxy": "audit_hours_proxy_inputs",
    "build_audit_acceptance_pack": "acceptance_requirements",
    "compare_peer_risk_profile": "peer_risk_metrics",
    "get_audit_history": "audit_history",
    "get_audit_report_sections": "audit_report_sections",
    "search_audit_report_matters": "audit_report_matters",
    "compare_peer_audit_report_matters": "peer_audit_report_matters",
    "get_kam_lifecycle": "kam_timeline",
    "compare_peer_kam_topics": "peer_kam_topics",
    "get_financial_snapshot": "financial_trend",
    "select_peer_group": "peer_selection",
    "compare_to_industry_multi": "industry_metrics",
    "get_investor_signals": "investor_checks",
    "search_disclosure_events": "disclosure_events",
    "get_quality_of_earnings_pack": "quality_of_earnings",
    "get_dcf_input_candidates": "dcf_candidates",
    "build_dcf_model_pack": "dcf_model_readiness",
}


def _quality(*, status: str = "limited") -> dict:
    return {
        "status": status,
        "limitations": ["합성 공시 출처의 범위를 추가 확인하세요."],
        "section_statuses": {
            "coverage": {
                "status": status,
                "required": True,
                "applicability": "applicable",
                "coverage": {"rows": 1},
                "sources": [_source(2025)],
                "blockers": [],
            },
        },
    }


def _priority_arguments(name: str) -> dict:
    if name == "build_dcf_model_pack":
        return {"company": "005930", "base_year": 2025}
    if name in {"get_kam_lifecycle", "get_quality_of_earnings_pack", "get_dcf_input_candidates"}:
        return {"company": "005930", "start_year": 2021, "end_year": 2025}
    if name in {"compare_peer_audit_fees", "prepare_standard_audit_hours_inputs", "compare_peer_risk_profile", "compare_peer_kam_topics", "compare_peer_audit_report_matters"}:
        return {"company": "005930", "year": 2025}
    return {"company": "005930"}


def _priority_result(name: str) -> dict:
    source = _source(2025)
    result: dict = {
        "subject": {"corp_code": "00126380", "corp_name": "삼성전자"},
        "confirmed_facts": [{"statement": "공시로 확인한 합성 사실입니다.", "source": source}],
        "data_quality": _quality(),
    }
    if name == "prepare_standard_audit_hours_inputs":
        return _prepared_hours_result()
    if name == "compare_peer_audit_fees":
        result.update(subject_metrics={"corp_name": "삼성전자", "audit_fee_m": 200, "audit_hours": 1000}, peers=[{"corp_name": "비교회사", "audit_fee_m": 100, "audit_hours": 600}])
    elif name == "estimate_audit_hours_proxy":
        result["subject_metrics"] = {"audit_fee_m": 200, "audit_hours": 1000, "total_assets": 1000}
    elif name == "build_audit_acceptance_pack":
        result["data_quality"]["section_statuses"] = {
            key: {"status": "limited", "required": True, "applicability": "applicable", "coverage": {"rows": 1}, "sources": [source], "blockers": []}
            for key in ("peer_group", "audit_effort", "financial_risk", "audit_history", "accounting_policy", "kam", "audit_report_matters")
        }
    elif name == "compare_peer_risk_profile":
        result["metric_rows"] = [{"metric": "beneish_m_score", "peer_n": 2, "p25": -2.5, "p50": -2.0, "p75": -1.5, "subject_value": -2.1}]
    elif name == "get_audit_history":
        result["history"] = [{"year": 2025, "fs_div": "CFS", "auditor_nm": "감사법인", "audit_opinion": "적정", "auditor_changed": True, "consecutive_years": 1, "rcept_no": source["rcept_no"]}]
    elif name == "get_audit_report_sections":
        result["sections"] = [{"bsns_year": 2025, "section_key": "audit_opinion", "section_title": "감사의견", "source_type": "audit_report", "rcept_no": source["rcept_no"]}]
    elif name in {"search_audit_report_matters", "compare_peer_audit_report_matters"}:
        result["subject_matters"] = [{"section_key": "emphasis", "rcept_no": source["rcept_no"], "corp_name": "삼성전자"}]
        if name == "compare_peer_audit_report_matters":
            result["peer_matter_samples"] = {"peer": [{"section_key": "other_matter", "rcept_no": source["rcept_no"]}]}
    elif name == "get_kam_lifecycle":
        result["events"] = [{"year": 2025, "topic": "revenue", "status": "new", "title": "수익인식", "reason_hint": "추정", "procedure_hint": "검증"}]
    elif name == "compare_peer_kam_topics":
        result["subject_sections"] = [{"bsns_year": 2025, "section_key": "kam", "rcept_no": source["rcept_no"], "corp_name": "삼성전자", "kam_items": [{"normalized_topic": "revenue", "reason_text": "추정", "audit_response_text": "검증"}]}]
        result["audit_report_sections"] = {"semantic_complete": 1, "total": 1}
    elif name == "get_financial_snapshot":
        result.update(unit="억원", rows=[{"연도": 2025, "구분": "CFS", "매출액": 1000, "영업이익": 100, "순이익": 80, "영업CF": 120, "매출성장률": 3, "영업이익률": 10, "source": source}])
    elif name == "select_peer_group":
        result["peer_selection"] = [{"company_name": "비교회사", "ksic": "264", "scale": 1000, "include_reason": "동일 업종"}]
    elif name == "compare_to_industry_multi":
        result.update(fs_div_used="CFS", n_peers=2, metrics=["ROE"], results={2025: {"ROE": {"subject_value": 8, "percentile": 50, "p25": 5, "p50": 7, "p75": 9, "n": 2, "metric_n": 2, "cohort_n": 2, "missing_n": 0, "observed_n": 2, "aggregate_status": "available", "cohort_digest": "digest", "unit": "%", "source": source}}})
    elif name == "get_investor_signals":
        result["quality_snapshot"] = {"checks": {"cash": {"name": "현금전환", "value": None, "status": "unknown", "meaning": "확인 필요"}}}
    elif name == "search_disclosure_events":
        result["events"] = [{"event_date": "2025-01-01", "corp_name": "삼성전자", "event_type": "capital_raise", "event_title": "유상증자", "rcept_no": source["rcept_no"]}]
    elif name == "get_quality_of_earnings_pack":
        result["metrics"] = {"cash_conversion": 0.8}
    elif name == "get_dcf_input_candidates":
        result.update(historical_actuals=[{"year": 2025, "revenue": 1000, "operating_profit": 100, "operating_cf": 120}], candidate_assumptions={"revenue_growth": {"value": 0.03, "basis": "historical_median"}}, valuation_readiness="blocked", valuation_blockers=[{"field": "wacc", "kind": "missing", "impact": "valuation", "owner": "analyst", "next_action": "입력"}])
    elif name == "build_dcf_model_pack":
        result.update(base_year=2025, fs_div="CFS", enterprise_value=None, missing_inputs=["wacc"], missing_accounts=[{"field": "revenue", "year": 2025, "fs_div": "CFS", "basis": "requested_dcf_source_actual"}], assumptions=[])
    return result


@pytest.mark.parametrize("tool_name,table_id", PRIORITY_TABLES.items())
def test_each_priority_tool_has_a_material_canonical_public_pack(tool_name, table_id, monkeypatch):
    """Replacing a priority table with availability fallback must fail this contract."""
    from kreports.mcp.catalog import TOOL_CATALOG
    from kreports.mcp.tools import call_tool

    original = TOOL_CATALOG[tool_name]
    monkeypatch.setitem(TOOL_CATALOG, tool_name, replace(original, handler=lambda _args: deepcopy(_priority_result(tool_name))))
    out = json.loads(call_tool(tool_name, _priority_arguments(tool_name)))

    assert out["data_quality"]["status"] in {"usable", "limited", "missing", "error"}
    assert out["answer"].startswith("판정:")
    assert out["answer_pack"] is not None
    assert out["answer_pack"]["data_quality"]["status"] == out["data_quality"]["status"]
    table = next(table for table in out["answer_pack"]["tables"] if table["id"] == table_id)
    assert table_id != "availability"
    assert len(table["rows"]) > 0
    has_sources_and_facts = (
        len(out["answer_pack"].get("sources") or []) >= 1
        and len(out.get("confirmed_facts") or []) >= 1
    )
    has_explicit_source_blocker = (
        out["data_quality"]["status"] == "limited"
        and any(
            "출처" in limitation or "근거" in limitation
            for limitation in out["data_quality"].get("limitations") or []
        )
    )
    if tool_name != "build_dcf_model_pack":
        assert has_sources_and_facts or has_explicit_source_blocker
    assert all(phrase not in out["answer"] for phrase in ("승인", "거절", "매수", "매도", "적정 의견"))


@pytest.mark.parametrize(
    "tool_name",
    ("get_dcf_input_candidates", "build_dcf_model_pack"),
)
def test_dcf_answers_start_with_canonical_status_at_all_public_boundaries(tool_name, monkeypatch):
    """A DCF-specific preamble must not displace the public 판정 header."""
    from kreports.mcp.catalog import TOOL_CATALOG
    from kreports.mcp.dispatch import dispatch_tool
    from kreports.mcp.server import handle_call_tool
    from kreports.mcp.tools import call_tool

    original = TOOL_CATALOG[tool_name]
    monkeypatch.setitem(TOOL_CATALOG, tool_name, replace(original, handler=lambda _args: deepcopy(_priority_result(tool_name))))
    arguments = _priority_arguments(tool_name)
    legacy = json.loads(call_tool(tool_name, arguments))
    envelope = dispatch_tool(tool_name, arguments).model_dump(mode="json")
    content, stdio = asyncio.run(handle_call_tool(tool_name, arguments))

    assert legacy["answer"].startswith("판정:")
    assert envelope["answer"].startswith("판정:")
    assert content[0].text.startswith("판정:")
    assert stdio["answer"].startswith("판정:")


WORKFLOWS = (
    ("prepare_standard_audit_hours_inputs", "standard_audit_hours_inputs"),
    ("build_audit_acceptance_pack", "acceptance_requirements"),
    ("get_audit_history", "audit_history"),
    ("compare_to_industry_multi", "industry_metrics"),
    ("get_investor_signals", "investor_checks"),
    ("get_quality_of_earnings_pack", "quality_of_earnings"),
    ("get_dcf_input_candidates", "dcf_candidates"),
    ("build_dcf_model_pack", "dcf_model_readiness"),
)


def _workflow_result(tool_name: str) -> dict:
    result = _priority_result(tool_name)
    if tool_name == "prepare_standard_audit_hours_inputs":
        result["rows"][0]["audit_hours"] = None
        result["rows"][0]["missing_fields"] = ["audit_hours"]
        result["rows"][0]["input_status"] = "limited"
    elif tool_name == "get_audit_history":
        result["history"].append({
            "year": 2024,
            "fs_div": "CFS",
            "auditor_nm": "감사법인",
            "audit_opinion": "적정",
            "auditor_changed": False,
            "consecutive_years": 2,
            "rcept_no": _source(2024)["rcept_no"],
        })
    elif tool_name == "get_quality_of_earnings_pack":
        result["audit_matter_summary"] = {
            "unique_receipt_count": 1,
            "section_count": 1,
            "dedupe_basis": "receipt",
            "groups": [{
                "year": 2025,
                "matter_type": "emphasis",
                "severity": "info",
                "section_count": 1,
                "source": _source(2025),
            }],
        }
    return result


@pytest.mark.parametrize("tool_name,table_id", WORKFLOWS)
def test_synthetic_professional_workflows_match_across_call_dispatch_and_stdio(tool_name, table_id, monkeypatch):
    """A changed public boundary must not alter a golden professional workflow."""
    from kreports.mcp.catalog import TOOL_CATALOG
    from kreports.mcp.dispatch import dispatch_tool
    from kreports.mcp.resources import read_resource
    from kreports.mcp.server import handle_call_tool
    from kreports.mcp.tools import call_tool

    original = TOOL_CATALOG[tool_name]
    monkeypatch.setitem(TOOL_CATALOG, tool_name, replace(original, handler=lambda _args: deepcopy(_workflow_result(tool_name))))
    arguments = _priority_arguments(tool_name)
    legacy = json.loads(call_tool(tool_name, arguments))
    envelope = dispatch_tool(tool_name, arguments).model_dump(mode="json")
    content, stdio = asyncio.run(handle_call_tool(tool_name, arguments))

    assert content[0].text == envelope["answer"]
    assert stdio == envelope
    assert legacy["data_quality"]["status"] == envelope["data_quality"]["status"]
    assert legacy["data_quality"]["section_statuses"] == envelope["data_quality"]["section_statuses"]
    assert legacy["answer_pack"]["data_quality"]["section_statuses"] == legacy["data_quality"]["section_statuses"]
    resource = read_resource(legacy["answer_pack"]["resource_uri"])
    assert legacy["data_quality"]["status"] in resource["text"]
    table = next(table for table in legacy["answer_pack"]["tables"] if table["id"] == table_id)
    assert len(table["rows"]) >= 1
    assert all(phrase not in legacy["answer"] for phrase in ("승인", "거절", "매수", "매도", "적정 의견"))


def test_handler_execution_error_is_canonical_and_never_leaks_sql(monkeypatch):
    """A prepared-cache schema failure must not become raw SQL at any boundary."""
    from kreports.mcp.catalog import TOOL_CATALOG
    from kreports.mcp.dispatch import dispatch_tool
    from kreports.mcp.server import handle_call_tool
    from kreports.mcp.tools import call_tool

    original = TOOL_CATALOG["get_audit_report_sections"]

    def schema_failure(_args):
        raise RuntimeError("OperationalError: no such column: audit_procedure_items.kam_item_id")

    monkeypatch.setitem(TOOL_CATALOG, "get_audit_report_sections", replace(original, handler=schema_failure))
    arguments = {"company": "005930", "year": 2025}
    legacy = json.loads(call_tool("get_audit_report_sections", arguments))
    envelope = dispatch_tool("get_audit_report_sections", arguments).model_dump(mode="json")
    content, stdio = asyncio.run(handle_call_tool("get_audit_report_sections", arguments))

    for out in (legacy, envelope, stdio):
        assert out["data_quality"]["status"] == "error"
        assert out["answer"].startswith("판정:")
        assert "로컬 캐시 스키마 또는 준비된 데이터" in out["answer"]
        assert out["answer_pack"]["data_quality"]["status"] == "error"
        assert out["answer_pack"]["tables"][0]["id"] == "availability"
    assert content[0].text == envelope["answer"]
    rendered = json.dumps([legacy, envelope, stdio], ensure_ascii=False)
    assert all(token not in rendered for token in ("OperationalError", "no such column", "audit_procedure_items", "kam_item_id", "SQL"))


def test_prepared_audit_hours_parity_across_every_public_boundary(monkeypatch):
    """A dispatch/stdio bypass would split the professional status contract."""
    from kreports.mcp.catalog import TOOL_CATALOG
    from kreports.mcp.dispatch import dispatch_tool
    from kreports.mcp.server import handle_call_tool
    from kreports.mcp.tools import call_tool

    original = TOOL_CATALOG["prepare_standard_audit_hours_inputs"]
    monkeypatch.setitem(
        TOOL_CATALOG,
        "prepare_standard_audit_hours_inputs",
        replace(original, handler=lambda _args: deepcopy(_prepared_hours_result())),
    )
    arguments = {"company": "005930", "year": 2025}

    legacy = json.loads(call_tool("prepare_standard_audit_hours_inputs", arguments))
    envelope = dispatch_tool("prepare_standard_audit_hours_inputs", arguments).model_dump(
        mode="json"
    )
    content, stdio = asyncio.run(
        handle_call_tool("prepare_standard_audit_hours_inputs", arguments)
    )

    assert legacy["answer"].startswith("판정:")
    assert envelope["answer"].startswith("판정:")
    assert content[0].text == envelope["answer"]
    assert stdio == envelope
    assert legacy["data_quality"]["status"] == envelope["data_quality"]["status"]
    assert (
        legacy["answer_pack"]["summary"]["status"]
        == legacy["data_quality"]["status"]
    )
    table = next(
        table
        for table in legacy["answer_pack"]["tables"]
        if table["id"] == "standard_audit_hours_inputs"
    )
    assert len(table["rows"]) == 3
    assert "최근 3개년 공개자료 입력" in legacy["answer"]
    assert legacy["data_quality"]["section_statuses"] == envelope["data_quality"]["section_statuses"]
