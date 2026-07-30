"""Regression contracts for auditor materiality preparation.

Each test names the production break it catches before exercising the public
calculation boundary with literal, source-backed fixtures.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import json


_ANNUAL_BASIS = "company_year_annual_filing_match"


def _annual_disclosure(session, *, corp_code: str, year: int, receipt: str) -> None:
    """Persist the exact annual filing that a compact fact is allowed to cite."""
    from kreports.db.models import Disclosure

    session.add(Disclosure(
        rcept_no=receipt,
        corp_code=corp_code,
        corp_name="삼성전자" if corp_code == "00126380" else "다른회사",
        disc_date=date(int(receipt[:4]), int(receipt[4:6]), int(receipt[6:8])),
        disc_type="A",
        report_nm=f"사업보고서 ({year}.12)",
        flr_nm="삼성전자" if corp_code == "00126380" else "다른회사",
    ))


def test_benchmark_series_rejects_malformed_receipts_and_wrong_citation_basis():
    """A compact amount is not a benchmark fact without canonical annual provenance."""
    from kreports.analysis.materiality_benchmark import build_benchmark_series

    series = build_benchmark_series([
        {"bsns_year": 2025, "fs_div": "CFS", "metric_key": "revenue", "amount": 100,
         "unit": "KRW", "period_type": "duration", "quality_status": "usable",
         "citation_rcept_no": "not-a-rcept", "citation_basis": _ANNUAL_BASIS},
        {"bsns_year": 2024, "fs_div": "CFS", "metric_key": "revenue", "amount": 100,
         "unit": "KRW", "period_type": "duration", "quality_status": "usable",
         "citation_rcept_no": "20250318000001", "citation_basis": "endpoint_lineage"},
    ], years=[2025, 2024], fs_div="CFS")

    assert all(row["amount"] is None for row in series["revenue"])
    assert "invalid_citation_receipt" in series["revenue"][0]["limitations"]
    assert "citation_basis_not_company_year_annual_filing_match" in series["revenue"][1]["limitations"]


def test_prepare_rejects_foreign_or_wrong_year_annual_filing_receipts(temp_engine):
    """A receipt must resolve to this company and this business year's annual filing."""
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from kreports.analysis.materiality_benchmark import prepare_audit_materiality_inputs
    from kreports.db.models import Company

    with Session(temp_engine) as session:
        session.add_all([
            Company(corp_code="00126380", stock_code="005930", corp_name="삼성전자"),
            Company(corp_code="00999999", corp_name="다른회사"),
        ])
        _annual_disclosure(session, corp_code="00999999", year=2025, receipt="20260318000001")
        _annual_disclosure(session, corp_code="00126380", year=2023, receipt="20250318000001")
        for year, receipt in ((2025, "20260318000001"), (2024, "20250318000001")):
            session.execute(text("""
                INSERT INTO financial_facts_compact
                (corp_code, bsns_year, fs_div, metric_key, metric_name, amount, unit, period_type,
                 citation_rcept_no, citation_basis, quality_status, fetched_at)
                VALUES ('00126380', :year, 'CFS', 'revenue', '매출액', 100, 'KRW', 'duration',
                        :receipt, 'company_year_annual_filing_match', 'usable', CURRENT_TIMESTAMP)
            """), {"year": year, "receipt": receipt})
        session.commit()

    result = prepare_audit_materiality_inputs("00126380", end_year=2025, years_back=3, fs_strategy="CFS")

    assert result["data_quality"]["status"] == "limited"
    assert all(row["amount"] is None for row in result["benchmark_series"]["revenue"])
    assert all("annual_filing_receipt_mismatch" in row["limitations"] for row in result["benchmark_series"]["revenue"][:2])
    assert result["materiality_candidates"] == []


def test_benchmark_series_rejects_conflicting_duplicates_and_deduplicates_identical_rows():
    """Database row order must neither decide a conflict nor duplicate a proven observation."""
    from kreports.analysis.materiality_benchmark import build_benchmark_series

    proven = {
        "bsns_year": 2024, "fs_div": "CFS", "metric_key": "revenue", "amount": 100,
        "unit": "KRW", "period_type": "duration", "quality_status": "usable",
        "citation_rcept_no": "20250318000001", "citation_basis": _ANNUAL_BASIS,
    }
    series = build_benchmark_series([
        {**proven, "bsns_year": 2025, "amount": 100, "citation_rcept_no": "20260318000001"},
        {**proven, "bsns_year": 2025, "amount": 101, "citation_rcept_no": "20260318000001"},
        proven,
        dict(proven),
    ], years=[2025, 2024], fs_div="CFS")

    conflict, identical = series["revenue"]
    assert conflict["amount"] is None
    assert conflict["basis"] == "limited"
    assert "conflicting_compact_series_rows" in conflict["limitations"]
    assert identical["amount"] == Decimal("100")
    assert identical["sources"] == [identical["source"]]


