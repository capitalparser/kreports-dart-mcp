"""Feature-specific quality ledger and read-only release checks."""
from __future__ import annotations

from typing import Any

__all__ = [
    "company_year_quality",
    "rebuild_company_year_quality",
]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from kreports.quality import company_year

        return getattr(company_year, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
