from datetime import date

from sqlalchemy import text

from kreports.analysis.api import get_business_overview, get_subsidiary_auditors
from kreports.collector.report_document_collector import (
    collect_report_sections_for_disclosure,
    run_document_extractors,
)
from kreports.db.models import Auditor, Company, Disclosure, ReportSection, SourceDocument


def _create_subsidiary_auditor_matrix_cache(session):
    session.execute(text("""
        CREATE TABLE IF NOT EXISTS subsidiary_auditor_matrix (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parent_corp_code VARCHAR(8) NOT NULL,
            parent_rcept_no VARCHAR(14) NOT NULL,
            bsns_year SMALLINT NOT NULL,
            name VARCHAR(200) NOT NULL,
            relation VARCHAR(50),
            ownership_pct FLOAT,
            business TEXT,
            listed_yn VARCHAR(1),
            corp_code VARCHAR(8),
            stock_code VARCHAR(6),
            market VARCHAR(10),
            auditor_nm VARCHAR(100),
            auditor_year SMALLINT,
            audit_opinion VARCHAR(20),
            source VARCHAR(50),
            ordinal SMALLINT NOT NULL DEFAULT 0,
            fetched_at DATETIME
        )
    """))


def _insert_subsidiary_cache_row(session, **overrides):
    values = {
        "parent_corp_code": "00000001",
        "parent_rcept_no": "20250331000002",
        "bsns_year": 2024,
        "name": "자회사",
        "relation": "subsidiary",
        "ownership_pct": 100.0,
        "business": "소프트웨어",
        "listed_yn": "Y",
        "corp_code": "00000002",
        "stock_code": "000002",
        "market": "KOSPI",
        "auditor_nm": "삼일회계법인",
        "auditor_year": 2024,
        "audit_opinion": "적정",
        "source": "SUB_CMPN",
        "ordinal": 0,
        "fetched_at": "2026-05-17 00:00:00",
    }
    values.update(overrides)
    session.execute(text("""
        INSERT INTO subsidiary_auditor_matrix (
            parent_corp_code, parent_rcept_no, bsns_year, name, relation,
            ownership_pct, business, listed_yn, corp_code, stock_code, market,
            auditor_nm, auditor_year, audit_opinion, source, ordinal, fetched_at
        )
        VALUES (
            :parent_corp_code, :parent_rcept_no, :bsns_year, :name, :relation,
            :ownership_pct, :business, :listed_yn, :corp_code, :stock_code, :market,
            :auditor_nm, :auditor_year, :audit_opinion, :source, :ordinal, :fetched_at
        )
    """), values)


def test_business_report_collector_persists_management_sections_for_mcp(temp_engine, monkeypatch):
    import kreports.collector.report_document_collector as collector_module
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(
            corp_code="00000001",
            stock_code="000001",
            corp_name="대상",
            market="KOSPI",
            induty_code="58221",
        ))
        session.add(Disclosure(
            rcept_no="20250331000001",
            corp_code="00000001",
            corp_name="대상",
            disc_date=date(2025, 3, 31),
            disc_type="F",
            report_nm="사업보고서 (2024.12)",
        ))

    monkeypatch.setattr(
        collector_module,
        "fetch_document_zip_files",
        lambda _rcept_no: {
            "20250331000001.xml": """
            <DOCUMENT>
              <DOCUMENT-NAME>사업보고서</DOCUMENT-NAME>
              <TITLE>II. 사업의 내용</TITLE>
              <P>회사는 클라우드 소프트웨어 서비스를 제공합니다.</P>
              <TITLE>1. 사업의 개요</TITLE>
              <P>구독형 플랫폼과 구축형 솔루션을 판매합니다.</P>
              <TITLE>2. 시장위험과 위험관리</TITLE>
              <P>환율 위험과 유동성 위험을 관리합니다.</P>
              <TITLE>3. 연구개발활동</TITLE>
              <P>연구개발비 / 매출액 비율은 8.5%입니다.</P>
              <TITLE>4. 향후 추진계획</TITLE>
              <P>해외 SaaS 매출 확대와 파트너 채널 강화를 추진합니다.</P>
              <TITLE>5. 경영상의 주요계약</TITLE>
              <P>주요 고객과 공급계약을 체결했습니다.</P>
              <TITLE>III. 재무에 관한 사항</TITLE>
              <P>재무 섹션입니다.</P>
            </DOCUMENT>
            """,
        },
    )
    monkeypatch.setattr(collector_module, "fetch_dart_main_html", lambda _rcept_no: "")

    result = collect_report_sections_for_disclosure("20250331000001")

    assert result["ok"] == 1
    with get_session() as session:
        section_keys = {
            row.section_key
            for row in session.query(ReportSection).filter_by(
                corp_code="00000001",
                bsns_year=2024,
                source_type="business_report",
            )
        }
    assert {
        "business_description",
        "business_overview",
        "risk_management",
        "management_plan",
        "rd_activities",
        "key_contracts",
    } <= section_keys

    overview = get_business_overview("000001", bsns_year=2024)
    assert overview["data_quality"]["status"] == "usable"
    assert overview["section_count"] >= 6
    assert "구독형 플랫폼" in overview["sections"]["business_overview"]["body_text"]
    assert "파트너 채널" in overview["sections"]["management_plan"]["body_text"]


