from datetime import date
from pathlib import Path
import tempfile

from kreports.analysis.readiness import (
    audit_kam_quality_snapshot,
    auditor_readiness_snapshot,
    auditor_feature_readiness_snapshot,
    backfill_plan,
    kam_repair_targets_snapshot,
    readiness_verdict,
)
from kreports.cli.main import _select_policy_targets
from kreports.db.models import (
    AccountingNoteChapter,
    AccountingPolicyItem,
    AuditProcedureItem,
    Company,
    Disclosure,
    EvidenceDocument,
    ReportSection,
    SourceDocument,
)
from tests.historical_membership_fixture import verified_membership


def test_backfill_preflight_rejects_low_free_disk_space():
    import subprocess

    proc = subprocess.run(
        [
            "bash",
            "-c",
            (
                "source scripts/backfill_preflight.sh; "
                "KREPORTS_BACKFILL_FREE_KB_OVERRIDE=42 "
                "KREPORTS_MIN_FREE_KB=100 "
                "require_backfill_free_space 'test backfill'"
            ),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 70
    assert "test backfill requires at least 100 KB free" in proc.stderr
    assert "found 42 KB" in proc.stderr


def test_complete_and_limit_aware_backfills_run_disk_preflight_before_work():
    complete_script = open("scripts/run_complete_dataset_backfill.sh", encoding="utf-8").read()
    wrapper_script = open("scripts/dart_limit_aware_backfill.sh", encoding="utf-8").read()

    assert "source scripts/backfill_preflight.sh" in complete_script
    assert "require_backfill_free_space \"complete dataset backfill\"" in complete_script
    assert complete_script.index("require_backfill_free_space") < complete_script.index(
        'log "complete dataset backfill started"'
    )

    assert "source \"$PROJECT_DIR/scripts/backfill_preflight.sh\"" in wrapper_script
    assert "require_backfill_free_space \"DART backfill wrapper\"" in wrapper_script
    assert wrapper_script.index("require_backfill_free_space") < wrapper_script.index('log "probe started"')


def test_full_backfill_runs_policies_before_financial_endpoint():
    script = open("scripts/run_full_dataset_backfill.sh", encoding="utf-8").read()

    business_pos = script.index("collect-business-report-sections")
    extractor_pos = script.index("run-document-extractors --source-type business_report")
    policy_pos = script.index("collect-policies --market KOSPI --year \"$year\"")
    financial_pos = script.index("collect-all --year-from")

    assert business_pos < extractor_pos < policy_pos < financial_pos


def test_complete_backfill_rebuilds_compact_after_financial_failure():
    script = open("scripts/run_complete_dataset_backfill.sh", encoding="utf-8").read()

    financial_pos = script.index('run_api_step "financial facts 2021-2025"')
    compact_pos = script.index('run_step "rebuild compact financial facts 2021-2025"')
    exit_pos = script.rindex('if (( api_exit != 0 ))')

    assert "api_exit=0" in script
    assert "run_api_step()" in script
    assert financial_pos < compact_pos < exit_pos


def test_complete_backfill_runs_local_derived_steps_before_api_failure_exit():
    script = open("scripts/run_complete_dataset_backfill.sh", encoding="utf-8").read()

    compact_pos = script.index('run_step "rebuild compact financial facts 2021-2025"')
    evidence_pos = script.index('run_step "rebuild normalized evidence documents 2021-2025"')
    diagnostics_pos = script.index('run_step "dataset audit"')
    exit_pos = script.rindex('if (( api_exit != 0 ))')

    assert compact_pos < evidence_pos < diagnostics_pos < exit_pos
    assert 'log "complete dataset backfill finished with API failure exit_code=$api_exit"' in script


def test_complete_backfill_limits_document_extractors_to_five_year_window():
    script = open("scripts/run_complete_dataset_backfill.sh", encoding="utf-8").read()

    assert "for year in 2021 2022 2023 2024 2025; do" in script
    assert 'run-document-extractors --year "$year" --source-type business_report' in script
    assert 'run-document-extractors --year "$year" --source-type audit_report' in script
    assert "run-document-extractors --source-type business_report" not in script


def test_complete_backfill_collects_2021_disclosure_list_for_five_year_readiness():
    script = open("scripts/run_complete_dataset_backfill.sh", encoding="utf-8").read()

    assert "disclosure list 2021-2026" in script
    assert "--start-date 20210101" in script
    assert "--start-date 20220101" not in script


def test_complete_backfill_guards_raw_report_expansion_by_default():
    script = open("scripts/run_complete_dataset_backfill.sh", encoding="utf-8").read()

    assert "initial disclosure list skipped" in script
    assert "source scripts/raw_backfill_guard.sh" in script
    assert "require_external_raw_backfill" in script
    guard_pos = script.index('KREPORTS_ENABLE_RAW_BACKFILL:-0')
    gap_pos = script.index('"2023 KOSDAQ"')
    report_pos = script.index('run_api_step "business report sections ${year} ${market}"')
    skip_pos = script.index("raw report section backfill skipped")
    financial_pos = script.index('run_api_step "financial facts 2021-2025"')

    assert gap_pos < guard_pos < report_pos < skip_pos < financial_pos
    assert "only for explicit hot-raw archive operations" in script


def test_legacy_raw_backfill_scripts_require_external_storage_guard():
    guarded_scripts = [
        "scripts/run_full_dataset_backfill.sh",
        "scripts/run_document_first_backfill.sh",
        "scripts/run_2023_expansion_backfill.sh",
        "scripts/run_business_report_cache_backfill.sh",
        "scripts/run_source_documents_backfill.sh",
    ]

    for path in guarded_scripts:
        script = open(path, encoding="utf-8").read()
        assert "source scripts/raw_backfill_guard.sh" in script, path
        assert "collect-business-report-sections" in script, path
        assert "require_external_raw_backfill" in script, path


def test_raw_backfill_guard_rejects_inline_storage_by_default():
    script = open("scripts/raw_backfill_guard.sh", encoding="utf-8").read()

    assert "KREPORTS_ENABLE_RAW_BACKFILL=1" in script
    assert "RAW_STORAGE_BACKEND=file or gcs" in script
    assert "RAW_STORAGE_KEEP_INLINE=false" in script


def test_source_documents_backfill_avoids_financial_endpoint():
    script = open("scripts/run_source_documents_backfill.sh", encoding="utf-8").read()

    assert "collect-business-report-sections" in script
    assert "run-document-extractors --source-type business_report" in script
    assert "collect-all --year-from" not in script
    assert "collect-audit-fees" not in script


def test_derived_dataset_backfill_does_not_collect_more_raw_documents():
    script = open("scripts/run_derived_dataset_backfill.sh", encoding="utf-8").read()

    assert "collect-business-report-sections" not in script
    assert 'run-document-extractors --year "$year" --source-type business_report' in script
    assert 'run-document-extractors --year "$year" --source-type audit_report' in script
    assert "trim-evidence-documents --year-from 2024 --year-to 2025" in script
    assert "rebuild-evidence-documents --year-from 2024 --year-to 2025" in script
    assert "--max-text-chars 12000" in script
    assert "collect-all --year-from 2021 --year-to 2025" in script
    assert "collect-audit-fees" in script


def test_limit_aware_backfill_defaults_to_complete_dataset_script():
    script = open("scripts/dart_limit_aware_backfill.sh", encoding="utf-8").read()

    assert "KREPORTS_BACKFILL_SCRIPT" in script
    assert "scripts/run_complete_dataset_backfill.sh" in script


def test_readiness_verdict_passes_core_thresholds():
    snapshot = {
        "markets": {
            "KOSPI": {
                "listed": 838,
                "financial_any_2025": 835,
                "business_report_2025": 835,
                "audit_report_2025": 835,
                "auditor_2025": 835,
                "audit_fee_2025": 0,
                "disclosure_recent": 838,
            },
            "KOSDAQ": {
                "listed": 1817,
                "financial_any_2025": 1808,
                "business_report_2025": 1809,
                "audit_report_2025": 1809,
                "auditor_2025": 1809,
                "audit_fee_2025": 0,
                "disclosure_recent": 1817,
            },
        },
        "policy_corps": 7,
        "audit_fee_2025_corps": 0,
    }
    out = readiness_verdict(snapshot)
    assert out["verdict"] == "conditional_pass"
    assert "accounting_policy" in out["recommended_gaps"]
    assert "audit_fee" in out["recommended_gaps"]


def test_readiness_verdict_fails_when_financial_coverage_low():
    snapshot = {
        "markets": {
            "KOSPI": {
                "listed": 838,
                "financial_any_2025": 400,
                "business_report_2025": 835,
                "audit_report_2025": 835,
                "auditor_2025": 835,
                "disclosure_recent": 838,
            },
            "KOSDAQ": {
                "listed": 1817,
                "financial_any_2025": 1808,
                "business_report_2025": 1809,
                "audit_report_2025": 1809,
                "auditor_2025": 1809,
                "disclosure_recent": 1817,
            },
        },
        "policy_corps": 7,
        "audit_fee_2025_corps": 0,
    }
    out = readiness_verdict(snapshot)
    assert out["verdict"] == "fail"
    assert "financial_any_2025" in out["required_gaps"]


def test_readiness_verdict_fails_when_five_year_core_coverage_low():
    snapshot = {
        "required_years": [2021, 2022, 2023, 2024, 2025],
        "markets": {
            "KOSPI": {"listed": 100, "financial_any_2025": 99, "business_report_2025": 99, "audit_report_2025": 99, "auditor_2025": 99, "disclosure_recent": 100},
            "KOSDAQ": {"listed": 100, "financial_any_2025": 99, "business_report_2025": 99, "audit_report_2025": 99, "auditor_2025": 99, "disclosure_recent": 100},
        },
        "yearly_markets": {
            y: {
                "KOSPI": {"listed": 100, "financial_any": 99, "business_report": 99, "audit_report": 99, "auditor": 99, "audit_fee": 0},
                "KOSDAQ": {"listed": 100, "financial_any": 99, "business_report": 99, "audit_report": 99, "auditor": 99, "audit_fee": 0},
            }
            for y in [2021, 2022, 2023, 2024, 2025]
        },
        "policy_corps": 120,
        "audit_fee_2025_corps": 0,
    }
    snapshot["yearly_markets"][2022]["KOSDAQ"]["business_report"] = 0

    out = readiness_verdict(snapshot)

    assert out["verdict"] == "fail"
    assert "business_report_2022" in out["required_gaps"]
    assert "audit_fee_2022" not in out["required_gaps"]


def test_backfill_plan_returns_actionable_commands_for_coverage_gaps():
    snapshot = {
        "year": 2025,
        "required_years": [2021, 2022, 2023, 2024, 2025],
        "markets": {
            "KOSPI": {"listed": 100, "financial_any_2025": 99, "business_report_2025": 99, "audit_report_2025": 99, "auditor_2025": 99, "disclosure_recent": 100},
            "KOSDAQ": {"listed": 100, "financial_any_2025": 99, "business_report_2025": 99, "audit_report_2025": 99, "auditor_2025": 99, "disclosure_recent": 100},
        },
        "yearly_markets": {
            y: {
                "KOSPI": {"listed": 100, "financial_any": 99, "business_report": 99, "audit_report": 99, "auditor": 99, "audit_fee": 0},
                "KOSDAQ": {"listed": 100, "financial_any": 99, "business_report": 99, "audit_report": 99, "auditor": 99, "audit_fee": 0},
            }
            for y in [2021, 2022, 2023, 2024, 2025]
        },
        "policy_corps": 7,
        "audit_fee_2025_corps": 0,
    }
    snapshot["yearly_markets"][2022]["KOSDAQ"]["financial_any"] = 20
    snapshot["yearly_markets"][2021]["KOSPI"]["audit_report"] = 0

    plan = backfill_plan(snapshot)

    assert plan["required_commands"] == [
        ".venv/bin/kreports collect-all --year-from 2022 --year-to 2022",
        ".venv/bin/kreports collect-disclosures --market KOSPI --start-date 20220101 --end-date 20221231",
        ".venv/bin/kreports collect-auditors",
    ]
    assert ".venv/bin/kreports collect-policies --market KOSPI --year 2025 --limit 100" in plan["recommended_commands"]
    assert ".venv/bin/kreports collect-audit-fees --market KOSPI --year-from 2021 --year-to 2025" in plan["recommended_commands"]


def test_select_policy_targets_by_market_and_limit(temp_engine):
    from kreports.db.engine import get_session

    with get_session() as session:
        for idx in range(12):
            corp_code = f"{idx:08d}"
            stock_code = f"{idx:06d}"
            session.add(
                Company(
                    corp_code=corp_code,
                    stock_code=stock_code,
                    corp_name=f"C{idx}",
                    market="KOSPI",
                    induty_code="264",
                )
            )
            membership, _raw_path = verified_membership(
                root=(
                    Path(tempfile.mkdtemp(prefix="kreports-auditor-membership-test-"))
                ),
                corp_code=corp_code,
                stock_code=stock_code,
                year=2025,
                market="KOSPI",
            )
            session.add(membership)

    targets = _select_policy_targets(year=2025, fs_div="CFS", market="KOSPI", limit=10, missing_only=False)
    assert len(targets) == 10
    assert all(len(t[0]) == 8 and t[1] == 2025 and t[2] == "CFS" for t in targets)


def test_auditor_feature_readiness_snapshot_counts_feature_layers(temp_engine):
    from kreports.db.engine import get_session

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
        session.add_all(
            [
                SourceDocument(
                    rcept_no="20260301000001",
                    corp_code="00126380",
                    bsns_year=2025,
                    source_type="business_report",
                    report_nm="사업보고서",
                    content_type="html",
                    raw_content="사업보고서 원문",
                    doc_hash="brhash",
                ),
                SourceDocument(
                    rcept_no="20260301000002",
                    corp_code="00126380",
                    bsns_year=2025,
                    source_type="audit_report",
                    report_nm="감사보고서",
                    content_type="xml",
                    raw_content="감사보고서 원문",
                    doc_hash="arhash",
                ),
                ReportSection(
                    rcept_no="20260301000002",
                    corp_code="00126380",
                    bsns_year=2025,
                    source_type="audit_report",
                    section_key="kam",
                    section_title="수익인식",
                    body_text="핵심감사사항으로 결정하였으며 감사절차를 수행하였습니다.",
                    ordinal=0,
                ),
                ReportSection(
                    rcept_no="20260301000002",
                    corp_code="00126380",
                    bsns_year=2025,
                    source_type="audit_report",
                    section_key="emphasis",
                    section_title="강조사항",
                    body_text="계속기업 관련 중요한 불확실성이 있습니다.",
                    ordinal=0,
                ),
                AccountingNoteChapter(
                    corp_code="00126380",
                    bsns_year=2024,
                    fs_div="CFS",
                    rcept_no="20250301000001",
                    source_type="business_report",
                    note_no="2",
                    note_title="재무제표 작성기준",
                    section_type="basis",
                    body="한국채택국제회계기준에 따라 작성되었습니다.",
                ),
                AccountingNoteChapter(
                    corp_code="00126380",
                    bsns_year=2025,
                    fs_div="CFS",
                    rcept_no="20260301000001",
                    source_type="business_report",
                    note_no="2",
                    note_title="재무제표 작성기준",
                    section_type="basis",
                    body="한국채택국제회계기준에 따라 작성되었습니다.",
                ),
                Disclosure(
                    rcept_no="20250301000001",
                    corp_code="00126380",
                    corp_name="감사준비회사",
                    disc_date=date(2025, 3, 1),
                    disc_type="A",
                    report_nm="사업보고서 (2024.12)",
                ),
                Disclosure(
                    rcept_no="20260301000001",
                    corp_code="00126380",
                    corp_name="감사준비회사",
                    disc_date=date(2026, 3, 1),
                    disc_type="A",
                    report_nm="사업보고서 (2025.12)",
                ),
                AccountingPolicyItem(
                    corp_code="00126380",
                    bsns_year=2025,
                    fs_div="CFS",
                    rcept_no="20260301000001",
                    item_key="revenue_recognition",
                    heading="수익인식",
                    body="수익은 수행의무 이행 시 인식합니다.",
                ),
                AuditProcedureItem(
                    rcept_no="20260301000002",
                    corp_code="00126380",
                    bsns_year=2025,
                    source_type="audit_report",
                    kam_topic="revenue",
                    procedure_type="test_of_details",
                    procedure_text="계약서와 세금계산서를 대사하였습니다.",
                    section_ordinal=0,
                    procedure_ordinal=0,
                ),
            ]
        )

    snapshot = auditor_feature_readiness_snapshot(year=2025, market="KOSPI")

    assert snapshot["verdict"] == "pass"
    assert snapshot["counts"]["raw_source_documents"] == 2
    assert snapshot["counts"]["raw_business_documents"] == 1
    assert snapshot["counts"]["raw_audit_documents"] == 1
    assert snapshot["counts"]["kam_sections"] == 1
    assert snapshot["counts"]["audit_report_matters"] == 1
    assert snapshot["counts"]["accounting_note_chapters"] == 1
    assert snapshot["counts"]["accounting_policy_change_chapters"] == 1
    assert snapshot["counts"]["accounting_policy_items"] == 1
    assert snapshot["counts"]["audit_procedure_items"] == 1
    assert snapshot["feature_status"]["accounting_policy_changes"] == "usable"
    assert snapshot["missing_features"] == []


def test_auditor_feature_readiness_recommends_derived_first_backfill(temp_engine):
    snapshot = auditor_feature_readiness_snapshot(year=2025, market="KOSPI")

    joined = "\n".join(snapshot["recommended_next"])
    assert "source_documents backfill" not in joined
    assert "evidence_documents" in joined
    assert "audit_fee" in joined


def test_auditor_feature_readiness_does_not_count_derived_only_docs_as_raw(temp_engine):
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(
            corp_code="00000001",
            stock_code="000001",
            corp_name="파생문서회사",
            market="KOSPI",
            induty_code="264",
        ))
        session.add_all([
            SourceDocument(
                rcept_no="20260311000001",
                corp_code="00000001",
                bsns_year=2025,
                source_type="business_report",
                report_nm="사업보고서",
                content_type="xml",
                raw_content="",
                doc_hash="derived-business",
                storage_status="derived_only",
            ),
            SourceDocument(
                rcept_no="20260311000001_A",
                corp_code="00000001",
                bsns_year=2025,
                source_type="audit_report",
                report_nm="감사보고서",
                content_type="xml",
                raw_content="",
                doc_hash="derived-audit",
                storage_status="derived_only",
            ),
        ])

    snapshot = auditor_feature_readiness_snapshot(year=2025, market="KOSPI")

    assert snapshot["counts"]["raw_source_documents"] == 0
    assert snapshot["counts"]["raw_source_document_companies"] == 0
    assert snapshot["counts"]["raw_business_documents"] == 0
    assert snapshot["counts"]["raw_audit_documents"] == 0
    assert snapshot["counts"]["derived_source_document_placeholders"] == 2
    assert snapshot["counts"]["derived_business_document_placeholders"] == 1
    assert snapshot["counts"]["derived_audit_document_placeholders"] == 1
    assert "derived placeholders" in snapshot["recommended_next"][0]


def test_auditor_feature_readiness_counts_evidence_kam_fallback(temp_engine):
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(
            corp_code="00000001",
            stock_code="000001",
            corp_name="증거KAM",
            market="KOSPI",
            induty_code="264",
        ))
        session.add(EvidenceDocument(
            corp_code="00000001",
            bsns_year=2025,
            source_type="audit_report",
            rcept_no="20260311000001_A",
            dcm_no="A",
            evidence_scope="auditor_view",
            title="2025 audit report evidence",
            normalized_text=(
                "## report_section/kam: 수익인식\n"
                "핵심감사사항으로 선정한 이유는 중요한 왜곡표시위험 때문입니다. "
                "우리는 내부통제 이해 및 평가와 문서검사 감사절차를 수행하였습니다.\n"
                "## report_section/other_matter: 기타사항\n"
                "비교표시 재무제표는 전임감사인이 감사하였습니다."
            ),
            text_hash="x",
            text_length=250,
            source_count=2,
        ))

    snapshot = auditor_feature_readiness_snapshot(year=2025, market="KOSPI")

    assert snapshot["source_basis"]["kam_sections"] == "evidence_documents"
    assert snapshot["counts"]["kam_sections"] == 1
    assert snapshot["counts"]["kam_companies"] == 1
    assert snapshot["counts"]["kam_reason_hints"] == 1
    assert snapshot["counts"]["kam_procedure_hints"] == 1
    assert snapshot["counts"]["audit_report_matters"] == 1
    assert "kam_sections" not in snapshot["missing_features"]


