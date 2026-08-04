import hashlib
import json
from datetime import UTC, date, datetime

import pytest
from typer.testing import CliRunner

from kreports.db.engine import get_session
from kreports.db.migrations import MIGRATIONS, apply_schema_migrations
from kreports.db.models import (
    Company,
    CompanyYearQuality,
    DatasetManifest,
    Disclosure,
    FinancialFactCompact,
)
from kreports.quality.company_year_fingerprint import (
    build_quality_evidence_summary,
    quality_input_fingerprint,
)

_QUALITY_CONTENT_FIELDS = (
    "corp_code",
    "bsns_year",
    "market",
    "financial_core_status",
    "auditor_status",
    "audit_fee_status",
    "policy_status",
    "kam_status",
    "audit_procedure_status",
    "group_audit_status",
    "investor_grade",
    "auditor_grade",
    "group_audit_grade",
    "blockers_json",
    "quality_version",
    "input_fingerprint",
    "evidence_summary_json",
)


def _expected_quality_digest(rows: list[CompanyYearQuality]) -> str:
    ordered = sorted(
        (
            {
                field: (
                    sorted(json.loads(getattr(row, field)))
                    if field == "blockers_json"
                    else json.loads(getattr(row, field))
                    if field == "evidence_summary_json"
                    else getattr(row, field)
                )
                for field in _QUALITY_CONTENT_FIELDS
            }
            for row in rows
        ),
        key=lambda row: (row["corp_code"], row["bsns_year"]),
    )
    payload = json.dumps(
        ordered,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _seed_valid_manifest(temp_engine, *, year: int = 2025) -> None:
    from kreports.db.engine import write_dataset_manifest

    with temp_engine.begin() as connection:
        apply_schema_migrations(connection)
    result = write_dataset_manifest("release-v1")
    assert result["year_to"] in {None, year}


def _quality_freshness_fields(
    *,
    investor_grade: str = "A",
    policy_status: str = "full_body",
    procedure_status: str = "available",
    kam_status: str = "full_body",
) -> dict[str, str]:
    summary = build_quality_evidence_summary(
        statuses={
            "financial_core": "available",
            "auditor": "available",
            "audit_fee": "available",
            "policy": policy_status,
            "kam": kam_status,
            "audit_procedure": procedure_status,
            "group_audit": "missing",
        },
        grades={
            "investor_core": investor_grade,
            "auditor_full": "A",
            "group_audit": "D",
        },
        blockers=(),
        quality_version="v1",
    )
    return {
        "input_fingerprint": quality_input_fingerprint(summary),
        "evidence_summary_json": json.dumps(
            summary,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    }


def _seed_quality_row(
    *,
    corp_code: str,
    grade: str,
    market: str = "KOSPI",
    stock_code: str | None = "000001",
    policy_status: str = "full_body",
    procedure_status: str = "available",
    kam_status: str = "full_body",
) -> None:
    with get_session() as session:
        session.add(
            Company(
                corp_code=corp_code,
                stock_code=stock_code,
                corp_name=f"회사-{corp_code}",
                market=market,
            )
        )
        session.add(
            CompanyYearQuality(
                corp_code=corp_code,
                bsns_year=2025,
                market=market,
                financial_core_status="available",
                auditor_status="available",
                audit_fee_status="available",
                policy_status=policy_status,
                kam_status=kam_status,
                audit_procedure_status=procedure_status,
                group_audit_status="missing",
                investor_grade=grade,
                auditor_grade="A",
                group_audit_grade="D",
                blockers_json="[]",
                quality_version="v1",
                **_quality_freshness_fields(
                    investor_grade=grade,
                    policy_status=policy_status,
                    procedure_status=procedure_status,
                    kam_status=kam_status,
                ),
                updated_at=datetime.now(UTC),
            )
        )


def _seed_materiality_fact_years(
    corp_code: str,
    years: tuple[int, ...],
    *,
    fact_receipt: str | None = None,
    citation_basis: str = "company_year_annual_filing_match",
    unit: str = "KRW",
) -> None:
    """Seed literal compact facts whose receipts prove each annual filing."""
    with get_session() as session:
        for year in years:
            receipt = f"{year + 1}0331{int(corp_code):06d}"
            session.add(
                Disclosure(
                    rcept_no=receipt,
                    corp_code=corp_code,
                    corp_name=f"회사-{corp_code}",
                    disc_date=date(year + 1, 3, 31),
                    disc_type="A",
                    report_nm=f"사업보고서 ({year}.12)",
                )
            )
            session.add(
                FinancialFactCompact(
                    corp_code=corp_code,
                    bsns_year=year,
                    fs_div="CFS",
                    metric_key="revenue",
                    metric_name="매출액",
                    amount=100_000_000,
                    source_account_id="ifrs-full_Revenue",
                    source_table="financial_facts",
                    unit=unit,
                    period_type="duration",
                    citation_rcept_no=fact_receipt or receipt,
                    citation_report_nm=f"사업보고서 ({year}.12)",
                    citation_basis=citation_basis,
                    quality_status="usable",
                )
            )


def _replace_compact_table_without_unique(temp_engine) -> None:
    """Allow literal duplicate fixtures while retaining the final columns."""
    with temp_engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE financial_facts_compact")
        connection.exec_driver_sql(
            """
            CREATE TABLE financial_facts_compact (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                corp_code VARCHAR(8) NOT NULL,
                bsns_year SMALLINT NOT NULL,
                fs_div VARCHAR(3) NOT NULL,
                metric_key VARCHAR(50) NOT NULL,
                metric_name VARCHAR(200) NOT NULL,
                amount,
                source_account_id VARCHAR(200),
                source_account_nm VARCHAR(300),
                source_table VARCHAR(40),
                unit VARCHAR(30),
                period_type VARCHAR(20),
                citation_rcept_no VARCHAR(80),
                citation_report_nm VARCHAR(300),
                citation_basis VARCHAR(50) NOT NULL DEFAULT 'uncitable',
                quality_status VARCHAR(24) NOT NULL DEFAULT 'limited',
                fetched_at DATETIME NOT NULL
            )
            """
        )


def _seed_duplicate_compact_fact(
    corp_code: str,
    year: int,
    *,
    metric_key: str = "revenue",
    amount=100_000_000,
    source_account_id: str = "ifrs-full_Revenue",
    unit: str = "KRW",
) -> None:
    receipt = f"{year + 1}0331{int(corp_code):06d}"
    with get_session() as session:
        session.add(
            FinancialFactCompact(
                corp_code=corp_code,
                bsns_year=year,
                fs_div="CFS",
                metric_key=metric_key,
                metric_name=metric_key,
                amount=amount,
                source_account_id=source_account_id,
                source_table="financial_facts",
                unit=unit,
                period_type="duration",
                citation_rcept_no=receipt,
                citation_report_nm=f"사업보고서 ({year}.12)",
                citation_basis="company_year_annual_filing_match",
                quality_status="usable",
            )
        )


def _seed_derived_pbt_years(
    corp_code: str,
    years: tuple[int, ...],
    *,
    tax_unit: str = "KRW",
) -> None:
    with get_session() as session:
        for year in years:
            receipt = f"{year + 1}0331{int(corp_code):06d}"
            report_name = f"사업보고서 ({year}.12)"
            session.add(
                Disclosure(
                    rcept_no=receipt,
                    corp_code=corp_code,
                    corp_name=f"회사-{corp_code}",
                    disc_date=date(year + 1, 3, 31),
                    disc_type="A",
                    report_nm=report_name,
                )
            )
            for metric_key, amount, account_id, unit in (
                ("profit_loss", 80_000_000, "ifrs-full_ProfitLoss", "KRW"),
                (
                    "tax_expense",
                    20_000_000,
                    "ifrs-full_IncomeTaxExpenseContinuingOperations",
                    tax_unit,
                ),
            ):
                session.add(
                    FinancialFactCompact(
                        corp_code=corp_code,
                        bsns_year=year,
                        fs_div="CFS",
                        metric_key=metric_key,
                        metric_name=metric_key,
                        amount=amount,
                        source_account_id=account_id,
                        source_table="financial_facts",
                        unit=unit,
                        period_type="duration",
                        citation_rcept_no=receipt,
                        citation_report_nm=report_name,
                        citation_basis="company_year_annual_filing_match",
                        quality_status="usable",
                    )
                )



def test_public_runtime_accepts_exact_95_percent_with_exact_denominator(
    temp_engine,
    monkeypatch,
):
    from kreports.quality import release_gate

    for index in range(20):
        _seed_quality_row(
            corp_code=f"{index + 1:08d}",
            grade="A" if index < 19 else "D",
            stock_code=f"{index + 1:06d}",
        )
    _seed_quality_row(
        corp_code="90000001",
        grade="D",
        stock_code=None,
    )
    _seed_quality_row(
        corp_code="90000002",
        grade="D",
        market="KONEX",
        stock_code="900002",
    )
    _seed_valid_manifest(temp_engine)
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    report = release_gate.evaluate_release_gate("public_runtime")

    assert report["ok"] is True
    assert report["tool_count"] == 34
    assert report["denominators"]["investor_core"] == 20
    assert report["coverage"]["investor_core"] == {
        "numerator": 19,
        "denominator": 20,
        "coverage_pct": 95.0,
        "threshold_pct": 95.0,
    }
    assert report["excluded_populations"]["investor_core"] == {
        "not_listed": 1,
        "outside_core_markets": 1,
    }
    assert "investor_core_coverage" not in report["required_failures"]

    monkeypatch.setattr(release_gate, "_tool_count", lambda: 32)
    mismatch = release_gate.evaluate_release_gate("public_runtime")
    assert mismatch["ok"] is False
    assert "unexpected_tool_count" in mismatch["required_failures"]


def test_auditor_full_promotes_optional_policy_and_procedure_gaps(
    temp_engine,
    monkeypatch,
):
    from kreports.quality.release_gate import evaluate_release_gate

    for index in range(20):
        _seed_quality_row(
            corp_code=f"{index + 1:08d}",
            grade="A",
            stock_code=f"{index + 1:06d}",
            policy_status="missing" if index < 2 else "full_body",
            procedure_status="missing" if index < 2 else "available",
        )
        _seed_materiality_fact_years(
            f"{index + 1:08d}",
            (2023, 2024, 2025),
        )
    _seed_valid_manifest(temp_engine)
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    public = evaluate_release_gate("public_runtime")
    auditor = evaluate_release_gate("auditor_full")

    assert public["ok"] is True
    assert public["required_failures"] == []
    assert public["degraded_features"] == [
        "accounting_policy",
        "audit_procedure",
    ]
    assert auditor["ok"] is False
    assert auditor["required_failures"] == [
        "accounting_policy_coverage",
        "audit_procedure_coverage",
    ]


def test_zero_materiality_benchmark_coverage_degrades_public_and_blocks_auditor_full(
    temp_engine,
    monkeypatch,
):
    """Catch an auditor-full pass when no proven three-year benchmark exists."""
    from kreports.quality.release_gate import evaluate_release_gate

    _seed_quality_row(corp_code="00126380", grade="A", stock_code="005930")
    _seed_valid_manifest(temp_engine)
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    public = evaluate_release_gate("public_runtime")
    auditor = evaluate_release_gate("auditor_full")

    assert public["ok"] is True
    assert public["degraded_features"] == ["materiality_benchmark"]
    assert auditor["ok"] is False
    assert auditor["required_failures"] == [
        "materiality_benchmark_coverage"
    ]
    assert auditor["coverage"]["materiality_benchmark"] == {
        "numerator": 0,
        "denominator": 1,
        "coverage_pct": 0.0,
        "threshold_pct": 95.0,
    }
    assert auditor["excluded_populations"]["materiality_benchmark"] == {
        "not_listed": 0,
        "outside_core_markets": 0,
        "zero_proven_years": 1,
        "one_proven_year": 0,
        "two_proven_years": 0,
    }


def test_two_proven_years_are_partial_support_not_materiality_coverage(
    temp_engine,
    monkeypatch,
):
    """Catch a metric that counts two years as enough for stability coverage."""
    from kreports.quality.release_gate import evaluate_release_gate

    _seed_quality_row(corp_code="00126380", grade="A", stock_code="005930")
    _seed_materiality_fact_years("00126380", (2024, 2025))
    _seed_valid_manifest(temp_engine)
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    report = evaluate_release_gate("auditor_full")

    assert "materiality_benchmark_coverage" in report["required_failures"]
    assert report["coverage"]["materiality_benchmark"]["numerator"] == 0
    assert report["excluded_populations"]["materiality_benchmark"][
        "two_proven_years"
    ] == 1


def test_three_exact_proven_years_pass_materiality_benchmark_coverage(
    temp_engine,
    monkeypatch,
):
    """Catch a release gate that ignores a complete exact annual-filing series."""
    from kreports.quality.release_gate import evaluate_release_gate

    _seed_quality_row(corp_code="00126380", grade="A", stock_code="005930")
    _seed_materiality_fact_years("00126380", (2023, 2024, 2025))
    _seed_valid_manifest(temp_engine)
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    report = evaluate_release_gate("auditor_full")

    assert report["ok"] is True
    assert report["coverage"]["materiality_benchmark"] == {
        "numerator": 1,
        "denominator": 1,
        "coverage_pct": 100.0,
        "threshold_pct": 95.0,
    }
    assert report["coverage_metadata"]["materiality_benchmark"] == {
        "window_years": 3,
        "metric_keys": [
            "profit_before_tax",
            "revenue",
            "assets",
            "equity",
        ],
        "fs_div_policy": "one_of_CFS_or_OFS_per_metric",
        "unit": "KRW",
        "citation_basis": "company_year_annual_filing_match",
        "receipt_policy": "exact_canonical_company_year_annual_filing",
        "annual_source_policy": "latest_company_year_fs_annual_filing",
        "duplicate_policy": "value_and_provenance_identical_only",
        "amount_policy": "finite_sqlite_integer_or_real",
        "pbt_policy": "direct_or_profit_loss_plus_tax_expense",
    }


@pytest.mark.parametrize(
    ("fact_receipt", "citation_basis", "unit"),
    [
        ("20260331000999", "company_year_annual_filing_match", "KRW"),
        (None, "uncitable", "KRW"),
        (None, "company_year_annual_filing_match", "million_KRW"),
    ],
    ids=("wrong_receipt", "wrong_basis", "wrong_unit"),
)
def test_unproven_materiality_facts_do_not_count_toward_auditor_coverage(
    temp_engine,
    monkeypatch,
    fact_receipt,
    citation_basis,
    unit,
):
    """Catch coverage that accepts forged, uncitable, or non-KRW facts."""
    from kreports.quality.release_gate import evaluate_release_gate

    _seed_quality_row(corp_code="00126380", grade="A", stock_code="005930")
    _seed_materiality_fact_years(
        "00126380",
        (2023, 2024, 2025),
        fact_receipt=fact_receipt,
        citation_basis=citation_basis,
        unit=unit,
    )
    _seed_valid_manifest(temp_engine)
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    report = evaluate_release_gate("auditor_full")

    assert "materiality_benchmark_coverage" in report["required_failures"]
    assert report["coverage"]["materiality_benchmark"]["numerator"] == 0


@pytest.mark.parametrize(
    ("amount", "source_account_id"),
    [
        (100_000_001, "ifrs-full_Revenue"),
        (100_000_000, "dart_Revenue"),
    ],
    ids=("conflicting_value", "conflicting_provenance"),
)
def test_conflicting_compact_duplicates_invalidate_exact_materiality_year(
    temp_engine,
    monkeypatch,
    amount,
    source_account_id,
):
    """Catch coverage that hides a conflicting compact value or provenance."""
    from kreports.quality.release_gate import evaluate_release_gate

    _replace_compact_table_without_unique(temp_engine)
    _seed_quality_row(corp_code="00126380", grade="A", stock_code="005930")
    _seed_materiality_fact_years("00126380", (2023, 2024, 2025))
    _seed_duplicate_compact_fact(
        "00126380",
        2025,
        amount=amount,
        source_account_id=source_account_id,
    )
    _seed_valid_manifest(temp_engine)
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    report = evaluate_release_gate("auditor_full")

    assert "materiality_benchmark_coverage" in report["required_failures"]
    assert report["excluded_populations"]["materiality_benchmark"][
        "two_proven_years"
    ] == 1


def test_value_and_provenance_identical_compact_duplicates_dedupe(
    temp_engine,
    monkeypatch,
):
    """Catch a gate that rejects byte-equivalent duplicate compact evidence."""
    from kreports.quality.release_gate import evaluate_release_gate

    _replace_compact_table_without_unique(temp_engine)
    _seed_quality_row(corp_code="00126380", grade="A", stock_code="005930")
    _seed_materiality_fact_years("00126380", (2023, 2024, 2025))
    _seed_duplicate_compact_fact("00126380", 2025)
    _seed_valid_manifest(temp_engine)
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    report = evaluate_release_gate("auditor_full")

    assert report["ok"] is True
    assert report["coverage"]["materiality_benchmark"]["numerator"] == 1


@pytest.mark.parametrize(
    "invalid_amount",
    ["not-a-number", float("inf")],
    ids=("sqlite_text", "sqlite_infinity"),
)
def test_nonnumeric_or_nonfinite_compact_amount_is_not_materiality_coverage(
    temp_engine,
    monkeypatch,
    invalid_amount,
):
    """Catch SQLite affinity coercion of text or infinity into a benchmark."""
    from kreports.quality.release_gate import evaluate_release_gate

    _seed_quality_row(corp_code="00126380", grade="A", stock_code="005930")
    _seed_materiality_fact_years("00126380", (2023, 2024, 2025))
    with temp_engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE financial_facts_compact SET amount=? "
            "WHERE corp_code=? AND bsns_year=2025 AND metric_key='revenue'",
            (invalid_amount, "00126380"),
        )
    _seed_valid_manifest(temp_engine)
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    report = evaluate_release_gate("auditor_full")

    assert "materiality_benchmark_coverage" in report["required_failures"]
    assert report["excluded_populations"]["materiality_benchmark"][
        "two_proven_years"
    ] == 1


def test_only_latest_company_year_annual_filing_can_prove_materiality(
    temp_engine,
    monkeypatch,
):
    """Catch coverage that accepts an older annual filing after a newer one."""
    from kreports.quality.release_gate import evaluate_release_gate

    _seed_quality_row(corp_code="00126380", grade="A", stock_code="005930")
    _seed_materiality_fact_years("00126380", (2023, 2024, 2025))
    with get_session() as session:
        for year in (2023, 2024, 2025):
            session.add(
                Disclosure(
                    rcept_no=f"{year + 1}0430{int('00126380'):06d}",
                    corp_code="00126380",
                    corp_name="회사-00126380",
                    disc_date=date(year + 1, 4, 30),
                    disc_type="A",
                    report_nm=f"사업보고서 ({year}.12)",
                )
            )
    _seed_valid_manifest(temp_engine)
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    report = evaluate_release_gate("auditor_full")

    assert "materiality_benchmark_coverage" in report["required_failures"]
    assert report["excluded_populations"]["materiality_benchmark"][
        "zero_proven_years"
    ] == 1


@pytest.mark.parametrize(
    "receipt_template",
    (
        "{receipt}-attachment",
        "attachment-{receipt}",
        " {receipt} ",
    ),
    ids=("suffix", "prefix", "whitespace"),
)
def test_contaminated_latest_annual_disclosure_cannot_borrow_older_materiality_source(
    temp_engine,
    monkeypatch,
    receipt_template,
):
    """Latest malformed disclosure rows must close, not revive older proof."""
    from kreports.quality.release_gate import evaluate_release_gate

    _seed_quality_row(corp_code="00126380", grade="A", stock_code="005930")
    _seed_materiality_fact_years("00126380", (2023, 2024, 2025))
    with get_session() as session:
        for year in (2023, 2024, 2025):
            receipt = f"{year + 1}0331{int('00126380'):06d}"
            session.add(Disclosure(
                rcept_no=receipt_template.format(receipt=receipt),
                corp_code="00126380",
                corp_name="회사-00126380",
                disc_date=date(year + 1, 4, 30),
                disc_type="A",
                report_nm=f"사업보고서 ({year}.12) [정정]",
            ))
    _seed_valid_manifest(temp_engine)
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    report = evaluate_release_gate("auditor_full")

    assert "materiality_benchmark_coverage" in report["required_failures"]
    assert report["coverage"]["materiality_benchmark"]["numerator"] == 0


def test_implausibly_late_receipt_date_cannot_prove_materiality(
    temp_engine,
    monkeypatch,
):
    """Catch a receipt outside production's company-year plausibility range."""
    from kreports.quality.release_gate import evaluate_release_gate

    _seed_quality_row(corp_code="00126380", grade="A", stock_code="005930")
    _seed_materiality_fact_years("00126380", (2023, 2024, 2025))
    with temp_engine.begin() as connection:
        for year in (2023, 2024, 2025):
            old_receipt = f"{year + 1}0331{int('00126380'):06d}"
            late_receipt = f"{year + 11}0331{int('00126380'):06d}"
            connection.exec_driver_sql(
                "UPDATE disclosures SET rcept_no=?, disc_date=? "
                "WHERE rcept_no=?",
                (late_receipt, f"{year + 11}-03-31", old_receipt),
            )
            connection.exec_driver_sql(
                "UPDATE financial_facts_compact "
                "SET citation_rcept_no=? WHERE citation_rcept_no=?",
                (late_receipt, old_receipt),
            )
    _seed_valid_manifest(temp_engine)
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    report = evaluate_release_gate("auditor_full")

    assert "materiality_benchmark_coverage" in report["required_failures"]


def test_proven_compatible_derived_pbt_three_years_count_as_coverage(
    temp_engine,
    monkeypatch,
):
    """Catch readiness that omits production's compatible PBT derivation."""
    from kreports.quality.release_gate import evaluate_release_gate

    _seed_quality_row(corp_code="00126380", grade="A", stock_code="005930")
    _seed_derived_pbt_years("00126380", (2023, 2024, 2025))
    _seed_valid_manifest(temp_engine)
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    report = evaluate_release_gate("auditor_full")

    assert report["ok"] is True
    assert report["coverage"]["materiality_benchmark"]["numerator"] == 1


def test_bad_derived_pbt_operand_does_not_count_as_materiality_coverage(
    temp_engine,
    monkeypatch,
):
    """Catch a derived PBT series that accepts a non-KRW tax operand."""
    from kreports.quality.release_gate import evaluate_release_gate

    _seed_quality_row(corp_code="00126380", grade="A", stock_code="005930")
    _seed_derived_pbt_years(
        "00126380",
        (2023, 2024, 2025),
        tax_unit="million_KRW",
    )
    _seed_valid_manifest(temp_engine)
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    report = evaluate_release_gate("auditor_full")

    assert "materiality_benchmark_coverage" in report["required_failures"]


def test_conflicting_derived_pbt_operand_invalidates_only_that_year(
    temp_engine,
    monkeypatch,
):
    """Catch derivation that chooses one of two conflicting tax operands."""
    from kreports.quality.release_gate import evaluate_release_gate

    _replace_compact_table_without_unique(temp_engine)
    _seed_quality_row(corp_code="00126380", grade="A", stock_code="005930")
    _seed_derived_pbt_years("00126380", (2023, 2024, 2025))
    _seed_duplicate_compact_fact(
        "00126380",
        2025,
        metric_key="tax_expense",
        amount=21_000_000,
        source_account_id="ifrs-full_IncomeTaxExpenseContinuingOperations",
    )
    _seed_valid_manifest(temp_engine)
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    report = evaluate_release_gate("auditor_full")

    assert "materiality_benchmark_coverage" in report["required_failures"]
    assert report["excluded_populations"]["materiality_benchmark"][
        "two_proven_years"
    ] == 1


def test_missing_compact_schema_fails_materiality_coverage_closed(
    temp_engine,
    monkeypatch,
):
    """Catch a missing compact table that disappears from auditor readiness."""
    from kreports.quality.release_gate import evaluate_release_gate

    _seed_quality_row(corp_code="00126380", grade="A", stock_code="005930")
    _seed_valid_manifest(temp_engine)
    with temp_engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE financial_facts_compact")
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    report = evaluate_release_gate("auditor_full")

    assert "missing_table:financial_facts_compact" in report["required_failures"]
    assert "materiality_benchmark_coverage" in report["required_failures"]
    assert report["coverage"]["materiality_benchmark"]["denominator"] == 1


def test_explicit_no_kam_is_excluded_from_procedure_denominator(
    temp_engine,
    monkeypatch,
):
    from kreports.quality.release_gate import evaluate_release_gate

    for index in range(20):
        no_kam = index >= 18
        _seed_quality_row(
            corp_code=f"{index + 1:08d}",
            grade="A",
            stock_code=f"{index + 1:06d}",
            procedure_status=(
                "not_applicable" if no_kam else "available"
            ),
            kam_status="explicit_no_kam" if no_kam else "full_body",
        )
        _seed_materiality_fact_years(
            f"{index + 1:08d}",
            (2023, 2024, 2025),
        )
    _seed_valid_manifest(temp_engine)
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    report = evaluate_release_gate("auditor_full")

    assert report["ok"] is True
    assert report["coverage"]["audit_procedure"] == {
        "numerator": 18,
        "denominator": 18,
        "coverage_pct": 100.0,
        "threshold_pct": 95.0,
    }
    assert report["excluded_populations"]["audit_procedure"][
        "explicit_no_kam"
    ] == 2


def test_invalid_manifest_fails_closed(temp_engine, monkeypatch):
    from kreports.quality.release_gate import evaluate_release_gate

    with temp_engine.begin() as connection:
        apply_schema_migrations(connection)
    with get_session() as session:
        session.add(
            DatasetManifest(
                manifest_id="manifest-id",
                schema_version=MIGRATIONS[-1].revision,
                dataset_version="different-version",
                generated_at=datetime.now(UTC),
                year_from=2025,
                year_to=2025,
                company_count=0,
                disclosure_count=0,
                evidence_document_count=0,
                quality_snapshot_json="{}",
            )
        )
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    report = evaluate_release_gate("public_runtime")

    assert report["schema_version"] == "unknown"
    assert report["dataset_version"] == "unknown"
    assert "release_manifest_unavailable" in report["required_failures"]


def test_empty_manifest_and_quality_ledger_fail_closed_with_actionable_guidance(
    temp_engine,
    monkeypatch,
):
    from kreports.quality.release_gate import evaluate_release_gate

    with temp_engine.begin() as connection:
        apply_schema_migrations(connection)
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    report = evaluate_release_gate("public_runtime")

    assert report["ok"] is False
    assert report["required_failures"] == [
        "investor_core_coverage",
        "release_manifest_unavailable",
    ]
    guidance = {
        item["blocker"]: item for item in report["blocker_guidance"]
    }
    assert guidance["release_manifest_unavailable"] == {
        "blocker": "release_manifest_unavailable",
        "owner": "dataset_release_maintainer",
        "action": "write a validated dataset manifest from the prepared runtime DB",
    }
    assert guidance["investor_core_coverage"] == {
        "blocker": "investor_core_coverage",
        "owner": "dataset_backfill_maintainer",
        "action": "backfill and validate investor-core company-year coverage before release",
    }


def test_release_gate_is_read_only_and_does_not_require_dart_key(
    temp_engine,
    monkeypatch,
):
    from sqlalchemy import event

    import kreports.db.engine as engine_module
    from kreports.quality.release_gate import evaluate_release_gate

    _seed_quality_row(corp_code="00126380", grade="A", stock_code="005930")
    _seed_valid_manifest(temp_engine)
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")
    monkeypatch.delenv("DART_API_KEY", raising=False)
    monkeypatch.setattr(
        engine_module,
        "init_db",
        lambda: (_ for _ in ()).throw(
            AssertionError("release gate must not initialize schema")
        ),
    )
    statements: list[str] = []

    def capture_sql(
        _conn,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ):
        statements.append(str(statement))

    event.listen(temp_engine, "before_cursor_execute", capture_sql)
    try:
        report = evaluate_release_gate("public_runtime")
    finally:
        event.remove(temp_engine, "before_cursor_execute", capture_sql)

    assert report["ok"] is True
    assert statements
    assert all(
        statement.lstrip().upper().startswith(("SELECT", "WITH", "PRAGMA"))
        for statement in statements
    )


def test_quality_release_gate_cli_supports_json_and_human_output(monkeypatch):
    from kreports.cli.main import app
    from kreports.quality import release_gate

    report = {
        "ok": False,
        "profile": "auditor_full",
        "schema_version": MIGRATIONS[-1].revision,
        "dataset_version": "release-v1",
        "required_failures": ["audit_procedure_coverage"],
        "degraded_features": ["audit_procedure"],
        "tool_count": 34,
        "coverage_year": 2025,
        "coverage": {
            "audit_procedure": {
                "numerator": 18,
                "denominator": 20,
                "coverage_pct": 90.0,
                "threshold_pct": 95.0,
            }
        },
        "coverage_metadata": {
            "materiality_benchmark": {
                "window_years": 3,
                "receipt_policy": "exact_canonical_company_year_annual_filing",
            }
        },
        "denominators": {"audit_procedure": 20},
        "excluded_populations": {
            "audit_procedure": {
                "not_listed": 2,
                "outside_core_markets": 1,
                "explicit_no_kam": 3,
            }
        },
    }
    monkeypatch.setattr(release_gate, "evaluate_release_gate", lambda _profile: report)
    runner = CliRunner()

    json_result = runner.invoke(
        app,
        ["quality-release-gate", "--profile", "auditor_full", "--json"],
    )
    human_result = runner.invoke(
        app,
        ["quality-release-gate", "--profile", "auditor_full"],
    )

    assert json_result.exit_code == 1
    assert json.loads(json_result.stdout) == report
    assert human_result.exit_code == 1
    assert "Required failures: audit_procedure_coverage" in human_result.stdout
    assert "Degraded features: audit_procedure" in human_result.stdout
    assert "audit_procedure: 18/20 (90.0%, threshold 95.0%)" in human_result.stdout
    assert (
        "audit_procedure: explicit_no_kam=3, not_listed=2, "
        "outside_core_markets=1"
    ) in human_result.stdout
    assert (
        "materiality_benchmark: "
        "{\"receipt_policy\": \"exact_canonical_company_year_annual_filing\", "
        "\"window_years\": 3}"
    ) in human_result.stdout


def test_rebuild_company_year_quality_cli_supports_json_and_human(
    monkeypatch,
):
    from kreports.cli import main as cli_main
    from kreports.quality import company_year as company_year_module

    result = {
        "year_from": 2024,
        "year_to": 2025,
        "market": "KOSPI",
        "companies_evaluated": 2,
        "rows_written": 4,
        "quality_version": "v1",
    }
    monkeypatch.setattr(cli_main, "init_db", lambda: None)
    monkeypatch.setattr(
        company_year_module,
        "rebuild_company_year_quality",
        lambda **_kwargs: result,
    )
    runner = CliRunner()

    json_result = runner.invoke(
        cli_main.app,
        [
            "rebuild-company-year-quality",
            "--year-from",
            "2024",
            "--year-to",
            "2025",
            "--market",
            "KOSPI",
            "--json",
        ],
    )
    human_result = runner.invoke(
        cli_main.app,
        [
            "rebuild-company-year-quality",
            "--year-from",
            "2024",
            "--year-to",
            "2025",
            "--market",
            "KOSPI",
        ],
    )

    assert json_result.exit_code == 0
    assert json.loads(json_result.stdout) == result
    assert human_result.exit_code == 0
    assert "Rows written: 4" in human_result.stdout
    assert "market=KOSPI" in human_result.stdout


def test_release_gate_rejects_recorded_migration_checksum_mismatch(
    temp_engine,
    monkeypatch,
):
    from sqlalchemy import text

    from kreports.quality.release_gate import evaluate_release_gate

    _seed_quality_row(
        corp_code="00126380",
        grade="A",
        stock_code="005930",
    )
    _seed_valid_manifest(temp_engine)
    with temp_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE schema_migrations SET checksum=:checksum "
                "WHERE revision=:revision"
            ),
            {
                "checksum": "0" * 64,
                "revision": MIGRATIONS[0].revision,
            },
        )
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    report = evaluate_release_gate("public_runtime")

    assert "schema_migration_contract_mismatch" in report[
        "required_failures"
    ]
    assert "release_manifest_unavailable" in report["required_failures"]


