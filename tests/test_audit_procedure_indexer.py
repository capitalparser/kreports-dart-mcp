from datetime import datetime

from typer.testing import CliRunner

from kreports.cli.main import app
from kreports.collector.report_document_collector import index_audit_procedures_from_sections
from kreports.db.engine import get_session
from kreports.db.models import (
    AuditProcedureItem,
    Company,
    KamItem,
    ReportSection,
    SourceDocument,
)


def test_index_audit_procedures_uses_cached_kam_body_only(temp_engine, monkeypatch):
    with get_session() as session:
        session.add(Company(corp_code="00126380", stock_code="005930", corp_name="삼성전자", market="KOSPI"))
        session.add(
            ReportSection(
                corp_code="00126380",
                bsns_year=2025,
                rcept_no="20260301000001_100",
                dcm_no="100",
                source_type="audit_report",
                section_key="kam",
                section_title="핵심감사사항",
                body_text=(
                    "수익인식\n"
                    "핵심감사사항으로 선정한 이유: 기간귀속 판단에 중요한 왜곡표시위험이 있습니다.\n"
                    "감사인의 대응\n"
                    "가. 계약서와 세금계산서 대사를 수행하였습니다.\n"
                    "나. 보고기간 전후 매출의 기간귀속 테스트를 수행하였습니다.\n"
                ),
                body_length=142,
                ordinal=1,
                fetched_at=datetime(2026, 3, 1),
            )
        )
        session.add(
            KamItem(
                corp_code="00126380",
                bsns_year=2025,
                rcept_no="20260301000001_100",
                dcm_no="100",
                source_type="audit_report",
                ordinal=1,
                title="수익인식",
                normalized_topic="revenue",
                reason_text="기간귀속 위험",
                audit_response_text=(
                    "계약서와 세금계산서 대사를 수행하였습니다.\n"
                    "보고기간 전후 매출의 기간귀속 테스트를 수행하였습니다."
                ),
                related_note_references_json="[]",
                full_body_hash="k" * 40,
                full_body_length=500,
                source_basis="source_documents.full_body",
                parser_version="kam.v1",
                quality_status="full_body",
                fetched_at=datetime(2026, 3, 1),
            )
        )

    result = index_audit_procedures_from_sections(year=2025)

    assert result["rows_written"] == 2
    with get_session() as session:
        procedure_types = [
            row.procedure_type
            for row in session.query(AuditProcedureItem)
            .order_by(AuditProcedureItem.procedure_ordinal)
            .all()
        ]
    assert procedure_types == ["other", "cutoff"]
    with get_session() as session:
        assert all(
            row.kam_item_id is not None and row.method is not None
            for row in session.query(AuditProcedureItem).all()
        )


def test_index_audit_procedures_cli_uses_structured_kam_lifecycle(
    temp_engine,
    monkeypatch,
):
    import kreports.cli.main as cli_module

    monkeypatch.setattr(cli_module, "init_db", lambda: None)
    with get_session() as session:
        session.add(
            KamItem(
                corp_code="00126380",
                bsns_year=2025,
                rcept_no="CLI1",
                source_type="audit_report",
                ordinal=1,
                title="수익인식",
                normalized_topic="revenue",
                reason_text="위험",
                audit_response_text="계약서를 검사하였습니다.",
                related_note_references_json="[]",
                full_body_hash="c" * 40,
                full_body_length=500,
                source_basis="source_documents.full_body",
                parser_version="kam.v1",
                quality_status="full_body",
                fetched_at=datetime(2026, 3, 1),
            )
        )
        session.add(
            KamItem(
                corp_code="00126380",
                bsns_year=2025,
                rcept_no="CLI2",
                source_type="audit_report",
                ordinal=1,
                title="요약",
                normalized_topic="revenue",
                reason_text=None,
                audit_response_text=None,
                related_note_references_json="[]",
                full_body_hash="s" * 40,
                full_body_length=20,
                source_basis="report_sections.derived_summary",
                parser_version="kam.v1",
                quality_status="summary_only",
                fetched_at=datetime(2026, 3, 1),
            )
        )

    result = CliRunner().invoke(
        app,
        ["index-audit-procedures", "--year", "2025"],
    )

    assert result.exit_code == 0
    assert "처리 2" in result.stdout
    assert "rows_written 1" in result.stdout
    with get_session() as session:
        row = session.query(AuditProcedureItem).one()
        assert row.kam_item_id is not None
        assert row.method == "inspection"


