"""Deterministic non-empty seed and bounded Task 8 facade parity cases."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any


VOLATILE_FIELDS = frozenset({"fetched_at", "generated_at", "updated_at"})


def normalize_analysis_result(value: Any) -> Any:
    """Normalize only explicitly enumerated volatile persistence timestamps."""
    if isinstance(value, dict):
        return {
            key: normalize_analysis_result(item)
            for key, item in sorted(value.items())
            if key not in VOLATILE_FIELDS
        }
    if isinstance(value, list):
        return [normalize_analysis_result(item) for item in value]
    if isinstance(value, tuple):
        return [normalize_analysis_result(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def seed_analysis_database() -> None:
    from kreports.db.engine import get_session
    from kreports.db.models import (
        AccountingPolicyItem,
        AuditFee,
        AuditProcedureItem,
        Auditor,
        BusinessAffiliateAuditor,
        Company,
        Disclosure,
        Financial,
        ReportSection,
    )

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
            corp_name="패리티피어A",
            market="KOSPI",
            induty_code="26410",
        ),
        Company(
            corp_code="00000003",
            stock_code="000003",
            corp_name="패리티피어B",
            market="KOSPI",
            induty_code="26420",
        ),
        Company(
            corp_code="00000004",
            stock_code="000004",
            corp_name="패리티OFS",
            market="KOSPI",
            induty_code="26430",
        ),
    ]
    financial_specs = [
        ("00000001", 2023, "CFS", 100, 9, 7, 180, 70, 110, 8),
        ("00000001", 2024, "CFS", 120, 12, 9, 200, 80, 120, 11),
        ("00000001", 2024, "OFS", 100, 10, 7, 160, 60, 100, 8),
        ("00000002", 2024, "CFS", 90, 8, 6, 150, 50, 100, 7),
        ("00000003", 2024, "CFS", 150, 18, 12, 240, 100, 140, 15),
        ("00000004", 2024, "OFS", 70, 4, 3, 100, 35, 65, 4),
    ]
    financials = [
        Financial(
            corp_code=corp_code,
            year=year,
            quarter=4,
            fs_div=fs_div,
            revenue=revenue * 1_000_000_000,
            operating_profit=operating_profit * 1_000_000_000,
            net_income=net_income * 1_000_000_000,
            total_assets=assets * 1_000_000_000,
            total_debt=debt * 1_000_000_000,
            total_equity=equity * 1_000_000_000,
            operating_cf=operating_cf * 1_000_000_000,
            account_map_confidence=1.0,
            accrual_ratio=round((net_income - operating_cf) / net_income, 4),
            revenue_yoy=0.2 if year == 2024 else None,
            amendment_count_annual=1 if corp_code == "00000002" else 0,
            beneish_m_score=-2.1 if corp_code != "00000003" else -1.6,
            beneish_flag=corp_code == "00000003",
        )
        for (
            corp_code,
            year,
            fs_div,
            revenue,
            operating_profit,
            net_income,
            assets,
            debt,
            equity,
            operating_cf,
        ) in financial_specs
    ]
    with get_session() as session:
        session.add_all(companies + financials)
        for index, corp_code in enumerate(("00000001", "00000002", "00000003"), 1):
            session.add(
                Disclosure(
                    rcept_no=f"2025033100000{index}",
                    corp_code=corp_code,
                    corp_name=companies[index - 1].corp_name,
                    disc_date=date(2025, 3, 31),
                    disc_type="F",
                    report_nm="사업보고서 (2024.12)",
                )
            )
            session.add(
                Disclosure(
                    rcept_no=f"2025040100000{index}",
                    corp_code=corp_code,
                    corp_name=companies[index - 1].corp_name,
                    disc_date=date(2025, 4, 1),
                    disc_type="F",
                    report_nm="감사보고서 (2024.12)",
                )
            )
            session.add(
                Auditor(
                    corp_code=corp_code,
                    bsns_year=2024,
                    fs_div="CFS",
                    auditor_nm=f"패리티회계법인{index}",
                    audit_opinion="적정" if index < 3 else "한정",
                    rcept_no=f"2025040100000{index}",
                    consecutive_years=index,
                )
            )
            session.add(
                AuditFee(
                    corp_code=corp_code,
                    bsns_year=2024,
                    auditor_nm=f"패리티회계법인{index}",
                    audit_fee_m=100 + index * 20,
                    audit_hours=1_000 + index * 100,
                    non_audit_fee_m=10 * index,
                    non_audit_hours=50 * index,
                    nas_ratio=round((10 * index) / (100 + index * 20), 4),
                    independence_risk_flag=False,
                )
            )
            session.add(
                AccountingPolicyItem(
                    corp_code=corp_code,
                    bsns_year=2024,
                    fs_div="CFS",
                    rcept_no=f"2025033100000{index}",
                    item_key="revenue_recognition",
                    heading="수익인식",
                    body=f"고객에게 통제가 이전될 때 수익을 인식합니다. 정책유형 {index}.",
                    body_hash=f"policy-{index}",
                    body_length=35,
                )
            )
            session.add(
                ReportSection(
                    rcept_no=f"2025040100000{index}",
                    corp_code=corp_code,
                    bsns_year=2024,
                    source_type="audit_report",
                    section_key="kam",
                    section_title="핵심감사사항",
                    body_text=(
                        f"수익인식 위험 {index}에 대해 계약 문서검사와 "
                        "기간귀속 테스트를 수행했습니다."
                    ),
                    body_hash=f"kam-{index}",
                    body_length=45,
                    ordinal=0,
                )
            )
            session.add(
                ReportSection(
                    rcept_no=f"2025040100000{index}",
                    corp_code=corp_code,
                    bsns_year=2024,
                    source_type="audit_report",
                    section_key="other_matter",
                    section_title="기타사항",
                    body_text=f"전기 재무제표 감사인과 비교 정보에 관한 기타사항 {index}.",
                    body_hash=f"matter-{index}",
                    body_length=32,
                    ordinal=0,
                )
            )
            session.add(
                AuditProcedureItem(
                    rcept_no=f"2025040100000{index}",
                    corp_code=corp_code,
                    bsns_year=2024,
                    source_type="audit_report",
                    kam_topic="revenue",
                    procedure_type="substantive_test",
                    procedure_text=(
                        f"매출 계약 표본 {index}에 대해 문서검사와 "
                        "기간귀속 테스트를 수행했습니다."
                    ),
                    procedure_hash=f"procedure-{index}",
                    procedure_length=40,
                    section_ordinal=0,
                    procedure_ordinal=0,
                )
            )
        session.add(
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
            )
        )
        session.add(
            BusinessAffiliateAuditor(
                parent_corp_code="00000001",
                parent_rcept_no="20250331000001",
                bsns_year=2024,
                name="패리티피어A",
                relation="subsidiary",
                ownership_pct=80.0,
                listed_yn="Y",
                business="반도체 부품",
                assets="150000",
                corp_code="00000002",
                stock_code="000002",
                market="KOSPI",
                auditor_nm="패리티회계법인2",
                audit_opinion="적정",
                auditor_year=2024,
                ordinal=0,
            )
        )


def collect_analysis_results(api_module: Any) -> dict[str, Any]:
    cases = {
        "search_company": lambda: api_module.search_company("패리티", limit=10),
        "resolve_company": lambda: api_module.resolve_corp_code("000001"),
        "resolve_missing": lambda: api_module.resolve_corp_code("99999999"),
        "financial_cfs": lambda: api_module.get_financial_snapshot(
            "000001", fs_div="CFS", years=2
        ),
        "financial_ofs": lambda: api_module.get_financial_snapshot(
            "000001", fs_div="OFS", years=2
        ),
        "financial_cfs_fallback": lambda: api_module.get_financial_snapshot(
            "000004", fs_div="CFS", years=2
        ),
        "financial_missing": lambda: api_module.get_financial_snapshot(
            "999999", fs_div="CFS", years=2
        ),
        "investor_signals": lambda: api_module.get_investor_signals(
            "000001", years=2, window_days=3650, event_limit=5
        ),
        "audit_history": lambda: api_module.get_audit_history("000001"),
        "accounting_policy": lambda: api_module.get_accounting_policy(
            "000001", 2024, fs_div="CFS"
        ),
        "audit_sections": lambda: api_module.get_audit_report_sections(
            "000001", year=2024, limit=10
        ),
        "audit_matters": lambda: api_module.search_audit_report_matters(
            company="000001", year=2024, limit=10
        ),
        "audit_procedures": lambda: api_module.search_audit_procedures(
            company="000001", year=2024, limit=10
        ),
        "peer_selection": lambda: api_module.select_peer_group(
            "000001", year=2024, peer_limit=3
        ),
        "peer_fees": lambda: api_module.compare_peer_audit_fees(
            "000001", year=2024, peer_limit=3
        ),
        "peer_risk": lambda: api_module.compare_peer_risk_profile(
            "000001", year=2024, peer_limit=3
        ),
        "peer_policy": lambda: api_module.compare_peer_accounting_policies(
            "000001", year=2024, peer_limit=3, fs_div="CFS"
        ),
        "peer_kam": lambda: api_module.compare_peer_kam_topics(
            "000001", year=2024, peer_limit=3
        ),
        "peer_matters": lambda: api_module.compare_peer_audit_report_matters(
            "000001", year=2024, peer_limit=3
        ),
        "peer_procedures": lambda: api_module.compare_peer_audit_procedures(
            "000001", year=2024, peer_limit=3
        ),
        "business_overview": lambda: api_module.get_business_overview(
            "000001", bsns_year=2024
        ),
        "subsidiary_auditors": lambda: api_module.get_subsidiary_auditors(
            "000001", limit=10
        ),
    }
    return {
        name: normalize_analysis_result(case())
        for name, case in sorted(cases.items())
    }
