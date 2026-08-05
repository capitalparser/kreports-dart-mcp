from datetime import date, datetime

from kreports.analysis.search_adapter import search_dataset
from kreports.db.engine import get_session
from kreports.db.models import AccountingNoteChapter, Company, Disclosure, SourceDocument


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


def test_accounting_note_revenue_search_ranks_recognition_evidence_before_generic_mentions(temp_engine):
    """Catches generic statement and FX mentions outranking revenue-recognition evidence."""
    del temp_engine
    recognition = (
        "고객과의 계약에서 재화의 판매로 인한 수익은 재화의 통제가 고객에게 이전되는 "
        "시점에 인식하며, 매출장려활동에 따른 변동대가를 추정한다."
    )
    _add_note(body=" ".join([
        "손익계산서에는 수익과 비용을 표시한다.",
        "연결회사간 수익과 비용은 제거한다.",
        "해외사업장의 자산과 부채는 평균환율로 환산한다.",
        recognition,
    ]))

    record = _first_record(search_dataset(
        dataset="accounting_note_chapters",
        keyword="수익",
    ))

    assert record["match_excerpts"][0] == recognition
    assert record["body_excerpt"] == recognition


def test_accounting_note_revenue_search_weights_sale_evidence_above_generic_recognition(temp_engine):
    """Catches a generic recognition mention winning a flat-score source-order tie."""
    del temp_engine
    generic = "수익과 비용은 발생주의에 따라 인식한다."
    sale_revenue = "재화의 판매로 인한 수익은 인도 시점에 회계처리한다."
    _add_note(body=f"{generic} {sale_revenue}")

    record = _first_record(search_dataset(
        dataset="accounting_note_chapters",
        keyword="수익",
    ))

    assert record["match_excerpts"][0] == sale_revenue
    assert record["body_excerpt"] == sale_revenue


def test_note_search_company_matrix_requires_canonical_annual_source_binding(temp_engine):
    """A valid-looking receipt alone must not promote a cached keyword match."""
    from kreports.mcp.handlers.search import handle_search_dataset
    from kreports.mcp.input_models import SearchDatasetInput

    del temp_engine
    with get_session() as session:
        session.add_all([
            Company(corp_code="90000001", corp_name="Verified", market="KOSPI", induty_code="264"),
            Company(corp_code="90000002", corp_name="Invalid receipt", market="KOSPI", induty_code="264"),
            Company(corp_code="90000003", corp_name="Quarterly source", market="KOSPI", induty_code="264"),
            Company(corp_code="90000004", corp_name="Foreign source", market="KOSPI", induty_code="264"),
            Company(corp_code="90000005", corp_name="Foreign owner", market="KOSPI", induty_code="264"),
            Disclosure(rcept_no="20260312000001", corp_code="90000001", corp_name="Verified", disc_date=date(2026, 3, 12), disc_type="A", report_nm="사업보고서 (2025.12)"),
            Disclosure(rcept_no="20260312000003", corp_code="90000003", corp_name="Quarterly source", disc_date=date(2026, 3, 12), disc_type="A", report_nm="사업보고서 (2025.12)"),
            Disclosure(rcept_no="20260312000004", corp_code="90000005", corp_name="Foreign owner", disc_date=date(2026, 3, 12), disc_type="A", report_nm="사업보고서 (2025.12)"),
            SourceDocument(rcept_no="20260312000001", corp_code="90000001", bsns_year=2025, source_type="business_report", report_nm="사업보고서 (2025.12)", raw_content="<xml/>", doc_hash="a" * 40),
            SourceDocument(rcept_no="20260312000003", corp_code="90000003", bsns_year=2025, source_type="business_report", report_nm="분기보고서 (2025.12)", raw_content="<xml/>", doc_hash="b" * 40),
            SourceDocument(rcept_no="20260312000004", corp_code="90000005", bsns_year=2025, source_type="business_report", report_nm="사업보고서 (2025.12)", raw_content="<xml/>", doc_hash="c" * 40),
            AccountingNoteChapter(corp_code="90000001", bsns_year=2025, fs_div="CFS", rcept_no="20260312000001", source_type="business_report", note_no="1", note_title="재고자산", section_type="policy", body="재고자산은 원가로 측정합니다."),
            AccountingNoteChapter(corp_code="90000002", bsns_year=2025, fs_div="CFS", rcept_no="attachment-only", source_type="business_report", note_no="1", note_title="재고자산", section_type="policy", body="재고자산은 원가로 측정합니다."),
            AccountingNoteChapter(corp_code="90000003", bsns_year=2025, fs_div="CFS", rcept_no="20260312000003", source_type="business_report", note_no="1", note_title="재고자산", section_type="policy", body="재고자산은 원가로 측정합니다."),
            AccountingNoteChapter(corp_code="90000004", bsns_year=2025, fs_div="CFS", rcept_no="20260312000004", source_type="business_report", note_no="1", note_title="재고자산", section_type="policy", body="재고자산은 원가로 측정합니다."),
        ])

    result = handle_search_dataset(SearchDatasetInput(
        dataset="accounting_note_chapters", keyword="재고자산", year=2025,
        market="KOSPI", induty_prefix="264", limit=4,
    ))

    matrix = result["note_disclosure_company_matrix"]
    assert matrix["scope"] == {
        "keyword": "재고자산", "year": 2025, "market": "KOSPI", "induty_prefix": "264",
    }
    assert matrix["configured_limit"] == 4
    assert matrix["returned_company_count"] == 4
    assert matrix["is_exhaustive"] is False
    by_code = {item["corp_code"]: item for item in matrix["companies"]}
    assert by_code["90000001"] == {
        "corp_code": "90000001", "corp_name": "Verified", "corp_name_truncated": False,
        "market": "KOSPI", "market_truncated": False,
        "induty_code": "264", "induty_code_truncated": False, "year": 2025,
        "matched_years": [2025], "matched_years_truncated": False,
        "matched_years_omitted_count": 0,
        "match_status": "verified_annual_filing_match", "record_count": 1,
        "match_status_label": "검증된 연간 공시 일치",
        "canonical_rcept_no": "20260312000001", "canonical_note_title": "재고자산",
        "canonical_note_title_truncated": False,
        "display_truncated": False,
    }
    for corp_code in ("90000002", "90000003", "90000004"):
        assert by_code[corp_code]["match_status"] == "unverified_cache_match"
        assert by_code[corp_code]["canonical_rcept_no"] is None
        assert by_code[corp_code]["canonical_note_title"] is None
    assert "공시 완전성" in matrix["limitations"][0]
    assert result["data_quality"]["status"] == "limited"
    assert "확인 1건" in result["data_quality"]["coverage_note"]
    assert "미검증 캐시 일치 3건" in result["data_quality"]["coverage_note"]
    assert [fact["source"]["corp_code"] for fact in result["confirmed_facts"]] == [
        "90000001",
    ]

    from kreports.mcp.answer_pack import build_answer_pack

    pack = build_answer_pack("search_dataset", result)
    assert pack is not None
    evidence = next(table for table in pack["tables"] if table["id"] == "accounting_note_evidence")
    assert [row["rcept_no"] for row in evidence["rows"]] == ["20260312000001"]
