from datetime import date, datetime

from sqlalchemy import text

from kreports.db.models import Company, Disclosure, DisclosureEvent, SourceDocument


def _add_core_compact_metrics(session, corp_code: str, year: int) -> None:
    for metric in ("revenue", "profit_loss", "operating_cash_flow", "assets", "liabilities", "equity"):
        session.execute(
            text("""
            INSERT INTO financial_facts_compact
            (corp_code, bsns_year, fs_div, metric_key, metric_name, amount,
             source_account_id, source_account_nm, fetched_at)
            VALUES
            (:corp_code, :year, 'CFS', :metric, :metric, 100, :metric, :metric, CURRENT_TIMESTAMP)
            """),
            {"corp_code": corp_code, "year": year, "metric": metric},
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