def test_materiality_public_surfaces_keep_rejected_rows_but_withhold_candidate_money(temp_engine):
    """Public envelope and answer pack expose the limitation, never money from rejected facts."""
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from kreports.db.models import Company
    from kreports.mcp.tools import call_tool

    with Session(temp_engine) as session:
        session.add(Company(corp_code="00126380", stock_code="005930", corp_name="삼성전자"))
        for year in (2025, 2024, 2023):
            _annual_disclosure(session, corp_code="00126380", year=year, receipt=f"{year + 1}0318000001")
            session.execute(text("""
                INSERT INTO financial_facts_compact
                (corp_code, bsns_year, fs_div, metric_key, metric_name, amount, unit, period_type,
                 citation_rcept_no, citation_basis, quality_status, fetched_at)
                VALUES ('00126380', :year, 'CFS', 'revenue', '매출액', 100, 'KRW', 'duration',
                        :receipt, 'wrong_basis', 'usable', CURRENT_TIMESTAMP)
            """), {"year": year, "receipt": f"{year + 1}0318000001"})
        session.commit()

    out = json.loads(call_tool("prepare_audit_materiality_inputs", {
        "company": "005930", "end_year": 2025, "years_back": 3, "fs_strategy": "CFS",
    }))

    series_rows = [row for row in out["benchmark_series"]["revenue"] if row["year"] in {2025, 2024, 2023}]
    assert out["data_quality"]["status"] == out["answer_pack"]["data_quality"]["status"] == "limited"
    assert all(row["amount"] is None and row["limitations"] for row in series_rows)
    assert out["materiality_candidates"] == []
    candidate_table = next(table for table in out["answer_pack"]["tables"] if table["id"] == "materiality_candidates")
    assert candidate_table["rows"] == []


def test_direct_pbt_wins_over_derived_pbt_and_keeps_annual_receipt():
    """Replacing a compatible direct PBT fact with a derived value is a bug."""
    from kreports.analysis.materiality_benchmark import build_benchmark_series

    rows = [
        {
            "bsns_year": 2025,
            "fs_div": "CFS",
            "metric_key": "profit_before_tax",
            "amount": 150,
            "unit": "KRW",
            "period_type": "duration",
            "quality_status": "usable",
            "citation_rcept_no": "20260318000001",
            "citation_report_nm": "사업보고서 (2025.12)",
            "citation_basis": _ANNUAL_BASIS,
        },
        {
            "bsns_year": 2025,
            "fs_div": "CFS",
            "metric_key": "profit_loss",
            "amount": 100,
            "unit": "KRW",
            "period_type": "duration",
            "quality_status": "usable",
            "citation_rcept_no": "20260318000001",
            "citation_basis": _ANNUAL_BASIS,
        },
        {
            "bsns_year": 2025,
            "fs_div": "CFS",
            "metric_key": "tax_expense",
            "amount": 20,
            "unit": "KRW",
            "period_type": "duration",
            "quality_status": "usable",
            "citation_rcept_no": "20260318000001",
            "citation_basis": _ANNUAL_BASIS,
        },
    ]

    series = build_benchmark_series(rows, years=[2025], fs_div="CFS")

    observation = series["profit_before_tax"][0]
    assert observation["amount"] == Decimal("150")
    assert observation["basis"] == "direct_annual_fact"
    assert observation["source"]["rcept_no"] == "20260318000001"


