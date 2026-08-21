"""Explain an existing peer-selection result without reselecting companies.

This module is an application projection over the canonical peer-selection
contract. It reads ``selection_policy`` and per-company evidence already emitted
by ``select_peer_group`` and produces one structured explanation shared by MCP,
chatbot, API, and demo surfaces. It must never run a second peer-selection
algorithm, query a different population, or invent a similarity dimension.
"""
from __future__ import annotations

from typing import Any


PEER_SELECTION_EXPLANATION_VERSION = "peer_selection_explanation.v1"

_DEFAULT_LEGACY_CRITERIA = ["industry", "sector", "financial_data"]

_FEATURE_LABELS = {
    "financials": "재무자료",
    "business_report": "사업보고서",
    "audit_report": "감사보고서",
    "audit_fees": "감사보수·시간 자료",
    "notes": "재무제표 주석 원문",
    "kam": "핵심감사사항",
}

_SIZE_LABELS = {
    "total_assets": "총자산",
    "revenue": "매출",
    "employees": "종업원 수",
}

_WEIGHT_LABELS = {
    "industry": "업종",
    "sector": "산업군",
    "size": "회사 규모",
    "business": "사업 내용",
    "coverage": "자료 확보",
}

_SECTOR_LABELS = {
    "financial": "금융",
    "holding": "지주",
    "real_estate": "부동산",
    "general": "일반",
    "unknown": "미분류",
}

_REASON_LABELS = {
    "same_ksic_prefix": "업종 기준 충족",
    "same_sector_group": "산업군 기준 충족",
    "asset_size_bucket": "총자산 규모 조건 충족",
    "audit_fee_available": "감사보수 자료 확보",
    "explicit_custom_code": "사용자가 직접 지정",
    "explicit_included_corp_code": "사용자가 직접 포함",
    "financial_data": "재무자료 확보",
}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _policy_from_result(result: dict[str, Any]) -> dict[str, Any]:
    policy = _dict(result.get("selection_policy"))
    if policy:
        return policy
    peer_group = _dict(result.get("peer_group"))
    policy = _dict(peer_group.get("selection_policy"))
    if policy:
        return policy
    cohort = _dict(result.get("cohort_snapshot"))
    criteria = _dict(cohort.get("criteria_applied"))
    if criteria:
        return {
            "criteria_applied": criteria,
            "requested_year": cohort.get("requested_year"),
            "resolved_year": cohort.get("resolved_year"),
            "fs_div_used": cohort.get("fs_div"),
            "matched_prefix_len": criteria.get("prefix_len"),
            "selection_mode": criteria.get("mode"),
            "legacy_criteria": False,
        }
    return {}


def _peers_from_result(result: dict[str, Any]) -> list[dict[str, Any]]:
    direct = [
        row for row in result.get("peers") or []
        if isinstance(row, dict)
    ]
    if direct:
        return direct
    peer_group = _dict(result.get("peer_group"))
    return [
        row for row in peer_group.get("peers") or []
        if isinstance(row, dict)
    ]


def _population_counts(
    result: dict[str, Any],
    peers: list[dict[str, Any]],
) -> dict[str, int]:
    peer_group = _dict(result.get("peer_group"))
    eligible = int(
        result.get("statistical_member_count")
        or result.get("peer_count")
        or result.get("n_peers")
        or peer_group.get("statistical_member_count")
        or peer_group.get("peer_count")
        or len(peers)
        or 0
    )
    returned = int(
        result.get("returned_peer_count")
        or peer_group.get("returned_peer_count")
        or len(peers)
        or 0
    )
    return {
        "eligible_company_count": eligible,
        "returned_company_count": returned,
    }


def _fs_label(value: Any) -> str:
    normalized = str(value or "").upper()
    if normalized == "CFS":
        return "연결재무제표"
    if normalized == "OFS":
        return "별도재무제표"
    return "재무제표 기준 미확정"


