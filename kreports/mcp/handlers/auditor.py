"""Auditor evidence, peer-comparison, and engagement-planning handlers."""
from __future__ import annotations

import re

from kreports.analysis.audit_reporting import (
    get_accounting_policy,
    get_accounting_policy_changes,
    get_audit_history,
    get_audit_report_sections,
    get_kam_lifecycle,
    search_audit_procedures,
    search_audit_report_matters,
)
from kreports.analysis.auditor_decisions import (
    build_audit_acceptance_pack,
    compare_peer_audit_report_matters,
    compare_peer_kam_topics,
    compare_peer_risk_profile,
)
from kreports.analysis.audit_effort_inputs import prepare_standard_audit_hours_inputs
from kreports.analysis.group_audit import get_subsidiary_auditors
from kreports.analysis.peer_benchmarks import (
    compare_peer_accounting_policies,
    compare_peer_audit_fees,
    compare_peer_audit_procedures,
    estimate_audit_hours_proxy,
)
from kreports.mcp.auditor_public import public_auditor_result
from kreports.mcp.contracts import SectionStatusV1
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
    return public_auditor_result(compare_peer_kam_topics(
        company=resolve_company(args.company),
        year=args.year,
        peer_limit=args.peer_limit,
        fs_strategy=args.fs_strategy,
    ))


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
    result = get_accounting_policy_changes(
        company=resolve_company(args.company),
        start_year=args.start_year,
        end_year=args.end_year,
        fs_div=args.fs_div,
    )
    return _enrich_policy_change_evidence(result)


_DART_RECEIPT_NO = re.compile(r"^[0-9]{14}$", re.ASCII)


def _enrich_policy_change_evidence(result: dict) -> dict:
    """Expose each public text-change candidate with its filing receipt."""
    enriched = dict(result)
    facts: list[dict] = []
    changed_items = enriched.get("changed_items")
    if not isinstance(changed_items, list):
        changed_items = []

    for item in changed_items[:20]:
        if not isinstance(item, dict):
            continue
        receipt = str(item.get("rcept_no") or "").strip()
        if not _DART_RECEIPT_NO.fullmatch(receipt):
            continue
        year = item.get("year")
        fs_div = str(item.get("fs_div") or "재무제표")
        note_no = str(item.get("note_no") or "").strip()
        note_title = str(item.get("note_title") or "회계정책 주석").strip()
        facts.append({
            "statement": (
                f"{year}년 {fs_div} 주석 {note_no or '-'} "
                f"'{note_title}'에서 텍스트 변경 후보가 확인되었습니다."
            ),
            "source": {
                "source_label": "DART 사업보고서",
                "source_url": (
                    "https://dart.fss.or.kr/dsaf001/main.do?"
                    f"rcpNo={receipt}"
                ),
                "rcept_no": receipt,
                "section_title": f"주석 {note_no} {note_title}".strip(),
            },
            "excerpt": str(item.get("body_excerpt") or "")[:600] or None,
        })

    if facts:
        enriched["confirmed_facts"] = facts
        return enriched

    if changed_items:
        raw_quality = (
            enriched.get("data_quality")
            if isinstance(enriched.get("data_quality"), dict)
            else {}
        )
        limitations = [
            str(item)
            for item in raw_quality.get("limitations") or []
            if str(item).strip()
        ]
        limitations.append(
            "회계정책 변경 후보의 DART 접수번호를 확인하지 못해 원문 근거를 "
            "직접 연결할 수 없습니다."
        )
        enriched["data_quality"] = {
            **raw_quality,
            "status": "limited",
            "limitations": list(dict.fromkeys(limitations)),
        }
    return enriched


def handle_get_audit_report_sections(args: GetAuditReportSectionsInput) -> dict:
    return public_auditor_result(get_audit_report_sections(
        company=resolve_company(args.company),
        year=args.year,
        section_key=args.section_key,
        source_type=args.source_type,
        limit=args.limit,
    ))


def handle_estimate_audit_hours_proxy(args: EstimateAuditHoursProxyInput) -> dict:
    return estimate_audit_hours_proxy(
        company=resolve_company(args.company),
        year=args.year,
        peer_limit=args.peer_limit,
        fs_strategy=args.fs_strategy,
    )


def handle_build_audit_acceptance_pack(args: BuildAuditAcceptancePackInput) -> dict:
    company = resolve_company(args.company)
    prepared = prepare_standard_audit_hours_inputs(
        company,
        year=args.year,
        fs_strategy=args.fs_strategy,
    )
    rows = prepared.get("rows") if isinstance(prepared, dict) else []
    quality = prepared.get("data_quality") if isinstance(prepared, dict) else {}
    status = quality.get("status") if isinstance(quality, dict) else "missing"
    if status not in {"usable", "limited", "missing", "error"}:
        status = "missing"
    effort_section = SectionStatusV1(
        status=status,
        required=True,
        applicability="applicable",
        coverage={"requested_years": 3, "row_count": len(rows) if isinstance(rows, list) else 0},
    )
    return build_audit_acceptance_pack(
        company=company,
        year=args.year,
        peer_limit=args.peer_limit,
        fs_strategy=args.fs_strategy,
        audit_effort_section=effort_section,
        audit_effort_rows=rows if isinstance(rows, list) else [],
    )