def test_derived_pbt_requires_compatible_operands_and_preserves_both_sources():
    """Deriving PBT across incompatible scopes or without both receipts is a bug."""
    from kreports.analysis.materiality_benchmark import build_benchmark_series

    compatible = [
        {"bsns_year": 2024, "fs_div": "CFS", "metric_key": "profit_loss", "amount": 80, "unit": "KRW", "period_type": "duration", "quality_status": "usable", "citation_rcept_no": "20250318000001", "citation_basis": _ANNUAL_BASIS},
        {"bsns_year": 2024, "fs_div": "CFS", "metric_key": "tax_expense", "amount": 20, "unit": "KRW", "period_type": "duration", "quality_status": "usable", "citation_rcept_no": "20250318000001", "citation_basis": _ANNUAL_BASIS},
        {"bsns_year": 2023, "fs_div": "OFS", "metric_key": "profit_loss", "amount": 70, "unit": "KRW", "period_type": "duration", "quality_status": "usable", "citation_rcept_no": "20240318000001", "citation_basis": _ANNUAL_BASIS},
        {"bsns_year": 2023, "fs_div": "CFS", "metric_key": "tax_expense", "amount": 10, "unit": "KRW", "period_type": "duration", "quality_status": "usable", "citation_rcept_no": "20240318000001", "citation_basis": _ANNUAL_BASIS},
    ]

    series = build_benchmark_series(compatible, years=[2024, 2023], fs_div="CFS")

    derived = series["profit_before_tax"][0]
    incompatible = series["profit_before_tax"][1]
    assert derived["amount"] == Decimal("100")
    assert derived["basis"] == "derived_profit_loss_plus_tax_expense"
    assert {source["rcept_no"] for source in derived["sources"]} == {"20250318000001"}
    assert incompatible["amount"] is None
    assert "incompatible_operands" in incompatible["limitations"]


def test_derived_pbt_withholds_money_when_either_operand_lacks_annual_admission():
    """A derived result must not launder an unproven profit or tax compact row."""
    from kreports.analysis.materiality_benchmark import build_benchmark_series

    series = build_benchmark_series([
        {"bsns_year": 2025, "fs_div": "CFS", "metric_key": "profit_loss", "amount": 80,
         "unit": "KRW", "period_type": "duration", "quality_status": "usable",
         "citation_rcept_no": "20260318000001", "citation_basis": _ANNUAL_BASIS},
        {"bsns_year": 2025, "fs_div": "CFS", "metric_key": "tax_expense", "amount": 20,
         "unit": "KRW", "period_type": "duration", "quality_status": "usable",
         "citation_rcept_no": "20260318000001", "citation_basis": "endpoint_lineage"},
    ], years=[2025], fs_div="CFS", annual_sources={
        2025: {"rcept_no": "20260318000001", "fs_div": "CFS"},
    })

    pbt = series["profit_before_tax"][0]
    assert pbt["amount"] is None
    assert "citation_basis_not_company_year_annual_filing_match" in pbt["limitations"]
    assert pbt["rejected_rows"] == [{
        "metric_key": "tax_expense", "bsns_year": 2025, "fs_div": "CFS",
        "citation_rcept_no": "20260318000001", "citation_basis": "endpoint_lineage",
    }]


def test_stability_requires_three_comparable_years_and_keeps_anomalies_visible():
    """Calling two years stable or hiding a discontinuous equity observation is a bug."""
    from kreports.analysis.materiality_benchmark import observe_stability

    insufficient = observe_stability([
        {"year": 2025, "amount": Decimal("100")},
        {"year": 2024, "amount": Decimal("110")},
    ], requested_years=[2025, 2024, 2023])
    anomalous = observe_stability([
        {"year": 2025, "amount": Decimal("100")},
        {"year": 2024, "amount": Decimal("105")},
        {"year": 2023, "amount": Decimal("1000")},
    ], requested_years=[2025, 2024, 2023])

    assert insufficient["stability"] == "insufficient"
    assert insufficient["usable_year_count"] == 2
    assert anomalous["anomaly_flags"] == ["material_discontinuity"]
    assert anomalous["role"] == "avoid_as_sole_basis"


def test_candidates_use_exact_decimal_amounts_and_methodology_references():
    """Using binary-float candidate money or showing an unreferenced rate is a bug."""
    from kreports.analysis.materiality_benchmark import materiality_candidates

    candidates = materiality_candidates({
        "profit_before_tax": {
            "selected_amount": Decimal("100.10"),
            "selected_year": 2025,
            "role": "cross_check",
            "stability": "observed",
        },
    })

    row = candidates[0]
    assert row["central_candidate_amount"] == Decimal("5.0050")
    assert row["authority_levels"] == ["standard_illustration", "internal_methodology"]
    assert row["conclusion_status"] == "not_assessed"
    assert row["reference_ids"]


