from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, inspect

from kreports.db.engine import get_session
from kreports.db.models import (
    AuditProcedureItem,
    AuditFee,
    Company,
    BusinessAffiliateAuditor,
    FetchLog,
    Disclosure,
    FinancialFactCompact,
    ExtractionRun,
    ReportSection,
    SourceDocument,
)
from kreports.semantic.metrics import CORE_FINANCIAL_METRICS


def test_company_year_quality_schema_is_versioned_append_only(temp_engine):
    from kreports.db.migrations import MIGRATIONS, apply_schema_migrations
    from kreports.db.models import CompanyYearQuality

    assert [migration.revision for migration in MIGRATIONS] == [
        "20260711_01_quality_contract",
        "20260711_02_company_year_quality",
        "20260711_03_backfill_run_lifecycle",
        "20260711_04_backfill_owner_identity",
        "20260711_05_kam_items",
        "20260711_06_audit_procedure_linkage",
        "20260711_07_audit_fee_availability",
    ]
    assert CompanyYearQuality.__tablename__ == "company_year_quality"

    with temp_engine.begin() as connection:
        applied = apply_schema_migrations(connection)

    assert applied == [
        "20260711_01_quality_contract",
        "20260711_02_company_year_quality",
        "20260711_03_backfill_run_lifecycle",
        "20260711_04_backfill_owner_identity",
        "20260711_05_kam_items",
        "20260711_06_audit_procedure_linkage",
        "20260711_07_audit_fee_availability",
    ]
    columns = {
        column["name"]
        for column in inspect(temp_engine).get_columns("company_year_quality")
    }
    assert {
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
        "updated_at",
    } == columns


def _seed_core_financial_years(
    corp_code: str,
    years: range,
    *,
    market: str = "KOSPI",
) -> None:
    with get_session() as session:
        session.add(
            Company(
                corp_code=corp_code,
                stock_code=corp_code[-6:],
                corp_name=f"회사-{corp_code}",
                market=market,
            )
        )
        for year in years:
            for metric_key in CORE_FINANCIAL_METRICS:
                session.add(
                    FinancialFactCompact(
                        corp_code=corp_code,
                        bsns_year=year,
                        fs_div="CFS",
                        metric_key=metric_key,
                        metric_name=metric_key,
                        amount=100,
                    )
                )


def test_investor_grade_does_not_require_five_year_audit_fee(temp_engine):
    from kreports.quality.company_year import (
        company_year_quality,
        rebuild_company_year_quality,
    )

    _seed_core_financial_years("00126380", range(2021, 2026))
    with get_session() as session:
        session.add(
            Disclosure(
                rcept_no="20250318000001",
                corp_code="00126380",
                corp_name="회사-00126380",
                disc_date=date(2025, 3, 18),
                disc_type="A",
                report_nm="사업보고서 (2024.12)",
            )
        )

    result = rebuild_company_year_quality(2021, 2025)
    latest = company_year_quality("00126380", 2025)

    assert result["rows_written"] == 5
    assert latest["feature_grades"]["investor_core"] == "A"
    assert latest["feature_grades"]["audit_fee_peer"] == "D"


def test_summary_only_kam_is_not_procedure_ready(temp_engine):
    from kreports.quality.company_year import (
        company_year_quality,
        rebuild_company_year_quality,
    )

    with get_session() as session:
        session.add(
            Company(
                corp_code="00126380",
                stock_code="005930",
                corp_name="삼성전자",
                market="KOSPI",
            )
        )
        session.add(
            ReportSection(
                rcept_no="20250318000002",
                corp_code="00126380",
                bsns_year=2025,
                source_type="audit_report",
                section_key="kam",
                section_title="핵심감사사항",
                body_text="핵심감사사항이 존재합니다.",
                body_length=15,
            )
        )

    rebuild_company_year_quality(2025, 2025)
    quality = company_year_quality("00126380", 2025)

    assert quality["statuses"]["kam"] == "summary_only"
    assert quality["statuses"]["audit_procedure"] == "missing"


