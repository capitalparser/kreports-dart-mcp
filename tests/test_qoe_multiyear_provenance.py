from datetime import date
import math

import pytest
from sqlalchemy import create_engine, text

from kreports.db.models import Company, Disclosure, FinancialFactCompact


_ANNUAL_RECEIPTS = {
    2022: "20230318001234",
    2023: "20240318001234",
    2024: "20250318001234",
}


def _seed_annual_sources(session, years=(2022, 2023, 2024)):
    session.add(Company(
        corp_code="001", corp_name="A", stock_code="000001", market="KOSPI",
    ))
    for year in years:
        receipt = _ANNUAL_RECEIPTS[year]
        session.add(Disclosure(
            rcept_no=receipt,
            corp_code="001",
            corp_name="A",
            disc_date=date(int(receipt[:4]), 3, 18),
            disc_type="A",
            report_nm=f"사업보고서 ({year}.12)",
            flr_nm="A",
        ))


def _seed_qoe_facts(session, year, *, receipt=None, basis="company_year_annual_filing_match"):
    for metric_key, amount in (
        ("revenue", 100 + year),
        ("operating_profit", 10 + year),
        ("profit_loss", 8 + year),
        ("operating_cash_flow", 9 + year),
    ):
        session.add(FinancialFactCompact(
            corp_code="001",
            bsns_year=year,
            fs_div="CFS",
            metric_key=metric_key,
            metric_name=metric_key,
            amount=amount,
            source_account_id=f"ifrs-full_{metric_key}",
            source_table="financial_facts",
            unit="KRW",
            period_type="duration",
            citation_rcept_no=receipt if receipt is not None else _ANNUAL_RECEIPTS[year],
            citation_report_nm=f"사업보고서 ({year}.12)",
            citation_basis=basis,
            quality_status="usable",
        ))


def test_qoe_multiyear_result_is_limited_when_earlier_years_lack_exact_receipts(
    temp_engine,
):
    """Wrong-company/year receipts beside one proved year cannot prove three years."""
    from kreports.analysis.api import get_quality_of_earnings_pack
    from kreports.db.engine import get_session

    with get_session() as session:
        _seed_annual_sources(session)
        session.add(Disclosure(
            rcept_no="20230318009999",
            corp_code="002",
            corp_name="B",
            disc_date=date(2023, 3, 18),
            disc_type="A",
            report_nm="사업보고서 (2022.12)",
            flr_nm="B",
        ))
        _seed_qoe_facts(session, 2022, receipt="20230318009999")
        _seed_qoe_facts(session, 2023, receipt="20250318001234")
        _seed_qoe_facts(session, 2024)

    result = get_quality_of_earnings_pack("001", start_year=2022, end_year=2024)

    assert result["data_quality"]["status"] == "limited"
    assert result["metrics"]["years"] == 1
    assert [row["year"] for row in result["financial_observations"]] == [2022, 2023, 2024]
    assert result["financial_observations"][0]["source"]["rcept_no"] is None
    assert result["financial_observations"][1]["source"]["rcept_no"] is None
    assert result["financial_observations"][2]["source"]["rcept_no"] == _ANNUAL_RECEIPTS[2024]
    assert any("2022" in limitation for limitation in result["data_quality"]["limitations"])
    assert any("2023" in limitation for limitation in result["data_quality"]["limitations"])


