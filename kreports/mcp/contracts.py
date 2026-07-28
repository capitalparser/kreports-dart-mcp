"""Versioned professional answer contract for legacy MCP tool results."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from kreports.analysis.evidence import evidence_reference_fields


_CANONICAL_STATUSES = {"usable", "limited", "missing", "error"}
_QUALITY_STATUSES = _CANONICAL_STATUSES
_ADAPTER_VERSION = "legacy-result-adapter"

# Each public catalog tool has its own result-shape evidence.  Generic field
# names (notably items/results/inputs/assumptions) are deliberately scoped to
# the tools that actually produce them, rather than trusted globally.
_TOOL_PURPOSE_FIELDS: dict[str, dict[str, frozenset[str]]] = {
    "search_company": {"lists": frozenset({"results"}), "counts": frozenset({"count"})},
    "get_financial_snapshot": {"lists": frozenset({"rows"}), "counts": frozenset({"row_count"})},
    "score_going_concern": {"lists": frozenset({"factors"}), "maps": frozenset({"scorecard"})},
    "detect_restatement": {"lists": frozenset({"restatements"}), "counts": frozenset({"count"})},
    "get_accounting_policy": {"maps": frozenset({"items"}), "counts": frozenset({"item_count"})},
    "get_audit_history": {"lists": frozenset({"history"}), "counts": frozenset({"count"})},
    "get_subsidiary_auditors": {"lists": frozenset({"subsidiaries"}), "maps": frozenset({"consolidated_totals"}), "counts": frozenset({"count", "total"})},
    "compare_to_industry": {"lists": frozenset({"peers"}), "maps": frozenset({"distribution", "subject_metric"})},
    "get_business_overview": {"lists": frozenset({"insights"}), "maps": frozenset({"sections"})},
    "get_investor_signals": {"lists": frozenset({"recent_events"}), "maps": frozenset({"quality_snapshot", "accounting_risk", "event_counts"})},
    "select_peer_group": {"lists": frozenset({"peers"}), "maps": frozenset({"selection_policy"}), "counts": frozenset({"returned_peer_count"})},
    "compare_to_industry_multi": {"maps": frozenset({"results", "cohort_metadata"}), "counts": frozenset({"n_peers"})},
    "compare_peer_audit_fees": {"maps": frozenset({"subject_metrics", "benchmarks"}), "counts": frozenset({"peer_count"})},
    "compare_peer_risk_profile": {"maps": frozenset({"subject_metrics", "benchmarks"}), "counts": frozenset({"peer_count"})},
    "compare_peer_accounting_policies": {"maps": frozenset({"subject_items", "peer_coverage"}), "counts": frozenset({"peer_count", "peers_with_policy"})},
    "compare_peer_kam_topics": {"maps": frozenset({"topic_counts", "audit_report_events", "audit_report_sections"}), "counts": frozenset({"peer_count"})},
    "compare_peer_audit_report_matters": {"maps": frozenset({"matter_counts"}), "counts": frozenset({"peer_count"})},
    "search_dataset": {"lists": frozenset({"companies"}), "counts": frozenset({"total_records", "total_companies"})},
    "fetch_disclosure_on_demand": {"maps": frozenset({"document", "summary"})},
    "search_audit_report_matters": {"lists": frozenset({"companies"}), "counts": frozenset({"total_companies", "total_sections"})},
    "search_audit_procedures": {"lists": frozenset({"companies"}), "counts": frozenset({"total_companies", "total_procedures"})},
    "compare_peer_audit_procedures": {"maps": frozenset({"procedure_counts", "subject_procedures"}), "counts": frozenset({"peer_count"})},
    "get_kam_lifecycle": {"lists": frozenset({"events"}), "counts": frozenset({"event_count"})},
    "get_accounting_policy_changes": {"lists": frozenset({"changed_items"}), "counts": frozenset({"change_count"})},
    "get_quality_of_earnings_pack": {"lists": frozenset({"evidence", "signals", "audit_matter_flags"}), "maps": frozenset({"metrics"})},
    "get_dcf_input_candidates": {"lists": frozenset({"historical_actuals"}), "maps": frozenset({"candidate_assumptions"})},
    "search_disclosure_events": {"lists": frozenset({"events"}), "counts": frozenset({"total_events"})},
    "get_audit_report_sections": {"lists": frozenset({"sections"}), "counts": frozenset({"section_count"})},
    "estimate_audit_hours_proxy": {"lists": frozenset({"complexity_factors"}), "maps": frozenset({"complexity_components"})},
    "build_audit_acceptance_pack": {"lists": frozenset({"acceptance_signals"}), "maps": frozenset({"fee_benchmark", "risk_profile", "hours_proxy"})},
    "get_industry_audit_landscape": {"lists": frozenset({"auditor_market", "opinion_distribution"}), "maps": frozenset({"subject_auditor"})},
    "build_dcf_model_pack": {"lists": frozenset({"historical_actuals", "forecast"}), "maps": frozenset({"valuation", "sensitivity"})},
}

DOMAIN_VERDICT_ALLOWLISTS = {
    "get_quality_of_earnings_pack": {"stable", "monitor"},
    "get_dcf_input_candidates": {"screen_grade", "partial", "blocked"},
    "build_dcf_model_pack": {
        "reviewable_model",
        "partial_model",
        "calculation_unavailable",
    },
    "prepare_standard_audit_hours_inputs": {"not_assessed"},
}

DOMAIN_VERDICT_LABELS = {
    "get_quality_of_earnings_pack": {
        "stable": "안정적",
        "monitor": "모니터링 필요",
    },
    "get_dcf_input_candidates": {
        "screen_grade": "입력 후보 선별 결과",
        "partial": "일부 입력 확인",
        "blocked": "입력 산정 차단",
    },
    "build_dcf_model_pack": {
        "reviewable_model": "검토 가능한 모델",
        "partial_model": "일부 모델 구성",
        "calculation_unavailable": "계산 불가",
    },
    "prepare_standard_audit_hours_inputs": {
        "not_assessed": "평가 미실시",
    },
}


def public_domain_verdict_label(tool_name: str, verdict: str | None) -> str:
    """Return a Korean user-facing label for an allowlisted domain conclusion."""
    if verdict is None:
        return "별도 결론 없음"
    return DOMAIN_VERDICT_LABELS.get(tool_name, {}).get(verdict, "별도 결론 없음")


class SectionSourceV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    source_label: str
    source_url: str
    rcept_no: str | None = None


class SectionStatusV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["usable", "limited", "missing", "error"]
    required: bool
    applicability: Literal["applicable", "not_applicable", "unknown"]
    coverage: dict[str, int | float | str | None] = Field(
        default_factory=dict, max_length=32,
    )
    blockers: list[str] = Field(default_factory=list, max_length=64)
    sources: list[SectionSourceV1] = Field(default_factory=list, max_length=64)
    not_applicable_basis: str | None = None


class DataQualityV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["usable", "limited", "missing", "error"]
    grade: Literal["A", "B", "C", "D"] | None = None
    dataset_version: str
    schema_version: str
    covered_years: list[int] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    section_statuses: dict[str, SectionStatusV1] = Field(default_factory=dict)


class EvidenceRefV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    source_label: str
    source_url: str
    rcept_no: str | None = None
    section_title: str | None = None
    excerpt: str | None = None


class AnalysisItemV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    statement: str
    perspective: Literal["auditor", "investor", "both"] = "both"
    basis: str | None = None


class AnswerEnvelopeV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["1.0"] = "1.0"
    tool_name: str
    verdict: Literal["usable", "limited", "missing", "error"]
    domain_verdict: str | None = None
    answer: str
    confirmed_facts: list[dict[str, Any]
    ]
    analysis: list[AnalysisItemV1]
    evidence: list[EvidenceRefV1]
    data_quality: DataQualityV1
    warnings: list[str]
    next_checks: list[str]
    answer_pack: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _domain_verdict_matches_tool(self) -> AnswerEnvelopeV1:
        if (
            self.domain_verdict is not None
            and self.domain_verdict not in DOMAIN_VERDICT_ALLOWLISTS.get(
                self.tool_name, set(),
            )
        ):
            raise ValueError("domain verdict is not allowed for this tool")
        return self


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _covered_years(result: dict[str, Any], quality: dict[str, Any]) -> list[int]:
    values = quality.get("covered_years")
    if not isinstance(values, list):
        values = result.get("years")
    if not isinstance(values, list):
        values = [result.get("year", result.get("bsns_year"))]
    years: list[int] = []
    for value in values:
        try:
            year = int(value)
        except (TypeError, ValueError):
            continue
        if 1900 <= year <= 2100 and year not in years:
            years.append(year)
    return years


def _has_partial_payload(tool_name: str, result: dict[str, Any]) -> bool:
    """Return whether registered business fields contain affirmative data."""
    fields = _TOOL_PURPOSE_FIELDS.get(tool_name)
    if fields is None:
        return False
    # Facts are a dedicated channel: an uncitable fact remains an affirmative
    # but limited payload below, while only a validated source can retain
    # usable status.
    if any(isinstance(fact, dict) for fact in result.get("confirmed_facts") or []):
        return True
    for key in fields.get("lists", frozenset()):
        if isinstance(result.get(key), list) and result[key]:
            return True
    for key in fields.get("counts", frozenset()):
        value = result.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return True
    for key in fields.get("maps", frozenset()):
        value = result.get(key)
        if isinstance(value, dict) and value:
            return True
    return False


def _data_quality(tool_name: str, result: dict[str, Any]) -> DataQualityV1:
    raw_quality = result.get("data_quality")
    quality = raw_quality if isinstance(raw_quality, dict) else {}
    is_error = "error" in result
    if is_error:
        status = "error"
    elif quality.get("status") is not None:
        status = str(quality["status"])
    else:
        status = "limited" if _has_partial_payload(tool_name, result) else "missing"
    if status not in _QUALITY_STATUSES:
        raise ValueError(f"unsupported data quality status: {status}")

    # An upstream quality claim alone is not evidence.  Empty legacy payloads
    # have neither purpose-bearing inputs nor public facts, so availability is
    # genuinely missing before presentation layers build an availability pack.
    if status == "usable" and not _has_partial_payload(tool_name, result):
        status = "missing"

    confirmed_facts = [
        fact for fact in result.get("confirmed_facts") or []
        if isinstance(fact, dict)
    ]
    unresolved_fact_count = sum(
        not (
            isinstance(fact.get("source"), dict)
            and evidence_reference_fields(fact["source"])
        )
        for fact in confirmed_facts
    )
    evidence_gap = unresolved_fact_count > 0
    if status == "usable" and evidence_gap:
        status = "limited"

    meta = result.get("_meta") if isinstance(result.get("_meta"), dict) else {}
    limitations = _string_list(quality.get("limitations")) + _string_list(result.get("limitations"))
    coverage_note = quality.get("coverage_note")
    if coverage_note:
        limitations.append(str(coverage_note))
    if evidence_gap:
        limitations.append(
            f"확인된 사실 {unresolved_fact_count}개에 대해 공개적으로 해석 가능한 근거 링크를 확인하지 못했습니다. "
            "다른 연도 또는 다른 출처의 근거로 대체 인용하지 않았습니다."
        )
    if status == "missing":
        limitations.append("로컬 캐시에 확인 가능한 데이터가 없습니다. 이는 원 공시 부재를 뜻하지 않습니다.")
    if status == "limited" and not limitations:
        limitations.append(
            "확인 가능한 데이터가 제한되어 결과를 완전한 판단 근거로 사용할 수 없습니다."
        )
    if is_error:
        error_message = str(result["error"]).strip()
        limitations.insert(0, error_message or "도구 처리 중 오류가 발생했습니다. 원인 확인이 필요합니다.")

    raw_section_statuses = quality.get("section_statuses")
    section_statuses: dict[str, SectionStatusV1] = {}
    if isinstance(raw_section_statuses, dict):
        if len(raw_section_statuses) > 32:
            status = "limited" if status != "error" else status
            limitations.append("섹션 상태가 허용된 개수를 초과해 전체 상태를 제한으로 표시합니다.")
        else:
            for section_name, raw_section in raw_section_statuses.items():
                if not isinstance(section_name, str) or not section_name.strip():
                    status = "limited" if status != "error" else status
                    limitations.append("이름 없는 섹션 상태를 해석할 수 없어 전체 상태를 제한으로 표시합니다.")
                    continue
                try:
                    section_statuses[section_name] = SectionStatusV1.model_validate(raw_section)
                except (TypeError, ValueError):
                    status = "limited" if status != "error" else status
                    limitations.append(
                        f"섹션 상태 '{section_name}' 형식을 해석할 수 없어 전체 상태를 제한으로 표시합니다."
                    )
    elif raw_section_statuses is not None:
        status = "limited" if status != "error" else status
        limitations.append("섹션 상태 형식을 해석할 수 없어 전체 상태를 제한으로 표시합니다.")

    grade = quality.get("grade")
    return DataQualityV1(
        status=status,
        grade=grade if grade in {"A", "B", "C", "D"} else None,
        dataset_version=str(quality.get("dataset_version") or meta.get("dataset_version") or _ADAPTER_VERSION),
        schema_version=str(quality.get("schema_version") or meta.get("schema_version") or _ADAPTER_VERSION),
        covered_years=_covered_years(result, quality),
        missing_fields=_string_list(quality.get("missing_fields")),
        limitations=list(dict.fromkeys(limitations)),
        section_statuses=section_statuses,
    )


def normalize_answer_result(tool_name: str, result: dict[str, Any]) -> dict[str, Any]:
    """Attach one canonical quality status before pack or prose rendering."""
    if not isinstance(result, dict):
        raise TypeError("result must be a dict")
    normalized = dict(result)
    quality = _data_quality(tool_name, normalized)
    raw_verdict = str(
        normalized.get("domain_verdict")
        or normalized.get("verdict")
        or ""
    ).strip()
    allowed = DOMAIN_VERDICT_ALLOWLISTS.get(tool_name, set())
    normalized["domain_verdict"] = raw_verdict if raw_verdict in allowed else None
    # Keep additive legacy metadata (for example the local source label) but
    # replace every typed quality field with the canonical validated value.
    raw_quality = normalized.get("data_quality")
    normalized_quality = dict(raw_quality) if isinstance(raw_quality, dict) else {}
    normalized_quality.update(quality.model_dump())
    normalized["data_quality"] = normalized_quality
    normalized["quality_status"] = quality.status
    return normalized


def _analysis(result: dict[str, Any]) -> list[AnalysisItemV1]:
    items: list[AnalysisItemV1] = []
    for raw in result.get("analysis") or []:
        if not isinstance(raw, dict):
            continue
        statement = str(raw.get("statement") or "").strip()
        if not statement:
            continue
        perspective = raw.get("perspective")
        items.append(AnalysisItemV1(
            statement=statement,
            perspective=perspective if perspective in {"auditor", "investor", "both"} else "both",
            basis=str(raw["basis"]) if raw.get("basis") is not None else None,
        ))
    return items


def _evidence(result: dict[str, Any]) -> list[EvidenceRefV1]:
    refs: list[EvidenceRefV1] = []
    seen: set[str] = set()
    candidates: list[tuple[dict[str, Any], str | None]] = []
    for fact in result.get("confirmed_facts") or []:
        if isinstance(fact, dict) and isinstance(fact.get("source"), dict):
            candidates.append((fact["source"], str(fact.get("excerpt") or "").strip() or None))
    for field in ("rcept_no", "parent_rcept_no"):
        if result.get(field):
            candidates.append(({field: result[field]}, None))
    for row in result.get("history") or []:
        if not isinstance(row, dict):
            continue
        receipt = row.get("rcept_no") or row.get("접수번호")
        if receipt:
            candidates.append(({"rcept_no": receipt}, None))
    meta = result.get("_meta")
    if isinstance(meta, dict) and meta.get("source_rcept_no"):
        candidates.append(({"rcept_no": meta["source_rcept_no"]}, None))

    for source, excerpt in candidates:
        fields = evidence_reference_fields(source)
        if not fields or fields["source_url"] in seen:
            continue
        seen.add(fields["source_url"])
        refs.append(EvidenceRefV1(**fields, excerpt=excerpt))
    return refs


def build_answer_envelope(tool_name: str, result: dict[str, Any]) -> AnswerEnvelopeV1:
    """Adapt an existing MCP result without obscuring quality or error states."""
    if not isinstance(result, dict):
        raise TypeError("result must be a dict")

    normalized = normalize_answer_result(tool_name, result)
    quality = _data_quality(tool_name, normalized)
    warnings = list(quality.limitations)
    if quality.status == "missing" and not warnings:
        warnings.append("로컬 캐시 미확보는 원 공시 부재를 의미하지 않습니다.")
    return AnswerEnvelopeV1(
        tool_name=tool_name,
        verdict=quality.status,
        domain_verdict=normalized["domain_verdict"],
        answer=str(normalized.get("answer") or ""),
        confirmed_facts=[fact for fact in normalized.get("confirmed_facts") or [] if isinstance(fact, dict)],
        analysis=_analysis(normalized),
        evidence=_evidence(normalized),
        data_quality=quality,
        warnings=warnings,
        next_checks=_string_list(normalized.get("next_checks")),
        answer_pack=normalized.get("answer_pack") if isinstance(normalized.get("answer_pack"), dict) else None,
    )


def _canonical_answer_fallback(tool_name: str, result: dict[str, Any]) -> str:
    """Return safe Korean prose when a detail renderer cannot produce text."""
    envelope = build_answer_envelope(tool_name, result)
    return "\n".join([
        "판정:",
        f"- {envelope.verdict}",
        "",
        "업무 결론:",
        f"- {public_domain_verdict_label(tool_name, envelope.domain_verdict)}",
        "",
        "확인된 내용:",
        "- 구조화된 공시 근거와 데이터 한계를 확인하세요.",
    ])


def enrich_answer_response(tool_name: str, result: dict[str, Any]) -> dict[str, Any]:
    """Apply the shared answer-pack and narrative behavior after metadata is attached."""
    enriched = dict(result)
    raw_quality = enriched.get("data_quality")
    if isinstance(raw_quality, dict):
        normalized_status = {
            "cache_missing": "missing",
            "incomplete_core_metrics": "limited",
            "unavailable": "missing",
        }.get(raw_quality.get("status"))
        if normalized_status is not None:
            enriched["data_quality"] = {
                **raw_quality,
                "status": normalized_status,
            }
    enriched = normalize_answer_result(tool_name, enriched)
    # Do not let raw legacy prose survive a renderer-empty or renderer-failed
    # path. The response answer is rebuilt below from canonical state only.
    enriched.pop("answer", None)
    # Local imports avoid an import cycle: answer_pack and renderers consume this module.
    from kreports.mcp.answer_pack import build_answer_pack
    from kreports.mcp.renderers import render_answer

    if "error" not in enriched and not enriched.get("answer_pack"):
        answer_pack = build_answer_pack(tool_name, enriched)
        if answer_pack:
            enriched["answer_pack"] = answer_pack
    # The public professional answer is always regenerated from the normalized
    # envelope.  A legacy free-text answer remains in the raw input only and
    # cannot bypass verdict and evidence safeguards.
    try:
        answer = render_answer(tool_name, enriched)
    except Exception:
        answer = None
    enriched["answer"] = (
        answer
        if isinstance(answer, str) and answer.strip()
        else _canonical_answer_fallback(tool_name, enriched)
    )
    return enriched
