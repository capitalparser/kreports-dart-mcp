from sqlalchemy.orm import sessionmaker

from kreports.db.models import Company, ReportSection


def test_kam_lifecycle_marks_new_and_repeated_topics(temp_engine, monkeypatch):
    import kreports.analysis.kam_lifecycle as lifecycle

    monkeypatch.setattr(lifecycle, "engine", temp_engine)
    Session = sessionmaker(bind=temp_engine)
    with Session() as session:
        session.add(Company(corp_code="001", corp_name="A", stock_code="000001", market="KOSPI"))
        session.add_all([
            ReportSection(
                rcept_no="20240301000001",
                corp_code="001",
                bsns_year=2023,
                source_type="audit_report",
                section_key="kam",
                section_title="수익인식",
                body_text="수익인식에 대한 감사절차로 문서검사를 수행함",
                body_hash="a",
                body_length=30,
                ordinal=0,
            ),
            ReportSection(
                rcept_no="20250301000001",
                corp_code="001",
                bsns_year=2024,
                source_type="audit_report",
                section_key="kam",
                section_title="수익인식",
                body_text="수익인식에 대한 감사절차로 표본검사와 분석적 절차를 수행함",
                body_hash="b",
                body_length=40,
                ordinal=0,
            ),
        ])
        session.commit()

    out = lifecycle.kam_lifecycle_for_company("001", start_year=2023, end_year=2024)

    assert out["events"][0]["status"] == "new"
    assert out["events"][1]["status"] == "repeated_changed"
    assert out["events"][1]["topic"] == "revenue"
