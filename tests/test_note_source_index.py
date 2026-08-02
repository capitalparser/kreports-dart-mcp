from __future__ import annotations

import json


def test_note_source_parser_preserves_raw_chapters_and_normalizes_required_topics():
    from kreports.processor.note_source_index import parse_note_source_document

    content = """
    <DOCUMENT>
      <TITLE>연결재무제표 주석</TITLE>
      <P>10. 리스</P><P>사용권자산과 리스부채를 현재가치로 측정합니다.</P>
      <P>11. 금융상품</P><P>금융자산의 공정가치와 신용위험을 공시합니다.</P>
      <P>12. 특수관계자 거래</P><P>특수관계자와의 매출 거래 내역입니다.</P>
      <P>13. 손상차손</P><P>현금창출단위 손상검사를 수행했습니다.</P>
      <P>14. 충당부채 및 우발부채</P><P>소송 관련 충당부채와 우발상황입니다.</P>
      <P>15. 종속기업</P><P>연결대상 종속기업의 변동입니다.</P>
      <P>16. 보고기간후사건</P><P>보고기간후 발생한 주요 사건입니다.</P>
      <P>17. 중요한 회계정책</P><P>수익인식 회계정책을 적용합니다.</P>
    </DOCUMENT>
    """

    result = parse_note_source_document(
        {
            "id": 7,
            "corp_code": "00000001",
            "bsns_year": 2024,
            "source_type": "business_report",
            "rcept_no": "20250331000001",
            "dcm_no": None,
            "doc_hash": "a" * 40,
            "storage_uri": "file://raw/00000001.xml.gz",
            "content_length": len(content),
            "compressed_length": 321,
            "storage_status": "externalized",
        },
        content,
    )

    assert result["status"] == "available"
    assert [chapter["note_no"] for chapter in result["chapters"]] == [
        "10", "11", "12", "13", "14", "15", "16", "17",
    ]
    assert [chapter["topics"] for chapter in result["chapters"]] == [
        ["leases"], ["financial_instruments"], ["related_parties"],
        ["impairment"], ["provisions_contingencies"], ["subsidiaries"],
        ["subsequent_events"], ["accounting_policies"],
    ]
    lease = result["chapters"][0]
    assert lease["fs_div"] == "CFS"
    assert lease["raw_body"].startswith("<P>10. 리스</P>")
    assert content[lease["raw_start"]:lease["raw_end"]].strip() == lease["raw_body"]
    assert lease["raw_span_locator"] == (
        f"source_documents:7#chars={lease['raw_start']}-{lease['raw_end']}"
    )
    assert len(lease["raw_body_hash"]) == 40
    assert "리스부채" in lease["body_text"]
    assert lease["source_document_id"] == 7
    assert lease["full_text_uri"] == "file://raw/00000001.xml.gz"
    assert lease["full_text_hash"] == "a" * 40
    assert lease["full_text_storage_status"] == "externalized"


def test_note_source_index_is_dry_run_only_and_reports_inline_externalized_coverage(temp_engine):
    from kreports.db.engine import get_session
    from kreports.db.models import AccountingNoteChapter, SourceDocument
    from kreports.processor.note_source_index import build_note_source_index

    inline = "<DOCUMENT><P>1. 리스</P><P>사용권자산과 리스부채입니다.</P></DOCUMENT>"
    external = "<html><body><h2>2. 금융상품</h2><p>공정가치와 신용위험입니다.</p></body></html>"
    with get_session() as session:
        session.add_all([
            SourceDocument(
                rcept_no="20250331000001", corp_code="00000001", bsns_year=2024,
                source_type="business_report", report_nm="사업보고서", content_type="xml",
                raw_content=inline, doc_hash="a" * 40, storage_status="inline",
            ),
            SourceDocument(
                rcept_no="20250401000002", corp_code="00000002", bsns_year=2024,
                source_type="audit_report", report_nm="감사보고서", content_type="html",
                raw_content="", doc_hash="b" * 40, storage_uri="raw://audit/2",
                content_length=len(external), compressed_length=20, storage_status="externalized",
            ),
        ])

    result = build_note_source_index(
        year=2024,
        _read_engine=temp_engine,
        _content_loader=lambda row: external if row["storage_uri"] else row["raw_content"],
    )

    assert result["mode"] == "dry_run_read_only"
    assert result["write_boundary"]["writes_performed"] is False
    assert result["documents"] == {
        "scanned": 2,
        "available": 2,
        "summary_only": 0,
        "malformed": 0,
        "unavailable": 0,
    }
    assert result["topic_coverage"] == {"financial_instruments": 1, "leases": 1}
    assert [chapter["source_type"] for chapter in result["chapters"]] == [
        "audit_report", "business_report",
    ]
    with get_session() as session:
        assert session.query(AccountingNoteChapter).count() == 0