@pytest.mark.parametrize(
    "count_dimension",
    ["company", "disclosure", "evidence_document"],
)
def test_release_gate_rejects_manifest_live_count_mismatch(
    temp_engine,
    monkeypatch,
    count_dimension,
):
    from kreports.quality.release_gate import evaluate_release_gate
    from tests.factories import disclosure_factory, evidence_document_factory

    _seed_quality_row(
        corp_code="00126380",
        grade="A",
        stock_code="005930",
    )
    _seed_valid_manifest(temp_engine)
    with get_session() as session:
        if count_dimension == "company":
            session.add(
                Company(
                    corp_code="00999999",
                    stock_code="999999",
                    corp_name="추가회사",
                    market="KOSPI",
                )
            )
        elif count_dimension == "disclosure":
            session.add(
                disclosure_factory(
                    rcept_no="20250318000999",
                    corp_code="00126380",
                )
            )
        else:
            session.add(
                evidence_document_factory(
                    corp_code="00126380",
                    bsns_year=2025,
                    rcept_no="20250318000999",
                )
            )
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    report = evaluate_release_gate("public_runtime")

    assert "release_manifest_counts_mismatch" in report[
        "required_failures"
    ]
    assert "release_manifest_unavailable" in report["required_failures"]


def test_release_gate_rejects_quality_snapshot_version_mismatch(
    temp_engine,
    monkeypatch,
):
    from kreports.quality.release_gate import evaluate_release_gate

    _seed_quality_row(
        corp_code="00126380",
        grade="A",
        stock_code="005930",
    )
    _seed_valid_manifest(temp_engine)
    with get_session() as session:
        row = session.get(CompanyYearQuality, ("00126380", 2025))
        assert row is not None
        row.quality_version = "v2"
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    report = evaluate_release_gate("public_runtime")

    assert "quality_snapshot_mismatch" in report["required_failures"]
    assert "release_manifest_unavailable" in report["required_failures"]