def test_materiality_tool_dispatch_preserves_not_assessed_tables_and_receipts(temp_engine):
    """Dropping the materiality tool from a public boundary or its evidence pack is a bug."""
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from kreports.db.models import Company
    from kreports.mcp.tools import call_tool

    with Session(temp_engine) as session:
        session.add(Company(corp_code="00126380", stock_code="005930", corp_name="삼성전자"))
        for year, pbt, revenue, assets, equity in (
            (2025, 110, 1000, 2000, 1500),
            (2024, 100, 950, 1900, 1400),
            (2023, 90, 900, 1800, 1300),
        ):
            _annual_disclosure(session, corp_code="00126380", year=year, receipt=f"{year + 1}0318000001")
            for key, amount, period in (
                ("profit_before_tax", pbt, "duration"),
                ("revenue", revenue, "duration"),
                ("assets", assets, "instant"),
                ("equity", equity, "instant"),
            ):
                session.execute(text("""
                    INSERT INTO financial_facts_compact
                    (corp_code, bsns_year, fs_div, metric_key, metric_name, amount,
                     unit, period_type, citation_rcept_no, citation_report_nm, citation_basis,
                     quality_status, fetched_at)
                    VALUES ('00126380', :year, 'CFS', :key, :key, :amount,
                            'KRW', :period, :receipt, :report_nm, 'company_year_annual_filing_match',
                            'usable', CURRENT_TIMESTAMP)
                """), {
                    "year": year, "key": key, "amount": amount, "period": period,
                    "receipt": f"{year + 1}0318000001", "report_nm": f"사업보고서 ({year}.12)",
                })
        session.commit()

    out = json.loads(call_tool("prepare_audit_materiality_inputs", {
        "company": "005930", "end_year": 2025, "years_back": 3, "fs_strategy": "auto",
    }))

    assert out["assessment_status"] == out["domain_verdict"] == "not_assessed"
    assert {table["id"] for table in out["answer_pack"]["tables"]} >= {
        "materiality_benchmark_stability", "materiality_candidates", "materiality_methodology_references",
    }
    assert out["answer"].startswith("중요성 기준 후보 준비")
    assert out["answer_pack"]["sources"][0]["rcept_no"] == "20260318000001"


def test_candidates_withhold_numeric_amounts_for_anomalous_or_insufficient_benchmarks():
    """An excluded benchmark must remain visible in stability, never as money."""
    from kreports.analysis.materiality_benchmark import materiality_candidates

    candidates = materiality_candidates({
        "equity": {
            "selected_amount": Decimal("1000"), "selected_year": 2025,
            "stability": "observed", "role": "avoid_as_sole_basis",
        },
        "revenue": {
            "selected_amount": Decimal("2000"), "selected_year": 2025,
            "stability": "insufficient", "role": "not_assessed",
        },
    })

    assert candidates == []


def test_pbt_derivation_requires_same_filing_and_falls_back_when_direct_is_invalid():
    """A bad direct PBT row may not suppress a valid, same-filing derivation."""
    from kreports.analysis.materiality_benchmark import build_benchmark_series

    rows = [
        {"bsns_year": 2025, "fs_div": "CFS", "metric_key": "profit_before_tax", "amount": 999,
         "unit": None, "period_type": "duration", "quality_status": "limited", "citation_rcept_no": "20260318000001", "citation_basis": _ANNUAL_BASIS},
        {"bsns_year": 2025, "fs_div": "CFS", "metric_key": "profit_loss", "amount": 80,
         "unit": "KRW", "period_type": "duration", "quality_status": "usable", "citation_rcept_no": "20260318000001", "citation_basis": _ANNUAL_BASIS},
        {"bsns_year": 2025, "fs_div": "CFS", "metric_key": "tax_expense", "amount": 20,
         "unit": "KRW", "period_type": "duration", "quality_status": "usable", "citation_rcept_no": "20260318000001", "citation_basis": _ANNUAL_BASIS},
        {"bsns_year": 2024, "fs_div": "CFS", "metric_key": "profit_loss", "amount": 80,
         "unit": "KRW", "period_type": "duration", "quality_status": "usable", "citation_rcept_no": "20250318000001", "citation_basis": _ANNUAL_BASIS},
        {"bsns_year": 2024, "fs_div": "CFS", "metric_key": "tax_expense", "amount": 20,
         "unit": "KRW", "period_type": "duration", "quality_status": "usable", "citation_rcept_no": "20250319000001", "citation_basis": _ANNUAL_BASIS},
    ]

    series = build_benchmark_series(rows, years=[2025, 2024], fs_div="CFS")

    derived = series["profit_before_tax"][0]
    mismatch = series["profit_before_tax"][1]
    assert derived["amount"] == Decimal("100")
    assert derived["basis"] == "derived_profit_loss_plus_tax_expense"
    assert {source["operand_metric"] for source in derived["sources"]} == {"profit_loss", "tax_expense"}
    assert mismatch["amount"] is None
    assert "incompatible_filing_provenance" in mismatch["limitations"]


