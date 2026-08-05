from __future__ import annotations

from datetime import date
import json


def test_note_comparison_returns_side_by_side_rows_and_explicit_absence(temp_engine):
    from kreports.analysis.note_comparison import compare_peer_accounting_notes
    from kreports.db.engine import get_session
    from kreports.db.models import AccountingNoteChapter, Company, Disclosure, EvidenceDocument, SourceDocument

    with get_session() as session:
        session.add_all([
            Company(corp_code="00000001", corp_name="Subject", induty_code="26410"),
            Company(corp_code="00000002", corp_name="Peer", induty_code="26410"),
            Disclosure(rcept_no="20250301000001", corp_code="00000001", corp_name="Subject", disc_date=date(2025, 3, 1), disc_type="A", report_nm="사업보고서 (2024.12)"),
            Disclosure(rcept_no="20250301000002", corp_code="00000002", corp_name="Peer", disc_date=date(2025, 3, 1), disc_type="A", report_nm="사업보고서 (2024.12)"),
            SourceDocument(rcept_no="20250301000001", corp_code="00000001", bsns_year=2024, source_type="business_report", report_nm="사업보고서 (2024.12)", raw_content="<xml/>", doc_hash="a" * 40),
            SourceDocument(rcept_no="20250301000002", corp_code="00000002", bsns_year=2024, source_type="business_report", report_nm="사업보고서 (2024.12)", raw_content="<xml/>", doc_hash="b" * 40),
            AccountingNoteChapter(corp_code="00000001", bsns_year=2024, fs_div="CFS", rcept_no="20250301000001", source_type="business_report", note_no="10", note_title="리스", section_type="policy", body="리스부채를 현재가치로 측정합니다."),
            AccountingNoteChapter(corp_code="00000002", bsns_year=2024, fs_div="CFS", rcept_no="20250301000002", source_type="business_report", note_no="10", note_title="리스", section_type="policy", body="리스기간을 재검토합니다.", full_text_uri="raw://note/peer", full_text_length=100, full_text_storage_status="externalized"),
            EvidenceDocument(corp_code="00000002", bsns_year=2024, source_type="business_report", rcept_no="20250301000002", evidence_scope="auditor_view", normalized_text="리스 증빙 요약", source_count=1),
        ])

    result = compare_peer_accounting_notes(
        "00000001", 2024, topics=["leases", "impairment"],
        _peer_group={
            "subject": {"corp_code": "00000001", "corp_name": "Subject"},
            "peers": [{"corp_code": "00000002", "corp_name": "Peer"}],
            "selection_policy": {"selection_mode": "adaptive"},
            "confidence": "low",
        },
        _read_engine=temp_engine,
    )

    leases = result["topics"][0]
    assert leases["topic"] == "leases"
    assert [row["company"]["corp_code"] for row in leases["rows"]] == ["00000001", "00000002"]
    assert leases["rows"][1]["availability"] == "summary_only"
    assert leases["rows"][1]["source_locator"].startswith("accounting_note_chapters:")
    assert leases["rows"][1]["evidence_documents"][0]["source_locator"].startswith("evidence_documents:")
    impairment_rows = result["topics"][1]["rows"]
    assert impairment_rows[0]["availability"] == "unavailable"
    assert impairment_rows[0]["verified_annual_note_cache"] is True
    assert impairment_rows[0]["topic_match_status"] == "not_found_in_cached_scope"
    assert result["peer_selection"]["selection_mode"] == "adaptive"


def test_note_disclosure_matrix_groups_companies_by_topic_without_claiming_unavailable_is_non_disclosure(temp_engine):
    from kreports.analysis.note_comparison import build_note_disclosure_matrix
    from kreports.db.engine import get_session
    from kreports.db.models import AccountingNoteChapter, Company, Disclosure, SourceDocument

    with get_session() as session:
        session.add_all([
            Company(corp_code="00000001", corp_name="Subject", induty_code="26410"),
            Company(corp_code="00000002", corp_name="Summary peer", induty_code="26410"),
            Company(corp_code="00000003", corp_name="Cached non-match peer", induty_code="26410"),
            Company(corp_code="00000004", corp_name="Uncached peer", induty_code="26410"),
            Disclosure(rcept_no="20250301000001", corp_code="00000001", corp_name="Subject", disc_date=date(2025, 3, 1), disc_type="A", report_nm="사업보고서 (2024.12)"),
            Disclosure(rcept_no="20250301000002", corp_code="00000002", corp_name="Summary peer", disc_date=date(2025, 3, 1), disc_type="A", report_nm="사업보고서 (2024.12)"),
            Disclosure(rcept_no="20250301000003", corp_code="00000003", corp_name="Cached non-match peer", disc_date=date(2025, 3, 1), disc_type="A", report_nm="사업보고서 (2024.12)"),
            SourceDocument(rcept_no="20250301000001", corp_code="00000001", bsns_year=2024, source_type="business_report", report_nm="사업보고서 (2024.12)", raw_content="<xml/>", doc_hash="a" * 40),
            SourceDocument(rcept_no="20250301000002", corp_code="00000002", bsns_year=2024, source_type="business_report", report_nm="사업보고서 (2024.12)", raw_content="<xml/>", doc_hash="b" * 40),
            SourceDocument(rcept_no="20250301000003", corp_code="00000003", bsns_year=2024, source_type="business_report", report_nm="사업보고서 (2024.12)", raw_content="<xml/>", doc_hash="c" * 40),
            AccountingNoteChapter(corp_code="00000001", bsns_year=2024, fs_div="CFS", rcept_no="20250301000001", source_type="business_report", note_no="10", note_title="리스", section_type="policy", body="리스부채를 현재가치로 측정합니다."),
            AccountingNoteChapter(corp_code="00000002", bsns_year=2024, fs_div="CFS", rcept_no="20250301000002", source_type="business_report", note_no="10", note_title="리스", section_type="policy", body="리스기간을 재검토합니다.", full_text_uri="raw://note/peer", full_text_length=100, full_text_storage_status="externalized"),
            AccountingNoteChapter(corp_code="00000003", bsns_year=2024, fs_div="CFS", rcept_no="20250301000003", source_type="business_report", note_no="11", note_title="재고자산", section_type="policy", body="재고자산은 저가법으로 평가합니다."),
        ])

    result = build_note_disclosure_matrix(
        "00000001",
        2024,
        topics=["leases"],
        _peer_group={
            "subject": {"corp_code": "00000001", "corp_name": "Subject"},
            "peers": [
                {"corp_code": "00000002", "corp_name": "Summary peer"},
                {"corp_code": "00000003", "corp_name": "Cached non-match peer"},
                {"corp_code": "00000004", "corp_name": "Uncached peer"},
            ],
            "selection_policy": {"selection_mode": "adaptive", "criteria_requested": ["industry"]},
        },
        _read_engine=temp_engine,
    )

    assert result["year"] == 2024
    assert result["cohort_definition"]["criteria_requested"] == ["industry"]
    topic = result["topics"][0]
    assert topic["local_evidence_rate"] == {
        "numerator": 2,
        "denominator": 4,
        "pct": 50.0,
        "reviewable_denominator": 3,
        "unavailable_count": 1,
        "matched_count": 2,
        "all_company_count": 4,
        "reviewable_company_count": 3,
        "matched_within_reviewable_pct": 66.7,
        "scope": "returned_topic_rows",
        "represented_company_count": 4,
        "omitted_company_topic_rows": 0,
    }
    assert [cell["status"] for cell in topic["companies"]] == [
        "disclosed", "summary_only", "not_found_in_cached_scope", "unavailable_raw",
    ]
    assert topic["companies"][0]["rcept_no"] == "20250301000001"
    assert topic["companies"][0]["match_evidence"]["keyword"] == "리스"
    not_found = topic["companies"][2]
    assert not_found["disclosure_assessment"] == (
        "topic_not_found_in_cached_scope_not_non_disclosure"
    )
    assert not_found["rcept_no"] == "20250301000003"
    unavailable = topic["companies"][3]
    assert unavailable["unavailable_reason"] == "local_topic_cache_missing"
    assert unavailable["disclosure_assessment"] == "not_assessed"


