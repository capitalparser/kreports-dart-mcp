from datetime import date

from sqlalchemy.orm import sessionmaker

from kreports.db.models import AccountingNoteChapter, Company, Disclosure


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


def test_accounting_policy_changes_keeps_only_exact_annual_filing_receipt_as_proven_evidence(
    temp_engine,
):
    """A changed chapter is usable only with its own company-year annual filing."""
    import kreports.analysis.policy_changes as policy_changes

    Session = sessionmaker(bind=temp_engine)
    with Session() as session:
        session.add(Company(corp_code="001", corp_name="A", stock_code="000001", market="KOSPI"))
        session.add_all([
            Disclosure(
                rcept_no="20240301000001",
                corp_code="001",
                corp_name="A",
                disc_date=date(2024, 3, 1),
                disc_type="A",
                report_nm="사업보고서 (2023.12)",
            ),
            Disclosure(
                rcept_no="20250301000001",
                corp_code="001",
                corp_name="A",
                disc_date=date(2025, 3, 1),
                disc_type="A",
                report_nm="사업보고서 (2024.12)",
            ),
            AccountingNoteChapter(
                corp_code="001", bsns_year=2023, fs_div="CFS",
                rcept_no="20240301000001", source_type="business_report",
                note_no="2", note_title="중요한 회계정책", section_type="policy",
                body="수익은 인도 시점에 인식합니다.", body_hash="a", body_length=16,
            ),
            AccountingNoteChapter(
                corp_code="001", bsns_year=2024, fs_div="CFS",
                rcept_no="20250301000001", source_type="business_report",
                note_no="2", note_title="중요한 회계정책", section_type="policy",
                body="수익은 수행의무 이행 시점에 인식합니다.", body_hash="b", body_length=24,
            ),
        ])
        session.commit()

    out = policy_changes.accounting_policy_changes("001", start_year=2023, end_year=2024)

    changed = out["changed_items"]
    assert changed[0]["rcept_no"] == "20250301000001"
    assert changed[0]["provenance_status"] == "proven_annual_filing"
    assert changed[0]["filing_source"] == {
        "corp_code": "001",
        "corp_name": "A",
        "bsns_year": 2024,
        "fs_div": "CFS",
        "rcept_no": "20250301000001",
        "report_nm": "사업보고서 (2024.12)",
        "section_title": "주석 2 중요한 회계정책",
        "source_table": "accounting_note_chapters",
    }
    assert out["data_quality"]["status"] == "usable"


def test_proven_policy_change_receipt_survives_domain_to_mcp_answer_pack(temp_engine):
    """The same filing receipt must survive each public policy-change boundary."""
    import kreports.analysis.policy_changes as policy_changes
    from kreports.mcp.answer_pack import build_answer_pack
    from kreports.mcp.contracts import build_answer_envelope
    from kreports.mcp.handlers.auditor import _enrich_policy_change_evidence

    Session = sessionmaker(bind=temp_engine)
    with Session() as session:
        session.add(Company(corp_code="001", corp_name="A", stock_code="000001", market="KOSPI"))
        session.add_all([
            Disclosure(
                rcept_no="20240301000001", corp_code="001", corp_name="A",
                disc_date=date(2024, 3, 1), disc_type="A",
                report_nm="사업보고서 (2023.12)",
            ),
            Disclosure(
                rcept_no="20250301000001", corp_code="001", corp_name="A",
                disc_date=date(2025, 3, 1), disc_type="A",
                report_nm="사업보고서 (2024.12)",
            ),
            AccountingNoteChapter(
                corp_code="001", bsns_year=2023, fs_div="CFS",
                rcept_no="20240301000001", source_type="business_report",
                note_no="2", note_title="정책", section_type="policy",
                body="기존 정책", body_hash="before", body_length=5,
            ),
            AccountingNoteChapter(
                corp_code="001", bsns_year=2024, fs_div="CFS",
                rcept_no="20250301000001", source_type="business_report",
                note_no="2", note_title="정책", section_type="policy",
                body="변경 정책", body_hash="after", body_length=5,
            ),
        ])
        session.commit()

    domain = policy_changes.accounting_policy_changes("001", start_year=2023, end_year=2024)
    public = _enrich_policy_change_evidence({
        **domain,
        "subject": {"corp_code": "001", "corp_name": "A"},
    })
    envelope = build_answer_envelope("get_accounting_policy_changes", public)
    pack = build_answer_pack("get_accounting_policy_changes", public)

    assert domain["changed_items"][0]["rcept_no"] == "20250301000001"
    assert [item.rcept_no for item in envelope.evidence] == ["20250301000001"]
    table = next(table for table in pack["tables"] if table["id"] == "accounting_policy_changes")
    assert table["rows"][0]["rcept_no"] == "20250301000001"
    assert [source["rcept_no"] for source in pack["sources"]] == ["20250301000001"]