def test_note_source_index_reports_malformed_and_unavailable_without_writing(temp_engine):
    from kreports.db.engine import get_session
    from kreports.db.models import SourceDocument
    from kreports.processor.note_source_index import build_note_source_index

    with get_session() as session:
        session.add_all([
            SourceDocument(
                rcept_no="20250331000001", corp_code="00000001", bsns_year=2024,
                source_type="business_report", report_nm="사업보고서", content_type="xml",
                raw_content="<DOCUMENT><P>번호 없는 주석</P></DOCUMENT>", doc_hash="a" * 40,
            ),
            SourceDocument(
                rcept_no="20250331000002", corp_code="00000002", bsns_year=2024,
                source_type="business_report", report_nm="사업보고서", content_type="xml",
                raw_content="", doc_hash="b" * 40, storage_uri="raw://missing/2",
                storage_status="externalized",
            ),
        ])

    result = build_note_source_index(
        year=2024,
        _read_engine=temp_engine,
        _content_loader=lambda row: (_ for _ in ()).throw(FileNotFoundError(row["storage_uri"])),
    )

    assert result["documents"]["malformed"] == 1
    assert result["documents"]["summary_only"] == 0
    assert result["documents"]["unavailable"] == 1
    assert result["chapters"] == []
    assert result["limitations"] == [
        "malformed_note_headings:20250331000001",
        "raw_content_unavailable:20250331000002",
    ]


def test_note_source_parser_normalizes_spaced_korean_topics_without_risk_false_positive():
    from kreports.processor.note_source_index import parse_note_source_document

    result = parse_note_source_document(
        {"id": 8, "source_type": "business_report", "storage_status": "inline"},
        """
        <DOCUMENT>
          <P>1. 보고기간 후 사건</P><P>후속 공시를 검토했습니다.</P>
          <P>2. 특수 관계자 거래</P><P>관계회사 매출입니다.</P>
          <P>3. 중요한 회계 정책</P><P>수익인식 정책입니다.</P>
          <P>4. 시장 리스크 관리</P><P>환율 변동을 모니터링합니다.</P>
        </DOCUMENT>
        """,
    )

    assert [chapter["topics"] for chapter in result["chapters"]] == [
        ["subsequent_events"], ["related_parties"], ["accounting_policies"],
    ]
    assert all(chapter["note_title"] != "시장 리스크 관리" for chapter in result["chapters"])


def test_note_source_parser_supports_spaced_fs_div_marker_and_markerless_audit_rule():
    from kreports.processor.note_source_index import parse_note_source_document

    spaced_cfs = parse_note_source_document(
        {"id": 8, "source_type": "business_report", "storage_status": "inline"},
        "<DOCUMENT><TITLE>연 결 재 무 제 표 주 석</TITLE><P>1. 리스</P><P>리스부채입니다.</P></DOCUMENT>",
    )
    spaced_ofs = parse_note_source_document(
        {"id": 9, "source_type": "business_report", "storage_status": "inline"},
        "<DOCUMENT><TITLE>별 도 재 무 제 표 주 석</TITLE><P>1. 리스</P><P>리스부채입니다.</P></DOCUMENT>",
    )
    markerless_audit = parse_note_source_document(
        {"id": 10, "source_type": "audit_report", "storage_status": "inline"},
        "<DOCUMENT><P>1. 금융상품</P><P>공정가치 공시입니다.</P></DOCUMENT>",
    )

    assert spaced_cfs["chapters"][0]["fs_div"] == "CFS"
    assert spaced_ofs["chapters"][0]["fs_div"] == "OFS"
    assert markerless_audit["chapters"][0]["fs_div"] == "OFS"


def test_note_source_parser_never_labels_missing_external_raw_as_summary_only():
    from kreports.processor.note_source_index import parse_note_source_document

    result = parse_note_source_document(
        {"id": 11, "source_type": "audit_report", "storage_uri": "raw://missing", "storage_status": "externalized"},
        "",
    )

    assert result["status"] == "unavailable"


def test_note_source_index_makes_inline_missing_raw_content_explicit(temp_engine):
    from kreports.db.engine import get_session
    from kreports.db.models import SourceDocument
    from kreports.processor.note_source_index import build_note_source_index

    with get_session() as session:
        session.add(SourceDocument(
            rcept_no="20250331000003", corp_code="00000003", bsns_year=2024,
            source_type="business_report", report_nm="사업보고서", content_type="xml",
            raw_content="", doc_hash="c" * 40, storage_status="inline",
        ))

    result = build_note_source_index(year=2024, _read_engine=temp_engine)

    assert result["documents"]["unavailable"] == 1
    assert result["limitations"] == ["raw_content_unavailable:20250331000003"]


def test_note_source_index_cli_exposes_read_only_coverage(monkeypatch):
    from typer.testing import CliRunner

    import kreports.processor.note_source_index as index_module
    from kreports.cli.main import app

    monkeypatch.setattr(index_module, "build_note_source_index", lambda **kwargs: {
        "mode": "dry_run_read_only",
        "write_boundary": {"writes_performed": False},
        "arguments": kwargs,
    })

    result = CliRunner().invoke(app, ["index-note-sources", "--year", "2024", "--limit", "2"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["mode"] == "dry_run_read_only"
    assert payload["arguments"]["include_chapters"] is False
