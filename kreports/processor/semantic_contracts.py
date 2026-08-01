"""Additive, provenance-preserving semantic evidence contracts.

These contracts sit above the existing parsers.  They never replace raw text
or normalized section rows, and they intentionally use only deterministic
heading/keyword rules so callers can distinguish extraction from analysis.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


Availability = Literal["available", "missing", "summary_only", "unavailable"]

_BUSINESS_TAG_RULES: dict[str, tuple[str, ...]] = {
    "products_services": ("제품", "서비스", "상품", "수주", "솔루션"),
    "customers_markets": ("고객", "시장", "판매", "수출", "해외"),
    "raw_materials": ("원재료", "원료", "소재", "조달"),
    "facilities_capacity": ("생산설비", "설비", "공장", "생산능력", "가동률"),
    "risks": ("위험", "리스크", "환율", "변동성", "불확실성"),
}

_NOTE_TOPIC_RULES: dict[str, tuple[str, ...]] = {
    "revenue": ("수익", "매출"),
    "leases": ("리스", "사용권자산"),
    "financial_instruments": ("금융상품", "금융자산", "금융부채", "파생상품"),
    "related_parties": ("특수관계", "관계회사"),
    "provisions_contingencies": ("충당부채", "우발", "소송"),
    "impairment": ("손상", "회수가능액"),
    "subsidiaries": ("종속기업", "연결대상"),
    "subsequent_events": ("보고기간후", "후발사건", "후속사건"),
    "accounting_policies": ("회계정책", "회계처리방침"),
}


class SemanticEvidence(BaseModel):
    """A compact semantic pointer back to one filing section or chapter."""

    model_config = ConfigDict(extra="forbid")

    corp_code: str = Field(min_length=8, max_length=8)
    bsns_year: int = Field(ge=1900, le=2100)
    source_document_id: int | None = Field(default=None, ge=1)
    rcept_no: str | None = None
    section_key: str = Field(min_length=1)
    source_locator: str = Field(min_length=1)
    parser_version: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    availability: Availability
    extraction_method: str = Field(min_length=1)
    topic: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)
    section_title: str | None = None


class BusinessSemanticProfile(BaseModel):
    """Deterministic business tags and the section evidence that produced them."""

    model_config = ConfigDict(extra="forbid")

    corp_code: str = Field(min_length=8, max_length=8)
    bsns_year: int = Field(ge=1900, le=2100)
    source_document_id: int | None = Field(default=None, ge=1)
    rcept_no: str | None = None
    section_key: str = "business_profile"
    source_locator: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    availability: Availability
    extraction_method: str = Field(min_length=1)
    tags: dict[str, list[str]] = Field(default_factory=dict)
    evidence: list[SemanticEvidence] = Field(default_factory=list)
    parser_version: str = "semantic-v1"


class NoteSemanticItem(SemanticEvidence):
    """Semantic evidence specialized for an accounting-note chapter."""

    note_no: str | None = None
    note_title: str | None = None


def _matches(text: str, keywords: tuple[str, ...]) -> bool:
    normalized = (text or "").replace(" ", "")
    return any(keyword.replace(" ", "") in normalized for keyword in keywords)


def normalize_note_topic(title: str, body: str) -> str:
    """Return a stable note topic, retaining ``other_note`` when unknown."""
    if _matches(title or "", _NOTE_TOPIC_RULES["accounting_policies"]):
        return "accounting_policies"
    value = f"{title or ''}\n{body or ''}"
    for topic, keywords in _NOTE_TOPIC_RULES.items():
        if _matches(value, keywords):
            return topic
    return "other_note"


def build_business_semantic_profile(
    sections: dict[str, dict],
    *,
    corp_code: str,
    bsns_year: int,
    source_document_id: int | None,
    rcept_no: str | None,
    parser_version: str = "semantic-v1",
) -> BusinessSemanticProfile:
    """Derive bounded business tags while retaining original section excerpts."""
    tags: dict[str, list[str]] = {}
    evidence: list[SemanticEvidence] = []
    for section_key, section in sections.items():
        title = str(section.get("title") or "")
        body = str(section.get("body_text") or "")
        if not body:
            continue
        for topic, keywords in _BUSINESS_TAG_RULES.items():
            if not _matches(f"{title}\n{body}", keywords):
                continue
            tags.setdefault(topic, []).append(topic)
            evidence.append(
                SemanticEvidence(
                    corp_code=corp_code,
                    bsns_year=bsns_year,
                    source_document_id=source_document_id,
                    rcept_no=rcept_no,
                    section_key=section_key,
                    source_locator=f"report_sections:{rcept_no or 'unknown'}:{section_key}",
                    parser_version=parser_version,
                    confidence=0.8,
                    availability="available",
                    extraction_method="heading_keyword",
                    topic=topic,
                    excerpt=body,
                    section_title=title or None,
                )
            )
    return BusinessSemanticProfile(
        corp_code=corp_code,
        bsns_year=bsns_year,
        source_document_id=source_document_id,
        rcept_no=rcept_no,
        source_locator=f"source_documents:{source_document_id or 'unknown'}",
        confidence=0.8 if evidence else 0.0,
        availability="available" if evidence else "unavailable",
        extraction_method="heading_keyword",
        tags={topic: list(dict.fromkeys(values)) for topic, values in tags.items()},
        evidence=evidence,
        parser_version=parser_version,
    )


def build_audit_semantic_evidence(
    sections: dict[str, dict],
    *,
    corp_code: str,
    bsns_year: int,
    source_document_id: int | None,
    rcept_no: str | None,
    parser_version: str = "semantic-v1",
) -> list[SemanticEvidence]:
    """Wrap normalized audit sections without reinterpreting audit conclusions."""
    evidence: list[SemanticEvidence] = []
    for section_key, section in sections.items():
        body = str(section.get("body_text") or "")
        if not body:
            continue
        evidence.append(
            SemanticEvidence(
                corp_code=corp_code,
                bsns_year=bsns_year,
                source_document_id=source_document_id,
                rcept_no=rcept_no,
                section_key=section_key,
                source_locator=f"report_sections:{rcept_no or 'unknown'}:{section_key}",
                parser_version=parser_version,
                confidence=0.9 if section_key == "kam" else 0.8,
                availability="available",
                extraction_method="audit_section_parser",
                topic=section_key,
                excerpt=body,
                section_title=str(section.get("title") or "") or None,
            )
        )
    return evidence
