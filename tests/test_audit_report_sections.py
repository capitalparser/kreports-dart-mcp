from datetime import date, datetime

from kreports.analysis.api import compare_peer_kam_topics, get_audit_report_sections
from kreports.collector.fetcher import parse_attachment_options
from kreports.collector.report_document_collector import collect_report_sections_for_disclosure
from kreports.db.models import Company, Disclosure, Financial, ReportDocument, ReportSection, SourceDocument
from kreports.processor.audit_report_parser import (
    classify_kam_topics,
    extract_audit_report_sections,
    summarize_kam_body,
)


def test_extract_audit_report_sections_finds_kam_and_opinion():
    xml = """
    <DOCUMENT>
      <TITLE>감사의견</TITLE>
      <P>우리는 회사의 재무제표가 중요성의 관점에서 적정하게 표시하고 있다고 봅니다.</P>
      <TITLE>핵심감사사항</TITLE>
      <P>수익인식과 재고자산 평가충당금은 핵심감사사항입니다.</P>
      <TITLE>강조사항</TITLE>
      <P>계속기업 관련 중요한 불확실성은 없습니다.</P>
      <TITLE>재무제표에 대한 경영진의 책임</TITLE>
      <P>경영진은 재무제표 작성 책임이 있습니다.</P>
    </DOCUMENT>
    """

    sections = extract_audit_report_sections(xml)

    assert "audit_opinion" in sections
    assert "kam" in sections
    assert "emphasis" in sections
    assert "수익인식" in sections["kam"]["body_text"]
    assert classify_kam_topics(sections["kam"]["body_text"]) == ["revenue", "inventory"]


def test_summarize_kam_body_extracts_reason_and_audit_response():
    body = """
    핵심감사사항
    회사는 진행기준 매출을 인식하고 있으며, 추정의 불확실성과 중요한 왜곡표시위험이 존재하므로
    수익인식을 핵심감사사항으로 결정하였습니다.
    우리는 이와 관련하여 내부통제 이해 및 평가, 계약서 표본검토, 진행률 재계산,
    매출 cutoff 테스트 등의 감사절차를 수행하였습니다.
    """

    summary = summarize_kam_body(body)

    assert "revenue" in summary["topics"]
    assert summary["reason_excerpt"]
    assert "핵심감사사항" in summary["reason_excerpt"]
    assert summary["procedure_excerpt"]
    assert "감사절차" in summary["procedure_excerpt"]
    assert summary["has_reason_hint"] is True
    assert summary["has_procedure_hint"] is True


def test_extract_audit_report_sections_does_not_treat_auditor_responsibility_phrase_as_kam():
    xml = """
    <DOCUMENT>
      <P>감사인의 책임</P>
      <P>우리는 지배기구와 커뮤니케이션한 사항들 중에서 당기 재무제표감사에서 가장 유의적인 사항들을 핵심감사사항으로 결정합니다.</P>
      <P>법규에서 해당 사항에 대하여 공개적인 공시를 배제하거나 감사보고서에 커뮤니케이션해서는 안 된다고 결론을 내리는 경우가 아닌 한, 우리는 감사보고서에 이러한 사항들을 기술합니다.</P>
      <P>이 감사보고서의 근거가 된 감사를 실시한 업무수행이사는 공인회계사입니다.</P>
    </DOCUMENT>
    """

    sections = extract_audit_report_sections(xml)

    assert "kam" not in sections


def test_extract_audit_report_sections_trims_other_matter_before_attached_financials():
    xml = """
    <DOCUMENT>
      <TITLE>기타사항</TITLE>
      <P>기타사항 본문입니다. 전기 재무제표 감사인과 비교정보를 설명합니다.</P>
      <P>이 감사보고서의 근거가 된 감사를 실시한 업무수행이사는 공인회계사입니다.</P>
      <P>(첨부)재 무 제 표</P>
      <P>삼성전자주식회사 제 53 기 재무상태표와 손익계산서 본문입니다.</P>
      <TITLE>재무제표에 대한 경영진의 책임</TITLE>
      <P>경영진 책임입니다.</P>
    </DOCUMENT>
    """

    sections = extract_audit_report_sections(xml)

    assert "other_matter" in sections
    assert "기타사항 본문" in sections["other_matter"]["body_text"]
    assert "재무상태표" not in sections["other_matter"]["body_text"]
    assert len(sections["other_matter"]["body_text"]) < 120