def test_auditor_feature_readiness_marks_low_coverage_as_degraded(temp_engine):
    from kreports.db.engine import get_session

    with get_session() as session:
        for idx in range(10):
            session.add(Company(
                corp_code=f"{idx:08d}",
                stock_code=f"{idx:06d}",
                corp_name=f"상장사{idx}",
                market="KOSPI",
                induty_code="264",
            ))
        session.add(EvidenceDocument(
            corp_code="00000000",
            bsns_year=2025,
            source_type="audit_report",
            rcept_no="20260311000000_A",
            dcm_no="A",
            evidence_scope="auditor_view",
            title="2025 audit report evidence",
            normalized_text=(
                "## report_section/kam: 수익인식\n"
                "핵심감사사항으로 선정한 이유는 중요한 왜곡표시위험 때문입니다. "
                "우리는 문서검사 감사절차를 수행하였습니다."
            ),
            text_hash="x",
            text_length=180,
            source_count=1,
        ))
        session.add(AuditProcedureItem(
            rcept_no="20260311000000_A",
            corp_code="00000000",
            bsns_year=2025,
            source_type="audit_report",
            kam_topic="revenue",
            procedure_type="substantive_test",
            procedure_text="문서검사 감사절차를 수행하였습니다.",
            section_ordinal=0,
            procedure_ordinal=0,
        ))

    snapshot = auditor_feature_readiness_snapshot(year=2025, market="KOSPI")

    assert snapshot["verdict"] == "conditional"
    assert snapshot["feature_status"]["kam_sections"] == "degraded"
    assert snapshot["feature_status"]["audit_procedure_items"] == "degraded"
    assert "kam_sections" in snapshot["degraded_features"]
    assert "audit_procedure_items" in snapshot["degraded_features"]


