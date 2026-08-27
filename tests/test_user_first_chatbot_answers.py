from __future__ import annotations


_PROHIBITED_VISIBLE_TERMS = (
    "answer_pack",
    "_meta",
    "local_kreports_db",
    "cohort",
    "selection_score",
    "include_reasons",
    "mid-rank",
    "coverage",
    "summary_only",
    "different_normalized_text",
    "fallback_with_warning",
    "synonym",
    "레코드",
)


def _assert_plain_language(answer: str) -> None:
    lowered = answer.lower()
    for term in _PROHIBITED_VISIBLE_TERMS:
        assert term.lower() not in lowered, term


def _peer_result(count: int = 12) -> dict:
    peers = []
    for index in range(1, count + 1):
        peers.append({
            "corp_code": f"{index:08d}",
            "stock_code": f"{index:06d}",
            "corp_name": f"비교회사 {index}",
            "market": "KOSPI",
            "induty_code": "26410",
            "total_assets": index * 1_000_000_000_000,
            "revenue": index * 500_000_000_000,
            "include_reasons": [
                "same_ksic_prefix",
                "asset_size_bucket",
            ],
            "selection_score": 0.9,
        })
    return {
        "subject": {
            "corp_code": "00126380",
            "corp_name": "기준회사",
            "stock_code": "005930",
        },
        "selection_policy": {
            "resolved_year": 2024,
            "requested_year": 2024,
            "fs_div_used": "CFS",
        },
        "statistical_member_count": count,
        "peer_count": count,
        "returned_peer_count": count,
        "confidence": "medium",
        "peers": peers,
        "data_quality": {
            "status": "usable",
            "limitations": [
                "chatbot_peer_table_is_truncated",
            ],
        },
        "confirmed_facts": [{
            "statement": "2024년 재무제표를 기준으로 비교회사를 선정했습니다.",
            "source": {
                "rcept_no": "20250318000001",
                "section_title": "재무제표",
            },
        }],
    }


def test_peer_answer_is_split_into_five_company_pages_and_hides_internal_terms():
    from kreports.mcp.chatbot_company_pagination import (
        paginate_company_view,
        pagination_metadata,
        render_first_page_markdown,
    )
    from kreports.mcp.chatbot_contracts import build_chatbot_view
    from kreports.mcp.chatbot_user_experience import polish_chatbot_view

    result = _peer_result(12)
    base = build_chatbot_view("select_peer_group", result)
    assert base is not None
    polished = polish_chatbot_view("select_peer_group", base, result)
    paged = paginate_company_view("select_peer_group", polished, result)

    assert [len(table.rows) for table in paged.tables] == [5, 5, 2]
    assert pagination_metadata(paged)["page_size"] == 5
    assert pagination_metadata(paged)["page_count"] == 3

    answer = render_first_page_markdown(paged, result)
    assert "비교회사 1" in answer
    assert "비교회사 5" in answer
    assert "비교회사 6" not in answer
    assert "다음 5개 비교회사" in answer
    assert "같은 업종" in answer
    assert "회사 규모가 유사" in answer
    assert "[재무제표](https://dart.fss.or.kr/" in answer
    _assert_plain_language(answer)


def _note_search_result() -> dict:
    companies = []
    for index in range(1, 7):
        receipt = f"20250318{index:06d}"
        companies.append({
            "corp_code": f"{index:08d}",
            "corp_name": f"공시회사 {index}",
            "stock_code": f"{index:06d}",
            "records": [{
                "year": 2024,
                "fs_div": "CFS",
                "note_no": "32",
                "note_title": "우발부채 및 약정사항",
                "matched_term": "자금보충약정",
                "match_type": "synonym",
                "body_excerpt": "대출약정과 관련하여 자금보충약정을 제공하고 있습니다.",
                "rcept_no": receipt,
            }],
        })
    return {
        "query": {
            "dataset": "accounting_note_chapters",
            "keyword": "자금보충약정",
            "year": 2024,
            "search_mode": "synonym",
            "offset": 0,
        },
        "matched_company_count": 6,
        "matched_record_count": 8,
        "returned_company_count": 6,
        "total_companies": 6,
        "total_records": 8,
        "companies": companies,
        "data_quality": {
            "status": "usable",
            "limitations": [
                "cache_miss_is_not_disclosure_absence",
            ],
        },
    }


