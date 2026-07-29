from datetime import date

from sqlalchemy.orm import sessionmaker

from kreports.db.models import Company, Disclosure, SourceDocument


def test_raw_annual_report_coverage_counts_latest_only(temp_engine):
    import kreports.analysis.raw_coverage as raw_coverage

    Session = sessionmaker(bind=temp_engine)
    with Session() as session:
        session.add_all([
            Company(corp_code="001", corp_name="A", stock_code="000001", market="KOSPI"),
            Disclosure(
                rcept_no="20220301000001",
                corp_code="001",
                corp_name="A",
                disc_date=date(2022, 3, 1),
                disc_type="A",
                report_nm="사업보고서 (2021.12)",
                flr_nm="A",
            ),
            Disclosure(
                rcept_no="20220401000002",
                corp_code="001",
                corp_name="A",
                disc_date=date(2022, 4, 1),
                disc_type="A",
                report_nm="[기재정정]사업보고서 (2021.12)",
                flr_nm="A",
            ),
            SourceDocument(
                rcept_no="20220401000002",
                corp_code="001",
                bsns_year=2021,
                source_type="business_report",
                report_nm="[기재정정]사업보고서 (2021.12)",
                content_type="xml",
                raw_content="",
                doc_hash="h",
                storage_uri="gs://bucket/a.gz",
                storage_status="externalized",
            ),
        ])
        session.commit()

    out = raw_coverage.raw_annual_report_coverage(
        start_filing_year=2022,
        end_filing_year=2022,
        markets=["KOSPI"],
    )

    assert out["rows"][0]["filing_year"] == 2022
    assert out["rows"][0]["latest_reports"] == 1
    assert out["rows"][0]["raw_externalized"] == 1
    assert out["rows"][0]["raw_missing"] == 0
    assert out["totals"]["coverage_pct"] == 100.0