def test_audit_kam_quality_snapshot_identifies_repair_candidates(temp_engine):
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add_all([
            Company(
                corp_code="00000001",
                stock_code="000001",
                corp_name="짧은KAM",
                market="KOSPI",
                induty_code="264",
            ),
            Company(
                corp_code="00000002",
                stock_code="000002",
                corp_name="좋은KAM",
                market="KOSPI",
                induty_code="264",
            ),
        ])
        session.add_all([
            ReportSection(
                rcept_no="20260311000001_A",
                corp_code="00000001",
                bsns_year=2025,
                source_type="audit_report",
                section_key="kam",
                section_title="핵심감사사항",
                body_text="핵심감사사항은 우리의 판단에 따라 당기 감사에서 유의적인 사항입니다.",
                body_length=40,
                ordinal=0,
            ),
            ReportSection(
                rcept_no="20260311000002_A",
                corp_code="00000002",
                bsns_year=2025,
                source_type="audit_report",
                section_key="kam",
                section_title="수익인식",
                body_text=(
                    "수익인식은 핵심감사사항입니다. 핵심감사사항으로 선정한 이유는 거래조건 판단과 "
                    "중요한 왜곡표시위험 때문입니다. 우리는 매출 관련 내부통제 이해 및 평가와 "
                    "표본 문서검사 감사절차를 수행하였습니다."
                ),
                body_length=120,
                ordinal=0,
            ),
        ])

    snapshot = audit_kam_quality_snapshot(year=2025, market="KOSPI", min_body_length=80, limit=10)

    assert snapshot["verdict"] == "fail"
    assert snapshot["counts"]["kam_sections"] == 2
    assert snapshot["counts"]["short_kam_sections"] == 1
    assert snapshot["counts"]["reason_hints"] == 1
    assert snapshot["counts"]["procedure_hints"] == 1
    assert snapshot["rates"]["reason_hint_coverage"] == 50.0
    assert snapshot["repair_candidates"][0]["corp_name"] == "짧은KAM"
    assert "short_body" in snapshot["repair_candidates"][0]["gap_reasons"]


