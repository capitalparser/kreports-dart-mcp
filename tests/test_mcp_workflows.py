from __future__ import annotations

import json

from kreports.mcp.contracts import build_answer_envelope
from kreports.mcp.workflows import (
    WORKFLOW_SPECS,
    accounting_policy_peer_review,
    audit_acceptance_review,
    group_audit_scope,
    investor_first_pass,
)


def _envelope(name: str, *, status: str = "usable"):
    payload = {
        "answer": f"{name} answer",
        "data_quality": {
            "status": status,
            "grade": "A" if status == "usable" else None,
            "dataset_version": "dataset-v1",
            "schema_version": "schema-v1",
            "covered_years": [2025],
            "missing_fields": [],
            "limitations": [],
        },
        "confirmed_facts": [
            {
                "fact": name,
                "source": {
                    "source_label": "DART",
                    "source_url": (
                        "https://dart.fss.or.kr/dsaf001/main.do?"
                        "rcpNo=20250312000001"
                    ),
                    "rcept_no": "20250312000001",
                },
            }
        ],
    }
    if status == "error":
        payload["error"] = f"{name} failed"
    return build_answer_envelope(name, payload)


def test_workflow_specialists_run_once_in_declared_order():
    calls = []

    def dispatch(name, arguments):
        calls.append((name, arguments))
        return _envelope(name)

    result = investor_first_pass("00126380", 2025, dispatch=dispatch)

    expected = list(WORKFLOW_SPECS["investor_first_pass"])
    assert [name for name, _ in calls] == expected
    assert len(calls) == len({name for name, _ in calls})
    assert [child["tool_name"] for child in result["children"]] == expected


def test_workflows_preserve_child_quality_and_evidence():
    result = accounting_policy_peer_review(
        "00126380",
        2025,
        dispatch=lambda name, _args: _envelope(name, status="limited"),
    )

    assert all(
        child["data_quality"]["status"] == "limited"
        for child in result["children"]
    )
    assert all(child["evidence"] for child in result["children"])
    assert result["status"] == "limited"


def test_failed_child_is_error_and_limitation_not_missing_or_success():
    failing_name = WORKFLOW_SPECS["audit_acceptance_review"][1]

    def dispatch(name, _args):
        if name == failing_name:
            raise RuntimeError("private database path")
        return _envelope(name)

    result = audit_acceptance_review(
        "00126380",
        2025,
        dispatch=dispatch,
    )

    failed = next(
        child for child in result["children"]
        if child["tool_name"] == failing_name
    )
    assert failed["data_quality"]["status"] == "error"
    assert result["status"] == "error"
    assert any(failing_name in item for item in result["limitations"])
    assert "private database path" not in json.dumps(result)


def test_group_workflow_uses_no_server_key_and_results_are_bounded():
    calls = []

    def dispatch(name, arguments):
        calls.append((name, arguments))
        envelope = _envelope(name)
        envelope.answer = "x" * 50_000
        return envelope

    result = group_audit_scope(
        "00126380",
        2025,
        dispatch=dispatch,
    )

    assert all("user_dart_api_key" not in args for _, args in calls)
    assert all(name != "fetch_disclosure_on_demand" for name, _ in calls)
    assert len(json.dumps(result, ensure_ascii=False)) < 100_000
    assert len(result["children"][0]["answer"]) <= 8_000


def test_workflow_output_is_deterministic_for_same_child_results():
    def dispatch(name, _args):
        return _envelope(name)

    first = investor_first_pass("00126380", 2025, dispatch=dispatch)
    second = investor_first_pass("00126380", 2025, dispatch=dispatch)

    assert first == second