def test_note_disclosure_matrix_never_promotes_a_topic_match_without_canonical_annual_binding(temp_engine):
    """A stale or foreign annual-source binding cannot become local disclosure evidence."""
    from kreports.analysis.note_comparison import build_note_disclosure_matrix
    from kreports.db.engine import get_session
    from kreports.db.models import AccountingNoteChapter, Company, Disclosure, SourceDocument

    with get_session() as session:
        session.add_all([
            Company(corp_code="00000001", corp_name="Subject", induty_code="26410"),
            Disclosure(rcept_no="20250301000001", corp_code="00000001", corp_name="Subject", disc_date=date(2025, 3, 1), disc_type="A", report_nm="사업보고서 (2024.12)"),
            # This is a same-receipt cache row, but not an annual source binding.
            SourceDocument(rcept_no="20250301000001", corp_code="00000001", bsns_year=2024, source_type="business_report", report_nm="분기보고서 (2024.12)", raw_content="<xml/>", doc_hash="a" * 40),
            AccountingNoteChapter(corp_code="00000001", bsns_year=2024, fs_div="CFS", rcept_no="20250301000001", source_type="business_report", note_no="10", note_title="리스", section_type="policy", body="리스부채를 현재가치로 측정합니다."),
        ])

    result = build_note_disclosure_matrix(
        "00000001", 2024, topics=["leases"],
        _peer_group={
            "subject": {"corp_code": "00000001", "corp_name": "Subject"},
            "peers": [],
            "selection_policy": {"selection_mode": "adaptive"},
        },
        _read_engine=temp_engine,
    )

    cell = result["topics"][0]["companies"][0]
    assert cell["status"] == "summary_only_unverified"
    assert cell["canonical_source_binding"] is False
    assert cell["rcept_no"] is None
    assert cell["source_locator"] is None
    assert cell["disclosure_assessment"] == "not_assessed"


def test_note_disclosure_matrix_uses_supplied_comparison_and_labels_subject_plus_peers_cap(temp_engine, monkeypatch):
    from kreports.analysis import note_comparison

    comparison = {
        "year": 2024,
        "subject": {"corp_code": "00000001", "corp_name": "Subject"},
        "selection_policy": {},
        "pagination": {"offset": 0, "page_size": 199},
        "topics": [{"topic": "leases", "rows": []}],
    }

    def should_not_query_again(*args, **kwargs):
        raise AssertionError("matrix must transpose the supplied comparison")

    monkeypatch.setattr(note_comparison, "compare_peer_accounting_notes", should_not_query_again)
    result = note_comparison.build_note_disclosure_matrix(
        "00000001", 2024, peer_limit=200, page_size=200,
        _comparison=comparison, _read_engine=temp_engine,
    )

    assert result["pagination"]["maximum_companies"] == 200
    assert result["pagination"]["subject_included"] is True
    assert result["pagination"]["requested_peer_limit"] == 200
    assert result["pagination"]["effective_peer_limit"] == 199
    assert result["pagination"]["requested_page_size"] == 200
    assert result["pagination"]["effective_page_size"] == 199


def test_note_disclosure_matrix_exposes_budget_omissions_and_returned_row_rate_scope():
    from kreports.analysis.note_comparison import build_note_disclosure_matrix

    comparison = {
        "year": 2024,
        "subject": {"corp_code": "00000001", "corp_name": "Subject"},
        "selection_policy": {},
        "pagination": {
            "offset": 0,
            "page_size": 199,
            "total_peer_count": 220,
            "available_peer_count": 220,
            "returned_peer_count": 199,
            "has_more": True,
        },
        "truncation": {
            "applied": True,
            "reason": "note_comparison_output_budget",
            "output_budget_applied": True,
            "omitted_peer_rows": 197,
        },
        "topics": [{
            "topic": "leases",
            "omitted_peer_rows": 197,
            "rows": [
                {"company": {"corp_code": "00000001"}, "availability": "available"},
                {"company": {"corp_code": "00000002"}, "availability": "unavailable"},
            ],
        }],
    }

    result = build_note_disclosure_matrix(
        "00000001", 2024, peer_limit=200, page_size=200,
        _comparison=comparison,
    )

    assert result["is_complete"] is False
    assert result["represented_company_count"] == 2
    assert result["requested_company_count"] == 200
    assert result["available_company_count"] == 221
    assert result["omitted_company_topic_rows"] == 197
    assert {
        key: result["source_truncation"][key]
        for key in comparison["truncation"]
    } == comparison["truncation"]
    assert result["source_truncation"]["matrix_output_budget_applied"] is False
    assert (
        result["source_truncation"]["matrix_output_bytes"]
        <= result["source_truncation"]["matrix_max_output_bytes"]
    )
    assert result["rate_scope"] == "returned_topic_rows"
    assert result["topics"][0]["local_evidence_rate"]["scope"] == "returned_topic_rows"
    assert "returned topic rows" in result["limitations"][0]