def test_parse_attachment_options_reads_dcm_no_from_dart_main_html():
    html = """
    <select id="att">
      <option value="">첨부선택</option>
      <option value="rcpNo=20250218800508&amp;dcmNo=10316976">감사보고서</option>
      <option value="rcpNo=20250218800508&amp;dcmNo=10316977">연결감사보고서</option>
      <option value="rcpNo=20250218800508&amp;dcmNo=10316978">내부회계관리제도 감사보고서</option>
    </select>
    """

    options = parse_attachment_options(html)

    assert options[:2] == [
        {"rcept_no": "20250218800508", "dcm_no": "10316976", "title": "감사보고서"},
        {"rcept_no": "20250218800508", "dcm_no": "10316977", "title": "연결감사보고서"},
    ]


def test_collect_audit_submission_uses_attachment_viewer_html(temp_engine, monkeypatch):
    import kreports.collector.report_document_collector as collector_module
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(corp_code="00126380", stock_code="005930", corp_name="삼성전자", market="KOSPI"))
        session.add(Disclosure(
            rcept_no="20250218800508",
            corp_code="00126380",
            corp_name="삼성전자",
            disc_date=date(2025, 2, 18),
            disc_type="F",
            report_nm="감사보고서제출",
        ))

    monkeypatch.setattr(collector_module, "fetch_document_xml", lambda _rcept_no: None)
    monkeypatch.setattr(
        collector_module,
        "fetch_dart_main_html",
        lambda _rcept_no: """
        <select id="att">
          <option value="rcpNo=20250218800508&amp;dcmNo=10316976">감사보고서</option>
          <option value="rcpNo=20250218800508&amp;dcmNo=10316977">연결감사보고서</option>
          <option value="rcpNo=20250218800508&amp;dcmNo=10316978">내부회계관리제도 감사보고서</option>
        </select>
        """,
    )
    monkeypatch.setattr(
        collector_module,
        "fetch_viewer_html",
        lambda _rcept_no, dcm_no: f"""
        <DOCUMENT>
          <TITLE>감사의견</TITLE>
          <P>{dcm_no} 감사의견은 적정입니다.</P>
          <TITLE>핵심감사사항</TITLE>
          <P>건설중인자산의 감가상각개시시점 평가를 핵심감사사항으로 결정한 이유입니다.</P>
          <P>감사에서 다루어진 방법은 내부통제 테스트와 관련 문서 대사입니다.</P>
          <TITLE>재무제표에 대한 경영진의 책임</TITLE>
          <P>경영진 책임입니다.</P>
        </DOCUMENT>
        """,
    )

    result = collect_report_sections_for_disclosure("20250218800508")

    assert result["ok"] == 1
    assert result["documents"] == 2
    assert result["sections"] >= 4
    with get_session() as session:
        docs = session.query(ReportDocument).order_by(ReportDocument.dcm_no).all()
        sections = session.query(ReportSection).filter_by(section_key="kam").order_by(ReportSection.dcm_no).all()
        doc_dcm_nos = [doc.dcm_no for doc in docs]
        section_rcept_nos = [section.rcept_no for section in sections]
        first_kam_body = sections[0].body_text
    assert doc_dcm_nos == ["10316976", "10316977"]
    assert section_rcept_nos == [
        "20250218800508_10316976",
        "20250218800508_10316977",
    ]
    assert "감가상각개시시점" in first_kam_body


