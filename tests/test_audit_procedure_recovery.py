"""Historical-membership recovery targets for audit-procedure evidence."""
from __future__ import annotations

import hashlib
import json
from datetime import date

import pytest

def _create_fallback_resolution_table() -> None:
    from sqlalchemy import text

    from kreports.db.engine import get_session

    with get_session() as session:
        session.execute(text("""
            CREATE TABLE IF NOT EXISTS audit_procedure_recovery_fallbacks (
              direct_rcept_no VARCHAR(14) PRIMARY KEY,
              corp_code VARCHAR(8) NOT NULL,
              bsns_year SMALLINT NOT NULL,
              fallback_rcept_no VARCHAR(14) NOT NULL,
              resolution_reason VARCHAR(80) NOT NULL,
              resolved_at DATETIME NOT NULL
            )
        """))


def _fallback_resolution_rows() -> list[tuple[str, str, int, str, str]]:
    from sqlalchemy import text

    from kreports.db.engine import get_session

    with get_session() as session:
        return [
            tuple(row)
            for row in session.execute(text("""
                SELECT direct_rcept_no, corp_code, bsns_year,
                       fallback_rcept_no, resolution_reason
                FROM audit_procedure_recovery_fallbacks
                ORDER BY direct_rcept_no
            """))
        ]


def _seed_complete_audit_evidence(corp_code: str, receipt: str) -> None:
    from kreports.db.engine import get_session
    from kreports.db.models import AuditProcedureItem, KamItem

    with get_session() as session:
        session.add(KamItem(
            rcept_no=receipt,
            corp_code=corp_code,
            bsns_year=2025,
            source_type="audit_report",
            ordinal=0,
            full_body_hash="b" * 40,
            full_body_length=1000,
            source_basis="report_sections.structured_body",
            quality_status="full_body",
        ))
        session.add(AuditProcedureItem(
            rcept_no=receipt,
            corp_code=corp_code,
            bsns_year=2025,
            source_type="audit_report",
            procedure_type="substantive_test",
            procedure_text="계약서를 검사하였습니다.",
            section_ordinal=0,
            procedure_ordinal=0,
        ))


def _seed_fallback_resolution(
    *,
    direct_rcept_no: str,
    corp_code: str,
    fallback_rcept_no: str,
) -> None:
    from sqlalchemy import text

    from kreports.db.engine import get_session

    _create_fallback_resolution_table()
    with get_session() as session:
        session.execute(text("""
            INSERT INTO audit_procedure_recovery_fallbacks (
              direct_rcept_no, corp_code, bsns_year, fallback_rcept_no,
              resolution_reason, resolved_at
            ) VALUES (
              :direct_rcept_no, :corp_code, 2025, :fallback_rcept_no,
              'audit_report_attachment_not_found', CURRENT_TIMESTAMP
            )
        """), {
            "direct_rcept_no": direct_rcept_no,
            "corp_code": corp_code,
            "fallback_rcept_no": fallback_rcept_no,
        })


def _membership(corp_code: str, *, year: int = 2025, market: str = "KOSPI", status: str = "verified"):
    from kreports.db.models import CompanyYearListingMembership

    return CompanyYearListingMembership(
        corp_code=corp_code,
        stock_code=corp_code[-6:],
        bsns_year=year,
        market=market,
        status=status,
        evidence_basis="current_open_interval",
        as_of=date(2026, 8, 10),
        manifest_checksum=hashlib.sha256(f"manifest:{corp_code}:{year}".encode()).hexdigest(),
        manifest_storage_uri="file:///test-membership-manifest.json",
        manifest_size_bytes=1,
        manifest_raw_receipt_count=1,
        normalized_checksum=hashlib.sha256(f"normalized:{corp_code}:{year}".encode()).hexdigest(),
        normalized_storage_uri="file:///test-membership-normalized.csv",
        normalized_size_bytes=1,
        transformation_version="krx-year-end-listing-membership-v1",
        source_row_no=int(corp_code[-3:]),
    )


def _seed_target(
    corp_code: str,
    rcept_no: str,
    *,
    membership_market: str = "KOSPI",
    membership_year: int = 2025,
    membership_status: str | None = "verified",
    current_market: str = "KOSPI",
    full_kam: bool = False,
    procedure: bool = False,
    report_nm: str = "감사보고서제출",
    disc_date: date = date(2026, 3, 20),
) -> None:
    from kreports.db.engine import get_session
    from kreports.db.models import AuditProcedureItem, Company, Disclosure, KamItem

    with get_session() as session:
        if session.get(Company, corp_code) is None:
            session.add(Company(
                corp_code=corp_code,
                stock_code=corp_code[-6:],
                corp_name=f"회사{corp_code}",
                market=current_market,
            ))
        if membership_status is not None and not session.query(type(_membership(corp_code))).filter_by(
            corp_code=corp_code,
            bsns_year=membership_year,
        ).first():
            session.add(_membership(
                corp_code,
                year=membership_year,
                market=membership_market,
                status=membership_status,
            ))
        session.add(Disclosure(
            rcept_no=rcept_no,
            corp_code=corp_code,
            corp_name=f"회사{corp_code}",
            disc_date=disc_date,
            disc_type="F",
            report_nm=report_nm,
        ))
        if full_kam:
            session.add(KamItem(
                rcept_no=rcept_no,
                corp_code=corp_code,
                bsns_year=2025,
                source_type="audit_report",
                ordinal=0,
                full_body_hash="a" * 40,
                full_body_length=1000,
                source_basis="source_documents.raw_body",
                quality_status="full_body",
            ))
        if procedure:
            session.add(AuditProcedureItem(
                rcept_no=rcept_no,
                corp_code=corp_code,
                bsns_year=2025,
                source_type="audit_report",
                procedure_type="substantive_test",
                procedure_text="계약서를 검사하였습니다.",
                section_ordinal=0,
                procedure_ordinal=0,
            ))


def _empty_recovery_result() -> dict[str, object]:
    return {
        "processed": 0,
        "ok": 0,
        "failed": 0,
        "sections": 0,
        "errors": [],
        "next_cursor": None,
        "exhausted": True,
    }


def _seed_target_year_audit_attachment(corp_code: str, root_rcept_no: str) -> None:
    from kreports.db.engine import get_session
    from kreports.db.models import SourceDocument

    with get_session() as session:
        session.add(SourceDocument(
            rcept_no=f"{root_rcept_no}_11100001",
            dcm_no="11100001",
            corp_code=corp_code,
            bsns_year=2025,
            source_type="audit_report",
            report_nm="감사보고서",
            content_type="html",
            raw_content="핵심감사사항",
            doc_hash="b" * 40,
            storage_status="externalized",
        ))