def test_business_report_backfill_targets_existing_sections_when_source_document_missing(temp_engine, monkeypatch):
    import kreports.collector.report_document_collector as collector_module
    from kreports.db.engine import get_session
    from kreports.db.models import BusinessAffiliateAuditor

    with get_session() as session:
        session.add(Company(
            corp_code="00000001",
            stock_code="000001",
            corp_name="대상",
            market="KOSPI",
            induty_code="58221",
        ))
        session.add(Disclosure(
            rcept_no="20250331000001",
            corp_code="00000001",
            corp_name="대상",
            disc_date=date(2025, 3, 31),
            disc_type="F",
            report_nm="사업보고서 (2024.12)",
        ))
        session.add(ReportSection(
            rcept_no="20250331000001",
            corp_code="00000001",
            bsns_year=2024,
            source_type="business_report",
            section_key="business_overview",
            section_title="사업의 개요",
            body_text="이미 저장된 사업 개요",
            body_hash="overview",
            body_length=11,
            ordinal=0,
        ))
        session.add(ReportSection(
            rcept_no="20250331000001_attached",
            corp_code="00000001",
            bsns_year=2024,
            source_type="audit_report",
            section_key="kam",
            section_title="핵심감사사항",
            body_text="이미 저장된 핵심감사사항",
            body_hash="kam",
            body_length=12,
            ordinal=0,
        ))
        session.add(BusinessAffiliateAuditor(
            parent_corp_code="00000001",
            parent_rcept_no="20250331000001",
            bsns_year=2024,
            name="자회사",
            ordinal=0,
        ))

    calls = []

    def fake_collect(rcept_no):
        calls.append(rcept_no)
        return {"ok": 1, "sections": 0}

    monkeypatch.setattr(collector_module, "collect_report_sections_for_disclosure", fake_collect)

    out = collector_module.collect_business_report_sections(year=2024, missing_only=True)

    assert out["total"] == 1
    assert out["ok"] == 1
    assert calls == ["20250331000001"]