def test_collect_business_report_collects_summary_and_attached_audit_reports(temp_engine, monkeypatch):
    import kreports.collector.report_document_collector as collector_module
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(corp_code="00126380", stock_code="005930", corp_name="삼성전자", market="KOSPI"))
        session.add(Disclosure(
            rcept_no="20250311001085",
            corp_code="00126380",
            corp_name="삼성전자",
            disc_date=date(2025, 3, 11),
            disc_type="F",
            report_nm="사업보고서 (2024.12)",
        ))

    monkeypatch.setattr(collector_module, "fetch_document_xml", lambda _rcept_no: None)
    monkeypatch.setattr(
        collector_module,
        "fetch_document_zip_files",
        lambda _rcept_no: {
            "20250311001085.xml": """
        <DOCUMENT>
          <DOCUMENT-NAME>사업보고서</DOCUMENT-NAME>
          <TITLE>핵심감사사항</TITLE>
          <P>사업보고서 안의 KAM 요약입니다.</P>
          <TITLE>강조사항</TITLE>
          <P>사업보고서 강조사항 요약입니다.</P>
        </DOCUMENT>
        """,
            "audit.xml": """
        <DOCUMENT>
          <DOCUMENT-NAME>감사보고서</DOCUMENT-NAME>
          <TITLE>감사의견</TITLE>
          <P>별도 감사의견은 적정입니다.</P>
          <TITLE>핵심감사사항</TITLE>
          <P>첨부 감사보고서 상세 KAM입니다. 핵심감사사항으로 결정한 이유와 감사에서 다루어진 방법입니다.</P>
          <TITLE>재무제표에 대한 경영진의 책임</TITLE>
          <P>경영진 책임입니다.</P>
        </DOCUMENT>
        """,
            "audit_committee.xml": """
        <DOCUMENT>
          <DOCUMENT-NAME>감사의감사보고서</DOCUMENT-NAME>
          <TITLE>감사의 감사보고서</TITLE>
          <P>제외 대상입니다.</P>
        </DOCUMENT>
        """,
            "audit_consolidated.xml": """
        <DOCUMENT>
          <DOCUMENT-NAME>연결감사보고서</DOCUMENT-NAME>
          <TITLE>감사의견</TITLE>
          <P>연결 감사의견은 적정입니다.</P>
          <TITLE>핵심감사사항</TITLE>
          <P>연결 첨부 감사보고서 상세 KAM입니다. 핵심감사사항으로 결정한 이유와 감사에서 다루어진 방법입니다.</P>
          <TITLE>재무제표에 대한 경영진의 책임</TITLE>
          <P>경영진 책임입니다.</P>
        </DOCUMENT>
        """,
        },
    )
    monkeypatch.setattr(
        collector_module,
        "fetch_dart_main_html",
        lambda _rcept_no: """
        <select id="att">
          <option value="rcpNo=20250311001085&amp;dcmNo=10392690">감사보고서</option>
          <option value="rcpNo=20250311001085&amp;dcmNo=10392715">감사의감사보고서</option>
          <option value="rcpNo=20250311001085&amp;dcmNo=10392691">연결감사보고서</option>
        </select>
        """,
    )
    monkeypatch.setattr(
        collector_module,
        "fetch_viewer_html",
        lambda _rcept_no, dcm_no: f"""
        <DOCUMENT>
          <TITLE>감사의견</TITLE>
          <P>{dcm_no} 감사의견은 적정입니다.</P>
          <TITLE>핵심감사사항</TITLE>
          <P>첨부 감사보고서 상세 KAM입니다. 핵심감사사항으로 결정한 이유와 감사에서 다루어진 방법입니다.</P>
          <TITLE>재무제표에 대한 경영진의 책임</TITLE>
          <P>경영진 책임입니다.</P>
        </DOCUMENT>
        """,
    )

    result = collect_report_sections_for_disclosure("20250311001085")

    assert result["ok"] == 1
    assert result["documents"] == 3
    assert result["business_report_sections"] >= 2
    assert result["audit_report_sections"] >= 4
    with get_session() as session:
        business_kam = session.query(ReportSection).filter_by(
            rcept_no="20250311001085",
            source_type="business_report",
            section_key="kam",
        ).one()
        audit_kams = session.query(ReportSection).filter_by(
            source_type="audit_report",
            section_key="kam",
        ).order_by(ReportSection.dcm_no).all()
        audit_dcm_nos = [row.dcm_no for row in audit_kams]
        business_kam_text = business_kam.body_text
        audit_text = audit_kams[0].body_text
    assert "요약" in business_kam_text
    assert audit_dcm_nos == ["audit_consolidated_x", "audit_xml"]
    assert "감사에서 다루어진 방법" in audit_text


