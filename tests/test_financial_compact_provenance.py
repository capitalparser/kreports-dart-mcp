from datetime import date

import pytest
from sqlalchemy import event, text

from kreports.db.models import Company, Disclosure


def _seed_citable_compact_years(
    years: list[int],
    *,
    metric_amounts: dict[str, int],
) -> dict[int, str]:
    from kreports.db.engine import get_session

    receipts = {
        year: f"{year + 1}0318000001"
        for year in years
    }
    with get_session() as session:
        session.add(Company(corp_code="00126380", corp_name="삼성전자"))
        for year in years:
            for metric_key, base_amount in metric_amounts.items():
                session.execute(text("""
                    INSERT INTO financial_facts_compact
                    (corp_code, bsns_year, fs_div, metric_key, metric_name,
                     amount, source_table, unit, period_type,
                     citation_rcept_no, citation_report_nm, citation_basis,
                     quality_status, fetched_at)
                    VALUES
                    (:corp_code, :bsns_year, 'CFS', :metric_key, :metric_key,
                     :amount, 'financial_facts', 'KRW', 'duration',
                     :citation_rcept_no, :citation_report_nm,
                     'company_year_annual_filing_match', 'usable',
                     CURRENT_TIMESTAMP)
                """), {
                    "corp_code": "00126380",
                    "bsns_year": year,
                    "metric_key": metric_key,
                    "amount": base_amount + (year - years[0]) * 10,
                    "citation_rcept_no": receipts[year],
                    "citation_report_nm": f"사업보고서 ({year}.12)",
                })
        session.commit()
    return receipts


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


def test_compact_snapshot_prefers_persisted_citation_over_newer_disclosure(temp_engine):
    """A newer disclosure cannot rewrite a citation that the compact rebuild persisted."""
    from sqlalchemy import text

    from kreports.analysis.financial_analysis import _financial_snapshot_from_compact
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(corp_code="00126380", corp_name="삼성전자"))
        session.execute(text("""
            INSERT INTO financial_facts_compact
            (corp_code, bsns_year, fs_div, metric_key, metric_name, amount,
             source_table, unit, period_type, citation_rcept_no,
             citation_report_nm, citation_basis, quality_status, fetched_at)
            VALUES ('00126380', 2024, 'CFS', 'revenue', '매출액', 100,
                    'financial_facts', 'KRW', 'duration', '20250318000001',
                    '사업보고서 (2024.12)', 'company_year_annual_filing_match',
                    'usable', CURRENT_TIMESTAMP)
        """))
        session.add(Disclosure(
            rcept_no="20250319000001",
            corp_code="00126380",
            corp_name="삼성전자",
            disc_date=date(2025, 3, 19),
            disc_type="A",
            report_nm="사업보고서 (2024.12) [정정]",
            flr_nm="삼성전자",
        ))
        session.commit()

    result = _financial_snapshot_from_compact("00126380", "CFS", None)

    assert result["rows"][0]["source"]["rcept_no"] == "20250318000001"
    assert result["rows"][0]["source"]["report_nm"] == "사업보고서 (2024.12)"
    assert result["rows"][0]["source"]["corp_name"] == "삼성전자"
    assert result["unit"] == "억원"
    assert result["data_quality"]["status"] == "usable"


