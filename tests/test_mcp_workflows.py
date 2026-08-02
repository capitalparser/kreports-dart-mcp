from __future__ import annotations

import json

from kreports.mcp.contracts import build_answer_envelope
from kreports.mcp.workflows import (
    MAX_WORKFLOW_OUTPUT_BYTES,
    MAX_WORKFLOW_OUTPUT_CHARACTERS,
    MAX_SEMANTIC_PEER_CONTEXT_WORKFLOW_BYTES,
    WORKFLOW_SPECS,
    accounting_policy_peer_review,
    audit_acceptance_review,
    group_audit_scope,
    investor_first_pass,
    semantic_peer_context_review,
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


def test_semantic_peer_context_workflow_reuses_one_cohort_and_preserves_source_separation():
    calls = []
    shared_cohort = {
        "subject": {"corp_code": "00000001", "corp_name": "Context Corp"},
        "peers": [{"corp_code": "00000002", "corp_name": "Peer Corp"}],
        "selection_policy": {
            "selection_mode": "strict",
            "fs_div_used": "OFS",
            "requested_year": 2024,
        },
        "confidence": "medium",
    }

    def context_builder(company, year, topics=None):
        calls.append(("context", company, year, topics))
        return {
            "subject": {"corp_code": "00000001", "corp_name": "Context Corp"},
            "year": 2024,
            "availability": {
                "business_report": "available",
                "audit_report": "unavailable",
                "notes": "available",
                "evidence_documents": "unavailable",
                "disclosures": "unavailable",
                "financials": "unavailable",
            },
            "business_report": [
                {
                    "source_locator": "report_sections:1",
                    "section_key": "risks",
                    "excerpt": "DART risk disclosure",
                    "full_text_hash": "a" * 40,
                    "rcept_no": "20250301000001",
                }
            ],
            "notes": [
                {
                    "source_locator": "accounting_note_chapters:10",
                    "fs_div": "CFS",
                    "topic": "leases",
                    "excerpt": "CFS lease note",
                    "full_text_hash": "b" * 40,
                },
                {
                    "source_locator": "accounting_note_chapters:11",
                    "fs_div": "OFS",
                    "topic": "leases",
                    "excerpt": "OFS lease note",
                    "full_text_hash": "c" * 40,
                },
            ],
        }

    def cohort_selector(company, **kwargs):
        calls.append(("cohort", company, kwargs))
        return shared_cohort

    def note_builder(company, year, **kwargs):
        calls.append(("notes", company, year, kwargs))
        assert kwargs["_peer_group"] is shared_cohort
        assert kwargs["topics"] == ["leases"]
        return {
            "subject": shared_cohort["subject"],
            "year": year,
            "peer_selection": shared_cohort["selection_policy"],
            "topics": [
                {
                    "topic": "leases",
                    "rows": [
                        {
                            "company": {"corp_code": "00000001"},
                            "availability": "available",
                            "source_locator": "accounting_note_chapters:11",
                            "fs_div": "OFS",
                        },
                        {
                            "company": {"corp_code": "00000002"},
                            "availability": "summary_only",
                            "source_locator": "accounting_note_chapters:12",
                            "fs_div": "OFS",
                        },
                    ],
                }
            ],
            "read_only": True,
        }

    result = semantic_peer_context_review(
        "00000001",
        2024,
        topics=["risks", "leases"],
        peer_criteria={"mode": "strict", "prefix_len": 3},
        fs_strategy="auto",
        company_ir=[
            {
                "source_class": "company_ir",
                "source_id": "ir-1",
                "excerpt": "Management target",
            }
        ],
        web_news=[
            {
                "source_class": "web_news",
                "source_id": "news-1",
                "excerpt": "External coverage",
            }
        ],
        context_builder=context_builder,
        cohort_selector=cohort_selector,
        note_builder=note_builder,
    )

    assert [call[0] for call in calls] == ["context", "cohort", "notes"]
    assert calls[0][2] == calls[1][2]["year"] == calls[2][2] == 2024
    assert calls[1][2]["criteria"] == {"mode": "strict", "prefix_len": 3}
    assert result["read_only"] is True
    assert result["fs_div_used"] == "OFS"
    assert result["peer_selection"] == shared_cohort["selection_policy"]
    assert [item["fs_div"] for item in result["semantic_context"]["notes"]] == ["OFS"]
    pack = result["context_pack"]
    assert pack["source_precedence"] == ["dart_filing", "company_ir", "web_news", "llm_analysis"]
    assert [item["source_id"] for item in pack["dart_filing"]] == [
        "report_sections:1",
        "accounting_note_chapters:11",
    ]
    assert [item["source_id"] for item in pack["company_ir"]] == ["ir-1"]
    assert [item["source_id"] for item in pack["web_news"]] == ["news-1"]
    assert pack["peer_note_comparison"]["data"]["topics"][0]["rows"][1]["availability"] == "summary_only"
    assert pack["peer_note_comparison"]["data"]["topics"][0]["rows"][1]["source_locator"] == "accounting_note_chapters:12"
    assert {item["evidence_type"] for item in pack["missing_evidence"]} >= {"audit_report"}


def test_semantic_peer_context_workflow_keeps_explicit_note_fs_fallback_provenance():
    from kreports.mcp.workflows import semantic_peer_context_review

    def context_builder(_company, _year, topics=None):
        assert topics == ["risks", "leases"]
        return {
            "subject": {"corp_code": "00000001", "corp_name": "Context Corp"},
            "year": 2024,
            "availability": {
                "business_report": "available",
                "notes": "available",
                "financials": "available",
            },
            "business_report": [
                {
                    "source_locator": "report_sections:1",
                    "section_key": "risks",
                    "excerpt": "DART risk disclosure",
                    "full_text_hash": "a" * 40,
                }
            ],
            "notes": [
                {
                    "source_locator": "accounting_note_chapters:10",
                    "fs_div": "CFS",
                    "topic": "leases",
                    "excerpt": "CFS fallback lease note",
                    "full_text_hash": "b" * 40,
                }
            ],
            "financials": [
                {
                    "source_locator": "financials:00000001:2024:CFS:Q4",
                    "fs_div": "CFS",
                    "excerpt": "CFS financials",
                },
                {
                    "source_locator": "financials:00000001:2024:OFS:Q4",
                    "fs_div": "OFS",
                    "excerpt": "OFS financials",
                },
            ],
        }

    cohort = {
        "subject": {"corp_code": "00000001", "corp_name": "Context Corp"},
        "peers": [],
        "selection_policy": {"fs_div_used": "OFS", "requested_year": 2024},
    }

    result = semantic_peer_context_review(
        "00000001",
        2024,
        topics=["risks", "leases"],
        context_builder=context_builder,
        cohort_selector=lambda *_args, **_kwargs: cohort,
        note_builder=lambda _company, _year, **kwargs: {
            "subject": cohort["subject"],
            "year": 2024,
            "peer_selection": cohort["selection_policy"],
            "topics": [
                {
                    "topic": "leases",
                    "rows": [
                        {
                            "company": {"corp_code": "00000001"},
                            "availability": "available",
                            "source_locator": "accounting_note_chapters:10",
                            "fs_div": "CFS",
                            "fs_div_selection": {
                                "requested": "OFS",
                                "used": "CFS",
                                "status": "fallback_requested_fs_div_unavailable",
                            },
                        }
                    ],
                }
            ],
            "read_only": True,
        },
    )

    assert [item["fs_div"] for item in result["semantic_context"]["financials"]] == ["OFS"]
    assert [item["fs_div"] for item in result["semantic_context"]["notes"]] == ["CFS"]
    assert result["semantic_context"]["notes"][0]["fs_div_selection"] == {
        "requested": "OFS",
        "used": "CFS",
        "status": "fallback_requested_fs_div_unavailable",
    }
    pack_notes = [
        item for item in result["context_pack"]["dart_filing"]
        if item["metadata"]["bucket"] == "notes"
    ]
    assert pack_notes[0]["metadata"]["fs_div"] == "CFS"
    assert pack_notes[0]["metadata"]["fs_div_selection"]["status"] == "fallback_requested_fs_div_unavailable"
    assert [item["source_id"] for item in result["context_pack"]["dart_filing"] if item["metadata"]["bucket"] == "financials"] == ["financials:00000001:2024:OFS:Q4"]


def test_semantic_peer_context_workflow_applies_total_output_budget():
    from kreports.mcp.workflows import semantic_peer_context_review

    huge = "x" * 4_000
    result = semantic_peer_context_review(
        "00000001",
        2024,
        context_builder=lambda *_args, **_kwargs: {
            "subject": {"corp_code": "00000001", "corp_name": "Context Corp"},
            "year": 2024,
            "business_report": [
                {
                    "source_locator": f"report_sections:{index}",
                    "section_key": "risks",
                    "excerpt": huge,
                    "full_text_hash": f"{index:040d}",
                }
                for index in range(80)
            ],
        },
        cohort_selector=lambda *_args, **_kwargs: {
            "subject": {"corp_code": "00000001", "corp_name": "Context Corp"},
            "peers": [],
            "selection_policy": {"fs_div_used": "CFS"},
        },
        note_builder=lambda *_args, **_kwargs: {
            "year": 2024,
            "topics": [{"topic": "leases", "rows": [{"excerpt": huge}] * 80}],
            "read_only": True,
        },
    )

    assert len(json.dumps(result, ensure_ascii=False).encode("utf-8")) <= MAX_SEMANTIC_PEER_CONTEXT_WORKFLOW_BYTES
    assert result["truncation"] == {
        "applied": True,
        "max_output_bytes": MAX_SEMANTIC_PEER_CONTEXT_WORKFLOW_BYTES,
        "reason": "semantic_peer_context_output_budget",
    }
    assert result["context_pack"]["truncation"]["max_output_bytes"]


def test_semantic_workflow_budget_preserves_an_already_bounded_context_pack():
    from kreports.mcp.workflows import semantic_peer_context_review

    huge_selection = [[["x" * 4_000 for _ in range(20)] for _ in range(20)] for _ in range(20)]
    result = semantic_peer_context_review(
        "00000001",
        2024,
        context_builder=lambda *_args, **_kwargs: {
            "subject": {"corp_code": "00000001", "corp_name": "Context Corp"},
            "year": 2024,
            "business_report": [
                {
                    "source_locator": "report_sections:1",
                    "section_key": "risks",
                    "excerpt": "DART excerpt",
                    "full_text_hash": "a" * 40,
                    "availability": "summary_only",
                    "rcept_no": "20250301000001",
                    "fs_div_selection": {
                        "requested": "OFS",
                        "used": "CFS",
                        "status": "fallback_requested_fs_div_unavailable",
                        "huge_nested_metadata": huge_selection,
                    },
                }
            ],
        },
        cohort_selector=lambda *_args, **_kwargs: {
            "subject": {"corp_code": "00000001", "corp_name": "Context Corp"},
            "peers": [],
            "selection_policy": {
                "fs_div_used": "CFS",
                "large_selection_metadata": huge_selection,
            },
        },
        note_builder=lambda *_args, **_kwargs: {"year": 2024, "topics": [], "read_only": True},
        company_ir=[
            {"source_class": "company_ir", "source_id": "ir-1", "excerpt": "IR"}
        ],
        web_news=[
            {"source_class": "web_news", "source_id": "news-1", "excerpt": "News"}
        ],
    )

    assert len(json.dumps(result, ensure_ascii=False).encode("utf-8")) <= MAX_SEMANTIC_PEER_CONTEXT_WORKFLOW_BYTES
    assert result["truncation"]["applied"] is True
    dart = result["context_pack"]["dart_filing"][0]
    assert dart["source_id"] == "report_sections:1"
    assert dart["metadata"]["availability"] == "summary_only"
    assert dart["metadata"]["rcept_no"] == "20250301000001"
    assert dart["metadata"]["fs_div_selection"] == {
        "requested": "OFS",
        "used": "CFS",
        "status": "fallback_requested_fs_div_unavailable",
    }
    assert [item["source_id"] for item in result["context_pack"]["company_ir"]] == ["ir-1"]
    assert [item["source_id"] for item in result["context_pack"]["web_news"]] == ["news-1"]


def test_investor_workflow_uses_non_overlapping_specialists():
    calls = []

    def dispatch(name, arguments):
        calls.append(name)
        return _envelope(name)

    result = investor_first_pass("00126380", 2025, dispatch=dispatch)

    assert "get_investor_signals" not in calls
    assert calls.count("get_financial_snapshot") == 1
    assert calls.count("search_disclosure_events") == 1
    assert calls.count("score_going_concern") == 1
    assert calls.count("get_audit_history") == 1
    assert {
        "financial_snapshot",
        "quality_of_earnings",
        "disclosure_events",
        "accounting_audit_risk",
    }.issubset(result["categories"])


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


def test_dict_error_cannot_claim_usable_quality():
    def dispatch(name, _arguments):
        return {
            "tool_name": name,
            "answer": "claimed success",
            "error": "bounded child failure",
            "data_quality": {
                "status": "usable",
                "grade": "A",
                "dataset_version": "v1",
                "schema_version": "v1",
            },
            "evidence": [],
        }

    result = group_audit_scope("00126380", 2025, dispatch=dispatch)

    child = result["children"][0]
    assert child["data_quality"]["status"] == "error"
    assert "bounded child failure" in child["data_quality"]["limitations"]
    assert result["status"] == "error"


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


def test_workflow_has_deterministic_global_output_budget_for_huge_envelopes():
    huge_fact = {"payload": "x" * 4_000_000}

    def dispatch(name, _arguments):
        envelope = _envelope(name)
        envelope.answer = "a" * 4_000_000
        envelope.confirmed_facts = [huge_fact] * 3
        envelope.evidence[0].excerpt = "e" * 4_000_000
        return envelope

    first = investor_first_pass("00126380", 2025, dispatch=dispatch)
    second = investor_first_pass("00126380", 2025, dispatch=dispatch)
    encoded = json.dumps(first, ensure_ascii=False, sort_keys=True)

    assert first == second
    assert len(encoded) <= MAX_WORKFLOW_OUTPUT_CHARACTERS
    assert first["status"] == "limited"
    assert "workflow_output_truncated" in first["limitations"]
    assert all(child["tool_name"] for child in first["children"])
    assert all(child["data_quality"]["status"] for child in first["children"])
    assert all(child["evidence"] for child in first["children"])


def test_workflow_global_budget_handles_twelve_megabyte_plain_dict():
    huge = "z" * 12_000_000

    def dispatch(name, _arguments):
        return {
            "tool_name": name,
            "answer": huge,
            "verdict": huge,
            "confirmed_facts": [{"huge": huge}],
            "analysis": [],
            "evidence": [
                {
                    "source_label": "DART",
                    "source_url": "https://dart.fss.or.kr/example",
                    "excerpt": huge,
                }
            ],
            "data_quality": {
                "status": "usable",
                "grade": "A",
                "dataset_version": "v1",
                "schema_version": "v1",
                "covered_years": [2025],
                "missing_fields": [],
                "limitations": [],
            },
            "warnings": [],
            "next_checks": [],
        }

    result = accounting_policy_peer_review(
        "00126380", 2025, dispatch=dispatch
    )
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True)

    assert len(encoded) <= MAX_WORKFLOW_OUTPUT_CHARACTERS
    assert result["status"] == "limited"
    assert "workflow_output_truncated" in result["limitations"]