def test_audit_kam_quality_snapshot_falls_back_to_evidence_documents(temp_engine):
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(
            corp_code="00000001",
            stock_code="000001",
            corp_name="증거KAM",
            market="KOSPI",
            induty_code="264",
        ))
        session.add(EvidenceDocument(
            corp_code="00000001",
            bsns_year=2025,
            source_type="audit_report",
            rcept_no="20260311000001_A",
            dcm_no="A",
            evidence_scope="auditor_view",
            title="2025 audit report evidence",
            normalized_text=(
                "# Evidence document\n"
                "## report_section/kam: 핵심감사사항\n"
                "핵심감사사항으로 결정한 이유는 중요한 왜곡표시위험 때문입니다. "
                "우리는 내부통제 이해 및 평가와 문서검사 감사절차를 수행하였습니다."
            ),
            text_hash="x",
            text_length=180,
            source_count=1,
        ))

    snapshot = audit_kam_quality_snapshot(year=2025, market="KOSPI", min_body_length=80, limit=10)

    assert snapshot["source_basis"] == "evidence_documents"
    assert snapshot["counts"]["kam_sections"] == 1
    assert snapshot["counts"]["reason_hints"] == 1
    assert snapshot["counts"]["procedure_hints"] == 1


def test_audit_kam_quality_counts_company_year_procedure_index_for_evidence(temp_engine):
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(
            corp_code="00000001",
            stock_code="000001",
            corp_name="절차색인회사",
            market="KOSPI",
            induty_code="264",
        ))
        session.add(EvidenceDocument(
            corp_code="00000001",
            bsns_year=2025,
            source_type="audit_report",
            rcept_no="20260311000001_A",
            dcm_no="A",
            evidence_scope="auditor_view",
            title="2025 audit report evidence",
            normalized_text=(
                "## report_section/kam: 수익인식\n"
                "핵심감사사항으로 선정한 이유는 중요한 왜곡표시위험 때문입니다. "
                "우리는 내부통제 이해 및 평가와 문서검사 감사절차를 수행하였습니다."
            ),
            text_hash="x",
            text_length=180,
            source_count=1,
        ))
        session.add(AuditProcedureItem(
            rcept_no="20260311000001_B",
            corp_code="00000001",
            bsns_year=2025,
            source_type="audit_report",
            kam_topic="revenue",
            procedure_type="substantive_test",
            procedure_text="문서검사 감사절차를 수행하였습니다.",
            section_ordinal=0,
            procedure_ordinal=0,
        ))

    snapshot = audit_kam_quality_snapshot(year=2025, market="KOSPI", min_body_length=80, limit=10)

    assert snapshot["source_basis"] == "evidence_documents"
    assert snapshot["counts"]["indexed_procedure_sections"] == 1
    assert snapshot["rates"]["indexed_procedure_coverage"] == 100.0