def _ratio_text(value: float) -> str:
    decimals = 0 if value >= 100 else 1 if value >= 10 else 2
    number = f"{value:,.{decimals}f}".rstrip("0").rstrip(".")
    return f"{number}배"


def _bounded_ratio_range(tolerance: Any) -> tuple[float, float] | None:
    try:
        distance = float(tolerance)
    except (TypeError, ValueError):
        return None
    if distance < 0:
        return None
    upper = 10.0 ** distance
    return 1.0 / upper, upper


def _criteria_origin(policy: dict[str, Any]) -> tuple[str, str]:
    requested = policy.get("criteria_requested")
    if not policy.get("legacy_criteria"):
        return "user_customized", "사용자가 지정한 기준"
    if isinstance(requested, list) and requested != _DEFAULT_LEGACY_CRITERIA:
        return "legacy_user_criteria", "사용자가 지정한 기존 형식의 기준"
    return "default", "기본 비교 기준"


def _feature_labels(values: Any) -> list[str]:
    return [
        _FEATURE_LABELS.get(str(value), str(value))
        for value in values or []
        if value
    ]


def _weight_text(weights: dict[str, Any]) -> str | None:
    parts: list[str] = []
    for key, raw_value in weights.items():
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        parts.append(
            f"{_WEIGHT_LABELS.get(str(key), str(key))} {value * 100:,.0f}%"
        )
    return " · ".join(parts) if parts else None


def _ordering(
    criteria: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any]:
    mode = str(
        criteria.get("mode")
        or policy.get("selection_mode")
        or "adaptive"
    )
    weights = _dict(criteria.get("weights"))
    required_features = _feature_labels(criteria.get("required_features"))
    if mode == "ranked":
        weight_text = _weight_text(weights)
        if weight_text:
            return {
                "key": "weighted_fit_desc",
                "label": "사용자가 지정한 가중치에 따른 기준 적합도 높은 순",
                "detail": weight_text,
                "is_relevance_ranking": True,
            }
        if required_features:
            return {
                "key": "evidence_coverage_desc",
                "label": "요청한 비교자료가 더 많이 확보된 순",
                "detail": "동률이면 동일한 회사 순서를 유지합니다.",
                "is_relevance_ranking": True,
            }
        return {
            "key": "deterministic_rank",
            "label": "선택한 기준을 충족한 회사의 고정된 순서",
            "detail": "별도 가중치를 지정하지 않았습니다.",
            "is_relevance_ranking": False,
        }
    return {
        "key": "total_assets_desc",
        "label": "선택한 조건을 충족한 회사 중 총자산이 큰 순",
        "detail": "이 순서는 관련성 점수가 아니라 화면 표시 순서입니다.",
        "is_relevance_ranking": False,
    }


