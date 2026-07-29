from sqlalchemy.orm import sessionmaker

from kreports.db.models import AccountingNoteChapter, Company


def test_accounting_policy_changes_detects_changed_text(temp_engine):
    import kreports.analysis.policy_changes as policy_changes

    Session = sessionmaker(bind=temp_engine)
    with Session() as session:
        session.add(Company(corp_code="001", corp_name="A", stock_code="000001", market="KOSPI"))
        session.add_all([
            AccountingNoteChapter(
                corp_code="001",
                bsns_year=2023,
                fs_div="CFS",
                rcept_no="20240301000001",
                source_type="business_report",
                note_no="2",
                note_title="중요한 회계정책",
                section_type="policy",
                body="수익은 인도 시점에 인식합니다.",
                body_hash="a",
                body_length=16,
            ),
            AccountingNoteChapter(
                corp_code="001",
                bsns_year=2024,
                fs_div="CFS",
                rcept_no="20250301000001",
                source_type="business_report",
                note_no="2",
                note_title="중요한 회계정책",
                section_type="policy",
                body="수익은 수행의무 이행 시점에 인식합니다.",
                body_hash="b",
                body_length=24,
            ),
        ])
        session.commit()

    out = policy_changes.accounting_policy_changes("001", start_year=2023, end_year=2024)

    assert out["changes"][0]["change_type"] == "new"
    assert out["changes"][1]["change_type"] == "changed"
    assert out["change_count"] == 1
