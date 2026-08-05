import json

import pytest

from kreports.mcp.answer_pack import build_answer_pack
from kreports.mcp.contracts import build_answer_envelope, enrich_answer_response
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
                "source_document_id": 1,
                "source_document_rcept_no": rcept_no,
                "source_document_corp_code": "00999999",
                "source_document_bsns_year": 2025,
                "source_document_report_nm": "사업보고서 (2025.12)",
                "disclosure_rcept_no": rcept_no,
                "disclosure_corp_code": "00999999",
                "disclosure_disc_date": "2025-03-12",
                "disclosure_report_nm": "사업보고서 (2025.12)",
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
        "주석 7 재고자산에서 재고자산 관련 주석 문구를 확인했습니다."
    )
    assert result["confirmed_facts"][0]["source"]["rcept_no"] == "20250312000001"
    assert result["confirmed_facts"][0]["source"]["source_table"] == "accounting_note_chapters"
    assert result["confirmed_facts"][0]["source"]["report_nm"] == "사업보고서"
    assert result["analysis"][0]["perspective"] == "auditor"
    assert "재고자산" in result["analysis"][0]["statement"]
    assert result["next_checks"] == [
        "재고 실사와 수량 확인 결과가 기말 잔액 및 이동 내역과 일치하는지 확인하세요.",
        "원가, 순실현가능가치 및 진부화 평가에 사용한 가정과 근거를 검토하세요.",
        "해당 주석 전문과 관련 공시의 후속 변경사항을 검토하세요.",
    ]


def test_note_enrichment_marks_matched_uncitable_row_limited():
    """Treating a matched row without a canonical DART receipt as usable is a bug."""
    result = _enrich_accounting_note_search(_matched_note_result(rcept_no="attachment-only"))

    assert result["data_quality"]["status"] == "limited"
    assert result["confirmed_facts"] == []
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


def test_note_enrichment_normalizes_unpunctuated_nested_note_title_for_citation():
    """Leaving a numeric subtitle ungrouped makes real Samsung note citations ambiguous."""
    raw = _matched_note_result()
    raw["query"]["keyword"] = "재무제표"
    record = raw["companies"][0]["records"][0]
    record["note_no"] = "2"
    record["note_title"] = "1 재무제표 작성기준"
    record["match_excerpts"] = ["재무제표는 한국채택국제회계기준에 따라 작성합니다."]

    result = _enrich_accounting_note_search(raw)

    assert result["confirmed_facts"][0]["note_reference"] == "주석 2 · 1 재무제표 작성기준"


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


@pytest.mark.parametrize(
    ("topic", "excerpt", "required_groups"),
    [
        (
            "수익",
            "수익은 수행의무 이행에 따라 인식하며 변동대가를 추정합니다.",
            (("수행의무", "통제이전", "기간귀속"), ("변동대가", "매출차감")),
        ),
        (
            "재고자산",
            "재고자산은 원가와 순실현가능가치 중 낮은 금액으로 측정합니다.",
            (("실사", "수량"), ("원가", "순실현가능가치", "진부화")),
        ),
        (
            "충당부채",
            "충당부채는 현재의무와 최선추정액에 따라 인식합니다.",
            (("의무 완전성",), ("과거 보증청구", "최선추정", "사후실적")),
        ),
    ],
)
def test_note_enrichment_tailors_audit_checks_to_topic(topic, excerpt, required_groups):
    """Replacing topic-specific audit checks with generic review steps is a bug."""
    raw = _matched_note_result()
    raw["query"]["keyword"] = topic
    record = raw["companies"][0]["records"][0]
    record["note_title"] = topic
    record["match_excerpts"] = [excerpt]

    result = _enrich_accounting_note_search(raw)
    rendered_checks = " ".join(result["next_checks"])

    for alternatives in required_groups:
        assert any(term in rendered_checks for term in alternatives)