def test_accounting_policy_changes_marks_malformed_foreign_and_wrong_year_receipts_limited(
    temp_engine,
):
    """No alternate filing may be borrowed for an unproven changed chapter."""
    import kreports.analysis.policy_changes as policy_changes

    Session = sessionmaker(bind=temp_engine)
    with Session() as session:
        session.add_all([
            Company(corp_code="001", corp_name="A", stock_code="000001", market="KOSPI"),
            Company(corp_code="002", corp_name="B", stock_code="000002", market="KOSPI"),
            Disclosure(
                rcept_no="20250301000001", corp_code="001", corp_name="A",
                disc_date=date(2025, 3, 1), disc_type="A",
                report_nm="사업보고서 (2024.12)",
            ),
            Disclosure(
                rcept_no="20250302000002", corp_code="002", corp_name="B",
                disc_date=date(2025, 3, 2), disc_type="A",
                report_nm="사업보고서 (2024.12)",
            ),
            Disclosure(
                rcept_no="20240301000001", corp_code="001", corp_name="A",
                disc_date=date(2024, 3, 1), disc_type="A",
                report_nm="사업보고서 (2023.12)",
            ),
            AccountingNoteChapter(
                corp_code="001", bsns_year=2021, fs_div="CFS", rcept_no="bad-receipt",
                source_type="business_report", note_no="2", note_title="정책", section_type="policy",
                body="기존 정책", body_hash="old", body_length=4,
            ),
            AccountingNoteChapter(
                corp_code="001", bsns_year=2022, fs_div="CFS", rcept_no="20250302000002",
                source_type="business_report", note_no="2", note_title="정책", section_type="policy",
                body="외부 회사 접수번호", body_hash="foreign", body_length=10,
            ),
            AccountingNoteChapter(
                corp_code="001", bsns_year=2023, fs_div="CFS", rcept_no="20250301000001",
                source_type="business_report", note_no="2", note_title="정책", section_type="policy",
                body="다른 연도 접수번호", body_hash="wrong-year", body_length=10,
            ),
        ])
        session.commit()

    out = policy_changes.accounting_policy_changes("001", start_year=2021, end_year=2023)

    assert [item["provenance_status"] for item in out["changes"]] == [
        "invalid_receipt", "unproven_annual_filing", "unproven_annual_filing",
    ]
    assert all(item.get("filing_source") is None for item in out["changes"])
    assert out["data_quality"]["status"] == "limited"
    assert any("검증" in limitation for limitation in out["data_quality"]["limitations"])


def test_accounting_policy_changes_rejects_receipt_date_mismatched_to_annual_disclosure(
    temp_engine,
):
    """A plausible receipt date cannot cite a disclosure recorded on another day."""
    import kreports.analysis.policy_changes as policy_changes

    Session = sessionmaker(bind=temp_engine)
    with Session() as session:
        session.add(Company(corp_code="001", corp_name="A", stock_code="000001", market="KOSPI"))
        session.add_all([
            Disclosure(
                rcept_no="20240301000001", corp_code="001", corp_name="A",
                disc_date=date(2024, 3, 1), disc_type="A",
                report_nm="사업보고서 (2023.12)",
            ),
            Disclosure(
                rcept_no="20250305000001", corp_code="001", corp_name="A",
                disc_date=date(2025, 3, 1), disc_type="A",
                report_nm="사업보고서 (2024.12)",
            ),
            AccountingNoteChapter(
                corp_code="001", bsns_year=2023, fs_div="CFS",
                rcept_no="20240301000001", source_type="business_report",
                note_no="2", note_title="정책", section_type="policy",
                body="기존 정책", body_hash="before", body_length=5,
            ),
            AccountingNoteChapter(
                corp_code="001", bsns_year=2024, fs_div="CFS",
                rcept_no="20250305000001", source_type="business_report",
                note_no="2", note_title="정책", section_type="policy",
                body="변경 정책", body_hash="after", body_length=5,
            ),
        ])
        session.commit()

    out = policy_changes.accounting_policy_changes("001", start_year=2023, end_year=2024)

    changed = out["changed_items"][0]
    assert changed["provenance_status"] == "unproven_annual_filing"
    assert changed["filing_source"] is None
    assert out["data_quality"]["status"] == "limited"


def test_accounting_policy_changes_rejects_contaminated_chapter_receipt(
    temp_engine,
):
    """A parent receipt embedded in a chapter identifier is not canonical evidence."""
    import kreports.analysis.policy_changes as policy_changes
    from kreports.mcp.handlers.auditor import _enrich_policy_change_evidence

    Session = sessionmaker(bind=temp_engine)
    with Session() as session:
        session.add(Company(corp_code="001", corp_name="A", stock_code="000001", market="KOSPI"))
        session.add_all([
            Disclosure(
                rcept_no="20240301000001", corp_code="001", corp_name="A",
                disc_date=date(2024, 3, 1), disc_type="A",
                report_nm="사업보고서 (2023.12)",
            ),
            Disclosure(
                rcept_no="20250301000001", corp_code="001", corp_name="A",
                disc_date=date(2025, 3, 1), disc_type="A",
                report_nm="사업보고서 (2024.12)",
            ),
            AccountingNoteChapter(
                corp_code="001", bsns_year=2023, fs_div="CFS",
                rcept_no="20240301000001", source_type="business_report",
                note_no="2", note_title="정책", section_type="policy",
                body="기존 정책", body_hash="before", body_length=5,
            ),
            AccountingNoteChapter(
                corp_code="001", bsns_year=2024, fs_div="CFS",
                rcept_no="synthetic-20250301000001-attachment",
                source_type="business_report", note_no="2", note_title="정책",
                section_type="policy", body="변경 정책", body_hash="after",
                body_length=5,
            ),
        ])
        session.commit()

    out = policy_changes.accounting_policy_changes("001", start_year=2023, end_year=2024)
    changed = out["changed_items"][0]
    public = _enrich_policy_change_evidence(out)

    assert changed["rcept_no"] == "synthetic-20250301000001-attachment"
    assert changed["provenance_status"] == "invalid_receipt"
    assert changed["filing_source"] is None
    assert public.get("confirmed_facts") is None
    assert public["data_quality"]["status"] == "limited"


