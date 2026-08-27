"""Additive MCP input extensions for customizable peer workflows.

The frozen 34-entry catalog (33 public tools and one operator-opt-in tool) is
preserved; selected tools receive richer input models without changing tool
names or handler identities.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Literal

from pydantic import Field, field_validator

from kreports.analysis.peer_criteria import PeerCriteriaProfile
from kreports.mcp import catalog as catalog_module
from kreports.mcp import input_models as models
from kreports.mcp.catalog import TOOL_CATALOG
from kreports.mcp.schema_utils import legacy_compatible_schema


PeerCriteriaValue = PeerCriteriaProfile | list[str] | None


class EnhancedSelectPeerGroupInput(models.SelectPeerGroupInput):
    year: int | None = Field(
        None,
        ge=2000,
        le=2100,
        description=(
            "동종업종 선정 기준 사업연도. "
            "생략하면 최신 가용 연도를 사용한다."
        ),
    )


class EnhancedCompareToIndustryMultiInput(
    models.CompareToIndustryMultiInput
):
    year: int | None = Field(
        None,
        ge=2000,
        le=2100,
        description=(
            "커스텀 cohort 선정 기준 사업연도. "
            "생략하면 최신 가용 연도를 사용한다."
        ),
    )
    peer_criteria: PeerCriteriaValue = Field(
        None,
        description=(
            "KSIC/sector/규모/포함·제외기업/coverage 기반 "
            "동종업종 선정 커스터마이징 기준."
        ),
    )
    peer_limit: int = Field(
        50,
        ge=1,
        le=200,
        description=(
            "사내 챗봇에 표시할 peer 최대 개수. "
            "통계 모집단 크기에는 영향을 주지 않는다."
        ),
    )


class EnhancedComparePeerAuditFeesInput(
    models.ComparePeerAuditFeesInput
):
    peer_criteria: PeerCriteriaValue = Field(
        None,
        description=(
            "동종업종 선정 커스터마이징 기준. "
            "select_peer_group과 동일한 프로필을 사용한다."
        ),
    )


class EnhancedComparePeerRiskProfileInput(
    models.ComparePeerRiskProfileInput
):
    peer_criteria: PeerCriteriaValue = Field(
        None,
        description="동종업종 선정 커스터마이징 기준.",
    )


class EnhancedComparePeerAccountingPoliciesInput(
    models.ComparePeerAccountingPoliciesInput
):
    peer_criteria: PeerCriteriaValue = Field(
        None,
        description="동종업종 선정 커스터마이징 기준.",
    )


class EnhancedComparePeerKamTopicsInput(
    models.ComparePeerKamTopicsInput
):
    peer_criteria: PeerCriteriaValue = Field(
        None,
        description="동종업종 선정 커스터마이징 기준.",
    )


class EnhancedComparePeerAuditReportMattersInput(
    models.ComparePeerAuditReportMattersInput
):
    peer_criteria: PeerCriteriaValue = Field(
        None,
        description="동종업종 선정 커스터마이징 기준.",
    )


class EnhancedComparePeerAuditProceduresInput(
    models.ComparePeerAuditProceduresInput
):
    peer_criteria: PeerCriteriaValue = Field(
        None,
        description="동종업종 선정 커스터마이징 기준.",
    )


class EnhancedSearchDatasetInput(models.SearchDatasetInput):
    offset: int = Field(
        0,
        ge=0,
        le=100_000,
        description="회사 단위 페이지 offset.",
    )
    search_mode: Literal[
        "exact",
        "normalized",
        "synonym",
    ] = Field(
        "exact",
        description=(
            "exact=원문 부분일치, normalized=공백·구두점 정규화, "
            "synonym=정규화와 통제된 동의어 확장."
        ),
    )
    synonyms: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="synonym 모드에서 추가할 사용자 동의어.",
    )

    @field_validator("synonyms")
    @classmethod
    def normalize_synonyms(
        cls,
        values: list[str],
    ) -> list[str]:
        return list(dict.fromkeys(
            value.strip()
            for value in values
            if value.strip()
        ))


_EXTENSIONS = {
    "select_peer_group": EnhancedSelectPeerGroupInput,
    "compare_to_industry_multi": EnhancedCompareToIndustryMultiInput,
    "compare_peer_audit_fees": EnhancedComparePeerAuditFeesInput,
    "compare_peer_risk_profile": EnhancedComparePeerRiskProfileInput,
    "compare_peer_accounting_policies": (
        EnhancedComparePeerAccountingPoliciesInput
    ),
    "compare_peer_kam_topics": EnhancedComparePeerKamTopicsInput,
    "compare_peer_audit_report_matters": (
        EnhancedComparePeerAuditReportMattersInput
    ),
    "search_dataset": EnhancedSearchDatasetInput,
    "compare_peer_audit_procedures": (
        EnhancedComparePeerAuditProceduresInput
    ),
}


def install_catalog_extensions() -> None:
    """Install richer schemas idempotently without changing tool exposure."""
    catalog_module._legacy_compatible_schema = legacy_compatible_schema

    for name, input_model in _EXTENSIONS.items():
        spec = TOOL_CATALOG[name]
        if spec.input_model is input_model:
            continue
        TOOL_CATALOG[name] = replace(
            spec,
            input_model=input_model,
        )
