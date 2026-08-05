import sqlite3
from datetime import date, datetime

import pytest
from sqlalchemy import inspect
from sqlalchemy import text

from kreports.db.models import (
    Company,
    Disclosure,
    Financial,
    FinancialFactCompact,
    SourceDocument,
)


def test_financial_facts_compact_schema(temp_engine):
    inspector = inspect(temp_engine)
    columns = {column["name"] for column in inspector.get_columns("financial_facts_compact")}
    assert {
        "corp_code",
        "bsns_year",
        "fs_div",
        "metric_key",
        "metric_name",
        "amount",
        "source_account_id",
        "source_account_nm",
        "source_table",
        "unit",
        "period_type",
        "citation_rcept_no",
        "citation_report_nm",
        "citation_basis",
        "quality_status",
    }.issubset(columns)
    unique_constraints = {
        item["name"]: item
        for item in inspector.get_unique_constraints("financial_facts_compact")
    }
    assert unique_constraints["uq_financial_facts_compact"]["column_names"] == [
        "corp_code",
        "bsns_year",
        "fs_div",
        "metric_key",
    ]


def test_rebuild_financial_facts_compact_maps_core_metrics(temp_engine):
    from kreports.db.engine import get_session
    from kreports.maintenance.financial_compact import rebuild_financial_facts_compact

    with get_session() as session:
        session.execute(text("""
            INSERT INTO financial_facts
            (corp_code, bsns_year, reprt_code, fs_div, sj_div, account_id, account_nm,
             ord, thstrm_amount, frmtrm_amount, fetched_at)
            VALUES
            ('00126380', 2024, '11011', 'CFS', 'BS', 'ifrs-full_Assets', '자산총계', 1, 1000, 900, CURRENT_TIMESTAMP),
            ('00126380', 2024, '11011', 'CFS', 'IS', 'ifrs-full_Revenue', '매출액', 1, 300, 250, CURRENT_TIMESTAMP),
            ('00126380', 2024, '11011', 'CFS', 'IS', 'dart_OperatingIncomeLoss', '영업이익', 2, 30, 20, CURRENT_TIMESTAMP),
            ('00126380', 2024, '11011', 'CFS', 'IS', 'ifrs-full_IncomeTaxExpenseContinuingOperations', '법인세비용', 3, 5, 4, CURRENT_TIMESTAMP),
            ('00126380', 2024, '11011', 'CFS', 'CF', 'ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities', '유형자산의 취득', 4, 40, 35, CURRENT_TIMESTAMP),
            ('00126380', 2024, '11011', 'CFS', 'CF', 'ifrs-full_PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities', '무형자산의 취득', 5, 6, 5, CURRENT_TIMESTAMP)
        """))
        session.commit()

    out = rebuild_financial_facts_compact(year_from=2024, year_to=2024)

    assert out["inserted_or_updated"] == 6
    with get_session() as session:
        rows = session.execute(text("""
            SELECT metric_key, amount FROM financial_facts_compact
            WHERE corp_code='00126380'
            ORDER BY metric_key
        """)).all()
    assert rows == [
        ("assets", 1000),
        ("operating_profit", 30),
        ("purchase_intangible_assets", 6),
        ("purchase_ppe", 40),
        ("revenue", 300),
        ("tax_expense", 5),
    ]


def test_rebuild_financial_facts_compact_falls_back_to_annual_financials(temp_engine):
    from kreports.db.engine import get_session
    from kreports.maintenance.financial_compact import rebuild_financial_facts_compact

    with get_session() as session:
        session.add(Financial(
            corp_code="00126380",
            year=2024,
            quarter=4,
            fs_div="CFS",
            revenue=300,
            operating_profit=30,
            net_income=20,
            total_assets=1000,
            total_debt=400,
            total_equity=600,
            operating_cf=25,
            source="summary_fallback",
        ))

    out = rebuild_financial_facts_compact(year_from=2024, year_to=2024)

    assert out["summary_source_rows"] == 1
    assert out["summary_inserted_or_updated"] == 7
    with get_session() as session:
        rows = session.execute(text("""
            SELECT metric_key, metric_name, amount, source_account_id
            FROM financial_facts_compact
            WHERE corp_code='00126380'
            ORDER BY metric_key
        """)).all()
    assert rows == [
        ("assets", "자산총계", 1000, "financials.total_assets"),
        ("equity", "자본총계", 600, "financials.total_equity"),
        ("liabilities", "부채총계", 400, "financials.total_debt"),
        ("operating_cash_flow", "영업활동현금흐름", 25, "financials.operating_cf"),
        ("operating_profit", "영업손익", 30, "financials.operating_profit"),
        ("profit_loss", "당기순손익", 20, "financials.net_income"),
        ("revenue", "매출액", 300, "financials.revenue"),
    ]