def test_note_disclosure_matrix_marks_peer_pagination_incomplete_without_row_omission():
    """A next page means the matrix cannot claim its peer cohort is complete."""
    from kreports.analysis.note_comparison import build_note_disclosure_matrix

    result = build_note_disclosure_matrix(
        "00000001", 2024,
        _comparison={
            "year": 2024,
            "subject": {"corp_code": "00000001", "corp_name": "Subject"},
            "selection_policy": {},
            "pagination": {
                "offset": 0, "page_size": 1, "returned_peer_count": 0,
                "total_peer_count": 2, "available_peer_count": 2, "has_more": True,
            },
            "truncation": {"applied": True, "output_budget_applied": False},
            "topics": [{"topic": "leases", "rows": [{
                "company": {"corp_code": "00000001", "corp_name": "Subject"},
                "availability": "available",
            }]}],
        },
    )

    assert result["is_complete"] is False
    assert any("pagination" in limitation for limitation in result["limitations"])
    from kreports.mcp.answer_pack import build_answer_pack

    pack = build_answer_pack("compare_peer_accounting_policies", {
        "note_disclosure_matrix": result,
        "data_quality": {"status": "limited"},
    })
    table = next(table for table in pack["tables"] if table["id"] == "topic_company_disclosure_matrix")
    assert "다음 peer 페이지" in table["note"]


def test_note_disclosure_matrix_bounds_hostile_subject_and_selection_metadata():
    """Subject-only matrices must remain bounded even when selector metadata is hostile."""
    from kreports.analysis.note_comparison import (
        MAX_NOTE_DISCLOSURE_MATRIX_OUTPUT_BYTES,
        build_note_disclosure_matrix,
    )

    oversized = "가" * 40_000
    topics = [{
        "topic": f"{oversized}{index}",
        "rows": [{
            "company": {"corp_code": "00000001", "corp_name": oversized},
            "availability": "available",
            "note_title": oversized,
            "match_keyword": oversized,
            "source_locator": oversized,
        }],
    } for index in range(100)]
    result = build_note_disclosure_matrix(
        "00000001", 2024,
        _comparison={
            "year": 2024,
            "subject": {"corp_code": "00000001", "corp_name": oversized},
            "selection_policy": {
                "criteria_requested": [oversized],
                "criteria_applied": {"hostile": oversized},
                "selection_mode": oversized,
                "nested": {"metadata": oversized},
            },
            "pagination": {
                "offset": 0, "page_size": 0, "returned_peer_count": 0,
                "total_peer_count": 0, "available_peer_count": 0, "has_more": False,
            },
            "truncation": {"applied": False, "output_budget_applied": False},
            "topics": topics,
        },
    )

    truncation = result["source_truncation"]
    assert truncation["matrix_output_budget_applied"] is True
    assert truncation["cohort_metadata_truncated"] is True
    assert truncation["emergency_minimal_result"] is True
    assert result["topics"][0]["companies"][0]["status"] == "disclosed"
    assert truncation["matrix_output_bytes"] == len(json.dumps(
        result, ensure_ascii=False, separators=(",", ":"),
    ).encode())
    assert truncation["matrix_output_bytes"] <= MAX_NOTE_DISCLOSURE_MATRIX_OUTPUT_BYTES

    from kreports.mcp.contracts import build_answer_envelope, enrich_answer_response

    public_result = enrich_answer_response("compare_peer_accounting_policies", {
        "subject": result["cohort_definition"]["subject"],
        "note_disclosure_matrix": result,
        "data_quality": {"status": "limited"},
    })
    envelope = build_answer_envelope("compare_peer_accounting_policies", public_result)
    assert len(json.dumps(
        envelope.model_dump(mode="json"), ensure_ascii=False,
    ).encode()) <= 100_000