def test_selector_uses_verified_historical_membership_and_evidence_gap(temp_engine):
    """Catch a selector that uses the current company master or any section row as completion."""
    from kreports.collector.audit_procedure_recovery import select_audit_procedure_recovery_targets
    from kreports.db.engine import get_session
    from kreports.db.models import ReportSection

    _seed_target("00000001", "20260320000001")  # missing KAM/procedures -> include
    _seed_target("00000002", "20260320000002", current_market="KONEX")  # historical KOSPI -> include
    _seed_target("00000003", "20260320000003", membership_status="unknown")  # exclude
    _seed_target("00000008", "20260320000008", membership_status=None)  # current-only exclude
    _seed_target("00000004", "20260320000004", membership_market="KOSDAQ")  # isolate market
    _seed_target("00000005", "20260320000005", full_kam=True, procedure=True)  # adequate -> exclude
    _seed_target("00000007", "20260320000007", full_kam=True)  # no procedures -> include
    _seed_target("00000006", "20260320000006")  # truncated section must remain include
    with get_session() as session:
        session.add(ReportSection(
            rcept_no="20260320000006",
            corp_code="00000006",
            bsns_year=2025,
            source_type="audit_report",
            section_key="kam",
            body_text="짧은 요약",
            ordinal=0,
        ))

    result = select_audit_procedure_recovery_targets(year=2025, market="KOSPI", limit=20)

    assert [(row["corp_code"], row["rcept_no"]) for row in result["targets"]] == [
        ("00000001", "20260320000001"),
        ("00000002", "20260320000002"),
        ("00000006", "20260320000006"),
        ("00000007", "20260320000007"),
    ]


def test_selector_is_stably_paginated_by_company_and_receipt(temp_engine):
    """Catch a target query whose pagination changes order or repeats its prefix."""
    from kreports.collector.audit_procedure_recovery import select_audit_procedure_recovery_targets

    _seed_target("00000010", "20260320000001")
    _seed_target("00000011", "20260320000002")
    _seed_target("00000012", "20260320000003")

    first = select_audit_procedure_recovery_targets(year=2025, market="KOSPI", limit=2)
    second = select_audit_procedure_recovery_targets(
        year=2025, market="KOSPI", limit=2, after=first["targets"][-1]
    )

    assert [(row["corp_code"], row["rcept_no"]) for row in first["targets"]] == [
        ("00000010", "20260320000001"),
        ("00000011", "20260320000002"),
    ]
    assert [(row["corp_code"], row["rcept_no"]) for row in second["targets"]] == [
        ("00000012", "20260320000003"),
    ]


def test_selector_uses_one_own_company_target_year_root_per_company(temp_engine):
    """Catch a selector that spends a 2025 lease on child or prior-year filings."""
    from kreports.collector.audit_procedure_recovery import select_audit_procedure_recovery_targets

    _seed_target("00000012", "20260320000012")
    _seed_target(
        "00000012",
        "20260420000012",
        report_nm="[첨부정정]감사보고서제출 (2025 사업연도)",
        disc_date=date(2026, 4, 20),
    )
    _seed_target(
        "00000012",
        "20260520000012",
        report_nm="[첨부정정]감사보고서제출 (2024 사업연도)",
        disc_date=date(2026, 5, 20),
    )
    _seed_target(
        "00000012",
        "20260620000012",
        report_nm="감사보고서제출(자회사의 주요경영사항)",
        disc_date=date(2026, 6, 20),
    )

    result = select_audit_procedure_recovery_targets(year=2025, market="KOSPI", limit=20)

    assert [(row["corp_code"], row["rcept_no"]) for row in result["targets"]] == [
        ("00000012", "20260420000012"),
    ]


def test_selector_includes_target_year_standalone_audit_report_roots(temp_engine):
    """Catch a selector that only sees the 감사보고서제출 filing label."""
    from kreports.collector.audit_procedure_recovery import select_audit_procedure_recovery_targets

    _seed_target(
        "00000013",
        "20260609000508",
        report_nm="감사보고서 (2025.12)",
        disc_date=date(2026, 6, 9),
    )
    _seed_target(
        "00000013",
        "20260609000510",
        report_nm="연결감사보고서 (2025.12)",
        disc_date=date(2026, 6, 9),
    )

    result = select_audit_procedure_recovery_targets(year=2025, market="KOSPI", limit=20)

    assert [(row["corp_code"], row["rcept_no"]) for row in result["targets"]] == [
        ("00000013", "20260609000510"),
    ]


def test_selector_includes_target_year_business_report_root_for_non_calendar_filer(temp_engine):
    """Catch a selector that drops 2025 audit attachments under a 2025 business report."""
    from kreports.collector.audit_procedure_recovery import select_audit_procedure_recovery_targets

    _seed_target(
        "00000014",
        "20250618000208",
        membership_market="KOSDAQ",
        report_nm="사업보고서 (2025.03)",
        disc_date=date(2025, 6, 18),
    )
    _seed_target_year_audit_attachment("00000014", "20250618000208")
    _seed_target(
        "00000014",
        "20260618000208",
        membership_market="KOSDAQ",
        report_nm="사업보고서 (2026.03)",
        disc_date=date(2026, 6, 18),
    )
    _seed_target(
        "00000014",
        "20250617000208",
        membership_market="KOSDAQ",
        report_nm="사업보고서 (2024.03)",
        disc_date=date(2025, 6, 17),
    )

    result = select_audit_procedure_recovery_targets(year=2025, market="KOSDAQ", limit=20)

    assert [(row["corp_code"], row["rcept_no"]) for row in result["targets"]] == [
        ("00000014", "20250618000208"),
    ]


def test_selector_includes_business_report_fallback_before_attachments_are_indexed(temp_engine):
    """Catch a selector that cannot fetch a valid business root to discover its audit attachments."""
    from kreports.collector.audit_procedure_recovery import select_audit_procedure_recovery_targets

    _seed_target(
        "00000016",
        "20250618000216",
        membership_market="KOSDAQ",
        report_nm="사업보고서 (2025.03)",
        disc_date=date(2025, 6, 18),
    )

    result = select_audit_procedure_recovery_targets(year=2025, market="KOSDAQ", limit=20)

    assert [(row["corp_code"], row["rcept_no"]) for row in result["targets"]] == [
        ("00000016", "20250618000216"),
    ]