def test_export_runtime_db_excludes_heavy_warehouse_tables(temp_engine, tmp_path):
    from kreports.maintenance.runtime_export import export_runtime_db

    out_path = tmp_path / "runtime.db"
    result = export_runtime_db(output_path=out_path, year_from=2024, year_to=2025, profile="compact")

    assert result["ok"] is True
    assert out_path.exists()
    assert "financial_facts" in result["excluded_tables"]
    assert "extraction_runs" in result["excluded_tables"]
    assert "fetch_log" in result["excluded_tables"]


def test_export_runtime_db_applies_the_requested_year_window_to_runtime_rows(
    temp_engine,
    tmp_path,
):
    """Catches an export manifest claiming 2024 while copying 2023 rows."""
    from sqlalchemy.orm import Session

    from kreports.collector.audit_fee_sources import AuditFeeObservation
    from kreports.db.audit_fee_observation_store import persist_audit_fee_observations
    from kreports.db.models import (
        AccountingPolicyItem,
        AuditFee,
        BackfillRun,
            CompanyYearQuality,
            DatasetManifest,
            DisclosureEvent,
        EvidenceDocument,
        GroupEntityRecord,
        ReportDocument,
    )
    from kreports.maintenance.runtime_export import export_runtime_db

    with Session(temp_engine) as session:
        for year in (2023, 2024):
            receipt = f"{year + 1}0318000001"
            session.add(Financial(
                corp_code="00126380",
                year=year,
                quarter=4,
                fs_div="CFS",
            ))
            session.add(FinancialFactCompact(
                corp_code="00126380",
                bsns_year=year,
                fs_div="CFS",
                metric_key=f"revenue_{year}",
                metric_name="매출액",
            ))
            session.add(AuditFee(corp_code="00126380", bsns_year=year))
            session.add(AccountingPolicyItem(
                corp_code="00126380",
                bsns_year=year,
                fs_div="CFS",
                rcept_no=receipt,
                item_key=f"revenue_{year}",
                body="policy",
            ))
            session.add(ReportDocument(
                rcept_no=receipt,
                corp_code="00126380",
                bsns_year=year,
                source_type="business_report",
                report_nm="사업보고서",
            ))
            session.add(EvidenceDocument(
                corp_code="00126380",
                bsns_year=year,
                source_type="business_report",
                rcept_no=receipt,
                normalized_text="evidence",
            ))
            session.add(GroupEntityRecord(
                parent_corp_code="00126380",
                effective_year=year,
                entity_key=f"entity-{year}",
                original_name="entity",
                normalized_name="entity",
                resolution_status="unresolved",
                resolution_reason="fixture",
                source_rcept_no=receipt,
                source_table="fixture",
                source_ordinal=0,
            ))
            session.add(CompanyYearQuality(
                corp_code="00126380",
                bsns_year=year,
                financial_core_status="available",
                auditor_status="available",
                audit_fee_status="available",
                policy_status="available",
                kam_status="available",
                audit_procedure_status="available",
                group_audit_status="available",
                investor_grade="A",
                auditor_grade="A",
                group_audit_grade="A",
            ))
            session.add(BackfillRun(task_type="fixture", year=year, status="success"))
            session.add(DisclosureEvent(
                rcept_no=f"{year}0101000001",
                corp_code="00126380",
                event_date=datetime(year, 1, 1),
                event_type="capital",
                event_title="event",
                source_report_nm="주요사항보고서",
            ))
        session.add(BackfillRun(task_type="fixture", year=None, status="success"))
        session.add_all([
            DatasetManifest(
                manifest_id="fixture-2023-2024",
                schema_version="v1",
                dataset_version="fixture",
                generated_at=datetime(2025, 1, 1),
                year_from=2023,
                year_to=2024,
                company_count=1,
                disclosure_count=2,
                evidence_document_count=2,
            ),
            DatasetManifest(
                manifest_id="fixture-2024",
                schema_version="v1",
                dataset_version="fixture",
                generated_at=datetime(2025, 1, 1),
                year_from=2024,
                year_to=2024,
                company_count=1,
                disclosure_count=2,
                evidence_document_count=1,
            ),
        ])
        session.add_all([
            Disclosure(
                rcept_no="20240318000001",
                corp_code="00126380",
                corp_name="삼성전자",
                disc_date=date(2024, 3, 18),
                disc_type="A",
                report_nm="사업보고서 (2023.12)",
            ),
            Disclosure(
                rcept_no="20230101000001",
                corp_code="00126380",
                corp_name="삼성전자",
                disc_date=date(2023, 1, 1),
                disc_type="B",
                report_nm="주요사항보고서",
            ),
            SourceDocument(
                rcept_no="20240318000001",
                corp_code="00126380",
                bsns_year=2023,
                source_type="business_report",
                report_nm="사업보고서",
                content_type="xml",
                raw_content="old raw",
                doc_hash="old",
            ),
            SourceDocument(
                rcept_no="20250101000001",
                corp_code="00126380",
                bsns_year=2024,
                source_type="event_disclosure",
                report_nm="주요사항보고서",
                content_type="xml",
                raw_content="event raw",
                doc_hash="event",
            ),
            SourceDocument(
                rcept_no="20250318000001",
                corp_code="00126380",
                bsns_year=2024,
                source_type="business_report",
                report_nm="사업보고서",
                content_type="xml",
                raw_content="current raw",
                doc_hash="current",
            ),
        ])
        persist_audit_fee_observations(session, [
            AuditFeeObservation(
                corp_code="00126380",
                bsns_year=2023,
                source_class="cached_business_report",
                source_rcept_no="20240318000001",
            ),
            AuditFeeObservation(
                corp_code="00126380",
                bsns_year=2024,
                source_class="cached_business_report",
                source_rcept_no="20250318000001",
            ),
        ])
        session.commit()

    out_path = tmp_path / "runtime-2024.db"
    result = export_runtime_db(output_path=out_path, year_from=2024, year_to=2024)

    assert result["table_filters"]["financials"] == "year BETWEEN 2024 AND 2024"
    assert result["table_filters"]["group_entities"] == "effective_year BETWEEN 2024 AND 2024"
    assert result["table_filters"]["backfill_runs"] == "year IS NULL OR year BETWEEN 2024 AND 2024"
    assert result["table_filters"]["source_documents"] == (
        "bsns_year BETWEEN 2024 AND 2024 AND source_type <> 'event_disclosure'"
    )
    assert result["table_filters"]["dataset_manifest"] == "year_from >= 2024 AND year_to <= 2024"
    assert result["copied_row_counts"]["financials"] == 1
    assert result["copied_row_counts"]["source_documents"] == 1

    with sqlite3.connect(out_path) as connection:
        for table, column in (
            ("financials", "year"),
            ("financial_facts_compact", "bsns_year"),
            ("audit_fees", "bsns_year"),
            ("audit_fee_observations", "bsns_year"),
            ("accounting_policy_items", "bsns_year"),
            ("report_documents", "bsns_year"),
            ("evidence_documents", "bsns_year"),
            ("group_entities", "effective_year"),
            ("company_year_quality", "bsns_year"),
        ):
            assert connection.execute(
                f"SELECT DISTINCT {column} FROM {table}"
            ).fetchall() == [(2024,)]
        assert connection.execute(
            "SELECT year FROM backfill_runs ORDER BY year IS NOT NULL, year"
        ).fetchall() == [(None,), (2024,)]
        assert connection.execute("SELECT COUNT(*) FROM disclosures").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM disclosure_events").fetchone()[0] == 2
        assert connection.execute(
            "SELECT rcept_no, raw_content FROM source_documents"
        ).fetchall() == [("20250318000001", "")]
        assert connection.execute(
            "SELECT manifest_id FROM dataset_manifest"
        ).fetchall() == [("fixture-2024",)]


