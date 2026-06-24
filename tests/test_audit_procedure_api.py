from datetime import datetime

from kreports.analysis.api import search_audit_procedures
from kreports.db.engine import get_session
from kreports.db.models import AuditProcedureItem, Company


def test_search_audit_procedures_returns_linkages_and_source_note(temp_engine):
    with get_session() as session:
        session.add(
            Company(
                corp_code="00126380",
                stock_code="005930",
                corp_name="삼성전자",
                market="KOSPI",
                induty_code="264",
            )
        )
        session.add(
            AuditProcedureItem(
                corp_code="00126380",
                bsns_year=2025,
                rcept_no="20260301000001_100",
                dcm_no="100",
                source_type="audit_report",
                section_ordinal=1,
                kam_topic="revenue",
                procedure_type="substantive_test",
                procedure_text="매출 계약서 문서검사와 기간귀속 테스트를 수행하였습니다.",
                procedure_hash="p1",
                procedure_length=32,
                procedure_ordinal=1,
                fetched_at=datetime(2026, 3, 1),
            )
        )

    result = search_audit_procedures(company="005930", year=2025, limit=5)

    record = result["companies"][0]["records"][0]
    assert record["linkages"][0]["category"] == "audit_report_kam"
    assert result["data_quality"]["source"] == "audit_procedure_items"
    assert "audit-report KAM" in result["data_quality"]["interpretation"]