def test_audit_kam_quality_excludes_explicit_no_kam_reports_from_repair_denominator(temp_engine):
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(
            corp_code="00000001",
            stock_code="000001",
            corp_name="무KAM회사",
            market="KOSPI",
            induty_code="64992",
        ))
        session.add(EvidenceDocument(
            corp_code="00000001",
            bsns_year=2025,
            source_type="audit_report",
            rcept_no="20260311000001_A",
            dcm_no="A",
            evidence_scope="auditor_view",
            title="2025 audit report evidence",
            normalized_text=(
                "## report_section/kam: 핵심감사사항\n"
                "우리는 감사보고서에 보고해야 할 핵심감사사항이 없다고 결정하였습니다.\n"
                "## report_section/management_responsibility: 재무제표에 대한 경영진\n"
                "경영진은 재무제표 작성 책임이 있습니다."
            ),
            text_hash="x",
            text_length=180,
            source_count=1,
        ))

    snapshot = audit_kam_quality_snapshot(year=2025, market="KOSPI", min_body_length=80, limit=10)

    assert snapshot["counts"]["kam_sections"] == 0
    assert snapshot["counts"]["no_kam_sections"] == 1
    assert snapshot["repair_candidates"] == []


def test_kam_repair_targets_normalizes_original_disclosure_receipts(temp_engine):
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add_all([
            Company(
                corp_code="00000001",
                stock_code="000001",
                corp_name="재파싱대상",
                market="KOSPI",
                induty_code="264",
            ),
            Company(
                corp_code="00000002",
                stock_code="000002",
                corp_name="색인만대상",
                market="KOSPI",
                induty_code="264",
            ),
        ])
        session.add_all([
            EvidenceDocument(
                corp_code="00000001",
                bsns_year=2025,
                source_type="audit_report",
                rcept_no="20260311000001_20260311000001_00761_xml",
                dcm_no="20260311000001_00761",
                evidence_scope="auditor_view",
                title="2025 audit report evidence",
                normalized_text=(
                    "## report_section/kam: 핵심감사사항\n"
                    "핵심감사사항은 유의적인 사항입니다. 감사절차를 수행하였습니다."
                ),
                text_hash="x",
                text_length=90,
                source_count=1,
            ),
            EvidenceDocument(
                corp_code="00000002",
                bsns_year=2025,
                source_type="audit_report",
                rcept_no="20260311000002_20260311000002_00761_xml",
                dcm_no="20260311000002_00761",
                evidence_scope="auditor_view",
                title="2025 audit report evidence",
                normalized_text=(
                    "## report_section/kam: 수익인식\n"
                    "핵심감사사항으로 선정한 이유는 중요한 왜곡표시위험 때문입니다. "
                    "우리는 문서검사 감사절차를 수행하였습니다. "
                    "관련 내부통제 이해와 평가, 계약 조건 검토 및 기간귀속 테스트를 수행하였습니다."
                ),
                text_hash="y",
                text_length=180,
                source_count=1,
            ),
        ])

    targets = kam_repair_targets_snapshot(
        year=2025,
        market="KOSPI",
        min_body_length=120,
        limit=10,
    )

    assert targets["total_candidates"] == 1
    assert targets["targets"][0]["source_rcept_no"] == "20260311000001"
    assert targets["targets"][0]["rcept_no"] == "20260311000001_20260311000001_00761_xml"
    assert "missing_indexed_procedures" in targets["excluded_gap_reasons"]