@pytest.mark.parametrize(
    ("rows_sql", "expected_limitations"),
    [
        (
            """
            ('00126380', 2024, 'CFS', 'revenue', '매출액', 100000000,
             'financial_facts', 'KRW', 'duration', '20250318000001',
             '사업보고서 (2024.12)', 'company_year_annual_filing_match',
             'usable', CURRENT_TIMESTAMP),
            ('00126380', 2024, 'CFS', 'assets', '자산총계', 200000000,
             'financial_facts', NULL, 'instant', '20250318000001',
             '사업보고서 (2024.12)', 'company_year_annual_filing_match',
             'limited', CURRENT_TIMESTAMP)
            """,
            {"unit_unproven:assets", "quality_limited:assets"},
        ),
        (
            """
            ('00126380', 2024, 'CFS', 'revenue', '매출액', 100000000,
             'financial_facts', NULL, 'duration', '20250318000001',
             '사업보고서 (2024.12)', 'company_year_annual_filing_match',
             'limited', CURRENT_TIMESTAMP)
            """,
            {"unit_unproven:revenue", "quality_limited:revenue"},
        ),
    ],
    ids=["mixed", "all-limited"],
)
def test_compact_snapshot_does_not_confirm_converted_unit_for_unproven_values(
    temp_engine,
    rows_sql,
    expected_limitations,
):
    """One displayed unproven value must fail the snapshot unit and quality closed."""
    from sqlalchemy import text

    from kreports.analysis.financial_analysis import _financial_snapshot_from_compact
    from kreports.db.engine import get_session
    from kreports.mcp.contracts import build_answer_envelope

    with get_session() as session:
        session.add(Company(corp_code="00126380", corp_name="삼성전자"))
        session.execute(text("""
            INSERT INTO financial_facts_compact
            (corp_code, bsns_year, fs_div, metric_key, metric_name, amount,
             source_table, unit, period_type, citation_rcept_no,
             citation_report_nm, citation_basis, quality_status, fetched_at)
            VALUES
        """ + rows_sql))
        session.commit()

    result = _financial_snapshot_from_compact("00126380", "CFS", None)

    assert result["unit"] is None
    assert result["data_quality"]["status"] == "limited"
    assert expected_limitations.issubset(
        set(result["data_quality"]["limitations"])
    )
    assert (
        build_answer_envelope(
            "get_financial_snapshot",
            result,
        ).data_quality.status
        == "limited"
    )
    assert result["rows"][0]["source"] == {
        "corp_code": "00126380",
        "corp_name": "삼성전자",
        "report_nm": "사업보고서 (2024.12)",
        "bsns_year": 2024,
        "rcept_no": "20250318000001",
        "section_title": "재무제표",
        "source_table": "financial_facts_compact",
        "citation_basis": "company_year_annual_filing_match",
    }


def test_compact_snapshot_limits_provenance_to_returned_years_across_surfaces(
    temp_engine,
):
    """A hidden prior year must not downgrade the requested latest-year snapshot."""
    from sqlalchemy import text

    from kreports.analysis.financial_analysis import (
        _financial_snapshot_from_compact,
        get_financial_snapshot,
    )
    from kreports.db.engine import get_session
    from kreports.mcp.handlers.company import handle_get_financial_snapshot
    from kreports.mcp.input_models import GetFinancialSnapshotInput

    with get_session() as session:
        session.add(Company(corp_code="00126380", corp_name="삼성전자"))
        session.execute(text("""
            INSERT INTO financial_facts_compact
            (corp_code, bsns_year, fs_div, metric_key, metric_name, amount,
             source_table, unit, period_type, citation_rcept_no,
             citation_report_nm, citation_basis, quality_status, fetched_at)
            VALUES
            ('00126380', 2023, 'CFS', 'assets', '자산총계', 200000000,
             'financial_facts', NULL, 'instant', '20240318000001',
             '사업보고서 (2023.12)', 'company_year_annual_filing_match',
             'limited', CURRENT_TIMESTAMP),
            ('00126380', 2024, 'CFS', 'revenue', '매출액', 100000000,
             'financial_facts', 'KRW', 'duration', '20250318000001',
             '사업보고서 (2024.12)', 'company_year_annual_filing_match',
             'usable', CURRENT_TIMESTAMP)
        """))
        session.commit()

    raw = _financial_snapshot_from_compact("00126380", "CFS", 1)
    public = get_financial_snapshot("00126380", years=1)
    mcp = handle_get_financial_snapshot(
        GetFinancialSnapshotInput(company="00126380", years=1)
    )

    for result in (raw, public, mcp):
        assert result["row_count"] == 1
        assert result["rows"][0]["연도"] == 2024
        assert result["unit"] == "억원"
        assert result["data_quality"]["status"] == "usable"
        assert "limitations" not in result["data_quality"]
        assert result["rows"][0]["source"]["rcept_no"] == "20250318000001"

    two_years = _financial_snapshot_from_compact("00126380", "CFS", 2)

    assert [row["연도"] for row in two_years["rows"]] == [2023, 2024]
    assert two_years["unit"] is None
    assert two_years["data_quality"]["status"] == "limited"
    assert {
        "unit_unproven:assets",
        "quality_limited:assets",
    }.issubset(set(two_years["data_quality"]["limitations"]))