def test_export_runtime_db_rejects_inverted_year_window(temp_engine, tmp_path):
    """Catches an invalid manifest range being accepted and copied without a policy."""
    from kreports.maintenance.runtime_export import export_runtime_db

    with pytest.raises(ValueError, match="year_from must not exceed year_to"):
        export_runtime_db(
            output_path=tmp_path / "invalid-runtime.db",
            year_from=2025,
            year_to=2024,
        )


def test_exported_compact_runtime_preserves_all_tool_public_contract(
    temp_engine,
    tmp_path,
):
    """Excluded warehouse tables must not become public schema errors."""
    from kreports.db.engine import get_session
    from kreports.maintenance.runtime_export import export_runtime_db
    from kreports.mcp.dispatch import dispatch_tool
    from kreports.release_artifact import (
        _bound_explicit_runtime,
        run_all_tool_contract,
    )

    metric_values = {
        "revenue": 300_000_000_000,
        "operating_profit": 30_000_000_000,
        "profit_loss": 20_000_000_000,
        "assets": 500_000_000_000,
        "liabilities": 200_000_000_000,
        "equity": 300_000_000_000,
        "operating_cash_flow": 25_000_000_000,
    }
    with get_session() as session:
        session.add(Company(
            corp_code="00126380",
            stock_code="005930",
            corp_name="삼성전자",
            market="KOSPI",
            induty_code="264",
        ))
        for year in (2023, 2024):
            receipt = f"{year + 1}0318000001"
            session.add(Financial(
                corp_code="00126380",
                year=year,
                quarter=4,
                fs_div="CFS",
                revenue=metric_values["revenue"],
                operating_profit=metric_values["operating_profit"],
                net_income=metric_values["profit_loss"],
                total_assets=metric_values["assets"],
                total_debt=metric_values["liabilities"],
                total_equity=metric_values["equity"],
                operating_cf=metric_values["operating_cash_flow"],
                source="summary_fallback",
            ))
            session.add(Disclosure(
                rcept_no=receipt,
                corp_code="00126380",
                corp_name="삼성전자",
                disc_date=date(year + 1, 3, 18),
                disc_type="A",
                report_nm=f"사업보고서 ({year}.12)",
                flr_nm="삼성전자",
            ))
            session.add_all([
                FinancialFactCompact(
                    corp_code="00126380",
                    bsns_year=year,
                    fs_div="CFS",
                    metric_key=metric_key,
                    metric_name=metric_key,
                    amount=amount,
                    source_table="financials",
                    unit="KRW",
                    period_type="instant" if metric_key in {
                        "assets",
                        "liabilities",
                        "equity",
                    } else "duration",
                    citation_rcept_no=receipt,
                    citation_report_nm=f"사업보고서 ({year}.12)",
                    citation_basis="company_year_annual_filing_match",
                    quality_status="usable",
                )
                for metric_key, amount in metric_values.items()
            ])

    runtime_db = tmp_path / "compact-runtime.db"
    export_runtime_db(
        output_path=runtime_db,
        year_from=2023,
        year_to=2024,
    )

    with sqlite3.connect(runtime_db) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "financial_facts_compact" in tables
    assert {"financial_facts", "extraction_runs", "fetch_log"}.isdisjoint(
        tables
    )
    with _bound_explicit_runtime(runtime_db):
        statuses = {
            name: dispatch_tool(name, arguments).data_quality.status
            for name, arguments in (
                ("score_going_concern", {"company": "005930"}),
                ("detect_restatement", {"company": "005930"}),
                ("get_investor_signals", {"company": "005930"}),
            )
        }
    assert statuses == {
        "score_going_concern": "limited",
        "detect_restatement": "missing",
        "get_investor_signals": "limited",
    }
    assert run_all_tool_contract(runtime_db) == {"passed": True, "checks": 34}


