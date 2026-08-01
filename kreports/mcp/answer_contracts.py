"""Typed, source-separated contracts for evidence-grounded LLM answers."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


SourceClass = Literal["dart_filing", "company_ir", "web_news", "llm_analysis"]


class _ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContextEvidenceV1(_ContractModel):
    source_class: SourceClass
    source_id: str = Field(min_length=1, max_length=300)
    title: str | None = Field(default=None, max_length=500)
    excerpt: str = Field(min_length=1, max_length=4000)
    url: str | None = Field(default=None, max_length=2000)
    checksum: str | None = Field(default=None, max_length=128)
    claim_key: str | None = Field(default=None, max_length=200)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SuppliedExternalEvidenceV1(_ContractModel):
    """Evidence supplied by the caller; it is never fetched by KReports."""

    source_class: Literal["company_ir", "web_news"]
    source_id: str = Field(min_length=1, max_length=300)
    excerpt: str = Field(min_length=1, max_length=4000)
    title: str | None = Field(default=None, max_length=500)
    url: str | None = Field(default=None, max_length=2000)
    checksum: str | None = Field(default=None, max_length=128)
    claim_key: str | None = Field(default=None, max_length=200)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def as_context_evidence(self) -> ContextEvidenceV1:
        return ContextEvidenceV1(**self.model_dump())


class MissingEvidenceV1(_ContractModel):
    evidence_type: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=500)
    source_class: SourceClass | None = None


class SourceConflictV1(_ContractModel):
    claim_key: str = Field(min_length=1, max_length=200)
    source_ids: list[str] = Field(min_length=2, max_length=20)
    source_classes: list[SourceClass] = Field(min_length=2, max_length=4)
    status: Literal["potential_conflict"] = "potential_conflict"
    note: str = "Different source-class statements share this claim key; no resolution is inferred."

    @field_validator("source_ids", "source_classes")
    @classmethod
    def unique_sorted(cls, value: list[str]) -> list[str]:
        return sorted(dict.fromkeys(value))


class PeerNoteComparisonV1(_ContractModel):
    source_class: Literal["dart_filing"] = "dart_filing"
    data: dict[str, Any]


class LLMAnalysisV1(_ContractModel):
    statement: str = Field(min_length=1, max_length=4000)
    source_ids: list[str] = Field(min_length=1, max_length=20)

    @field_validator("source_ids")
    @classmethod
    def unique_sorted_source_ids(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        if not cleaned:
            raise ValueError("source_ids must not be blank")
        return sorted(dict.fromkeys(cleaned))


class ContextPackV1(_ContractModel):
    schema_version: Literal["context_pack.v1"] = "context_pack.v1"
    subject: dict[str, Any]
    year: int
    read_only: Literal[True] = True
    source_precedence: list[SourceClass]
    dart_filing: list[ContextEvidenceV1] = Field(default_factory=list)
    company_ir: list[ContextEvidenceV1] = Field(default_factory=list)
    web_news: list[ContextEvidenceV1] = Field(default_factory=list)
    llm_analysis: list[LLMAnalysisV1] = Field(default_factory=list)
    peer_note_comparison: PeerNoteComparisonV1 | None = None
    missing_evidence: list[MissingEvidenceV1] = Field(default_factory=list)
    conflicts: list[SourceConflictV1] = Field(default_factory=list)
    llm_guidance: list[str] = Field(default_factory=list)


class SourceSeparatedAnswerV1(_ContractModel):
    schema_version: Literal["source_separated_answer.v1"] = "source_separated_answer.v1"
    confirmed_facts: list[ContextEvidenceV1] = Field(default_factory=list)
    management_claims: list[ContextEvidenceV1] = Field(default_factory=list)
    external_context: list[ContextEvidenceV1] = Field(default_factory=list)
    analysis: list[LLMAnalysisV1] = Field(default_factory=list)
    counterpoints: list[LLMAnalysisV1] = Field(default_factory=list)
    missing_evidence: list[MissingEvidenceV1] = Field(default_factory=list)
    conflicts: list[SourceConflictV1] = Field(default_factory=list)
    sources: list[ContextEvidenceV1] = Field(default_factory=list)
    llm_guidance: list[str] = Field(default_factory=list)


def _validate_analysis_sources(
    items: list[LLMAnalysisV1],
    source_ids: set[str],
) -> list[LLMAnalysisV1]:
    unknown = sorted({source_id for item in items for source_id in item.source_ids} - source_ids)
    if unknown:
        raise ValueError(f"unknown source_ids: {', '.join(unknown)}")
    return items


def build_source_separated_answer(
    context_pack: ContextPackV1 | dict[str, Any],
    *,
    analysis: list[LLMAnalysisV1 | dict[str, Any]] | None = None,
    counterpoints: list[LLMAnalysisV1 | dict[str, Any]] | None = None,
) -> SourceSeparatedAnswerV1:
    """Build an answer shell that cannot blend source classes into facts."""
    pack = ContextPackV1.model_validate(context_pack)
    sources = [*pack.dart_filing, *pack.company_ir, *pack.web_news]
    source_ids = {item.source_id for item in sources}
    analysis_items = [LLMAnalysisV1.model_validate(item) for item in (analysis or pack.llm_analysis)]
    counterpoint_items = [LLMAnalysisV1.model_validate(item) for item in (counterpoints or [])]
    _validate_analysis_sources(analysis_items, source_ids)
    _validate_analysis_sources(counterpoint_items, source_ids)
    return SourceSeparatedAnswerV1(
        confirmed_facts=pack.dart_filing,
        management_claims=pack.company_ir,
        external_context=pack.web_news,
        analysis=analysis_items,
        counterpoints=counterpoint_items,
        missing_evidence=pack.missing_evidence,
        conflicts=pack.conflicts,
        sources=sources,
        llm_guidance=pack.llm_guidance,
    )
