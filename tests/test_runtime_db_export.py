from sqlalchemy import inspect
from sqlalchemy import text


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
            ('00126380', 2024, '11011', 'CFS', 'IS', 'ifrs-full_Revenue', '매출액', 1, 300, 250, CURRENT_TIMESTAMP)
        """))
        session.commit()

    out = rebuild_financial_facts_compact(year_from=2024, year_to=2024)

    assert out["inserted_or_updated"] == 2
    with get_session() as session:
        rows = session.execute(text("""
            SELECT metric_key, amount FROM financial_facts_compact
            WHERE corp_code='00126380'
            ORDER BY metric_key
        """)).all()
    assert rows == [("assets", 1000), ("revenue", 300)]


def test_export_runtime_db_excludes_heavy_warehouse_tables(temp_engine, tmp_path):
    from kreports.maintenance.runtime_export import export_runtime_db

    out_path = tmp_path / "runtime.db"
    result = export_runtime_db(output_path=out_path, year_from=2024, year_to=2025, profile="compact")

    assert result["ok"] is True
    assert out_path.exists()
    assert "financial_facts" in result["excluded_tables"]
    assert "extraction_runs" in result["excluded_tables"]
    assert "fetch_log" in result["excluded_tables"]


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