def test_hidden_unproven_prior_revenue_suppresses_growth_across_surfaces(
    temp_engine,
):
    """Revenue growth must fail closed when its hidden prior-year input is unproven."""
    from sqlalchemy import text

    from kreports.analysis.financial_analysis import (
        _financial_snapshot_from_compact,
        get_financial_snapshot,
    )
    from kreports.db.engine import get_session
    from kreports.mcp.handlers.company import handle_get_financial_snapshot
    from kreports.mcp.input_models import GetFinancialSnapshotInput

    with get_session() as session:
        session.add(Company(corp_code="00126380", corp_name="삼성전자"))
        session.execute(text("""
            INSERT INTO financial_facts_compact
            (corp_code, bsns_year, fs_div, metric_key, metric_name, amount,
             source_table, unit, period_type, citation_rcept_no,
             citation_report_nm, citation_basis, quality_status, fetched_at)
            VALUES
            ('00126380', 2023, 'CFS', 'revenue', '매출액', 100000000,
             'financial_facts', NULL, 'duration', NULL, NULL,
             'uncitable', 'limited', CURRENT_TIMESTAMP),
            ('00126380', 2024, 'CFS', 'revenue', '매출액', 150000000,
             'financial_facts', 'KRW', 'duration', '20250318000001',
             '사업보고서 (2024.12)', 'company_year_annual_filing_match',
             'usable', CURRENT_TIMESTAMP)
        """))
        session.commit()

    results = (
        _financial_snapshot_from_compact("00126380", "CFS", 1),
        get_financial_snapshot("00126380", years=1),
        handle_get_financial_snapshot(
            GetFinancialSnapshotInput(company="00126380", years=1)
        ),
    )

    for result in results:
        row = result["rows"][0]
        assert row["연도"] == 2024
        assert row["매출성장률"] is None
        assert result["unit"] == "억원"
        assert result["data_quality"]["status"] == "limited"
        assert (
            "derived_input_unproven:revenue_growth:2023"
            in result["data_quality"]["limitations"]
        )
        assert row["source"]["rcept_no"] == "20250318000001"


def test_hidden_proven_prior_revenue_keeps_growth_and_exact_sources_across_surfaces(
    temp_engine,
):
    """Revenue growth may use a hidden prior year only with both proven sources."""
    from sqlalchemy import text

    from kreports.analysis.financial_analysis import (
        _financial_snapshot_from_compact,
        get_financial_snapshot,
    )
    from kreports.db.engine import get_session
    from kreports.mcp.handlers.company import handle_get_financial_snapshot
    from kreports.mcp.input_models import GetFinancialSnapshotInput

    with get_session() as session:
        session.add(Company(corp_code="00126380", corp_name="삼성전자"))
        session.execute(text("""
            INSERT INTO financial_facts_compact
            (corp_code, bsns_year, fs_div, metric_key, metric_name, amount,
             source_table, unit, period_type, citation_rcept_no,
             citation_report_nm, citation_basis, quality_status, fetched_at)
            VALUES
            ('00126380', 2023, 'CFS', 'revenue', '매출액', 100000000,
             'financial_facts', 'KRW', 'duration', '20240318000001',
             '사업보고서 (2023.12)', 'company_year_annual_filing_match',
             'usable', CURRENT_TIMESTAMP),
            ('00126380', 2024, 'CFS', 'revenue', '매출액', 150000000,
             'financial_facts', 'KRW', 'duration', '20250318000001',
             '사업보고서 (2024.12)', 'company_year_annual_filing_match',
             'usable', CURRENT_TIMESTAMP)
        """))
        session.commit()

    results = (
        _financial_snapshot_from_compact("00126380", "CFS", 1),
        get_financial_snapshot("00126380", years=1),
        handle_get_financial_snapshot(
            GetFinancialSnapshotInput(company="00126380", years=1)
        ),
    )

    for result in results:
        row = result["rows"][0]
        assert row["연도"] == 2024
        assert row["매출성장률"] == 50.0
        assert result["unit"] == "억원"
        assert result["data_quality"]["status"] == "usable"
        assert "limitations" not in result["data_quality"]
        assert [
            source["rcept_no"]
            for source in row["derived_sources"]["매출성장률"]
        ] == ["20240318000001", "20250318000001"]