@pytest.mark.parametrize(
    ("field_name", "tampered_value"),
    [
        ("investor_grade", "D"),
        ("kam_status", "summary_only"),
    ],
)
def test_release_gate_rejects_quality_content_tamper_without_count_change(
    temp_engine,
    monkeypatch,
    field_name,
    tampered_value,
):
    from kreports.quality.release_gate import evaluate_release_gate

    _seed_quality_row(
        corp_code="00126380",
        grade="A",
        stock_code="005930",
    )
    _seed_valid_manifest(temp_engine)
    with get_session() as session:
        row = session.get(CompanyYearQuality, ("00126380", 2025))
        assert row is not None
        setattr(row, field_name, tampered_value)
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    report = evaluate_release_gate("public_runtime")

    assert "quality_snapshot_mismatch" in report["required_failures"]
    assert "release_manifest_unavailable" in report["required_failures"]


def test_release_gate_rejects_matching_snapshot_for_unsupported_version(
    temp_engine,
    monkeypatch,
):
    from kreports.quality.release_gate import evaluate_release_gate

    _seed_quality_row(
        corp_code="00126380",
        grade="A",
        stock_code="005930",
    )
    _seed_valid_manifest(temp_engine)
    with get_session() as session:
        quality_row = session.get(
            CompanyYearQuality,
            ("00126380", 2025),
        )
        manifest = session.get(DatasetManifest, "release-v1")
        assert quality_row is not None
        assert manifest is not None
        quality_row.quality_version = "v2"
        session.flush()
        snapshot = json.loads(manifest.quality_snapshot_json)
        snapshot["quality_version"] = "v2"
        snapshot["content_digest"] = _expected_quality_digest(
            [quality_row]
        )
        manifest.quality_snapshot_json = json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
        )
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    report = evaluate_release_gate("public_runtime")

    assert "quality_version_unsupported" in report["required_failures"]
    assert "release_manifest_unavailable" in report["required_failures"]


