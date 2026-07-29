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
