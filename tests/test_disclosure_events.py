from datetime import date

from sqlalchemy.orm import sessionmaker

from kreports.db.models import Company, Disclosure, DisclosureEvent


def test_rebuild_disclosure_events_indexes_capital_raise(temp_engine, monkeypatch):
    import kreports.collector.disclosure_event_indexer as indexer

    monkeypatch.setattr(indexer, "init_db", lambda: None)
    Session = sessionmaker(bind=temp_engine)
    with Session() as session:
        session.add(Company(corp_code="001", corp_name="A", stock_code="000001", market="KOSPI"))
        session.add(Disclosure(
            rcept_no="20250101000001",
            corp_code="001",
            corp_name="A",
            disc_date=date(2025, 1, 1),
            disc_type="B",
            report_nm="주요사항보고서(유상증자결정)",
            flr_nm="A",
        ))
        session.commit()

    out = indexer.rebuild_disclosure_events(year=2025)

    assert out["indexed"] == 1
    with Session() as session:
        event = session.query(DisclosureEvent).one()
        assert event.event_type == "capital_raise"
        assert event.severity_hint == "monitor"


def test_search_disclosure_events_marks_list_only_storage_policy(temp_engine):
    from kreports.analysis.disclosure_events import search_disclosure_events
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(corp_code="001", corp_name="A", stock_code="000001", market="KOSPI"))
        session.add(DisclosureEvent(
            rcept_no="20250101000001",
            corp_code="001",
            event_date=date(2025, 1, 1),
            event_type="capital_raise",
            event_title="주요사항보고서(유상증자결정)",
            severity_hint="monitor",
            source_report_nm="주요사항보고서(유상증자결정)",
        ))

    out = search_disclosure_events(
        start_date="2025-01-01",
        end_date="2025-12-31",
        event_types=["capital_raise"],
    )

    assert out["total_events"] == 1
    assert out["data_quality"]["storage_policy"] == "list_only_preload_body_on_demand"