def test_collect_business_report_uses_viewer_html_when_document_api_unavailable(temp_engine, monkeypatch):
    import kreports.collector.report_document_collector as collector_module
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(corp_code="00126380", stock_code="005930", corp_name="삼성전자", market="KOSPI"))
        session.add(Disclosure(
            rcept_no="20250311001085",
            corp_code="00126380",
            corp_name="삼성전자",
            disc_date=date(2025, 3, 11),
            disc_type="F",
            report_nm="사업보고서 (2024.12)",
        ))

    monkeypatch.setattr(collector_module, "fetch_document_zip_files", lambda _rcept_no: {})
    monkeypatch.setattr(collector_module, "fetch_document_xml", lambda _rcept_no: None)
    monkeypatch.setattr(
        collector_module,
        "fetch_dart_main_html",
        lambda _rcept_no: """
        <select id="att">
          <option value="rcpNo=20250311001085&amp;dcmNo=10392689">사업보고서</option>
          <option value="rcpNo=20250311001085&amp;dcmNo=10392690">감사보고서</option>
        </select>
        """,
    )
    monkeypatch.setattr(
        collector_module,
        "fetch_viewer_html",
        lambda _rcept_no, dcm_no: f"""
        <html><body>
          <h1>{dcm_no} 사업보고서</h1>
          <p>II. 사업의 내용</p>
          <p>회사는 반도체와 디스플레이 제품을 판매합니다.</p>
          <p>핵심감사사항</p>
          <p>수익인식 관련 핵심감사사항 요약입니다.</p>
        </body></html>
        """,
    )

    result = collect_report_sections_for_disclosure("20250311001085")

    assert result["ok"] == 1
    assert result["source"] == "dart_viewer_html"
    with get_session() as session:
        source_doc = session.query(SourceDocument).filter_by(
            rcept_no="20250311001085",
            source_type="business_report",
        ).one()
        section = session.query(ReportSection).filter_by(
            rcept_no="20250311001085",
            source_type="business_report",
            section_key="kam",
        ).one()
        source_content_type = source_doc.content_type
        source_raw_content = source_doc.raw_content
        section_body = section.body_text
    assert source_content_type == "html"
    assert "사업보고서" in source_raw_content
    assert "수익인식" in section_body


