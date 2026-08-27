"""Decision-ready public auditor evidence without changing legacy collectors."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal

from pydantic import BaseModel

from kreports.analysis.audit_reporting import (
    AUDIT_MATTER_KEYS,
    attach_kam_item_semantics,
    classify_audit_matter,
    get_audit_history,
    kam_section_confirmed_facts,
    kam_semantic_coverage,
)
from kreports.analysis.audit_reporting import (
    get_audit_report_sections as _get_audit_report_sections,
)
from kreports.analysis.evidence import evidence_reference_fields, parent_rcept_no
from kreports.analysis.filing_provenance import annual_filing_source
from kreports.analysis.peer_benchmarks import (
    build_audit_acceptance_pack as _legacy_build_audit_acceptance_pack,
)
from kreports.analysis.peer_benchmarks import (
    compare_peer_audit_report_matters as _legacy_compare_peer_audit_report_matters,
)
from kreports.analysis.peer_benchmarks import (
    compare_peer_kam_topics as _legacy_compare_peer_kam_topics,
)
from kreports.analysis.peer_benchmarks import (
    compare_peer_risk_profile as _legacy_compare_peer_risk_profile,
)
from kreports.analysis.peer_benchmarks import (
    select_peer_group as _legacy_select_peer_group,
)
from kreports.mcp.contracts import SectionStatusV1

PUBLIC_ACCEPTANCE_LABELS = {
    "non_audit_fee_exceeds_audit_fee": "비감사보수가 감사보수를 초과하여 독립성 검토가 필요합니다.",
    "loss_based_going_concern_flag": "손실·현금흐름 기반 계속기업 스크리닝 신호가 있습니다.",
    "audit_report_emphasis_paragraph_present": "감사보고서 강조사항 문단이 확인됩니다.",
    "audit_report_going_concern_paragraph_present": "계속기업 관련 문단이 확인됩니다.",
}
PUBLIC_AUDIT_MATTER_LABELS = {
    "basis_for_opinion": "의견근거",
    "emphasis": "강조사항",
    "going_concern": "계속기업",
    "other_matter": "기타사항",
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


def _source_matches_year(source: object, year: int) -> bool:
    if not isinstance(source, dict):
        return False
    try:
        return int(source.get("bsns_year")) == year
    except (TypeError, ValueError):
        return False


def _section(
    *,
    status: str,
    requirement: AcceptanceRequirementV1,
    coverage: dict[str, Any],
    blockers: list[str] | None = None,
    sources: list[dict[str, Any]] | None = None,
    applicability: str | None = None,
    not_applicable_basis: str | None = None,
    requested_year: int | None = None,
    source_years: list[int | None] | None = None,
) -> dict[str, Any]:
    actual_applicability = applicability or requirement.applicability
    clean_source_pairs = []
    for index, source in enumerate(sources or []):
        if not (
            isinstance(source, dict)
            and source.get("source_label")
            and source.get("source_url")
        ):
            continue
        explicit_year = (
            source_years[index]
            if source_years is not None and index < len(source_years)
            else source.get("bsns_year")
        )
        try:
            normalized_year = int(explicit_year) if explicit_year is not None else None
        except (TypeError, ValueError):
            normalized_year = None
        clean_source_pairs.append(({
            "source_label": source.get("source_label"),
            "source_url": source.get("source_url"),
            "rcept_no": source.get("rcept_no"),
        }, normalized_year))
    clean_sources = [source for source, _ in clean_source_pairs]
    normalized_coverage = {
        key: ("true" if value else "false") if isinstance(value, bool) else value
        for key, value in coverage.items()
        if value is None or isinstance(value, (int, float, str, bool))
    }
    if actual_applicability == "unknown":
        status = "limited"
        blockers = [*(blockers or []), "applicability_unknown"]
    if actual_applicability == "not_applicable":
        filing_sources = [
            (source, source_year)
            for source, source_year in clean_source_pairs
            if source.get("rcept_no")
        ]
        if not not_applicable_basis or not filing_sources:
            status = "limited"
            blockers = [*(blockers or []), "not_applicable_basis_or_source_missing"]
        elif (
            requested_year is not None
            and not any(source_year == requested_year for _, source_year in filing_sources)
        ):
            status = "limited"
            blockers = [
                *(blockers or []),
                "not_applicable_requested_year_source_missing",
            ]
        else:
            status = "usable"
            blockers = []
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


def _canonical_receipt(row: dict[str, Any]) -> None:
    row["rcept_no"] = parent_rcept_no(str(row.get("rcept_no") or ""))


def _matter_confirmed_facts(
    subject: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    year: int,
    limit: int = 4,
) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        receipt = parent_rcept_no(str(row.get("rcept_no") or ""))
        category = str(
            row.get("matter_category")
            or row.get("section_key")
            or ""
        )
        excerpt = str(row.get("body_excerpt") or "").strip()[:260]
        corp_code = str(
            row.get("corp_code")
            or subject.get("corp_code")
            or ""
        )
        corp_name = str(
            row.get("corp_name")
            or subject.get("corp_name")
            or corp_code
        )
        identity = (corp_code, category, receipt or "")
        if (
            not receipt
            or category not in PUBLIC_AUDIT_MATTER_LABELS
            or not excerpt
            or identity in seen
        ):
            continue
        seen.add(identity)
        facts.append({
            "statement": (
                f"{year}년 {corp_name} 감사보고서에서 "
                f"{PUBLIC_AUDIT_MATTER_LABELS[category]} 문단이 확인됩니다."
            ),
            "source": {
                "corp_code": corp_code,
                "corp_name": corp_name,
                "report_nm": "감사보고서",
                "bsns_year": year,
                "rcept_no": receipt,
                "section_title": (
                    row.get("section_title")
                    or PUBLIC_AUDIT_MATTER_LABELS[category]
                ),
                "section_key": category,
                "source_table": "report_sections.audit_report",
            },
            "excerpt": excerpt,
        })
        if len(facts) >= limit:
            break
    return facts


def compare_peer_kam_topics(
    company: str,
    year: int = 2025,
    peer_limit: int = 30,
    fs_strategy: str = "auto",
    _peer_group: dict | None = None,
) -> dict[str, Any]:
    """Add Task-5 KAM semantics without altering the shared peer collector."""
    legacy_result = _legacy_compare_peer_kam_topics(
        company=company,
        year=year,
        peer_limit=peer_limit,
        fs_strategy=fs_strategy,
        _peer_group=_peer_group,
    )
    if not isinstance(legacy_result, dict) or "error" in legacy_result:
        return legacy_result
    result = deepcopy(legacy_result)
    subject = result.get("subject") if isinstance(result.get("subject"), dict) else {}
    subject_sections = [
        row for row in (result.get("subject_sections") or []) if isinstance(row, dict)
    ]
    subject_kam_sections = [
        row for row in subject_sections if row.get("section_key") == "kam"
    ]
    non_kam_subject_sections = [
        row for row in subject_sections if row.get("section_key") != "kam"
    ]
    section_summary = (
        result.get("audit_report_sections")
        if isinstance(result.get("audit_report_sections"), dict)
        else {}
    )
    quality = dict(result.get("data_quality") or {})
    raw_material_count = quality.get("subject_kam_body_count")
    material_count_available = (
        isinstance(raw_material_count, int)
        and not isinstance(raw_material_count, bool)
        and raw_material_count >= 0
    )
    material_count = (
        raw_material_count
        if material_count_available
        else len(subject_kam_sections)
    )
    corp_code = str(subject.get("corp_code") or "")
    if material_count > len(subject_kam_sections) and corp_code:
        reloaded = _get_audit_report_sections(
            corp_code,
            year=year,
            section_key="kam",
            source_type="audit_report",
            limit=max(material_count, 20),
        )
        reloaded_sections = (
            reloaded.get("sections")
            if isinstance(reloaded, dict) and isinstance(reloaded.get("sections"), list)
            else []
        )
        reloaded_kam_sections = [
            deepcopy(row)
            for row in reloaded_sections
            if isinstance(row, dict) and row.get("section_key") == "kam"
        ]
        if len(reloaded_kam_sections) > len(subject_kam_sections):
            subject_kam_sections = reloaded_kam_sections
            subject_sections = [
                *subject_kam_sections,
                *non_kam_subject_sections,
            ]
    if corp_code:
        attach_kam_item_semantics(
            subject_kam_sections,
            corp_code=corp_code,
            year=year,
        )
    for row in subject_sections:
        _canonical_receipt(row)
    peer_samples = result.get("peer_section_samples")
    if isinstance(peer_samples, dict):
        for peer_code, rows in peer_samples.items():
            if not isinstance(rows, list):
                continue
            peer_rows = [row for row in rows if isinstance(row, dict)]
            attach_kam_item_semantics(peer_rows, corp_code=str(peer_code), year=year)
            for row in peer_rows:
                _canonical_receipt(row)
    for row in result.get("subject_business_report_kam_summary") or []:
        if isinstance(row, dict):
            _canonical_receipt(row)
    semantic = kam_semantic_coverage(subject_kam_sections)
    population_proved = (
        material_count_available
        and material_count == len(subject_kam_sections)
    )
    if not population_proved:
        semantic["semantic_complete"] = False
        for coverage_key in (
            "topic_coverage",
            "reason_coverage",
            "procedure_coverage",
            "source_coverage",
        ):
            coverage = dict(semantic[coverage_key])
            coverage["total"] = max(int(coverage.get("total") or 0), material_count)
            coverage["status"] = "limited"
            semantic[coverage_key] = coverage
    timeline_status = str(quality.get("status") or "missing")
    quality.update({
        "timeline_status": timeline_status,
        "semantic_complete": semantic["semantic_complete"],
        "topic_coverage": semantic["topic_coverage"],
        "reason_coverage": semantic["reason_coverage"],
        "procedure_coverage": semantic["procedure_coverage"],
        "source_coverage": semantic["source_coverage"],
        "status": "limited"
        if timeline_status == "usable" and not semantic["semantic_complete"]
        else timeline_status,
    })
    sections = dict(section_summary)
    sections.update({
        "timeline_status": timeline_status,
        "semantic_complete": semantic["semantic_complete"],
        "topic_coverage": semantic["topic_coverage"],
        "reason_coverage": semantic["reason_coverage"],
        "procedure_coverage": semantic["procedure_coverage"],
        "source_coverage": semantic["source_coverage"],
    })
    result["subject_sections"] = subject_sections
    result["data_quality"] = quality
    result["audit_report_sections"] = sections
    result["confirmed_facts"] = kam_section_confirmed_facts(
        subject,
        subject_kam_sections,
        statement_subject="대상 회사 감사보고서",
        default_year=year,
    )
    return result


def compare_peer_audit_report_matters(
    company: str,
    year: int = 2025,
    peer_limit: int = 30,
    fs_strategy: str = "auto",
    _peer_group: dict | None = None,
) -> dict[str, Any]:
    """Apply receipt and boilerplate guards after the shared peer query."""
    legacy_result = _legacy_compare_peer_audit_report_matters(
        company=company,
        year=year,
        peer_limit=peer_limit,
        fs_strategy=fs_strategy,
        _peer_group=_peer_group,
    )
    if not isinstance(legacy_result, dict) or "error" in legacy_result:
        return legacy_result
    result = deepcopy(legacy_result)
    subject_matters = [
        row for row in (result.get("subject_matters") or []) if isinstance(row, dict)
    ]
    for row in subject_matters:
        _canonical_receipt(row)
        row.update(classify_audit_matter(
            str(row.get("body_excerpt") or ""),
            str(row.get("matter_category") or row.get("section_key") or ""),
        ))
    peer_samples = result.get("peer_matter_samples")
    if isinstance(peer_samples, dict):
        for rows in peer_samples.values():
            for row in rows if isinstance(rows, list) else []:
                if isinstance(row, dict):
                    _canonical_receipt(row)
    counts = dict(result.get("matter_counts") or {})
    for key in AUDIT_MATTER_KEYS:
        bucket = dict(counts.get(key) or {})
        bucket["subject_signal_count"] = sum(
            row.get("matter_category") == key
            and row.get("acceptance_signal") is True
            for row in subject_matters
        )
        counts[key] = bucket
    result["subject_matters"] = subject_matters
    result["matter_counts"] = counts
    peer_matter_rows = [
        row
        for rows in (
            peer_samples.values()
            if isinstance(peer_samples, dict)
            else []
        )
        for row in (rows if isinstance(rows, list) else [])
        if isinstance(row, dict)
    ]
    result["confirmed_facts"] = _matter_confirmed_facts(
        (
            result.get("subject")
            if isinstance(result.get("subject"), dict)
            else {}
        ),
        [*subject_matters, *peer_matter_rows],
        year=year,
    )
    return result


def _history_section(history_payload: dict[str, Any], year: int) -> dict[str, Any]:
    history = history_payload.get("history") if isinstance(history_payload.get("history"), list) else []
    current = [row for row in history if isinstance(row, dict) and row.get("year") == year]
    prior = [row for row in history if isinstance(row, dict) and row.get("year") == year - 1]
    sources = []
    source_years: list[int | None] = []
    for row in [*current, *prior]:
        if not row.get("rcept_no"):
            continue
        source = _receipt_source(row.get("rcept_no"), label="감사인 이력 공시")
        if source:
            sources.append(source)
            source_years.append(row.get("year"))
    explicit_source = _source_ref(history_payload.get("source"))
    if explicit_source:
        sources.append(explicit_source)
        explicit_payload = history_payload.get("source")
        source_years.append(
            explicit_payload.get("bsns_year")
            if isinstance(explicit_payload, dict)
            else None
        )
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
        applicability=history_payload.get("applicability"),
        not_applicable_basis=history_payload.get("not_applicable_basis"),
        requested_year=year,
        source_years=source_years,
    )


def _audit_effort_row_evidence(
    rows: list[dict[str, Any]],
    requested_years: set[int],
) -> tuple[set[int], set[int], list[dict[str, Any]], bool]:
    complete_years: set[int] = set()
    cited_years: set[int] = set()
    sources: list[dict[str, Any]] = []
    receipts_by_year: dict[int, set[str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            row_year = int(row.get("year"))
        except (TypeError, ValueError):
            continue
        if row_year not in requested_years or row.get("input_status") != "usable":
            continue
        raw_financial_source = row.get("financial_source")
        raw_audit_source = row.get("audit_source")
        financial_source = (
            _source_ref(raw_financial_source)
            if _source_matches_year(raw_financial_source, row_year)
            else None
        )
        audit_source = (
            _source_ref(raw_audit_source)
            if _source_matches_year(raw_audit_source, row_year)
            else None
        )
        if not financial_source or not audit_source:
            continue
        normalized_receipts = {
            receipt
            for receipt in (
                parent_rcept_no(str(raw_financial_source.get("rcept_no") or ""))
                if isinstance(raw_financial_source, dict)
                else None,
                parent_rcept_no(str(raw_audit_source.get("rcept_no") or ""))
                if isinstance(raw_audit_source, dict)
                else None,
            )
            if receipt
        }
        if not normalized_receipts:
            continue
        complete_years.add(row_year)
        cited_years.add(row_year)
        receipts_by_year.setdefault(row_year, set()).update(normalized_receipts)
        sources.extend([financial_source, audit_source])
    distinct_year_receipts = all(
        not (receipts_by_year.get(left, set()) & receipts_by_year.get(right, set()))
        for left in requested_years
        for right in requested_years
        if left < right
    )
    return complete_years, cited_years, sources, distinct_year_receipts


def _requested_year_effort_sources(
    rows: list[dict[str, Any]],
    requested_year: int,
) -> list[dict[str, Any]]:
    sources = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            row_year = int(row.get("year"))
        except (TypeError, ValueError):
            continue
        if (
            row_year != requested_year
            or row.get("input_status") != "not_applicable"
        ):
            continue
        for key in ("financial_source", "audit_source"):
            raw_source = row.get(key)
            if not _source_matches_year(raw_source, requested_year):
                continue
            source = _source_ref(raw_source)
            if source and source not in sources:
                sources.append(source)
    return sources


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
    selected_peers = peer_group.get("selected_peers")
    sample_peers = peer_group.get("sample_peers")
    peers = (
        selected_peers
        if isinstance(selected_peers, list)
        else
        sample_peers
        if isinstance(sample_peers, list)
        else peer_group.get("peers")
        if isinstance(peer_group.get("peers"), list)
        else []
    )
    selection = peer_group.get("selection_policy") if isinstance(peer_group.get("selection_policy"), dict) else {}
    peer_source = _source_ref(peer_group.get("source"))
    peer_blockers = []
    if not selection or not any(value for value in selection.values()):
        peer_blockers.append("peer_selection_basis_missing")
    if len(peers) < 5:
        peer_blockers.append("included_peer_count_below_5")
    if peer_group.get("cohort_identity_verified") is False:
        peer_blockers.append("peer_cohort_identity_mismatch")
    raw_peer_source = peer_group.get("source")
    peer_section = _section(
        status="usable" if not peer_blockers else "limited",
        requirement=_REQUIREMENTS["peer_group"],
        coverage={"selection_basis": bool(selection), "included_peers": len(peers)},
        blockers=peer_blockers,
        sources=[peer_source] if peer_source else [],
        applicability=peer_group.get("applicability"),
        not_applicable_basis=peer_group.get("not_applicable_basis"),
        requested_year=year,
        source_years=[
            raw_peer_source.get("bsns_year")
            if isinstance(raw_peer_source, dict)
            else None
        ] if peer_source else [],
    )

    if audit_effort_section is None:
        effort_section = _section(
            status="limited", requirement=_REQUIREMENTS["audit_effort"],
            coverage={"requested_years": 3, "complete_years": 0, "cited_years": 0, "row_count": len(audit_effort_rows)},
            blockers=["audit_effort_helper_not_integrated"],
        )
    else:
        supplied = audit_effort_section.model_dump(mode="json")
        requested_years = {year, year - 1, year - 2}
        (
            complete_years,
            cited_years,
            row_sources,
            distinct_year_receipts,
        ) = _audit_effort_row_evidence(
            audit_effort_rows,
            requested_years,
        )
        coverage = {
            "requested_years": len(requested_years),
            "complete_years": len(complete_years),
            "cited_years": len(cited_years),
            "row_count": len(audit_effort_rows),
        }
        blockers = list(supplied.get("blockers") or [])
        valid = (
            supplied.get("status") == "usable"
            and complete_years == requested_years
            and cited_years == requested_years
            and distinct_year_receipts
        )
        if not valid:
            blockers.append("audit_effort_three_year_cited_coverage_missing")
        if not distinct_year_receipts:
            blockers.append("audit_effort_distinct_year_receipts_missing")
        supplied_sources = supplied.get("sources") or []
        effort_source_years: list[int | None] = []
        if supplied.get("applicability") == "not_applicable":
            verified_sources = _requested_year_effort_sources(
                audit_effort_rows,
                year,
            )
            effort_sources = verified_sources or supplied_sources
            effort_source_years = (
                [year] * len(verified_sources)
                if verified_sources
                else [None] * len(supplied_sources)
            )
        else:
            effort_sources = row_sources
        effort_section = _section(
            status="usable" if valid else "limited",
            requirement=_REQUIREMENTS["audit_effort"], coverage=coverage,
            blockers=blockers, sources=effort_sources,
            applicability=supplied.get("applicability"),
            not_applicable_basis=supplied.get("not_applicable_basis"),
            requested_year=year,
            source_years=effort_source_years,
        )

    risk_summary = (
        dict(result["risk_summary"])
        if isinstance(result.get("risk_summary"), dict)
        else {}
    )
    risk_summary.setdefault("metric_rows", _risk_metric_rows(risk_summary))
    result["risk_summary"] = risk_summary
    risk_metrics = risk_summary.get("subject_metrics") if isinstance(risk_summary.get("subject_metrics"), dict) else {}
    benchmarks = risk_summary.get("benchmarks") if isinstance(risk_summary.get("benchmarks"), dict) else {}
    required_risk = {"receivables_to_revenue", "inventory_to_revenue", "op_cf_to_operating_profit", "accrual_ratio", "beneish_m_score"}
    risk_missing_subject = sorted(metric for metric in required_risk if risk_metrics.get(metric) is None)
    risk_missing_peer = sorted(
        metric for metric in required_risk
        if not isinstance(benchmarks.get(metric), dict) or (benchmarks[metric].get("n") or 0) < 5
    )
    raw_risk_source = risk_summary.get("source")
    risk_source = (
        _source_ref(raw_risk_source)
        if _source_matches_year(raw_risk_source, year)
        else None
    )
    cited_risk_facts = []
    for fact in risk_summary.get("confirmed_facts") or []:
        if not isinstance(fact, dict):
            continue
        fact_source_payload = fact.get("source")
        fact_source = (
            _source_ref(fact_source_payload)
            if _source_matches_year(fact_source_payload, year)
            else None
        )
        if (
            fact_source
            and risk_source
            and isinstance(fact.get("statement"), str)
            and fact["statement"].strip()
            and fact_source.get("rcept_no")
            and fact_source.get("rcept_no") == risk_source.get("rcept_no")
        ):
            cited_risk_facts.append(fact)
    risk_applicability = risk_summary.get("applicability") or "applicable"
    risk_blockers = []
    if risk_applicability == "applicable":
        if risk_missing_subject:
            risk_blockers.append("required_subject_metrics_missing")
        if risk_missing_peer:
            risk_blockers.append("required_peer_metric_coverage_missing")
        if not risk_source or not risk_source.get("rcept_no"):
            risk_blockers.append("financial_risk_filing_source_missing")
        if not cited_risk_facts:
            risk_blockers.append("financial_risk_confirmed_fact_missing")
    section_risk_source = _source_ref(raw_risk_source)
    risk_section = _section(
        status="usable" if not risk_blockers else "limited", requirement=_REQUIREMENTS["financial_risk"],
        coverage={"subject_metric_count": len(required_risk) - len(risk_missing_subject), "peer_metric_count": len(required_risk) - len(risk_missing_peer)},
        blockers=risk_blockers,
        sources=[section_risk_source] if section_risk_source else [],
        applicability=risk_applicability,
        not_applicable_basis=risk_summary.get("not_applicable_basis"),
        requested_year=year,
        source_years=[
            raw_risk_source.get("bsns_year")
            if isinstance(raw_risk_source, dict)
            else None
        ] if section_risk_source else [],
    )

    history_payload = result.get("audit_history") if isinstance(result.get("audit_history"), dict) else {}
    history_section = _history_section(history_payload, year)

    policy = result.get("policy_summary") if isinstance(result.get("policy_summary"), dict) else {}
    raw_policy_source = policy.get("source")
    policy_source = _source_ref(raw_policy_source)
    current_policy_source = (
        policy_source
        if _source_matches_year(raw_policy_source, year)
        else None
    )
    policy_applicability = policy.get("applicability") or "applicable"
    policy_blockers = []
    if policy_applicability == "applicable":
        if not (policy.get("subject_policy_count") or 0):
            policy_blockers.append("current_period_policy_missing")
        if not current_policy_source:
            policy_blockers.append("policy_current_year_source_missing")
    policy_section_source = (
        policy_source
        if policy_applicability == "not_applicable"
        else current_policy_source
    )
    policy_section = _section(
        status="usable" if not policy_blockers else "limited", requirement=_REQUIREMENTS["accounting_policy"],
        coverage={"subject_policy_count": policy.get("subject_policy_count") or 0, "filing_source": bool(current_policy_source)},
        blockers=policy_blockers,
        sources=[policy_section_source] if policy_section_source else [],
        applicability=policy_applicability,
        not_applicable_basis=policy.get("not_applicable_basis"),
        requested_year=year,
        source_years=[
            policy.get("source", {}).get("bsns_year")
            if isinstance(policy.get("source"), dict)
            else None
        ] if policy_section_source else [],
    )

    kam = result.get("kam_summary") if isinstance(result.get("kam_summary"), dict) else {}
    raw_kam_source = kam.get("source")
    kam_source = _source_ref(raw_kam_source)
    current_kam_source = (
        kam_source
        if _source_matches_year(raw_kam_source, year)
        else None
    )
    kam_applicability = kam.get("applicability") or "applicable"
    kam_blockers = []
    if kam_applicability == "applicable":
        if not current_kam_source:
            kam_blockers.append("kam_current_year_source_missing")
        if kam.get("semantic_complete") is not True:
            kam_blockers.append("kam_semantic_completion_missing")
    kam_section_source = (
        kam_source
        if kam_applicability == "not_applicable"
        else current_kam_source
    )
    # KAM rows establish only timeline existence.  The legacy producer must
    # provide semantic_complete explicitly after checking every current-period
    # item and its receipt-linked source; this wrapper must never infer it from
    # a row count or a non-empty subject_sections list.
    kam_section = _section(
        status="usable" if not kam_blockers else "limited", requirement=_REQUIREMENTS["kam"],
        coverage={
            "current_filing_source": bool(current_kam_source),
            "semantic_complete": kam.get("semantic_complete") is True,
        },
        blockers=kam_blockers,
        sources=[kam_section_source] if kam_section_source else [],
        applicability=kam_applicability,
        not_applicable_basis=kam.get("not_applicable_basis"),
        requested_year=year,
        source_years=[
            kam.get("source", {}).get("bsns_year")
            if isinstance(kam.get("source"), dict)
            else None
        ] if kam_section_source else [],
    )

    matters = result.get("audit_report_matter_summary") if isinstance(result.get("audit_report_matter_summary"), dict) else {}
    raw_matter_source = matters.get("source")
    matter_source = _source_ref(raw_matter_source)
    current_matter_source = (
        matter_source
        if _source_matches_year(raw_matter_source, year)
        else None
    )
    matter_applicability = matters.get("applicability") or "applicable"
    matter_counts = matters.get("matter_counts") if isinstance(matters.get("matter_counts"), dict) else {}
    zero_classified = bool(matter_counts) and all(
        isinstance(value, dict) and (value.get("subject_count") or 0) == 0
        for value in matter_counts.values()
    )
    matter_blockers = []
    if matter_applicability == "applicable":
        if not current_matter_source:
            matter_blockers.append("audit_report_current_year_source_missing")
        if zero_classified and matters.get("classification_complete") is not True:
            matter_blockers.append("zero_matter_classification_incomplete")
    matter_section_source = (
        matter_source
        if matter_applicability == "not_applicable"
        else current_matter_source
    )
    matter_section = _section(
        status="usable" if not matter_blockers else "limited", requirement=_REQUIREMENTS["audit_report_matters"],
        coverage={"current_audit_report_source": bool(current_matter_source), "classification_complete": matters.get("classification_complete") is True},
        blockers=matter_blockers,
        sources=[matter_section_source] if matter_section_source else [],
        applicability=matter_applicability,
        not_applicable_basis=matters.get("not_applicable_basis"),
        requested_year=year,
        source_years=[
            matters.get("source", {}).get("bsns_year")
            if isinstance(matters.get("source"), dict)
            else None
        ] if matter_section_source else [],
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
    accepted_sources = []
    seen_sources: set[str] = set()
    for section in section_statuses.values():
        if section["status"] != "usable":
            continue
        for source in section.get("sources") or []:
            key = str(
                source.get("rcept_no")
                or source.get("source_url")
                or source.get("source_label")
            )
            if key in seen_sources:
                continue
            seen_sources.add(key)
            accepted_sources.append(source)
    promoted_facts = [
        fact
        for fact in (result.get("confirmed_facts") or [])
        if isinstance(fact, dict)
        and isinstance(fact.get("statement"), str)
        and fact["statement"].strip()
        and isinstance(fact.get("source"), dict)
    ]
    for fact in cited_risk_facts:
        if fact not in promoted_facts:
            promoted_facts.append(fact)
    promoted_source_urls = {
        reference["source_url"]
        for fact in promoted_facts
        if isinstance(fact.get("source"), dict)
        for reference in [_source_ref(fact["source"])]
        if reference
    }
    for source in accepted_sources:
        reference = _source_ref(source)
        if not reference or reference["source_url"] in promoted_source_urls:
            continue
        promoted_facts.append({
            "statement": (
                f"{reference.get('source_label') or '검토영역'}의 공시 근거가 "
                "요청된 검토 매트릭스에 포함되었습니다."
            ),
            "source": reference,
        })
        promoted_source_urls.add(reference["source_url"])
    result["sources"] = accepted_sources
    result["confirmed_facts"] = promoted_facts
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
    audit_effort_section: SectionStatusV1 | None = None,
    audit_effort_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Wrap legacy acceptance evidence with Task-4 decision contracts only."""
    selected_cohort = _legacy_select_peer_group(
        company=company,
        year=year,
        peer_limit=peer_limit,
        fs_strategy=fs_strategy,
    )
    if not isinstance(selected_cohort, dict) or "error" in selected_cohort:
        return selected_cohort
    legacy = _legacy_build_audit_acceptance_pack(
        company=company, year=year, peer_limit=peer_limit, fs_strategy=fs_strategy,
    )
    if not isinstance(legacy, dict) or "error" in legacy:
        return legacy
    legacy_peer_group = (
        dict(legacy.get("peer_group"))
        if isinstance(legacy.get("peer_group"), dict)
        else {}
    )
    legacy_peer_count = legacy_peer_group.get("peer_count")
    selected_peers = (
        selected_cohort.get("peers")
        if isinstance(selected_cohort.get("peers"), list)
        else []
    )
    legacy_sample = (
        legacy_peer_group.get("sample_peers")
        if isinstance(legacy_peer_group.get("sample_peers"), list)
        else []
    )
    legacy_full_peers = (
        legacy_peer_group.get("selected_peers")
        if isinstance(legacy_peer_group.get("selected_peers"), list)
        else legacy_peer_group.get("peers")
        if isinstance(legacy_peer_group.get("peers"), list)
        else None
    )
    selected_subject = (
        selected_cohort.get("subject")
        if isinstance(selected_cohort.get("subject"), dict)
        else {}
    )
    legacy_subject = (
        legacy.get("subject")
        if isinstance(legacy.get("subject"), dict)
        else {}
    )
    legacy_identity_peers = (
        legacy_full_peers
        if legacy_full_peers is not None
        else legacy_sample
        if legacy_peer_group.get("peer_count") == len(legacy_sample)
        else None
    )
    cohort_identity_verified = (
        bool(selected_peers)
        and legacy_identity_peers is not None
        and legacy_peer_count == selected_cohort.get("peer_count")
        and legacy_peer_group.get("selection_policy")
        == selected_cohort.get("selection_policy")
        and str(legacy_subject.get("corp_code") or "")
        == str(selected_subject.get("corp_code") or "")
        and [
            str(peer.get("corp_code") or "")
            for peer in legacy_identity_peers
            if isinstance(peer, dict)
        ]
        == [
            str(peer.get("corp_code") or "")
            for peer in selected_peers
            if isinstance(peer, dict)
        ]
    )
    legacy_peer_group["selected_peers"] = selected_peers
    legacy_peer_group["legacy_peer_count"] = legacy_peer_count
    legacy_peer_group["peer_count"] = len(selected_peers)
    legacy_peer_group["cohort_identity_verified"] = cohort_identity_verified
    if selected_cohort.get("source") and not legacy_peer_group.get("source"):
        legacy_peer_group["source"] = selected_cohort["source"]
    legacy["peer_group"] = legacy_peer_group
    subject = legacy.get("subject") if isinstance(legacy.get("subject"), dict) else {}
    corp_code = str(subject.get("corp_code") or company)
    history = get_audit_history(corp_code)
    risk = compare_peer_risk_profile(
        company=company,
        year=year,
        peer_limit=peer_limit,
        fs_strategy=fs_strategy,
        _peer_group=selected_cohort,
    )
    if isinstance(risk, dict) and "error" not in risk:
        confirmed_facts = [
            fact for fact in (risk.get("confirmed_facts") or [])
            if isinstance(fact, dict)
        ]
        source = (
            confirmed_facts[0].get("source")
            if confirmed_facts
            and isinstance(confirmed_facts[0].get("source"), dict)
            else None
        )
        legacy["risk_summary"] = {
            "subject_metrics": risk.get("subject_metrics"),
            "benchmarks": risk.get("benchmarks"),
            "metric_rows": risk.get("metric_rows"),
            "disclosure_event_counts": risk.get("disclosure_event_counts"),
            "source": source,
            "confirmed_facts": confirmed_facts,
            "data_quality": risk.get("data_quality"),
        }
    kam = compare_peer_kam_topics(
        company=company, year=year, peer_limit=peer_limit, fs_strategy=fs_strategy,
    )
    if isinstance(kam, dict) and "error" not in kam:
        sections = kam.get("audit_report_sections")
        sections = dict(sections) if isinstance(sections, dict) else {}
        source = next((
            {
                "rcept_no": row.get("rcept_no"),
                "bsns_year": year,
                "section_title": row.get("section_title") or "핵심감사사항",
            }
            for row in (kam.get("subject_sections") or [])
            if isinstance(row, dict) and row.get("rcept_no")
        ), None)
        legacy["kam_summary"] = {
            **(legacy.get("kam_summary") or {}),
            "source": source,
            "semantic_complete": sections.get("semantic_complete") is True,
            "timeline_status": sections.get("timeline_status"),
            "topic_coverage": sections.get("topic_coverage"),
            "reason_coverage": sections.get("reason_coverage"),
            "procedure_coverage": sections.get("procedure_coverage"),
            "source_coverage": sections.get("source_coverage"),
        }
    matters = compare_peer_audit_report_matters(
        company=company, year=year, peer_limit=peer_limit, fs_strategy=fs_strategy,
    )
    matter_signal_names = {
        "audit_report_emphasis_paragraph_present",
        "audit_report_going_concern_paragraph_present",
        "audit_report_other_matter_paragraph_present",
    }
    legacy["acceptance_signals"] = [
        signal
        for signal in (legacy.get("acceptance_signals") or [])
        if not (
            isinstance(signal, dict)
            and signal.get("signal") in matter_signal_names
        )
    ]
    if isinstance(matters, dict) and "error" not in matters:
        subject_matters = matters.get("subject_matters") or []
        source = next((
            {
                "rcept_no": row.get("rcept_no"),
                "bsns_year": year,
                "section_title": row.get("section_title") or "감사보고서 사항",
            }
            for row in subject_matters
            if isinstance(row, dict) and row.get("rcept_no")
        ), None)
        legacy["audit_report_matter_summary"] = {
            **(legacy.get("audit_report_matter_summary") or {}),
            "matter_counts": matters.get("matter_counts") or {},
            "subject_matters": subject_matters[:5],
            "source": source,
            "classification_complete": all(
                isinstance(row, dict)
                and row.get("matter_category") in AUDIT_MATTER_KEYS
                for row in subject_matters
            ),
        }
        matter_signal_by_key = {
            "emphasis": ("audit_report_matters", "review", "audit_report_emphasis_paragraph_present"),
            "going_concern": ("going_concern", "review", "audit_report_going_concern_paragraph_present"),
            "other_matter": ("audit_report_matters", "info", "audit_report_other_matter_paragraph_present"),
        }
        for matter_key, (area, severity, signal_name) in matter_signal_by_key.items():
            count = (matters.get("matter_counts") or {}).get(matter_key) or {}
            if count.get("subject_signal_count"):
                legacy["acceptance_signals"].append({
                    "area": area,
                    "severity": severity,
                    "signal": signal_name,
                })
    return build_acceptance_evidence(
        legacy_payload={**legacy, "audit_history": history},
        audit_effort_section=audit_effort_section,
        audit_effort_rows=audit_effort_rows or [],
    )