def test_selector_prefers_direct_audit_root_over_later_business_report_fallback(temp_engine):
    """Catch a later annual filing replacing a direct audit root for the same company."""
    from kreports.collector.audit_procedure_recovery import select_audit_procedure_recovery_targets

    _seed_target("00000015", "20260318000208", disc_date=date(2026, 3, 18))
    _seed_target(
        "00000015",
        "20260418000208",
        report_nm="사업보고서 (2025.12)",
        disc_date=date(2026, 4, 18),
    )
    _seed_target_year_audit_attachment("00000015", "20260418000208")

    result = select_audit_procedure_recovery_targets(year=2025, market="KOSPI", limit=20)

    assert [(row["corp_code"], row["rcept_no"]) for row in result["targets"]] == [
        ("00000015", "20260318000208"),
    ]


def test_selector_keeps_direct_root_inadequate_without_fallback_resolution(
    temp_engine,
):
    """Catch annual evidence masking a direct root without an exact resolution."""
    from kreports.collector.audit_procedure_recovery import (
        select_audit_procedure_recovery_targets,
    )

    corp_code = "00000018"
    corrected_receipt = "20260428800618"
    business_receipt = "20260428000618"
    attachment_receipt = f"{business_receipt}_11351227"
    _seed_target(
        corp_code,
        corrected_receipt,
        report_nm="[기재정정]감사보고서제출",
        disc_date=date(2026, 4, 28),
    )
    _seed_target(
        corp_code,
        business_receipt,
        report_nm="사업보고서 (2025.12)",
        disc_date=date(2026, 4, 28),
    )
    _seed_complete_audit_evidence(corp_code, attachment_receipt)

    result = select_audit_procedure_recovery_targets(
        year=2025,
        market="KOSPI",
        limit=20,
    )

    assert result["canonical_roots"] == 1
    assert result["inadequate_roots"] == 1
    assert result["targets"] == [{
        "corp_code": corp_code,
        "rcept_no": corrected_receipt,
        "corp_name": f"회사{corp_code}",
    }]


def test_selector_rejects_different_stale_or_excluded_fallback_resolutions(
    temp_engine,
):
    """Catch any mapping that is not the current eligible annual fallback."""
    from kreports.collector.audit_procedure_recovery import (
        select_audit_procedure_recovery_targets,
    )

    _create_fallback_resolution_table()
    cases = (
        ("00000024", "different", "20260428000999"),
        ("00000025", "stale", "20260427000625"),
        ("00000026", "excluded", "20260429000626"),
    )
    for corp_code, kind, mapped_receipt in cases:
        direct_receipt = f"20260428800{corp_code[-3:]}"
        business_receipt = f"20260428000{corp_code[-3:]}"
        _seed_target(
            corp_code,
            direct_receipt,
            report_nm="[기재정정]감사보고서제출",
            disc_date=date(2026, 4, 28),
        )
        _seed_target(
            corp_code,
            business_receipt,
            report_nm="사업보고서 (2025.12)",
            disc_date=date(2026, 4, 28),
        )
        _seed_complete_audit_evidence(corp_code, f"{business_receipt}_11351227")
        if kind == "different":
            _seed_target(
                "00000027",
                mapped_receipt,
                membership_status=None,
                report_nm="사업보고서 (2025.12)",
                disc_date=date(2026, 4, 28),
            )
        elif kind == "stale":
            _seed_target(
                corp_code,
                mapped_receipt,
                report_nm="사업보고서 (2025.12)",
                disc_date=date(2026, 4, 27),
            )
        else:
            _seed_target(
                corp_code,
                mapped_receipt,
                report_nm="사업보고서 (2025.12) (자회사의 주요경영사항)",
                disc_date=date(2026, 4, 29),
            )
        _seed_fallback_resolution(
            direct_rcept_no=direct_receipt,
            corp_code=corp_code,
            fallback_rcept_no=mapped_receipt,
        )

    result = select_audit_procedure_recovery_targets(
        year=2025,
        market="KOSPI",
        limit=20,
    )

    assert {(row["corp_code"], row["rcept_no"]) for row in result["targets"]} == {
        ("00000024", "20260428800024"),
        ("00000025", "20260428800025"),
        ("00000026", "20260428800026"),
    }


def test_batch_uses_canonical_business_report_after_empty_corrected_audit_root(
    temp_engine,
    monkeypatch,
):
    """Catch an empty corrected audit root blocking its business-report attachment."""
    from kreports.collector import audit_procedure_recovery as recovery
    from kreports.maintenance.backfill_runs import BackfillLease

    corp_code = "00000017"
    corrected_receipt = "20260428800599"
    business_receipt = "20260428000679"
    _create_fallback_resolution_table()
    _seed_target(
        corp_code,
        corrected_receipt,
        report_nm="[기재정정]감사보고서제출",
        disc_date=date(2026, 4, 28),
    )
    _seed_target(
        corp_code,
        business_receipt,
        report_nm="사업보고서 (2025.12)",
        disc_date=date(2026, 4, 28),
    )
    fetched: list[str] = []
    rebuilt: list[str] = []

    def fetch(receipt: str) -> dict[str, object]:
        fetched.append(receipt)
        if receipt == corrected_receipt:
            return {
                "ok": 0,
                "sections": 0,
                "error": "audit report attachment not found",
            }
        return {"ok": 1, "sections": 2, "audit_report_sections": 2}

    monkeypatch.setattr(recovery, "collect_report_sections_for_disclosure", fetch)
    def rebuild(*, year, target):
        rebuilt.append(str(target["rcept_no"]))
        _seed_complete_audit_evidence(
            corp_code,
            f"{target['rcept_no']}_11351227",
        )
        return {"rcept_no": str(target["rcept_no"])}

    monkeypatch.setattr(recovery, "_rebuild_derived_receipt", rebuild)
    params = recovery.recovery_backfill_params(year=2025, market="KOSPI")
    lease = BackfillLease.start("audit_procedure_recovery", 2025, "KOSPI", params)

    result = recovery.run_audit_procedure_recovery_batch(
        lease,
        year=2025,
        market="KOSPI",
        limit=1,
    )

    assert result["targets"] == [{
        "corp_code": corp_code,
        "rcept_no": corrected_receipt,
        "corp_name": f"회사{corp_code}",
    }]
    assert fetched == [corrected_receipt, business_receipt]
    assert rebuilt == [business_receipt]
    assert result["ok"] == 1
    assert result["failed"] == 0
    assert result["api_receipt_fetches"] == 2
    assert result["next_cursor"] == {
        "corp_code": corp_code,
        "rcept_no": corrected_receipt,
    }
    assert _fallback_resolution_rows() == [
        (
            corrected_receipt,
            corp_code,
            2025,
            business_receipt,
            "audit_report_attachment_not_found",
        )
    ]
    full_sweep = recovery.select_audit_procedure_recovery_targets(
        year=2025,
        market="KOSPI",
        limit=20,
    )
    assert full_sweep["targets"] == []