def test_business_report_backfill_skips_invalid_synthetic_receipt_numbers(temp_engine, monkeypatch):
    import kreports.collector.report_document_collector as collector_module
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(
            corp_code="00000001",
            stock_code="000001",
            corp_name="대상",
            market="KOSPI",
            induty_code="58221",
        ))
        session.add(Disclosure(
            rcept_no="20250331000001",
            corp_code="00000001",
            corp_name="대상",
            disc_date=date(2025, 3, 31),
            disc_type="F",
            report_nm="사업보고서 (2024.12)",
        ))
        session.add(Disclosure(
            rcept_no="2099c35fff9f04",
            corp_code="00000001",
            corp_name="대상",
            disc_date=date(2025, 3, 31),
            disc_type="F",
            report_nm="사업보고서 (2024.12)",
        ))
        session.add(Disclosure(
            rcept_no="20990331000004",
            corp_code="00000001",
            corp_name="대상",
            disc_date=date(2025, 3, 31),
            disc_type="F",
            report_nm="사업보고서 (2024.12)",
        ))

    calls = []
    monkeypatch.setattr(
        collector_module,
        "collect_report_sections_for_disclosure",
        lambda rcept_no: calls.append(rcept_no) or {"ok": 1, "sections": 0},
    )

    out = collector_module.collect_business_report_sections(year=2024, missing_only=True)

    assert out["total"] == 1
    assert calls == ["20250331000001"]


def test_business_report_backfill_targets_derived_source_document_as_raw_missing(temp_engine, monkeypatch):
    import kreports.collector.report_document_collector as collector_module
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(
            corp_code="00000001",
            stock_code="000001",
            corp_name="대상",
            market="KOSPI",
            induty_code="58221",
        ))
        session.add(Disclosure(
            rcept_no="20250331000001",
            corp_code="00000001",
            corp_name="대상",
            disc_date=date(2025, 3, 31),
            disc_type="F",
            report_nm="사업보고서 (2024.12)",
        ))
        session.add(SourceDocument(
            rcept_no="20250331000001",
            corp_code="00000001",
            bsns_year=2024,
            source_type="business_report",
            report_nm="사업보고서 (2024.12)",
            content_type="derived_report_sections",
            raw_content="파생 문단 묶음입니다.",
            doc_hash="derived",
        ))

    calls = []
    monkeypatch.setattr(
        collector_module,
        "collect_report_sections_for_disclosure",
        lambda rcept_no: calls.append(rcept_no) or {"ok": 1, "sections": 0},
    )

    out = collector_module.collect_business_report_sections(year=2024, missing_only=True)

    assert out["total"] == 1
    assert calls == ["20250331000001"]


def test_hydrates_derived_source_documents_from_report_sections(temp_engine):
    from kreports.collector.report_document_collector import hydrate_source_documents_from_report_sections
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(
            corp_code="00000001",
            stock_code="000001",
            corp_name="대상",
            market="KOSPI",
            induty_code="58221",
        ))
        session.add(ReportSection(
            rcept_no="20250331000001",
            corp_code="00000001",
            bsns_year=2024,
            source_type="audit_report",
            section_key="kam",
            section_title="핵심감사사항",
            body_text="수익인식 관련 핵심감사사항입니다.",
            body_hash="kam",
            body_length=18,
            ordinal=0,
        ))
        session.add(ReportSection(
            rcept_no="20250331000001",
            corp_code="00000001",
            bsns_year=2024,
            source_type="audit_report",
            section_key="emphasis",
            section_title="강조사항",
            body_text="계속기업 관련 강조사항입니다.",
            body_hash="emphasis",
            body_length=16,
            ordinal=1,
        ))

    out = hydrate_source_documents_from_report_sections(year=2024, source_type="audit_report")

    assert out["total"] == 1
    assert out["created"] == 1
    with get_session() as session:
        doc = session.query(SourceDocument).filter_by(
            rcept_no="20250331000001",
            source_type="audit_report",
        ).one()
        assert doc.content_type == "derived_report_sections"
        assert "DERIVED FROM report_sections" in doc.raw_content
        assert "핵심감사사항" in doc.raw_content
        assert "계속기업 관련 강조사항" in doc.raw_content


