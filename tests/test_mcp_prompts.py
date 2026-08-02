from __future__ import annotations

import pytest

from kreports.mcp.prompts import PromptRequestError, get_prompt, list_prompts


EXPECTED_NAMES = {
    "investor_first_pass",
    "audit_acceptance_review",
    "group_audit_scope",
    "accounting_policy_peer_review",
    "semantic_peer_context_review",
}


def test_workflow_prompt_names_are_stable():
    assert {prompt.name for prompt in list_prompts()} == EXPECTED_NAMES


@pytest.mark.parametrize("name", sorted(EXPECTED_NAMES))
def test_prompts_preserve_evidence_and_prohibit_unsupported_conclusions(name):
    result = get_prompt(name, {"company": "00126380", "year": "2025"})
    text = result.messages[0].content.text

    assert "DART" in text
    assert "링크" in text
    assert "사실" in text and "분석" in text and "한계" in text
    assert "missing" in text and "error" in text
    assert "감사의견 결론" in text
    assert "투자 추천" in text
    assert "QSC" in text
    assert "회계정책" in text
    assert "fabricat" in text.lower() or "날조" in text


def test_prompt_arguments_are_bounded_and_unknown_prompt_fails_closed():
    with pytest.raises(PromptRequestError, match="unknown_prompt"):
        get_prompt("not-a-prompt", {"company": "x"})
    with pytest.raises(PromptRequestError, match="invalid_argument"):
        get_prompt(
            "investor_first_pass",
            {"company": "x" * 300, "year": "2025"},
        )


def test_semantic_peer_context_prompt_declares_source_and_statement_boundaries():
    result = get_prompt(
        "semantic_peer_context_review",
        {"company": "00126380", "year": "2025"},
    )
    text = result.messages[0].content.text

    assert "semantic_peer_context_review" in text
    assert "get_semantic_company_context" in text
    assert "compare_peer_accounting_notes" in text
    assert "DART → company IR → web/news → LLM" in text
    assert "confirmed facts" in text
    assert "management claims" in text
    assert "external context" in text
    assert "analysis" in text
    assert "caller-supplied" in text
    assert "summary_only" in text
    assert "fs_div_selection" in text
