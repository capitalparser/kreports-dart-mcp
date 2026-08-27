"""Final cleanup boundary for user-facing structured chatbot packs."""
from __future__ import annotations

from typing import Any

from kreports.mcp.chatbot_contracts import ChatbotViewV1
from kreports.mcp.chatbot_user_experience import (
    build_user_visualization_pack,
)
from kreports.mcp.visual_contracts import build_visualization_pack


_TECHNICAL_WARNING_PREFIXES = (
    "chatbot_display:",
    "peer_chart_suppressed:",
    "dcf_candidate_chart_suppressed:",
)


def build_clean_user_visualization_pack(
    view: ChatbotViewV1,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Remove presentation diagnostics before the UI sees warnings."""
    pack = build_user_visualization_pack(view, result)
    pack.pop("resource_uri", None)

    def visible(values: list[Any]) -> list[str]:
        output: list[str] = []
        for value in values:
            text = str(value or "").strip()
            if not text or text.startswith(_TECHNICAL_WARNING_PREFIXES):
                continue
            if text not in output:
                output.append(text)
        return output

    pack["warnings"] = visible(list(pack.get("warnings") or []))
    pack["limitations"] = visible(list(pack.get("limitations") or []))
    quality = pack.get("data_quality")
    if isinstance(quality, dict):
        quality["limitations"] = visible(
            list(quality.get("limitations") or [])
        )
    rebuilt = build_visualization_pack(pack)
    return rebuilt.model_dump(mode="json")
