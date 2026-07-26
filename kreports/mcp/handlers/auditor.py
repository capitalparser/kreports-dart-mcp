"""Auditor evidence, peer-comparison, and engagement-planning handlers."""
from __future__ import annotations

from kreports.analysis.audit_reporting import (
    get_accounting_policy,
    get_accounting_policy_changes,
    get_audit_history,
    get_audit_report_sections,
    get_kam_lifecycle,
    search_audit_procedures,
    search_audit_report_matters,
)
from kreports.analysis.group_audit import get_subsidiary_auditors
from kreports.analysis.peer_benchmarks import (
    build_audit_acceptance_pack,
    compare_peer_accounting_policies,
    compare_peer_audit_fees,
    compare_peer_audit_procedures,
    compare_peer_audit_report_matters,
    compare_peer_kam_topics,
    compare_peer_risk_profile,
    estimate_audit_hours_proxy,
)
from kreports.mcp.dispatch import resolve_company
from kreports.mcp.input_models import (
    BuildAuditAcceptancePackInput,
    ComparePeerAccountingPoliciesInput,
    ComparePeerAuditFeesInput,
    ComparePeerAuditProceduresInput,
    ComparePeerAuditReportMattersInput,
    ComparePeerKamTopicsInput,
    ComparePeerRiskProfileInput,
    EstimateAuditHoursProxyInput,
    GetAccountingPolicyChangesInput,
    GetAccountingPolicyInput,
    GetAuditHistoryInput,
    GetAuditReportSectionsInput,
    GetKamLifecycleInput,
    GetSubsidiaryAuditorsInput,
    SearchAuditProceduresInput,
    SearchAuditReportMattersInput,
)


def handle_get_accounting_policy(args: GetAccountingPolicyInput) -> dict:
    corp_code = resolve_company(args.company)
    result = get_accounting_policy(corp_code, args.bsns_year, fs_div=args.fs_div)
    if result is not None:
        return result
    return {
        "corp_code": corp_code,
        "bsns_year": args.bsns_year,
        "fs_div": args.fs_div,
        "items": {},
        "item_count": 0,
        "note": "해당 연도 사업보고서가 수집되지 않았거나 주석이 파싱되지 않음.",
    }


def handle_get_audit_history(args: GetAuditHistoryInput) -> dict:
    return get_audit_history(resolve_company(args.company))


def handle_get_subsidiary_auditors(args: GetSubsidiaryAuditorsInput) -> dict:
    return get_subsidiary_auditors(
        resolve_company(args.company),
        limit=args.limit,
        only_with_auditor=args.only_with_auditor,
        slim=args.slim,
    )


def handle_compare_peer_audit_fees(args: ComparePeerAuditFeesInput) -> dict:
    return compare_peer_audit_fees(
        company=resolve_company(args.company),
        year=args.year,
        peer_limit=args.peer_limit,
        fs_strategy=args.fs_strategy,
        size_bucket_decade=args.size_bucket_decade,
    )


def handle_compare_peer_risk_profile(args: ComparePeerRiskProfileInput) -> dict:
    return compare_peer_risk_profile(
        company=resolve_company(args.company),
        year=args.year,
        peer_limit=args.peer_limit,
        fs_strategy=args.fs_strategy,
    )


def handle_compare_peer_accounting_policies(
    args: ComparePeerAccountingPoliciesInput,
) -> dict:
    return compare_peer_accounting_policies(
        company=resolve_company(args.company),
        year=args.year,
        peer_limit=args.peer_limit,
        fs_div=args.fs_div,
        fs_strategy=args.fs_strategy,
    )


def handle_compare_peer_kam_topics(args: ComparePeerKamTopicsInput) -> dict:
    return compare_peer_kam_topics(
        company=resolve_company(args.company),
        year=args.year,
        peer_limit=args.peer_limit,
        fs_strategy=args.fs_strategy,
    )


def handle_compare_peer_audit_report_matters(
    args: ComparePeerAuditReportMattersInput,
) -> dict:
    return compare_peer_audit_report_matters(
        company=resolve_company(args.company),
        year=args.year,
        peer_limit=args.peer_limit,
        fs_strategy=args.fs_strategy,
    )


def handle_search_audit_report_matters(args: SearchAuditReportMattersInput) -> dict:
    return search_audit_report_matters(
        company=args.company,
        year=args.year,
        market=args.market,
        induty_prefix=args.induty_prefix,
        section_keys=args.section_keys,
        limit=args.limit,
        include_excerpt=args.include_excerpt,
    )


def handle_search_audit_procedures(args: SearchAuditProceduresInput) -> dict:
    return search_audit_procedures(
        company=args.company,
        year=args.year,
        market=args.market,
        induty_prefix=args.induty_prefix,
        kam_topic=args.kam_topic,
        procedure_type=args.procedure_type,
        keyword=args.keyword,
        limit=args.limit,
        include_excerpt=args.include_excerpt,
    )


def handle_compare_peer_audit_procedures(
    args: ComparePeerAuditProceduresInput,
) -> dict:
    return compare_peer_audit_procedures(
        company=resolve_company(args.company),
        year=args.year,
        peer_limit=args.peer_limit,
        fs_strategy=args.fs_strategy,
    )


def handle_get_kam_lifecycle(args: GetKamLifecycleInput) -> dict:
    return get_kam_lifecycle(
        company=resolve_company(args.company),
        start_year=args.start_year,
        end_year=args.end_year,
    )


def handle_get_accounting_policy_changes(
    args: GetAccountingPolicyChangesInput,
) -> dict:
    return get_accounting_policy_changes(
        company=resolve_company(args.company),
        start_year=args.start_year,
        end_year=args.end_year,
        fs_div=args.fs_div,
    )


def handle_get_audit_report_sections(args: GetAuditReportSectionsInput) -> dict:
    return get_audit_report_sections(
        company=resolve_company(args.company),
        year=args.year,
        section_key=args.section_key,
        source_type=args.source_type,
        limit=args.limit,
    )


def handle_estimate_audit_hours_proxy(args: EstimateAuditHoursProxyInput) -> dict:
    return estimate_audit_hours_proxy(
        company=resolve_company(args.company),
        year=args.year,
        peer_limit=args.peer_limit,
        fs_strategy=args.fs_strategy,
    )


def handle_build_audit_acceptance_pack(args: BuildAuditAcceptancePackInput) -> dict:
    return build_audit_acceptance_pack(
        company=resolve_company(args.company),
        year=args.year,
        peer_limit=args.peer_limit,
        fs_strategy=args.fs_strategy,
    )