@pytest.mark.parametrize(
    "extractor_name",
    ["sections", "document_features", "kam_sections"],
)
def test_kam_extraction_error_is_not_degraded_to_missing(
    temp_engine,
    extractor_name,
):
    from kreports.quality.company_year import (
        company_year_quality,
        rebuild_company_year_quality,
    )

    with get_session() as session:
        session.add(
            Company(
                corp_code="00126380",
                stock_code="005930",
                corp_name="삼성전자",
                market="KOSPI",
            )
        )
        source = SourceDocument(
            rcept_no="20250318000003",
            corp_code="00126380",
            bsns_year=2025,
            source_type="audit_report",
            report_nm="감사보고서",
            raw_content="",
            doc_hash="a" * 40,
        )
        session.add(source)
        session.flush()
        session.add(
            ExtractionRun(
                source_document_id=source.id,
                rcept_no=source.rcept_no,
                source_type="audit_report",
                extractor_name=extractor_name,
                status="error",
                rows_written=0,
                error_msg="parser failed",
            )
        )

    rebuild_company_year_quality(2025, 2025)
    quality = company_year_quality("00126380", 2025)

    assert quality["statuses"]["kam"] == "error"
    assert quality["statuses"]["audit_procedure"] == "error"
    assert "kam_error" in quality["blockers"]


def test_cached_default_all_extractor_error_outranks_missing(
    temp_engine,
    monkeypatch,
):
    import kreports.collector.report_document_collector as collector_module
    from kreports.quality.company_year import (
        company_year_quality,
        rebuild_company_year_quality,
    )

    with get_session() as session:
        session.add(
            Company(
                corp_code="00126380",
                stock_code="005930",
                corp_name="삼성전자",
                market="KOSPI",
            )
        )
        session.add(
            SourceDocument(
                rcept_no="20250318000009",
                corp_code="00126380",
                bsns_year=2025,
                source_type="audit_report",
                report_nm="감사보고서",
                raw_content="<DOCUMENT>cached audit report</DOCUMENT>",
                doc_hash="b" * 40,
            )
        )

    def fail_cached_extraction(*_args, **_kwargs):
        raise ValueError("cached parser failed")

    monkeypatch.setattr(
        collector_module,
        "extract_document_features_from_content",
        fail_cached_extraction,
    )

    extraction = collector_module.run_document_extractors(
        year=2025,
        source_type="audit_report",
    )

    assert extraction["failed"] == 1
    with get_session() as session:
        outcome = (
            session.query(ExtractionRun)
            .filter_by(
                rcept_no="20250318000009",
                extractor_name="all",
                status="error",
            )
            .one()
        )
        assert outcome.error_msg == "cached parser failed"

    rebuild_company_year_quality(2025, 2025)
    quality = company_year_quality("00126380", 2025)

    assert quality["statuses"]["kam"] == "error"
    assert quality["statuses"]["audit_procedure"] == "error"
    assert "kam_error" in quality["blockers"]


def test_group_audit_withholds_a_without_persisted_qsc_evidence(temp_engine):
    from kreports.quality.company_year import (
        company_year_quality,
        rebuild_company_year_quality,
    )

    _seed_core_financial_years("00126380", range(2025, 2026))
    with get_session() as session:
        session.add(
            BusinessAffiliateAuditor(
                parent_corp_code="00126380",
                parent_rcept_no="20250318000004",
                bsns_year=2025,
                name="종속회사",
                relation="종속기업",
                ownership_pct=80.0,
                assets="20",
            )
        )

    rebuild_company_year_quality(2025, 2025)
    quality = company_year_quality("00126380", 2025)

    assert quality["statuses"]["group_audit"] == "partial"
    assert quality["feature_grades"]["group_audit"] == "D"


