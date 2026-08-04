import hashlib
import json
import re
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from kreports.db.engine import get_session
from kreports.db.models import (
    AuditFee,
    Auditor,
    AuditProcedureItem,
    BusinessAffiliateAuditor,
    Company,
    CompanyYearQuality,
    Disclosure,
    ExtractionRun,
    FetchLog,
    FinancialFactCompact,
    ReportSection,
    SourceDocument,
    Base,
)
from kreports.quality.company_year_fingerprint import (
    QUALITY_GRADE_KEYS,
    QUALITY_STATUS_KEYS,
    build_quality_evidence_summary,
    quality_input_fingerprint,
)
from kreports.semantic.metrics import CORE_FINANCIAL_METRICS


def _quality_evidence_inputs() -> dict:
    return {
        "statuses": {
            "financial_core": "available",
            "auditor": "available",
            "audit_fee": "partial",
            "policy": "full_body",
            "kam": "summary_only",
            "audit_procedure": "missing",
            "group_audit": "partial",
        },
        "grades": {
            "investor_core": "A",
            "auditor_full": "D",
            "group_audit": "D",
        },
        "blockers": ("kam_summary_only", "procedure_missing"),
        "quality_version": "v1",
    }


def test_quality_fingerprint_is_stable_across_mapping_and_blocker_order():
    left = build_quality_evidence_summary(**_quality_evidence_inputs())
    right = build_quality_evidence_summary(
        statuses=dict(reversed(list(left["statuses"].items()))),
        grades=dict(reversed(list(left["grades"].items()))),
        blockers=("procedure_missing", "kam_summary_only", "kam_summary_only"),
        quality_version="v1",
    )

    assert QUALITY_STATUS_KEYS == (
        "financial_core",
        "auditor",
        "audit_fee",
        "policy",
        "kam",
        "audit_procedure",
        "group_audit",
    )
    assert QUALITY_GRADE_KEYS == (
        "investor_core",
        "auditor_full",
        "group_audit",
    )
    assert left == right
    assert quality_input_fingerprint(left) == quality_input_fingerprint(right)
    assert len(quality_input_fingerprint(left)) == 64


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("statuses", {"financial_core": "partial"}),
        ("grades", {"investor_core": "B"}),
        ("blockers", ("new_blocker",)),
        ("quality_version", "v2"),
    ],
)
def test_quality_fingerprint_changes_for_each_semantic_input(
    field,
    replacement,
):
    baseline_inputs = _quality_evidence_inputs()
    changed_inputs = _quality_evidence_inputs()
    if field in {"statuses", "grades"}:
        changed_inputs[field].update(replacement)
    else:
        changed_inputs[field] = replacement

    baseline = build_quality_evidence_summary(**baseline_inputs)
    changed = build_quality_evidence_summary(**changed_inputs)

    assert quality_input_fingerprint(changed) != quality_input_fingerprint(
        baseline
    )


@pytest.mark.parametrize(
    ("field", "key_change"),
    [
        ("statuses", ("audit_fee", None)),
        ("statuses", ("unknown_status", "missing")),
        ("grades", ("group_audit", None)),
        ("grades", ("unknown_grade", "D")),
    ],
)
def test_quality_summary_rejects_missing_or_unknown_required_keys(
    field,
    key_change,
):
    inputs = _quality_evidence_inputs()
    key, value = key_change
    if value is None:
        inputs[field].pop(key)
    else:
        inputs[field][key] = value

    label = "status" if field == "statuses" else "grade"
    with pytest.raises(ValueError, match=f"{label} keys must equal"):
        build_quality_evidence_summary(**inputs)


def test_quality_summary_serialization_contains_no_timestamp():
    summary = build_quality_evidence_summary(**_quality_evidence_inputs())

    assert "timestamp" not in json.dumps(summary, sort_keys=True)
    assert set(summary) == {
        "statuses",
        "grades",
        "blockers",
        "quality_version",
    }


@pytest.mark.parametrize(
    "mutation",
    [
        "extra_key",
        "invalid_status",
        "invalid_grade",
        "too_many_blockers",
        "long_blocker",
        "long_version",
    ],
)
def test_quality_summary_rejects_noncanonical_or_unbounded_semantics(mutation):
    inputs = _quality_evidence_inputs()
    if mutation == "extra_key":
        summary = build_quality_evidence_summary(**inputs)
        summary["timestamp"] = "2026-07-29T00:00:00Z"
        with pytest.raises(ValueError):
            quality_input_fingerprint(summary)
        return
    if mutation == "invalid_status":
        inputs["statuses"]["kam"] = "available"
    elif mutation == "invalid_grade":
        inputs["grades"]["group_audit"] = "B"
    elif mutation == "too_many_blockers":
        inputs["blockers"] = tuple(f"blocker-{index}" for index in range(33))
    elif mutation == "long_blocker":
        inputs["blockers"] = ("x" * 129,)
    else:
        inputs["quality_version"] = "v" * 21

    with pytest.raises(ValueError):
        build_quality_evidence_summary(**inputs)