def test_note_comparison_multilabels_long_chapters_with_centered_topic_context(temp_engine):
    """One cached chapter can evidence every requested keyword-bearing topic."""
    from kreports.analysis.note_comparison import compare_peer_accounting_notes
    from kreports.db.engine import get_session
    from kreports.db.models import (
        AccountingNoteChapter,
        Company,
        Disclosure,
        SourceDocument,
    )

    generic_prefix = "일반적인 재무제표 작성기준. " + ("가" * 500)
    subject_body = (
        generic_prefix
        + " 수익은 수행의무 이행 시 인식합니다. "
        + ("나" * 500)
        + " 리스부채는 현재가치로 측정합니다. "
        + ("다" * 500)
        + " 회계정책은 중요한 판단을 포함합니다."
    )
    peer_body = subject_body.replace(
        "수익은 수행의무 이행 시 인식", "수익은 고객과의 계약에 따라 인식",
    )
    with get_session() as session:
        session.add_all([
            Company(corp_code="00000001", corp_name="Subject", induty_code="26410"),
            Company(corp_code="00000002", corp_name="Peer", induty_code="26410"),
            Disclosure(rcept_no="20250301000001", corp_code="00000001", corp_name="Subject", disc_date=date(2025, 3, 1), disc_type="A", report_nm="사업보고서 (2024.12)"),
            Disclosure(rcept_no="20250301000002", corp_code="00000002", corp_name="Peer", disc_date=date(2025, 3, 1), disc_type="A", report_nm="사업보고서 (2024.12)"),
            SourceDocument(rcept_no="20250301000001", corp_code="00000001", bsns_year=2024, source_type="business_report", report_nm="사업보고서 (2024.12)", raw_content="<xml/>", doc_hash="a" * 40),
            SourceDocument(rcept_no="20250301000002", corp_code="00000002", bsns_year=2024, source_type="business_report", report_nm="사업보고서 (2024.12)", raw_content="<xml/>", doc_hash="b" * 40),
            AccountingNoteChapter(corp_code="00000001", bsns_year=2024, fs_div="CFS", rcept_no="20250301000001", source_type="business_report", note_no="1", note_title="회계정책", section_type="policy", body=subject_body),
            AccountingNoteChapter(corp_code="00000002", bsns_year=2024, fs_div="CFS", rcept_no="20250301000002", source_type="business_report", note_no="1", note_title="회계정책", section_type="policy", body=peer_body),
            AccountingNoteChapter(corp_code="00000001", bsns_year=2024, fs_div="CFS", rcept_no="20250301000001", source_type="business_report", note_no="0", note_title="재무제표 작성기준", section_type="policy", body="자산·부채 및 수익·비용과 회계정책의 일반적인 표시 기준입니다."),
            AccountingNoteChapter(corp_code="00000001", bsns_year=2024, fs_div="CFS", rcept_no="20250301000001", source_type="business_report", note_no="2", note_title="재무제표 작성기준", section_type="policy", body="일반적인 작성기준만 기재합니다."),
        ])

    kwargs = {
        "topics": ["revenue", "leases", "accounting_policies", "impairment"],
        "_peer_group": {
            "subject": {"corp_code": "00000001", "corp_name": "Subject"},
            "peers": [{"corp_code": "00000002", "corp_name": "Peer"}],
            "selection_policy": {"fs_div_used": "CFS"},
        },
        "_read_engine": temp_engine,
    }
    result = compare_peer_accounting_notes("00000001", 2024, **kwargs)
    repeated = compare_peer_accounting_notes("00000001", 2024, **kwargs)
    rows = {topic["topic"]: topic["rows"] for topic in result["topics"]}
    revenue_subject = rows["revenue"][0]
    lease_subject = rows["leases"][0]
    policy_subject = rows["accounting_policies"][0]

    assert revenue_subject["rcept_no"] == "20250301000001"
    assert revenue_subject["match_keyword"] == "수행의무"
    assert revenue_subject["match_location"] == "body"
    assert revenue_subject["match_offset"] > len(generic_prefix)
    assert revenue_subject["excerpt_start"] > 0
    assert "수익" in revenue_subject["value_or_excerpt"]
    assert "재무제표 작성기준" not in revenue_subject["value_or_excerpt"]
    assert revenue_subject["raw_text"].startswith("일반적인 재무제표 작성기준")
    assert lease_subject["match_keyword"] == "리스부채"
    assert lease_subject["match_location"] == "body"
    assert policy_subject["match_keyword"] == "회계정책"
    assert policy_subject["match_location"] == "title"
    assert policy_subject["note_no"] == "1"
    assert rows["impairment"][0]["availability"] == "unavailable"
    assert revenue_subject["comparison_text_hash"] == repeated["topics"][0]["rows"][0]["comparison_text_hash"]
    assert any(item["topic"] == "revenue" for item in result["differences"])


def test_title_match_count_is_distinct_keywords_not_repeated_occurrences():
    from kreports.analysis.note_comparison import _topic_match

    match = _topic_match(
        {"note_title": "회계정책 및 회계정책", "body": ""},
        "accounting_policies",
    )

    assert match is not None
    assert match["match_keyword"] == "회계정책"
    assert match["matched_keyword_count"] == 1


def test_revenue_body_match_rejects_compound_financial_income_prefixes():
    from kreports.analysis.note_comparison import _topic_match

    for body in (
        "이자수익인식은 유효이자율법에 따릅니다.",
        "이자수익을 인식합니다.",
        "금융수익인식은 금융상품 정책에 따릅니다.",
        "배당수익을 인식합니다.",
    ):
        assert _topic_match(
            {"note_title": "금융상품", "body": body}, "revenue",
        ) is None
    for title in ("이자수익인식", "금융수익인식", "배당수익을 인식"):
        assert _topic_match({"note_title": title, "body": ""}, "revenue") is None
    assert _topic_match(
        {"note_title": "기타", "body": "수익인식 정책을 적용합니다."}, "revenue",
    ) is not None
    assert _topic_match(
        {"note_title": "기타", "body": "고객과의 계약에서 수행의무를 식별합니다."}, "revenue",
    ) is not None


def test_lease_match_rejects_risk_substrings_but_keeps_lease_compounds():
    from kreports.analysis.note_comparison import _topic_match

    assert _topic_match(
        {"note_title": "기타", "body": "시장리스크 및 신용리스크를 관리합니다. 리스크를 반복합니다."},
        "leases",
    ) is None
    for body in ("리스 정책을 적용합니다.", "판매후리스 거래입니다.", "리스부채를 측정합니다.", "사용권자산을 인식합니다."):
        assert _topic_match({"note_title": "기타", "body": body}, "leases") is not None


def test_note_comparison_marks_unbound_cached_note_summary_only_without_receipt(temp_engine):
    from kreports.analysis.note_comparison import compare_peer_accounting_notes
    from kreports.db.engine import get_session
    from kreports.db.models import AccountingNoteChapter, Company

    with get_session() as session:
        session.add_all([
            Company(corp_code="00000001", corp_name="Subject", induty_code="26410"),
            AccountingNoteChapter(
                corp_code="00000001", bsns_year=2024, fs_div="CFS",
                rcept_no="20250301000001", source_type="business_report",
                note_no="10", note_title="리스", section_type="policy",
                body="원천 결합이 입증되지 않은 캐시 주석",
            ),
        ])

    result = compare_peer_accounting_notes(
        "00000001", 2024, topics=["leases"],
        _peer_group={
            "subject": {"corp_code": "00000001", "corp_name": "Subject"},
            "peers": [],
            "selection_policy": {"selection_mode": "adaptive"},
        },
        _read_engine=temp_engine,
    )

    row = result["topics"][0]["rows"][0]
    assert row["availability"] == "summary_only"
    assert row["rcept_no"] is None
    assert row["provenance_status"] == "unproven_source_binding"
    assert row["cached_rcept_no"] == "20250301000001"