def test_batch_keeps_corrected_root_retryable_when_business_fallback_has_no_audit_evidence(
    temp_engine,
    monkeypatch,
):
    """Catch a business-only fallback being counted as recovered audit evidence."""
    from kreports.collector import audit_procedure_recovery as recovery
    from kreports.db.engine import get_session
    from kreports.db.models import BackfillRun
    from kreports.maintenance.backfill_runs import BackfillLease

    corp_code = "00000019"
    corrected_receipt = "20260428800619"
    business_receipt = "20260428000619"
    _create_fallback_resolution_table()
    _seed_target(
        corp_code,
        corrected_receipt,
        report_nm="[기재정정]감사보고서제출",
        disc_date=date(2026, 4, 28),
    )
    _seed_target(
        corp_code,
        business_receipt,
        report_nm="사업보고서 (2025.12)",
        disc_date=date(2026, 4, 28),
    )
    fetched: list[str] = []
    rebuilt: list[str] = []

    def fetch(receipt: str) -> dict[str, object]:
        fetched.append(receipt)
        if receipt == corrected_receipt:
            return {
                "ok": 0,
                "sections": 0,
                "error": "audit report attachment not found",
            }
        return {
            "ok": 1,
            "sections": 5,
            "audit_report_sections": 0,
            "errors": ["audit report attachment not found"],
        }

    monkeypatch.setattr(recovery, "collect_report_sections_for_disclosure", fetch)
    monkeypatch.setattr(
        recovery,
        "_rebuild_derived_receipt",
        lambda *, year, target: rebuilt.append(str(target["rcept_no"])),
    )
    params = recovery.recovery_backfill_params(year=2025, market="KOSPI")
    lease = BackfillLease.start("audit_procedure_recovery", 2025, "KOSPI", params)

    result = recovery.run_audit_procedure_recovery_batch(
        lease,
        year=2025,
        market="KOSPI",
        limit=1,
    )

    with get_session() as session:
        row = session.get(BackfillRun, lease.id)
        checkpoint = json.loads(row.checkpoint_json)
    assert fetched == [corrected_receipt, business_receipt]
    assert rebuilt == []
    assert result["ok"] == 0
    assert result["failed"] == 1
    assert result["api_receipt_fetches"] == 2
    assert result["next_cursor"] is None
    assert checkpoint["last_error"] == {
        "corp_code": corp_code,
        "rcept_no": corrected_receipt,
        "message": (
            "audit report attachment not found; annual business report "
            "fallback yielded no audit report sections"
        ),
    }
    assert _fallback_resolution_rows() == []


def test_fallback_resolution_is_written_once_after_a_retryable_derived_failure(
    temp_engine,
    monkeypatch,
):
    """Catch a pre-commit marker or duplicate marker across a failed retry."""
    from kreports.collector import audit_procedure_recovery as recovery
    from kreports.maintenance.backfill_runs import BackfillLease

    corp_code = "00000028"
    corrected_receipt = "20260428800028"
    business_receipt = "20260428000028"
    _create_fallback_resolution_table()
    _seed_target(
        corp_code,
        corrected_receipt,
        report_nm="[기재정정]감사보고서제출",
        disc_date=date(2026, 4, 28),
    )
    _seed_target(
        corp_code,
        business_receipt,
        report_nm="사업보고서 (2025.12)",
        disc_date=date(2026, 4, 28),
    )
    monkeypatch.setattr(
        recovery,
        "collect_report_sections_for_disclosure",
        lambda receipt: (
            {"ok": 0, "sections": 0, "error": "audit report attachment not found"}
            if receipt == corrected_receipt
            else {"ok": 1, "sections": 2, "audit_report_sections": 2}
        ),
    )
    rebuild_attempts = 0

    def rebuild(*, year, target):
        nonlocal rebuild_attempts
        rebuild_attempts += 1
        if rebuild_attempts == 1:
            raise RuntimeError("derived rebuild failed")
        _seed_complete_audit_evidence(
            corp_code,
            f"{target['rcept_no']}_11351227",
        )
        return {"rcept_no": str(target["rcept_no"])}

    monkeypatch.setattr(recovery, "_rebuild_derived_receipt", rebuild)
    params = recovery.recovery_backfill_params(year=2025, market="KOSPI")
    failed_lease = BackfillLease.start("audit_procedure_recovery", 2025, "KOSPI", params)
    failed = recovery.run_audit_procedure_recovery_batch(
        failed_lease,
        year=2025,
        market="KOSPI",
        limit=1,
    )
    failed_lease.fail("storage_error", "derived rebuild failed")

    assert failed["failed"] == 1
    assert _fallback_resolution_rows() == []

    retry_lease = BackfillLease.start("audit_procedure_recovery", 2025, "KOSPI", params)
    retry = recovery.run_audit_procedure_recovery_batch(
        retry_lease,
        year=2025,
        market="KOSPI",
        limit=1,
    )

    assert retry["ok"] == 1
    assert _fallback_resolution_rows() == [
        (
            corrected_receipt,
            corp_code,
            2025,
            business_receipt,
            "audit_report_attachment_not_found",
        )
    ]


