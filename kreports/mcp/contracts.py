"""Versioned professional answer contract for legacy MCP tool results."""
from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from kreports.analysis.evidence import (
    evidence_reference_fields,
    parent_rcept_no,
)


_CANONICAL_STATUSES = {"usable", "limited", "missing", "error"}
_QUALITY_STATUSES = _CANONICAL_STATUSES
_ADAPTER_VERSION = "legacy-result-adapter"
_PEER_COMPARISON_TOOL = "compare_to_industry_multi"
_PEER_ERROR_LIMITATION = "세부 데이터 제한 사항이 있어 추가 확인이 필요합니다."
_DCF_MODEL_TOOL = "build_dcf_model_pack"
_DCF_ERROR_LIMITATION = (
    "기업가치 산출에 필요한 공시 실제값 또는 분석가 입력을 확인하지 못했습니다."
)
_QOE_TOOL = "get_quality_of_earnings_pack"
_DCF_ASSUMPTION_KEYS = {
    "revenue_growth",
    "operating_margin",
    "tax_rate",
    "da_to_revenue",
    "capex_to_revenue",
    "nwc_to_revenue",
    "wacc",
    "terminal_growth",
}
_DCF_MISSING_FIELDS = {
    *_DCF_ASSUMPTION_KEYS,
    "revenue",
    "operating_profit",
    "depreciation_amortization",
    "purchase_ppe",
    "purchase_intangible_assets",
    "trade_receivables",
    "inventories",
    "trade_payables",
    "cash_and_equivalents",
    "interest_bearing_debt",
}

ToolPurposePredicate = Callable[[dict[str, Any]], bool]

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


class ReleaseContextV1(BaseModel):
    """Bounded release readiness, deliberately separate from question quality."""

    model_config = ConfigDict(extra="forbid", strict=True)

    release_ready: bool
    manifest_available: bool
    required_failures: list[str] = Field(default_factory=list, max_length=10)
    degraded_features: list[str] = Field(default_factory=list, max_length=10)
    snapshot_version: str | None = None


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
    release_context: ReleaseContextV1 = Field(default_factory=lambda: ReleaseContextV1(
        release_ready=False,
        manifest_available=False,
        required_failures=["release_context_unavailable"],
        degraded_features=[],
        snapshot_version=None,
    ))
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


def _positive_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and (isinstance(value, int) or math.isfinite(value))
        and value > 0
    )