def test_missing_pbt_operand_does_not_claim_a_filing_provenance_mismatch():
    """Receipt mismatch is meaningful only after both otherwise-usable operands exist."""
    from kreports.analysis.materiality_benchmark import build_benchmark_series

    series = build_benchmark_series([
        {"bsns_year": 2025, "fs_div": "CFS", "metric_key": "profit_before_tax", "amount": 999,
         "unit": None, "period_type": "duration", "quality_status": "limited", "citation_rcept_no": "20260318000001", "citation_basis": _ANNUAL_BASIS},
        {"bsns_year": 2025, "fs_div": "CFS", "metric_key": "profit_loss", "amount": 80,
         "unit": "KRW", "period_type": "duration", "quality_status": "usable", "citation_rcept_no": "20260318000001", "citation_basis": _ANNUAL_BASIS},
    ], years=[2025], fs_div="CFS")

    limitations = series["profit_before_tax"][0]["limitations"]
    assert "incompatible_operands" in limitations
    assert "direct_pbt_unusable" in limitations
    assert "incompatible_filing_provenance" not in limitations


def test_stability_role_changes_with_observed_variation_not_metric_name():
    """The same metric must not have a fixed role independent of its series."""
    from kreports.analysis.materiality_benchmark import observe_stability

    stable = observe_stability([
        {"year": 2025, "amount": Decimal("100")},
        {"year": 2024, "amount": Decimal("102")},
        {"year": 2023, "amount": Decimal("98")},
    ], requested_years=[2025, 2024, 2023])
    volatile = observe_stability([
        {"year": 2025, "amount": Decimal("100")},
        {"year": 2024, "amount": Decimal("500")},
        {"year": 2023, "amount": Decimal("20")},
    ], requested_years=[2025, 2024, 2023])

    assert stable["volatility_classification"] == "low"
    assert stable["role"] == "primary_candidate"
    assert volatile["volatility_classification"] == "high"
    assert volatile["role"] == "avoid_as_sole_basis"


def test_prepare_assigns_roles_from_each_metric_variation_not_metric_identity(temp_engine):
    """Volatile PBT and volatile revenue must each be excluded when their own series moves."""
    from sqlalchemy import text
    from sqlalchemy.orm import Session
    from kreports.analysis.materiality_benchmark import prepare_audit_materiality_inputs
    from kreports.db.models import Company

    with Session(temp_engine) as session:
        session.add(Company(corp_code="00126380", stock_code="005930", corp_name="삼성전자"))
        for year, pbt, revenue in ((2025, 100, 100), (2024, 102, 500), (2023, 98, 20)):
            _annual_disclosure(session, corp_code="00126380", year=year, receipt=f"{year + 1}0318000001")
            for key, amount, period in (("profit_before_tax", pbt, "duration"), ("revenue", revenue, "duration")):
                session.execute(text("""
                    INSERT INTO financial_facts_compact
                    (corp_code, bsns_year, fs_div, metric_key, metric_name, amount, unit, period_type,
                     citation_rcept_no, citation_basis, quality_status, fetched_at)
                    VALUES ('00126380', :year, 'CFS', :key, :key, :amount, 'KRW', :period,
                            :receipt, 'company_year_annual_filing_match', 'usable', CURRENT_TIMESTAMP)
                """), {"year": year, "key": key, "amount": amount, "period": period, "receipt": f"{year + 1}0318000001"})
        session.commit()

    first = prepare_audit_materiality_inputs("00126380", end_year=2025, years_back=3, fs_strategy="CFS")
    assert first["benchmark_stability"]["profit_before_tax"]["role"] == "primary_candidate"
    assert first["benchmark_stability"]["revenue"]["role"] == "avoid_as_sole_basis"

    with temp_engine.begin() as conn:
        conn.execute(text("UPDATE financial_facts_compact SET amount=CASE metric_key WHEN 'profit_before_tax' THEN CASE bsns_year WHEN 2025 THEN 100 WHEN 2024 THEN 500 ELSE 20 END WHEN 'revenue' THEN CASE bsns_year WHEN 2025 THEN 100 WHEN 2024 THEN 102 ELSE 98 END END WHERE metric_key IN ('profit_before_tax', 'revenue')"))
    second = prepare_audit_materiality_inputs("00126380", end_year=2025, years_back=3, fs_strategy="CFS")
    assert second["benchmark_stability"]["profit_before_tax"]["role"] == "avoid_as_sole_basis"
    assert second["benchmark_stability"]["revenue"]["role"] == "primary_candidate"


