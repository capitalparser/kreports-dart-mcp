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
