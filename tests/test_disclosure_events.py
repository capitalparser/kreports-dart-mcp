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