def test_qoe_multiyear_public_pack_lists_every_proven_financial_year(
    temp_engine,
):
    """A usable QoE conclusion exposes all three exact annual-filing receipts."""
    from kreports.analysis.api import get_quality_of_earnings_pack
    from kreports.db.engine import get_session
    from kreports.mcp.contracts import build_answer_envelope, enrich_answer_response

    with get_session() as session:
        _seed_annual_sources(session)
        for year in _ANNUAL_RECEIPTS:
            _seed_qoe_facts(session, year)

    result = get_quality_of_earnings_pack("001", start_year=2022, end_year=2024)
    envelope = build_answer_envelope("get_quality_of_earnings_pack", result)
    pack = enrich_answer_response("get_quality_of_earnings_pack", result)["answer_pack"]

    assert result["data_quality"]["status"] == "usable"
    assert result["metrics"]["years"] == 3
    assert {
        source["rcept_no"] for source in result["financial_sources"]
    } == set(_ANNUAL_RECEIPTS.values())
    assert {item.rcept_no for item in envelope.evidence} == set(_ANNUAL_RECEIPTS.values())
    table = next(table for table in pack["tables"] if table["id"] == "quality_financial_provenance")
    assert [row["rcept_no"] for row in table["rows"]] == list(_ANNUAL_RECEIPTS.values())
    assert {source["rcept_no"] for source in pack["sources"]} == set(_ANNUAL_RECEIPTS.values())


def test_qoe_rejects_contaminated_compact_receipt_even_when_parent_is_canonical(
    temp_engine,
):
    """Extracting 14 digits from an attachment id must not admit the raw citation."""
    from kreports.analysis.investor_quality import quality_of_earnings_pack
    from kreports.db.engine import get_session

    with get_session() as session:
        _seed_annual_sources(session)
        _seed_qoe_facts(session, 2022)
        _seed_qoe_facts(
            session,
            2023,
            receipt="attachment_20240318001234_001_xml",
        )
        _seed_qoe_facts(session, 2024)

    result = quality_of_earnings_pack("001", start_year=2022, end_year=2024)

    assert result["data_quality"]["status"] == "limited"
    assert result["metrics"]["years"] == 2
    observation = next(
        row for row in result["financial_observations"]
        if row["year"] == 2023
    )
    assert observation["provenance_status"] == "compact_citation_not_exact_annual_filing"
    assert observation["source"]["rcept_no"] is None


@pytest.mark.parametrize(
    ("field", "invalid_value", "expected_status"),
    [
        ("quality_status", "limited", "compact_quality_not_usable"),
        ("amount", math.inf, "compact_amount_not_finite_numeric"),
        ("unit", "  ", "compact_unit_missing"),
        ("period_type", "instant", "compact_period_not_duration"),
    ],
)
def test_qoe_rejects_semantically_invalid_compact_metric_rows(
    temp_engine,
    field,
    invalid_value,
    expected_status,
):
    """QoE must not compute from non-usable, non-finite, or instant values."""
    from kreports.analysis.investor_quality import quality_of_earnings_pack
    from kreports.db.engine import get_session

    with get_session() as session:
        _seed_annual_sources(session)
        for year in _ANNUAL_RECEIPTS:
            _seed_qoe_facts(session, year)
    with temp_engine.begin() as connection:
        connection.execute(text(f"""
            UPDATE financial_facts_compact
            SET {field}=:invalid_value
            WHERE corp_code='001' AND bsns_year=2023
              AND metric_key='profit_loss'
        """), {"invalid_value": invalid_value})

    result = quality_of_earnings_pack("001", start_year=2022, end_year=2024)

    assert result["data_quality"]["status"] == "limited"
    assert result["metrics"]["years"] == 2
    observation = next(
        row for row in result["financial_observations"]
        if row["year"] == 2023
    )
    assert observation["provenance_status"] == expected_status


def test_qoe_rejects_mixed_units_between_ratio_operands(temp_engine):
    """A KRW numerator and USD denominator cannot produce a QoE ratio."""
    from kreports.analysis.investor_quality import quality_of_earnings_pack
    from kreports.db.engine import get_session

    with get_session() as session:
        _seed_annual_sources(session)
        for year in _ANNUAL_RECEIPTS:
            _seed_qoe_facts(session, year)
    with temp_engine.begin() as connection:
        connection.execute(text("""
            UPDATE financial_facts_compact
            SET unit='USD'
            WHERE corp_code='001' AND bsns_year=2023
              AND metric_key='operating_profit'
        """))

    result = quality_of_earnings_pack("001", start_year=2022, end_year=2024)

    assert result["data_quality"]["status"] == "limited"
    assert result["metrics"]["years"] == 2
    observation = next(
        row for row in result["financial_observations"]
        if row["year"] == 2023
    )
    assert observation["provenance_status"] == "compact_ratio_unit_mismatch"


