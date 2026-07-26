"""Compatibility facade for the domain-owned KReports analysis API.

Public functions are imported directly, so every legacy facade export is the
exact implementation object from its owning domain module.  Keep this module
free of domain SQL and result-construction logic.
"""
# ruff: noqa: F401
from __future__ import annotations

from kreports.analysis._shared import (
    _clean_dict,
    _clean_value,
    _df_to_records,
    _display_text,
)
from kreports.analysis.audit_reporting import (
    get_accounting_policy,
    get_accounting_policy_changes,
    get_audit_history,
    get_audit_report_sections,
    get_kam_lifecycle,
    search_audit_procedures,
    search_audit_report_matters,
)
from kreports.analysis.company_profile import (
    get_business_overview,
    get_company,
    resolve_corp_code,
    search_company,
    search_dataset,
)
from kreports.analysis.financial_analysis import (
    detect_restatement,
    get_dcf_input_candidates,
    get_financial_snapshot,
    get_investor_signals,
    get_quality_of_earnings_pack,
    score_going_concern,
    search_disclosure_events,
)
from kreports.analysis.group_audit import get_subsidiary_auditors
from kreports.analysis.peer import resolve_fs_div_for_company, resolve_peers
from kreports.analysis.peer_benchmarks import (
    _is_big4,
    _quantile,
    build_audit_acceptance_pack,
    compare_peer_accounting_policies,
    compare_peer_audit_fees,
    compare_peer_audit_procedures,
    compare_peer_audit_report_matters,
    compare_peer_kam_topics,
    compare_peer_risk_profile,
    compare_to_industry,
    compare_to_industry_multi,
    estimate_audit_hours_proxy,
    get_industry_aggregates,
    get_industry_audit_landscape,
    select_peer_group,
)

__all__ = [
    "search_company",
    "get_company",
    "resolve_corp_code",
    "get_business_overview",
    "search_dataset",
    "get_financial_snapshot",
    "get_investor_signals",
    "score_going_concern",
    "detect_restatement",
    "get_quality_of_earnings_pack",
    "get_dcf_input_candidates",
    "search_disclosure_events",
    "get_accounting_policy",
    "get_audit_history",
    "get_audit_report_sections",
    "search_audit_report_matters",
    "search_audit_procedures",
    "get_kam_lifecycle",
    "get_accounting_policy_changes",
    "get_industry_aggregates",
    "compare_to_industry",
    "compare_to_industry_multi",
    "select_peer_group",
    "compare_peer_audit_fees",
    "compare_peer_risk_profile",
    "compare_peer_accounting_policies",
    "compare_peer_kam_topics",
    "compare_peer_audit_report_matters",
    "compare_peer_audit_procedures",
    "estimate_audit_hours_proxy",
    "build_audit_acceptance_pack",
    "get_industry_audit_landscape",
    "get_subsidiary_auditors",
]
