from datetime import datetime

from kreports.collector.report_document_collector import index_audit_procedures_from_sections
from kreports.db.engine import get_session
from kreports.db.models import AuditProcedureItem, Company, ReportSection


def test_index_audit_procedures_uses_cached_kam_body_only(temp_engine, monkeypatch):
    import kreports.collector.report_document_collector as collector_module

    monkeypatch.setattr(collector_module, "engine", temp_engine)
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

    result = index_audit_procedures_from_sections(year=2025)

    assert result["rows_written"] == 2
    with get_session() as session:
        procedure_types = [
            row.procedure_type
            for row in session.query(AuditProcedureItem)
            .order_by(AuditProcedureItem.procedure_ordinal)
            .all()
        ]
    assert procedure_types == ["substantive_test", "cutoff"]