def test_explicit_no_kam_and_source_no_data_support_auditor_a(temp_engine):
    from kreports.db.models import AccountingPolicyItem, Auditor
    from kreports.quality.company_year import (
        company_year_quality,
        rebuild_company_year_quality,
    )

    with get_session() as session:
        session.add(
            Company(
                corp_code="00126380",
                stock_code="005930",
                corp_name="삼성전자",
                market="KOSPI",
            )
        )
        session.add(
            Auditor(
                corp_code="00126380",
                bsns_year=2025,
                fs_div="CFS",
                auditor_nm="감사법인",
                audit_opinion="적정",
            )
        )
        session.add(
            ReportSection(
                rcept_no="20250318000005",
                corp_code="00126380",
                bsns_year=2025,
                source_type="audit_report",
                section_key="kam",
                body_text="보고할 핵심감사사항이 없습니다.",
            )
        )
        session.add(
            AccountingPolicyItem(
                corp_code="00126380",
                bsns_year=2025,
                fs_div="CFS",
                rcept_no="20250318000006",
                item_key="revenue",
                body="수익인식 회계정책 근거 " * 20,
            )
        )
        session.add(
            FetchLog(
                task_type="audit_fee",
                corp_code="00126380",
                year=2025,
                status="no_data",
            )
        )

    rebuild_company_year_quality(2025, 2025)
    quality = company_year_quality("00126380", 2025)

    assert quality["statuses"]["kam"] == "explicit_no_kam"
    assert quality["statuses"]["audit_procedure"] == "not_applicable"
    assert quality["statuses"]["audit_fee"] == "not_available"
    assert quality["feature_grades"]["auditor_full"] == "A"


def test_rebuild_is_idempotent_and_scoped_by_year_and_market(temp_engine):
    from kreports.db.models import CompanyYearQuality
    from kreports.quality.company_year import rebuild_company_year_quality

    _seed_core_financial_years("00126380", range(2024, 2026), market="KOSPI")
    _seed_core_financial_years("00999999", range(2024, 2026), market="KOSDAQ")

    first = rebuild_company_year_quality(2024, 2025, market="KOSPI")
    second = rebuild_company_year_quality(2024, 2025, market="KOSPI")

    with get_session() as session:
        count = int(session.query(func.count(CompanyYearQuality.corp_code)).scalar())
        rows = (
            session.query(
                CompanyYearQuality.corp_code,
                CompanyYearQuality.bsns_year,
            )
            .order_by(
                CompanyYearQuality.corp_code,
                CompanyYearQuality.bsns_year,
            )
            .all()
        )

    assert first["rows_written"] == 2
    assert second["rows_written"] == 2
    assert count == 2
    assert rows == [("00126380", 2024), ("00126380", 2025)]


def test_three_core_years_are_investor_b_but_transport_error_is_d(
    temp_engine,
):
    from kreports.quality.company_year import (
        company_year_quality,
        rebuild_company_year_quality,
    )

    _seed_core_financial_years("00126380", range(2023, 2026))
    _seed_core_financial_years("00999999", range(2023, 2026))
    with get_session() as session:
        session.add(
            FetchLog(
                task_type="financial",
                corp_code="00999999",
                year=2025,
                status="error",
                error_msg="transport failed",
            )
        )

    rebuild_company_year_quality(2025, 2025)

    grade_b = company_year_quality("00126380", 2025)
    grade_d = company_year_quality("00999999", 2025)
    assert grade_b["feature_grades"]["investor_core"] == "B"
    assert grade_d["statuses"]["financial_core"] == "error"
    assert grade_d["feature_grades"]["investor_core"] == "D"
    assert "financial_core_error" in grade_d["blockers"]


def test_rebuild_requires_collector_mode_without_mutating_rows(
    temp_engine,
    monkeypatch,
):
    from kreports.db.models import CompanyYearQuality
    from kreports.quality.company_year import rebuild_company_year_quality

    _seed_core_financial_years("00126380", range(2025, 2026))
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    with pytest.raises(RuntimeError, match="requires collector mode"):
        rebuild_company_year_quality(2025, 2025)

    with get_session() as session:
        assert session.query(CompanyYearQuality).count() == 0


def test_explicit_financial_no_data_is_not_available_not_missing(temp_engine):
    from kreports.quality.company_year import (
        company_year_quality,
        rebuild_company_year_quality,
    )

    with get_session() as session:
        session.add(
            Company(
                corp_code="00126380",
                stock_code="005930",
                corp_name="삼성전자",
                market="KOSPI",
            )
        )
        session.add(
            FetchLog(
                task_type="financial",
                corp_code="00126380",
                year=2025,
                status="no_data",
            )
        )

    rebuild_company_year_quality(2025, 2025)
    quality = company_year_quality("00126380", 2025)

    assert quality["statuses"]["financial_core"] == "not_available"