def test_workflow_budget_never_raises_for_reviewer_nested_shape():
    nested_facts = [
        {f"field_{field}": "x" * 1_000 for field in range(50)}
        for _ in range(20)
    ]

    def dispatch(name, _arguments):
        return {
            "tool_name": name,
            "answer": "answer",
            "verdict": "usable",
            "confirmed_facts": nested_facts,
            "analysis": [],
            "evidence": [
                {
                    "source_label": "DART",
                    "source_url": "https://dart.fss.or.kr/reviewer",
                    "rcept_no": "20250312000001",
                }
            ],
            "data_quality": {
                "status": "usable",
                "grade": "A",
                "dataset_version": "v1",
                "schema_version": "v1",
                "covered_years": [2025],
                "missing_fields": [],
                "limitations": [],
            },
            "warnings": [],
            "next_checks": [],
        }

    first = investor_first_pass("00126380", 2025, dispatch=dispatch)
    second = investor_first_pass("00126380", 2025, dispatch=dispatch)
    encoded = json.dumps(first, ensure_ascii=False, sort_keys=True)

    assert first == second
    assert len(encoded) <= MAX_WORKFLOW_OUTPUT_CHARACTERS
    assert len(encoded.encode("utf-8")) <= MAX_WORKFLOW_OUTPUT_BYTES
    assert first["status"] == "limited"
    assert "workflow_output_truncated" in first["limitations"]
    assert all(child["tool_name"] for child in first["children"])
    assert all(child["data_quality"]["status"] == "usable"
               for child in first["children"])
    assert all(child["evidence"] for child in first["children"])