def test_batch_excludes_noncanonical_business_report_from_audit_fallback(
    temp_engine,
    monkeypatch,
):
    """Catch fallback selection that admits subsidiary or delayed filing labels."""
    from kreports.collector import audit_procedure_recovery as recovery
    from kreports.maintenance.backfill_runs import BackfillLease

    corp_code = "00000023"
    corrected_receipt = "20260428800623"
    business_receipt = "20260428000623"
    excluded_receipt = "20260429000623"
    _seed_target(
        corp_code,
        corrected_receipt,
        report_nm="[기재정정]감사보고서제출",
        disc_date=date(2026, 4, 28),
    )
    _seed_target(
        corp_code,
        business_receipt,
        report_nm="사업보고서 (2025.12)",
        disc_date=date(2026, 4, 28),
    )
    _seed_target(
        corp_code,
        excluded_receipt,
        report_nm="사업보고서 (2025.12) (자회사의 주요경영사항)",
        disc_date=date(2026, 4, 29),
    )
    fetched: list[str] = []

    def fetch(receipt: str) -> dict[str, object]:
        fetched.append(receipt)
        if receipt == corrected_receipt:
            return {
                "ok": 0,
                "sections": 0,
                "error": "audit report attachment not found",
            }
        return {"ok": 1, "sections": 2, "audit_report_sections": 2}

    monkeypatch.setattr(recovery, "collect_report_sections_for_disclosure", fetch)
    monkeypatch.setattr(
        recovery,
        "_rebuild_derived_receipt",
        lambda *, year, target: {"rcept_no": str(target["rcept_no"])},
    )
    params = recovery.recovery_backfill_params(year=2025, market="KOSPI")
    lease = BackfillLease.start("audit_procedure_recovery", 2025, "KOSPI", params)

    result = recovery.run_audit_procedure_recovery_batch(
        lease,
        year=2025,
        market="KOSPI",
        limit=1,
    )

    assert result["ok"] == 1
    assert fetched == [corrected_receipt, business_receipt]


def test_successful_batch_resumes_after_saved_prefix_and_failure_retries(temp_engine, monkeypatch):
    """Catch a lease checkpoint that refetches successes or skips a failed receipt."""
    from kreports.collector import audit_procedure_recovery as recovery
    from kreports.maintenance.backfill_runs import BackfillLease

    _seed_target("00000020", "20260320000020")
    _seed_target("00000021", "20260320000021")
    fetched: list[str] = []

    def successful_fetch(receipt: str) -> dict:
        fetched.append(receipt)
        return {"ok": 1, "sections": 2}

    monkeypatch.setattr(recovery, "collect_report_sections_for_disclosure", successful_fetch)
    params = recovery.recovery_backfill_params(year=2025, market="KOSPI")
    first_lease = BackfillLease.start("audit_procedure_recovery", 2025, "KOSPI", params)
    first = recovery.run_audit_procedure_recovery_batch(first_lease, year=2025, market="KOSPI", limit=1)
    first_lease.succeed(first)
    second_lease = BackfillLease.start("audit_procedure_recovery", 2025, "KOSPI", params)
    second = recovery.run_audit_procedure_recovery_batch(second_lease, year=2025, market="KOSPI", limit=1)
    second_lease.succeed(second)

    assert fetched == ["20260320000020", "20260320000021"]
    assert first["next_cursor"] == {"corp_code": "00000020", "rcept_no": "20260320000020"}
    assert second["next_cursor"] == {"corp_code": "00000021", "rcept_no": "20260320000021"}

    def failed_fetch(receipt: str) -> dict:
        fetched.append(receipt)
        return {"ok": 0, "sections": 0, "error": "transport timeout"}

    _seed_target("00000022", "20260320000022")
    monkeypatch.setattr(recovery, "collect_report_sections_for_disclosure", failed_fetch)
    failed_lease = BackfillLease.start("audit_procedure_recovery", 2025, "KOSPI", params)
    failed = recovery.run_audit_procedure_recovery_batch(failed_lease, year=2025, market="KOSPI", limit=1)
    failed_lease.fail("transport_error", "transport timeout")
    retry_lease = BackfillLease.start("audit_procedure_recovery", 2025, "KOSPI", params)
    retry = recovery.run_audit_procedure_recovery_batch(retry_lease, year=2025, market="KOSPI", limit=1)

    assert failed["failed"] == 1
    assert retry["targets"][0]["corp_code"] == "00000022"
    assert retry["next_cursor"] == second["next_cursor"]


def test_resume_uses_latest_terminal_v4_cursor_once_before_v5_history(
    temp_engine,
):
    """Catch a v5 selector scope restart from the beginning after v4 progress."""
    from kreports.collector import audit_procedure_recovery as recovery
    from kreports.maintenance.backfill_runs import BackfillLease

    v5_params = recovery.recovery_backfill_params(year=2025, market="KOSPI")
    v4_params = {**v5_params, "selector_version": 4}
    completed_cursor = {"corp_code": "00838500", "rcept_no": "20260428800598"}
    failed_cursor = {"corp_code": "00838500", "rcept_no": "20260428800599"}
    v5_cursor = {"corp_code": "00838500", "rcept_no": "20260428800600"}

    completed_v4 = BackfillLease.start(
        "audit_procedure_recovery", 2025, "KOSPI", v4_params,
    )
    completed_v4.checkpoint(
        {"next_cursor": completed_cursor}, attempted=1, saved=1, no_data=0, errors=0,
    )
    completed_v4.succeed({"ok": 1})

    failed_v4 = BackfillLease.start(
        "audit_procedure_recovery", 2025, "KOSPI", v4_params,
    )
    failed_v4.checkpoint(
        {"next_cursor": failed_cursor}, attempted=2, saved=1, no_data=0, errors=1,
    )
    failed_v4.fail("transport_error", "retry corrected audit receipt")

    assert recovery._latest_resume_cursor(
        year=2025, market="KOSPI", params=v5_params,
    ) == failed_cursor

    terminal_v5 = BackfillLease.start(
        "audit_procedure_recovery", 2025, "KOSPI", v5_params,
    )
    terminal_v5.checkpoint(
        {"next_cursor": v5_cursor}, attempted=1, saved=1, no_data=0, errors=0,
    )
    terminal_v5.succeed({"ok": 1})

    assert recovery._latest_resume_cursor(
        year=2025, market="KOSPI", params=v5_params,
    ) == v5_cursor


