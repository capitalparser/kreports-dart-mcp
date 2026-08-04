from __future__ import annotations

from pathlib import Path


def test_semantic_workflow_docs_preserve_read_only_source_and_peer_boundaries():
    text = Path("docs/semantic-layer.md").read_text(encoding="utf-8")

    for expected in (
        "semantic_peer_context_review",
        "include_semantic_context=true",
        "include_note_comparison=true",
        "DART → company IR → web/news → LLM",
        "confirmed facts",
        "management claims",
        "external context",
        "analysis",
        "caller-supplied",
        "summary_only",
        "fs_div_selection",
        "read-only",
        "60,000 UTF-8",
        "100,000 UTF-8",
        "truncation.applied=true",
    ):
        assert expected in text


def test_readme_has_no_stale_32_tool_reference():
    text = Path("README.md").read_text(encoding="utf-8")

    assert "32 catalog-bound MCP tools" not in text
    assert "32개 도구" not in text
    assert "32-tool" not in text
    assert "MCP Tools (34)" in text
    assert "MCP 도구 (34개)" in text
