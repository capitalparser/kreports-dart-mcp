"""Behavior and import contracts for the domain-decomposed analysis facade."""
from __future__ import annotations

import inspect
import re
import subprocess
import sys
from datetime import date

import pytest

from kreports.analysis import api
from kreports.analysis import (
    audit_reporting,
    company_profile,
    financial_analysis,
    group_audit,
    peer_benchmarks,
)
from kreports.db.models import (
    AccountingPolicyItem,
    Auditor,
    BusinessAffiliateAuditor,
    Company,
    Disclosure,
    Financial,
    ReportSection,
    AuditProcedureItem,
)


DOMAIN_EXPORTS = {
    company_profile: (
        "search_company",
        "get_company",
        "resolve_corp_code",
        "get_business_overview",
        "search_dataset",
    ),
    financial_analysis: (
        "get_financial_snapshot",
        "get_investor_signals",
        "score_going_concern",
        "detect_restatement",
        "get_quality_of_earnings_pack",
        "get_dcf_input_candidates",
        "search_disclosure_events",
    ),
    audit_reporting: (
        "get_accounting_policy",
        "get_audit_history",
        "get_audit_report_sections",
        "search_audit_report_matters",
        "search_audit_procedures",
        "get_kam_lifecycle",
        "get_accounting_policy_changes",
    ),
    peer_benchmarks: (
        "get_industry_aggregates",
        "compare_to_industry",
        "compare_to_industry_multi",
        "select_peer_group",
        "compare_peer_audit_fees",
        "compare_peer_risk_profile",
        "compare_peer_accounting_policies",
        "compare_peer_kam_topics",
        "compare_peer_audit_report_matters",
        "compare_peer_audit_procedures",
        "estimate_audit_hours_proxy",
        "build_audit_acceptance_pack",
        "get_industry_audit_landscape",
    ),
    group_audit: ("get_subsidiary_auditors",),
}


@pytest.mark.parametrize(
    ("domain", "export"),
    [
        (domain, export)
        for domain, exports in DOMAIN_EXPORTS.items()
        for export in exports
    ],
)
def test_facade_reexports_exact_domain_object(domain, export):
    assert getattr(api, export) is getattr(domain, export)


def test_facade_is_small_and_contains_no_domain_sql():
    source = inspect.getsource(api)
    assert len(source.splitlines()) < 500
    assert not re.search(r"\b(SELECT|INSERT|UPDATE|DELETE)\b", source, re.IGNORECASE)
    assert "sqlalchemy" not in source


@pytest.mark.parametrize(
    "module_name",
    [
        "kreports.analysis.company_profile",
        "kreports.analysis.financial_analysis",
        "kreports.analysis.audit_reporting",
        "kreports.analysis.peer_benchmarks",
        "kreports.analysis.group_audit",
        "kreports.analysis.api",
        "kreports.mcp.catalog",
        "kreports.mcp.server",
        "kreports.mcp.tools",
    ],
)
def test_analysis_and_mcp_modules_import_in_fresh_interpreter(module_name):
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import importlib; importlib.import_module({module_name!r})",
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**dict(__import__("os").environ), "PYTHONPATH": "."},
    )
    assert completed.returncode == 0, completed.stderr