@pytest.mark.parametrize(
    ("v5_checkpoint", "expected_legacy"),
    (
        ({}, True),
        ({"next_cursor": {"corp_code": 838500, "rcept_no": "20260428800599"}}, True),
        ({"exhausted": False}, True),
        ({"exhausted": True}, False),
    ),
)
def test_terminal_v5_consumes_legacy_only_with_cursor_or_explicit_exhaustion(
    temp_engine,
    v5_checkpoint,
    expected_legacy,
):
    """Catch empty or malformed v5 terminal records restarting a v4 prefix."""
    from kreports.collector import audit_procedure_recovery as recovery
    from kreports.maintenance.backfill_runs import BackfillLease

    v5_params = recovery.recovery_backfill_params(year=2025, market="KOSPI")
    v4_params = {**v5_params, "selector_version": 4}
    legacy_cursor = {"corp_code": "00828789", "rcept_no": "20260428828789"}
    legacy = BackfillLease.start(
        "audit_procedure_recovery", 2025, "KOSPI", v4_params,
    )
    legacy.checkpoint(
        {"next_cursor": legacy_cursor}, attempted=1, saved=1, no_data=0, errors=0,
    )
    legacy.fail("transport_error", "retry after saved prefix")

    v5 = BackfillLease.start("audit_procedure_recovery", 2025, "KOSPI", v5_params)
    v5.checkpoint(v5_checkpoint, attempted=0, saved=0, no_data=0, errors=0)
    v5.succeed({"ok": 0})

    assert recovery._latest_resume_cursor(
        year=2025, market="KOSPI", params=v5_params,
    ) == (legacy_cursor if expected_legacy else None)


def test_v5_batch_retries_failed_v4_target_after_saved_prefix(temp_engine, monkeypatch):
    """Keep run438's saved prefix and retry its direct failure with strict cursor order."""
    from kreports.collector import audit_procedure_recovery as recovery
    from kreports.maintenance.backfill_runs import BackfillLease

    saved_cursor = {"corp_code": "00828789", "rcept_no": "20260428828789"}
    failed_target = {"corp_code": "00838500", "rcept_no": "20260428800599"}
    _seed_target(saved_cursor["corp_code"], saved_cursor["rcept_no"])
    _seed_target(
        failed_target["corp_code"],
        failed_target["rcept_no"],
        report_nm="[기재정정]감사보고서제출",
        disc_date=date(2026, 4, 28),
    )
    v5_params = recovery.recovery_backfill_params(year=2025, market="KOSPI")
    v4_params = {**v5_params, "selector_version": 4}
    failed_v4 = BackfillLease.start(
        "audit_procedure_recovery", 2025, "KOSPI", v4_params,
    )
    failed_v4.checkpoint(
        {
            "next_cursor": saved_cursor,
            "exhausted": False,
            "last_error": {
                "corp_code": failed_target["corp_code"],
                "rcept_no": failed_target["rcept_no"],
                "message": "audit report attachment not found",
            },
        },
        attempted=2,
        saved=1,
        no_data=0,
        errors=1,
    )
    failed_v4.fail("transport_error", "retry corrected audit receipt")

    fetched: list[str] = []
    monkeypatch.setattr(
        recovery,
        "collect_report_sections_for_disclosure",
        lambda receipt: fetched.append(receipt) or {"ok": 1, "sections": 1},
    )
    monkeypatch.setattr(
        recovery,
        "_rebuild_derived_receipt",
        lambda *, year, target: {"rcept_no": target["rcept_no"]},
    )
    v5 = BackfillLease.start("audit_procedure_recovery", 2025, "KOSPI", v5_params)

    result = recovery.run_audit_procedure_recovery_batch(
        v5, year=2025, market="KOSPI", limit=1,
    )

    assert result["cursor_start"] == saved_cursor
    assert result["targets"] == [{
        "corp_code": failed_target["corp_code"],
        "rcept_no": failed_target["rcept_no"],
        "corp_name": f"회사{failed_target['corp_code']}",
    }]
    assert fetched == [failed_target["rcept_no"]]


def test_batch_checkpoint_counts_each_processed_receipt_once(temp_engine, monkeypatch):
    """Catch a checkpoint that adds a cumulative batch count more than once."""
    from kreports.collector import audit_procedure_recovery as recovery
    from kreports.db.engine import get_session
    from kreports.db.models import BackfillRun
    from kreports.maintenance.backfill_runs import BackfillLease

    _seed_target("00000030", "20260320000030")
    _seed_target("00000031", "20260320000031")
    monkeypatch.setattr(
        recovery,
        "collect_report_sections_for_disclosure",
        lambda _receipt: {"ok": 1, "sections": 1},
    )
    lease = BackfillLease.start(
        "audit_procedure_recovery",
        2025,
        "KOSPI",
        recovery.recovery_backfill_params(year=2025, market="KOSPI"),
    )
    result = recovery.run_audit_procedure_recovery_batch(
        lease,
        year=2025,
        market="KOSPI",
        limit=2,
    )
    lease.succeed(result)

    with get_session() as session:
        row = session.get(BackfillRun, lease.id)
        assert (row.attempted_count, row.saved_count, row.error_count) == (2, 2, 0)


def test_partial_success_failure_retries_only_the_failed_receipt(temp_engine, monkeypatch):
    """Catch a retry that refetches a saved prefix after a later receipt fails."""
    from kreports.collector import audit_procedure_recovery as recovery
    from kreports.maintenance.backfill_runs import BackfillLease

    _seed_target("00000050", "20260320000050")
    _seed_target("00000051", "20260320000051")
    params = recovery.recovery_backfill_params(year=2025, market="KOSPI")
    first_attempts: list[str] = []

    def partial_failure(receipt: str) -> dict:
        first_attempts.append(receipt)
        if receipt.endswith("51"):
            return {"ok": 0, "sections": 0, "error": "transport timeout"}
        return {"ok": 1, "sections": 1}

    monkeypatch.setattr(recovery, "collect_report_sections_for_disclosure", partial_failure)
    failed_lease = BackfillLease.start("audit_procedure_recovery", 2025, "KOSPI", params)
    failed = recovery.run_audit_procedure_recovery_batch(
        failed_lease,
        year=2025,
        market="KOSPI",
        limit=2,
    )
    failed_lease.fail("transport_error", "transport timeout")
    retry_attempts: list[str] = []
    monkeypatch.setattr(
        recovery,
        "collect_report_sections_for_disclosure",
        lambda receipt: retry_attempts.append(receipt) or {"ok": 1, "sections": 1},
    )
    retry_lease = BackfillLease.start("audit_procedure_recovery", 2025, "KOSPI", params)
    retry = recovery.run_audit_procedure_recovery_batch(
        retry_lease,
        year=2025,
        market="KOSPI",
        limit=1,
    )

    assert first_attempts == ["20260320000050", "20260320000051"]
    assert failed["next_cursor"] == {"corp_code": "00000050", "rcept_no": "20260320000050"}
    assert retry_attempts == ["20260320000051"]
    assert retry["next_cursor"] == {"corp_code": "00000051", "rcept_no": "20260320000051"}