def test_document_extractors_rerun_from_raw_source_without_dart(temp_engine, monkeypatch):
    import kreports.collector.report_document_collector as collector_module
    from kreports.db.engine import get_session
    from kreports.db.models import ExtractionRun, SourceDocument

    with get_session() as session:
        session.add(Company(
            corp_code="00000001",
            stock_code="000001",
            corp_name="대상",
            market="KOSPI",
            induty_code="58221",
        ))
        session.add(Disclosure(
            rcept_no="20250331000001",
            corp_code="00000001",
            corp_name="대상",
            disc_date=date(2025, 3, 31),
            disc_type="F",
            report_nm="사업보고서 (2024.12)",
        ))

    monkeypatch.setattr(
        collector_module,
        "fetch_document_zip_files",
        lambda _rcept_no: {
            "20250331000001.xml": """
            <DOCUMENT>
              <DOCUMENT-NAME>사업보고서</DOCUMENT-NAME>
              <TABLE-GROUP ACLASS="AUD_OPN">
                <TE ACODE="OPN_AUR1_C">삼일회계법인</TE>
                <TE ACODE="OPN_CMT1">적정의견</TE>
              </TABLE-GROUP>
              <TITLE>II. 사업의 내용</TITLE>
              <TITLE>1. 사업의 개요</TITLE>
              <P>구독형 플랫폼과 구축형 솔루션을 판매합니다.</P>
            </DOCUMENT>
            """,
        },
    )
    monkeypatch.setattr(collector_module, "fetch_dart_main_html", lambda _rcept_no: "")

    collect_report_sections_for_disclosure("20250331000001")

    def fail_dart_call(*_args, **_kwargs):
        raise AssertionError("extractor rerun must not call DART")

    monkeypatch.setattr(collector_module, "fetch_document_zip_files", fail_dart_call)
    monkeypatch.setattr(collector_module, "fetch_document_xml", fail_dart_call)
    monkeypatch.setattr(collector_module, "fetch_dart_main_html", fail_dart_call)

    out = run_document_extractors(year=2024, source_type="business_report")

    assert out["total"] == 1
    assert out["ok"] == 1
    assert out["rows_written"] >= 2
    with get_session() as session:
        assert session.query(SourceDocument).count() == 1
        assert session.query(ExtractionRun).filter_by(status="success").count() >= 2
        auditor = session.query(Auditor).filter_by(corp_code="00000001", bsns_year=2024).one()
        assert auditor.auditor_nm == "삼일회계법인"


def test_document_extractors_persist_accounting_note_chapters_from_raw_source(temp_engine, monkeypatch):
    import kreports.collector.report_document_collector as collector_module
    from kreports.db.engine import get_session
    from kreports.db.models import AccountingNoteChapter, AccountingPolicyItem

    def fail_dart_call(*_args, **_kwargs):
        raise AssertionError("note chapter extraction must use cached source_documents only")

    monkeypatch.setattr(collector_module, "fetch_document_zip_files", fail_dart_call)
    monkeypatch.setattr(collector_module, "fetch_document_xml", fail_dart_call)
    monkeypatch.setattr(collector_module, "fetch_dart_main_html", fail_dart_call)

    raw_business_report = """
    <DOCUMENT>
      <DOCUMENT-NAME>사업보고서</DOCUMENT-NAME>
      <TITLE>III. 재무에 관한 사항</TITLE>
      <TITLE>연결재무제표 주석</TITLE>
      <P>1. 일반사항</P><P>회사의 개요입니다.</P>
      <P>2. 재무제표 작성기준</P><P>연결재무제표는 한국채택국제회계기준에 따라 작성되었습니다.</P>
      <P>3. 중요한 회계정책</P><P>수익은 고객과의 계약에서 수행의무가 이행될 때 인식합니다.</P>
      <P>4. 중요한 회계추정 및 판단</P><P>손상검사와 이연법인세자산 인식에는 경영진의 판단이 필요합니다.</P>
      <P>5. 영업부문</P><P>다음 주석입니다.</P>
    </DOCUMENT>
    """

    with get_session() as session:
        session.add(SourceDocument(
            rcept_no="20250331000001",
            corp_code="00000001",
            bsns_year=2024,
            source_type="business_report",
            report_nm="사업보고서 (2024.12)",
            content_type="xml",
            raw_content=raw_business_report,
            doc_hash="cached-doc-hash",
        ))

    out = run_document_extractors(year=2024, source_type="business_report", extractor="note_chapters")

    assert out["total"] == 1
    assert out["ok"] == 1
    assert out["rows_written"] >= 3
    with get_session() as session:
        rows = (
            session.query(AccountingNoteChapter)
            .with_entities(
                AccountingNoteChapter.note_no,
                AccountingNoteChapter.section_type,
                AccountingNoteChapter.body,
            )
            .filter_by(corp_code="00000001", bsns_year=2024, fs_div="CFS")
            .order_by(AccountingNoteChapter.note_no)
            .all()
        )
        policy_items = (
            session.query(AccountingPolicyItem)
            .with_entities(AccountingPolicyItem.item_key, AccountingPolicyItem.body)
            .filter_by(corp_code="00000001", bsns_year=2024, fs_div="CFS")
            .all()
        )
    assert [row.note_no for row in rows] == ["2", "3", "4"]
    assert [row.section_type for row in rows] == ["basis", "policy", "estimate_judgment"]
    assert "수행의무" in rows[1].body
    assert ("revenue_recognition", rows[1].body[:2000]) in policy_items