def _is_numeric_measure(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    try:
        return Decimal(str(value)).is_finite()
    except (InvalidOperation, ValueError):
        return False


def _has_result_rows(result: dict[str, Any], key: str) -> bool:
    """Whether a handler returned at least one non-empty result record."""
    value = result.get(key)
    return isinstance(value, list) and any(
        isinstance(item, dict) and bool(item)
        for item in value
    )


def _has_positive_count(result: dict[str, Any], *keys: str) -> bool:
    return any(_positive_number(result.get(key)) for key in keys)


def _has_quantile_rows(result: dict[str, Any], key: str) -> bool:
    """Peer benchmark output is evidence only when a metric has observations."""
    values = result.get(key)
    return isinstance(values, dict) and any(
        isinstance(metric, dict) and _positive_number(metric.get("n"))
        for metric in values.values()
    )


def _has_industry_multi_result(result: dict[str, Any]) -> bool:
    """A multi-year benchmark needs at least one observed peer metric."""
    matrix = result.get("results")
    return isinstance(matrix, dict) and any(
        isinstance(metrics, dict) and any(
            isinstance(cell, dict) and _positive_number(cell.get("n"))
            for cell in metrics.values()
        )
        for metrics in matrix.values()
    )


def _has_nested_count(result: dict[str, Any], key: str, *count_keys: str) -> bool:
    value = result.get(key)
    if not isinstance(value, dict):
        return False
    return any(_positive_number(value.get(count_key)) for count_key in count_keys)


def _has_business_sections(result: dict[str, Any]) -> bool:
    sections = result.get("sections")
    return isinstance(sections, dict) and any(
        isinstance(section, dict)
        and isinstance(section.get("body_text"), str)
        and bool(section["body_text"].strip())
        for section in sections.values()
    )


def _has_investor_signal_result(result: dict[str, Any]) -> bool:
    if _has_result_rows(result, "recent_events"):
        return True
    snapshot = result.get("quality_snapshot")
    if isinstance(snapshot, dict) and any(
        _is_numeric_measure(snapshot.get(key))
        for key in (
            "avg_roe",
            "avg_operating_margin",
            "avg_revenue_growth",
            "latest_debt_ratio",
            "latest_fcf",
            "latest_cfo_ni",
        )
    ):
        return True
    accounting_risk = result.get("accounting_risk")
    raw_summary = (
        accounting_risk.get("raw_summary")
        if isinstance(accounting_risk, dict)
        else None
    )
    return isinstance(raw_summary, dict) and raw_summary.get("has_data") is True


def _has_audit_fee_result(result: dict[str, Any]) -> bool:
    if _has_quantile_rows(result, "benchmarks"):
        return True
    subject_metrics = result.get("subject_metrics")
    return isinstance(subject_metrics, dict) and any(
        _is_numeric_measure(subject_metrics.get(key))
        for key in (
            "audit_fee_m",
            "audit_hours",
            "non_audit_fee_m",
            "nas_ratio",
            "actual_fee_m",
            "actual_hours",
            "contract_fee_m",
            "contract_hours",
        )
    )


def _has_risk_profile_result(result: dict[str, Any]) -> bool:
    if _has_quantile_rows(result, "benchmarks"):
        return True
    events = result.get("disclosure_event_counts")
    if not isinstance(events, dict):
        return False
    subject_events = events.get("subject")
    peer_events = events.get("peers")
    return (
        isinstance(subject_events, dict)
        and _positive_number(subject_events.get("total_disclosures"))
    ) or (
        isinstance(peer_events, dict)
        and any(
            isinstance(value, dict)
            and _positive_number(value.get("total_disclosures"))
            for value in peer_events.values()
        )
    )


def _has_policy_item_result(result: dict[str, Any]) -> bool:
    items = result.get("items")
    return isinstance(items, dict) and any(
        isinstance(item, str) and bool(item.strip())
        or isinstance(item, dict) and any(
            isinstance(item.get(key), str) and bool(item[key].strip())
            for key in ("heading", "body")
        )
        for item in items.values()
    )


def _has_peer_policy_result(result: dict[str, Any]) -> bool:
    return _has_positive_count(result, "subject_policy_count", "peers_with_policy")


def _has_kam_topic_result(result: dict[str, Any]) -> bool:
    return (
        _has_result_rows(result, "subject_sections")
        or _has_nested_count(result, "audit_report_events", "total_events")
        or _has_nested_count(result, "audit_report_sections", "total_sections", "kam_body_count")
    )


def _has_audit_matter_result(result: dict[str, Any]) -> bool:
    if _has_result_rows(result, "subject_matters"):
        return True
    matters = result.get("matter_counts")
    return isinstance(matters, dict) and any(
        isinstance(value, dict) and _positive_number(value.get("total_sections"))
        for value in matters.values()
    )


def _has_audit_procedure_result(result: dict[str, Any]) -> bool:
    return _has_positive_count(result, "companies_with_procedures") or any(
        isinstance(counts, dict) and any(
            _positive_number(value)
            for value in counts.values()
        )
        for counts in (
            result.get("subject_procedure_type_counts"),
            result.get("peer_procedure_type_counts"),
            result.get("subject_method_counts"),
            result.get("peer_method_counts"),
            result.get("peer_kam_topic_counts"),
        )
    )


def _has_quality_of_earnings_result(result: dict[str, Any]) -> bool:
    if (
        _has_result_rows(result, "evidence")
        or _has_result_rows(result, "signals")
        or _has_result_rows(result, "audit_matter_flags")
    ):
        return True
    metrics = result.get("metrics")
    if not isinstance(metrics, dict):
        return False
    return (
        _positive_number(metrics.get("years"))
        or _is_numeric_measure(metrics.get("margin_volatility"))
        or any(isinstance(metrics.get(key), list) and bool(metrics[key]) for key in (
            "low_cash_conversion_years",
            "negative_ocf_years",
        ))
    )


def _has_audit_hours_proxy_result(result: dict[str, Any]) -> bool:
    if _has_result_rows(result, "drivers"):
        return True
    subject_metrics = result.get("subject_metrics")
    return isinstance(subject_metrics, dict) and any(
        _is_numeric_measure(subject_metrics.get(key))
        for key in ("audit_hours", "audit_fee_m", "total_assets", "beneish_m_score")
    )


def _has_audit_effort_input_result(result: dict[str, Any]) -> bool:
    return _has_result_rows(result, "rows") and any(
        isinstance(row, dict) and row.get("input_status") in {"usable", "limited"}
        for row in result.get("rows") or []
    )


def _has_dcf_model_result(result: dict[str, Any]) -> bool:
    return (
        _has_result_rows(result, "actuals")
        or _has_result_rows(result, "projections")
        or _has_nested_count(result, "valuation_bridge", "enterprise_value", "equity_value")
    )


def _has_dcf_candidate_result(result: dict[str, Any]) -> bool:
    if _has_result_rows(result, "historical_actuals"):
        return True
    assumptions = result.get("candidate_assumptions")
    return isinstance(assumptions, dict) and any(
        isinstance(value, dict)
        and any(
            isinstance(value.get(key), (int, float))
            and not isinstance(value.get(key), bool)
            for key in ("value", "low", "high", "median")
        )
        for value in assumptions.values()
    )


def _has_audit_acceptance_result(result: dict[str, Any]) -> bool:
    fee_summary = result.get("audit_fee_summary")
    risk_summary = result.get("risk_summary")
    if isinstance(fee_summary, dict) and _has_quantile_rows(fee_summary, "benchmarks"):
        return True
    if isinstance(risk_summary, dict) and _has_quantile_rows(risk_summary, "benchmarks"):
        return True
    policy_summary = result.get("policy_summary")
    return isinstance(policy_summary, dict) and any(
        _positive_number(policy_summary.get(key))
        for key in ("subject_policy_count", "peers_with_policy")
    )


def _has_disclosure_document(result: dict[str, Any]) -> bool:
    document = result.get("document")
    if isinstance(document, dict) and any(
        isinstance(document.get(key), str) and bool(document[key].strip())
        for key in ("body", "body_text", "content", "xml")
    ):
        return True
    return (
        _positive_number(result.get("body_length"))
        or isinstance(result.get("body_excerpt"), str)
        and bool(result["body_excerpt"].strip())
    )


# Each predicate is bound to a real handler result shape and explicitly rejects
# its no-data shape.  Configuration, subject metadata, selection policy, and
# cohort descriptors never establish answer usability.
_TOOL_PURPOSE_PREDICATES: dict[str, ToolPurposePredicate] = {
    "search_company": lambda result: _has_result_rows(result, "results"),
    "get_financial_snapshot": lambda result: _has_result_rows(result, "rows"),
    "score_going_concern": lambda result: (
        result.get("has_data") is True
        and isinstance(result.get("score"), (int, float))
        and not isinstance(result.get("score"), bool)
        and isinstance(result.get("grade"), str)
        and result["grade"].strip() not in {"", "-"}
    ),
    "detect_restatement": lambda result: _has_result_rows(result, "restatements"),
    "get_accounting_policy": _has_policy_item_result,
    "get_audit_history": lambda result: _has_result_rows(result, "history"),
    "get_subsidiary_auditors": lambda result: _has_result_rows(result, "subsidiaries"),
    "compare_to_industry": lambda result: (
        _has_result_rows(result, "peers")
        or _has_positive_count(result, "n")
    ),
    "get_business_overview": _has_business_sections,
    "get_investor_signals": _has_investor_signal_result,
    "select_peer_group": lambda result: (
        _has_result_rows(result, "peers")
        or _has_positive_count(result, "returned_peer_count")
    ),
    "compare_to_industry_multi": _has_industry_multi_result,
    "compare_peer_audit_fees": _has_audit_fee_result,
    "prepare_standard_audit_hours_inputs": _has_audit_effort_input_result,
    "compare_peer_risk_profile": _has_risk_profile_result,
    "compare_peer_accounting_policies": _has_peer_policy_result,
    "compare_peer_kam_topics": _has_kam_topic_result,
    "compare_peer_audit_report_matters": _has_audit_matter_result,
    "search_dataset": lambda result: _has_result_rows(result, "companies"),
    "fetch_disclosure_on_demand": _has_disclosure_document,
    "search_audit_report_matters": lambda result: _has_result_rows(result, "companies"),
    "search_audit_procedures": lambda result: _has_result_rows(result, "companies"),
    "compare_peer_audit_procedures": _has_audit_procedure_result,
    "get_kam_lifecycle": lambda result: _has_result_rows(result, "events"),
    "get_accounting_policy_changes": lambda result: (
        _has_result_rows(result, "changes")
        or _has_result_rows(result, "changed_items")
    ),
    "get_quality_of_earnings_pack": _has_quality_of_earnings_result,
    "get_dcf_input_candidates": _has_dcf_candidate_result,
    "search_disclosure_events": lambda result: _has_result_rows(result, "events"),
    "get_audit_report_sections": lambda result: _has_result_rows(result, "sections"),
    "estimate_audit_hours_proxy": _has_audit_hours_proxy_result,
    "build_audit_acceptance_pack": _has_audit_acceptance_result,
    "get_industry_audit_landscape": lambda result: (
        _has_result_rows(result, "auditor_market_share")
        or isinstance(result.get("subject_auditor"), dict)
        and isinstance(result["subject_auditor"].get("auditor_nm"), str)
        and bool(result["subject_auditor"]["auditor_nm"].strip())
    ),
    "build_dcf_model_pack": _has_dcf_model_result,
}


def _has_tool_purpose_result(tool_name: str, result: dict[str, Any]) -> bool:
    """Return whether the current tool produced affirmative domain output."""
    predicate = _TOOL_PURPOSE_PREDICATES.get(tool_name)
    return bool(predicate and predicate(result))


def _canonicalize_dcf_model_result(result: dict[str, Any]) -> dict[str, Any]:
    """Make enterprise-value availability authoritative over stale presentation."""
    if "enterprise_value" not in result and "error" in result:
        # Input-validation errors have no DCF payload to quarantine and must
        # remain ordinary public validation errors.  A declared unavailable
        # source, however, may carry stale model fields and is fail-closed.
        if result.get("error_code") == "dcf_source_unavailable":
            return _quarantine_unavailable_dcf_result(result)
        return dict(result)
    if result.get("enterprise_value") is None:
        return _quarantine_unavailable_dcf_result(result)
    return dict(result)


def _safe_dcf_unavailable_assumptions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for raw in value[:8]:
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("key") or "")
        if key not in _DCF_ASSUMPTION_KEYS:
            continue
        assumption_value = raw.get("value")
        if not _is_numeric_measure(assumption_value):
            continue
        rows.append({
            "key": key,
            "value": assumption_value,
            "unit": "ratio",
            "basis": "analyst_input",
        })
    return rows