def test_release_gate_accepts_matching_quality_content_digest(
    temp_engine,
    monkeypatch,
):
    from kreports.quality.release_gate import evaluate_release_gate

    _seed_quality_row(
        corp_code="00126380",
        grade="A",
        stock_code="005930",
    )
    _seed_valid_manifest(temp_engine)
    with get_session() as session:
        manifest = session.get(DatasetManifest, "release-v1")
        assert manifest is not None
        snapshot = json.loads(manifest.quality_snapshot_json)
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    report = evaluate_release_gate("public_runtime")

    assert snapshot.get("content_digest")
    assert "quality_snapshot_mismatch" not in report["required_failures"]
    assert "release_manifest_unavailable" not in report[
        "required_failures"
    ]
    assert report["ok"] is True


def test_release_gate_fails_closed_on_malformed_blocker_json(
    temp_engine,
    monkeypatch,
):
    from kreports.quality.release_gate import evaluate_release_gate

    _seed_quality_row(
        corp_code="00126380",
        grade="A",
        stock_code="005930",
    )
    _seed_valid_manifest(temp_engine)
    with get_session() as session:
        row = session.get(CompanyYearQuality, ("00126380", 2025))
        assert row is not None
        row.blockers_json = "{not-json"
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    report = evaluate_release_gate("public_runtime")

    assert "quality_snapshot_invalid" in report["required_failures"]
    assert "release_manifest_unavailable" in report["required_failures"]


