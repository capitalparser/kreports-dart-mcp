"""
kreports.analysis — 공개 분석 API.

dict/list[dict] 반환, JSON-safe.
"""

from kreports.analysis.api import (
    search_company,
    get_company,
    get_financial_snapshot,
    score_going_concern,
    detect_restatement,
    get_accounting_policy,
    get_audit_history,
    get_subsidiary_auditors,
    get_industry_aggregates,
    resolve_corp_code,
)

__all__ = [
    "search_company",
    "get_company",
    "get_financial_snapshot",
    "score_going_concern",
    "detect_restatement",
    "get_accounting_policy",
    "get_audit_history",
    "get_subsidiary_auditors",
    "get_industry_aggregates",
    "resolve_corp_code",
]
