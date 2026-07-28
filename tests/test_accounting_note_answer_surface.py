from kreports.mcp.answer_pack import build_answer_pack
from kreports.mcp.contracts import build_answer_envelope
from kreports.mcp.handlers.search import _enrich_accounting_note_search
from kreports.mcp.renderers import render_answer


_MATCHED_EXCERPT = "재고자산은 평균법으로 측정하며 순실현가능가치로 감액합니다."


def _matched_note_result(*, rcept_no: str = "20250312000001") -> dict:
    return {
        "query": {
            "dataset": "accounting_note_chapters",
            "company": "테스트회사",
            "year": 2025,
            "keyword": "재고자산",
            "fs_div": "CFS",
        },
        "subject": {"corp_code": "00999999", "corp_name": "테스트회사"},
        "total_companies": 1,
        "total_records": 1,
        "companies": [{
            "corp_code": "00999999",
            "corp_name": "테스트회사",
            "records": [{
                "year": 2025,
                "fs_div": "CFS",
                "note_no": "7",
                "note_title": "재고자산",
                "rcept_no": rcept_no,
                "match_excerpts": [_MATCHED_EXCERPT, _MATCHED_EXCERPT],
                "body_excerpt": "무시되어야 하는 앞부분",
            }],
        }],
        "data_quality": {"status": "usable", "source": "accounting_note_chapters"},
    }


def test_note_enrichment_binds_keyword_passage_to_auditor_fact_and_dart_receipt():
    """Removing passage provenance or inventory guidance must fail this contract."""
    result = _enrich_accounting_note_search(_matched_note_result())

    assert result["data_quality"]["status"] == "usable"
    assert result["confirmed_facts"][0]["excerpt"] == _MATCHED_EXCERPT
    assert result["confirmed_facts"][0]["statement"] == (
        "주석 7 재고자산: 재고자산은 평균법으로 측정하며 순실현가능가치로 감액합니다."
    )
    assert result["confirmed_facts"][0]["source"]["rcept_no"] == "20250312000001"
    assert result["confirmed_facts"][0]["source"]["source_table"] == "accounting_note_chapters"
    assert result["confirmed_facts"][0]["source"]["report_nm"] == "사업보고서"
    assert result["analysis"][0]["perspective"] == "auditor"
    assert "재고자산" in result["analysis"][0]["statement"]
    assert result["next_checks"] == [
        "관련 잔액과 비교표시 금액을 원 공시와 대조하세요.",
        "주요 회계추정 입력과 근거를 검토하세요.",
        "해당 주석 전문과 관련 공시의 후속 변경사항을 검토하세요.",
    ]


def test_note_enrichment_marks_matched_uncitable_row_limited():
    """Treating a matched row without a canonical DART receipt as usable is a bug."""
    result = _enrich_accounting_note_search(_matched_note_result(rcept_no="attachment-only"))

    assert result["data_quality"]["status"] == "limited"
    assert result["confirmed_facts"][0]["excerpt"] == _MATCHED_EXCERPT
    assert "14자리" in result["data_quality"]["coverage_note"]


def test_note_enrichment_labels_report_and_nested_note_reference_for_citation():
    """Flattening a nested note heading makes the filing citation hard to review."""
    raw = _matched_note_result()
    raw["query"]["keyword"] = "재무제표"
    record = raw["companies"][0]["records"][0]
    record["note_no"] = "2"
    record["note_title"] = "1. 재무제표 작성기준"
    record["match_excerpts"] = ["재무제표는 한국채택국제회계기준에 따라 작성합니다."]

    result = _enrich_accounting_note_search(raw)

    assert result["confirmed_facts"][0]["source"]["report_nm"] == "사업보고서"
    assert result["confirmed_facts"][0]["note_reference"] == "주석 2 · 1 재무제표 작성기준"
    assert result["confirmed_facts"][0]["source"]["section_title"] == "주석 2 · 1 재무제표 작성기준"


def test_note_enrichment_marks_empty_cache_missing_without_claiming_filing_absence():
    """Replacing cache-absence wording with a filing-absence claim must fail this contract."""
    result = _enrich_accounting_note_search({
        "query": {"dataset": "accounting_note_chapters", "keyword": "우발"},
        "subject": {"corp_code": "00999999", "corp_name": "테스트회사"},
        "total_companies": 0,
        "total_records": 0,
        "companies": [],
        "data_quality": {"status": "missing", "source": "accounting_note_chapters"},
    })

    assert result["data_quality"]["status"] == "missing"
    assert result["confirmed_facts"] == []
    assert result["data_quality"]["coverage_note"] == "로컬 캐시에 일치하는 회계주석 근거가 없습니다."
    assert result["next_checks"] == [
        "원 공시의 해당 주석 전문을 직접 확인하세요.",
        "필요하면 최신 수집본으로 로컬 캐시를 보완한 뒤 다시 조회하세요.",
    ]


def test_note_pack_and_chatbot_table_share_enriched_status_and_evidence():
    """Dropping the dedicated evidence table or deriving a different status must fail."""
    result = _enrich_accounting_note_search(_matched_note_result())
    pack = build_answer_pack("search_dataset", result)
    text = render_answer("search_dataset", result)

    assert pack is not None
    table = next(table for table in pack["tables"] if table["id"] == "accounting_note_evidence")
    assert pack["status"] == result["data_quality"]["status"] == "usable"
    assert build_answer_envelope("search_dataset", result).data_quality.status == "usable"
    assert table["rows"] == [{
        "topic": "재고자산",
        "year": 2025,
        "fs_div": "CFS",
        "note_reference": "주석 7 재고자산",
        "confirmed_statement": "주석 7 재고자산에서 재고자산 관련 주석 문구가 확인되었습니다.",
        "matched_excerpt": _MATCHED_EXCERPT,
        "audit_implication": "재고자산 정책 문구는 평균법과 순실현가능가치 평가의 적용 일관성 및 기말 평가 추정을 점검할 스크리닝 근거입니다. 이는 감사 결론이 아닙니다.",
        "rcept_no": "20250312000001",
    }]
    assert pack["sources"][0]["rcept_no"] == "20250312000001"
    assert text is not None
    assert "확인된 내용" in text
    assert _MATCHED_EXCERPT in text.split("분석:")[0]
    assert "회계정책 주석 캐시에서 조회한 결과" in text
    assert "캐시을" not in text
    assert "표 형태 결과" in text
    assert "시각화 대체 표" not in text
    assert text.index("확인된 내용") < text.index("표 형태 결과")


def test_non_note_visual_tools_keep_their_existing_table_heading():
    """Changing the fallback heading for non-note visual tools must fail this contract."""
    text = render_answer("search_disclosure_events", {
        "events": [{
            "event_date": "2025-03-12",
            "corp_name": "테스트회사",
            "event_type": "정기공시",
            "event_title": "사업보고서",
            "rcept_no": "20250312000001",
        }],
        "data_quality": {"status": "usable"},
    })

    assert text is not None
    assert "시각화 대체 표" in text
    assert "표 형태 결과" not in text