def test_derived_failure_does_not_advance_the_fetch_cursor(temp_engine, monkeypatch):
    """Catch a checkpoint that skips a fetched receipt when its derived rebuild fails."""
    from kreports.collector import audit_procedure_recovery as recovery
    from kreports.db.engine import get_session
    from kreports.db.models import BackfillRun
    from kreports.maintenance.backfill_runs import BackfillLease

    _seed_target("00000060", "20260320000060")
    params = recovery.recovery_backfill_params(year=2025, market="KOSPI")
    monkeypatch.setattr(
        recovery,
        "collect_report_sections_for_disclosure",
        lambda _receipt: {"ok": 1, "sections": 1},
    )
    monkeypatch.setattr(
        recovery,
        "rebuild_kam_items_for_receipts",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("derived rebuild failed")),
    )
    failed_lease = BackfillLease.start("audit_procedure_recovery", 2025, "KOSPI", params)
    failed = recovery.run_audit_procedure_recovery_batch(
        failed_lease,
        year=2025,
        market="KOSPI",
        limit=1,
    )

    with get_session() as session:
        row = session.get(BackfillRun, failed_lease.id)
        checkpoint = json.loads(row.checkpoint_json)
        assert (row.attempted_count, row.saved_count, row.error_count) == (1, 0, 1)
        assert checkpoint["next_cursor"] is None
        assert checkpoint["batch"]["api_receipt_fetches"] == 1
        assert checkpoint["last_error"] == {
            "corp_code": "00000060",
            "rcept_no": "20260320000060",
            "message": "derived rebuild failed",
        }
    assert failed["failed"] == 1
    failed_lease.fail("storage_error", "derived rebuild failed")

    retried: list[str] = []
    monkeypatch.setattr(
        recovery,
        "collect_report_sections_for_disclosure",
        lambda receipt: retried.append(receipt) or {"ok": 1, "sections": 1},
    )
    monkeypatch.setattr(
        recovery,
        "rebuild_kam_items_for_receipts",
        lambda **_kwargs: {"total": 0, "rows_written": 0, "procedure_items": 0, "receipts": []},
    )
    retry_lease = BackfillLease.start("audit_procedure_recovery", 2025, "KOSPI", params)
    recovery.run_audit_procedure_recovery_batch(
        retry_lease,
        year=2025,
        market="KOSPI",
        limit=1,
    )

    assert retried == ["20260320000060"]


def test_scoped_kam_rebuild_includes_collector_attachment_receipts(temp_engine):
    """Catch a post-fetch rebuild that ignores DART attachment receipt suffixes."""
    from kreports.collector.report_document_collector import rebuild_kam_items_for_receipts
    from kreports.db.engine import get_session
    from kreports.db.models import ReportSection

    root_receipt = "20260320000040"
    attachment_receipt = f"{root_receipt}_11100001"
    _seed_target("00000040", root_receipt)
    with get_session() as session:
        session.add(ReportSection(
            rcept_no=attachment_receipt,
            corp_code="00000040",
            bsns_year=2025,
            source_type="audit_report",
            section_key="kam",
            body_text="핵심감사사항 수익인식 감사절차를 수행하였습니다.",
            ordinal=0,
        ))

    result = rebuild_kam_items_for_receipts(year=2025, rcept_nos=[root_receipt])

    assert [row["rcept_no"] for row in result["receipts"]] == [attachment_receipt]


def test_cli_exposes_a_bounded_historical_recovery_mode(temp_engine, monkeypatch, tmp_path):
    """Catch removal of the public collector boundary around the durable selector."""
    from typer.testing import CliRunner
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import kreports.db.engine as db_engine
    from kreports.cli.main import app
    from kreports.collector import audit_procedure_recovery as recovery
    from kreports.config import settings
    from kreports.db.models import Base
    from kreports.maintenance import rehearsal_safety

    database = tmp_path / "recovery.db"
    active_engine = create_engine(f"sqlite:///{database}")
    Base.metadata.create_all(bind=active_engine)
    monkeypatch.setattr(db_engine, "engine", active_engine)
    monkeypatch.setattr(db_engine, "SessionLocal", sessionmaker(bind=active_engine))

    monkeypatch.setattr(settings, "dart_api_key", "test-key")
    monkeypatch.setenv("RAW_STORAGE_BACKEND", "gcs")
    monkeypatch.setenv("RAW_STORAGE_BUCKET", "test-recovery-raw")
    monkeypatch.setenv("RAW_STORAGE_KEEP_INLINE", "false")
    monkeypatch.setattr(settings, "raw_storage_backend", "gcs")
    monkeypatch.setattr(settings, "raw_storage_keep_inline", False)
    monkeypatch.setattr(rehearsal_safety, "assert_free_space", lambda *_args, **_kwargs: 20 * 1024**3)
    captured = {}

    def fake_run(lease, **kwargs):
        captured.update(kwargs)
        return {
            "processed": 0,
            "ok": 0,
            "failed": 0,
            "sections": 0,
            "errors": [],
            "next_cursor": None,
            "exhausted": True,
        }

    monkeypatch.setattr(recovery, "run_audit_procedure_recovery_batch", fake_run)
    result = CliRunner().invoke(
        app,
        ["collect-audit-procedure-recovery", "--year", "2025", "--market", "KOSPI", "--limit", "2"],
    )

    assert result.exit_code == 0, result.stdout
    assert captured["year"] == 2025
    assert captured["market"] == "KOSPI"
    assert captured["limit"] == 2
    assert callable(captured["progress_callback"])


def test_cli_rejects_non_gcs_raw_storage_before_starting_lease(temp_engine, monkeypatch):
    """Catch a recovery CLI that permits local file raw bodies for this cohort."""
    from typer.testing import CliRunner

    from kreports.cli.main import app
    from kreports.collector import audit_procedure_recovery as recovery
    from kreports.config import settings

    monkeypatch.setattr(settings, "dart_api_key", "test-key")
    monkeypatch.setattr(settings, "raw_storage_backend", "file")
    monkeypatch.setattr(settings, "raw_storage_keep_inline", False)
    monkeypatch.setenv("RAW_STORAGE_BACKEND", "file")
    monkeypatch.setenv("RAW_STORAGE_KEEP_INLINE", "false")
    started: list[bool] = []
    monkeypatch.setattr(
        recovery,
        "run_audit_procedure_recovery_batch",
        lambda *_args, **_kwargs: started.append(True) or _empty_recovery_result(),
    )

    result = CliRunner().invoke(
        app,
        ["collect-audit-procedure-recovery", "--year", "2025", "--market", "KOSPI", "--limit", "2"],
    )

    assert result.exit_code == 2
    assert "GCS" in result.output
    assert started == []