def test_note_comparison_rejects_nonannual_source_document_name(temp_engine):
    from kreports.analysis.note_comparison import compare_peer_accounting_notes
    from kreports.db.engine import get_session
    from kreports.db.models import AccountingNoteChapter, Company, Disclosure, SourceDocument

    with get_session() as session:
        session.add_all([
            Company(corp_code="00000001", corp_name="Subject", induty_code="26410"),
            SourceDocument(
                rcept_no="20250301000001", corp_code="00000001", bsns_year=2024,
                source_type="business_report", report_nm="분기보고서 (2024.12)",
                raw_content="<xml/>", doc_hash="a" * 40,
            ),
            Disclosure(
                rcept_no="20250301000001", corp_code="00000001", corp_name="Subject",
                disc_date=date(2025, 3, 1), disc_type="A", report_nm="사업보고서 (2024.12)",
            ),
            AccountingNoteChapter(
                corp_code="00000001", bsns_year=2024, fs_div="CFS",
                rcept_no="20250301000001", source_type="business_report",
                note_no="10", note_title="리스", section_type="policy",
                body="분기보고서 원천에 잘못 결합된 캐시 주석",
            ),
        ])

    result = compare_peer_accounting_notes(
        "00000001", 2024, topics=["leases"],
        _peer_group={
            "subject": {"corp_code": "00000001", "corp_name": "Subject"},
            "peers": [],
            "selection_policy": {"selection_mode": "adaptive"},
        },
        _read_engine=temp_engine,
    )

    row = result["topics"][0]["rows"][0]
    assert row["availability"] == "summary_only"
    assert row["rcept_no"] is None
    assert row["provenance_status"] == "unproven_source_binding"


def test_note_comparison_preserves_raw_text_and_returns_table_safe_peer_matrix(temp_engine):
    from kreports.analysis.note_comparison import compare_peer_accounting_notes
    from kreports.db.engine import get_session
    from kreports.db.models import (
        AccountingNoteChapter,
        Company,
        Disclosure,
        SourceDocument,
    )

    subject_raw = "리스 정책\n| 구분 | 금액 |\n<note>가나다</note>\t\x01"
    with get_session() as session:
        session.add_all([
            Company(corp_code="00000001", corp_name="주식회사 기준", induty_code="26410"),
            Company(corp_code="00000002", corp_name="동종 A", induty_code="26410"),
            Company(corp_code="00000003", corp_name="동종 B", induty_code="26410"),
            Company(corp_code="00000004", corp_name="동종 C", induty_code="26410"),
            SourceDocument(rcept_no="20250301000001", corp_code="00000001", bsns_year=2024, source_type="business_report", report_nm="사업보고서 (2024.12)", raw_content="<xml/>", doc_hash="a" * 40),
            SourceDocument(rcept_no="20250301000002", corp_code="00000002", bsns_year=2024, source_type="business_report", report_nm="사업보고서 (2024.12)", raw_content="<xml/>", doc_hash="b" * 40),
            SourceDocument(rcept_no="20250301000003", corp_code="00000003", bsns_year=2024, source_type="business_report", report_nm="사업보고서 (2024.12)", raw_content="<xml/>", doc_hash="c" * 40),
            Disclosure(rcept_no="20250301000001", corp_code="00000001", corp_name="주식회사 기준", disc_date=date(2025, 3, 1), disc_type="A", report_nm="사업보고서 (2024.12)"),
            Disclosure(rcept_no="20250301000002", corp_code="00000002", corp_name="동종 A", disc_date=date(2025, 3, 1), disc_type="A", report_nm="사업보고서 (2024.12)"),
            Disclosure(rcept_no="20250301000003", corp_code="00000003", corp_name="동종 B", disc_date=date(2025, 3, 1), disc_type="A", report_nm="사업보고서 (2024.12)"),
            AccountingNoteChapter(
                corp_code="00000001", bsns_year=2024, fs_div="CFS", rcept_no="20250301000001",
                source_type="business_report", note_no="10", note_title="리스", section_type="policy",
                body=subject_raw, full_text_hash="a" * 40,
            ),
            AccountingNoteChapter(
                corp_code="00000002", bsns_year=2024, fs_div="CFS", rcept_no="20250301000002",
                source_type="business_report", note_no="10", note_title="리스", section_type="policy",
                body="<p>동종 A 리스 정책</p>", full_text_hash="b" * 40,
            ),
            AccountingNoteChapter(
                corp_code="00000003", bsns_year=2024, fs_div="CFS", rcept_no="20250301000003",
                source_type="business_report", note_no="10", note_title="리스", section_type="policy",
                body="동종 B\n리스 정책", full_text_hash="c" * 40,
            ),
        ])

    result = compare_peer_accounting_notes(
        "00000001", 2024, topics=["leases"], peer_limit=2,
        _peer_group={
            "subject": {"corp_code": "00000001", "corp_name": "주식회사 기준"},
            "peers": [
                {"corp_code": "00000002", "corp_name": "동종 A"},
                {"corp_code": "00000003", "corp_name": "동종 B"},
                {"corp_code": "00000004", "corp_name": "동종 C"},
            ],
            "selection_policy": {"selection_mode": "strict", "fs_div_used": "CFS"},
        },
        _read_engine=temp_engine,
    )

    row = result["topics"][0]["rows"][0]
    assert row["raw_text"] == subject_raw
    assert row["raw_text_length"] == len(subject_raw)
    assert row["raw_text_truncated"] is False
    assert row["raw_text_format"] == "markdown_table+html"
    assert row["comparison_text"] == "리스 정책 | 구분 | 금액 | <note>가나다</note>"
    assert row["display"]["text"] == "리스 정책\\n\\| 구분 \\| 금액 \\|\\n&lt;note&gt;가나다&lt;/note&gt;\\t\\u0001"
    assert row["display"]["markdown_table_escaped"] is True
    assert row["display"]["html_escaped"] is True
    assert row["display"]["control_characters_escaped"] is True
    assert row["rcept_no"] == "20250301000001"
    assert row["full_text_hash"] == "a" * 40
    assert row["source_locator"].startswith("accounting_note_chapters:")
    assert [item["corp_code"] for item in result["coverage_matrix"]["companies"]] == [
        "00000001", "00000002", "00000003",
    ]
    assert result["coverage_matrix"]["topics"][0]["coverage"] == {
        "available": 3,
        "summary_only": 0,
        "unavailable": 0,
    }
    assert result["selection_policy"] == {"selection_mode": "strict", "fs_div_used": "CFS"}
    assert result["pagination"] == {
        "offset": 0,
        "page_size": 2,
        "peer_limit": 2,
        "total_peer_count": 3,
        "available_peer_count": 3,
        "returned_peer_count": 2,
        "has_more": True,
        "next_page_token": "offset:2",
    }
    assert result["truncation"]["applied"] is True
    assert result["truncation"]["reason"] == "peer_pagination"
    assert result["truncation"]["peer_limit"] == 2
    assert result["truncation"]["output_budget_applied"] is False
    assert result["differences"] == [
        {
            "topic": "leases",
            "subject_corp_code": "00000001",
            "peer_corp_code": "00000002",
            "status": "different_normalized_text",
            "subject_source_locator": row["source_locator"],
            "peer_source_locator": result["topics"][0]["rows"][1]["source_locator"],
        },
        {
            "topic": "leases",
            "subject_corp_code": "00000001",
            "peer_corp_code": "00000003",
            "status": "different_normalized_text",
            "subject_source_locator": row["source_locator"],
            "peer_source_locator": result["topics"][0]["rows"][2]["source_locator"],
        },
    ]