def test_company_year_quality_schema_is_versioned_append_only(temp_engine):
    from kreports.db.migrations import MIGRATIONS, apply_schema_migrations
    from kreports.db.models import CompanyYearQuality

    expected_prefix = [
        "20260711_01_quality_contract",
        "20260711_02_company_year_quality",
        "20260711_03_backfill_run_lifecycle",
        "20260711_04_backfill_owner_identity",
        "20260711_05_kam_items",
        "20260711_06_audit_procedure_linkage",
        "20260711_07_audit_fee_availability",
        "20260711_08_group_audit_graph",
        "20260711_09_audit_fee_observations",
        "20260711_10_financial_compact_provenance",
        "20260711_11_company_year_quality_freshness",
    ]
    expected_append_only_tail = [
        "20260731_12_accounting_note_chapter_contract",
        "20260731_13_accounting_note_chapter_storage_contract",
        "20260731_14_schema_contract_repair",
        "20260805_15_disclosure_lookup_index",
    ]
    revisions = [migration.revision for migration in MIGRATIONS]
    assert revisions[:len(expected_prefix)] == expected_prefix
    assert revisions[len(expected_prefix):] == expected_append_only_tail
    assert revisions == sorted(revisions)
    assert len(revisions) == len(set(revisions))
    assert all(re.fullmatch(r"\d{8}_\d{2}_[a-z0-9_]+", revision) for revision in revisions)
    assert CompanyYearQuality.__tablename__ == "company_year_quality"

    with temp_engine.begin() as connection:
        applied = apply_schema_migrations(connection)

    assert applied == revisions
    columns = {
        column["name"]
        for column in inspect(temp_engine).get_columns("company_year_quality")
    }
    assert {
        "corp_code",
        "bsns_year",
        "market",
        "financial_core_status",
        "auditor_status",
        "audit_fee_status",
        "policy_status",
        "kam_status",
        "audit_procedure_status",
        "group_audit_status",
        "investor_grade",
        "auditor_grade",
        "group_audit_grade",
        "blockers_json",
        "quality_version",
        "updated_at",
        "input_fingerprint",
        "evidence_summary_json",
    } == columns


def _seed_core_financial_years(
    corp_code: str,
    years: range,
    *,
    market: str = "KOSPI",
) -> None:
    with get_session() as session:
        session.add(
            Company(
                corp_code=corp_code,
                stock_code=corp_code[-6:],
                corp_name=f"회사-{corp_code}",
                market=market,
            )
        )
        for year in years:
            for metric_key in CORE_FINANCIAL_METRICS:
                session.add(
                    FinancialFactCompact(
                        corp_code=corp_code,
                        bsns_year=year,
                        fs_div="CFS",
                        metric_key=metric_key,
                        metric_name=metric_key,
                        amount=100,
                    )
                )


def _seed_source_backed_core_financial_years(
    corp_code: str,
    years: range,
    *,
    market: str = "KOSPI",
    invalid_field: str | None = None,
    split_metric: str | None = None,
) -> None:
    """Seed compact metrics only when every row has a literal annual citation."""
    with get_session() as session:
        session.add(
            Company(
                corp_code=corp_code,
                stock_code=corp_code[-6:],
                corp_name=f"회사-{corp_code}",
                market=market,
            )
        )
        for year in years:
            receipt = f"{year + 1}0318{int(corp_code):06d}"
            report_nm = f"사업보고서 ({year}.12)"
            session.add(
                Disclosure(
                    rcept_no=receipt,
                    corp_code=corp_code,
                    corp_name=f"회사-{corp_code}",
                    disc_date=date(year + 1, 3, 18),
                    disc_type="A",
                    report_nm=report_nm,
                )
            )
            for metric_key in CORE_FINANCIAL_METRICS:
                fs_div = "OFS" if metric_key == split_metric else "CFS"
                values = {
                    "corp_code": corp_code,
                    "bsns_year": year,
                    "fs_div": fs_div,
                    "metric_key": metric_key,
                    "metric_name": metric_key,
                    "amount": 100,
                    "source_account_id": f"ifrs-full_{metric_key}",
                    "source_table": "financial_facts",
                    "unit": "KRW",
                    "period_type": "instant" if metric_key in {
                        "assets", "liabilities", "equity",
                    } else "duration",
                    "citation_rcept_no": receipt,
                    "citation_report_nm": report_nm,
                    "citation_basis": "company_year_annual_filing_match",
                    "quality_status": "usable",
                }
                if invalid_field == "quality_status":
                    values["quality_status"] = "limited"
                elif invalid_field == "citation_basis":
                    values["citation_basis"] = "uncitable"
                elif invalid_field == "citation_rcept_no":
                    values["citation_rcept_no"] = f"{year + 1}0318009999"
                session.add(FinancialFactCompact(**values))