def test_cli_rejects_gcs_without_bucket_before_starting_lease(temp_engine, monkeypatch):
    """Catch a GCS recovery invocation that would create unaddressable raw objects."""
    from typer.testing import CliRunner

    from kreports.cli.main import app
    from kreports.collector import audit_procedure_recovery as recovery
    from kreports.config import settings

    monkeypatch.setattr(settings, "dart_api_key", "test-key")
    monkeypatch.setattr(settings, "raw_storage_backend", "gcs")
    monkeypatch.setattr(settings, "raw_storage_keep_inline", False)
    monkeypatch.setenv("RAW_STORAGE_BACKEND", "gcs")
    monkeypatch.delenv("RAW_STORAGE_BUCKET", raising=False)
    started: list[bool] = []
    monkeypatch.setattr(
        recovery,
        "run_audit_procedure_recovery_batch",
        lambda *_args, **_kwargs: started.append(True) or _empty_recovery_result(),
    )

    result = CliRunner().invoke(
        app,
        ["collect-audit-procedure-recovery", "--year", "2025", "--market", "KOSPI", "--limit", "2"],
    )

    assert result.exit_code == 2
    assert "RAW_STORAGE_BUCKET" in result.output
    assert started == []


def test_cli_rejects_limit_above_safe_recovery_batch(temp_engine, monkeypatch):
    """Catch an operator invocation that bypasses the bounded 25-root limit."""
    from typer.testing import CliRunner

    from kreports.cli.main import app
    from kreports.collector import audit_procedure_recovery as recovery
    from kreports.config import settings

    monkeypatch.setattr(settings, "dart_api_key", "test-key")
    monkeypatch.setattr(settings, "raw_storage_backend", "gcs")
    monkeypatch.setattr(settings, "raw_storage_keep_inline", False)
    monkeypatch.setenv("RAW_STORAGE_BACKEND", "gcs")
    monkeypatch.setenv("RAW_STORAGE_BUCKET", "test-recovery-raw")
    started: list[bool] = []
    monkeypatch.setattr(
        recovery,
        "run_audit_procedure_recovery_batch",
        lambda *_args, **_kwargs: started.append(True) or _empty_recovery_result(),
    )

    result = CliRunner().invoke(
        app,
        ["collect-audit-procedure-recovery", "--year", "2025", "--market", "KOSPI", "--limit", "26"],
    )

    assert result.exit_code == 2
    assert "25" in result.output
    assert started == []


def test_cli_blocks_mutation_when_python_disk_preflight_fails(temp_engine, monkeypatch, tmp_path):
    """Catch a collector that opens a recovery lease after the 10 GiB guard fails."""
    from typer.testing import CliRunner
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import kreports.db.engine as db_engine
    from kreports.cli.main import app
    from kreports.collector import audit_procedure_recovery as recovery
    from kreports.config import settings
    from kreports.db.models import Base
    from kreports.maintenance import rehearsal_safety

    monkeypatch.setattr(settings, "dart_api_key", "test-key")
    monkeypatch.setattr(settings, "raw_storage_backend", "gcs")
    monkeypatch.setattr(settings, "raw_storage_keep_inline", False)
    monkeypatch.setenv("RAW_STORAGE_BACKEND", "gcs")
    monkeypatch.setenv("RAW_STORAGE_BUCKET", "test-recovery-raw")
    database = tmp_path / "candidate" / "recovery.db"
    database.parent.mkdir()
    active_engine = create_engine(f"sqlite:///{database}")
    Base.metadata.create_all(bind=active_engine)
    monkeypatch.setattr(db_engine, "engine", active_engine)
    monkeypatch.setattr(db_engine, "SessionLocal", sessionmaker(bind=active_engine))
    monkeypatch.setattr(
        rehearsal_safety,
        "assert_free_space",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            rehearsal_safety.RehearsalSafetyError("insufficient_free_space", "test disk guard")
        ),
    )
    started: list[bool] = []
    monkeypatch.setattr(
        recovery,
        "run_audit_procedure_recovery_batch",
        lambda *_args, **_kwargs: started.append(True) or _empty_recovery_result(),
    )

    result = CliRunner().invoke(
        app,
        ["collect-audit-procedure-recovery", "--year", "2025", "--market", "KOSPI", "--limit", "2"],
    )

    assert result.exit_code == 2
    assert "disk" in result.output.lower()
    assert started == []


def test_cli_preflight_checks_the_active_sqlite_database_parent(temp_engine, monkeypatch, tmp_path):
    """Catch a disk guard that validates cwd instead of the database filesystem it mutates."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from typer.testing import CliRunner

    import kreports.db.engine as db_engine
    from kreports.cli.main import app
    from kreports.collector import audit_procedure_recovery as recovery
    from kreports.config import settings
    from kreports.db.models import Base
    from kreports.maintenance import rehearsal_safety

    database = tmp_path / "candidate" / "recovery.db"
    database.parent.mkdir()
    active_engine = create_engine(f"sqlite:///{database}")
    Base.metadata.create_all(bind=active_engine)
    monkeypatch.setattr(db_engine, "engine", active_engine)
    monkeypatch.setattr(db_engine, "SessionLocal", sessionmaker(bind=active_engine))
    monkeypatch.setattr(settings, "dart_api_key", "test-key")
    monkeypatch.setattr(settings, "raw_storage_backend", "gcs")
    monkeypatch.setattr(settings, "raw_storage_keep_inline", False)
    monkeypatch.setenv("RAW_STORAGE_BACKEND", "gcs")
    monkeypatch.setenv("RAW_STORAGE_BUCKET", "test-recovery-raw")
    paths = []
    monkeypatch.setattr(
        rehearsal_safety,
        "assert_free_space",
        lambda path, **_kwargs: paths.append(path) or 20 * 1024**3,
    )
    monkeypatch.setattr(
        recovery,
        "run_audit_procedure_recovery_batch",
        lambda *_args, **_kwargs: _empty_recovery_result(),
    )

    result = CliRunner().invoke(
        app,
        ["collect-audit-procedure-recovery", "--year", "2025", "--market", "KOSPI", "--limit", "2"],
    )

    assert result.exit_code == 0, result.output
    assert paths == [database.parent.resolve()]