def test_accounting_policy_changes_rejects_contaminated_disclosure_receipt(
    temp_engine,
):
    """A parent receipt embedded in a disclosure identifier cannot prove a chapter."""
    import kreports.analysis.policy_changes as policy_changes
    from kreports.mcp.handlers.auditor import _enrich_policy_change_evidence

    Session = sessionmaker(bind=temp_engine)
    with Session() as session:
        session.add(Company(corp_code="001", corp_name="A", stock_code="000001", market="KOSPI"))
        session.add_all([
            Disclosure(
                rcept_no="20240301000001", corp_code="001", corp_name="A",
                disc_date=date(2024, 3, 1), disc_type="A",
                report_nm="사업보고서 (2023.12)",
            ),
            Disclosure(
                rcept_no="synthetic-20250301000001-attachment",
                corp_code="001", corp_name="A", disc_date=date(2025, 3, 1),
                disc_type="A", report_nm="사업보고서 (2024.12)",
            ),
            AccountingNoteChapter(
                corp_code="001", bsns_year=2023, fs_div="CFS",
                rcept_no="20240301000001", source_type="business_report",
                note_no="2", note_title="정책", section_type="policy",
                body="기존 정책", body_hash="before", body_length=5,
            ),
            AccountingNoteChapter(
                corp_code="001", bsns_year=2024, fs_div="CFS",
                rcept_no="20250301000001", source_type="business_report",
                note_no="2", note_title="정책", section_type="policy",
                body="변경 정책", body_hash="after", body_length=5,
            ),
        ])
        session.commit()

    out = policy_changes.accounting_policy_changes("001", start_year=2023, end_year=2024)
    changed = out["changed_items"][0]
    public = _enrich_policy_change_evidence(out)

    assert changed["rcept_no"] == "20250301000001"
    assert changed["provenance_status"] == "unproven_annual_filing"
    assert changed["filing_source"] is None
    assert public.get("confirmed_facts") is None
    assert public["data_quality"]["status"] == "limited"


def test_accounting_policy_changes_rejects_whitespace_wrapped_latest_disclosure_and_chapter(
    temp_engine,
):
    """Whitespace is part of each stored receipt identity and cannot produce sources."""
    import kreports.analysis.policy_changes as policy_changes
    from kreports.mcp.answer_pack import build_answer_pack
    from kreports.mcp.handlers.auditor import _enrich_policy_change_evidence

    Session = sessionmaker(bind=temp_engine)
    with Session() as session:
        session.add(Company(corp_code="001", corp_name="A", stock_code="000001", market="KOSPI"))
        session.add_all([
            Disclosure(
                rcept_no="20240301000001", corp_code="001", corp_name="A",
                disc_date=date(2024, 3, 1), disc_type="A", report_nm="사업보고서 (2023.12)",
            ),
            Disclosure(
                rcept_no=" 20250301000001 ", corp_code="001", corp_name="A",
                disc_date=date(2025, 3, 1), disc_type="A", report_nm="사업보고서 (2024.12)",
            ),
            AccountingNoteChapter(
                corp_code="001", bsns_year=2023, fs_div="CFS", rcept_no="20240301000001",
                source_type="business_report", note_no="2", note_title="정책", section_type="policy",
                body="기존 정책", body_hash="before", body_length=5,
            ),
            AccountingNoteChapter(
                corp_code="001", bsns_year=2024, fs_div="CFS", rcept_no=" 20250301000001 ",
                source_type="business_report", note_no="2", note_title="정책", section_type="policy",
                body="변경 정책", body_hash="after", body_length=5,
            ),
        ])
        session.commit()

    domain = policy_changes.accounting_policy_changes("001", start_year=2023, end_year=2024)
    public = _enrich_policy_change_evidence({**domain, "subject": {"corp_code": "001", "corp_name": "A"}})
    pack = build_answer_pack("get_accounting_policy_changes", public)
    changed = domain["changed_items"][0]

    assert changed["provenance_status"] == "invalid_receipt"
    assert changed["filing_source"] is None
    assert public.get("confirmed_facts") is None
    assert pack["sources"] == []