def test_investor_grade_does_not_require_five_year_audit_fee(temp_engine):
    from kreports.quality.company_year import (
        company_year_quality,
        rebuild_company_year_quality,
    )

    _seed_source_backed_core_financial_years("00126380", range(2021, 2026))

    result = rebuild_company_year_quality(2021, 2025)
    latest = company_year_quality("00126380", 2025)

    assert result["rows_written"] == 5
    assert latest["feature_grades"]["investor_core"] == "A"
    assert latest["feature_grades"]["audit_fee_peer"] == "not_applicable"


def test_investor_grade_rejects_uncited_core_metrics(temp_engine):
    """Populated compact values cannot establish investor-core coverage alone."""
    from kreports.quality.company_year import (
        company_year_quality,
        rebuild_company_year_quality,
    )

    _seed_core_financial_years("00126380", range(2023, 2026))

    rebuild_company_year_quality(2025, 2025)
    quality = company_year_quality("00126380", 2025)

    assert quality["statuses"]["financial_core"] == "partial"
    assert quality["feature_grades"]["investor_core"] == "D"
    assert "financial_core_source_unproven" in quality["blockers"]


@pytest.mark.parametrize(
    "invalid_field",
    ["quality_status", "citation_basis", "citation_rcept_no"],
)
def test_investor_grade_rejects_each_unproven_core_metric(
    temp_engine,
    invalid_field,
):
    """Every required core metric must retain usable canonical filing proof."""
    from kreports.quality.company_year import (
        company_year_quality,
        rebuild_company_year_quality,
    )

    _seed_source_backed_core_financial_years(
        "00126380",
        range(2023, 2026),
        invalid_field=invalid_field,
    )

    rebuild_company_year_quality(2025, 2025)
    quality = company_year_quality("00126380", 2025)

    assert quality["statuses"]["financial_core"] == "partial"
    assert quality["feature_grades"]["investor_core"] == "D"
    assert "financial_core_source_unproven" in quality["blockers"]
    assert quality["quality_version"] == "v2"


def test_investor_grade_does_not_combine_core_metrics_across_financial_statements(
    temp_engine,
):
    """CFS and OFS fragments cannot be merged into a source-backed core year."""
    from kreports.quality.company_year import (
        company_year_quality,
        rebuild_company_year_quality,
    )

    _seed_source_backed_core_financial_years(
        "00126380",
        range(2023, 2026),
        split_metric="equity",
    )

    rebuild_company_year_quality(2025, 2025)
    quality = company_year_quality("00126380", 2025)

    assert quality["statuses"]["financial_core"] == "partial"
    assert quality["feature_grades"]["investor_core"] == "D"


def test_investor_b_keeps_three_source_backed_years_despite_two_unproven_years(
    temp_engine,
):
    """Unproven extras cannot erase three independently source-backed years."""
    from kreports.quality.company_year import (
        company_year_quality,
        rebuild_company_year_quality,
    )

    _seed_source_backed_core_financial_years("00126380", range(2021, 2026))
    with get_session() as session:
        for row in (
            session.query(FinancialFactCompact)
            .filter(
                FinancialFactCompact.corp_code == "00126380",
                FinancialFactCompact.bsns_year.in_((2021, 2022)),
            )
            .all()
        ):
            row.quality_status = "limited"

    rebuild_company_year_quality(2025, 2025)
    quality = company_year_quality("00126380", 2025)

    assert quality["statuses"]["financial_core"] == "available"
    assert quality["feature_grades"]["investor_core"] == "B"


def test_investor_a_needs_five_proven_years_not_a_calendar_year_disclosure(
    temp_engine,
):
    """A is defined by five annual-core proofs, not an unrelated filing date."""
    from kreports.quality.company_year import _investor_grade

    grade, blockers = _investor_grade(
        "00126380",
        2025,
        {year: "available" for year in range(2021, 2026)},
        set(),
    )

    assert grade == "A"
    assert blockers == []


def test_summary_only_kam_is_not_procedure_ready(temp_engine):
    from kreports.quality.company_year import (
        company_year_quality,
        rebuild_company_year_quality,
    )

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
            ReportSection(
                rcept_no="20250318000002",
                corp_code="00126380",
                bsns_year=2025,
                source_type="audit_report",
                section_key="kam",
                section_title="핵심감사사항",
                body_text="핵심감사사항이 존재합니다.",
                body_length=15,
            )
        )

    rebuild_company_year_quality(2025, 2025)
    quality = company_year_quality("00126380", 2025)

    assert quality["statuses"]["kam"] == "summary_only"
    assert quality["statuses"]["audit_procedure"] == "missing"