def test_note_comparison_paginates_against_selector_total_peer_count(temp_engine):
    from kreports.analysis.note_comparison import compare_peer_accounting_notes

    result = compare_peer_accounting_notes(
        "00000001", 2024, topics=["leases"], peer_limit=2,
        peer_offset=1, page_size=2,
        _peer_group={
            "subject": {"corp_code": "00000001", "corp_name": "Subject"},
            "peers": [
                {"corp_code": "00000002", "corp_name": "Peer 1"},
                {"corp_code": "00000003", "corp_name": "Peer 2"},
                {"corp_code": "00000004", "corp_name": "Peer 3"},
                {"corp_code": "00000005", "corp_name": "Peer 4"},
            ],
            "peer_count": 5,
            "selection_policy": {"selection_mode": "adaptive"},
        },
        _read_engine=temp_engine,
    )

    assert [peer["corp_code"] for peer in result["cohort"]["peers"]] == [
        "00000003", "00000004",
    ]
    assert result["pagination"] == {
        "offset": 1,
        "page_size": 2,
        "peer_limit": 2,
        "total_peer_count": 5,
        "available_peer_count": 5,
        "returned_peer_count": 2,
        "has_more": True,
        "next_page_token": "offset:3",
    }
    assert result["truncation"]["reason"] == "peer_pagination"


def test_note_comparison_ignores_hidden_changes_outside_topic_excerpt(temp_engine):
    from kreports.analysis.note_comparison import compare_peer_accounting_notes
    from kreports.db.engine import get_session
    from kreports.db.models import AccountingNoteChapter

    common_prefix = "가" * 12_500
    with get_session() as session:
        session.add_all([
            AccountingNoteChapter(
                corp_code="00000001", bsns_year=2024, fs_div="CFS", rcept_no="20250301000001",
                source_type="business_report", note_no="10", note_title="리스", section_type="policy",
                body=f"{common_prefix}기준",
            ),
            AccountingNoteChapter(
                corp_code="00000002", bsns_year=2024, fs_div="CFS", rcept_no="20250301000002",
                source_type="business_report", note_no="10", note_title="리스", section_type="policy",
                body=f"{common_prefix}동종",
            ),
        ])

    result = compare_peer_accounting_notes(
        "00000001", 2024, topics=["leases"],
        _peer_group={
            "subject": {"corp_code": "00000001", "corp_name": "Subject"},
            "peers": [{"corp_code": "00000002", "corp_name": "Peer"}],
            "peer_count": 1,
            "selection_policy": {},
        },
        _read_engine=temp_engine,
    )

    subject_row, peer_row = result["topics"][0]["rows"]
    assert subject_row["comparison_text_truncated"] is True
    assert peer_row["comparison_text_truncated"] is True
    assert subject_row["comparison_text"] == peer_row["comparison_text"]
    assert subject_row["comparison_text_hash"] == peer_row["comparison_text_hash"]
    assert subject_row["raw_text_hash"] != peer_row["raw_text_hash"]
    assert result["differences"] == []


def test_note_comparison_keeps_complete_four_topic_six_company_cohort_under_budget(temp_engine):
    from kreports.analysis.note_comparison import compare_peer_accounting_notes
    from kreports.db.engine import get_session
    from kreports.db.models import AccountingNoteChapter

    large_body = "수익인식 수행의무 리스 손상 회계정책 " + ("가나다라마바사" * 2_000)
    peer_codes = [f"0000000{number}" for number in range(2, 7)]
    with get_session() as session:
        session.add(AccountingNoteChapter(
            corp_code="00000001", bsns_year=2024, fs_div="CFS", rcept_no="20250301000001",
            source_type="business_report", note_no="10", note_title="리스", section_type="policy",
            body=large_body,
        ))
        session.add_all([
            AccountingNoteChapter(
                corp_code=code, bsns_year=2024, fs_div="CFS", rcept_no=f"2025030100000{index + 2}",
                source_type="business_report", note_no="10", note_title="리스", section_type="policy",
                body=large_body,
            )
            for index, code in enumerate(peer_codes)
        ])

    result = compare_peer_accounting_notes(
        "00000001", 2024,
        topics=["revenue", "leases", "impairment", "accounting_policies"], peer_limit=5,
        _peer_group={
            "subject": {"corp_code": "00000001", "corp_name": "Subject"},
            "peers": [{"corp_code": code, "corp_name": f"Peer {index}"} for index, code in enumerate(peer_codes)],
            "peer_count": 5,
            "selection_policy": {},
        },
        _read_engine=temp_engine,
    )

    assert len(json.dumps(result, ensure_ascii=False).encode("utf-8")) <= 100_000
    assert result["truncation"]["applied"] is False
    assert result["truncation"]["output_budget_applied"] is False
    assert result["truncation"]["max_output_bytes"] == 100_000
    assert [len(topic["rows"]) for topic in result["topics"]] == [6, 6, 6, 6]
    assert [topic["coverage"] for topic in result["topics"]] == [6, 6, 6, 6]
    assert [
        sum(topic["coverage"].values())
        for topic in result["coverage_matrix"]["topics"]
    ] == [6, 6, 6, 6]
    for topic in result["topics"]:
        for row in topic["rows"]:
            assert len(row["raw_text"] or "") <= 2_000
            assert row.get("output_budget_truncated") is not True
            assert row["comparison_text_hash"]
            assert row["raw_text_hash"]