def _safe_dcf_missing_accounts(
    value: Any,
    *,
    base_year: int | None,
    request_fs_div: str | None,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for raw in value[:20]:
        if not isinstance(raw, dict):
            continue
        field = str(raw.get("field") or "")
        try:
            year = int(raw.get("year"))
        except (TypeError, ValueError):
            continue
        row_fs_div = str(raw.get("fs_div") or "")
        if (
            field not in _DCF_MISSING_FIELDS
            or not 1900 <= year <= 2100
            or row_fs_div not in {"CFS", "OFS"}
            or base_year is None
            or year != base_year
            or row_fs_div != request_fs_div
        ):
            continue
        rows.append({
            "field": field,
            "year": year,
            "fs_div": row_fs_div,
            "basis": "requested_dcf_source_actual",
        })
    return rows


def _safe_dcf_request_year(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        year = value
    elif isinstance(value, str) and value.isascii() and value.isdigit():
        year = int(value)
    else:
        return None
    return year if 1900 <= year <= 2100 else None


def _safe_dcf_company(value: Any) -> str | dict[str, str] | None:
    if isinstance(value, str):
        return value[:80]
    if not isinstance(value, dict):
        return None
    company = {
        key: str(value[key])[:120]
        for key in ("corp_code", "stock_code", "corp_name", "market")
        if value.get(key) is not None and value.get(key) != ""
    }
    return company or None


def _quarantine_unavailable_dcf_result(
    result: dict[str, Any],
) -> dict[str, Any]:
    """Keep only bounded request and remediation fields for unavailable DCF."""
    base_year = _safe_dcf_request_year(result.get("base_year"))
    request_fs_div = str(result.get("fs_div") or "")
    if request_fs_div not in {"CFS", "OFS"}:
        request_fs_div = ""
    normalized = {
        key: result[key]
        for key in ("error", "error_code")
        if key in result
    }
    company = _safe_dcf_company(result.get("company"))
    if company is not None:
        normalized["company"] = company
    if base_year is not None:
        normalized["base_year"] = base_year
    if request_fs_div:
        normalized["fs_div"] = request_fs_div
    normalized.update({
        "enterprise_value": None,
        "equity_value": None,
        "calculation_status": "unavailable",
        "domain_verdict": "calculation_unavailable",
    })
    normalized["assumptions"] = _safe_dcf_unavailable_assumptions(
        result.get("assumptions")
    )
    normalized["missing_inputs"] = [
        field
        for field in _string_list(result.get("missing_inputs"))
        if field in _DCF_MISSING_FIELDS
    ]
    normalized["missing_accounts"] = _safe_dcf_missing_accounts(
        result.get("missing_accounts"),
        base_year=base_year,
        request_fs_div=request_fs_div or None,
    )
    normalized["data_quality"] = {
        "source": "financial_facts_compact",
        "status": "missing",
        "covered_years": [],
        "missing_fields": [
            field
            for field in _string_list(result.get("missing_inputs"))
            if field in _DCF_MISSING_FIELDS
        ],
        "limitations": [_DCF_ERROR_LIMITATION],
    }
    return normalized


def _canonicalize_qoe_matter_evidence(
    result: dict[str, Any],
) -> dict[str, Any]:
    """Bind QoE filing evidence to its deduplicated audit-matter groups."""
    normalized = dict(result)
    summary = result.get("audit_matter_summary")
    groups = (
        summary.get("groups")
        if isinstance(summary, dict)
        else None
    )
    if not isinstance(groups, list) or not groups:
        return normalized
    canonical_groups: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    for group in groups[:32]:
        if not isinstance(group, dict):
            continue
        source = group.get("source")
        receipt = parent_rcept_no(
            str(
                (
                    source.get("rcept_no")
                    if isinstance(source, dict)
                    else None
                )
                or group.get("rcept_no")
                or ""
            )
        )
        if not receipt:
            continue
        canonical_source = {
            "rcept_no": receipt,
            "url": (
                "https://dart.fss.or.kr/dsaf001/main.do?"
                f"rcpNo={receipt}"
            ),
            "source_type": "audit_report",
        }
        canonical_group = {
            key: deepcopy(group[key])
            for key in (
                "year",
                "matter_type",
                "severity",
                "excerpt",
                "section_count",
            )
            if key in group
        }
        canonical_group["rcept_no"] = receipt
        canonical_group["source"] = canonical_source
        canonical_groups.append(canonical_group)
        facts.append({
            "statement": (
                f"{canonical_group.get('year')}년 "
                f"{canonical_group.get('matter_type')} "
                f"감사보고서 matter를 접수번호 {receipt}에서 확인했습니다."
            ),
            "source": {
                "rcept_no": receipt,
                "report_nm": "감사보고서",
                "section_title": str(
                    group.get("matter_type") or "감사보고서 matter"
                ),
                "source_table": "audit_matter_items",
            },
            "excerpt": str(canonical_group.get("excerpt") or "")[:500],
        })
    section_count = 0
    for group in canonical_groups:
        try:
            count = int(group.get("section_count") or 0)
        except (TypeError, ValueError):
            count = 0
        section_count += max(count, 0)
    normalized["audit_matter_summary"] = {
        "unique_receipt_count": len({
            group["rcept_no"] for group in canonical_groups
        }),
        "section_count": section_count,
        "dedupe_basis": "parent_rcept_no + matter_type + normalized_excerpt",
        "groups": canonical_groups,
    }
    normalized["confirmed_facts"] = facts
    for field in (
        "rcept_no",
        "parent_rcept_no",
        "_meta",
        "history",
        "events",
        "evidence",
    ):
        normalized.pop(field, None)
    return normalized


def _data_quality(tool_name: str, result: dict[str, Any]) -> DataQualityV1:
    # A peer-comparison handler error is an opaque implementation diagnostic,
    # not presentation data.  Establish a complete public quality contract
    # before inspecting any raw error, coverage, facts, or section metadata.
    # The raw top-level error stays in the caller-owned result for programmatic
    # handling, but cannot be promoted into a public limitation.
    if tool_name == _PEER_COMPARISON_TOOL and "error" in result:
        return DataQualityV1(
            status="error",
            dataset_version=_ADAPTER_VERSION,
            schema_version=_ADAPTER_VERSION,
            limitations=[_PEER_ERROR_LIMITATION],
        )
    if (
        tool_name == _DCF_MODEL_TOOL
        and "enterprise_value" in result
        and result.get("enterprise_value") is None
    ):
        raw_quality = result.get("data_quality")
        quality = raw_quality if isinstance(raw_quality, dict) else {}
        return DataQualityV1(
            status="missing",
            dataset_version=str(
                quality.get("dataset_version") or _ADAPTER_VERSION
            ),
            schema_version=str(
                quality.get("schema_version") or _ADAPTER_VERSION
            ),
            covered_years=_covered_years(result, quality),
            missing_fields=[
                field
                for field in _string_list(quality.get("missing_fields"))
                if field in _DCF_MISSING_FIELDS
            ],
            limitations=[_DCF_ERROR_LIMITATION],
        )

    raw_quality = result.get("data_quality")
    quality = raw_quality if isinstance(raw_quality, dict) else {}
    is_error = "error" in result
    if is_error:
        status = "error"
    elif quality.get("status") is not None:
        status = str(quality["status"])
    else:
        status = "limited" if _has_tool_purpose_result(tool_name, result) else "missing"
    if status not in _QUALITY_STATUSES:
        raise ValueError(f"unsupported data quality status: {status}")

    # An upstream quality claim and cited facts alone are not purpose evidence.
    # The current tool must independently return its own audited domain result
    # before a public surface can say usable.
    if status == "usable" and not _has_tool_purpose_result(tool_name, result):
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
    if tool_name == _PEER_COMPARISON_TOOL:
        # This is the canonical-limitation boundary.  coverage_note and error
        # have already been promoted above, so sanitizing here cannot be
        # bypassed by a later quality-normalization pass.
        from kreports.mcp.professional_surfaces.investor import (
            publicize_peer_result_limitations,
        )

        limitations = publicize_peer_result_limitations({
            "data_quality": {"limitations": limitations},
        })["data_quality"]["limitations"]
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
    normalized = (
        _canonicalize_dcf_model_result(result)
        if tool_name == _DCF_MODEL_TOOL
        else _canonicalize_qoe_matter_evidence(result)
        if tool_name == _QOE_TOOL
        else dict(result)
    )
    quality = _data_quality(tool_name, normalized)
    peer_error = (
        tool_name == _PEER_COMPARISON_TOOL
        and quality.status == "error"
    )
    if peer_error:
        # The normalizer feeds every public peer surface.  Preserve only the
        # raw top-level error for programmatic callers; stale facts, sources,
        # results, and legacy presentation payloads must not survive an error.
        normalized = (
            {"error": normalized["error"]}
            if "error" in normalized
            else {}
        )
    public_quality: dict[str, Any] | None = None
    if tool_name == _PEER_COMPARISON_TOOL and not peer_error:
        # _data_quality() synthesizes the canonical limitation list from every
        # promotable field, including coverage_note and error.  Public peer
        # localization must happen *after* that synthesis; doing it earlier
        # lets those fields reintroduce internal diagnostic codes downstream.
        from kreports.mcp.professional_surfaces.investor import (
            publicize_peer_result_limitations,
        )

        public_result = publicize_peer_result_limitations({
            "data_quality": {
                **(
                    normalized.get("data_quality")
                    if isinstance(normalized.get("data_quality"), dict)
                    else {}
                ),
                "limitations": quality.limitations,
            },
        })
        public_quality = public_result["data_quality"]
    raw_verdict = str(
        normalized.get("domain_verdict")
        or normalized.get("verdict")
        or ""
    ).strip()
    allowed = DOMAIN_VERDICT_ALLOWLISTS.get(tool_name, set())
    normalized["domain_verdict"] = raw_verdict if raw_verdict in allowed else None
    # Keep additive legacy metadata (for example the local source label) but
    # replace every typed quality field with the canonical validated value.
    if peer_error:
        normalized_quality = quality.model_dump()
    else:
        raw_quality = normalized.get("data_quality")
        normalized_quality = dict(raw_quality) if isinstance(raw_quality, dict) else {}
        if public_quality is not None and "coverage_note" in public_quality:
            normalized_quality["coverage_note"] = public_quality["coverage_note"]
        normalized_quality.update(quality.model_dump())
    normalized["data_quality"] = normalized_quality
    normalized["quality_status"] = quality.status
    if tool_name == "get_kam_lifecycle":
        from kreports.mcp.auditor_public import public_kam_lifecycle_events

        normalized["events"] = public_kam_lifecycle_events(
            normalized.get("events"),
        )
    # Legacy packs have no binding to the current tool input or evidence.
    # Treat them as untrusted presentation data and rebuild a public pack only
    # after normalization from the current canonical result.
    normalized.pop("answer_pack", None)
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


def _release_context(result: dict[str, Any]) -> ReleaseContextV1:
    meta = result.get("_meta")
    candidate = meta.get("release_context") if isinstance(meta, dict) else None
    try:
        return ReleaseContextV1.model_validate(candidate)
    except Exception:
        return ReleaseContextV1(
            release_ready=False,
            manifest_available=False,
            required_failures=["release_context_unavailable"],
            degraded_features=[],
            snapshot_version=None,
        )


def build_answer_envelope(tool_name: str, result: dict[str, Any]) -> AnswerEnvelopeV1:
    """Adapt an existing MCP result without obscuring quality or error states."""
    if not isinstance(result, dict):
        raise TypeError("result must be a dict")

    normalized = normalize_answer_result(tool_name, result)
    quality = _data_quality(tool_name, normalized)
    peer_error = (
        tool_name == _PEER_COMPARISON_TOOL
        and quality.status == "error"
    )
    dcf_unavailable = (
        tool_name == _DCF_MODEL_TOOL
        and "enterprise_value" in normalized
        and normalized.get("enterprise_value") is None
    )
    # Unavailable DCF results are already canonicalized into a bounded,
    # remediation-only payload.  Keep their public limitation visible rather
    # than treating them as an opaque peer-comparison implementation error.
    quarantined_error = peer_error
    warnings = list(quality.limitations)
    if quality.status == "missing" and not warnings:
        warnings.append("로컬 캐시 미확보는 원 공시 부재를 의미하지 않습니다.")
    rendered_answer = result.get("answer")
    canonical_answer = (
        rendered_answer
        if isinstance(rendered_answer, str)
        and rendered_answer.startswith("판정:")
        else normalized.get("answer")
    )
    return AnswerEnvelopeV1(
        tool_name=tool_name,
        verdict=quality.status,
        domain_verdict=normalized["domain_verdict"],
        answer=(
            "" if quarantined_error
            else str(
                canonical_answer
                or (_DCF_ERROR_LIMITATION if dcf_unavailable else "")
            )
        ),
        confirmed_facts=(
            [] if quarantined_error
            else [
                fact for fact in normalized.get("confirmed_facts") or []
                if isinstance(fact, dict)
            ]
        ),
        analysis=[] if quarantined_error else _analysis(normalized),
        evidence=[] if quarantined_error else _evidence(normalized),
        data_quality=quality,
        release_context=_release_context(normalized),
        warnings=warnings,
        next_checks=(
            [] if quarantined_error
            else _string_list(normalized.get("next_checks"))
        ),
        answer_pack=(
            None if quarantined_error
            else normalized.get("answer_pack")
            if isinstance(normalized.get("answer_pack"), dict)
            else None
        ),
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
    if tool_name == "compare_to_industry_multi":
        from kreports.mcp.professional_surfaces.investor import (
            publicize_peer_result_limitations,
        )

        enriched = publicize_peer_result_limitations(enriched)
    enriched = normalize_answer_result(tool_name, enriched)
    # Do not let raw legacy prose survive a renderer-empty or renderer-failed
    # path. The response answer is rebuilt below from canonical state only.
    enriched.pop("answer", None)
    # A handler-supplied pack can describe another tool's prior usable result.
    # Discard it so every public pack is rebuilt from this normalized result.
    enriched.pop("answer_pack", None)
    # Local imports avoid an import cycle: answer_pack and renderers consume this module.
    from kreports.mcp.answer_pack import build_answer_pack
    from kreports.mcp.renderers import render_answer

    if (
        "error" not in enriched
        or tool_name in {
            _PEER_COMPARISON_TOOL,
            _DCF_MODEL_TOOL,
        }
    ):
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