def test_dispatch_promotes_exact_growth_sources_to_envelope_and_investor_pack(
    temp_engine,
):
    """Dropping either growth input receipt from the public MCP result is a bug."""
    from sqlalchemy import text

    from kreports.db.engine import get_session
    from kreports.mcp.dispatch import dispatch_tool

    with get_session() as session:
        session.add(Company(corp_code="00126380", corp_name="삼성전자"))
        session.execute(text("""
            INSERT INTO financial_facts_compact
            (corp_code, bsns_year, fs_div, metric_key, metric_name, amount,
             source_table, unit, period_type, citation_rcept_no,
             citation_report_nm, citation_basis, quality_status, fetched_at)
            VALUES
            ('00126380', 2023, 'CFS', 'revenue', '매출액', 100000000,
             'financial_facts', 'KRW', 'duration', '20240318000001',
             '사업보고서 (2023.12)', 'company_year_annual_filing_match',
             'usable', CURRENT_TIMESTAMP),
            ('00126380', 2024, 'CFS', 'revenue', '매출액', 150000000,
             'financial_facts', 'KRW', 'duration', '20250318000001',
             '사업보고서 (2024.12)', 'company_year_annual_filing_match',
             'usable', CURRENT_TIMESTAMP)
        """))
        session.commit()

    result = dispatch_tool(
        "get_financial_snapshot",
        {"company": "00126380", "years": 1},
    ).model_dump(mode="json")

    expected_receipts = {"20240318000001", "20250318000001"}
    assert result["verdict"] == "usable"
    assert {
        evidence["rcept_no"] for evidence in result["evidence"]
    } == expected_receipts
    assert len(result["evidence"]) == 2

    growth_fact = next(
        fact
        for fact in result["confirmed_facts"]
        if "매출성장률" in fact["statement"]
    )
    assert "source" not in growth_fact
    assert [
        source["rcept_no"] for source in growth_fact["sources"]
    ] == ["20240318000001", "20250318000001"]

    pack = result["answer_pack"]
    assert {
        source["rcept_no"] for source in pack["sources"]
    } == expected_receipts
    assert len(pack["sources"]) == 2
    growth_row = pack["tables"][0]["rows"][0]
    assert growth_row["revenue_growth"] == 50.0
    assert growth_row["source"] == "20250318000001"
    assert growth_row["growth_sources"] == [
        "20240318000001",
        "20250318000001",
    ]
    revenue_column = next(
        column
        for column in pack["tables"][0]["columns"]
        if column["field"] == "revenue"
    )
    assert revenue_column["unit"] == "억원"