def test_document_extractors_persist_accounting_notes_from_viewer_html_source(temp_engine, monkeypatch):
    import kreports.collector.report_document_collector as collector_module
    from kreports.db.engine import get_session
    from kreports.db.models import AccountingNoteChapter, AccountingPolicyItem

    def fail_dart_call(*_args, **_kwargs):
        raise AssertionError("HTML note extraction must use cached source_documents only")

    monkeypatch.setattr(collector_module, "fetch_document_zip_files", fail_dart_call)
    monkeypatch.setattr(collector_module, "fetch_document_xml", fail_dart_call)
    monkeypatch.setattr(collector_module, "fetch_dart_main_html", fail_dart_call)

    raw_business_report = """
    <html><body>
      <h1>III. 재무에 관한 사항</h1>
      <p>연결재무제표 주석</p>
      <p>1. 일반사항</p><p>회사의 개요입니다.</p>
      <p>2. 재무제표 작성기준</p><p>연결재무제표는 한국채택국제회계기준에 따라 작성되었습니다.</p>
      <p>3. 중요한 회계정책</p><p>고객과의 계약에서 수행의무가 이행될 때 수익을 인식합니다.</p>
      <p>4. 중요한 회계추정 및 판단</p><p>손상검사에는 회수가능액 추정과 경영진의 판단이 필요합니다.</p>
      <p>5. 영업부문</p><p>다음 주석입니다.</p>
    </body></html>
    """

    with get_session() as session:
        session.add(SourceDocument(
            rcept_no="20250331000001",
            corp_code="00000001",
            bsns_year=2024,
            source_type="business_report",
            report_nm="사업보고서 (2024.12)",
            content_type="html",
            raw_content=raw_business_report,
            doc_hash="cached-html-doc-hash",
        ))

    out = run_document_extractors(year=2024, source_type="business_report")

    assert out["total"] == 1
    assert out["ok"] == 1
    with get_session() as session:
        chapter_note_nos = {
            row.note_no
            for row in session.query(AccountingNoteChapter)
            .with_entities(AccountingNoteChapter.note_no)
            .filter_by(corp_code="00000001")
            .all()
        }
        policy = session.query(AccountingPolicyItem).filter_by(
            corp_code="00000001",
            bsns_year=2024,
            fs_div="CFS",
            item_key="revenue_recognition",
        ).one()
        policy_body = policy.body
    assert chapter_note_nos == {"2", "3", "4"}
    assert "수행의무" in policy_body


