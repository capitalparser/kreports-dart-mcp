"""Regression contracts for auditor materiality preparation.

Each test names the production break it catches before exercising the public
calculation boundary with literal, source-backed fixtures.
"""
from __future__ import annotations

from decimal import Decimal
import json


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
        {"bsns_year": 2024, "fs_div": "CFS", "metric_key": "profit_loss", "amount": 80, "unit": "KRW", "period_type": "duration", "quality_status": "usable", "citation_rcept_no": "20250318000001"},
        {"bsns_year": 2024, "fs_div": "CFS", "metric_key": "tax_expense", "amount": 20, "unit": "KRW", "period_type": "duration", "quality_status": "usable", "citation_rcept_no": "20250318000001"},
        {"bsns_year": 2023, "fs_div": "OFS", "metric_key": "profit_loss", "amount": 70, "unit": "KRW", "period_type": "duration", "quality_status": "usable", "citation_rcept_no": "20240318000001"},
        {"bsns_year": 2023, "fs_div": "CFS", "metric_key": "tax_expense", "amount": 10, "unit": "KRW", "period_type": "duration", "quality_status": "usable", "citation_rcept_no": "20240318000001"},
    ]

    series = build_benchmark_series(compatible, years=[2024, 2023], fs_div="CFS")

    derived = series["profit_before_tax"][0]
    incompatible = series["profit_before_tax"][1]
    assert derived["amount"] == Decimal("100")
    assert derived["basis"] == "derived_profit_loss_plus_tax_expense"
    assert {source["rcept_no"] for source in derived["sources"]} == {"20250318000001"}
    assert incompatible["amount"] is None
    assert "incompatible_operands" in incompatible["limitations"]


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
    assert row["authority_levels"] == ["standard_illustration", "practice_observation"]
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
            for key, amount, period in (
                ("profit_before_tax", pbt, "duration"),
                ("revenue", revenue, "duration"),
                ("assets", assets, "instant"),
                ("equity", equity, "instant"),
            ):
                session.execute(text("""
                    INSERT INTO financial_facts_compact
                    (corp_code, bsns_year, fs_div, metric_key, metric_name, amount,
                     unit, period_type, citation_rcept_no, citation_report_nm,
                     quality_status, fetched_at)
                    VALUES ('00126380', :year, 'CFS', :key, :key, :amount,
                            'KRW', :period, :receipt, :report_nm,
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