@pytest.fixture
def seeded_analysis_cases(temp_engine):
    """Non-empty CFS/OFS, evidence, peer, and group paths for facade parity."""
    from kreports.db.engine import get_session

    companies = [
        Company(
            corp_code="00000001",
            stock_code="000001",
            corp_name="패리티대상",
            market="KOSPI",
            induty_code="26400",
        ),
        Company(
            corp_code="00000002",
            stock_code="000002",
            corp_name="패리티피어",
            market="KOSPI",
            induty_code="26410",
        ),
    ]
    financials = [
        Financial(
            corp_code="00000001",
            year=2024,
            quarter=4,
            fs_div="CFS",
            revenue=120_000_000_000,
            operating_profit=12_000_000_000,
            net_income=9_000_000_000,
            total_assets=200_000_000_000,
            total_debt=80_000_000_000,
            total_equity=120_000_000_000,
            operating_cf=11_000_000_000,
            account_map_confidence=1.0,
        ),
        Financial(
            corp_code="00000001",
            year=2024,
            quarter=4,
            fs_div="OFS",
            revenue=100_000_000_000,
            operating_profit=10_000_000_000,
            net_income=7_000_000_000,
            total_assets=160_000_000_000,
            total_debt=60_000_000_000,
            total_equity=100_000_000_000,
            operating_cf=8_000_000_000,
            account_map_confidence=1.0,
        ),
        Financial(
            corp_code="00000002",
            year=2024,
            quarter=4,
            fs_div="CFS",
            revenue=90_000_000_000,
            operating_profit=8_000_000_000,
            net_income=6_000_000_000,
            total_assets=150_000_000_000,
            total_debt=50_000_000_000,
            total_equity=100_000_000_000,
            operating_cf=7_000_000_000,
            account_map_confidence=1.0,
        ),
    ]
    with get_session() as session:
        session.add_all(companies + financials)
        session.add_all(
            [
                Disclosure(
                    rcept_no="20250331000001",
                    corp_code="00000001",
                    corp_name="패리티대상",
                    disc_date=date(2025, 3, 31),
                    disc_type="F",
                    report_nm="사업보고서 (2024.12)",
                ),
                Auditor(
                    corp_code="00000001",
                    bsns_year=2024,
                    fs_div="CFS",
                    auditor_nm="패리티회계법인",
                    audit_opinion="적정",
                    rcept_no="20250331000001",
                ),
                AccountingPolicyItem(
                    corp_code="00000001",
                    bsns_year=2024,
                    fs_div="CFS",
                    rcept_no="20250331000001",
                    item_key="revenue_recognition",
                    heading="수익인식",
                    body="고객에게 통제가 이전될 때 수익을 인식합니다.",
                    body_hash="policy",
                    body_length=28,
                ),
                ReportSection(
                    rcept_no="20250331000001",
                    corp_code="00000001",
                    bsns_year=2024,
                    source_type="audit_report",
                    section_key="kam",
                    section_title="핵심감사사항",
                    body_text="수익인식 위험에 대해 계약 문서검사와 기간귀속 테스트를 수행했습니다.",
                    body_hash="kam",
                    body_length=40,
                    ordinal=0,
                ),
                ReportSection(
                    rcept_no="20250331000001",
                    corp_code="00000001",
                    bsns_year=2024,
                    source_type="audit_report",
                    section_key="other_matter",
                    section_title="기타사항",
                    body_text="전기 재무제표는 다른 감사인이 감사했습니다.",
                    body_hash="matter",
                    body_length=25,
                    ordinal=0,
                ),
                ReportSection(
                    rcept_no="20250331000001",
                    corp_code="00000001",
                    bsns_year=2024,
                    source_type="business_report",
                    section_key="business_overview",
                    section_title="사업의 개요",
                    body_text="회사는 반도체 부품을 제조하고 해외 고객에게 판매합니다.",
                    body_hash="business",
                    body_length=31,
                    ordinal=0,
                ),
                AuditProcedureItem(
                    rcept_no="20250331000001",
                    corp_code="00000001",
                    bsns_year=2024,
                    source_type="audit_report",
                    kam_topic="revenue",
                    procedure_type="substantive_test",
                    procedure_text="매출 계약 표본에 대해 문서검사와 기간귀속 테스트를 수행했습니다.",
                    procedure_hash="procedure",
                    procedure_length=35,
                    section_ordinal=0,
                    procedure_ordinal=0,
                ),
                BusinessAffiliateAuditor(
                    parent_corp_code="00000001",
                    parent_rcept_no="20250331000001",
                    bsns_year=2024,
                    name="패리티피어",
                    relation="subsidiary",
                    ownership_pct=80.0,
                    listed_yn="Y",
                    business="반도체 부품",
                    corp_code="00000002",
                    stock_code="000002",
                    market="KOSPI",
                    auditor_nm="피어회계법인",
                    audit_opinion="적정",
                    auditor_year=2024,
                    ordinal=0,
                ),
            ]
        )

    return [
        (company_profile.search_company, api.search_company, ("패리티",), {"limit": 5}, lambda v: len(v) == 2),
        (company_profile.resolve_corp_code, api.resolve_corp_code, ("000001",), {}, lambda v: v == "00000001"),
        (financial_analysis.get_financial_snapshot, api.get_financial_snapshot, ("000001",), {"years": 2}, lambda v: v["row_count"] > 0),
        (financial_analysis.get_investor_signals, api.get_investor_signals, ("000001",), {"years": 2}, lambda v: v["has_data"] and bool(v["quality_snapshot"])),
        (audit_reporting.get_audit_history, api.get_audit_history, ("000001",), {}, lambda v: v["count"] > 0),
        (audit_reporting.get_accounting_policy, api.get_accounting_policy, ("000001", 2024), {}, lambda v: v["item_count"] > 0),
        (audit_reporting.get_audit_report_sections, api.get_audit_report_sections, ("000001",), {"year": 2024}, lambda v: v["section_count"] > 0),
        (audit_reporting.search_audit_report_matters, api.search_audit_report_matters, (), {"company": "000001", "year": 2024}, lambda v: v["total_companies"] > 0),
        (audit_reporting.search_audit_procedures, api.search_audit_procedures, (), {"company": "000001", "year": 2024}, lambda v: v["total_companies"] > 0),
        (peer_benchmarks.select_peer_group, api.select_peer_group, ("000001",), {"year": 2024}, lambda v: v["peer_count"] > 0),
        (company_profile.get_business_overview, api.get_business_overview, ("000001",), {"bsns_year": 2024}, lambda v: v["section_count"] > 0),
        (group_audit.get_subsidiary_auditors, api.get_subsidiary_auditors, ("000001",), {}, lambda v: v["count"] > 0),
    ]


def test_seeded_non_empty_facade_behavior_matches_domains(seeded_analysis_cases):
    for domain_function, facade_function, args, kwargs, is_non_empty in seeded_analysis_cases:
        domain_result = domain_function(*args, **kwargs)
        facade_result = facade_function(*args, **kwargs)
        assert is_non_empty(domain_result), domain_function.__name__
        assert facade_result == domain_result
