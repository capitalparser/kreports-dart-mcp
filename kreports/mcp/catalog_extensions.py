"""Additive MCP input extensions for customizable peer workflows.

The frozen 34-tool catalog is preserved; selected tools receive richer input
models without changing tool names or handler identities.
"""
from __future__ import annotations

from dataclasses import replace

from pydantic import Field

from kreports.analysis.peer_criteria import PeerCriteriaProfile
from kreports.mcp import input_models as models
from kreports.mcp.catalog import TOOL_CATALOG


PeerCriteriaValue = PeerCriteriaProfile | list[str] | None


class EnhancedSelectPeerGroupInput(models.SelectPeerGroupInput):
    year: int | None = Field(
        None,
        ge=2000,
        le=2100,
        description="동종업종 선정 기준 사업연도. 생략하면 최신 가용 연도를 사용한다.",
    )


class EnhancedCompareToIndustryMultiInput(models.CompareToIndustryMultiInput):
    year: int | None = Field(
        None,
        ge=2000,
        le=2100,
        description="커스텀 cohort 선정 기준 사업연도. 생략하면 최신 가용 연도를 사용한다.",
    )
    peer_criteria: PeerCriteriaValue = Field(
        None,
        description="KSIC/sector/규모/포함·제외기업/coverage 기반 동종업종 선정 커스터마이징 기준.",
    )


class EnhancedComparePeerAuditFeesInput(models.ComparePeerAuditFeesInput):
    peer_criteria: PeerCriteriaValue = Field(
        None,
        description="동종업종 선정 커스터마이징 기준. select_peer_group과 동일한 프로필을 사용한다.",
    )


class EnhancedComparePeerRiskProfileInput(models.ComparePeerRiskProfileInput):
    peer_criteria: PeerCriteriaValue = Field(None, description="동종업종 선정 커스터마이징 기준.")


class EnhancedComparePeerAccountingPoliciesInput(models.ComparePeerAccountingPoliciesInput):
    peer_criteria: PeerCriteriaValue = Field(None, description="동종업종 선정 커스터마이징 기준.")


class EnhancedComparePeerKamTopicsInput(models.ComparePeerKamTopicsInput):
    peer_criteria: PeerCriteriaValue = Field(None, description="동종업종 선정 커스터마이징 기준.")


class EnhancedComparePeerAuditReportMattersInput(models.ComparePeerAuditReportMattersInput):
    peer_criteria: PeerCriteriaValue = Field(None, description="동종업종 선정 커스터마이징 기준.")


class EnhancedComparePeerAuditProceduresInput(models.ComparePeerAuditProceduresInput):
    peer_criteria: PeerCriteriaValue = Field(None, description="동종업종 선정 커스터마이징 기준.")


_EXTENSIONS = {
    "select_peer_group": EnhancedSelectPeerGroupInput,
    "compare_to_industry_multi": EnhancedCompareToIndustryMultiInput,
    "compare_peer_audit_fees": EnhancedComparePeerAuditFeesInput,
    "compare_peer_risk_profile": EnhancedComparePeerRiskProfileInput,
    "compare_peer_accounting_policies": EnhancedComparePeerAccountingPoliciesInput,
    "compare_peer_kam_topics": EnhancedComparePeerKamTopicsInput,
    "compare_peer_audit_report_matters": EnhancedComparePeerAuditReportMattersInput,
    "compare_peer_audit_procedures": EnhancedComparePeerAuditProceduresInput,
}


def install_catalog_extensions() -> None:
    """Install richer schemas idempotently while keeping the 34-tool contract."""
    for name, input_model in _EXTENSIONS.items():
        spec = TOOL_CATALOG[name]
        if spec.input_model is input_model:
            continue
        TOOL_CATALOG[name] = replace(spec, input_model=input_model)