def test_revenue_match_strength_prefers_multi_signal_and_rejects_generic_interest(temp_engine):
    from kreports.analysis.note_comparison import _topic_match, compare_peer_accounting_notes
    from kreports.db.engine import get_session
    from kreports.db.models import AccountingNoteChapter

    generic_interest = {
        "note_title": "금융수익",
        "body": "이자수익은 유효이자율법에 따라 인식합니다.",
    }
    assert _topic_match(generic_interest, "revenue") is None

    with get_session() as session:
        session.add_all([
            AccountingNoteChapter(
                corp_code="00000001", bsns_year=2024, fs_div="CFS", rcept_no="20250301000001",
                source_type="business_report", note_no="1", note_title="기타", section_type="policy",
                body="고객과의 계약 관련 일반 설명입니다.",
            ),
            AccountingNoteChapter(
                corp_code="00000001", bsns_year=2024, fs_div="CFS", rcept_no="20250301000001",
                source_type="business_report", note_no="2", note_title="기타", section_type="policy",
                body="수익인식 정책에서 수행의무를 식별하고 수익을 인식합니다.",
            ),
            AccountingNoteChapter(
                corp_code="00000002", bsns_year=2024, fs_div="CFS", rcept_no="20250301000002",
                source_type="business_report", note_no="1", note_title="기타", section_type="policy",
                body="고객과의 계약 관련 일반 설명입니다.",
            ),
        ])

    result = compare_peer_accounting_notes(
        "00000001", 2024, topics=["revenue"],
        _peer_group={
            "subject": {"corp_code": "00000001", "corp_name": "Subject"},
            "peers": [{"corp_code": "00000002", "corp_name": "Peer"}],
            "selection_policy": {"fs_div_used": "CFS"},
        },
        _read_engine=temp_engine,
    )
    subject, peer = result["topics"][0]["rows"]
    assert subject["note_no"] == "2"
    assert subject["match_keyword"] == "수익인식"
    assert subject["match_strength"] == "body_multi_signal"
    assert subject["matched_keyword_count"] == 3
    assert peer["match_strength"] == "body_single_signal_reference"
    assert peer["matched_keyword_count"] == 1


def test_topic_match_prefers_later_substantive_local_clusters(temp_engine):
    from kreports.analysis.note_comparison import compare_peer_accounting_notes
    from kreports.db.engine import get_session
    from kreports.db.models import AccountingNoteChapter

    body = (
        "리스 기준서 개정에 대한 일반 참조. "
        + ("가" * 500)
        + " 사용권자산 리스부채 리스료 리스이용자의 회계처리. "
        + ("나" * 500)
        + " 손상 관련 일반 참조. "
        + ("다" * 500)
        + " 현금창출단위 회수가능액 회수가능금액 기대신용손실 손상차손 검토."
    )
    with get_session() as session:
        session.add(AccountingNoteChapter(
            corp_code="00000001", bsns_year=2024, fs_div="CFS", rcept_no="20250301000001",
            source_type="business_report", note_no="1", note_title="기타", section_type="policy",
            body=body,
        ))

    result = compare_peer_accounting_notes(
        "00000001", 2024, topics=["leases", "impairment"],
        _peer_group={"subject": {"corp_code": "00000001", "corp_name": "Subject"}, "peers": []},
        _read_engine=temp_engine,
    )
    leases, impairment = (topic["rows"][0] for topic in result["topics"])

    assert leases["match_keyword"] == "사용권자산"
    assert leases["matched_keyword_count"] == 4
    assert leases["match_offset"] > body.index("리스 기준서")
    assert "리스 기준서 개정" not in leases["value_or_excerpt"]
    assert "리스부채" in leases["value_or_excerpt"]
    assert impairment["match_keyword"] == "현금창출단위"
    assert impairment["matched_keyword_count"] == 5
    assert impairment["match_offset"] > body.index("손상 관련 일반")
    assert "손상 관련 일반" not in impairment["value_or_excerpt"]
    assert "회수가능액" in impairment["value_or_excerpt"]


def test_note_row_selection_prefers_higher_local_signal_density(temp_engine):
    from kreports.analysis.note_comparison import compare_peer_accounting_notes
    from kreports.db.engine import get_session
    from kreports.db.models import AccountingNoteChapter

    with get_session() as session:
        session.add_all([
            AccountingNoteChapter(
                corp_code="00000001", bsns_year=2024, fs_div="CFS", rcept_no="20250301000001",
                source_type="business_report", note_no="1", note_title="기타", section_type="policy",
                body="사용권자산과 리스부채를 인식합니다.",
            ),
            AccountingNoteChapter(
                corp_code="00000001", bsns_year=2024, fs_div="CFS", rcept_no="20250301000001",
                source_type="business_report", note_no="2", note_title="기타", section_type="policy",
                body="사용권자산, 리스부채, 리스료 및 리스이용자 회계처리를 설명합니다.",
            ),
        ])

    result = compare_peer_accounting_notes(
        "00000001", 2024, topics=["leases"],
        _peer_group={"subject": {"corp_code": "00000001", "corp_name": "Subject"}, "peers": []},
        _read_engine=temp_engine,
    )
    row = result["topics"][0]["rows"][0]

    assert row["note_no"] == "2"
    assert row["matched_keyword_count"] == 4


def test_note_comparison_output_budget_compaction_remains_visible(temp_engine, monkeypatch):
    from kreports.analysis import note_comparison
    from kreports.db.engine import get_session
    from kreports.db.models import AccountingNoteChapter

    monkeypatch.setattr(note_comparison, "MAX_NOTE_COMPARISON_OUTPUT_BYTES", 1_000)
    with get_session() as session:
        session.add(AccountingNoteChapter(
            corp_code="00000001", bsns_year=2024, fs_div="CFS", rcept_no="20250301000001",
            source_type="business_report", note_no="1", note_title="리스", section_type="policy",
            body="리스 " + ("가나다라마바사" * 1_000),
        ))
    result = note_comparison.compare_peer_accounting_notes(
        "00000001", 2024, topics=["leases"],
        _peer_group={"subject": {"corp_code": "00000001", "corp_name": "Subject"}, "peers": []},
        _read_engine=temp_engine,
    )
    assert result["truncation"]["output_budget_applied"] is True
    assert "note_comparison_output_truncated" in result["limitations"]