def test_evidence_kam_quality_ignores_generic_auditor_responsibility_procedures(temp_engine):
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(
            corp_code="00000001",
            stock_code="000001",
            corp_name="절차오인방지",
            market="KOSPI",
            induty_code="264",
        ))
        session.add(EvidenceDocument(
            corp_code="00000001",
            bsns_year=2025,
            source_type="audit_report",
            rcept_no="20260311000001_20260311000001_00761_xml",
            dcm_no="20260311000001_00761",
            evidence_scope="auditor_view",
            title="2025 audit report evidence",
            normalized_text=(
                "## report_section/kam: 핵심감사사항\n"
                "핵심감사사항은 우리의 전문가적 판단에 따라 당기 감사에서 가장 유의적인 사항입니다.\n"
                "## report_section/auditor_responsibility: 감사인의 책임\n"
                "우리는 중요왜곡표시위험에 대응하는 감사절차를 설계하고 수행합니다."
            ),
            text_hash="x",
            text_length=210,
            source_count=2,
        ))

    quality = audit_kam_quality_snapshot(year=2025, market="KOSPI", min_body_length=10, limit=10)

    assert quality["counts"]["kam_sections"] == 1
    assert quality["counts"]["procedure_hints"] == 0
    assert quality["repair_candidates"][0]["gap_reasons"] == [
        "missing_reason_hint",
        "missing_procedure_hint",
        "missing_indexed_procedures",
    ]