def test_note_enrichment_keeps_conservative_generic_checks_for_other_topics():
    """Making unsupported topics look procedurally covered is a bug."""
    raw = _matched_note_result()
    raw["query"]["keyword"] = "리스"
    record = raw["companies"][0]["records"][0]
    record["note_title"] = "리스"
    record["match_excerpts"] = ["리스부채는 유효이자율법으로 측정합니다."]

    result = _enrich_accounting_note_search(raw)

    assert result["next_checks"] == [
        "관련 잔액과 비교표시 금액을 원 공시와 대조하세요.",
        "주요 회계추정 입력과 근거를 검토하세요.",
        "해당 주석 전문과 관련 공시의 후속 변경사항을 검토하세요.",
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
        "company": "테스트회사",
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
    assert result["confirmed_facts"][0]["excerpt"] == _MATCHED_EXCERPT
    assert text.count(_MATCHED_EXCERPT) == 1
    assert "주석 7 재고자산에서 재고자산 관련 일치 문구 1건을 확인했습니다." in text
    assert "원문 발췌는 아래 표에 표시합니다." in text
    assert "사업보고서" in text
    assert "회계정책 주석 캐시에서 조회한 결과" in text
    assert "캐시을" not in text
    assert "표 형태 결과" in text
    assert "시각화 대체 표" not in text
    assert text.index("확인된 내용") < text.index("표 형태 결과")


def test_note_evidence_table_labels_each_fact_with_its_source_company():
    """Multiple-company note evidence must retain the company-to-receipt association."""
    raw = _matched_note_result()
    second_record = dict(raw["companies"][0]["records"][0])
    second_record.update({
        "rcept_no": "20250312000002",
        "source_document_id": 2,
        "source_document_rcept_no": "20250312000002",
        "source_document_corp_code": "00888888",
        "disclosure_rcept_no": "20250312000002",
        "disclosure_corp_code": "00888888",
    })
    raw["companies"].append({
        "corp_code": "00888888",
        "corp_name": "다른회사",
        "records": [second_record],
    })

    result = _enrich_accounting_note_search(raw)
    pack = build_answer_pack("search_dataset", result)
    table = next(table for table in pack["tables"] if table["id"] == "accounting_note_evidence")

    assert [row["company"] for row in table["rows"]] == ["테스트회사", "다른회사"]
    assert {"company", "rcept_no"} <= {column["field"] for column in table["columns"]}


def test_note_pack_adds_company_matrix_without_replacing_existing_evidence_table():
    """The reverse company lookup must remain visible without removing fact evidence."""
    result = _enrich_accounting_note_search(_matched_note_result())
    result["note_disclosure_company_matrix"] = {
        "scope": {"keyword": "재고자산", "year": 2025, "market": "KOSPI", "induty_prefix": "264"},
        "configured_limit": 50,
        "returned_company_count": 2,
        "is_exhaustive": False,
        "limitations": ["캐시 일치는 규제상 공시 완전성 또는 공시 부재 결론이 아닙니다."],
        "companies": [
            {"corp_code": "00999999", "corp_name": "테스트회사", "market": "KOSPI", "induty_code": "264", "year": 2025, "matched_years": [2025], "match_status": "verified_annual_filing_match", "record_count": 1, "canonical_rcept_no": "20250312000001", "canonical_note_title": "재고자산"},
            {"corp_code": "00999998", "corp_name": "미검증회사", "market": "KOSPI", "induty_code": "264", "year": 2025, "matched_years": [2025, 2024], "match_status": "unverified_cache_match", "record_count": 2, "canonical_rcept_no": None, "canonical_note_title": None},
        ],
    }

    pack = build_answer_pack("search_dataset", result)

    assert pack is not None
    assert {table["id"] for table in pack["tables"]} >= {
        "accounting_note_evidence", "note_disclosure_company_matrix",
    }
    table = next(table for table in pack["tables"] if table["id"] == "note_disclosure_company_matrix")
    assert table["rows"] == [
        {"company": "테스트회사", "market": "KOSPI", "induty_code": "264", "year": 2025, "matched_years": [2025], "match_status": "verified_annual_filing_match", "match_status_label": "검증된 연간 공시 일치", "record_count": 1, "source_records_truncated": None, "source_record_rows_omitted_count": None, "note_title": "재고자산", "note_title_truncated": None, "display_truncated": None, "rcept_no": "20250312000001"},
        {"company": "미검증회사", "market": "KOSPI", "induty_code": "264", "year": 2025, "matched_years": [2025, 2024], "match_status": "unverified_cache_match", "match_status_label": "미검증 로컬 캐시 일치", "record_count": 2, "source_records_truncated": None, "source_record_rows_omitted_count": None, "note_title": None, "note_title_truncated": None, "display_truncated": None, "rcept_no": None},
    ]


def test_note_company_matrix_uses_latest_matched_year_when_query_year_is_omitted():
    """An unscoped reverse search must retain each company's returned year coverage."""
    records = [
        {
            "year": year,
            "note_no": "1",
            "note_title": "재고자산",
            "rcept_no": receipt,
            "source_document_id": index + 1,
            "source_document_rcept_no": receipt,
            "source_document_corp_code": "00999999",
            "source_document_bsns_year": year,
            "source_document_report_nm": f"사업보고서 ({year}.12)",
            "disclosure_rcept_no": receipt,
            "disclosure_corp_code": "00999999",
            "disclosure_disc_date": f"{year + 1}-03-12",
            "disclosure_report_nm": f"사업보고서 ({year}.12)",
            "match_excerpts": [_MATCHED_EXCERPT],
        }
        for index, year in enumerate(range(2025, 2013, -1))
        for receipt in [f"{year + 1}0312{index:06d}"]
    ]
    result = _enrich_accounting_note_search({
        "query": {"dataset": "accounting_note_chapters", "keyword": "재고자산", "limit": 50},
        "companies": [{
            "corp_code": "00999999", "corp_name": "테스트회사", "market": "KOSPI",
            "induty_code": "264", "record_count": 12, "records": records,
        }],
        "data_quality": {"status": "usable", "source": "accounting_note_chapters"},
    })

    company = result["note_disclosure_company_matrix"]["companies"][0]
    assert company["year"] == 2025
    assert company["matched_years"] == list(range(2025, 2015, -1))
    assert company["matched_years_truncated"] is True
    assert company["matched_years_omitted_count"] == 2
    assert company["display_truncated"] is True


def test_note_company_matrix_marks_source_record_truncation_without_guessing_year_omissions():
    """A search-row cap cannot be reported as an exact count of omitted distinct years."""
    raw = _matched_note_result()
    raw["companies"][0]["record_count"] = 12

    result = _enrich_accounting_note_search(raw)
    company = result["note_disclosure_company_matrix"]["companies"][0]
    pack = build_answer_pack("search_dataset", result)
    table = next(table for table in pack["tables"] if table["id"] == "note_disclosure_company_matrix")

    assert company["source_records_truncated"] is True
    assert company["source_record_rows_omitted_count"] == 11
    assert company["matched_years_truncated"] is True
    assert company["matched_years_omitted_count"] is None
    assert table["rows"][0]["source_records_truncated"] is True
    assert {"source_records_truncated", "source_record_rows_omitted_count"} <= {
        column["field"] for column in table["columns"]
    }
    assert "원본 검색 행" in table["note"]


def test_note_company_matrix_bounds_hostile_query_scope_before_serialization():
    """Scope filters are public matrix fields and must not bypass the byte budget."""
    oversized = "재" * 20_000
    result = _enrich_accounting_note_search({
        "query": {
            "dataset": "accounting_note_chapters", "keyword": oversized,
            "market": oversized, "induty_prefix": oversized,
        },
        "companies": [],
        "data_quality": {"status": "missing", "source": "accounting_note_chapters"},
    })

    matrix = result["note_disclosure_company_matrix"]
    assert matrix["scope_truncated"] is True
    assert set(matrix["scope_truncated_fields"]) == {"keyword", "market", "induty_prefix"}
    assert len(matrix["scope"]["keyword"]) <= 160
    assert len(matrix["scope"]["market"]) <= 24
    assert len(matrix["scope"]["induty_prefix"]) <= 24
    assert matrix["matrix_output_bytes"] == len(json.dumps(
        matrix, ensure_ascii=False, separators=(",", ":"),
    ).encode())
    assert matrix["matrix_output_bytes"] <= matrix["matrix_max_output_bytes"]


def test_note_company_matrix_prioritizes_verified_companies_and_bounds_titles():
    """A long canonical title or cache-only match must not dominate the matrix UI."""
    def record(*, corp_code: str, title: str, receipt: str, verified: bool) -> dict:
        return {
            "year": 2025,
            "note_no": "1",
            "note_title": title,
            "rcept_no": receipt,
            "source_document_id": 1 if verified else None,
            "source_document_rcept_no": receipt if verified else None,
            "source_document_corp_code": corp_code if verified else None,
            "source_document_bsns_year": 2025 if verified else None,
            "source_document_report_nm": "사업보고서 (2025.12)" if verified else None,
            "disclosure_rcept_no": receipt if verified else None,
            "disclosure_corp_code": corp_code if verified else None,
            "disclosure_disc_date": "2025-03-12" if verified else None,
            "disclosure_report_nm": "사업보고서 (2025.12)" if verified else None,
            "match_excerpts": [_MATCHED_EXCERPT],
        }

    result = _enrich_accounting_note_search({
        "query": {"dataset": "accounting_note_chapters", "keyword": "재고자산", "year": 2025, "limit": 50},
        "companies": [
            {"corp_code": "00000001", "corp_name": "AAA cache", "market": "KOSPI", "induty_code": "264", "record_count": 1, "records": [record(corp_code="00000001", title="캐시 제목", receipt="20250312000001", verified=False)]},
            {"corp_code": "00000002", "corp_name": "Alpha verified", "market": "KOSDAQ", "induty_code": "264", "record_count": 1, "records": [record(corp_code="00000002", title="  정상   제목  ", receipt="20250312000002", verified=True)]},
            {"corp_code": "00000003", "corp_name": "Zulu verified", "market": "KOSPI", "induty_code": "264", "record_count": 1, "records": [record(corp_code="00000003", title="가" * 161, receipt="20250312000003", verified=True)]},
        ],
        "data_quality": {"status": "usable", "source": "accounting_note_chapters"},
    })

    companies = result["note_disclosure_company_matrix"]["companies"]
    assert [item["corp_code"] for item in companies] == ["00000002", "00000003", "00000001"]
    assert companies[0]["canonical_note_title"] == "정상 제목"
    assert companies[0]["canonical_note_title_truncated"] is False
    assert companies[1]["canonical_note_title"] == "가" * 160
    assert companies[1]["canonical_note_title_truncated"] is True

    pack = build_answer_pack("search_dataset", result)
    assert pack is not None
    table = next(table for table in pack["tables"] if table["id"] == "note_disclosure_company_matrix")
    assert [row["note_title"] for row in table["rows"]] == [
        "정상 제목", "가" * 160, None,
    ]


def test_note_company_matrix_bounds_reverse_lookup_to_two_hundred_companies():
    """A broad cache query must not make the additive matrix unbounded."""
    companies = [
        {
            "corp_code": f"{index:08d}",
            "corp_name": f"회사 {index}",
            "market": "KOSPI",
            "induty_code": "264",
            "record_count": 1,
            "records": [{
                "year": 2025,
                "note_no": "1",
                "note_title": "재고자산",
                "rcept_no": "20250312000001",
                "source_document_id": index + 1,
                "source_document_rcept_no": "20250312000001",
                "source_document_corp_code": f"{index:08d}",
                "source_document_bsns_year": 2025,
                "source_document_report_nm": "사업보고서 (2025.12)",
                "disclosure_rcept_no": "20250312000001",
                "disclosure_corp_code": f"{index:08d}",
                "disclosure_disc_date": "2025-03-12",
                "disclosure_report_nm": "사업보고서 (2025.12)",
                "match_excerpts": [_MATCHED_EXCERPT],
            }],
        }
        for index in range(201)
    ]

    result = _enrich_accounting_note_search({
        "query": {"dataset": "accounting_note_chapters", "keyword": "재고자산", "year": 2025, "limit": 500},
        "companies": companies,
        "data_quality": {"status": "usable", "source": "accounting_note_chapters"},
    })

    matrix = result["note_disclosure_company_matrix"]
    assert matrix["configured_limit"] == 500
    assert 0 < matrix["returned_company_count"] <= 200
    assert len(matrix["companies"]) == matrix["returned_company_count"]
    assert matrix["matrix_output_budget_applied"] is True
    assert matrix["omitted_company_count"] > 0
    assert matrix["is_exhaustive"] is False
    assert len(result["confirmed_facts"]) == 20
    truncation = result["confirmed_facts_truncation"]
    assert truncation["applied"] is True
    assert truncation["max_rows"] == 20
    assert truncation["max_output_bytes"] > 0
    assert truncation["output_bytes"] <= truncation["max_output_bytes"]
    assert truncation["omitted_count"] == 181
    assert "confirmed_facts_output_truncated:181" in result["data_quality"]["limitations"]

    public_result = enrich_answer_response("search_dataset", result)
    envelope = build_answer_envelope("search_dataset", public_result)
    assert len(json.dumps(envelope.model_dump(mode="json"), ensure_ascii=False).encode()) <= 100_000


def test_note_company_matrix_worst_case_text_stays_within_public_envelope_budget():
    """Long Korean display fields cannot make the matrix answer pack invalid."""
    long_name = "가" * 100
    long_title = "나" * 300
    long_excerpt = "재고자산 " + ("다" * 500)
    companies = [
        {
            "corp_code": f"{index:08d}",
            "corp_name": f"{long_name}{index}",
            "market": "KOSPI" * 20,
            "induty_code": "264" * 30,
            "record_count": 1,
            "records": [{
                "year": 2025 - (index % 12),
                "note_no": "1",
                "note_title": long_title,
                "rcept_no": f"20250312{index:06d}",
                "source_document_id": index + 1,
                "source_document_rcept_no": f"20250312{index:06d}",
                "source_document_corp_code": f"{index:08d}",
                "source_document_bsns_year": 2025 - (index % 12),
                "source_document_report_nm": f"사업보고서 ({2025 - (index % 12)}.12)",
                "disclosure_rcept_no": f"20250312{index:06d}",
                "disclosure_corp_code": f"{index:08d}",
                "disclosure_disc_date": "2025-03-12",
                "disclosure_report_nm": f"사업보고서 ({2025 - (index % 12)}.12)",
                "match_excerpts": [long_excerpt],
            }],
        }
        for index in range(201)
    ]
    result = _enrich_accounting_note_search({
        "query": {"dataset": "accounting_note_chapters", "keyword": "재고자산", "limit": 500},
        "companies": companies,
        "data_quality": {"status": "usable", "source": "accounting_note_chapters"},
    })

    matrix = result["note_disclosure_company_matrix"]
    assert matrix["matrix_output_budget_applied"] is True
    assert matrix["matrix_output_bytes"] <= matrix["matrix_max_output_bytes"]
    assert matrix["matrix_output_bytes"] == len(json.dumps(
        matrix, ensure_ascii=False, separators=(",", ":"),
    ).encode())
    assert matrix["omitted_company_count"] > 0
    assert matrix["is_exhaustive"] is False
    assert matrix["companies"]
    assert matrix["companies"][0]["corp_name_truncated"] is True
    assert matrix["companies"][0]["market_truncated"] is True
    assert matrix["companies"][0]["induty_code_truncated"] is True
    assert matrix["companies"][0]["matched_years_truncated"] is False
    facts_truncation = result["confirmed_facts_truncation"]
    assert facts_truncation["max_output_bytes"] > 0
    assert facts_truncation["output_bytes"] <= facts_truncation["max_output_bytes"]
    assert facts_truncation["output_bytes"] == len(json.dumps(
        result["confirmed_facts"], ensure_ascii=False, separators=(",", ":"),
    ).encode())
    assert facts_truncation["omitted_count"] > 0
    assert all("재고자산" in fact["excerpt"] for fact in result["confirmed_facts"])
    assert all(len(fact["note_reference"]) <= 160 for fact in result["confirmed_facts"])
    assert all(len(fact["source"]["corp_name"]) <= 48 for fact in result["confirmed_facts"])
    assert all(long_excerpt not in fact["statement"] for fact in result["confirmed_facts"])

    public_result = enrich_answer_response("search_dataset", result)
    envelope = build_answer_envelope("search_dataset", public_result)
    assert len(json.dumps(envelope.model_dump(mode="json"), ensure_ascii=False).encode()) <= 100_000
    table = next(table for table in public_result["answer_pack"]["tables"] if table["id"] == "note_disclosure_company_matrix")
    assert "생략" in table["note"]
    assert {"record_count", "match_status_label", "note_title_truncated", "display_truncated"} <= {
        column["field"] for column in table["columns"]
    }


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


def test_non_note_professional_answer_keeps_its_original_confirmed_fact():
    """Applying note-search presentation summarization to another tool is a bug."""
    text = render_answer("get_business_overview", {
        "confirmed_facts": [{
            "statement": "원래 상세 사실은 그대로 표시됩니다.",
            "excerpt": "원래 상세 사실은 그대로 표시됩니다.",
            "source": {"rcept_no": "20250312000001", "source_table": "report_sections"},
        }],
        "data_quality": {"status": "usable"},
    })

    assert text is not None
    assert "원래 상세 사실은 그대로 표시됩니다." in text