def test_document_extractors_load_raw_from_storage_uri(temp_engine, tmp_path, monkeypatch):
    import kreports.collector.report_document_collector as collector_module
    from kreports.db.engine import get_session
    from kreports.db.models import AccountingNoteChapter, SourceDocument
    from kreports.storage.raw_documents import RawDocumentStore

    store = RawDocumentStore(base_dir=tmp_path)
    content = """
    <DOCUMENT>
      <TITLE>III. 재무에 관한 사항</TITLE>
      <TITLE>연결재무제표 주석</TITLE>
      <P>2. 재무제표 작성기준</P><P>한국채택국제회계기준에 따라 작성되었습니다.</P>
      <P>3. 중요한 회계정책</P><P>수익은 수행의무 이행 시 인식합니다.</P>
      <P>4. 중요한 회계추정 및 판단</P><P>손상검사에는 경영진 판단이 필요합니다.</P>
      <P>5. 영업부문</P><P>다음 주석입니다.</P>
    </DOCUMENT>
    """
    saved = store.write(
        corp_code="00000001",
        bsns_year=2024,
        source_type="business_report",
        rcept_no="20250331000001",
        content_type="xml",
        content=content,
    )
    monkeypatch.setattr(collector_module, "RawDocumentStore", lambda: store)

    with get_session() as session:
        session.add(SourceDocument(
            rcept_no="20250331000001",
            corp_code="00000001",
            bsns_year=2024,
            source_type="business_report",
            report_nm="사업보고서",
            content_type="xml",
            raw_content="",
            doc_hash=saved.doc_hash,
            storage_uri=saved.storage_uri,
            content_length=saved.content_length,
            compressed_length=saved.compressed_length,
            storage_status="externalized",
        ))

    out = collector_module.run_document_extractors(
        year=2024,
        source_type="business_report",
        extractor="note_chapters",
    )

    assert out["ok"] == 1
    with get_session() as session:
        assert session.query(AccountingNoteChapter).filter_by(corp_code="00000001").count() == 3


def test_get_business_overview_reads_cached_management_sections_without_dart(temp_engine, monkeypatch):
    import kreports.collector.fetcher as fetcher_module
    import kreports.collector.report_document_collector as collector_module
    from kreports.db.engine import get_session

    def fail_dart_call(*_args, **_kwargs):
        raise AssertionError("get_business_overview must use cached report_sections only")

    monkeypatch.setattr(fetcher_module, "fetch_document_xml", fail_dart_call)
    monkeypatch.setattr(collector_module, "fetch_document_xml", fail_dart_call)
    monkeypatch.setattr(collector_module, "fetch_document_zip_files", fail_dart_call)

    with get_session() as session:
        session.add(Company(
            corp_code="00000001",
            stock_code="000001",
            corp_name="대상",
            market="KOSPI",
            induty_code="58221",
        ))
        for ordinal, (section_key, title, body) in enumerate([
            ("business_overview", "사업의 개요", "구독형 플랫폼 사업 개요입니다."),
            ("business_description", "사업의 내용", "클라우드 서비스와 구축형 솔루션을 판매합니다."),
            ("risk_management", "위험관리", "환율 위험과 유동성 위험을 모니터링합니다."),
            ("management_plan", "향후 추진계획", "파트너 채널과 해외 매출을 확대합니다."),
            ("rd_activities", "연구개발활동", "AI 기능 연구개발을 수행합니다."),
            ("key_contracts", "경영상의 주요계약", "주요 고객 장기 공급계약이 있습니다."),
        ]):
            session.add(ReportSection(
                rcept_no="20250331000001",
                corp_code="00000001",
                bsns_year=2024,
                source_type="business_report",
                section_key=section_key,
                section_title=title,
                body_text=body,
                body_hash=section_key,
                body_length=len(body),
                ordinal=ordinal,
            ))

    out = get_business_overview("000001", bsns_year=2024)

    assert out["data_quality"]["status"] == "usable"
    assert out["data_quality"]["source"] == "local_report_sections"
    assert out["data_quality"]["requested_year"] == 2024
    assert out["section_count"] == 6
    assert out["total_chars"] > 0
    assert set(out["sections"]) == {
        "business_overview",
        "business_description",
        "risk_management",
        "management_plan",
        "rd_activities",
        "key_contracts",
    }
    assert "해외 매출" in out["sections"]["management_plan"]["body_text"]