def test_workflow_budget_is_deterministic_for_adversarial_unicode_children():
    statuses = ["error", "missing", "limited", "usable"]
    call_index = 0

    def dispatch(name, _arguments):
        nonlocal call_index
        status = statuses[call_index]
        call_index += 1
        return {
            "tool_name": name,
            "answer": "가🙂" * 2_000_000,
            "verdict": "검토🙂" * 500_000,
            "confirmed_facts": [
                {f"필드_{idx}": "값🙂" * 500 for idx in range(50)}
                for _ in range(20)
            ],
            "analysis": [],
            "evidence": [
                {
                    "source_label": "다트🙂" * 200,
                    "source_url": "https://dart.fss.or.kr/" + "근거🙂" * 500,
                    "rcept_no": "20250312000001",
                    "excerpt": "증거🙂" * 500_000,
                }
            ],
            "data_quality": {
                "status": status,
                "grade": None,
                "dataset_version": "자료🙂" * 100,
                "schema_version": "스키마🙂" * 100,
                "covered_years": [2025],
                "missing_fields": [],
                "limitations": [],
            },
            "warnings": [],
            "next_checks": [],
        }

    first = accounting_policy_peer_review(
        "00126380", 2025, dispatch=dispatch
    )
    call_index = 0
    second = accounting_policy_peer_review(
        "00126380", 2025, dispatch=dispatch
    )
    encoded = json.dumps(first, ensure_ascii=False, sort_keys=True)

    assert first == second
    assert len(encoded.encode("utf-8")) <= MAX_WORKFLOW_OUTPUT_BYTES
    assert first["status"] == "error"
    assert "workflow_output_truncated" in first["limitations"]
    assert [
        child["data_quality"]["status"] for child in first["children"]
    ] == statuses
    assert all(child["evidence"] for child in first["children"])