@pytest.mark.parametrize(
    "extractor_name",
    ["sections", "document_features", "kam_sections"],
)
def test_kam_extraction_error_is_not_degraded_to_missing(
    temp_engine,
    extractor_name,
):
    from kreports.quality.company_year import (
        company_year_quality,
        rebuild_company_year_quality,
    )

    with get_session() as session:
        session.add(
            Company(
                corp_code="00126380",
                stock_code="005930",
                corp_name="삼성전자",
                market="KOSPI",
            )
        )
        source = SourceDocument(
            rcept_no="20250318000003",
            corp_code="00126380",
            bsns_year=2025,
            source_type="audit_report",
            report_nm="감사보고서",
            raw_content="",
            doc_hash="a" * 40,
        )
        session.add(source)
        session.flush()
        session.add(
            ExtractionRun(
                source_document_id=source.id,
                rcept_no=source.rcept_no,
                source_type="audit_report",
                extractor_name=extractor_name,
                status="error",
                rows_written=0,
                error_msg="parser failed",
            )
        )

    rebuild_company_year_quality(2025, 2025)
    quality = company_year_quality("00126380", 2025)

    assert quality["statuses"]["kam"] == "error"
    assert quality["statuses"]["audit_procedure"] == "error"
    assert "kam_error" in quality["blockers"]


def test_cached_default_all_extractor_error_outranks_missing(
    temp_engine,
    monkeypatch,
):
    import kreports.collector.report_document_collector as collector_module
    from kreports.quality.company_year import (
        company_year_quality,
        rebuild_company_year_quality,
    )

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
                rcept_no="20250318000009",
                corp_code="00126380",
                bsns_year=2025,
                source_type="audit_report",
                report_nm="감사보고서",
                raw_content="<DOCUMENT>cached audit report</DOCUMENT>",
                doc_hash="b" * 40,
            )
        )

    def fail_cached_extraction(*_args, **_kwargs):
        raise ValueError("cached parser failed")

    monkeypatch.setattr(
        collector_module,
        "extract_document_features_from_content",
        fail_cached_extraction,
    )

    extraction = collector_module.run_document_extractors(
        year=2025,
        source_type="audit_report",
    )

    assert extraction["failed"] == 1
    with get_session() as session:
        outcome = (
            session.query(ExtractionRun)
            .filter_by(
                rcept_no="20250318000009",
                extractor_name="all",
                status="error",
            )
            .one()
        )
        assert outcome.error_msg == "cached parser failed"

    rebuild_company_year_quality(2025, 2025)
    quality = company_year_quality("00126380", 2025)

    assert quality["statuses"]["kam"] == "error"
    assert quality["statuses"]["audit_procedure"] == "error"
    assert "kam_error" in quality["blockers"]


def test_group_audit_withholds_a_without_persisted_qsc_evidence(temp_engine):
    from kreports.quality.company_year import (
        company_year_quality,
        rebuild_company_year_quality,
    )

    _seed_core_financial_years("00126380", range(2025, 2026))
    with get_session() as session:
        session.add(
            BusinessAffiliateAuditor(
                parent_corp_code="00126380",
                parent_rcept_no="20250318000004",
                bsns_year=2025,
                name="종속회사",
                relation="종속기업",
                ownership_pct=80.0,
                assets="20",
            )
        )

    rebuild_company_year_quality(2025, 2025)
    quality = company_year_quality("00126380", 2025)

    assert quality["statuses"]["group_audit"] == "partial"
    assert quality["feature_grades"]["group_audit"] == "D"


def test_explicit_no_kam_and_supported_source_gap_is_not_auditor_ready(
    temp_engine,
):
    from kreports.db.models import AccountingPolicyItem, Auditor
    from kreports.quality.company_year import (
        company_year_quality,
        rebuild_company_year_quality,
    )

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
            Auditor(
                corp_code="00126380",
                bsns_year=2025,
                fs_div="CFS",
                auditor_nm="감사법인",
                audit_opinion="적정",
            )
        )
        session.add(
            ReportSection(
                rcept_no="20250318000005",
                corp_code="00126380",
                bsns_year=2025,
                source_type="audit_report",
                section_key="kam",
                body_text="보고할 핵심감사사항이 없습니다.",
            )
        )
        session.add(
            AccountingPolicyItem(
                corp_code="00126380",
                bsns_year=2025,
                fs_div="CFS",
                rcept_no="20250318000006",
                item_key="revenue",
                body="수익인식 회계정책 근거 " * 20,
            )
        )
        session.add(
            FetchLog(
                task_type="audit_fee",
                corp_code="00126380",
                year=2025,
                status="no_data",
            )
        )

    rebuild_company_year_quality(2025, 2025)
    quality = company_year_quality("00126380", 2025)

    assert quality["statuses"]["kam"] == "explicit_no_kam"
    assert quality["statuses"]["audit_procedure"] == "not_applicable"
    assert quality["statuses"]["audit_fee"] == "partial"
    assert quality["feature_grades"]["auditor_full"] == "D"


def test_rebuild_is_idempotent_and_scoped_by_year_and_market(temp_engine):
    from kreports.quality.company_year import rebuild_company_year_quality

    _seed_core_financial_years("00126380", range(2024, 2026), market="KOSPI")
    _seed_core_financial_years("00999999", range(2024, 2026), market="KOSDAQ")

    first = rebuild_company_year_quality(2024, 2025, market="KOSPI")
    second = rebuild_company_year_quality(2024, 2025, market="KOSPI")

    with get_session() as session:
        count = int(session.query(func.count(CompanyYearQuality.corp_code)).scalar())
        rows = (
            session.query(
                CompanyYearQuality.corp_code,
                CompanyYearQuality.bsns_year,
            )
            .order_by(
                CompanyYearQuality.corp_code,
                CompanyYearQuality.bsns_year,
            )
            .all()
        )

    assert first["rows_written"] == 2
    assert second["rows_written"] == 2
    assert count == 2
    assert rows == [("00126380", 2024), ("00126380", 2025)]


