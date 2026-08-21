from __future__ import annotations


def _seed_note_search_fixture():
    from kreports.db.engine import get_session
    from kreports.db.models import (
        AccountingNoteChapter,
        Company,
    )

    long_prefix = "일반 약정 설명 " * 180
    with get_session() as session:
        session.add_all([
            Company(
                corp_code="00000001",
                stock_code="000001",
                corp_name="Alpha",
                market="KOSPI",
                induty_code="35110",
            ),
            Company(
                corp_code="00000002",
                stock_code="000002",
                corp_name="Beta",
                market="KOSPI",
                induty_code="35120",
            ),
            Company(
                corp_code="00000003",
                stock_code="000003",
                corp_name="Gamma",
                market="KOSDAQ",
                induty_code="26410",
            ),
            AccountingNoteChapter(
                corp_code="00000001",
                bsns_year=2024,
                fs_div="CFS",
                rcept_no="20250318000001",
                source_type="business_report",
                note_no="31",
                note_title="약정사항",
                section_type="other_note",
                body=(
                    long_prefix
                    + "회사는 자금보충약정을 체결하였습니다."
                ),
            ),
            AccountingNoteChapter(
                corp_code="00000001",
                bsns_year=2024,
                fs_div="CFS",
                rcept_no="20250318000002",
                source_type="business_report",
                note_no="32",
                note_title="추가 약정",
                section_type="other_note",
                body="자금보충약정에 따른 한도를 공시합니다.",
            ),
            AccountingNoteChapter(
                corp_code="00000002",
                bsns_year=2024,
                fs_div="CFS",
                rcept_no="20250318000003",
                source_type="business_report",
                note_no="28",
                note_title="자금 보충 약정",
                section_type="other_note",
                body="유동성 지원 조건을 설명합니다.",
            ),
        ])


def test_note_search_has_true_totals_pagination_and_centered_excerpt(
    temp_engine,
):
    from kreports.analysis.note_search import (
        search_note_disclosing_companies,
    )

    _seed_note_search_fixture()

    first = search_note_disclosing_companies(
        "자금보충약정",
        year=2024,
        search_mode="normalized",
        limit=1,
        offset=0,
    )
    second = search_note_disclosing_companies(
        "자금보충약정",
        year=2024,
        search_mode="normalized",
        limit=1,
        offset=1,
    )

    assert first["matched_company_count"] == 2
    assert first["matched_record_count"] == 3
    assert first["returned_company_count"] == 1
    assert first["has_more"] is True
    assert first["next_offset"] == 1
    assert second["returned_company_count"] == 1
    assert second["has_more"] is False

    record = first["companies"][0]["records"][0]
    assert "자금보충약정" in record["body_excerpt"]
    assert record["excerpt_start"] > 0
    assert record["matched_field"] == "body"
    assert record["source_url"].endswith(
        record["rcept_no"]
    )


def test_exact_normalized_and_synonym_modes_are_distinct(
    temp_engine,
):
    from kreports.analysis.note_search import (
        search_note_disclosing_companies,
    )

    _seed_note_search_fixture()

    exact = search_note_disclosing_companies(
        "자금보충약정",
        year=2024,
        search_mode="exact",
    )
    normalized = search_note_disclosing_companies(
        "자금보충약정",
        year=2024,
        search_mode="normalized",
    )
    synonym = search_note_disclosing_companies(
        "유동성보충약정",
        year=2024,
        search_mode="synonym",
    )

    assert exact["matched_company_count"] == 1
    assert normalized["matched_company_count"] == 2
    assert synonym["matched_company_count"] == 2


def test_sql_wildcards_are_literal_not_match_all(
    temp_engine,
):
    from kreports.analysis.note_search import (
        search_note_disclosing_companies,
    )

    _seed_note_search_fixture()

    result = search_note_disclosing_companies(
        "%",
        search_mode="exact",
    )

    assert result["matched_company_count"] == 0
    assert result["matched_record_count"] == 0