def test_collect_business_report_uses_viewer_tree_when_attachment_option_missing(temp_engine, monkeypatch):
    import kreports.collector.report_document_collector as collector_module
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(corp_code="00126380", stock_code="005930", corp_name="삼성전자", market="KOSPI"))
        session.add(Disclosure(
            rcept_no="20250311001085",
            corp_code="00126380",
            corp_name="삼성전자",
            disc_date=date(2025, 3, 11),
            disc_type="F",
            report_nm="사업보고서 (2024.12)",
        ))

    monkeypatch.setattr(collector_module, "fetch_document_zip_files", lambda _rcept_no: {})
    monkeypatch.setattr(collector_module, "fetch_document_xml", lambda _rcept_no: None)
    monkeypatch.setattr(
        collector_module,
        "fetch_dart_main_html",
        lambda _rcept_no: """
        <script>
        var node1 = {};
        node1['text'] = "사 업 보 고 서";
        node1['rcpNo'] = "20250311001085";
        node1['dcmNo'] = "10392689";
        node1['eleId'] = "1";
        node1['offset'] = "100";
        node1['length'] = "200";
        node1['dtd'] = "dart4.xsd";
        </script>
        """,
    )

    calls = []

    def fake_viewer(rcept_no, dcm_no, **kwargs):
        calls.append((rcept_no, dcm_no, kwargs))
        return """
        <html><body>
          <p>핵심감사사항</p>
          <p>재고자산 평가와 관련하여 감사절차를 수행하였습니다.</p>
        </body></html>
        """

    monkeypatch.setattr(collector_module, "fetch_viewer_html", fake_viewer)

    result = collect_report_sections_for_disclosure("20250311001085")

    assert result["ok"] == 1
    assert result["source"] == "dart_viewer_html"
    assert calls[0][1] == "10392689"
    assert calls[0][2]["ele_id"] == "1"
    assert calls[0][2]["dtd"] == "dart4.xsd"
    with get_session() as session:
        source_doc = session.query(SourceDocument).filter_by(
            rcept_no="20250311001085",
            source_type="business_report",
        ).one()
        section = session.query(ReportSection).filter_by(
            rcept_no="20250311001085",
            source_type="business_report",
            section_key="kam",
        ).one()
        source_content_type = source_doc.content_type
        section_body = section.body_text
    assert source_content_type == "html"
    assert "재고자산" in section_body


def test_get_audit_report_sections_reads_cached_sections(temp_engine):
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(corp_code="00000001", stock_code="000001", corp_name="샘플", market="KOSPI"))
        session.add(ReportSection(
            rcept_no="20250331000001",
            corp_code="00000001",
            bsns_year=2024,
            source_type="audit_report",
            section_key="kam",
            section_title="핵심감사사항",
            body_text="수익인식이 핵심감사사항입니다.",
            body_hash="x",
            body_length=15,
            ordinal=0,
            fetched_at=datetime.utcnow(),
        ))

    out = get_audit_report_sections("000001", year=2024, section_key="kam")

    assert out["section_count"] == 1
    assert out["sections"][0]["section_key"] == "kam"
    assert "수익인식" in out["sections"][0]["body_excerpt"]


def test_get_audit_report_sections_adds_kam_analysis(temp_engine):
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(corp_code="00000001", stock_code="000001", corp_name="샘플", market="KOSPI"))
        session.add(ReportSection(
            rcept_no="20250331000001",
            corp_code="00000001",
            bsns_year=2024,
            source_type="audit_report",
            section_key="kam",
            section_title="핵심감사사항",
            body_text=(
                "수익인식은 추정 불확실성과 중요한 왜곡표시위험으로 인해 "
                "핵심감사사항으로 결정되었습니다. 감사절차로 계약서 검토와 "
                "매출 cutoff 테스트를 수행하였습니다."
            ),
            body_hash="x",
            body_length=80,
            ordinal=0,
            fetched_at=datetime.utcnow(),
        ))

    out = get_audit_report_sections("000001", year=2024, section_key="kam")

    analysis = out["sections"][0]["kam_analysis"]
    assert analysis["has_reason_hint"] is True
    assert analysis["has_procedure_hint"] is True
    assert "revenue" in analysis["topics"]
    assert out["data_quality"]["kam_reason_coverage"]["with_reason_hint"] == 1
    assert out["data_quality"]["kam_procedure_coverage"]["with_procedure_hint"] == 1