def test_rebuild_reads_audit_fee_from_current_collector_engine_after_wal_write(
    tmp_path,
    monkeypatch,
):
    """Regression: quality-ledger writes must not make later rows unavailable.

    `procedure_read_engine` must still reject this same uncheckpointed WAL for
    public immutable readers.  The collector-only ledger rebuild, however,
    must read its own current SQLite engine after the first quality-row write.
    """
    import kreports.db.engine as engine_module
    from kreports.analysis.audit_reporting import audit_fee_availability
    from kreports.quality.company_year import rebuild_company_year_quality

    db_path = tmp_path / "quality-wal.db"
    file_engine = create_engine(f"sqlite:///{db_path}", poolclass=NullPool)
    Base.metadata.create_all(file_engine)
    file_session = sessionmaker(
        bind=file_engine,
        autocommit=False,
        autoflush=False,
    )
    monkeypatch.setattr(engine_module, "engine", file_engine)
    monkeypatch.setattr(engine_module, "SessionLocal", file_session)
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")

    with file_engine.connect() as keeper:
        keeper.execute(text("PRAGMA journal_mode=WAL"))
        keeper.execute(text("PRAGMA wal_autocheckpoint=0"))
        keeper.commit()
        with file_session() as session:
            for corp_code in ("00000001", "00000002"):
                session.add(
                    Company(
                        corp_code=corp_code,
                        stock_code=corp_code[-6:],
                        corp_name=f"WAL회사-{corp_code}",
                        market="KOSPI",
                    )
                )
                session.add(
                    AuditFee(
                        corp_code=corp_code,
                        bsns_year=2025,
                        audit_fee_m=100,
                        audit_hours=1_000,
                        actual_fee_m=100,
                        actual_hours=1_000,
                        source_class="cached_business_report",
                        source_rcept_no=f"receipt-{corp_code}",
                        source_period="2025",
                        availability_status="available",
                        quality_status="verified",
                        compatibility_basis="actual",
                        conflict_status="none",
                        source_observations_json="[]",
                    )
                )
            session.commit()
        keeper.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
        keeper.commit()

        result = rebuild_company_year_quality(2025, 2025)

        wal_path = db_path.with_name(f"{db_path.name}-wal")
        assert wal_path.exists() and wal_path.stat().st_size > 0
        with file_session() as session:
            statuses = dict(
                session.query(
                    CompanyYearQuality.corp_code,
                    CompanyYearQuality.audit_fee_status,
                ).all()
            )
        public_status = audit_fee_availability(
            "00000002",
            2025,
        )["availability_status"]

    assert result["rows_written"] == 2
    assert statuses == {"00000001": "available", "00000002": "available"}
    assert public_status == "schema_unavailable"


def test_rebuild_persists_stable_freshness_and_tracks_evidence_change(
    temp_engine,
):
    from kreports.quality.company_year import (
        company_year_quality,
        rebuild_company_year_quality,
    )

    with get_session() as session:
        session.add(
            Company(
                corp_code="00126380",
                stock_code="005930",
                corp_name="삼성전자",
                market="KOSPI",
            )
        )

    rebuild_company_year_quality(2025, 2025)
    first = company_year_quality("00126380", 2025)
    with get_session() as session:
        first_row = session.get(CompanyYearQuality, ("00126380", 2025))
        assert first_row is not None
        first_summary_json = first_row.evidence_summary_json
        first_updated_at = first_row.updated_at

    assert first["input_fingerprint"]
    assert first["evidence_summary"]["statuses"] == first["statuses"]
    assert first["evidence_summary"]["grades"] == {
        "investor_core": first["feature_grades"]["investor_core"],
        "auditor_full": first["feature_grades"]["auditor_full"],
        "group_audit": first["feature_grades"]["group_audit"],
    }
    assert first["freshness_limitations"] == []

    rebuild_company_year_quality(2025, 2025)
    second = company_year_quality("00126380", 2025)
    with get_session() as session:
        second_row = session.get(CompanyYearQuality, ("00126380", 2025))
        assert second_row is not None
        second_summary_json = second_row.evidence_summary_json
        second_updated_at = second_row.updated_at

    assert second["input_fingerprint"] == first["input_fingerprint"]
    assert second_summary_json == first_summary_json
    assert second_updated_at >= first_updated_at

    with get_session() as session:
        session.add(
            Auditor(
                corp_code="00126380",
                bsns_year=2025,
                fs_div="CFS",
                auditor_nm="감사법인",
                audit_opinion="적정",
            )
        )
    rebuild_company_year_quality(2025, 2025)
    changed = company_year_quality("00126380", 2025)

    assert first["statuses"]["auditor"] == "missing"
    assert changed["statuses"]["auditor"] == "available"
    assert changed["input_fingerprint"] != first["input_fingerprint"]


