"""Compatibility exports for MCP models.

Input models live exclusively in :mod:`kreports.mcp.input_models`.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from kreports.mcp.catalog import TOOL_CATALOG
from kreports.mcp.input_models import *  # noqa: F403 - public compatibility


COMPARE_METRICS = Literal[
    "영업이익률",
    "순이익률",
    "부채비율",
    "ROE",
    "ROA",
    "자기자본비율",
    "매출성장률",
    "Beneish_M",
]
CompanyIdent = Annotated[
    str,
    Field(
        description="corp_code(8자리) / 종목코드(6자리) / 정확한 회사명 중 하나",
        min_length=1,
    ),
]
BsnsYear = Annotated[int, Field(ge=2000, le=2100, description="사업연도")]


class KReportsToolResponse(BaseModel):
    """Legacy structured-content container retained for import compatibility."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)
    meta: dict[str, Any] = Field(default_factory=dict, alias="_meta")


TOOL_INPUT_MODELS: dict[str, type[BaseModel]] = {
    name: spec.input_model for name, spec in TOOL_CATALOG.items()
}