def test_qoe_three_revenue_only_years_do_not_form_a_qoe_conclusion(temp_engine):
    """A year count is not QoE coverage without all four canonical inputs."""
    from kreports.analysis.api import get_quality_of_earnings_pack
    from kreports.db.engine import get_session
    from kreports.mcp.contracts import enrich_answer_response

    with get_session() as session:
        _seed_annual_sources(session)
        for year, receipt in _ANNUAL_RECEIPTS.items():
            session.add(FinancialFactCompact(
                corp_code="001",
                bsns_year=year,
                fs_div="CFS",
                metric_key="revenue",
                metric_name="revenue",
                amount=100 + year,
                source_account_id="ifrs-full_Revenue",
                source_table="financial_facts",
                unit="KRW",
                period_type="duration",
                citation_rcept_no=receipt,
                citation_report_nm=f"사업보고서 ({year}.12)",
                citation_basis="company_year_annual_filing_match",
                quality_status="usable",
            ))

    result = get_quality_of_earnings_pack("001", start_year=2022, end_year=2024)
    pack = enrich_answer_response(
        "get_quality_of_earnings_pack", result,
    )["answer_pack"]

    assert result["metrics"]["years"] == 0
    assert result["verdict"] == "insufficient_data"
    assert result["data_quality"]["status"] == "limited"
    assert result["confirmed_facts"] == []
    assert pack["summary"]["status"] == "limited"
    assert [
        row["provenance_status"] for row in result["financial_observations"]
    ] == ["compact_required_metrics_missing"] * 3
    assert result["financial_observations"][0]["missing_metrics"] == [
        "operating_cash_flow",
        "operating_profit",
        "profit_loss",
    ]


def test_qoe_two_proven_complete_years_do_not_create_public_confirmed_fact(
    temp_engine,
):
    """Two valid observations are inspectable but not a multi-year QoE fact."""
    from kreports.analysis.api import get_quality_of_earnings_pack
    from kreports.db.engine import get_session
    from kreports.mcp.contracts import build_answer_envelope

    with get_session() as session:
        _seed_annual_sources(session, years=(2023, 2024))
        _seed_qoe_facts(session, 2023)
        _seed_qoe_facts(session, 2024)

    result = get_quality_of_earnings_pack("001", start_year=2023, end_year=2024)
    envelope = build_answer_envelope("get_quality_of_earnings_pack", result)

    assert result["metrics"]["years"] == 2
    assert result["data_quality"]["status"] == "limited"
    assert result["confirmed_facts"] == []
    assert envelope.confirmed_facts == []
    assert envelope.evidence == []