def test_export_runtime_db_retains_all_audit_observation_history(temp_engine, tmp_path):
    from kreports.collector.audit_fee_sources import AuditFeeObservation
    from kreports.db.audit_fee_observation_store import persist_audit_fee_observations
    from kreports.maintenance.runtime_export import export_runtime_db
    from sqlalchemy.orm import Session

    first = AuditFeeObservation(
        corp_code="00126380", bsns_year=2024, source_class="cached_business_report",
        source_rcept_no="receipt", actual_fee_m=100,
    )
    correction = AuditFeeObservation(
        corp_code="00126380", bsns_year=2024, source_class="cached_business_report",
        source_rcept_no="receipt", actual_fee_m=120,
    )
    with Session(temp_engine) as session:
        persist_audit_fee_observations(session, [first, correction])
        session.commit()

    out_path = tmp_path / "runtime-history.db"
    export_runtime_db(output_path=out_path, year_from=2024, year_to=2024)
    with sqlite3.connect(out_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM audit_fee_observations"
        ).fetchone()[0]
        current_count = connection.execute(
            "SELECT COUNT(*) FROM audit_fee_observations WHERE is_current=1"
        ).fetchone()[0]
    assert count == 2
    assert current_count == 1


def test_export_runtime_db_keeps_disclosure_list_but_excludes_on_demand_bodies(temp_engine, tmp_path):
    from kreports.db.engine import get_session
    from kreports.maintenance.runtime_export import export_runtime_db

    with get_session() as session:
        session.add(Disclosure(
            rcept_no="20250101000001",
            corp_code="00126380",
            corp_name="삼성전자",
            disc_date=date(2025, 1, 1),
            disc_type="B",
            report_nm="주요사항보고서(유상증자결정)",
            flr_nm="삼성전자",
        ))
        session.add(SourceDocument(
            rcept_no="20250101000001",
            corp_code="00126380",
            bsns_year=2025,
            source_type="event_disclosure",
            report_nm="주요사항보고서(유상증자결정)",
            content_type="xml",
            raw_content="<DOCUMENT>user keyed on-demand body</DOCUMENT>",
            doc_hash="hash",
        ))

    out_path = tmp_path / "runtime.db"
    result = export_runtime_db(output_path=out_path, year_from=2024, year_to=2025, profile="compact")

    assert result["table_filters"]["source_documents"] == (
        "bsns_year BETWEEN 2024 AND 2025 AND source_type <> 'event_disclosure'"
    )
    with sqlite3.connect(out_path) as conn:
        disclosure_count = conn.execute("SELECT COUNT(*) FROM disclosures").fetchone()[0]
        source_count = conn.execute("SELECT COUNT(*) FROM source_documents").fetchone()[0]
    assert disclosure_count == 1
    assert source_count == 0


