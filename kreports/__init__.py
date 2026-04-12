"""
KReports — Korean Financial Intelligence MCP.

DART를 매년 수작업으로 뒤지던 Big4 감사인이 만든 재무 인텔리전스 MCP.

공개 API:
    from kreports import (
        search_company, get_company,
        get_financial_snapshot,
        score_going_concern,
        detect_restatement,
        get_accounting_policy,
        get_audit_history,
        get_subsidiary_auditors,
        get_industry_aggregates,
        resolve_corp_code,
    )

MCP 서버:
    python -m kreports.mcp
    kreports serve
"""
import os as _os

# headless flag 자동 설정 (streamlit 캐시 경고 방지)
_os.environ.setdefault("KREPORTS_HEADLESS", "1")
# backward compat
_os.environ.setdefault("DART_HEADLESS", _os.environ.get("KREPORTS_HEADLESS", "1"))

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
    get_business_overview,
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
    "get_business_overview",
    "resolve_corp_code",
]

__version__ = "0.1.0"
