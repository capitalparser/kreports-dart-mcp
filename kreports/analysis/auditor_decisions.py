"""Decision-ready public auditor evidence without changing legacy collectors."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from kreports.analysis.audit_reporting import get_audit_history
from kreports.analysis.evidence import evidence_reference_fields
from kreports.analysis.filing_provenance import annual_filing_source
from kreports.analysis.peer_benchmarks import (
    build_audit_acceptance_pack as _legacy_build_audit_acceptance_pack,
)
from kreports.analysis.peer_benchmarks import (
    compare_peer_risk_profile as _legacy_compare_peer_risk_profile,
)
from kreports.mcp.contracts import SectionStatusV1


PUBLIC_ACCEPTANCE_LABELS = {
    "non_audit_fee_exceeds_audit_fee": "비감사보수가 감사보수를 초과하여 독립성 검토가 필요합니다.",
    "loss_based_going_concern_flag": "손실·현금흐름 기반 계속기업 스크리닝 신호가 있습니다.",
    "audit_report_emphasis_paragraph_present": "감사보고서 강조사항 문단이 확인됩니다.",
    "audit_report_going_concern_paragraph_present": "계속기업 관련 문단이 확인됩니다.",
}


class AcceptanceRequirementV1(BaseModel):
    section_key: Literal[
        "peer_group",
        "audit_effort",
        "financial_risk",
        "audit_history",
        "accounting_policy",
        "kam",
        "audit_report_matters",
    ]
    required: bool
    applicability: Literal["applicable", "not_applicable", "unknown"]
    minimum_coverage: dict[str, int | float | bool | str]


_REQUIREMENTS = {
    "peer_group": AcceptanceRequirementV1(
        section_key="peer_group", required=True, applicability="applicable",
        minimum_coverage={"selection_basis": True, "included_peers": 5},
    ),
    "audit_effort": AcceptanceRequirementV1(
        section_key="audit_effort", required=True, applicability="applicable",
        minimum_coverage={"requested_years": 3, "complete_years": 3, "cited_years": 3},
    ),
    "financial_risk": AcceptanceRequirementV1(
        section_key="financial_risk", required=True, applicability="applicable",
        minimum_coverage={"subject_metrics": "receivables,inventory,cash_flow,beneish", "peer_observations_per_metric": 5},
    ),
    "audit_history": AcceptanceRequirementV1(
        section_key="audit_history", required=True, applicability="applicable",
        minimum_coverage={"current_year_receipt": True, "prior_year_receipt": True},
    ),
    "accounting_policy": AcceptanceRequirementV1(
        section_key="accounting_policy", required=True, applicability="applicable",
        minimum_coverage={"current_period_policy": True, "filing_source": True},
    ),
    "kam": AcceptanceRequirementV1(
        section_key="kam", required=True, applicability="applicable",
        minimum_coverage={"current_filing_source": True, "semantic_complete": True},
    ),
    "audit_report_matters": AcceptanceRequirementV1(
        section_key="audit_report_matters", required=True, applicability="applicable",
        minimum_coverage={"current_audit_report_source": True, "classification_complete_for_zero": True},
    ),
}


def _source_ref(source: object) -> dict[str, Any] | None:
    return evidence_reference_fields(source) if isinstance(source, dict) else None


def _receipt_source(receipt: object, *, label: str) -> dict[str, Any] | None:
    return _source_ref({"rcept_no": receipt, "source_label": label})


def _section(
    *,
    status: str,
    requirement: AcceptanceRequirementV1,
    coverage: dict[str, Any],
    blockers: list[str] | None = None,
    sources: list[dict[str, Any]] | None = None,
    applicability: str | None = None,
    not_applicable_basis: str | None = None,
) -> dict[str, Any]:
    actual_applicability = applicability or requirement.applicability
    clean_sources = [
        {
            "source_label": source.get("source_label"),
            "source_url": source.get("source_url"),
            "rcept_no": source.get("rcept_no"),
        }
        for source in (sources or [])
        if isinstance(source, dict) and source.get("source_label") and source.get("source_url")
    ]
    normalized_coverage = {
        key: ("true" if value else "false") if isinstance(value, bool) else value
        for key, value in coverage.items()
        if value is None or isinstance(value, (int, float, str, bool))
    }
    if actual_applicability == "unknown":
        status = "limited"
        blockers = [*(blockers or []), "applicability_unknown"]
    if actual_applicability == "not_applicable":
        if not not_applicable_basis or not clean_sources:
            status = "limited"
            blockers = [*(blockers or []), "not_applicable_basis_or_source_missing"]
        else:
            status = "usable"
    return SectionStatusV1(
        status=status,
        required=requirement.required,
        applicability=actual_applicability,
        coverage=normalized_coverage,
        blockers=list(dict.fromkeys(blockers or [])),
        sources=clean_sources,
        not_applicable_basis=not_applicable_basis,
    ).model_dump(mode="json")


def _risk_metric_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    benchmarks = payload.get("benchmarks") if isinstance(payload.get("benchmarks"), dict) else {}
    subject_metrics = payload.get("subject_metrics") if isinstance(payload.get("subject_metrics"), dict) else {}
    metric_coverage = (payload.get("data_quality") or {}).get("metric_coverage")
    metric_coverage = metric_coverage if isinstance(metric_coverage, dict) else {}
    rows = []
    for metric, benchmark in benchmarks.items():
        if not isinstance(benchmark, dict):
            continue
        coverage = metric_coverage.get(metric) if isinstance(metric_coverage.get(metric), dict) else {}
        available_n = benchmark.get("n") or coverage.get("available_n") or 0
        rows.append({
            "metric": metric,
            "peer_n": available_n,
            "p25": benchmark.get("p25"),
            "p50": benchmark.get("p50"),
            "p75": benchmark.get("p75"),
            "subject_value": subject_metrics.get(metric),
            "limitation": (
                "Peer observation count is below 5."
                if not isinstance(available_n, (int, float)) or available_n < 5
                else None
            ),
        })
    return rows


def compare_peer_risk_profile(
    company: str,
    year: int = 2025,
    peer_limit: int = 30,
    fs_strategy: str = "auto",
    _peer_group: dict | None = None,
) -> dict[str, Any]:
    """Enrich the legacy peer-risk signal with canonical quality and evidence."""
    payload = _legacy_compare_peer_risk_profile(
        company=company, year=year, peer_limit=peer_limit,
        fs_strategy=fs_strategy, _peer_group=_peer_group,
    )
    if not isinstance(payload, dict) or "error" in payload:
        return payload

    result = dict(payload)
    subject = result.get("subject") if isinstance(result.get("subject"), dict) else {}
    corp_code = str(subject.get("corp_code") or company)
    fs_div = result.get("fs_div_used")
    source = annual_filing_source(corp_code, year, source_table="financials", fs_div=fs_div)
    metric_rows = _risk_metric_rows(result)
    required_metrics = {
        "receivables_to_revenue", "inventory_to_revenue", "op_cf_to_operating_profit",
        "accrual_ratio", "beneish_m_score",
    }
    present_subject_metrics = {
        row["metric"] for row in metric_rows
        if row.get("subject_value") is not None
    }
    covered_peer_metrics = {
        row["metric"] for row in metric_rows
        if isinstance(row.get("peer_n"), (int, float)) and row["peer_n"] >= 5
    }
    missing_subject = sorted(required_metrics - present_subject_metrics)
    missing_peer = sorted(required_metrics - covered_peer_metrics)
    blockers = []
    if missing_subject:
        blockers.append("required_subject_metrics_missing")
    if missing_peer:
        blockers.append("required_peer_metric_coverage_missing")
    if not source:
        blockers.append("subject_annual_filing_provenance_missing")
    status = "usable" if not blockers else "limited"
    source_ref = _source_ref(source)
    result["metric_rows"] = metric_rows
    raw_quality = result.get("data_quality") if isinstance(result.get("data_quality"), dict) else {}
    result["data_quality"] = {
        **raw_quality,
        "status": status,
        "section_statuses": {
            "metric_coverage": _section(
                status=status,
                requirement=AcceptanceRequirementV1(
                    section_key="financial_risk", required=True, applicability="applicable",
                    minimum_coverage={"peer_observations_per_metric": 5},
                ),
                coverage={
                    "required_subject_metric_count": len(required_metrics),
                    "subject_metric_count": len(present_subject_metrics),
                    "required_peer_metric_count": len(required_metrics),
                    "peer_metric_count": len(covered_peer_metrics),
                },
                blockers=blockers,
                sources=[source_ref] if source_ref else [],
            ),
        },
        "limitations": [
            "Peer aggregates are cohort-based and are not filing-specific facts.",
            *(["Required subject values are incomplete."] if missing_subject else []),
        ],
    }
    result["confirmed_facts"] = ([{
        "statement": f"{year}년 대상 회사 재무 위험 지표의 연간 공시 근거가 확인되었습니다.",
        "source": source,
    }] if source else [])
    checks = []
    labels = {
        "receivables_to_revenue": "매출채권",
        "inventory_to_revenue": "재고자산",
        "op_cf_to_operating_profit": "영업현금흐름",
        "accrual_ratio": "발생액",
        "beneish_m_score": "Beneish",
    }
    for metric in sorted(set(missing_subject) | set(missing_peer)):
        checks.append(f"{labels[metric]} 입력값과 peer 관측치를 추가 확인하세요.")
    event_inputs = result.get("disclosure_event_counts")
    if not isinstance(event_inputs, dict) or not event_inputs.get("subject"):
        checks.append("정정·주요사항 공시 이벤트 입력값을 추가 확인하세요.")
    result["next_checks"] = checks
    result["limitations"] = list(dict.fromkeys([
        *(result.get("limitations") or []),
        "Percentile and peer quantiles are comparison evidence, not an audit-risk conclusion.",
    ]))
    return result


def _history_section(history_payload: dict[str, Any], year: int) -> dict[str, Any]:
    history = history_payload.get("history") if isinstance(history_payload.get("history"), list) else []
    current = [row for row in history if isinstance(row, dict) and row.get("year") == year]
    prior = [row for row in history if isinstance(row, dict) and row.get("year") == year - 1]
    sources = [
        _receipt_source(row.get("rcept_no"), label="감사인 이력 공시")
        for row in [*current, *prior] if row.get("rcept_no")
    ]
    blockers = []
    if not current or not all(row.get("rcept_no") for row in current):
        blockers.append("current_year_audit_history_receipt_missing")
    if not prior or not all(row.get("rcept_no") for row in prior):
        blockers.append("prior_year_audit_history_receipt_missing")
    return _section(
        status="usable" if not blockers else "limited",
        requirement=_REQUIREMENTS["audit_history"],
        coverage={"current_year_rows": len(current), "prior_year_rows": len(prior)},
        blockers=blockers,
        sources=[source for source in sources if source],
    )


def build_acceptance_evidence(
    *,
    legacy_payload: dict[str, Any],
    audit_effort_section: SectionStatusV1 | None,
    audit_effort_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the seven-section acceptance matrix; effort is injected only."""
    result = dict(legacy_payload)
    year = int(result.get("year") or 0)
    peer_group = result.get("peer_group") if isinstance(result.get("peer_group"), dict) else {}
    peers = peer_group.get("peers") if isinstance(peer_group.get("peers"), list) else []
    selection = peer_group.get("selection_policy") if isinstance(peer_group.get("selection_policy"), dict) else {}
    peer_blockers = []
    if not selection or not any(value for value in selection.values()):
        peer_blockers.append("peer_selection_basis_missing")
    if len(peers) < 5:
        peer_blockers.append("included_peer_count_below_5")
    peer_section = _section(
        status="usable" if not peer_blockers else "limited",
        requirement=_REQUIREMENTS["peer_group"],
        coverage={"selection_basis": bool(selection), "included_peers": len(peers)},
        blockers=peer_blockers,
    )

    if audit_effort_section is None:
        effort_section = _section(
            status="limited", requirement=_REQUIREMENTS["audit_effort"],
            coverage={"requested_years": 3, "complete_years": 0, "cited_years": 0, "row_count": len(audit_effort_rows)},
            blockers=["audit_effort_helper_not_integrated"],
        )
    else:
        supplied = audit_effort_section.model_dump(mode="json")
        coverage = dict(supplied.get("coverage") or {})
        covered_years = {row.get("year") for row in audit_effort_rows if isinstance(row, dict) and row.get("year") is not None}
        coverage.setdefault("requested_years", 3)
        coverage.setdefault("complete_years", len(covered_years))
        coverage.setdefault("cited_years", len(supplied.get("sources") or []))
        blockers = list(supplied.get("blockers") or [])
        valid = (
            supplied.get("status") == "usable"
            and coverage.get("complete_years", 0) >= 3
            and coverage.get("cited_years", 0) >= 3
        )
        if not valid:
            blockers.append("audit_effort_three_year_cited_coverage_missing")
        effort_section = _section(
            status="usable" if valid else "limited",
            requirement=_REQUIREMENTS["audit_effort"], coverage=coverage,
            blockers=blockers, sources=supplied.get("sources") or [],
            applicability=supplied.get("applicability"),
            not_applicable_basis=supplied.get("not_applicable_basis"),
        )

    risk_summary = result.get("risk_summary") if isinstance(result.get("risk_summary"), dict) else {}
    risk_metrics = risk_summary.get("subject_metrics") if isinstance(risk_summary.get("subject_metrics"), dict) else {}
    benchmarks = risk_summary.get("benchmarks") if isinstance(risk_summary.get("benchmarks"), dict) else {}
    required_risk = {"receivables_to_revenue", "inventory_to_revenue", "op_cf_to_operating_profit", "accrual_ratio", "beneish_m_score"}
    risk_missing_subject = sorted(metric for metric in required_risk if risk_metrics.get(metric) is None)
    risk_missing_peer = sorted(
        metric for metric in required_risk
        if not isinstance(benchmarks.get(metric), dict) or (benchmarks[metric].get("n") or 0) < 5
    )
    risk_blockers = []
    if risk_missing_subject:
        risk_blockers.append("required_subject_metrics_missing")
    if risk_missing_peer:
        risk_blockers.append("required_peer_metric_coverage_missing")
    risk_section = _section(
        status="usable" if not risk_blockers else "limited", requirement=_REQUIREMENTS["financial_risk"],
        coverage={"subject_metric_count": len(required_risk) - len(risk_missing_subject), "peer_metric_count": len(required_risk) - len(risk_missing_peer)},
        blockers=risk_blockers,
    )

    history_payload = result.get("audit_history") if isinstance(result.get("audit_history"), dict) else {}
    history_section = _history_section(history_payload, year)

    policy = result.get("policy_summary") if isinstance(result.get("policy_summary"), dict) else {}
    policy_source = _source_ref(policy.get("source"))
    policy_blockers = []
    if not (policy.get("subject_policy_count") or 0):
        policy_blockers.append("current_period_policy_missing")
    if not policy_source:
        policy_blockers.append("policy_filing_source_missing")
    policy_section = _section(
        status="usable" if not policy_blockers else "limited", requirement=_REQUIREMENTS["accounting_policy"],
        coverage={"subject_policy_count": policy.get("subject_policy_count") or 0, "filing_source": bool(policy_source)},
        blockers=policy_blockers, sources=[policy_source] if policy_source else [],
    )

    kam = result.get("kam_summary") if isinstance(result.get("kam_summary"), dict) else {}
    kam_source = _source_ref(kam.get("source"))
    kam_applicability = kam.get("applicability") or "applicable"
    kam_blockers = []
    if kam_applicability == "applicable":
        if not kam_source:
            kam_blockers.append("kam_current_filing_source_missing")
        if kam.get("semantic_complete") is not True:
            kam_blockers.append("kam_semantic_completion_missing")
    kam_section = _section(
        status="usable" if not kam_blockers else "limited", requirement=_REQUIREMENTS["kam"],
        coverage={"current_filing_source": bool(kam_source), "semantic_complete": kam.get("semantic_complete") is True, "row_count": len(kam.get("subject_sections") or [])},
        blockers=kam_blockers, sources=[kam_source] if kam_source else [], applicability=kam_applicability,
        not_applicable_basis=kam.get("not_applicable_basis"),
    )

    matters = result.get("audit_report_matter_summary") if isinstance(result.get("audit_report_matter_summary"), dict) else {}
    matter_source = _source_ref(matters.get("source"))
    matter_applicability = matters.get("applicability") or "applicable"
    matter_counts = matters.get("matter_counts") if isinstance(matters.get("matter_counts"), dict) else {}
    zero_classified = bool(matter_counts) and all(
        isinstance(value, dict) and (value.get("subject_count") or 0) == 0
        for value in matter_counts.values()
    )
    matter_blockers = []
    if matter_applicability == "applicable":
        if not matter_source:
            matter_blockers.append("audit_report_source_missing")
        if zero_classified and matters.get("classification_complete") is not True:
            matter_blockers.append("zero_matter_classification_incomplete")
    matter_section = _section(
        status="usable" if not matter_blockers else "limited", requirement=_REQUIREMENTS["audit_report_matters"],
        coverage={"current_audit_report_source": bool(matter_source), "classification_complete": matters.get("classification_complete") is True},
        blockers=matter_blockers, sources=[matter_source] if matter_source else [], applicability=matter_applicability,
        not_applicable_basis=matters.get("not_applicable_basis"),
    )

    section_statuses = {
        "peer_group": peer_section,
        "audit_effort": effort_section,
        "financial_risk": risk_section,
        "audit_history": history_section,
        "accounting_policy": policy_section,
        "kam": kam_section,
        "audit_report_matters": matter_section,
    }
    all_usable = all(section["status"] == "usable" for section in section_statuses.values())
    any_content = any((peers, risk_metrics, history_payload.get("history"), policy, kam, matters))
    status = "usable" if all_usable else "limited" if any_content else "missing"
    result["data_quality"] = {
        "status": status,
        "section_statuses": section_statuses,
        "requirements": {key: requirement.model_dump(mode="json") for key, requirement in _REQUIREMENTS.items()},
    }
    result["acceptance_signals"] = [
        {
            "area": signal.get("area"),
            "severity": signal.get("severity"),
            "label": PUBLIC_ACCEPTANCE_LABELS.get(signal.get("signal"), "추가 검토가 필요한 공시 기반 관찰사항입니다."),
        }
        for signal in (result.get("acceptance_signals") or [])
        if isinstance(signal, dict)
    ]
    result["next_checks"] = [
        "각 검토영역의 제한 사유와 원 공시 접수번호를 확인하세요.",
        "이 근거 매트릭스는 업무상 결정 또는 감사 결론을 제시하지 않습니다.",
    ]
    return result


def build_audit_acceptance_pack(
    company: str,
    year: int = 2025,
    peer_limit: int = 30,
    fs_strategy: str = "auto",
) -> dict[str, Any]:
    """Wrap legacy acceptance evidence with Task-4 decision contracts only."""
    legacy = _legacy_build_audit_acceptance_pack(
        company=company, year=year, peer_limit=peer_limit, fs_strategy=fs_strategy,
    )
    if not isinstance(legacy, dict) or "error" in legacy:
        return legacy
    subject = legacy.get("subject") if isinstance(legacy.get("subject"), dict) else {}
    corp_code = str(subject.get("corp_code") or company)
    history = get_audit_history(corp_code)
    return build_acceptance_evidence(
        legacy_payload={**legacy, "audit_history": history},
        audit_effort_section=None,
        audit_effort_rows=[],
    )