def test_repair_kam_sections_executes_only_normalized_targets(temp_engine, monkeypatch):
    from kreports.collector import report_document_collector
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(
            corp_code="00000001",
            stock_code="000001",
            corp_name="재파싱대상",
            market="KOSPI",
            induty_code="264",
        ))
        session.add(EvidenceDocument(
            corp_code="00000001",
            bsns_year=2025,
            source_type="audit_report",
            rcept_no="20260311000001_20260311000001_00761_xml",
            dcm_no="20260311000001_00761",
            evidence_scope="auditor_view",
            title="2025 audit report evidence",
            normalized_text=(
                "## report_section/kam: 핵심감사사항\n"
                "핵심감사사항은 유의적인 사항입니다. 감사절차를 수행하였습니다."
            ),
            text_hash="x",
            text_length=90,
            source_count=1,
        ))

    called = []

    def fake_collect(rcept_no):
        called.append(rcept_no)
        return {"ok": 1, "sections": 7}

    monkeypatch.setattr(
        report_document_collector,
        "collect_report_sections_for_disclosure",
        fake_collect,
    )

    result = report_document_collector.repair_kam_sections(
        year=2025,
        market="KOSPI",
        min_body_length=120,
        limit=10,
        dry_run=False,
    )

    assert called == ["20260311000001"]
    assert result["ok"] == 1
    assert result["sections"] == 7


def test_auditor_readiness_counts_cached_source_documents_not_submission_window(temp_engine):
    from kreports.db.engine import get_session

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
        session.add_all(
            [
                SourceDocument(
                    rcept_no="20260301000001",
                    corp_code="00126380",
                    bsns_year=2025,
                    source_type="business_report",
                    report_nm="사업보고서",
                    content_type="xml",
                    raw_content="",
                    doc_hash="brhash",
                    storage_status="derived_only",
                ),
                SourceDocument(
                    rcept_no="20260301000002",
                    corp_code="00126380",
                    bsns_year=2025,
                    source_type="audit_report",
                    report_nm="감사보고서",
                    content_type="xml",
                    raw_content="",
                    doc_hash="arhash",
                    storage_status="derived_only",
                ),
            ]
        )

    snapshot = auditor_readiness_snapshot(year=2025, years_back=1)

    kospi = snapshot["markets"]["KOSPI"]
    yearly = snapshot["yearly_markets"][2025]["KOSPI"]
    assert kospi["business_report_2025"] == 1
    assert kospi["audit_report_2025"] == 1
    assert yearly["business_report"] == 1
    assert yearly["audit_report"] == 1