def test_export_runtime_db_strips_original_inline_raw_but_keeps_derived_evidence(temp_engine, tmp_path):
    from kreports.db.engine import get_session
    from kreports.maintenance.runtime_export import export_runtime_db

    with get_session() as session:
        session.add(SourceDocument(
            rcept_no="20240318000001",
            corp_code="00126380",
            bsns_year=2023,
            source_type="business_report",
            report_nm="사업보고서",
            content_type="xml",
            raw_content="<DOCUMENT>" + ("원문" * 100) + "</DOCUMENT>",
            doc_hash="hash-original",
        ))
        session.add(SourceDocument(
            rcept_no="20240318000002",
            corp_code="00126380",
            bsns_year=2023,
            source_type="audit_report",
            report_nm="감사보고서 파생 근거",
            content_type="derived_report_sections",
            raw_content="DERIVED FROM report_sections\n## kam\n핵심감사사항 근거",
            doc_hash="hash-derived",
        ))

    out_path = tmp_path / "runtime.db"
    result = export_runtime_db(output_path=out_path, year_from=2023, year_to=2023, profile="compact")

    assert result["ok"] is True
    with sqlite3.connect(out_path) as conn:
        rows = dict(conn.execute("""
            SELECT rcept_no, raw_content
            FROM source_documents
            ORDER BY rcept_no
        """).fetchall())

    assert rows["20240318000001"] == ""
    assert "핵심감사사항 근거" in rows["20240318000002"]