def test_company_year_quality_reads_legacy_blank_fingerprint_as_limited(
    temp_engine,
):
    from kreports.quality.company_year import company_year_quality

    with get_session() as session:
        session.add(
            CompanyYearQuality(
                corp_code="00126380",
                bsns_year=2025,
                market="KOSPI",
                financial_core_status="available",
                auditor_status="available",
                audit_fee_status="available",
                policy_status="full_body",
                kam_status="full_body",
                audit_procedure_status="available",
                group_audit_status="missing",
                investor_grade="A",
                auditor_grade="A",
                group_audit_grade="D",
                blockers_json="[]",
                quality_version="v1",
                input_fingerprint="",
                evidence_summary_json="{}",
                updated_at=datetime.now(UTC),
            )
        )

    quality = company_year_quality("00126380", 2025)

    assert quality["input_fingerprint"] is None
    assert quality["evidence_summary"] == {}
    assert quality["freshness_limitations"] == [
        "품질 원장이 입력 증거 fingerprint 도입 이전 상태입니다."
    ]


@pytest.mark.parametrize(
    ("fingerprint", "summary_json", "expected_limitation"),
    [
        (
            "a" * 64,
            "{not-json",
            "품질 원장의 증거 요약이 유효한 JSON 객체가 아닙니다.",
        ),
        (
            "a" * 64,
            "[]",
            "품질 원장의 증거 요약이 유효한 JSON 객체가 아닙니다.",
        ),
        (
            "a" * 64,
            '{"statuses": {}}',
            "품질 원장의 증거 요약이 유효한 JSON 객체가 아닙니다.",
        ),
    ],
)
def test_company_year_quality_rejects_unverified_freshness_metadata(
    temp_engine,
    fingerprint,
    summary_json,
    expected_limitation,
):
    from kreports.quality.company_year import company_year_quality

    with get_session() as session:
        session.add(
            CompanyYearQuality(
                corp_code="00126380",
                bsns_year=2025,
                market="KOSPI",
                financial_core_status="available",
                auditor_status="available",
                audit_fee_status="available",
                policy_status="full_body",
                kam_status="full_body",
                audit_procedure_status="available",
                group_audit_status="missing",
                investor_grade="A",
                auditor_grade="A",
                group_audit_grade="D",
                blockers_json="[]",
                quality_version="v1",
                input_fingerprint=fingerprint,
                evidence_summary_json=summary_json,
                updated_at=datetime.now(UTC),
            )
        )

    quality = company_year_quality("00126380", 2025)

    assert quality["input_fingerprint"] == fingerprint
    assert quality["evidence_summary"] == {}
    assert quality["freshness_limitations"] == [expected_limitation]


def _raw_summary_fingerprint(summary: dict[str, object]) -> str:
    payload = json.dumps(
        summary,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("mismatch", ["extra_timestamp", "row_grade"])
def test_company_year_quality_rejects_self_consistent_noncanonical_summary(
    temp_engine,
    mismatch,
):
    from kreports.quality.company_year import company_year_quality

    summary = build_quality_evidence_summary(
        statuses={
            "financial_core": "available",
            "auditor": "available",
            "audit_fee": "available",
            "policy": "full_body",
            "kam": "full_body",
            "audit_procedure": "available",
            "group_audit": "missing",
        },
        grades={
            "investor_core": "B" if mismatch == "row_grade" else "A",
            "auditor_full": "A",
            "group_audit": "D",
        },
        blockers=(),
        quality_version="v1",
    )
    if mismatch == "extra_timestamp":
        summary["timestamp"] = "2026-07-29T00:00:00Z"
    fingerprint = _raw_summary_fingerprint(summary)
    with get_session() as session:
        session.add(
            CompanyYearQuality(
                corp_code="00126380",
                bsns_year=2025,
                market="KOSPI",
                financial_core_status="available",
                auditor_status="available",
                audit_fee_status="available",
                policy_status="full_body",
                kam_status="full_body",
                audit_procedure_status="available",
                group_audit_status="missing",
                investor_grade="A",
                auditor_grade="A",
                group_audit_grade="D",
                blockers_json="[]",
                quality_version="v1",
                input_fingerprint=fingerprint,
                evidence_summary_json=json.dumps(summary),
                updated_at=datetime.now(UTC),
            )
        )

    quality = company_year_quality("00126380", 2025)

    assert quality["evidence_summary"] == {}
    assert quality["freshness_limitations"]


def test_three_core_years_are_investor_b_but_transport_error_is_d(
    temp_engine,
):
    from kreports.quality.company_year import (
        company_year_quality,
        rebuild_company_year_quality,
    )

    _seed_source_backed_core_financial_years("00126380", range(2023, 2026))
    _seed_source_backed_core_financial_years("00999999", range(2023, 2026))
    with get_session() as session:
        session.add(
            FetchLog(
                task_type="financial",
                corp_code="00999999",
                year=2025,
                status="error",
                error_msg="transport failed",
            )
        )

    rebuild_company_year_quality(2025, 2025)

    grade_b = company_year_quality("00126380", 2025)
    grade_d = company_year_quality("00999999", 2025)
    assert grade_b["feature_grades"]["investor_core"] == "B"
    assert grade_d["statuses"]["financial_core"] == "error"
    assert grade_d["feature_grades"]["investor_core"] == "D"
    assert "financial_core_error" in grade_d["blockers"]


def test_rebuild_requires_collector_mode_without_mutating_rows(
    temp_engine,
    monkeypatch,
):
    from kreports.db.models import CompanyYearQuality
    from kreports.quality.company_year import rebuild_company_year_quality

    _seed_core_financial_years("00126380", range(2025, 2026))
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    with pytest.raises(RuntimeError, match="requires collector mode"):
        rebuild_company_year_quality(2025, 2025)

    with get_session() as session:
        assert session.query(CompanyYearQuality).count() == 0


def test_explicit_financial_no_data_is_not_available_not_missing(temp_engine):
    from kreports.quality.company_year import (
        company_year_quality,
        rebuild_company_year_quality,
    )

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
            FetchLog(
                task_type="financial",
                corp_code="00126380",
                year=2025,
                status="no_data",
            )
        )

    rebuild_company_year_quality(2025, 2025)
    quality = company_year_quality("00126380", 2025)

    assert quality["statuses"]["financial_core"] == "not_available"


