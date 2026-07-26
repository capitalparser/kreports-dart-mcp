"""Deterministic workflow packs composed solely from public MCP specialists."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from kreports.mcp.contracts import AnswerEnvelopeV1, build_answer_envelope
from kreports.mcp.dispatch import dispatch_tool


WorkflowDispatch = Callable[
    [str, dict[str, Any]],
    AnswerEnvelopeV1 | dict[str, Any],
]

WORKFLOW_SPECS: dict[str, tuple[str, ...]] = {
    "investor_first_pass": (
        "get_financial_snapshot",
        "get_quality_of_earnings_pack",
        "search_disclosure_events",
        "get_investor_signals",
    ),
    "audit_acceptance_review": (
        "get_audit_history",
        "build_audit_acceptance_pack",
    ),
    "group_audit_scope": (
        "get_subsidiary_auditors",
    ),
    "accounting_policy_peer_review": (
        "get_accounting_policy",
        "get_accounting_policy_changes",
        "compare_peer_accounting_policies",
        "get_kam_lifecycle",
    ),
}

_MAX_ANSWER_CHARACTERS = 8_000
_MAX_ITEMS = 20
_MAX_NESTED_TEXT = 1_000


def _bound_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 6:
        return None
    if isinstance(value, str):
        return value[:_MAX_NESTED_TEXT]
    if isinstance(value, list):
        return [
            _bound_value(item, depth=depth + 1)
            for item in value[:_MAX_ITEMS]
        ]
    if isinstance(value, dict):
        return {
            str(key)[:120]: _bound_value(item, depth=depth + 1)
            for key, item in list(value.items())[:50]
        }
    return value


def _bounded_child(
    envelope: AnswerEnvelopeV1 | dict[str, Any],
) -> dict[str, Any]:
    raw = (
        envelope.model_dump(mode="json")
        if isinstance(envelope, AnswerEnvelopeV1)
        else dict(envelope)
    )
    quality = raw.get("data_quality")
    if not isinstance(quality, dict):
        quality = {
            "status": "error",
            "grade": None,
            "dataset_version": "unknown",
            "schema_version": "unknown",
            "covered_years": [],
            "missing_fields": [],
            "limitations": ["invalid_child_answer_contract"],
        }
    return {
        "schema_version": str(raw.get("schema_version") or "1.0")[:20],
        "tool_name": str(raw.get("tool_name") or "unknown")[:120],
        "verdict": str(raw.get("verdict") or "")[:1_000],
        "answer": str(raw.get("answer") or "")[:_MAX_ANSWER_CHARACTERS],
        "confirmed_facts": _bound_value(raw.get("confirmed_facts") or []),
        "analysis": _bound_value(raw.get("analysis") or []),
        "evidence": _bound_value(raw.get("evidence") or []),
        "data_quality": _bound_value(quality),
        "warnings": _bound_value(raw.get("warnings") or []),
        "next_checks": _bound_value(raw.get("next_checks") or []),
    }


def _arguments(workflow: str, tool: str, company: str, year: int) -> dict[str, Any]:
    start_year = max(2000, year - 4)
    common = {"company": company}
    arguments_by_tool: dict[str, dict[str, Any]] = {
        "get_financial_snapshot": {**common, "years": 5},
        "get_quality_of_earnings_pack": {
            **common,
            "start_year": start_year,
            "end_year": year,
            "fs_div": "CFS",
        },
        "search_disclosure_events": {
            **common,
            "start_date": f"{year}-01-01",
            "end_date": f"{year}-12-31",
            "limit": 50,
        },
        "get_investor_signals": {
            **common,
            "years": 5,
            "window_days": 365,
            "event_limit": 20,
        },
        "get_audit_history": common,
        "build_audit_acceptance_pack": {
            **common,
            "year": year,
            "peer_limit": 30,
            "fs_strategy": "auto",
        },
        "get_subsidiary_auditors": {
            **common,
            "limit": 100,
            "only_with_auditor": False,
            "slim": True,
        },
        "get_accounting_policy": {
            **common,
            "bsns_year": year,
            "fs_div": "CFS",
        },
        "get_accounting_policy_changes": {
            **common,
            "start_year": start_year,
            "end_year": year,
            "fs_div": "CFS",
        },
        "compare_peer_accounting_policies": {
            **common,
            "year": year,
            "peer_limit": 30,
            "fs_div": "CFS",
            "fs_strategy": "auto",
        },
        "get_kam_lifecycle": {
            **common,
            "start_year": start_year,
            "end_year": year,
        },
    }
    return dict(arguments_by_tool[tool])


def _failed_child(tool: str) -> AnswerEnvelopeV1:
    return build_answer_envelope(
        tool,
        {
            "error": f"{tool} specialist execution failed",
            "answer": f"{tool} specialist execution failed",
        },
    )


def run_workflow(
    workflow: str,
    company: str,
    year: int,
    *,
    dispatch: WorkflowDispatch = dispatch_tool,
) -> dict[str, Any]:
    specialists = WORKFLOW_SPECS.get(workflow)
    if specialists is None:
        raise ValueError("unknown_workflow")
    normalized_company = str(company or "").strip()
    if not normalized_company or len(normalized_company) > 120:
        raise ValueError("invalid_company")
    if not 2000 <= int(year) <= 2100:
        raise ValueError("invalid_year")

    children: list[dict[str, Any]] = []
    limitations: list[str] = []
    seen: set[str] = set()
    for tool in specialists:
        if tool in seen:
            raise RuntimeError("duplicate_workflow_specialist")
        seen.add(tool)
        try:
            envelope = dispatch(
                tool,
                _arguments(workflow, tool, normalized_company, int(year)),
            )
        except Exception:
            envelope = _failed_child(tool)
        child = _bounded_child(envelope)
        status = str(
            (child.get("data_quality") or {}).get("status") or "error"
        )
        if status == "error":
            limitations.append(f"{tool}: specialist_error")
        elif status in {"missing", "limited"}:
            limitations.append(f"{tool}: {status}")
        children.append(child)

    statuses = {
        str((child.get("data_quality") or {}).get("status") or "error")
        for child in children
    }
    status = (
        "error"
        if "error" in statuses
        else "limited"
        if statuses & {"missing", "limited"}
        else "usable"
    )
    return {
        "workflow_version": "1.0",
        "workflow_name": workflow,
        "company": normalized_company,
        "year": int(year),
        "status": status,
        "children": children,
        "limitations": limitations,
    }


def investor_first_pass(
    company: str,
    year: int,
    *,
    dispatch: WorkflowDispatch = dispatch_tool,
) -> dict[str, Any]:
    return run_workflow(
        "investor_first_pass",
        company,
        year,
        dispatch=dispatch,
    )


def audit_acceptance_review(
    company: str,
    year: int,
    *,
    dispatch: WorkflowDispatch = dispatch_tool,
) -> dict[str, Any]:
    return run_workflow(
        "audit_acceptance_review",
        company,
        year,
        dispatch=dispatch,
    )


def group_audit_scope(
    company: str,
    year: int,
    *,
    dispatch: WorkflowDispatch = dispatch_tool,
) -> dict[str, Any]:
    return run_workflow(
        "group_audit_scope",
        company,
        year,
        dispatch=dispatch,
    )


def accounting_policy_peer_review(
    company: str,
    year: int,
    *,
    dispatch: WorkflowDispatch = dispatch_tool,
) -> dict[str, Any]:
    return run_workflow(
        "accounting_policy_peer_review",
        company,
        year,
        dispatch=dispatch,
    )
