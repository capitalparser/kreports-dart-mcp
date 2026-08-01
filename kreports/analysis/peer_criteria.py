"""Typed, bounded policy for explainable peer selection.

The profile deliberately contains only deterministic dimensions.  Narrative
similarity (business tags or an LLM) may be attached later as an *explanation*
or a ranking adapter, but it must not silently change the legal/industry peer
universe.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


PeerSelectionMode = Literal["strict", "adaptive", "ranked"]
IndustryBasis = Literal["ksic", "sector_group", "custom_codes"]
SizeMetric = Literal["total_assets", "revenue", "employees"]
RequiredFeature = Literal[
    "financials",
    "business_report",
    "audit_report",
    "audit_fees",
    "notes",
    "kam",
]

_SECTOR_GROUPS = frozenset({"financial", "holding", "real_estate", "general", "unknown"})
_WEIGHT_DIMENSIONS = frozenset({"industry", "sector", "size", "business", "coverage"})


class PeerCriteriaProfile(BaseModel):
    """User-controlled peer selection criteria with bounded dimensions.

    ``included_corp_codes`` is an explicit allow-list extension.  In
    ``custom_codes`` mode it is the candidate universe and therefore may not
    be empty.  ``excluded_corp_codes`` always wins over inclusion.
    """

    model_config = ConfigDict(extra="forbid")

    mode: PeerSelectionMode = "adaptive"
    industry_basis: IndustryBasis = "ksic"
    prefix_len: int = Field(3, ge=2, le=5)
    fallback_prefix_len: int | None = Field(2, ge=2, le=5)
    excluded_sector_groups: list[str] = Field(default_factory=list)
    size_metric: SizeMetric | None = None
    size_log10_tolerance: float | None = Field(None, ge=0.5, le=3.0)
    required_business_tags: list[str] = Field(default_factory=list)
    excluded_corp_codes: list[str] = Field(default_factory=list)
    included_corp_codes: list[str] = Field(default_factory=list)
    required_features: list[RequiredFeature] = Field(default_factory=list)
    minimum_coverage: float = Field(0.0, ge=0.0, le=1.0)
    weights: dict[str, float] = Field(default_factory=dict)

    @field_validator("excluded_sector_groups")
    @classmethod
    def validate_sector_groups(cls, values: list[str]) -> list[str]:
        normalized = sorted({value.strip().lower() for value in values if value.strip()})
        unknown = set(normalized) - _SECTOR_GROUPS
        if unknown:
            raise ValueError(f"지원하지 않는 sector group: {', '.join(sorted(unknown))}")
        return normalized

    @field_validator("required_business_tags")
    @classmethod
    def normalize_tags(cls, values: list[str]) -> list[str]:
        return sorted({value.strip().lower() for value in values if value.strip()})

    @field_validator("included_corp_codes", "excluded_corp_codes")
    @classmethod
    def normalize_corp_codes(cls, values: list[str]) -> list[str]:
        normalized = sorted({value.strip() for value in values if value.strip()})
        if any(not value.isdigit() or len(value) != 8 for value in normalized):
            raise ValueError("corp_code는 8자리 숫자여야 합니다.")
        return normalized

    @field_validator("required_features")
    @classmethod
    def unique_features(cls, values: list[RequiredFeature]) -> list[RequiredFeature]:
        return list(dict.fromkeys(values))

    @field_validator("weights")
    @classmethod
    def validate_weights(cls, values: dict[str, float]) -> dict[str, float]:
        unknown = set(values) - _WEIGHT_DIMENSIONS
        if unknown:
            raise ValueError(f"지원하지 않는 가중치 차원: {', '.join(sorted(unknown))}")
        if any(not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0 for value in values.values()):
            raise ValueError("가중치는 0 이상의 숫자여야 합니다.")
        if sum(values.values()) > 1.0 + 1e-9:
            raise ValueError("가중치 합계는 1 이하여야 합니다.")
        return dict(sorted((str(key), float(value)) for key, value in values.items()))

    @model_validator(mode="after")
    def validate_combinations(self):
        if self.fallback_prefix_len is not None and self.fallback_prefix_len > self.prefix_len:
            raise ValueError("fallback_prefix_len은 prefix_len 이하여야 합니다.")
        if self.size_log10_tolerance is not None and self.size_metric is None:
            raise ValueError("size_log10_tolerance에는 size_metric이 필요합니다.")
        if self.industry_basis == "custom_codes" and not self.included_corp_codes:
            raise ValueError("custom_codes에는 included_corp_codes가 필요합니다.")
        overlap = set(self.included_corp_codes) & set(self.excluded_corp_codes)
        if overlap:
            raise ValueError("included_corp_codes와 excluded_corp_codes는 겹칠 수 없습니다.")
        return self

    def requested_policy(self) -> dict:
        """JSON-safe, explicit user intent for the response contract."""
        return self.model_dump(mode="json")


def coerce_peer_criteria(
    criteria: list[str] | PeerCriteriaProfile | dict | None,
    *,
    prefix_len_start: int,
    size_bucket_decade: float | None,
    exclude_other_sectors: bool,
) -> tuple[PeerCriteriaProfile, list[str] | dict | None, bool]:
    """Return a normalized profile, original criteria, and legacy flag.

    Legacy list criteria remains an informational alias; the historic keyword
    arguments continue to determine the equivalent deterministic profile.
    """
    if isinstance(criteria, PeerCriteriaProfile):
        return criteria, criteria.requested_policy(), False
    if isinstance(criteria, dict):
        profile = PeerCriteriaProfile.model_validate(criteria)
        return profile, criteria, False
    profile = PeerCriteriaProfile(
        prefix_len=prefix_len_start,
        fallback_prefix_len=2 if prefix_len_start > 2 else None,
        excluded_sector_groups=([] if not exclude_other_sectors else ["financial", "holding", "real_estate"]),
        size_metric="total_assets" if size_bucket_decade is not None else None,
        size_log10_tolerance=size_bucket_decade,
    )
    return profile, criteria, True
