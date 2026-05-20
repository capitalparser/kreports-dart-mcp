from sqlalchemy import inspect

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