def test_actual_audit_report_section_error_is_preserved(temp_engine):
    from kreports.quality.company_year import (
        company_year_quality,
        rebuild_company_year_quality,
    )

    with get_session() as session:
        session.add(
            Company(
                corp_code="00126380",
                stock_code="005930",
                corp_name="삼성전자",
                market="KOSPI",
            )
        )
        session.add(
            FetchLog(
                task_type="audit_report_section",
                corp_code="00126380",
                year=2025,
                status="error",
                error_msg="document fetch failed",
            )
        )

    rebuild_company_year_quality(2025, 2025)
    quality = company_year_quality("00126380", 2025)

    assert quality["statuses"]["kam"] == "error"
    assert quality["statuses"]["audit_procedure"] == "error"


def test_kam_and_procedure_use_only_deterministic_current_receipt(
    temp_engine,
):
    from kreports.quality.company_year import (
        company_year_quality,
        rebuild_company_year_quality,
    )

    with get_session() as session:
        session.add(
            Company(
                corp_code="00126380",
                stock_code="005930",
                corp_name="삼성전자",
                market="KOSPI",
            )
        )
        session.add_all(
            [
                ReportSection(
                    rcept_no="20240318000001",
                    corp_code="00126380",
                    bsns_year=2025,
                    source_type="audit_report",
                    section_key="kam",
                    body_text=(
                        "핵심감사사항에 대해 다음 감사절차를 "
                        "수행하였습니다. " * 10
                    ),
                ),
                ReportSection(
                    rcept_no="20250318000001",
                    corp_code="00126380",
                    bsns_year=2025,
                    source_type="audit_report",
                    section_key="kam",
                    body_text="핵심감사사항이 존재합니다.",
                ),
            ]
        )
        session.add_all(
            [
                AuditProcedureItem(
                    rcept_no="20240318000001",
                    corp_code="00126380",
                    bsns_year=2025,
                    source_type="audit_report",
                    procedure_type="inspection",
                    procedure_text="과거 감사절차",
                ),
                AuditProcedureItem(
                    rcept_no="20250318000001",
                    corp_code="00126380",
                    bsns_year=2025,
                    source_type="audit_report",
                    procedure_type="inspection",
                    procedure_text="요약문에서 잘못 연결된 절차",
                ),
            ]
        )

    rebuild_company_year_quality(2025, 2025)
    quality = company_year_quality("00126380", 2025)

    assert quality["statuses"]["kam"] == "summary_only"
    assert quality["statuses"]["audit_procedure"] == "missing"


@pytest.mark.parametrize(
    "scenario,facts",
    [
        (
            "below_10_percent",
            [
                ("00126380", "CFS", "assets", 1_000),
                ("00126380", "CFS", "revenue", 1_000),
                ("00999999", "CFS", "assets", 50),
                ("00999999", "CFS", "revenue", 50),
            ],
        ),
        (
            "above_10_percent",
            [
                ("00126380", "CFS", "assets", 1_000),
                ("00126380", "CFS", "revenue", 1_000),
                ("00999999", "CFS", "assets", 200),
                ("00999999", "CFS", "revenue", 200),
            ],
        ),
        (
            "missing_component_revenue",
            [
                ("00126380", "CFS", "assets", 1_000),
                ("00126380", "CFS", "revenue", 1_000),
                ("00999999", "CFS", "assets", 200),
            ],
        ),
        (
            "mixed_fs_divisions",
            [
                ("00126380", "CFS", "assets", 1_000),
                ("00126380", "OFS", "revenue", 1_000),
                ("00999999", "CFS", "assets", 200),
                ("00999999", "OFS", "revenue", 200),
            ],
        ),
    ],
)
def test_group_audit_never_infers_qsc_from_amounts(
    temp_engine,
    scenario,
    facts,
):
    del scenario
    from kreports.quality.company_year import (
        company_year_quality,
        rebuild_company_year_quality,
    )

    with get_session() as session:
        session.add(
            Company(
                corp_code="00126380",
                stock_code="005930",
                corp_name="삼성전자",
                market="KOSPI",
            )
        )
        session.add(
            BusinessAffiliateAuditor(
                parent_corp_code="00126380",
                parent_rcept_no="20250318000007",
                bsns_year=2025,
                name="종속회사",
                relation="종속기업",
                ownership_pct=80.0,
                assets="200",
                corp_code="00999999",
            )
        )
        session.add_all(
            [
                FinancialFactCompact(
                    corp_code=corp_code,
                    bsns_year=2025,
                    fs_div=fs_div,
                    metric_key=metric_key,
                    metric_name=metric_key,
                    amount=amount,
                )
                for corp_code, fs_div, metric_key, amount in facts
            ]
        )

    rebuild_company_year_quality(2025, 2025)
    quality = company_year_quality("00126380", 2025)

    assert quality["statuses"]["group_audit"] == "partial"
    assert quality["feature_grades"]["group_audit"] == "D"


