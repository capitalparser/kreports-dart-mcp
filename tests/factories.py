from datetime import date, datetime, timezone

from kreports.db.models import Company, Disclosure, EvidenceDocument


def company_factory(
    *,
    corp_code: str = "00126380",
    corp_name: str = "삼성전자",
) -> Company:
    return Company(
        corp_code=corp_code,
        corp_name=corp_name,
        updated_at=datetime.now(timezone.utc),
    )


def disclosure_factory(
    *,
    rcept_no: str = "20250318000001",
    corp_code: str = "00126380",
    disc_date: date = date(2025, 3, 18),
) -> Disclosure:
    return Disclosure(
        rcept_no=rcept_no,
        corp_code=corp_code,
        corp_name="삼성전자",
        disc_date=disc_date,
        disc_type="A",
        report_nm="사업보고서",
        fetched_at=datetime.now(timezone.utc),
    )


def evidence_document_factory(
    *,
    corp_code: str = "00126380",
    bsns_year: int = 2024,
    rcept_no: str = "20250318000001",
) -> EvidenceDocument:
    return EvidenceDocument(
        corp_code=corp_code,
        bsns_year=bsns_year,
        source_type="audit_report",
        rcept_no=rcept_no,
        evidence_scope="auditor_view",
        normalized_text="감사 근거",
        generated_at=datetime.now(timezone.utc),
    )