def test_release_gate_rejects_quality_snapshot_row_count_mismatch(
    temp_engine,
    monkeypatch,
):
    from kreports.quality.release_gate import evaluate_release_gate

    _seed_quality_row(
        corp_code="00126380",
        grade="A",
        stock_code="005930",
    )
    _seed_valid_manifest(temp_engine)
    with get_session() as session:
        session.add(
            CompanyYearQuality(
                corp_code="00126380",
                bsns_year=2024,
                market="KOSPI",
                financial_core_status="available",
                auditor_status="available",
                audit_fee_status="available",
                policy_status="full_body",
                kam_status="full_body",
                audit_procedure_status="available",
                group_audit_status="missing",
                investor_grade="A",
                auditor_grade="A",
                group_audit_grade="D",
                blockers_json="[]",
                quality_version="v1",
                **_quality_freshness_fields(),
                updated_at=datetime.now(UTC),
            )
        )
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    report = evaluate_release_gate("public_runtime")

    assert "quality_snapshot_mismatch" in report["required_failures"]
    assert "release_manifest_counts_mismatch" not in report[
        "required_failures"
    ]


def test_release_gate_rejects_quality_snapshot_coverage_year_mismatch(
    temp_engine,
    monkeypatch,
):
    from kreports.quality.release_gate import evaluate_release_gate

    _seed_quality_row(
        corp_code="00126380",
        grade="A",
        stock_code="005930",
    )
    _seed_valid_manifest(temp_engine)
    with get_session() as session:
        old = session.get(CompanyYearQuality, ("00126380", 2025))
        assert old is not None
        session.delete(old)
        session.add(
            CompanyYearQuality(
                corp_code="00126380",
                bsns_year=2026,
                market="KOSPI",
                financial_core_status="available",
                auditor_status="available",
                audit_fee_status="available",
                policy_status="full_body",
                kam_status="full_body",
                audit_procedure_status="available",
                group_audit_status="missing",
                investor_grade="A",
                auditor_grade="A",
                group_audit_grade="D",
                blockers_json="[]",
                quality_version="v1",
                **_quality_freshness_fields(),
                updated_at=datetime.now(UTC),
            )
        )
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    report = evaluate_release_gate("public_runtime")

    assert "quality_snapshot_mismatch" in report["required_failures"]
    assert "release_manifest_year_mismatch" in report[
        "required_failures"
    ]


