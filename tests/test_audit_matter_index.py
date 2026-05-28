from sqlalchemy.orm import sessionmaker

from kreports.db.models import AuditMatterItem, Company, ReportSection


def test_rebuild_audit_matter_items_from_report_sections(temp_engine, monkeypatch):
    import kreports.collector.audit_matter_indexer as indexer

    monkeypatch.setattr(indexer, "init_db", lambda: None)
    Session = sessionmaker(bind=temp_engine)
    with Session() as session:
        session.add(Company(corp_code="001", corp_name="A", stock_code="000001", market="KOSPI", induty_code="264"))
        session.add(ReportSection(
            rcept_no="20250301000001",
            corp_code="001",
            bsns_year=2024,
            source_type="audit_report",
            section_key="emphasis",
            section_title="강조사항",
            body_text="계속기업 관련 중요한 불확실성이 존재합니다.",
            body_hash="h1",
            body_length=24,
            ordinal=0,
        ))
        session.commit()

    out = indexer.rebuild_audit_matter_items(year=2024)

    assert out["inserted"] == 1
    with Session() as session:
        item = session.query(AuditMatterItem).one()
        assert item.matter_type == "emphasis"
        assert item.severity_hint == "high"
        assert "going_concern" in item.topic_tags


def test_search_audit_report_matters_uses_audit_matter_items(temp_engine):
    import kreports.analysis.api as api

    Session = sessionmaker(bind=temp_engine)
    with Session() as session:
        session.add(Company(corp_code="001", corp_name="A", stock_code="000001", market="KOSPI", induty_code="264"))
        session.add(AuditMatterItem(
            rcept_no="20250301000001",
            corp_code="001",
            bsns_year=2024,
            matter_type="going_concern",
            matter_title="계속기업 관련 중요한 불확실성",
            matter_text="계속기업 관련 중요한 불확실성이 존재합니다.",
            matter_hash="h1",
            matter_length=24,
            topic_tags='["going_concern"]',
            severity_hint="high",
            source_type="audit_report",
            section_ordinal=0,
        ))
        session.commit()

    out = api.search_audit_report_matters(
        company="000001",
        year=2024,
        section_keys=["going_concern"],
    )

    assert out["data_quality"]["source"] == "audit_matter_items"
    assert out["total_companies"] == 1
    assert out["companies"][0]["matter_counts"]["going_concern"] == 1
