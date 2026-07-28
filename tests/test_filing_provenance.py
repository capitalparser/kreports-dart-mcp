from datetime import date

from kreports.analysis.evidence import evidence_reference_fields
from kreports.db.models import Company, Disclosure, FinancialFactCompact


def _add_compact_fact(session, corp_code: str, year: int, fs_div: str = "CFS") -> None:
    session.add(FinancialFactCompact(
        corp_code=corp_code,
        bsns_year=year,
        fs_div=fs_div,
        metric_key="revenue",
        metric_name="매출액",
        amount=100,
        source_account_id="ifrs-full_Revenue",
        source_account_nm="매출액",
    ))


def test_annual_filing_source_uses_latest_same_company_same_year_parent_receipt(temp_engine):
    """A correction must win without borrowing another company's receipt."""
    from kreports.analysis.filing_provenance import annual_filing_source

    from kreports.db.engine import get_session

    with get_session() as session:
        session.add_all([
            Company(corp_code="00126380", corp_name="삼성전자"),
            Company(corp_code="00999999", corp_name="다른회사"),
            Disclosure(
                rcept_no="20260301002820",
                corp_code="00126380",
                corp_name="삼성전자",
                disc_date=date(2026, 3, 1),
                disc_type="A",
                report_nm="사업보고서 (2025.12)",
                flr_nm="삼성전자",
            ),
            Disclosure(
                rcept_no="20260310002820_001_xml",
                corp_code="00126380",
                corp_name="삼성전자",
                disc_date=date(2026, 3, 10),
                disc_type="A",
                report_nm="사업보고서 (2025.12) [정정]",
                flr_nm="삼성전자",
            ),
            Disclosure(
                rcept_no="20260311002820",
                corp_code="00999999",
                corp_name="다른회사",
                disc_date=date(2026, 3, 11),
                disc_type="A",
                report_nm="사업보고서 (2025.12)",
                flr_nm="다른회사",
            ),
        ])
        _add_compact_fact(session, "00126380", 2025)

    source = annual_filing_source(
        "00126380",
        2025,
        source_table="financial_facts_compact",
        fs_div="CFS",
    )

    assert source == {
        "corp_code": "00126380",
        "corp_name": "삼성전자",
        "report_nm": "사업보고서 (2025.12) [정정]",
        "bsns_year": 2025,
        "rcept_no": "20260310002820",
        "section_title": "재무제표",
        "source_table": "financial_facts_compact",
        "fs_div": "CFS",
    }
    assert evidence_reference_fields(source) == {
        "source_label": "삼성전자 사업보고서 (2025.12) [정정]",
        "source_url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260310002820",
        "rcept_no": "20260310002820",
        "section_title": "재무제표",
    }


def test_annual_filing_source_returns_none_when_filing_identity_or_period_is_not_proven(temp_engine):
    """A source table row alone cannot establish a DART annual filing citation."""
    from kreports.analysis.filing_provenance import annual_filing_source

    from kreports.db.engine import get_session

    with get_session() as session:
        session.add_all([
            Company(corp_code="00126380", corp_name="삼성전자"),
            Company(corp_code="00999999", corp_name="다른회사"),
            Disclosure(
                rcept_no="20250311002820",
                corp_code="00126380",
                corp_name="삼성전자",
                disc_date=date(2025, 3, 11),
                disc_type="A",
                report_nm="사업보고서 (2024.12)",
                flr_nm="삼성전자",
            ),
            Disclosure(
                rcept_no="invalid-receipt",
                corp_code="00126380",
                corp_name="삼성전자",
                disc_date=date(2026, 3, 12),
                disc_type="A",
                report_nm="사업보고서 (2025.12)",
                flr_nm="삼성전자",
            ),
            Disclosure(
                rcept_no="20260311002820",
                corp_code="00999999",
                corp_name="다른회사",
                disc_date=date(2026, 3, 11),
                disc_type="A",
                report_nm="사업보고서 (2025.12)",
                flr_nm="다른회사",
            ),
        ])
        _add_compact_fact(session, "00126380", 2025)

    source = annual_filing_source(
        "00126380",
        2025,
        source_table="financial_facts_compact",
        fs_div="CFS",
    )

    assert source is None
    assert evidence_reference_fields(source or {}) is None