def test_dispatch_keeps_bad_prior_growth_suppressed_and_current_source_bounded(
    temp_engine,
):
    """A public pack must not recover growth evidence from an unproven prior row."""
    from sqlalchemy import text

    from kreports.db.engine import get_session
    from kreports.mcp.dispatch import dispatch_tool

    with get_session() as session:
        session.add(Company(corp_code="00126380", corp_name="삼성전자"))
        session.execute(text("""
            INSERT INTO financial_facts_compact
            (corp_code, bsns_year, fs_div, metric_key, metric_name, amount,
             source_table, unit, period_type, citation_rcept_no,
             citation_report_nm, citation_basis, quality_status, fetched_at)
            VALUES
            ('00126380', 2023, 'CFS', 'revenue', '매출액', 100000000,
             'financial_facts', NULL, 'duration', NULL, NULL,
             'uncitable', 'limited', CURRENT_TIMESTAMP),
            ('00126380', 2024, 'CFS', 'revenue', '매출액', 150000000,
             'financial_facts', 'KRW', 'duration', '20250318000001',
             '사업보고서 (2024.12)', 'company_year_annual_filing_match',
             'usable', CURRENT_TIMESTAMP)
        """))
        session.commit()

    result = dispatch_tool(
        "get_financial_snapshot",
        {"company": "00126380", "years": 1},
    ).model_dump(mode="json")

    assert result["verdict"] == "limited"
    assert (
        "derived_input_unproven:revenue_growth:2023"
        in result["data_quality"]["limitations"]
    )
    assert [evidence["rcept_no"] for evidence in result["evidence"]] == [
        "20250318000001"
    ]
    assert all(
        "매출성장률" not in fact["statement"]
        for fact in result["confirmed_facts"]
    )
    growth_row = result["answer_pack"]["tables"][0]["rows"][0]
    assert growth_row["revenue_growth"] is None
    assert not growth_row.get("growth_sources")
    assert growth_row["source"] == "20250318000001"
    assert [
        source["rcept_no"] for source in result["answer_pack"]["sources"]
    ] == ["20250318000001"]


def test_dispatch_pack_does_not_default_unproven_amount_unit_to_억원(
    temp_engine,
):
    """An explicit unknown domain unit must stay unknown in the public pack."""
    from sqlalchemy import text

    from kreports.analysis.financial_analysis import get_financial_snapshot
    from kreports.db.engine import get_session
    from kreports.mcp.dispatch import dispatch_tool

    with get_session() as session:
        session.add(Company(corp_code="00126380", corp_name="삼성전자"))
        session.execute(text("""
            INSERT INTO financial_facts_compact
            (corp_code, bsns_year, fs_div, metric_key, metric_name, amount,
             source_table, unit, period_type, citation_rcept_no,
             citation_report_nm, citation_basis, quality_status, fetched_at)
            VALUES
            ('00126380', 2024, 'CFS', 'revenue', '매출액', 100000000,
             'financial_facts', NULL, 'duration', '20250318000001',
             '사업보고서 (2024.12)', 'company_year_annual_filing_match',
             'limited', CURRENT_TIMESTAMP)
        """))
        session.commit()

    domain = get_financial_snapshot("00126380", years=1)
    result = dispatch_tool(
        "get_financial_snapshot",
        {"company": "00126380", "years": 1},
    ).model_dump(mode="json")

    assert domain["unit"] is None
    assert domain["data_quality"]["status"] == "limited"
    assert result["verdict"] == "limited"
    assert result["answer_pack"]["data_quality"]["status"] == "limited"
    table = result["answer_pack"]["tables"][0]
    assert table["rows"][0]["revenue"] == 1.0
    for field in ("revenue", "operating_profit", "net_income", "operating_cf"):
        column = next(
            column for column in table["columns"]
            if column["field"] == field
        )
        assert "unit" not in column
    assert [
        source["rcept_no"] for source in result["answer_pack"]["sources"]
    ] == ["20250318000001"]


def test_three_year_snapshot_keeps_each_persisted_receipt_in_envelope_and_pack(
    temp_engine,
):
    """Reducing a three-year snapshot to the latest receipt is a public evidence bug."""
    from kreports.mcp.dispatch import dispatch_tool

    expected = _seed_citable_compact_years(
        [2022, 2023, 2024],
        metric_amounts={"revenue": 100_000_000},
    )

    result = dispatch_tool(
        "get_financial_snapshot",
        {"company": "00126380", "years": 3},
    ).model_dump(mode="json")

    assert {
        evidence["rcept_no"] for evidence in result["evidence"]
    } == set(expected.values())
    pack = result["answer_pack"]
    assert {
        source["rcept_no"] for source in pack["sources"]
    } == set(expected.values())
    trend = next(
        table for table in pack["tables"]
        if table["id"] == "financial_trend"
    )
    assert {
        row["year"]: row["source"] for row in trend["rows"]
    } == expected