def test_get_audit_report_sections_returns_alternative_cached_year_when_requested_year_missing(temp_engine):
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(corp_code="00000001", stock_code="000001", corp_name="샘플", market="KOSPI"))
        session.add(ReportSection(
            rcept_no="20240331000001",
            corp_code="00000001",
            bsns_year=2023,
            source_type="audit_report",
            section_key="kam",
            section_title="핵심감사사항",
            body_text="재고자산 평가가 핵심감사사항입니다. 감사절차로 순실현가능가치를 검토하였습니다.",
            body_hash="x",
            body_length=50,
            ordinal=0,
            fetched_at=datetime.utcnow(),
        ))

    out = get_audit_report_sections("000001", year=2024, section_key="kam")

    assert out["section_count"] == 0
    assert out["data_quality"]["status"] == "missing"
    assert out["data_quality"]["latest_available_year"] == 2023
    assert out["data_quality"]["alternative_section_count"] == 1
    assert out["alternative_sections"][0]["bsns_year"] == 2023


def test_compare_peer_kam_topics_uses_cached_report_sections(temp_engine):
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add_all([
            Company(corp_code="00000001", stock_code="000001", corp_name="대상", market="KOSPI", induty_code="10101"),
            Company(corp_code="00000002", stock_code="000002", corp_name="피어", market="KOSPI", induty_code="10101"),
        ])
        for cc in ("00000001", "00000002"):
            session.add(Financial(
                corp_code=cc,
                year=2024,
                quarter=4,
                fs_div="CFS",
                revenue=100,
                operating_profit=10,
                net_income=8,
                total_assets=1000,
                total_debt=400,
                total_equity=600,
            ))
            session.add(Disclosure(
                rcept_no=f"20250331{cc[-6:]}",
                corp_code=cc,
                corp_name="대상" if cc.endswith("1") else "피어",
                disc_date=date(2025, 3, 31),
                disc_type="F",
                report_nm="감사보고서 (2024.12)",
            ))
            session.add(ReportSection(
                rcept_no=f"20250331{cc[-6:]}",
                corp_code=cc,
                bsns_year=2024,
                source_type="audit_report",
                section_key="kam",
                section_title="핵심감사사항",
                body_text="매출 수익인식 관련 핵심감사사항입니다.",
                body_hash=cc,
                body_length=20,
                ordinal=0,
                fetched_at=datetime.utcnow(),
            ))

    out = compare_peer_kam_topics("000001", year=2024, peer_limit=5)

    assert out["audit_report_sections"]["source"] == "audit_report_sections"
    assert out["audit_report_sections"]["kam_body_count"] == 2
    assert out["kam_topics"]["revenue"] == 2


def test_compare_peer_kam_topics_reports_reason_and_procedure_coverage(temp_engine):
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add_all([
            Company(corp_code="00000001", stock_code="000001", corp_name="대상", market="KOSPI", induty_code="10101"),
            Company(corp_code="00000002", stock_code="000002", corp_name="피어", market="KOSPI", induty_code="10101"),
        ])
        for cc in ("00000001", "00000002"):
            session.add(Financial(
                corp_code=cc,
                year=2024,
                quarter=4,
                fs_div="CFS",
                revenue=100,
                operating_profit=10,
                net_income=8,
                total_assets=1000,
                total_debt=400,
                total_equity=600,
            ))
            session.add(ReportSection(
                rcept_no=f"20250331{cc[-6:]}",
                corp_code=cc,
                bsns_year=2024,
                source_type="audit_report",
                section_key="kam",
                section_title="핵심감사사항",
                body_text=(
                    "매출 수익인식은 중요한 왜곡표시위험이 있어 핵심감사사항으로 결정되었습니다. "
                    "감사절차로 계약서 검토, 진행률 재계산, 매출 cutoff 테스트를 수행하였습니다."
                ),
                body_hash=cc,
                body_length=90,
                ordinal=0,
                fetched_at=datetime.utcnow(),
            ))

    out = compare_peer_kam_topics("000001", year=2024, peer_limit=5)

    assert out["audit_report_sections"]["kam_reason_coverage"]["with_reason_hint"] == 2
    assert out["audit_report_sections"]["kam_procedure_coverage"]["with_procedure_hint"] == 2
    assert out["subject_sections"][0]["kam_analysis"]["procedure_excerpt"]