def test_annual_filing_source_skips_invalid_newer_receipts_for_latest_valid_receipt(temp_engine):
    """An invalid newer receipt cannot suppress an older valid annual filing."""
    from kreports.analysis.filing_provenance import annual_filing_source

    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(corp_code="00126380", corp_name="삼성전자"))
        session.add_all([
            Disclosure(
                rcept_no="20260301002820",
                corp_code="00126380",
                corp_name="삼성전자",
                disc_date=date(2026, 3, 1),
                disc_type="A",
                report_nm="사업보고서 (2025.12)",
                flr_nm="삼성전자",
            ),
            Disclosure(
                rcept_no="invalid-receipt",
                corp_code="00126380",
                corp_name="삼성전자",
                disc_date=date(2026, 3, 10),
                disc_type="A",
                report_nm="사업보고서 (2025.12) [정정]",
                flr_nm="삼성전자",
            ),
        ])
        _add_compact_fact(session, "00126380", 2025)

    source = annual_filing_source(
        "00126380",
        2025,
        source_table="financial_facts_compact",
        fs_div="CFS",
    )

    assert source is not None
    assert source["rcept_no"] == "20260301002820"


def test_annual_filing_sources_require_matching_fact_identity_and_basis(temp_engine):
    """Batch provenance cannot cite a filing without the requested fact basis."""
    from kreports.analysis.filing_provenance import annual_filing_sources
    from kreports.db.engine import get_session
    from kreports.db.models import Financial

    with get_session() as session:
        session.add(Company(corp_code="00126380", corp_name="삼성전자"))
        session.add(Financial(
            corp_code="00126380",
            year=2025,
            quarter=4,
            fs_div="OFS",
            total_assets=100,
            revenue=50,
        ))
        session.add(Disclosure(
            rcept_no="20260301002820",
            corp_code="00126380",
            corp_name="삼성전자",
            disc_date=date(2026, 3, 1),
            disc_type="A",
            report_nm="사업보고서 (2025.12)",
            flr_nm="삼성전자",
        ))

    assert annual_filing_sources(
        "00126380",
        [2025],
        source_table="financials",
        fs_div="CFS",
    ) == {}


def test_annual_filing_sources_rank_each_year_without_duplicate_fact_starvation(temp_engine):
    """High-volume corrections for one year cannot consume older-year provenance."""
    from kreports.analysis.filing_provenance import annual_filing_sources
    from kreports.db.engine import get_session

    years = (2025, 2024, 2023)
    with get_session() as session:
        session.add(Company(corp_code="00126380", corp_name="삼성전자"))
        for year in years:
            for index in range(3):
                session.add(FinancialFactCompact(
                    corp_code="00126380",
                    bsns_year=year,
                    fs_div="CFS",
                    metric_key=f"metric_{index}",
                    metric_name=f"지표 {index}",
                    amount=100 + index,
                    source_account_id=f"account_{index}",
                    source_account_nm=f"계정 {index}",
                ))
            session.add(Disclosure(
                rcept_no=f"{year + 1}0301000001",
                corp_code="00126380",
                corp_name="삼성전자",
                disc_date=date(year + 1, 3, 1),
                disc_type="A",
                report_nm=f"사업보고서 ({year}.12)",
                flr_nm="삼성전자",
            ))
        for index in range(1, 65):
            session.add(Disclosure(
                rcept_no=f"2026{index:010d}",
                corp_code="00126380",
                corp_name="삼성전자",
                disc_date=date(2026, 3, 20),
                disc_type="A",
                report_nm="사업보고서 (2025.12) [정정]",
                flr_nm="삼성전자",
            ))

    sources = annual_filing_sources(
        "00126380",
        years,
        source_table="financial_facts_compact",
        fs_div="CFS",
    )

    assert list(sources) == [2025, 2024, 2023]
    assert sources[2025]["rcept_no"] == "20260000000064"
    assert sources[2024]["rcept_no"] == "20250301000001"
    assert sources[2023]["rcept_no"] == "20240301000001"