def test_materiality_pack_keeps_derived_operand_evidence_and_limited_tables(temp_engine):
    """The envelope and answer-pack must not drop derivation evidence or empty tables."""
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from kreports.db.models import Company
    from kreports.mcp.tools import call_tool

    with Session(temp_engine) as session:
        session.add(Company(corp_code="00126380", stock_code="005930", corp_name="삼성전자"))
        for year in (2025, 2024, 2023):
            receipt = f"{year + 1}0318000001"
            _annual_disclosure(session, corp_code="00126380", year=year, receipt=receipt)
            for key, amount in (("profit_loss", 80), ("tax_expense", 20)):
                session.execute(text("""
                    INSERT INTO financial_facts_compact
                    (corp_code, bsns_year, fs_div, metric_key, metric_name, amount,
                     unit, period_type, citation_rcept_no, citation_basis, quality_status, fetched_at)
                    VALUES ('00126380', :year, 'CFS', :key, :key, :amount,
                            'KRW', 'duration', :receipt, 'company_year_annual_filing_match', 'usable', CURRENT_TIMESTAMP)
                """), {"year": year, "key": key, "amount": amount, "receipt": receipt})
        session.commit()

    out = json.loads(call_tool("prepare_audit_materiality_inputs", {
        "company": "005930", "end_year": 2025, "years_back": 3, "fs_strategy": "CFS",
    }))

    pbt_fact = next(fact for fact in out["confirmed_facts"] if "법인세차감전순이익 2025" in fact["statement"])
    assert {source["operand_metric"] for source in pbt_fact["sources"]} == {"profit_loss", "tax_expense"}
    assert out["answer_pack"]["sources"][0]["rcept_no"] == "20260318000001"
    tables = {table["id"]: table for table in out["answer_pack"]["tables"]}
    assert {"materiality_benchmark_series", "materiality_benchmark_stability", "materiality_candidates", "materiality_methodology_references"} <= set(tables)
    assert tables["materiality_candidates"]["rows"][0]["benchmark_label_ko"] == "법인세차감전순이익"
    assert {"issuer", "source_location", "authority_level"} <= {column["field"] for column in tables["materiality_methodology_references"]["columns"]}
    internal_reference = next(
        row for row in tables["materiality_methodology_references"]["rows"]
        if row["reference_id"] == "materiality_candidate_ranges_v1"
    )
    assert internal_reference["source_location"] == "docs/data-contract.md#audit-materiality-preparation"


def test_limited_provenance_keeps_empty_candidate_table_and_withheld_wording(temp_engine):
    """A limited cache must render the contract tables without invented amounts."""
    from sqlalchemy.orm import Session

    from kreports.db.models import Company
    from kreports.mcp.tools import call_tool
    from kreports.analysis.materiality_benchmark import prepare_audit_materiality_inputs

    with Session(temp_engine) as session:
        session.add(Company(corp_code="00126380", stock_code="005930", corp_name="삼성전자"))
        session.commit()

    raw = prepare_audit_materiality_inputs("00126380", end_year=2025, years_back=3, fs_strategy="CFS")
    assert raw["data_quality"]["status"] == "limited"
    from kreports.mcp.answer_pack import build_answer_pack
    assert build_answer_pack("prepare_audit_materiality_inputs", raw)

    out = json.loads(call_tool("prepare_audit_materiality_inputs", {
        "company": "005930", "end_year": 2025, "years_back": 3, "fs_strategy": "CFS",
    }))

    tables = {table["id"]: table for table in out["answer_pack"]["tables"]}
    assert "materiality_candidates" in tables, out
    assert tables["materiality_candidates"]["rows"] == []
    assert "후보 금액을 표시하지 않았습니다" in tables["materiality_candidates"]["note"]
    assert "후보 금액을 표시하지 않았습니다" in out["answer"]


