from datetime import date

import pytest
from sqlalchemy import event

from kreports.db.models import Company, Disclosure


def test_compact_citation_anchors_are_bounded_and_keep_latest_valid_parent_receipt(
    temp_engine,
):
    """A compact writer must not issue one citation query per scope or borrow filings."""
    from kreports.analysis.filing_provenance import compact_citation_anchors
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
                rcept_no="invalid-receipt",
                corp_code="00126380",
                corp_name="삼성전자",
                disc_date=date(2026, 3, 11),
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
            Disclosure(
                rcept_no="20250311002820",
                corp_code="00126380",
                corp_name="삼성전자",
                disc_date=date(2025, 3, 11),
                disc_type="A",
                report_nm="사업보고서 (2024.12)",
                flr_nm="삼성전자",
            ),
        ])
        session.commit()

    scopes = [("00126380", 2025, "CFS"), ("00126380", 2025, "OFS")]
    scopes.extend((f"{index:08d}", 2025, "CFS") for index in range(100))
    scopes.extend([("00126380", 2025, "CFS"), ("00999998", 2025, "CFS")])
    statements: list[str] = []

    def record_statement(_conn, _cursor, statement, _parameters, _context, _many):
        if "WITH requested" in statement:
            statements.append(statement)

    event.listen(temp_engine, "after_cursor_execute", record_statement)
    try:
        anchors = compact_citation_anchors(scopes, batch_size=100)
    finally:
        event.remove(temp_engine, "after_cursor_execute", record_statement)

    assert anchors[("00126380", 2025, "CFS")] == {
        "corp_code": "00126380",
        "bsns_year": 2025,
        "fs_div": "CFS",
        "rcept_no": "20260310002820",
        "report_nm": "사업보고서 (2025.12) [정정]",
        "citation_basis": "company_year_annual_filing_match",
    }
    assert anchors[("00126380", 2025, "OFS")]["rcept_no"] == "20260310002820"
    assert ("00999998", 2025, "CFS") not in anchors
    assert len(statements) == 2


def test_compact_citation_anchors_reject_nonpositive_batch_size(temp_engine):
    """A nonpositive chunk size would defeat the bounded-query contract."""
    from kreports.analysis.filing_provenance import compact_citation_anchors

    with pytest.raises(ValueError, match="batch_size"):
        compact_citation_anchors([("00126380", 2025, "CFS")], batch_size=0)


def test_compact_rebuild_persists_authoritative_and_uncitable_provenance(temp_engine):
    """A rebuilt amount without its own citation anchor must remain explicitly limited."""
    from sqlalchemy import text

    from kreports.db.engine import get_session
    from kreports.maintenance.financial_compact import rebuild_financial_facts_compact

    with get_session() as session:
        session.add(Disclosure(
            rcept_no="20250318000001",
            corp_code="00126380",
            corp_name="삼성전자",
            disc_date=date(2025, 3, 18),
            disc_type="A",
            report_nm="사업보고서 (2024.12)",
            flr_nm="삼성전자",
        ))
        session.execute(text("""
            INSERT INTO financial_facts
            (corp_code, bsns_year, reprt_code, fs_div, sj_div, account_id, account_nm,
             ord, thstrm_amount, fetched_at)
            VALUES
            ('00126380', 2024, '11011', 'CFS', 'IS', 'ifrs-full_Revenue', '매출액', 1, 100, CURRENT_TIMESTAMP),
            ('00126380', 2024, '11011', 'CFS', 'BS', 'ifrs-full_Assets', '자산총계', 2, 200, CURRENT_TIMESTAMP),
            ('00126381', 2024, '11011', 'CFS', 'IS', 'ifrs-full_Revenue', '매출액', 1, 100, CURRENT_TIMESTAMP)
        """))
        session.commit()

    rebuild_financial_facts_compact(year_from=2024, year_to=2024)

    with get_session() as session:
        rows = {
            (row["corp_code"], row["metric_key"]): dict(row)
            for row in session.execute(text("""
                SELECT corp_code, metric_key, amount, source_table, unit, period_type,
                       citation_rcept_no, citation_report_nm, citation_basis, quality_status
                FROM financial_facts_compact
                ORDER BY corp_code, metric_key
            """)).mappings()
        }

    assert rows[("00126380", "revenue")] == {
        "corp_code": "00126380",
        "metric_key": "revenue",
        "amount": 100,
        "source_table": "financial_facts",
        "unit": "KRW",
        "period_type": "duration",
        "citation_rcept_no": "20250318000001",
        "citation_report_nm": "사업보고서 (2024.12)",
        "citation_basis": "company_year_annual_filing_match",
        "quality_status": "usable",
    }
    assert rows[("00126380", "assets")]["period_type"] == "instant"
    assert rows[("00126381", "revenue")]["amount"] == 100
    assert rows[("00126381", "revenue")]["citation_rcept_no"] is None
    assert rows[("00126381", "revenue")]["citation_basis"] == "uncitable"
    assert rows[("00126381", "revenue")]["quality_status"] == "limited"


def test_compact_provenance_rejects_unsupported_source_or_period():
    """An invented source or non-financial period cannot be written as compact provenance."""
    from kreports.maintenance.financial_compact import _compact_provenance

    with pytest.raises(ValueError, match="source table"):
        _compact_provenance(metric_key="revenue", source_table="invented", citation=None)
    with pytest.raises(ValueError, match="period type"):
        _compact_provenance(metric_key="audit_fee", source_table="financials", citation=None)
