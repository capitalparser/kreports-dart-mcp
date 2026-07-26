"""Compatibility exports for MCP models.

Input models live exclusively in :mod:`kreports.mcp.input_models`.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from kreports.mcp.catalog import TOOL_CATALOG
from kreports.mcp.input_models import *  # noqa: F403 - public compatibility


class KReportsToolResponse(BaseModel):
    """Legacy structured-content container retained for import compatibility."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)
    meta: dict[str, Any] = Field(default_factory=dict, alias="_meta")


TOOL_INPUT_MODELS: dict[str, type[BaseModel]] = {
    name: spec.input_model for name, spec in TOOL_CATALOG.items()
}
