"""Backward-compatible adapter over the typed MCP catalog and dispatcher."""
from __future__ import annotations

import json
from typing import Any, Callable

from kreports.mcp.catalog import TOOL_CATALOG
from kreports.mcp.dispatch import _attach_meta, legacy_result, list_mcp_tools


ALL_TOOLS = list_mcp_tools()


def _compat_handler(name: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    return lambda arguments: legacy_result(name, arguments)


HANDLERS = {name: _compat_handler(name) for name in TOOL_CATALOG}


def call_tool(name: str, arguments: dict[str, Any] | None) -> str:
    """Retain the established JSON-string Python API for existing callers."""
    return json.dumps(
        legacy_result(name, arguments),
        ensure_ascii=False,
        default=str,
    )


__all__ = ["ALL_TOOLS", "HANDLERS", "_attach_meta", "call_tool"]
