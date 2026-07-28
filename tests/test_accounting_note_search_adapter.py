from datetime import datetime

from kreports.analysis.search_adapter import search_dataset
from kreports.db.engine import get_session
from kreports.db.models import AccountingNoteChapter, Company


def _add_note(*, body: str) -> None:
    with get_session() as session:
        session.add(Company(
            corp_code="90000001",
            stock_code="900001",
            corp_name="주석검색테스트",
            market="KOSPI",
            induty_code="264",
        ))
        session.add(AccountingNoteChapter(
            corp_code="90000001",
            bsns_year=2025,
            fs_div="CFS",
            rcept_no="20260312000001",
            source_type="business_report",
            note_no="7",
            note_title="재고자산",
            section_type="policy",
            body=body,
            body_hash="note-search-test",
            body_length=len(body),
            fetched_at=datetime.utcnow(),
        ))


def _first_record(result: dict) -> dict:
    return result["companies"][0]["records"][0]


def test_accounting_note_keyword_search_uses_late_match_instead_of_body_prefix(temp_engine):
    """Catches a regression that returns an unrelated first 1,200 body characters."""
    del temp_engine
    prefix = "관련 없는 주석 앞부분입니다. " * 120
    matched_sentence = "재고자산은 평균법으로 측정하며 순실현가능가치로 평가한다."
    _add_note(body=prefix + matched_sentence)

    record = _first_record(search_dataset(
        dataset="accounting_note_chapters",
        keyword="재고자산",
    ))

    assert "재고자산" in record["body_excerpt"]
    assert "평균법" in record["body_excerpt"]
    assert record["body_excerpt"] != prefix[:1200]


def test_accounting_note_keyword_search_returns_up_to_three_unique_match_excerpts(temp_engine):
    """Catches a regression that omits excerpts or repeats the same match window."""
    del temp_engine
    repeated_sentence = "재고자산은 평균법으로 산정하고 순실현가능가치와 비교한다."
    _add_note(body=(repeated_sentence + "\n") * 8)

    record = _first_record(search_dataset(
        dataset="accounting_note_chapters",
        keyword="재고자산",
    ))

    excerpts = record["match_excerpts"]
    assert 1 <= len(excerpts) <= 3
    assert all("재고자산" in excerpt for excerpt in excerpts)
    assert len(excerpts) == len(set(excerpts))
