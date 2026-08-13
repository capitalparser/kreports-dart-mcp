from datetime import date, datetime

from sqlalchemy import text

from kreports.db.models import Company, Disclosure, DisclosureEvent, SourceDocument


def _add_core_compact_metrics(session, corp_code: str, year: int, fs_div: str = "CFS") -> None:
    for metric in ("revenue", "profit_loss", "operating_cash_flow", "assets", "liabilities", "equity"):
        session.execute(
            text("""
            INSERT INTO financial_facts_compact
            (corp_code, bsns_year, fs_div, metric_key, metric_name, amount,
             source_account_id, source_account_nm, fetched_at)
            VALUES
            (:corp_code, :year, :fs_div, :metric, :metric, 100, :metric, :metric, CURRENT_TIMESTAMP)
            """),
            {"corp_code": corp_code, "year": year, "fs_div": fs_div, "metric": metric},
        )


def test_investor_readiness_treats_disclosure_bodies_as_on_demand(temp_engine):
    from kreports.analysis.readiness import investor_dataset_readiness_snapshot
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add_all([
            Company(corp_code="001", corp_name="A", stock_code="000001", market="KOSPI"),
            Company(corp_code="002", corp_name="B", stock_code="000002", market="KOSPI"),
        ])
        for corp_code in ("001", "002"):
            _add_core_compact_metrics(session, corp_code, 2025)
            session.add(Disclosure(
                rcept_no=f"202501010000{corp_code}",
                corp_code=corp_code,
                corp_name=corp_code,
                disc_date=date(2025, 1, 1),
                disc_type="B",
                report_nm="주요사항보고서(유상증자결정)",
                flr_nm=corp_code,
            ))
        session.add(DisclosureEvent(
            rcept_no="202501010000001",
            corp_code="001",
            event_date=datetime(2025, 1, 1),
            event_type="capital_raise",
            event_title="주요사항보고서(유상증자결정)",
            severity_hint="monitor",
            source_report_nm="주요사항보고서(유상증자결정)",
        ))
        session.add(SourceDocument(
            rcept_no="202501010000001",
            corp_code="001",
            bsns_year=2025,
            source_type="event_disclosure",
            report_nm="주요사항보고서(유상증자결정)",
            content_type="xml",
            raw_content="<DOCUMENT>cached on-demand body</DOCUMENT>",
            doc_hash="hash",
        ))

    out = investor_dataset_readiness_snapshot(year=2025, years_back=1, market="KOSPI")

    assert out["verdict"] == "pass"
    assert out["disclosure_body_storage_policy"] == "on_demand_user_key"
    assert out["disclosure_body_required_for_runtime"] is False
    assert out["on_demand_cached_disclosure_bodies"] == 1
    assert out["required_gaps"] == []
    assert out["yearly"][0]["disclosure_list_coverage_pct"] == 100.0


def test_investor_readiness_uses_historical_disclosure_denominator(temp_engine):
    from kreports.analysis.readiness import investor_dataset_readiness_snapshot
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add_all([
            Company(corp_code="001", corp_name="Old Listed", stock_code="000001", market="KOSPI"),
            Company(corp_code="002", corp_name="Future Listed", stock_code="000002", market="KOSPI"),
        ])
        _add_core_compact_metrics(session, "001", 2021)
        session.add(Disclosure(
            rcept_no="20210101000001",
            corp_code="001",
            corp_name="Old Listed",
            disc_date=date(2021, 1, 1),
            disc_type="A",
            report_nm="분기보고서 (2021.03)",
            flr_nm="Old Listed",
        ))
        session.add(Disclosure(
            rcept_no="20250101000002",
            corp_code="002",
            corp_name="Future Listed",
            disc_date=date(2025, 1, 1),
            disc_type="A",
            report_nm="분기보고서 (2025.03)",
            flr_nm="Future Listed",
        ))

    out = investor_dataset_readiness_snapshot(year=2021, years_back=1, market="KOSPI")

    assert out["listed_companies"] == 2
    assert out["yearly"][0]["disclosure_eligible_companies"] == 1
    assert out["yearly"][0]["disclosure_list_coverage_pct"] == 100.0
    assert "disclosure_list_2021" not in out["required_gaps"]


def test_investor_readiness_counts_ofs_core_financials(temp_engine):
    from kreports.analysis.readiness import investor_dataset_readiness_snapshot
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(corp_code="001", corp_name="Standalone", stock_code="000001", market="KOSPI"))
        _add_core_compact_metrics(session, "001", 2021, fs_div="OFS")
        session.add(Disclosure(
            rcept_no="20220331000001",
            corp_code="001",
            corp_name="Standalone",
            disc_date=date(2022, 3, 31),
            disc_type="A",
            report_nm="사업보고서 (2021.12)",
            flr_nm="Standalone",
        ))

    out = investor_dataset_readiness_snapshot(year=2021, years_back=1, market="KOSPI")

    assert out["yearly"][0]["financial_eligible_companies"] == 1
    assert out["yearly"][0]["compact_core_companies"] == 1
    assert out["yearly"][0]["compact_core_coverage_pct"] == 100.0
    assert "financial_compact_core_2021" not in out["required_gaps"]