def test_index_audit_procedures_cleans_downgraded_identity(temp_engine, monkeypatch):
    with get_session() as session:
        item = KamItem(
            corp_code="00126380",
            bsns_year=2025,
            rcept_no="DOWNGRADE1",
            source_type="audit_report",
            ordinal=1,
            title="수익인식",
            normalized_topic="revenue",
            reason_text="위험",
            audit_response_text="계약서를 검사하였습니다.",
            related_note_references_json="[]",
            full_body_hash="d" * 40,
            full_body_length=500,
            source_basis="source_documents.full_body",
            parser_version="kam.v1",
            quality_status="full_body",
            fetched_at=datetime(2026, 3, 1),
        )
        session.add(item)
        session.add(
            KamItem(
                corp_code="00126380",
                bsns_year=2025,
                rcept_no="DOWNGRADE1",
                source_type="audit_report",
                ordinal=2,
                title="재고자산",
                normalized_topic="inventory",
                reason_text="위험",
                audit_response_text="재고 실사를 관찰하였습니다.",
                related_note_references_json="[]",
                full_body_hash="e" * 40,
                full_body_length=500,
                source_basis="source_documents.full_body",
                parser_version="kam.v1",
                quality_status="full_body",
                fetched_at=datetime(2026, 3, 1),
            )
        )

    assert index_audit_procedures_from_sections(year=2025)["rows_written"] == 2
    with get_session() as session:
        session.query(KamItem).filter_by(
            rcept_no="DOWNGRADE1",
            ordinal=1,
        ).update(
            {
                "quality_status": "summary_only",
                "audit_response_text": None,
            }
        )

    result = index_audit_procedures_from_sections(year=2025)

    assert result["total"] == 2
    assert result["rows_written"] == 1
    with get_session() as session:
        assert (
            session.query(AuditProcedureItem)
            .filter_by(rcept_no="DOWNGRADE1", section_ordinal=1)
            .count()
            == 0
        )
        assert (
            session.query(AuditProcedureItem)
            .filter_by(rcept_no="DOWNGRADE1", section_ordinal=2)
            .count()
            == 1
        )


def test_rebuild_reconciles_hash_replacement_with_zero_steps_exactly(
    temp_engine,
    monkeypatch,
):
    import kreports.collector.report_document_collector as collector_module

    old_body = """
    <TITLE>핵심감사사항</TITLE>
    <TITLE>수익인식</TITLE>
    <P>핵심감사사항으로 선정한 이유</P>
    <P>기간귀속 판단에 중요한 왜곡표시위험이 있습니다.</P>
    <P>감사에서 다루어진 방법</P>
    <P>계약서를 검사하였습니다.</P>
    <P>거래처에 외부조회서를 발송하였습니다.</P>
    """
    new_body = """
    <TITLE>핵심감사사항</TITLE>
    <TITLE>수익인식</TITLE>
    <P>핵심감사사항으로 선정한 이유</P>
    <P>기간귀속 판단에 중요한 왜곡표시위험이 있습니다.</P>
    <P>감사에서 다루어진 방법</P>
    <P>감사인은 중요한 왜곡표시위험을 식별하고 평가하며, 이에 대응하는
    감사절차를 설계하고 수행합니다.</P>
    """
    with get_session() as session:
        session.add(
            Company(
                corp_code="00126380",
                stock_code="005930",
                corp_name="삼성전자",
                market="KOSPI",
            )
        )
        session.add(
            SourceDocument(
                rcept_no="REPLACE1",
                corp_code="00126380",
                bsns_year=2025,
                source_type="audit_report",
                report_nm="감사보고서",
                content_type="xml",
                raw_content=old_body,
                doc_hash="a" * 40,
                storage_status="inline",
                fetched_at=datetime(2026, 3, 1),
            )
        )
        untouched = KamItem(
            corp_code="00126380",
            bsns_year=2025,
            rcept_no="UNTOUCHED1",
            source_type="audit_report",
            ordinal=1,
            title="재고자산",
            normalized_topic="inventory",
            reason_text="위험",
            audit_response_text="재고 실사를 관찰하였습니다.",
            related_note_references_json="[]",
            full_body_hash="u" * 40,
            full_body_length=500,
            source_basis="source_documents.full_body",
            parser_version="kam.v1",
            quality_status="full_body",
            fetched_at=datetime(2026, 3, 1),
        )
        session.add(untouched)

    collector_module.rebuild_kam_items(year=2025)
    assert index_audit_procedures_from_sections(year=2025)["rows_written"] == 3
    with get_session() as session:
        source = session.query(SourceDocument).filter_by(
            rcept_no="REPLACE1"
        ).one()
        source.raw_content = new_body
        source.doc_hash = "b" * 40
        source.fetched_at = datetime(2026, 3, 2)

    collector_module.rebuild_kam_items(year=2025)
    result = index_audit_procedures_from_sections(year=2025)

    assert result["rows_written"] == 1
    with get_session() as session:
        assert (
            session.query(AuditProcedureItem)
            .filter_by(rcept_no="REPLACE1")
            .count()
            == 0
        )
        assert (
            session.query(AuditProcedureItem)
            .filter_by(rcept_no="UNTOUCHED1")
            .count()
            == 1
        )