def test_get_business_overview_falls_back_to_cached_full_text_without_dart(temp_engine, monkeypatch):
    import kreports.collector.fetcher as fetcher_module
    import kreports.collector.report_document_collector as collector_module
    from kreports.db.engine import get_session

    def fail_dart_call(*_args, **_kwargs):
        raise AssertionError("get_business_overview must not call DART for full_text fallback")

    monkeypatch.setattr(fetcher_module, "fetch_document_xml", fail_dart_call)
    monkeypatch.setattr(collector_module, "fetch_document_xml", fail_dart_call)
    monkeypatch.setattr(collector_module, "fetch_document_zip_files", fail_dart_call)

    full_text = (
        "II. 사업의 내용\n"
        "신재생에너지 개발과 ESS 사업을 영위합니다.\n"
        "5. 위험관리 및 파생거래\n"
        "시장위험, 신용위험 및 유동성위험을 관리합니다."
    )
    with get_session() as session:
        session.add(Company(
            corp_code="00000001",
            stock_code="000001",
            corp_name="대상",
            market="KOSPI",
            induty_code="411",
        ))
        session.add(ReportSection(
            rcept_no="20260331000001",
            corp_code="00000001",
            bsns_year=2025,
            source_type="business_report",
            section_key="full_text",
            section_title="사업보고서 본문",
            body_text=full_text,
            body_hash="full_text",
            body_length=len(full_text),
            ordinal=0,
        ))

    out = get_business_overview("000001", bsns_year=2025)

    assert out["data_quality"]["status"] == "limited"
    assert out["data_quality"]["fallback_used"] == "full_text"
    assert out["section_count"] == 1
    assert set(out["missing_sections"]) == {
        "business_overview",
        "business_description",
        "risk_management",
        "management_plan",
        "rd_activities",
        "key_contracts",
    }
    assert "신재생에너지 개발" in out["sections"]["full_text"]["body_text"]


def test_subsidiary_auditors_reads_persistent_cache_without_dart(temp_engine, monkeypatch):
    import kreports.collector.report_document_collector as collector_module
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add_all([
            Company(corp_code="00000001", stock_code="000001", corp_name="모회사", market="KOSPI"),
            Company(corp_code="00000002", stock_code="000002", corp_name="자회사", market="KOSPI"),
            Company(corp_code="00000003", stock_code="000003", corp_name="관계회사", market="KOSDAQ"),
        ])
        session.add(Disclosure(
            rcept_no="20250331000002",
            corp_code="00000001",
            corp_name="모회사",
            disc_date=date(2025, 3, 31),
            disc_type="F",
            report_nm="사업보고서 (2024.12)",
        ))
        session.add(Auditor(
            corp_code="00000002",
            bsns_year=2024,
            fs_div="CFS",
            auditor_nm="삼일회계법인",
            audit_opinion="적정",
        ))

    monkeypatch.setattr(
        collector_module,
        "fetch_document_zip_files",
        lambda _rcept_no: {
            "20250331000002.xml": """
            <DOCUMENT>
              <DOCUMENT-NAME>사업보고서</DOCUMENT-NAME>
              <TABLE-GROUP ACLASS="SUB_CMPN">
                <TBODY>
                  <TR>
                    <TD ACODE="CRP_NM">자회사</TD>
                    <TD ACODE="M_IND">소프트웨어</TD>
                    <TD ACODE="KEY_IND">100</TD>
                  </TR>
                </TBODY>
              </TABLE-GROUP>
              <TABLE-GROUP ACLASS="INV_PRT">
                <TBODY>
                  <TR>
                    <TD ACODE="INV_PRM">관계회사</TD>
                    <TD ACODE="INV_LPR">25.0%</TD>
                    <TD AUNIT="INV_YN" AUNITVALUE="상장"></TD>
                    <TD ACODE="INV_OBJ">전략투자</TD>
                  </TR>
                </TBODY>
              </TABLE-GROUP>
            </DOCUMENT>
            """,
        },
    )
    monkeypatch.setattr(collector_module, "fetch_dart_main_html", lambda _rcept_no: "")

    collect_report_sections_for_disclosure("20250331000002")

    out = get_subsidiary_auditors("000001", only_with_auditor=True)
    assert out["data_quality"]["status"] == "usable"
    assert out["count"] == 1
    assert out["subsidiaries"][0]["name"] == "자회사"
    assert out["subsidiaries"][0]["auditor"]["auditor_nm"] == "삼일회계법인"


