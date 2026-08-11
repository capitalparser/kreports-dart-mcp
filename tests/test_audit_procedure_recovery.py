"""Historical-membership recovery targets for audit-procedure evidence."""
from __future__ import annotations

import hashlib
from datetime import date


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
            disc_date=date(2026, 3, 20),
            disc_type="F",
            report_nm="감사보고서 (2025.12)",
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

    _seed_target("00000010", "20260320000002")
    _seed_target("00000010", "20260320000001")
    _seed_target("00000011", "20260320000003")

    first = select_audit_procedure_recovery_targets(year=2025, market="KOSPI", limit=2)
    second = select_audit_procedure_recovery_targets(
        year=2025, market="KOSPI", limit=2, after=first["targets"][-1]
    )

    assert [(row["corp_code"], row["rcept_no"]) for row in first["targets"]] == [
        ("00000010", "20260320000001"),
        ("00000010", "20260320000002"),
    ]
    assert [(row["corp_code"], row["rcept_no"]) for row in second["targets"]] == [
        ("00000011", "20260320000003"),
    ]


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


def test_cli_exposes_a_bounded_historical_recovery_mode(temp_engine, monkeypatch):
    """Catch removal of the public collector boundary around the durable selector."""
    from typer.testing import CliRunner

    from kreports.cli.main import app
    from kreports.collector import audit_procedure_recovery as recovery
    from kreports.config import settings

    monkeypatch.setattr(settings, "dart_api_key", "test-key")
    monkeypatch.setattr(settings, "raw_storage_backend", "file")
    monkeypatch.setattr(settings, "raw_storage_keep_inline", False)
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