def test_audit_fee_quality_consumes_real_per_company_outcomes(temp_engine):
    from kreports.quality.company_year import (
        company_year_quality,
        rebuild_company_year_quality,
    )

    with get_session() as session:
        session.add_all(
            [
                Company(
                    corp_code="00126380",
                    stock_code="005930",
                    corp_name="no-data",
                    market="KOSPI",
                ),
                Company(
                    corp_code="00999998",
                    stock_code="999998",
                    corp_name="error",
                    market="KOSPI",
                ),
                Company(
                    corp_code="00999999",
                    stock_code="999999",
                    corp_name="no-log",
                    market="KOSPI",
                ),
            ]
        )
        session.add_all(
            [
                FetchLog(
                    task_type="audit_fee",
                    corp_code="00126380",
                    year=2025,
                    status="no_data",
                ),
                FetchLog(
                    task_type="audit_fee",
                    corp_code="00999998",
                    year=2025,
                    status="error",
                    error_msg="transport failed",
                ),
            ]
        )

    rebuild_company_year_quality(2025, 2025)

    assert company_year_quality("00126380", 2025)["statuses"][
        "audit_fee"
    ] == "not_available"
    assert company_year_quality("00999998", 2025)["statuses"][
        "audit_fee"
    ] == "error"
    assert company_year_quality("00999999", 2025)["statuses"][
        "audit_fee"
    ] == "missing"


def test_audit_fee_later_success_timestamp_recovers_older_error(
    temp_engine,
):
    from kreports.quality.company_year import (
        company_year_quality,
        rebuild_company_year_quality,
    )

    now = datetime.now(timezone.utc)
    with get_session() as session:
        session.add(
            Company(
                corp_code="00126380",
                stock_code="005930",
                corp_name="recovered",
                market="KOSPI",
            )
        )
        session.add(
            AuditFee(
                corp_code="00126380",
                bsns_year=2025,
                audit_fee_m=100,
                audit_hours=200,
                fetched_at=now,
            )
        )
        session.add_all(
            [
                FetchLog(
                    task_type="audit_fee",
                    corp_code="00126380",
                    year=2025,
                    status="success",
                    fetched_at=now,
                ),
                FetchLog(
                    task_type="audit_fee",
                    corp_code="00126380",
                    year=2025,
                    status="error",
                    error_msg="older transport failure",
                    fetched_at=now - timedelta(minutes=1),
                ),
            ]
        )

    rebuild_company_year_quality(2025, 2025)

    assert company_year_quality("00126380", 2025)["statuses"][
        "audit_fee"
    ] == "available"


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (("error", "success"), "available"),
        (("success", "error"), "error"),
        (("error", "no_data"), "not_available"),
    ],
)
def test_audit_fee_equal_timestamp_uses_later_fetch_log_id(
    temp_engine,
    statuses,
    expected,
):
    from kreports.quality.company_year import (
        company_year_quality,
        rebuild_company_year_quality,
    )

    now = datetime.now(timezone.utc)
    with get_session() as session:
        session.add(
            Company(
                corp_code="00126380",
                stock_code="005930",
                corp_name="same-timestamp",
                market="KOSPI",
            )
        )
        if "success" in statuses:
            session.add(
                AuditFee(
                    corp_code="00126380",
                    bsns_year=2025,
                    audit_fee_m=100,
                    audit_hours=200,
                    fetched_at=now,
                )
            )
        session.add_all(
            [
                FetchLog(
                    task_type="audit_fee",
                    corp_code="00126380",
                    year=2025,
                    status=status,
                    error_msg=(
                        "transport failure"
                        if status == "error"
                        else None
                    ),
                    fetched_at=now,
                )
                for status in statuses
            ]
        )

    rebuild_company_year_quality(2025, 2025)

    assert company_year_quality("00126380", 2025)["statuses"][
        "audit_fee"
    ] == expected