def test_actual_audit_report_section_error_is_preserved(temp_engine):
    from kreports.quality.company_year import (
        company_year_quality,
        rebuild_company_year_quality,
    )

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
            FetchLog(
                task_type="audit_report_section",
                corp_code="00126380",
                year=2025,
                status="error",
                error_msg="document fetch failed",
            )
        )

    rebuild_company_year_quality(2025, 2025)
    quality = company_year_quality("00126380", 2025)

    assert quality["statuses"]["kam"] == "error"
    assert quality["statuses"]["audit_procedure"] == "error"


def test_kam_and_procedure_use_only_deterministic_current_receipt(
    temp_engine,
):
    from kreports.quality.company_year import (
        company_year_quality,
        rebuild_company_year_quality,
    )

    with get_session() as session:
        session.add(
            Company(
                corp_code="00126380",
                stock_code="005930",
                corp_name="삼성전자",
                market="KOSPI",
            )
        )
        session.add_all(
            [
                ReportSection(
                    rcept_no="20240318000001",
                    corp_code="00126380",
                    bsns_year=2025,
                    source_type="audit_report",
                    section_key="kam",
                    body_text=(
                        "핵심감사사항에 대해 다음 감사절차를 "
                        "수행하였습니다. " * 10
                    ),
                ),
                ReportSection(
                    rcept_no="20250318000001",
                    corp_code="00126380",
                    bsns_year=2025,
                    source_type="audit_report",
                    section_key="kam",
                    body_text="핵심감사사항이 존재합니다.",
                ),
            ]
        )
        session.add_all(
            [
                AuditProcedureItem(
                    rcept_no="20240318000001",
                    corp_code="00126380",
                    bsns_year=2025,
                    source_type="audit_report",
                    procedure_type="inspection",
                    procedure_text="과거 감사절차",
                ),
                AuditProcedureItem(
                    rcept_no="20250318000001",
                    corp_code="00126380",
                    bsns_year=2025,
                    source_type="audit_report",
                    procedure_type="inspection",
                    procedure_text="요약문에서 잘못 연결된 절차",
                ),
            ]
        )

    rebuild_company_year_quality(2025, 2025)
    quality = company_year_quality("00126380", 2025)

    assert quality["statuses"]["kam"] == "summary_only"
    assert quality["statuses"]["audit_procedure"] == "missing"