def test_qoe_legacy_table_is_inspectable_but_never_assessed(temp_engine):
    """Missing provenance columns cannot leak money or produce stable QoE."""
    from kreports.analysis.api import get_quality_of_earnings_pack
    from kreports.db.engine import get_session
    from kreports.mcp.contracts import enrich_answer_response

    with get_session() as session:
        session.add(Company(
            corp_code="001",
            corp_name="A",
            stock_code="000001",
            market="KOSPI",
        ))
    with temp_engine.begin() as connection:
        connection.execute(text("DROP TABLE financial_facts_compact"))
        connection.execute(text("""
            CREATE TABLE financial_facts_compact (
                corp_code TEXT,
                bsns_year INTEGER,
                fs_div TEXT,
                metric_key TEXT,
                amount INTEGER
            )
        """))
        for year in _ANNUAL_RECEIPTS:
            for metric_key, amount in (
                ("revenue", 100 + year),
                ("operating_profit", 10 + year),
                ("profit_loss", 8 + year),
                ("operating_cash_flow", 9 + year),
            ):
                connection.execute(text("""
                    INSERT INTO financial_facts_compact
                    VALUES ('001', :year, 'CFS', :metric_key, :amount)
                """), {
                    "year": year,
                    "metric_key": metric_key,
                    "amount": amount,
                })

    result = get_quality_of_earnings_pack("001", start_year=2022, end_year=2024)
    public = enrich_answer_response("get_quality_of_earnings_pack", result)

    assert result["data_quality"]["status"] == "limited"
    assert result["verdict"] == "insufficient_data"
    assert result["metrics"] == {
        "years": 0,
        "margin_volatility": None,
        "low_cash_conversion_years": 0,
        "negative_ocf_years": 0,
    }
    assert result["signals"] == []
    assert result["evidence"] == []
    assert result["confirmed_facts"] == []
    assert [row["year"] for row in result["financial_observations"]] == [
        2022,
        2023,
        2024,
    ]
    for row in result["financial_observations"]:
        assert row["provenance_status"] == "compact_provenance_columns_missing"
        assert "revenue" not in row
        assert "operating_profit" not in row
        assert "net_income" not in row
        assert "operating_cf" not in row
    assert public["answer_pack"]["summary"]["status"] == "limited"
    assert "stable" not in public["answer"].casefold()


def test_qoe_conflicting_compact_duplicates_are_not_used_for_multiyear_conclusion(
    monkeypatch,
):
    """Two conflicting copies of one annual metric must fail closed, not pick a row."""
    import kreports.db.engine as engine_module
    from kreports.analysis.investor_quality import quality_of_earnings_pack

    engine = create_engine("sqlite:///:memory:")
    monkeypatch.setattr(engine_module, "engine", engine)
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE disclosures (
                rcept_no TEXT, corp_code TEXT, corp_name TEXT, disc_date TEXT,
                report_nm TEXT
            )
        """))
        connection.execute(text("""
            CREATE TABLE financial_facts_compact (
                corp_code TEXT, bsns_year INTEGER, fs_div TEXT, metric_key TEXT,
                amount INTEGER, unit TEXT, period_type TEXT,
                citation_rcept_no TEXT, citation_report_nm TEXT,
                citation_basis TEXT, quality_status TEXT,
                source_account_id TEXT, source_table TEXT
            )
        """))
        for year, receipt in _ANNUAL_RECEIPTS.items():
            connection.execute(text("""
                INSERT INTO disclosures
                VALUES (:receipt, '001', 'A', :disc_date, :report_nm)
            """), {
                "receipt": receipt,
                "disc_date": f"{receipt[:4]}-03-18",
                "report_nm": f"사업보고서 ({year}.12)",
            })
            for metric_key, amount in (
                ("revenue", 100 + year),
                ("operating_profit", 10 + year),
                ("profit_loss", 8 + year),
                ("operating_cash_flow", 9 + year),
            ):
                connection.execute(text("""
                    INSERT INTO financial_facts_compact VALUES (
                        '001', :year, 'CFS', :metric_key, :amount, 'KRW', 'duration',
                        :receipt, :report_nm, 'company_year_annual_filing_match',
                        'usable', 'account', 'financial_facts'
                    )
                """), {
                    "year": year,
                    "metric_key": metric_key,
                    "amount": amount,
                    "receipt": receipt,
                    "report_nm": f"사업보고서 ({year}.12)",
                })
        connection.execute(text("""
            INSERT INTO financial_facts_compact VALUES (
                '001', 2023, 'CFS', 'revenue', 999, 'KRW', 'duration',
                '20240318001234', '사업보고서 (2023.12)',
                'company_year_annual_filing_match', 'usable', 'other-account',
                'financial_facts'
            )
        """))

    result = quality_of_earnings_pack("001", start_year=2022, end_year=2024)

    assert result["data_quality"]["status"] == "limited"
    assert result["metrics"]["years"] == 2
    observation = next(row for row in result["financial_observations"] if row["year"] == 2023)
    assert observation["provenance_status"] == "conflicting_compact_series_rows"
    assert observation["source"]["rcept_no"] is None