def test_export_runtime_db_can_skip_vacuum_for_low_disk_repair(temp_engine, tmp_path):
    from kreports.maintenance.runtime_export import export_runtime_db

    out_path = tmp_path / "runtime.db"
    result = export_runtime_db(
        output_path=out_path,
        year_from=2024,
        year_to=2025,
        profile="compact",
        vacuum=False,
    )

    assert result["ok"] is True
    assert result["vacuum"] is False
    assert out_path.exists()


def test_financial_snapshot_uses_compact_facts_when_full_fact_table_absent(temp_engine):
    from kreports.analysis.api import get_financial_snapshot
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(
            corp_code="00126380",
            stock_code="005930",
            corp_name="삼성전자",
            market="KOSPI",
            induty_code="264",
        ))
        session.execute(text("""
            INSERT INTO financial_facts_compact
            (corp_code, bsns_year, fs_div, metric_key, metric_name, amount,
             source_account_id, source_account_nm, fetched_at)
            VALUES
            ('00126380', 2023, 'CFS', 'revenue', '매출액', 100000000000, 'ifrs-full_Revenue', '매출액', CURRENT_TIMESTAMP),
            ('00126380', 2023, 'CFS', 'operating_profit', '영업손익', 12000000000, 'dart_OperatingIncomeLoss', '영업이익', CURRENT_TIMESTAMP),
            ('00126380', 2023, 'CFS', 'profit_loss', '당기순손익', 9000000000, 'ifrs-full_ProfitLoss', '당기순이익', CURRENT_TIMESTAMP),
            ('00126380', 2023, 'CFS', 'assets', '자산총계', 300000000000, 'ifrs-full_Assets', '자산총계', CURRENT_TIMESTAMP),
            ('00126380', 2023, 'CFS', 'liabilities', '부채총계', 180000000000, 'ifrs-full_Liabilities', '부채총계', CURRENT_TIMESTAMP),
            ('00126380', 2023, 'CFS', 'equity', '자본총계', 120000000000, 'ifrs-full_Equity', '자본총계', CURRENT_TIMESTAMP),
            ('00126380', 2023, 'CFS', 'operating_cash_flow', '영업활동현금흐름', 15000000000, 'ifrs-full_CashFlowsFromUsedInOperatingActivities', '영업CF', CURRENT_TIMESTAMP),
            ('00126380', 2023, 'CFS', 'purchase_ppe', '유형자산 취득', -2000000000, 'ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities', '유형자산취득', CURRENT_TIMESTAMP)
        """))
        session.execute(text("DROP TABLE financial_facts"))
        session.commit()

    out = get_financial_snapshot("005930", years=1)

    assert out["corp_code"] == "00126380"
    assert out["data_quality"]["source"] == "financial_facts_compact"
    assert out["row_count"] == 1
    assert out["rows"][0]["매출액"] == 1000
    assert out["rows"][0]["영업이익"] == 120
    assert out["rows"][0]["CapEx"] == 20


def test_runtime_db_manifest_contains_hash_and_counts(tmp_path):
    from kreports.maintenance.runtime_export import build_runtime_db_manifest

    db_path = tmp_path / "runtime.db"
    db_path.write_bytes(b"runtime")

    manifest = build_runtime_db_manifest(db_path=db_path, profile="compact", year_from=2021, year_to=2025)

    assert manifest["profile"] == "compact"
    assert manifest["year_from"] == 2021
    assert manifest["year_to"] == 2025
    assert manifest["bytes"] == len(b"runtime")
    assert len(manifest["sha256"]) == 64