def test_live_shaped_legacy_compact_rows_render_limited_series_and_methodology_sources(temp_engine):
    """A legacy compact schema's null provenance must stay inspectable and withheld."""
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from kreports.db.models import Company
    from kreports.mcp.tools import call_tool

    with Session(temp_engine) as session:
        session.add(Company(corp_code="00126380", stock_code="005930", corp_name="삼성전자"))
        for year in (2025, 2024, 2023):
            session.execute(text("""
                INSERT INTO financial_facts_compact
                (corp_code, bsns_year, fs_div, metric_key, metric_name, amount, fetched_at)
                VALUES ('00126380', :year, 'CFS', 'revenue', 'revenue', :amount, CURRENT_TIMESTAMP)
            """), {"year": year, "amount": 1000})
        session.commit()

    out = json.loads(call_tool("prepare_audit_materiality_inputs", {
        "company": "005930", "end_year": 2025, "years_back": 3, "fs_strategy": "CFS",
    }))

    tables = {table["id"]: table for table in out["answer_pack"]["tables"]}
    assert out["data_quality"]["status"] == "limited"
    assert len(tables["materiality_benchmark_series"]["rows"]) == 12
    assert tables["materiality_candidates"]["rows"] == []
    assert any(source["url"] == "https://standards.auasb.gov.au/asa-320-dec-2015" for source in out["answer_pack"]["sources"])
    assert "후보 금액을 표시하지 않았습니다" in out["answer"]


def test_materiality_prepare_handles_missing_schema_without_operational_error(temp_engine):
    """A partial local cache is missing evidence, not an exposed SQL failure."""
    from sqlalchemy import text
    from kreports.analysis.materiality_benchmark import prepare_audit_materiality_inputs

    with temp_engine.begin() as conn:
        conn.execute(text("DROP TABLE companies"))
        conn.execute(text("DROP TABLE financial_facts_compact"))

    result = prepare_audit_materiality_inputs("00126380", end_year=2025, years_back=3)

    assert result["data_quality"]["status"] == "limited"
    assert result["subject"]["corp_code"] == "00126380"


def test_rate_references_do_not_overstate_isa_a8_ranges():
    """Only the PBT 5 percent illustration may carry the matching ISA A8 reference."""
    from kreports.analysis.materiality_benchmark import materiality_candidates, methodology_references

    candidates = materiality_candidates({
        "profit_before_tax": {"selected_amount": Decimal("100"), "selected_year": 2025, "stability": "observed", "role": "primary_candidate"},
        "revenue": {"selected_amount": Decimal("100"), "selected_year": 2025, "stability": "observed", "role": "primary_candidate"},
    })
    by_key = {row["benchmark_key"]: row for row in candidates}
    assert by_key["profit_before_tax"]["rate_reference_ids"]["central"] == ["isa_320_a8_pbt_illustration"]
    assert all("isa_320_a8" not in ref for refs in by_key["revenue"]["rate_reference_ids"].values() for ref in refs)
    assert next(item for item in methodology_references() if item["reference_id"] == "materiality_candidate_ranges_v1")["authority_level"] == "internal_methodology"


def test_methodology_uses_verified_asa_link_and_non_url_internal_locator():
    """Public methodology links must be verified URLs; internal rules use stable locators."""
    from kreports.analysis.materiality_benchmark import METHODOLOGY_VERSION, methodology_references

    references = {item["reference_id"]: item for item in methodology_references()}
    isa = references["isa_320_a8_pbt_illustration"]
    internal = references["materiality_candidate_ranges_v1"]

    assert isa["issuer"] == "AUASB"
    assert isa["standard_code"] == "ASA 320 (conforms with ISA 320)"
    assert isa["official_url"] == "https://standards.auasb.gov.au/asa-320-dec-2015"
    assert "official_url" not in internal
    assert internal["source_locator"] == "docs/data-contract.md#audit-materiality-preparation"
    assert internal["methodology_version"] == METHODOLOGY_VERSION