def test_note_search_shows_five_companies_and_clickable_filing_links():
    from kreports.mcp.chatbot_company_pagination import (
        paginate_company_view,
        render_first_page_markdown,
    )
    from kreports.mcp.chatbot_contracts import build_chatbot_view
    from kreports.mcp.chatbot_user_experience import polish_chatbot_view

    result = _note_search_result()
    base = build_chatbot_view("search_dataset", result)
    assert base is not None
    polished = polish_chatbot_view("search_dataset", base, result)
    paged = paginate_company_view("search_dataset", polished, result)

    assert [len(table.rows) for table in paged.tables] == [5, 1]
    answer = render_first_page_markdown(paged, result)
    assert "공시회사 1" in answer
    assert "공시회사 5" in answer
    assert "공시회사 6" not in answer
    assert "[공시 보기](https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" in answer
    assert "유사한 표현까지 포함" in answer
    assert "다음 5개 회사" in answer
    _assert_plain_language(answer)


def _note_comparison_result() -> dict:
    subject = {
        "corp_code": "00000001",
        "corp_name": "기준회사",
    }
    rows = [{
        "company": subject,
        "availability": "available",
        "fs_div": "CFS",
        "fs_div_selection": {
            "status": "exact",
        },
        "note_title": "리스",
        "comparison_text": "기준회사 리스 주석",
        "rcept_no": "20250318000001",
    }]
    differences = []
    for index in range(2, 8):
        code = f"{index:08d}"
        rows.append({
            "company": {
                "corp_code": code,
                "corp_name": f"비교회사 {index - 1}",
            },
            "availability": "available",
            "fs_div": "CFS",
            "fs_div_selection": {
                "status": "exact",
            },
            "note_title": "리스",
            "comparison_text": f"비교회사 {index - 1} 리스 주석",
            "rcept_no": f"20250318{index:06d}",
        })
        if index % 2 == 0:
            differences.append({
                "topic": "leases",
                "peer_corp_code": code,
                "status": "different_normalized_text",
            })
    return {
        "subject": subject,
        "topics": [{
            "topic": "leases",
            "rows": rows,
        }],
        "differences": differences,
        "difference_count": len(differences),
        "coverage_matrix": {
            "companies": [subject] + [row["company"] for row in rows[1:]],
        },
        "pagination": {
            "offset": 0,
            "page_size": 6,
            "total_peer_count": 6,
            "returned_peer_count": 6,
            "has_more": False,
        },
        "data_quality": {
            "status": "usable",
            "topic_count": 1,
            "coverage_pct": 100.0,
            "limitations": [],
        },
    }


def test_note_comparison_uses_company_pages_plain_statuses_and_links():
    from kreports.mcp.chatbot_company_pagination import (
        paginate_company_view,
        render_first_page_markdown,
    )
    from kreports.mcp.chatbot_contracts import build_chatbot_view
    from kreports.mcp.chatbot_user_experience import polish_chatbot_view

    result = _note_comparison_result()
    base = build_chatbot_view("compare_peer_accounting_notes", result)
    assert base is not None
    polished = polish_chatbot_view(
        "compare_peer_accounting_notes",
        base,
        result,
    )
    paged = paginate_company_view(
        "compare_peer_accounting_notes",
        polished,
        result,
    )

    assert [len(table.rows) for table in paged.tables] == [5, 1]
    answer = render_first_page_markdown(paged, result)
    assert "비교회사 1" in answer
    assert "비교회사 5" in answer
    assert "비교회사 6" not in answer
    assert "문구가 다른 주제" in answer
    assert "요청 기준과 일치" in answer
    assert "[공시 보기](https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" in answer
    _assert_plain_language(answer)


def test_structured_pack_retains_all_five_company_pages():
    from kreports.mcp.chatbot_company_pagination import paginate_company_view
    from kreports.mcp.chatbot_contracts import build_chatbot_view
    from kreports.mcp.chatbot_user_experience import (
        build_user_visualization_pack,
        polish_chatbot_view,
    )

    result = _peer_result(12)
    base = build_chatbot_view("select_peer_group", result)
    assert base is not None
    polished = polish_chatbot_view("select_peer_group", base, result)
    paged = paginate_company_view("select_peer_group", polished, result)
    pack = build_user_visualization_pack(paged, result)

    assert len(pack["tables"]) == 3
    assert [len(table["rows"]) for table in pack["tables"]] == [5, 5, 2]
    assert pack["sources"][0]["url"].startswith("https://dart.fss.or.kr/")
