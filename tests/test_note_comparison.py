from __future__ import annotations

from datetime import date
import json


def test_note_comparison_returns_side_by_side_rows_and_explicit_absence(temp_engine):
    from kreports.analysis.note_comparison import compare_peer_accounting_notes
    from kreports.db.engine import get_session
    from kreports.db.models import AccountingNoteChapter, Company, EvidenceDocument, SourceDocument

    with get_session() as session:
        session.add_all([
            Company(corp_code="00000001", corp_name="Subject", induty_code="26410"),
            Company(corp_code="00000002", corp_name="Peer", induty_code="26410"),
            SourceDocument(rcept_no="20250301000001", corp_code="00000001", bsns_year=2024, source_type="business_report", report_nm="사업보고서", raw_content="<xml/>", doc_hash="a" * 40),
            SourceDocument(rcept_no="20250301000002", corp_code="00000002", bsns_year=2024, source_type="business_report", report_nm="사업보고서", raw_content="<xml/>", doc_hash="b" * 40),
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
    assert result["topics"][1]["rows"][0]["availability"] == "unavailable"
    assert result["peer_selection"]["selection_mode"] == "adaptive"


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


def test_note_comparison_is_indeterminate_when_only_truncated_text_differs(temp_engine):
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
    assert subject_row["comparison_text_hash"] != peer_row["comparison_text_hash"]
    assert result["differences"][0]["status"] == "indeterminate_truncated"


def test_note_comparison_enforces_utf8_budget_without_unbounded_raw_duplicates(temp_engine):
    from kreports.analysis.note_comparison import compare_peer_accounting_notes
    from kreports.db.engine import get_session
    from kreports.db.models import AccountingNoteChapter

    large_body = "본문 " + ("가나다라마바사" * 2_000)
    peer_codes = [f"0000000{number}" for number in range(2, 8)]
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
        "00000001", 2024, topics=["leases"], peer_limit=6,
        _peer_group={
            "subject": {"corp_code": "00000001", "corp_name": "Subject"},
            "peers": [{"corp_code": code, "corp_name": f"Peer {index}"} for index, code in enumerate(peer_codes)],
            "peer_count": 6,
            "selection_policy": {},
        },
        _read_engine=temp_engine,
    )

    assert len(json.dumps(result, ensure_ascii=False).encode("utf-8")) <= 100_000
    assert result["truncation"]["applied"] is True
    assert result["truncation"]["reason"] == "note_comparison_output_budget"
    assert result["truncation"]["output_budget_applied"] is True
    assert result["truncation"]["max_output_bytes"] == 100_000
    for row in result["topics"][0]["rows"]:
        assert row["raw_text_length"] > len(row["raw_text"] or "")
        assert len(row["value_or_excerpt"] or "") <= len(row["raw_text"] or "")


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
