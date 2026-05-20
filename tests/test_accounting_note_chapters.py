from sqlalchemy import inspect

from kreports.analysis.queries import extract_accounting_note_chapters
from kreports.db.models import AccountingNoteChapter, Base


def test_accounting_note_chapter_schema(temp_engine):
    Base.metadata.create_all(bind=temp_engine)
    inspector = inspect(temp_engine)

    assert "accounting_note_chapters" in inspector.get_table_names()
    columns = {column["name"] for column in inspector.get_columns("accounting_note_chapters")}
    assert {
        "corp_code",
        "bsns_year",
        "fs_div",
        "rcept_no",
        "dcm_no",
        "source_type",
        "note_no",
        "note_title",
        "section_type",
        "body",
        "body_hash",
        "body_length",
        "fetched_at",
    }.issubset(columns)

    constraints = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("accounting_note_chapters")
    }
    assert "uq_accounting_note_chapter" in constraints


def test_accounting_note_chapter_indexes():
    index_names = {index.name for index in AccountingNoteChapter.__table__.indexes}
    assert "idx_note_chapter_corp_year" in index_names
    assert "idx_note_chapter_section_type" in index_names


def test_extracts_basis_policy_estimate_chapters():
    note_section = """
    <TITLE>주석</TITLE>
    <P>1. 일반사항</P><P>회사의 개요입니다.</P>
    <P>2. 재무제표 작성기준</P><P>연결재무제표는 한국채택국제회계기준에 따라 작성되었습니다.</P>
    <P>3. 중요한 회계정책</P><P>수익은 고객과의 계약에서 수행의무가 이행될 때 인식합니다.</P>
    <P>4. 중요한 회계추정 및 판단</P><P>손상검사와 이연법인세자산 인식에는 경영진의 판단이 필요합니다.</P>
    <P>5. 영업부문</P><P>다음 주석입니다.</P>
    """

    chapters = extract_accounting_note_chapters(note_section)

    assert [chapter["note_no"] for chapter in chapters] == ["2", "3", "4"]
    assert [chapter["section_type"] for chapter in chapters] == [
        "basis",
        "policy",
        "estimate_judgment",
    ]
    assert chapters[0]["note_title"] == "재무제표 작성기준"
    assert "수행의무" in chapters[1]["body"]
    assert "이연법인세자산" in chapters[2]["body"]


def test_extracts_combined_basis_and_policy_chapter_as_policy():
    note_section = """
    <TITLE>주석</TITLE>
    <P>1. 회사의 개요</P><P>개요입니다.</P>
    <P>2. 재무제표 작성기준 및 중요한 회계정책</P>
    <P>회사는 한국채택국제회계기준을 적용하고 수익인식 정책을 기술합니다.</P>
    <P>3. 금융위험관리</P><P>위험관리 내용입니다.</P>
    """

    chapters = extract_accounting_note_chapters(note_section)

    assert len(chapters) == 1
    assert chapters[0]["note_no"] == "2"
    assert chapters[0]["section_type"] == "policy"
    assert "수익인식" in chapters[0]["body"]
