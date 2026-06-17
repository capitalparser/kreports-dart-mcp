import sqlite3
from datetime import date

from sqlalchemy import inspect
from sqlalchemy import text

from kreports.db.models import Company, Disclosure, SourceDocument


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
    }.issubset(columns)


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


def test_export_runtime_db_excludes_heavy_warehouse_tables(temp_engine, tmp_path):
    from kreports.maintenance.runtime_export import export_runtime_db

    out_path = tmp_path / "runtime.db"
    result = export_runtime_db(output_path=out_path, year_from=2024, year_to=2025, profile="compact")

    assert result["ok"] is True
    assert out_path.exists()
    assert "financial_facts" in result["excluded_tables"]
    assert "extraction_runs" in result["excluded_tables"]
    assert "fetch_log" in result["excluded_tables"]


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

    assert result["table_filters"]["source_documents"] == "source_type <> 'event_disclosure'"
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