@pytest.mark.parametrize(
    "scenario,facts",
    [
        (
            "below_10_percent",
            [
                ("00126380", "CFS", "assets", 1_000),
                ("00126380", "CFS", "revenue", 1_000),
                ("00999999", "CFS", "assets", 50),
                ("00999999", "CFS", "revenue", 50),
            ],
        ),
        (
            "above_10_percent",
            [
                ("00126380", "CFS", "assets", 1_000),
                ("00126380", "CFS", "revenue", 1_000),
                ("00999999", "CFS", "assets", 200),
                ("00999999", "CFS", "revenue", 200),
            ],
        ),
        (
            "missing_component_revenue",
            [
                ("00126380", "CFS", "assets", 1_000),
                ("00126380", "CFS", "revenue", 1_000),
                ("00999999", "CFS", "assets", 200),
            ],
        ),
        (
            "mixed_fs_divisions",
            [
                ("00126380", "CFS", "assets", 1_000),
                ("00126380", "OFS", "revenue", 1_000),
                ("00999999", "CFS", "assets", 200),
                ("00999999", "OFS", "revenue", 200),
            ],
        ),
    ],
)
def test_group_audit_never_infers_qsc_from_amounts(
    temp_engine,
    scenario,
    facts,
):
    del scenario
    from kreports.quality.company_year import (
        company_year_quality,
        rebuild_company_year_quality,
    )

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
            BusinessAffiliateAuditor(
                parent_corp_code="00126380",
                parent_rcept_no="20250318000007",
                bsns_year=2025,
                name="종속회사",
                relation="종속기업",
                ownership_pct=80.0,
                assets="200",
                corp_code="00999999",
            )
        )
        session.add_all(
            [
                FinancialFactCompact(
                    corp_code=corp_code,
                    bsns_year=2025,
                    fs_div=fs_div,
                    metric_key=metric_key,
                    metric_name=metric_key,
                    amount=amount,
                )
                for corp_code, fs_div, metric_key, amount in facts
            ]
        )

    rebuild_company_year_quality(2025, 2025)
    quality = company_year_quality("00126380", 2025)

    assert quality["statuses"]["group_audit"] == "partial"
    assert quality["feature_grades"]["group_audit"] == "D"


def test_audit_fee_quality_consumes_real_per_company_outcomes(temp_engine):
    from kreports.quality.company_year import (
        company_year_quality,
        rebuild_company_year_quality,
    )

    with get_session() as session:
        session.add_all(
            [
                Company(
                    corp_code="00126380",
                    stock_code="005930",
                    corp_name="no-data",
                    market="KOSPI",
                ),
                Company(
                    corp_code="00999998",
                    stock_code="999998",
                    corp_name="error",
                    market="KOSPI",
                ),
                Company(
                    corp_code="00999999",
                    stock_code="999999",
                    corp_name="no-log",
                    market="KOSPI",
                ),
            ]
        )
        session.add_all(
            [
                FetchLog(
                    task_type="audit_fee",
                    corp_code="00126380",
                    year=2025,
                    status="no_data",
                ),
                FetchLog(
                    task_type="audit_fee",
                    corp_code="00999998",
                    year=2025,
                    status="error",
                    error_msg="transport failed",
                ),
            ]
        )

    rebuild_company_year_quality(2025, 2025)

    assert company_year_quality("00126380", 2025)["statuses"][
        "audit_fee"
    ] == "partial"
    assert company_year_quality("00999998", 2025)["statuses"][
        "audit_fee"
    ] == "error"
    assert company_year_quality("00999999", 2025)["statuses"][
        "audit_fee"
    ] == "missing"


def test_audit_fee_later_success_timestamp_recovers_older_error(
    temp_engine,
):
    from kreports.quality.company_year import (
        company_year_quality,
        rebuild_company_year_quality,
    )

    now = datetime.now(UTC)
    with get_session() as session:
        session.add(
            Company(
                corp_code="00126380",
                stock_code="005930",
                corp_name="recovered",
                market="KOSPI",
            )
        )
        session.add(
            AuditFee(
                corp_code="00126380",
                bsns_year=2025,
                audit_fee_m=100,
                audit_hours=200,
                fetched_at=now,
            )
        )
        session.add_all(
            [
                FetchLog(
                    task_type="audit_fee",
                    corp_code="00126380",
                    year=2025,
                    status="success",
                    fetched_at=now,
                ),
                FetchLog(
                    task_type="audit_fee",
                    corp_code="00126380",
                    year=2025,
                    status="error",
                    error_msg="older transport failure",
                    fetched_at=now - timedelta(minutes=1),
                ),
            ]
        )

    rebuild_company_year_quality(2025, 2025)

    assert company_year_quality("00126380", 2025)["statuses"][
        "audit_fee"
    ] == "available"


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (("error", "success"), "available"),
        (("success", "error"), "error"),
        (("error", "no_data"), "partial"),
    ],
)
def test_audit_fee_equal_timestamp_uses_later_fetch_log_id(
    temp_engine,
    statuses,
    expected,
):
    from kreports.quality.company_year import (
        company_year_quality,
        rebuild_company_year_quality,
    )

    now = datetime.now(UTC)
    with get_session() as session:
        session.add(
            Company(
                corp_code="00126380",
                stock_code="005930",
                corp_name="same-timestamp",
                market="KOSPI",
            )
        )
        if "success" in statuses:
            session.add(
                AuditFee(
                    corp_code="00126380",
                    bsns_year=2025,
                    audit_fee_m=100,
                    audit_hours=200,
                    fetched_at=now,
                )
            )
        session.add_all(
            [
                FetchLog(
                    task_type="audit_fee",
                    corp_code="00126380",
                    year=2025,
                    status=status,
                    error_msg=(
                        "transport failure"
                        if status == "error"
                        else None
                    ),
                    fetched_at=now,
                )
                for status in statuses
            ]
        )

    rebuild_company_year_quality(2025, 2025)

    assert company_year_quality("00126380", 2025)["statuses"][
        "audit_fee"
    ] == expected