def _criterion_rows(
    criteria: dict[str, Any],
    policy: dict[str, Any],
    *,
    origin_label: str,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    rows: list[dict[str, Any]] = []
    short_values: list[str] = []
    limitations: list[str] = []

    def add(
        key: str,
        label: str,
        value: str,
        *,
        status: str = "applied",
        detail: dict[str, Any] | None = None,
        short: bool = True,
    ) -> None:
        rows.append({
            "key": key,
            "label": label,
            "value": value,
            "status": status,
            "detail": detail or {},
        })
        if short and status == "applied":
            short_values.append(value)

    add("origin", "기준 출처", origin_label, short=False)

    requested_year = policy.get("requested_year")
    resolved_year = policy.get("resolved_year") or requested_year
    if resolved_year:
        value = f"{resolved_year}년"
        if requested_year and requested_year != resolved_year:
            value += f" 적용(요청 {requested_year}년)"
        add(
            "year",
            "기준연도",
            value,
            detail={
                "requested_year": requested_year,
                "resolved_year": resolved_year,
            },
        )

    fs_value = policy.get("fs_div_used")
    add(
        "fs_basis",
        "재무제표 기준",
        _fs_label(fs_value),
        detail={
            "requested_strategy": policy.get("fs_strategy"),
            "applied_fs_div": fs_value,
        },
    )

    industry_basis = str(criteria.get("industry_basis") or "ksic")
    requested_prefix = criteria.get("prefix_len")
    matched_prefix = policy.get("matched_prefix_len") or requested_prefix
    fallback_used = bool(policy.get("fallback_used"))
    if industry_basis == "custom_codes":
        industry_value = "사용자가 직접 지정한 회사 목록"
    elif industry_basis == "sector_group":
        industry_value = "기준회사와 같은 산업군"
    else:
        industry_value = (
            f"한국표준산업분류 앞 {matched_prefix}자리 일치"
            if matched_prefix
            else "같은 업종"
        )
        if fallback_used and requested_prefix and matched_prefix:
            industry_value += (
                f"(요청 {requested_prefix}자리에서 {matched_prefix}자리로 확대)"
            )
    add(
        "industry",
        "업종 범위",
        industry_value,
        detail={
            "industry_basis": industry_basis,
            "requested_prefix_len": requested_prefix,
            "matched_prefix_len": matched_prefix,
            "fallback_used": fallback_used,
        },
    )

    excluded_sectors = [
        _SECTOR_LABELS.get(str(value), str(value))
        for value in criteria.get("excluded_sector_groups") or []
    ]
    if excluded_sectors:
        add(
            "excluded_sectors",
            "제외 산업군",
            ", ".join(excluded_sectors),
            detail={"count": len(excluded_sectors)},
            short=False,
        )

    size_metric = criteria.get("size_metric")
    tolerance = criteria.get("size_log10_tolerance")
    if size_metric:
        size_label = _SIZE_LABELS.get(str(size_metric), str(size_metric))
        ratio_range = _bounded_ratio_range(tolerance)
        if str(size_metric) == "employees":
            add(
                "size",
                "회사 규모",
                "종업원 수 기준 요청",
                status="unsupported",
                detail={
                    "size_metric": size_metric,
                    "size_log10_tolerance": tolerance,
                },
            )
            limitations.append(
                "종업원 수 비교자료가 없어 해당 요청은 현재 지원되지 않습니다."
            )
        elif ratio_range:
            lower, upper = ratio_range
            size_value = (
                f"기준회사 {size_label}의 {_ratio_text(lower)}~"
                f"{_ratio_text(upper)} 범위"
            )
            add(
                "size",
                "회사 규모",
                size_value,
                detail={
                    "size_metric": size_metric,
                    "size_log10_tolerance": tolerance,
                    "lower_ratio": lower,
                    "upper_ratio": upper,
                },
            )
        else:
            add(
                "size",
                "회사 규모",
                f"{size_label} 기준 요청(허용 범위 미지정)",
                status="not_applied",
                detail={"size_metric": size_metric},
            )
            limitations.append(
                f"{size_label} 허용 범위를 지정하지 않아 규모 선별 조건에는 반영되지 않았습니다."
            )
    else:
        add(
            "size",
            "회사 규모",
            "규모 제한 없음",
            detail={"size_metric": None},
            short=False,
        )

    required_features = _feature_labels(criteria.get("required_features"))
    if required_features:
        mode = str(criteria.get("mode") or policy.get("selection_mode") or "")
        minimum = float(criteria.get("minimum_coverage") or 0.0)
        if mode == "strict":
            feature_value = ", ".join(required_features) + " 전부 확보한 회사만"
            feature_status = "applied"
        elif minimum > 0:
            feature_value = (
                ", ".join(required_features)
                + f" 중 최소 {minimum * 100:,.0f}% 확보"
            )
            feature_status = "applied"
        elif mode == "ranked":
            feature_value = (
                ", ".join(required_features)
                + " 확보 수준을 표시 순서에 반영"
            )
            feature_status = "applied"
        else:
            feature_value = (
                ", ".join(required_features)
                + " 확보 여부 확인(포함 여부·표시 순서에는 미반영)"
            )
            feature_status = "informational"
            limitations.append(
                "요청한 비교자료의 확보 여부는 표시하지만 현재 선정 방식에서는 포함 여부나 순서에 직접 반영되지 않습니다."
            )
        add(
            "required_features",
            "필요 비교자료",
            feature_value,
            status=feature_status,
            detail={
                "required_features": list(criteria.get("required_features") or []),
                "minimum_coverage": minimum,
            },
        )

    business_tags = list(criteria.get("required_business_tags") or [])
    if business_tags:
        add(
            "business_tags",
            "사업 내용 조건",
            "사업 내용 태그 기준 요청",
            status="unsupported",
            detail={"requested_tags": business_tags},
        )
        limitations.append(
            "사업 내용 태그 색인이 없어 의미 기반 사업 유사성은 현재 지원되지 않습니다."
        )

    included_count = len(criteria.get("included_corp_codes") or [])
    excluded_count = len(criteria.get("excluded_corp_codes") or [])
    if included_count:
        add(
            "included_companies",
            "직접 포함",
            f"사용자가 지정한 {included_count}개사",
            detail={"count": included_count},
            short=False,
        )
    if excluded_count:
        add(
            "excluded_companies",
            "직접 제외",
            f"사용자가 지정한 {excluded_count}개사",
            detail={"count": excluded_count},
            short=False,
        )

    mode = str(criteria.get("mode") or policy.get("selection_mode") or "adaptive")
    mode_values = {
        "strict": "모든 조건을 충족한 회사만 포함",
        "adaptive": "조건에 맞는 회사가 부족하면 업종 범위를 확대",
        "ranked": "선택한 기준 적합도가 높은 순으로 정렬",
    }
    add(
        "selection_mode",
        "선정 방식",
        mode_values.get(mode, mode),
        detail={"mode": mode},
        short=False,
    )

    weights = _dict(criteria.get("weights"))
    weight_value = _weight_text(weights)
    if weight_value:
        weight_status = "applied"
        if "size" in weights and (
            not size_metric
            or str(size_metric) == "employees"
            or tolerance is None
        ):
            limitations.append(
                "회사 규모 가중치가 요청됐지만 비교 가능한 규모 범위가 없어 이 차원은 회사 간 차이를 만들지 못할 수 있습니다."
            )
        if "business" in weights and not business_tags:
            limitations.append(
                "사업 내용 가중치가 요청됐지만 사업 내용 비교 조건이 없어 이 차원은 회사 간 차이를 만들지 못할 수 있습니다."
            )
        add(
            "weights",
            "적합도 가중치",
            weight_value,
            status=weight_status,
            detail={"weights": weights},
            short=False,
        )

    return rows, short_values, list(dict.fromkeys(limitations))


def _reason_label(value: Any) -> str:
    raw = str(value or "")
    if raw in _REASON_LABELS:
        return _REASON_LABELS[raw]
    if raw.startswith("sector_group:"):
        return "산업군 기준 충족"
    if raw.startswith("same_ksic"):
        return "업종 기준 충족"
    return "선택한 기준 충족"


def _peer_explanations(
    peers: list[dict[str, Any]],
    criteria: dict[str, Any],
    policy: dict[str, Any],
    ordering: dict[str, Any],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    required_features = _feature_labels(criteria.get("required_features"))
    minimum_coverage = float(criteria.get("minimum_coverage") or 0.0)
    mode = str(criteria.get("mode") or policy.get("selection_mode") or "")
    size_metric = criteria.get("size_metric")
    tolerance = criteria.get("size_log10_tolerance")
    size_label = _SIZE_LABELS.get(str(size_metric), str(size_metric))

    for position, peer in enumerate(peers, start=1):
        reasons: list[str] = []
        components = _dict(peer.get("reason_components"))
        industry_component = _dict(components.get("industry_match"))
        if industry_component.get("override"):
            reasons.append("사용자가 직접 포함(업종 조건 예외)")
        elif industry_component.get("matched") is True:
            reasons.append("업종 기준 충족")
        else:
            for raw_reason in peer.get("include_reasons") or []:
                label = _reason_label(raw_reason)
                if label not in reasons:
                    reasons.append(label)

        if (
            size_metric
            and str(size_metric) != "employees"
            and tolerance is not None
        ):
            reasons.append(f"{size_label} 규모 조건 충족")

        coverage = peer.get("feature_coverage")
        if required_features and coverage is not None:
            try:
                coverage_value = float(coverage)
            except (TypeError, ValueError):
                coverage_value = None
            if coverage_value is not None:
                if mode == "strict" and coverage_value >= 1.0:
                    reasons.append("요청한 비교자료 모두 확보")
                elif minimum_coverage > 0 and coverage_value >= minimum_coverage:
                    reasons.append(
                        f"요청한 비교자료 {coverage_value * 100:,.0f}% 확보"
                    )
                elif coverage_value > 0:
                    reasons.append(
                        f"요청한 비교자료 {coverage_value * 100:,.0f}% 확보"
                    )

        if not reasons:
            reasons.append("선택한 기준 충족")

        unique_reasons = list(dict.fromkeys(reasons))[:5]
        output.append({
            "corp_code": peer.get("corp_code"),
            "company": peer.get("corp_name") or peer.get("corp_code"),
            "display_position": position,
            "criteria_reasons": unique_reasons,
            "criteria_reason_text": " · ".join(unique_reasons),
            "ordering_key": ordering["key"],
            "ordering_label": ordering["label"],
            "feature_coverage": peer.get("feature_coverage"),
            "selection_score": peer.get("selection_score"),
            "total_assets": peer.get("total_assets"),
            "revenue": peer.get("revenue"),
        })
    return output


def enrich_peer_selection_explanation(
    result: dict[str, Any],
) -> dict[str, Any]:
    """Attach a transparent criteria/result mapping to an existing result.

    The function is intentionally pure with respect to peer membership: it does
    not query the database, resort companies, change peer counts, or recalculate
    scores. It only explains the exact criteria and ordering already present in
    the canonical selection result.
    """
    if not isinstance(result, dict) or "error" in result:
        return result
    if isinstance(result.get("selection_explanation"), dict):
        return result

    policy = _policy_from_result(result)
    criteria = _dict(policy.get("criteria_applied"))
    if not criteria:
        return result

    peers = _peers_from_result(result)
    origin, origin_label = _criteria_origin(policy)
    ordering = _ordering(criteria, policy)
    criteria_rows, short_values, limitations = _criterion_rows(
        criteria,
        policy,
        origin_label=origin_label,
    )
    counts = _population_counts(result, peers)
    criteria_sentence = f"{origin_label}을 적용했습니다"
    if short_values:
        criteria_sentence += ": " + " · ".join(short_values[:6])
    criteria_sentence += "."

    explanation = {
        "version": PEER_SELECTION_EXPLANATION_VERSION,
        "criteria_origin": origin,
        "criteria_origin_label": origin_label,
        "criteria_sentence": criteria_sentence,
        "applied_criteria": criteria_rows,
        "ordering": ordering,
        "population": counts,
        "company_explanations": _peer_explanations(
            peers,
            criteria,
            policy,
            ordering,
        ),
        "limitations": limitations,
        "principles": {
            "filtering_and_ordering_are_separate": True,
            "display_page_does_not_change_population": True,
            "generic_similarity_claim_prohibited": True,
            "requested_and_applied_criteria_are_distinct": True,
        },
    }

    enriched = dict(result)
    enriched["selection_explanation"] = explanation
    peer_group = _dict(enriched.get("peer_group"))
    if peer_group:
        updated_peer_group = dict(peer_group)
        updated_peer_group["selection_explanation"] = explanation
        enriched["peer_group"] = updated_peer_group
    return enriched


__all__ = [
    "PEER_SELECTION_EXPLANATION_VERSION",
    "enrich_peer_selection_explanation",
]
