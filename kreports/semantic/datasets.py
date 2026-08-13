"""Cached dataset semantics, including what an absent row can prove."""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping


@dataclass(frozen=True)
class DatasetDefinition:
    key: str
    grain: str
    unique_key: tuple[str, ...]
    year_field: str
    source_class: Literal["dart_api", "derived_from_filing", "derived_from_cache"]
    absence_semantics: Literal[
        "cache_absence_not_filing_absence",
        "absence_can_prove_nonexistence",
        "unknown",
    ]


_DATASETS = {
    "financial_facts": DatasetDefinition("financial_facts", "company-year-report-fs-statement-account", ("corp_code", "bsns_year", "reprt_code", "fs_div", "sj_div", "account_id"), "bsns_year", "dart_api", "cache_absence_not_filing_absence"),
    "financial_facts_compact": DatasetDefinition("financial_facts_compact", "company-year-fs-metric", ("corp_code", "bsns_year", "fs_div", "metric_key"), "bsns_year", "derived_from_cache", "cache_absence_not_filing_absence"),
    "audit_fees": DatasetDefinition("audit_fees", "company-year", ("corp_code", "bsns_year"), "bsns_year", "dart_api", "cache_absence_not_filing_absence"),
    "audit_matter_items": DatasetDefinition("audit_matter_items", "company-year-receipt-matter", ("rcept_no", "matter_type", "section_ordinal"), "bsns_year", "derived_from_filing", "cache_absence_not_filing_absence"),
}

DATASETS: Mapping[str, DatasetDefinition] = MappingProxyType(_DATASETS)


def dataset_definition(dataset_key: str) -> DatasetDefinition:
    """Return one registered dataset or fail closed for an unknown key."""
    return DATASETS[dataset_key]