def test_subsidiary_auditors_honors_limit_auditor_filter_and_slim_from_cache(temp_engine, monkeypatch):
    import kreports.analysis.queries as queries_module
    import kreports.collector.fetcher as fetcher_module
    import kreports.collector.report_document_collector as collector_module
    from kreports.db.engine import get_session

    def fail_dart_call(*_args, **_kwargs):
        raise AssertionError("get_subsidiary_auditors must use the local cache, not DART")

    monkeypatch.setattr(fetcher_module, "fetch_document_xml", fail_dart_call)
    monkeypatch.setattr(collector_module, "fetch_document_xml", fail_dart_call)
    monkeypatch.setattr(queries_module, "get_subsidiaries_with_auditors", fail_dart_call)

    with get_session() as session:
        session.add(Company(corp_code="00000001", stock_code="000001", corp_name="모회사", market="KOSPI"))
        session.add(Disclosure(
            rcept_no="20250331000002",
            corp_code="00000001",
            corp_name="모회사",
            disc_date=date(2025, 3, 31),
            disc_type="F",
            report_nm="사업보고서 (2024.12)",
        ))
        _create_subsidiary_auditor_matrix_cache(session)
        _insert_subsidiary_cache_row(session, name="감사인있는자회사A", auditor_nm="삼일회계법인", ordinal=0)
        _insert_subsidiary_cache_row(session, name="감사인없는자회사", auditor_nm=None, auditor_year=None, audit_opinion=None, ordinal=1)
        _insert_subsidiary_cache_row(session, name="감사인있는자회사B", auditor_nm="삼정회계법인", ordinal=2)

    out = get_subsidiary_auditors("000001", limit=1, only_with_auditor=True, slim=True)

    assert out["data_quality"] == {"status": "usable", "source": "local_subsidiary_auditor_matrix"}
    assert out["parent_rcept_no"] == "20250331000002"
    assert out["bsns_year"] == 2024
    assert out["total"] == 3
    assert out["count"] == 1
    assert out["truncated"] is True
    assert [row["name"] for row in out["subsidiaries"]] == ["감사인있는자회사A"]
    assert set(out["subsidiaries"][0]) == {
        "name",
        "relation",
        "ownership_pct",
        "listed_yn",
        "corp_code",
        "stock_code",
        "market",
        "auditor",
    }
    assert out["subsidiaries"][0]["auditor"]["auditor_nm"] == "삼일회계법인"


def test_subsidiary_auditors_full_mode_returns_cached_context_fields(temp_engine):
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(corp_code="00000001", stock_code="000001", corp_name="모회사", market="KOSPI"))
        session.add(Disclosure(
            rcept_no="20250331000002",
            corp_code="00000001",
            corp_name="모회사",
            disc_date=date(2025, 3, 31),
            disc_type="F",
            report_nm="사업보고서 (2024.12)",
        ))
        _create_subsidiary_auditor_matrix_cache(session)
        _insert_subsidiary_cache_row(
            session,
            name="해외제조회사",
            relation="associate",
            ownership_pct=25.5,
            business="제조",
            listed_yn="N",
            auditor_nm="한영회계법인",
            source="INV_PRT",
        )

    out = get_subsidiary_auditors("000001", limit=10, only_with_auditor=False, slim=False)

    assert out["data_quality"]["status"] == "usable"
    assert out["count"] == 1
    subsidiary = out["subsidiaries"][0]
    assert subsidiary["name"] == "해외제조회사"
    assert subsidiary["business"] == "제조"
    assert subsidiary["source"] == "INV_PRT"
    assert subsidiary["auditor"] == {
        "auditor_nm": "한영회계법인",
        "bsns_year": 2024,
        "audit_opinion": "적정",
    }
