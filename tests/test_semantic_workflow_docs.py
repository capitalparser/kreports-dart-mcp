from __future__ import annotations

from pathlib import Path


def test_semantic_workflow_docs_preserve_read_only_source_and_peer_boundaries():
    text = Path("docs/semantic-layer.md").read_text(encoding="utf-8")

    for expected in (
        "semantic_peer_context_review",
        "get_semantic_company_context",
        "compare_peer_accounting_notes",
        "DART → company IR → web/news → LLM",
        "confirmed facts",
        "management claims",
        "external context",
        "analysis",
        "caller-supplied",
        "summary_only",
        "fs_div_selection",
        "read-only",
    ):
        assert expected in text