def test_five_year_dcf_actuals_keep_persisted_receipt_per_year_in_public_outputs(
    temp_engine,
):
    """DCF actuals must not collapse five annual sources to one latest filing."""
    from kreports.mcp.dispatch import dispatch_tool

    expected = _seed_citable_compact_years(
        [2020, 2021, 2022, 2023, 2024],
        metric_amounts={
            "revenue": 1_000,
            "operating_profit": 100,
            "profit_loss": 80,
            "operating_cash_flow": 120,
            "tax_expense": 20,
            "purchase_ppe": 30,
            "purchase_intangible_assets": 5,
        },
    )

    result = dispatch_tool(
        "get_dcf_input_candidates",
        {
            "company": "00126380",
            "start_year": 2020,
            "end_year": 2024,
            "fs_div": "CFS",
        },
    ).model_dump(mode="json")

    assert {
        evidence["rcept_no"] for evidence in result["evidence"]
    } == set(expected.values())
    pack = result["answer_pack"]
    assert {
        source["rcept_no"] for source in pack["sources"]
    } == set(expected.values())
    actuals = next(
        table for table in pack["tables"]
        if table["id"] == "historical_actuals"
    )
    assert {
        row["year"]: row["source"] for row in actuals["rows"]
    } == expected
    assert any(
        column["field"] == "source"
        for column in actuals["columns"]
    )


def test_legacy_financial_rows_use_valid_annual_sources_for_growth(temp_engine):
    """Catches a malformed newer disclosure receipt contaminating legacy growth."""
    from kreports.analysis.financial_analysis import _attach_annual_sources
    from kreports.db.engine import get_session
    from kreports.db.models import Financial

    with get_session() as session:
        session.add(Company(corp_code="00126380", corp_name="삼성전자"))
        session.add_all([
            Financial(
                corp_code="00126380",
                year=2023,
                quarter=4,
                fs_div="CFS",
                revenue=100,
            ),
            Financial(
                corp_code="00126380",
                year=2024,
                quarter=4,
                fs_div="CFS",
                revenue=150,
            ),
            Disclosure(
                rcept_no="20240312000736",
                corp_code="00126380",
                corp_name="삼성전자",
                disc_date=date(2024, 3, 12),
                disc_type="A",
                report_nm="사업보고서 (2023.12)",
                flr_nm="삼성전자",
            ),
            Disclosure(
                rcept_no="20250311001085",
                corp_code="00126380",
                corp_name="삼성전자",
                disc_date=date(2025, 3, 11),
                disc_type="A",
                report_nm="사업보고서 (2024.12)",
                flr_nm="삼성전자",
            ),
            Disclosure(
                rcept_no="20998220384504",
                corp_code="00126380",
                corp_name="삼성전자",
                disc_date=date(2025, 3, 12),
                disc_type="A",
                report_nm="사업보고서 (2024.12) [정정]",
                flr_nm="삼성전자",
            ),
        ])

    result = _attach_annual_sources(
        {
            "corp_code": "00126380",
            "fs_div": "CFS",
            "rows": [
                {"연도": 2023, "매출성장률": 10.0},
                {"연도": 2024, "매출성장률": 50.0},
            ],
            "data_quality": {"status": "usable"},
        },
        source_table="financials",
    )

    assert [
        row["source"]["rcept_no"] for row in result["rows"]
    ] == ["20240312000736", "20250311001085"]
    assert [
        source["rcept_no"]
        for source in result["rows"][1]["derived_sources"]["매출성장률"]
    ] == ["20240312000736", "20250311001085"]
    assert "20998220384504" not in str(result)
