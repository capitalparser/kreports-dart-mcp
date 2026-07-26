"""Deterministic workflow packs composed solely from public MCP specialists."""
from __future__ import annotations

from collections.abc import Callable
import json
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
        "score_going_concern",
        "get_audit_history",
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

WORKFLOW_CATEGORIES: dict[str, tuple[str, ...]] = {
    "investor_first_pass": (
        "financial_snapshot",
        "quality_of_earnings",
        "disclosure_events",
        "accounting_audit_risk",
    ),
    "audit_acceptance_review": (
        "audit_history_and_opinion",
        "acceptance_evidence_and_gaps",
    ),
    "group_audit_scope": ("group_scope_and_component_evidence",),
    "accounting_policy_peer_review": (
        "policy_text",
        "policy_change_history",
        "peer_differences",
        "kam_linkage",
    ),
}

MAX_WORKFLOW_OUTPUT_BYTES = 100_000
MAX_WORKFLOW_OUTPUT_CHARACTERS = MAX_WORKFLOW_OUTPUT_BYTES
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
    tool: str,
) -> tuple[dict[str, Any], bool]:
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
    else:
        quality = dict(quality)
    error_key = (
        "error"
        if "error" in raw
        else "exception"
        if "exception" in raw
        else None
    )
    if error_key is not None:
        raw_error = raw.get(error_key)
        bounded_error = str(raw_error).replace("\n", " ")[:300]
        quality["status"] = "error"
        quality["grade"] = None
        limitations = quality.get("limitations")
        if not isinstance(limitations, list):
            limitations = []
        quality["limitations"] = [
            bounded_error or "specialist_error",
            *limitations,
        ][:_MAX_ITEMS]
    child = {
        "schema_version": str(raw.get("schema_version") or "1.0")[:20],
        "tool_name": str(raw.get("tool_name") or tool)[:120],
        "verdict": str(raw.get("verdict") or "")[:1_000],
        "answer": str(raw.get("answer") or "")[:_MAX_ANSWER_CHARACTERS],
        "confirmed_facts": _bound_value(raw.get("confirmed_facts") or []),
        "analysis": _bound_value(raw.get("analysis") or []),
        "evidence": _bound_value(raw.get("evidence") or []),
        "data_quality": _bound_value(quality),
        "warnings": _bound_value(raw.get("warnings") or []),
        "next_checks": _bound_value(raw.get("next_checks") or []),
    }
    was_bounded = (
        len(str(raw.get("answer") or "")) > _MAX_ANSWER_CHARACTERS
        or len(str(raw.get("verdict") or "")) > 1_000
        or raw.get("confirmed_facts", []) != child["confirmed_facts"]
        or raw.get("analysis", []) != child["analysis"]
        or raw.get("evidence", []) != child["evidence"]
        or raw.get("warnings", []) != child["warnings"]
        or raw.get("next_checks", []) != child["next_checks"]
    )
    return child, was_bounded


def _serialized_bytes(value: dict[str, Any]) -> int:
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    )


def _quality_status(child: dict[str, Any]) -> str:
    status = str(
        (child.get("data_quality") or {}).get("status") or "error"
    )
    return (
        status
        if status in {"usable", "limited", "missing", "error"}
        else "error"
    )


def _minimal_child(child: dict[str, Any]) -> dict[str, Any]:
    return {
        "tool_name": str(child.get("tool_name") or "unknown")[:120],
        "answer": "",
        "data_quality": {"status": _quality_status(child)},
        "evidence": [],
    }


def _essential_evidence(item: object) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    return {
        "source_label": str(item.get("source_label") or "")[:200],
        "source_url": str(item.get("source_url") or "")[:1_000],
        "rcept_no": (
            str(item["rcept_no"])[:80]
            if item.get("rcept_no") is not None
            else None
        ),
        "section_title": (
            str(item["section_title"])[:300]
            if item.get("section_title") is not None
            else None
        ),
        "excerpt": (
            str(item["excerpt"])[:500]
            if item.get("excerpt") is not None
            else None
        ),
    }


def _budgeted_workflow_result(
    result: dict[str, Any],
    children: list[dict[str, Any]],
) -> dict[str, Any]:
    original_status = str(result.get("status") or "error")
    truncated_status = (
        "error" if original_status == "error" else "limited"
    )
    limitations = [
        str(item)[:300]
        for item in list(result.get("limitations") or [])[:20]
        if str(item)
    ]
    if "workflow_output_truncated" not in limitations:
        limitations.append("workflow_output_truncated")
    budgeted = {
        "workflow_version": "1.0",
        "workflow_name": str(result.get("workflow_name") or "")[:120],
        "categories": [
            str(item)[:120]
            for item in list(result.get("categories") or [])[:10]
        ],
        "company": str(result.get("company") or "")[:120],
        "year": result.get("year"),
        "status": truncated_status,
        "children": [_minimal_child(child) for child in children],
        "limitations": limitations,
    }

    # Evidence is the first optional payload to consume the remaining budget.
    # Each candidate is measured using the same UTF-8 JSON representation that
    # defines the public cap.
    for index, child in enumerate(children):
        for item in list(child.get("evidence") or [])[:5]:
            evidence = _essential_evidence(item)
            if evidence is None:
                continue
            budgeted["children"][index]["evidence"].append(evidence)
            if _serialized_bytes(budgeted) > MAX_WORKFLOW_OUTPUT_BYTES:
                budgeted["children"][index]["evidence"].pop()
                break

    if _serialized_bytes(budgeted) <= MAX_WORKFLOW_OUTPUT_BYTES:
        return budgeted

    # Emergency fallback is deliberately tiny and deterministic. It preserves
    # every child identity and exact quality status, and cannot fail because of
    # valid user/domain payload size.
    return {
        "workflow_version": "1.0",
        "workflow_name": str(result.get("workflow_name") or "")[:120],
        "status": truncated_status,
        "children": [
            {
                "tool_name": str(
                    child.get("tool_name") or "unknown"
                )[:120],
                "data_quality": {"status": _quality_status(child)},
                "evidence": [],
            }
            for child in children
        ],
        "limitations": ["workflow_output_truncated"],
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
        "score_going_concern": common,
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
    output_was_bounded = False
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
        child, child_was_bounded = _bounded_child(envelope, tool)
        output_was_bounded = output_was_bounded or child_was_bounded
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
    result = {
        "workflow_version": "1.0",
        "workflow_name": workflow,
        "categories": list(WORKFLOW_CATEGORIES[workflow]),
        "company": normalized_company,
        "year": int(year),
        "status": status,
        "children": children,
        "limitations": limitations,
    }
    if (
        output_was_bounded
        or _serialized_bytes(result) > MAX_WORKFLOW_OUTPUT_BYTES
    ):
        result = _budgeted_workflow_result(result, children)
    return result


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
