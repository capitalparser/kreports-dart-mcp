"""Deterministic company-year feature quality ledger.

The ledger records factual source coverage separately from product grades.  It
is derived only from already-collected rows and never performs DART collection.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
import json
from typing import Any

from sqlalchemy import func

from kreports.db.engine import get_session
from kreports.db.models import (
    AccountingNoteChapter,
    AccountingPolicyItem,
    Auditor,
    AuditProcedureItem,
    BusinessAffiliateAuditor,
    Company,
    CompanyYearQuality,
    Disclosure,
    ExtractionRun,
    FetchLog,
    FinancialFactCompact,
    GroupEntityRecord,
    GroupRelationshipRecord,
    GroupComponentMetricRecord,
    ReportDocument,
    ReportSection,
    SourceDocument,
)
from kreports.runtime import require_runtime_write
from kreports.semantic.metrics import CORE_FINANCIAL_METRICS
from kreports.db.quality_snapshot import QUALITY_VERSION
from kreports.analysis.audit_reporting import audit_fee_availability


ANNUAL_CORE_YEARS_FOR_A = 5
ANNUAL_CORE_YEARS_FOR_B = 3
FULL_BODY_MIN_CHARS = 120
EXPLICIT_NO_KAM_MARKERS = (
    "핵심감사사항이 없습니다",
    "보고할 핵심감사사항이 없습니다",
    "핵심감사사항 없음",
    "no key audit matters",
)
KAM_PROCEDURE_MARKERS = (
    "감사절차",
    "수행하였습니다",
    "어떻게 다루",
    "감사인은 다음",
)

# Public constants make grading reviewable without reverse-engineering query
# branches.  Status names refer to factual evidence, not product availability.
INVESTOR_GRADE_RULES = {
    "A": {
        "annual_core_years": ANNUAL_CORE_YEARS_FOR_A,
        "current_disclosure_list": "available",
        "forbidden_statuses": ("error",),
    },
    "B": {
        "annual_core_years": ANNUAL_CORE_YEARS_FOR_B,
        "forbidden_statuses": ("error",),
    },
}
AUDITOR_GRADE_RULES = {
    "A": {
        "auditor": ("available",),
        "kam": ("full_body", "explicit_no_kam"),
        "policy": ("full_body",),
        "audit_fee": ("available", "not_available"),
    },
    "B": {
        "auditor": ("available",),
        "kam": ("full_body", "summary_only", "explicit_no_kam"),
        "policy": ("full_body", "summary_only"),
        "audit_fee": ("available", "not_available"),
    },
}
GROUP_AUDIT_GRADE_RULES = {
    "A": (
        "component_graph",
        "ownership",
        "denominator_metrics",
        "qsc_classification",
    ),
}


def _financial_statuses(
    corp_code: str,
    year_from: int,
    year_to: int,
) -> dict[int, str]:
    statuses = {year: "missing" for year in range(year_from, year_to + 1)}
    with get_session() as session:
        fetch_outcomes = (
                session.query(FetchLog.year, FetchLog.status)
                .filter(
                    FetchLog.corp_code == corp_code,
                    FetchLog.year.between(year_from, year_to),
                    FetchLog.task_type.in_(("financial", "financials")),
                    FetchLog.status.in_(("error", "no_data")),
                )
                .all()
        )
        errors = {
            int(year)
            for year, status in fetch_outcomes
            if year is not None and status == "error"
        }
        not_available = {
            int(year)
            for year, status in fetch_outcomes
            if year is not None and status == "no_data"
        }
        rows = (
            session.query(
                FinancialFactCompact.bsns_year,
                FinancialFactCompact.fs_div,
                FinancialFactCompact.metric_key,
                FinancialFactCompact.amount,
            )
            .filter(
                FinancialFactCompact.corp_code == corp_code,
                FinancialFactCompact.bsns_year.between(year_from, year_to),
                FinancialFactCompact.metric_key.in_(CORE_FINANCIAL_METRICS),
            )
            .all()
        )

    metrics_by_year_fs: dict[tuple[int, str], set[str]] = defaultdict(set)
    for bsns_year, fs_div, metric_key, amount in rows:
        if amount is not None:
            metrics_by_year_fs[(int(bsns_year), str(fs_div))].add(str(metric_key))
    required = set(CORE_FINANCIAL_METRICS)
    for year in statuses:
        if year in errors:
            statuses[year] = "error"
            continue
        best = max(
            (
                len(metrics)
                for (metric_year, _), metrics in metrics_by_year_fs.items()
                if metric_year == year
            ),
            default=0,
        )
        statuses[year] = (
            "available"
            if best == len(required)
            else "partial"
            if best
            else "not_available"
            if year in not_available
            else "missing"
        )
    return statuses


def _has_disclosure_list_evidence(corp_code: str, year: int) -> bool:
    with get_session() as session:
        return bool(
            session.query(Disclosure.rcept_no)
            .filter(
                Disclosure.corp_code == corp_code,
                Disclosure.disc_date >= date(year, 1, 1),
                Disclosure.disc_date <= date(year, 12, 31),
            )
            .first()
        )


def _investor_grade(
    corp_code: str,
    year: int,
    statuses: dict[int, str],
) -> tuple[str, list[str]]:
    window = [statuses.get(value, "missing") for value in range(year - 4, year + 1)]
    available_years = sum(status == "available" for status in window)
    blockers: list[str] = []
    if "error" in window:
        blockers.append("financial_core_error")
    disclosure_available = _has_disclosure_list_evidence(corp_code, year)
    if available_years == ANNUAL_CORE_YEARS_FOR_A and disclosure_available and not blockers:
        return "A", blockers
    if available_years >= ANNUAL_CORE_YEARS_FOR_B and not blockers:
        if not disclosure_available:
            blockers.append("current_disclosure_list_missing")
        return "B", blockers
    blockers.append("insufficient_annual_core_years")
    return "D", sorted(set(blockers))


def _audit_fee_peer_grade(corp_code: str, year: int) -> str:
    statuses = [
        audit_fee_availability(corp_code, candidate)
        for candidate in range(year - 4, year + 1)
    ]
    blocking_statuses = {
        "transport_error",
        "parse_error",
        "conflict",
    }
    if any(
        item.get("availability_status") in blocking_statuses
        for item in statuses
    ):
        return "D"
    eligible = [
        item
        for item in statuses
        if item.get("source_eligibility") == "eligible"
    ]
    if not eligible:
        return "not_applicable"
    available = sum(
        item.get("availability_status") in {"available", "conflict"}
        and item.get("selected", {}).get("audit_fee_m") is not None
        and item.get("selected", {}).get("audit_hours") is not None
        for item in eligible
    )
    coverage = available / len(eligible)
    return "A" if coverage >= 0.8 else "B" if coverage >= 0.6 else "C" if available else "D"


def _fetch_outcome(
    corp_code: str,
    year: int,
    task_types: tuple[str, ...],
) -> str | None:
    with get_session() as session:
        statuses = {
            str(status)
            for (status,) in (
                session.query(FetchLog.status)
                .filter(
                    FetchLog.corp_code == corp_code,
                    FetchLog.year == year,
                    FetchLog.task_type.in_(task_types),
                )
                .all()
            )
        }
    if "error" in statuses:
        return "error"
    if "no_data" in statuses:
        return "not_available"
    return None


def _latest_fetch_outcome(
    corp_code: str,
    year: int,
    task_types: tuple[str, ...],
) -> str | None:
    with get_session() as session:
        latest = (
            session.query(FetchLog.status)
            .filter(
                FetchLog.corp_code == corp_code,
                FetchLog.year == year,
                FetchLog.task_type.in_(task_types),
            )
            .order_by(
                FetchLog.fetched_at.desc(),
                FetchLog.id.desc(),
            )
            .first()
        )
        latest_status = str(latest.status) if latest else None
    if latest_status == "error":
        return "error"
    if latest_status == "no_data":
        return "not_available"
    return None


def _auditor_status(corp_code: str, year: int) -> str:
    fetch_outcome = _fetch_outcome(corp_code, year, ("auditor", "auditors"))
    if fetch_outcome == "error":
        return "error"
    with get_session() as session:
        rows = (
            session.query(Auditor.auditor_nm, Auditor.audit_opinion)
            .filter(
                Auditor.corp_code == corp_code,
                Auditor.bsns_year == year,
            )
            .all()
        )
    if any(auditor_nm and audit_opinion for auditor_nm, audit_opinion in rows):
        return "available"
    if rows:
        return "partial"
    return fetch_outcome or "missing"


def _audit_fee_status(corp_code: str, year: int) -> str:
    availability = audit_fee_availability(corp_code, year)
    status = availability.get("availability_status")
    if status in {"transport_error", "parse_error", "conflict", "schema_unavailable"}:
        return "error"
    if status == "available":
        return "available"
    if status == "partial":
        return "partial"
    if status == "not_available_from_endpoint":
        return "not_available"
    return "missing"


def _policy_status(corp_code: str, year: int) -> str:
    fetch_outcome = _fetch_outcome(
        corp_code,
        year,
        ("policy", "policy_items", "accounting_policy"),
    )
    if fetch_outcome == "error":
        return "error"
    with get_session() as session:
        bodies = [
            str(body or "")
            for (body,) in (
                session.query(AccountingPolicyItem.body)
                .filter(
                    AccountingPolicyItem.corp_code == corp_code,
                    AccountingPolicyItem.bsns_year == year,
                )
                .all()
            )
        ]
        bodies.extend(
            str(body or "")
            for (body,) in (
                session.query(AccountingNoteChapter.body)
                .filter(
                    AccountingNoteChapter.corp_code == corp_code,
                    AccountingNoteChapter.bsns_year == year,
                    AccountingNoteChapter.section_type == "policy",
                )
                .all()
            )
        )
    if not bodies:
        return fetch_outcome or "missing"
    return (
        "full_body"
        if max(len(body.strip()) for body in bodies) >= FULL_BODY_MIN_CHARS
        else "summary_only"
    )


def _kam_and_procedure_status(corp_code: str, year: int) -> tuple[str, str]:
    fetch_outcome = _fetch_outcome(
        corp_code,
        year,
        (
            "kam",
            "kam_sections",
            "audit_report_sections",
            "audit_report_section",
        ),
    )
    if fetch_outcome == "error":
        return "error", "error"
    with get_session() as session:
        receipt_candidates = [
            value
            for value in (
                session.query(func.max(ReportDocument.rcept_no))
                .filter(
                    ReportDocument.corp_code == corp_code,
                    ReportDocument.bsns_year == year,
                    ReportDocument.source_type == "audit_report",
                )
                .scalar(),
                session.query(func.max(ReportSection.rcept_no))
                .filter(
                    ReportSection.corp_code == corp_code,
                    ReportSection.bsns_year == year,
                    ReportSection.source_type == "audit_report",
                )
                .scalar(),
                session.query(func.max(SourceDocument.rcept_no))
                .filter(
                    SourceDocument.corp_code == corp_code,
                    SourceDocument.bsns_year == year,
                    SourceDocument.source_type == "audit_report",
                )
                .scalar(),
            )
            if value is not None
        ]
        selected_receipt = (
            max(str(value) for value in receipt_candidates)
            if receipt_candidates
            else None
        )
        extraction_error = bool(
            selected_receipt
            and session.query(ExtractionRun.id)
            .join(
                SourceDocument,
                SourceDocument.id == ExtractionRun.source_document_id,
            )
            .filter(
                SourceDocument.corp_code == corp_code,
                SourceDocument.bsns_year == year,
                SourceDocument.source_type == "audit_report",
                SourceDocument.rcept_no == selected_receipt,
                ExtractionRun.extractor_name.in_(
                    (
                        "all",
                        "sections",
                        "document_features",
                        "kam_sections",
                        "audit_procedure_items",
                    )
                ),
                ExtractionRun.status == "error",
            )
            .first()
        )
        bodies = [
            str(body or "").strip()
            for (body,) in (
                session.query(ReportSection.body_text)
                .filter(
                    ReportSection.corp_code == corp_code,
                    ReportSection.bsns_year == year,
                    ReportSection.source_type == "audit_report",
                    ReportSection.rcept_no == selected_receipt,
                    ReportSection.section_key.in_(
                        ("kam", "key_audit_matters", "kam_matters")
                    ),
                )
                .all()
            )
        ]
        procedure_count = int(
            session.query(func.count(AuditProcedureItem.id))
            .filter(
                AuditProcedureItem.corp_code == corp_code,
                AuditProcedureItem.bsns_year == year,
                AuditProcedureItem.rcept_no == selected_receipt,
            )
            .scalar()
            or 0
        )
    if extraction_error:
        return "error", "error"
    lowered_bodies = [body.lower() for body in bodies]
    if any(
        marker in body
        for body in lowered_bodies
        for marker in EXPLICIT_NO_KAM_MARKERS
    ):
        return "explicit_no_kam", "not_applicable"
    if not bodies:
        return fetch_outcome or "missing", "missing"
    has_full_body = any(
        len(body) >= FULL_BODY_MIN_CHARS
        and any(marker in body.lower() for marker in KAM_PROCEDURE_MARKERS)
        for body in bodies
    )
    kam_status = "full_body" if has_full_body else "summary_only"
    procedure_status = (
        "available"
        if kam_status == "full_body" and procedure_count
        else "missing"
    )
    return kam_status, procedure_status


def _auditor_grade(
    *,
    auditor_status: str,
    audit_fee_status: str,
    policy_status: str,
    kam_status: str,
) -> str:
    if "error" in {
        auditor_status,
        audit_fee_status,
        policy_status,
        kam_status,
    }:
        return "D"
    rule_a = AUDITOR_GRADE_RULES["A"]
    if (
        auditor_status in rule_a["auditor"]
        and kam_status in rule_a["kam"]
        and policy_status in rule_a["policy"]
        and audit_fee_status in rule_a["audit_fee"]
    ):
        return "A"
    rule_b = AUDITOR_GRADE_RULES["B"]
    if (
        auditor_status in rule_b["auditor"]
        and kam_status in rule_b["kam"]
        and policy_status in rule_b["policy"]
        and audit_fee_status in rule_b["audit_fee"]
    ):
        return "B"
    return "D"


def _group_audit_status_and_grade(corp_code: str, year: int) -> tuple[str, str]:
    fetch_outcome = _fetch_outcome(
        corp_code,
        year,
        ("subsidiary_matrix", "group_audit"),
    )
    if fetch_outcome == "error":
        return "error", "D"
    with get_session() as session:
        selected_receipt = (
            session.query(func.max(GroupEntityRecord.source_rcept_no))
            .filter(
                GroupEntityRecord.parent_corp_code == corp_code,
                GroupEntityRecord.effective_year == year,
            )
            .scalar()
        )
        canonical_entities = []
        canonical_relationships = []
        canonical_metrics = []
        if selected_receipt:
            canonical_entities = (
                session.query(
                    GroupEntityRecord.entity_key,
                    GroupEntityRecord.resolution_reason,
                )
                .filter(
                    GroupEntityRecord.parent_corp_code == corp_code,
                    GroupEntityRecord.effective_year == year,
                    GroupEntityRecord.source_rcept_no == selected_receipt,
                )
                .all()
            )
            canonical_relationships = (
                session.query(
                    GroupRelationshipRecord.relationship_key,
                    GroupRelationshipRecord.parent_entity_key,
                    GroupRelationshipRecord.child_entity_key,
                    GroupRelationshipRecord.relation_type,
                    GroupRelationshipRecord.ownership_pct,
                    GroupRelationshipRecord.source_rcept_no,
                )
                .filter(
                    GroupRelationshipRecord.parent_corp_code == corp_code,
                    GroupRelationshipRecord.effective_year == year,
                    GroupRelationshipRecord.source_rcept_no == selected_receipt,
                )
                .all()
            )
            canonical_metrics = (
                session.query(
                    GroupComponentMetricRecord.source_rcept_no,
                    GroupComponentMetricRecord.entity_key,
                    GroupComponentMetricRecord.metric_key,
                    GroupComponentMetricRecord.unit,
                    GroupComponentMetricRecord.share_pct,
                    GroupComponentMetricRecord.denominator_amount,
                    GroupComponentMetricRecord.denominator_unit,
                    GroupComponentMetricRecord.denominator_source_rcept_no,
                    GroupComponentMetricRecord.denominator_source_table,
                    GroupComponentMetricRecord.numerator_source_rcept_no,
                    GroupComponentMetricRecord.numerator_source_table,
                    GroupComponentMetricRecord.fs_div,
                    GroupComponentMetricRecord.period,
                    GroupComponentMetricRecord.elimination_basis,
                    GroupComponentMetricRecord.qsc_status,
                    GroupComponentMetricRecord.qsc_basis,
                    GroupComponentMetricRecord.qsc_evidence_refs_json,
                    GroupComponentMetricRecord.qsc_threshold_pct,
                    GroupComponentMetricRecord.quality_status,
                )
                .filter(
                    GroupComponentMetricRecord.parent_corp_code == corp_code,
                    GroupComponentMetricRecord.effective_year == year,
                    GroupComponentMetricRecord.source_rcept_no
                    == selected_receipt,
                )
                .all()
            )
        component_count = int(
            session.query(func.count(BusinessAffiliateAuditor.id))
            .filter(
                BusinessAffiliateAuditor.parent_corp_code == corp_code,
                BusinessAffiliateAuditor.bsns_year == year,
            )
            .scalar()
            or 0
        )
    if canonical_entities and canonical_relationships:
        entity_keys = {entity.entity_key for entity in canonical_entities}
        child_keys = {
            relationship.child_entity_key
            for relationship in canonical_relationships
        }
        metrics_by_child: dict[str, dict[str, list[tuple]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for metric in canonical_metrics:
            metrics_by_child[metric.entity_key][metric.metric_key].append(metric)
        graph_conflict = any(
            relationship.parent_entity_key not in entity_keys
            or relationship.child_entity_key not in entity_keys
            for relationship in canonical_relationships
        )
        edge_claims: dict[tuple[str, str], set[tuple[str, float | None]]] = (
            defaultdict(set)
        )
        parents_by_child: dict[str, set[str]] = defaultdict(set)
        adjacency: dict[str, set[str]] = defaultdict(set)
        for relationship in canonical_relationships:
            edge = (
                relationship.parent_entity_key,
                relationship.child_entity_key,
            )
            edge_claims[edge].add(
                (relationship.relation_type, relationship.ownership_pct)
            )
            parents_by_child[relationship.child_entity_key].add(
                relationship.parent_entity_key
            )
            adjacency[relationship.parent_entity_key].add(
                relationship.child_entity_key
            )
        graph_conflict = graph_conflict or any(
            len(claims) > 1 for claims in edge_claims.values()
        ) or any(
            len(parents) > 1 for parents in parents_by_child.values()
        ) or any(
            entity.resolution_reason in {
                "orphan_parent", "ambiguous_parent_name",
            }
            for entity in canonical_entities
        )
        graph_nodes = set(adjacency) | set(parents_by_child)
        if len(graph_nodes - set(parents_by_child)) != 1:
            graph_conflict = True

        def has_cycle(node: str, active: set[str], done: set[str]) -> bool:
            if node in active:
                return True
            if node in done:
                return False
            active.add(node)
            found = any(
                has_cycle(child, active, done)
                for child in adjacency.get(node, ())
            )
            active.remove(node)
            done.add(node)
            return found

        visited: set[str] = set()
        graph_conflict = graph_conflict or any(
            has_cycle(node, set(), visited)
            for node in sorted(graph_nodes)
        )
        ownership_complete = all(
            relationship.ownership_pct is not None
            for relationship in canonical_relationships
        )
        def metric_is_complete(metric) -> bool:
            try:
                evidence_refs = set(json.loads(
                    metric.qsc_evidence_refs_json or "[]"
                ))
            except (TypeError, ValueError):
                return False
            return bool(
                metric.source_rcept_no == selected_receipt
                and metric.share_pct is not None
                and metric.unit
                and metric.denominator_amount is not None
                and metric.denominator_unit == metric.unit
                and metric.denominator_source_rcept_no
                and metric.denominator_source_table
                and metric.numerator_source_rcept_no == selected_receipt
                and metric.numerator_source_table
                and metric.fs_div in {"CFS", "OFS"}
                and metric.period == str(year)
                and metric.elimination_basis in {
                    "before_elimination", "after_elimination",
                }
                and metric.qsc_threshold_pct == 10.0
                and metric.quality_status == "usable"
                and {
                    metric.numerator_source_rcept_no,
                    metric.denominator_source_rcept_no,
                }.issubset(evidence_refs)
            )

        def child_has_complete_qsc(child_key: str) -> bool:
            by_kind = metrics_by_child.get(child_key, {})
            if not by_kind or not set(by_kind).issubset(
                {"assets", "revenue"}
            ):
                return False
            if any(len(values) != 1 for values in by_kind.values()):
                return False
            metrics = {
                key: values[0] for key, values in by_kind.items()
            }
            statuses = {metric.qsc_status for metric in metrics.values()}
            if len(statuses) != 1:
                return False
            status = next(iter(statuses))
            if status == "qsc":
                for metric_key, metric in metrics.items():
                    expected_basis = (
                        f"{'asset' if metric_key == 'assets' else 'revenue'}"
                        "_share_pct>=10.0"
                    )
                    if (
                        metric_is_complete(metric)
                        and metric.share_pct >= 10.0
                        and expected_basis in {
                            item for item in (metric.qsc_basis or "").split("|")
                            if item
                        }
                    ):
                        return True
                return False
            if status == "not_qsc":
                if set(metrics) != {"assets", "revenue"}:
                    return False
                return all(
                    metric_is_complete(metric)
                    and metric.share_pct < 10.0
                    and not metric.qsc_basis
                    for metric in metrics.values()
                )
            return False

        evidence_complete = bool(child_keys) and all(
            child_has_complete_qsc(child_key)
            for child_key in child_keys
        )
        if ownership_complete and evidence_complete and not graph_conflict:
            return "available", "A"
        return "partial", "D"
    if not component_count:
        return fetch_outcome or "missing", "D"
    # Legacy affiliate rows and transient amounts cannot prove QSC.
    return "partial", "D"


def rebuild_company_year_quality(
    year_from: int,
    year_to: int,
    market: str | None = None,
) -> dict[str, Any]:
    """Upsert a scoped ledger from existing derived evidence only."""
    require_runtime_write("rebuild company-year quality")
    if year_from > year_to:
        raise ValueError("year_from must be less than or equal to year_to")

    with get_session() as session:
        query = session.query(
            Company.corp_code,
            Company.market,
        ).order_by(Company.corp_code)
        if market is not None:
            query = query.filter(Company.market == market)
        companies = [
            (str(corp_code), company_market)
            for corp_code, company_market in query.all()
        ]

    rows_written = 0
    for corp_code, company_market in companies:
        statuses = _financial_statuses(
            corp_code,
            year_from - (ANNUAL_CORE_YEARS_FOR_A - 1),
            year_to,
        )
        for year in range(year_from, year_to + 1):
            investor_grade, blockers = _investor_grade(
                corp_code,
                year,
                statuses,
            )
            auditor_status = _auditor_status(corp_code, year)
            audit_fee_status = _audit_fee_status(corp_code, year)
            policy_status = _policy_status(corp_code, year)
            kam_status, audit_procedure_status = _kam_and_procedure_status(
                corp_code,
                year,
            )
            auditor_grade = _auditor_grade(
                auditor_status=auditor_status,
                audit_fee_status=audit_fee_status,
                policy_status=policy_status,
                kam_status=kam_status,
            )
            group_audit_status, group_audit_grade = (
                _group_audit_status_and_grade(corp_code, year)
            )
            blockers.extend(
                f"{feature}_error"
                for feature, status in (
                    ("auditor", auditor_status),
                    ("audit_fee", audit_fee_status),
                    ("policy", policy_status),
                    ("kam", kam_status),
                    ("audit_procedure", audit_procedure_status),
                )
                if status == "error"
            )
            with get_session() as session:
                row = session.get(
                    CompanyYearQuality,
                    (corp_code, year),
                )
                if row is None:
                    row = CompanyYearQuality(
                        corp_code=corp_code,
                        bsns_year=year,
                    )
                    session.add(row)
                row.market = company_market
                row.financial_core_status = statuses[year]
                row.auditor_status = auditor_status
                row.audit_fee_status = audit_fee_status
                row.policy_status = policy_status
                row.kam_status = kam_status
                row.audit_procedure_status = audit_procedure_status
                row.group_audit_status = group_audit_status
                row.investor_grade = investor_grade
                row.auditor_grade = auditor_grade
                row.group_audit_grade = group_audit_grade
                row.blockers_json = json.dumps(
                    sorted(set(blockers)),
                    ensure_ascii=False,
                    sort_keys=True,
                )
                row.quality_version = QUALITY_VERSION
                row.updated_at = datetime.now(timezone.utc)
            rows_written += 1

    return {
        "year_from": year_from,
        "year_to": year_to,
        "market": market,
        "companies_evaluated": len(companies),
        "rows_written": rows_written,
        "quality_version": QUALITY_VERSION,
    }


def company_year_quality(corp_code: str, year: int) -> dict[str, Any]:
    """Return one persisted ledger row through a stable dict contract."""
    with get_session() as session:
        row = session.get(CompanyYearQuality, (corp_code, year))
        if row is None:
            raise LookupError(
                f"company-year quality not found: corp_code={corp_code}, year={year}"
            )
        return {
            "corp_code": row.corp_code,
            "bsns_year": int(row.bsns_year),
            "market": row.market,
            "statuses": {
                "financial_core": row.financial_core_status,
                "auditor": row.auditor_status,
                "audit_fee": row.audit_fee_status,
                "policy": row.policy_status,
                "kam": row.kam_status,
                "audit_procedure": row.audit_procedure_status,
                "group_audit": row.group_audit_status,
            },
            "feature_grades": {
                "investor_core": row.investor_grade,
                "audit_fee_peer": _audit_fee_peer_grade(corp_code, year),
                "auditor_full": row.auditor_grade,
                "group_audit": row.group_audit_grade,
            },
            "blockers": json.loads(row.blockers_json),
            "quality_version": row.quality_version,
            "updated_at": row.updated_at,
        }