def test_release_gate_rejects_missing_code_migration_revision(
    temp_engine,
    monkeypatch,
):
    from sqlalchemy import text

    from kreports.quality.release_gate import evaluate_release_gate

    _seed_quality_row(
        corp_code="00126380",
        grade="A",
        stock_code="005930",
    )
    _seed_valid_manifest(temp_engine)
    with temp_engine.begin() as connection:
        connection.execute(
            text(
                "DELETE FROM schema_migrations "
                "WHERE revision=:revision"
            ),
            {"revision": MIGRATIONS[0].revision},
        )
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    report = evaluate_release_gate("public_runtime")

    assert "schema_migration_contract_mismatch" in report[
        "required_failures"
    ]


def test_public_runtime_does_not_round_1899_of_1999_up_to_threshold(
    temp_engine,
    monkeypatch,
):
    from kreports.quality.release_gate import evaluate_release_gate

    now = datetime.now(UTC)
    with get_session() as session:
        session.add_all(
            [
                Company(
                    corp_code=f"{index + 1:08d}",
                    stock_code=f"{index + 1:06d}",
                    corp_name=f"회사-{index + 1}",
                    market="KOSPI",
                )
                for index in range(1999)
            ]
        )
        session.add_all(
            [
                CompanyYearQuality(
                    corp_code=f"{index + 1:08d}",
                    bsns_year=2025,
                    market="KOSPI",
                    financial_core_status="available",
                    auditor_status="available",
                    audit_fee_status="available",
                    policy_status="full_body",
                    kam_status="full_body",
                    audit_procedure_status="available",
                    group_audit_status="missing",
                    investor_grade="A" if index < 1899 else "D",
                    auditor_grade="A",
                    group_audit_grade="D",
                    blockers_json="[]",
                    quality_version="v1",
                    **_quality_freshness_fields(
                        investor_grade=(
                            "A" if index < 1899 else "D"
                        ),
                    ),
                    updated_at=now,
                )
                for index in range(1999)
            ]
        )
    _seed_valid_manifest(temp_engine)
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    report = evaluate_release_gate("public_runtime")

    assert report["coverage"]["investor_core"]["coverage_pct"] == 95.0
    assert "investor_core_coverage" in report["required_failures"]
