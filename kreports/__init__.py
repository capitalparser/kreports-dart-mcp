"""KReports public package entrypoint."""

from importlib import import_module as _import_module
import os as _os


_os.environ.setdefault("KREPORTS_HEADLESS", "1")
_os.environ.setdefault("DART_HEADLESS", _os.environ.get("KREPORTS_HEADLESS", "1"))

_PUBLIC_API = {
    "search_company": "kreports.analysis.api",
    "get_company": "kreports.analysis.api",
    "get_financial_snapshot": "kreports.analysis.api",
    "score_going_concern": "kreports.analysis.api",
    "detect_restatement": "kreports.analysis.api",
    "get_accounting_policy": "kreports.analysis.api",
    "get_audit_history": "kreports.analysis.api",
    "get_subsidiary_auditors": "kreports.analysis.api",
    "get_industry_aggregates": "kreports.analysis.api",
    "compare_to_industry": "kreports.analysis.api",
    "get_business_overview": "kreports.analysis.api",
    "resolve_corp_code": "kreports.analysis.api",
}


def __getattr__(name: str):
    module_name = _PUBLIC_API.get(name)
    if module_name is None:
        raise AttributeError(f"module 'kreports' has no attribute {name!r}")

    module = _import_module(module_name)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(list(globals().keys()) + list(_PUBLIC_API.keys()))


__all__ = list(_PUBLIC_API.keys())
__version__ = "0.1.0"
