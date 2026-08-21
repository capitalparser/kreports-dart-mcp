from __future__ import annotations

import pytest
from sqlalchemy.orm import Session


def _seed_notes(temp_engine):
    from kreports.db.models import AccountingNoteChapter, Company

    detailed = (
        "회사는 Alpha SPC의 3,000억원 PF 대출약정과 관련하여 자금보충약정을 "
        "제공하고 있습니다. 상환재원이 부족한 경우 회사는 부족액을 대여 또는 "
        "출자 방식으로 보충하여야 하며 약정기간은 2032년까지입니다. 당기말 현재 "
        "실행된 자금보충액은 없으며 관련 지급보증도 함께 제공하고 있습니다."
        "\n\n이 약정은 프로젝트 사업비와 원리금 상환재원을 지원하기 위한 것입니다."
    )
    brief = "회사는 관계기업에 자금보충약정을 제공하고 있습니다."
    with Session(temp_engine) as session:
        session.add(
            Company(
                corp_code="00000001",
                stock_code="000001",
                corp_name="Alpha",
                market="KOSPI",
                induty_code="35110",
            )
        )
        session.add_all([
            AccountingNoteChapter(
                corp_code="00000001",
                bsns_year=2024,
                fs_div="CFS",
                rcept_no="20250318000001",
                source_type="business_report",
                note_no="31",
                note_title="자금보충약정",
                section_type="other_note",
                body=detailed,
                body_length=len(detailed),
            ),
            AccountingNoteChapter(
                corp_code="00000001",
                bsns_year=2023,
                fs_div="CFS",
                rcept_no="20240318000001",
                source_type="business_report",
                note_no="30",
                note_title="약정사항",
                section_type="other_note",
                body=brief,
                body_length=len(brief),
            ),
            AccountingNoteChapter(
                corp_code="00000001",
                bsns_year=2022,
                fs_div="CFS",
                rcept_no="20230318000001",
                source_type="business_report",
                note_no="29",
                note_title="약정사항",
                section_type="other_note",
                body="회사는 자금보충약정을 체결했습니다.",
                body_length=20,
                full_text_length=10_000,
                full_text_storage_status="externalized",
            ),
        ])
        session.commit()


def _notes_by_year(temp_engine):
    from kreports.db.models import AccountingNoteChapter

    with Session(temp_engine, expire_on_commit=False) as session:
        rows = session.query(AccountingNoteChapter).order_by(
            AccountingNoteChapter.bsns_year.desc()
        ).all()
        for row in rows:
            session.expunge(row)
        return {row.bsns_year: row for row in rows}


def test_disclosure_depth_distinguishes_detailed_brief_and_partial(
    temp_engine,
):
    from kreports.analysis.note_evidence import build_note_evidence

    _seed_notes(temp_engine)
    notes = _notes_by_year(temp_engine)

    detailed = build_note_evidence(
        notes[2024],
        query_terms=["자금보충약정"],
        matched_term="자금보충약정",
        query_keyword="자금보충약정",
    )
    brief = build_note_evidence(
        notes[2023],
        query_terms=["자금보충약정"],
        matched_term="자금보충약정",
        query_keyword="자금보충약정",
    )
    partial = build_note_evidence(
        notes[2022],
        query_terms=["자금보충약정"],
        matched_term="자금보충약정",
        query_keyword="자금보충약정",
    )

    detailed_profile = detailed["disclosure_profile"]
    assert detailed_profile["topic"] == "funding_support"
    assert detailed_profile["level"] == "detailed"
    assert detailed_profile["expression_label"] == "직접 표현"
    assert detailed_profile["observed_dimension_count"] >= 6
    assert "한도·대상 금액" in detailed_profile["observed_items"]
    assert "의무 발생 조건" in detailed_profile["observed_items"]
    assert "당기말 실행·노출 현황" in detailed_profile["observed_items"]
    assert "자금보충약정" in detailed["related_text"]

    assert brief["disclosure_profile"]["level"] == "brief"
    assert partial["text"]["completeness"] == "partial"
    assert "전체 주석 확인 필요" in partial[
        "disclosure_profile"
    ]["level_label"]


def test_note_reference_is_deterministic_and_fails_closed_when_row_changes(
    temp_engine,
):
    from kreports.analysis.note_evidence import (
        NoteReferenceError,
        build_note_ref,
        note_resource_uris,
        resolve_note_ref,
    )
    from kreports.db.models import AccountingNoteChapter

    _seed_notes(temp_engine)
    notes = _notes_by_year(temp_engine)
    note_ref = build_note_ref(notes[2024])

    assert note_ref == build_note_ref(notes[2024])
    assert note_resource_uris(note_ref)["summary"].endswith(note_ref)
    with Session(temp_engine) as session:
        assert resolve_note_ref(
            note_ref,
            session=session,
        ).bsns_year == 2024

    with Session(temp_engine) as session:
        row = session.get(AccountingNoteChapter, notes[2024].id)
        row.body = row.body + " 변경된 문구"
        row.body_hash = None
        session.commit()

    with Session(temp_engine) as session:
        with pytest.raises(NoteReferenceError, match="stale"):
            resolve_note_ref(
                note_ref,
                session=session,
            )


def test_search_enrichment_uses_one_canonical_note_evidence_contract(
    temp_engine,
):
    from kreports.analysis.note_evidence import enrich_note_search_result
    from kreports.analysis.note_search import search_note_disclosing_companies

    _seed_notes(temp_engine)
    raw = search_note_disclosing_companies(
        "자금보충약정",
        year=2024,
        search_mode="exact",
    )
    enriched = enrich_note_search_result(raw)
    record = enriched["companies"][0]["records"][0]

    assert record["note_ref"].startswith("n1-")
    assert record["note_resource_uri"].startswith("kreports://note/")
    assert record["paragraph_resource_uri"].endswith("/paragraph")
    assert record["full_note_resource_uri"].endswith("/page/1")
    assert record["disclosure_level"] == "detailed"
    assert record["observed_disclosure_items"]
    assert "자금보충약정" in record["related_paragraph"]
    assert enriched["note_evidence"]["full_note_loaded_lazily"] is True