def test_note_comparison_display_escapes_markdown_links_and_unicode_separators():
    from kreports.analysis.note_comparison import _raw_text_fields

    display = _raw_text_fields("![alt](https://example.com/a)|[label](url)\r\nA\u2028B\u2029C\x1f")["display"]

    assert "![" not in display["text"]
    assert "\\[label\\]\\(url\\)" in display["text"]
    assert "\\u2028" in display["text"]
    assert "\\u2029" in display["text"]
    assert display["markdown_link_or_image_escaped"] is True
    assert display["unicode_separators_escaped"] is True
    assert display["control_characters_escaped"] is True


def test_note_comparison_mcp_input_accepts_additive_page_size_and_offset():
    from kreports.mcp.input_models import ComparePeerAccountingNotesInput

    args = ComparePeerAccountingNotesInput(
        company="00000001", year=2024, peer_limit=30, peer_offset=30, page_size=10,
    )

    assert args.peer_limit == 30
    assert args.peer_offset == 30
    assert args.page_size == 10


def test_note_comparison_never_reads_a_different_business_year(temp_engine):
    from kreports.analysis.note_comparison import compare_peer_accounting_notes
    from kreports.db.engine import get_session
    from kreports.db.models import AccountingNoteChapter, Company

    with get_session() as session:
        session.add_all([
            Company(corp_code="00000001", corp_name="Subject", induty_code="26410"),
            Company(corp_code="00000002", corp_name="Peer", induty_code="26410"),
            AccountingNoteChapter(corp_code="00000002", bsns_year=2023, fs_div="CFS", rcept_no="20240301000002", source_type="business_report", note_no="10", note_title="리스", section_type="policy", body="전년도 리스"),
        ])

    result = compare_peer_accounting_notes(
        "00000001", 2024, topics=["leases"],
        _peer_group={"subject": {"corp_code": "00000001", "corp_name": "Subject"}, "peers": [{"corp_code": "00000002", "corp_name": "Peer"}], "selection_policy": {}},
        _read_engine=temp_engine,
    )

    assert all(row["availability"] == "unavailable" for row in result["topics"][0]["rows"])


def test_note_comparison_uses_shared_cohort_fs_div_for_mixed_notes(temp_engine):
    from kreports.analysis.note_comparison import compare_peer_accounting_notes
    from kreports.db.engine import get_session
    from kreports.db.models import AccountingNoteChapter, Company

    with get_session() as session:
        session.add_all([
            Company(corp_code="00000001", corp_name="Subject", induty_code="26410"),
            Company(corp_code="00000002", corp_name="Peer", induty_code="26410"),
            AccountingNoteChapter(corp_code="00000001", bsns_year=2024, fs_div="CFS", rcept_no="20250301000001", source_type="business_report", note_no="10", note_title="리스", section_type="policy", body="연결 리스"),
            AccountingNoteChapter(corp_code="00000001", bsns_year=2024, fs_div="OFS", rcept_no="20250301000001", source_type="business_report", note_no="10", note_title="리스", section_type="policy", body="별도 리스"),
            AccountingNoteChapter(corp_code="00000002", bsns_year=2024, fs_div="CFS", rcept_no="20250301000002", source_type="business_report", note_no="10", note_title="리스", section_type="policy", body="Peer 연결 리스"),
            AccountingNoteChapter(corp_code="00000002", bsns_year=2024, fs_div="OFS", rcept_no="20250301000002", source_type="business_report", note_no="10", note_title="리스", section_type="policy", body="Peer 별도 리스"),
        ])

    result = compare_peer_accounting_notes(
        "00000001", 2024, topics=["leases"],
        _peer_group={
            "subject": {"corp_code": "00000001", "corp_name": "Subject"},
            "peers": [{"corp_code": "00000002", "corp_name": "Peer"}],
            "selection_policy": {"fs_div_used": "OFS"},
        },
        _read_engine=temp_engine,
    )

    rows = result["topics"][0]["rows"]
    assert [row["value_or_excerpt"] for row in rows] == ["별도 리스", "Peer 별도 리스"]
    assert all(row["fs_div_selection"]["requested"] == "OFS" for row in rows)
    assert all(row["fs_div_selection"]["used"] == "OFS" for row in rows)
    assert all(row["fs_div_selection"]["status"] == "exact" for row in rows)


def test_note_comparison_labels_fs_div_fallback_when_requested_notes_are_missing(temp_engine):
    from kreports.analysis.note_comparison import compare_peer_accounting_notes
    from kreports.db.engine import get_session
    from kreports.db.models import AccountingNoteChapter, Company

    with get_session() as session:
        session.add_all([
            Company(corp_code="00000001", corp_name="Subject", induty_code="26410"),
            Company(corp_code="00000002", corp_name="Peer", induty_code="26410"),
            AccountingNoteChapter(corp_code="00000001", bsns_year=2024, fs_div="OFS", rcept_no="20250301000001", source_type="business_report", note_no="10", note_title="리스", section_type="policy", body="별도 리스"),
            AccountingNoteChapter(corp_code="00000002", bsns_year=2024, fs_div="CFS", rcept_no="20250301000002", source_type="business_report", note_no="10", note_title="리스", section_type="policy", body="Peer 연결 리스"),
        ])

    result = compare_peer_accounting_notes(
        "00000001", 2024, topics=["leases"],
        _peer_group={
            "subject": {"corp_code": "00000001", "corp_name": "Subject"},
            "peers": [{"corp_code": "00000002", "corp_name": "Peer"}],
            "selection_policy": {"fs_div_used": "OFS"},
        },
        _read_engine=temp_engine,
    )

    peer_row = result["topics"][0]["rows"][1]
    assert peer_row["value_or_excerpt"] == "Peer 연결 리스"
    assert peer_row["fs_div_selection"] == {
        "requested": "OFS",
        "used": "CFS",
        "status": "fallback_requested_fs_div_unavailable",
    }
