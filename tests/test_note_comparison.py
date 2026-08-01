from __future__ import annotations


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
