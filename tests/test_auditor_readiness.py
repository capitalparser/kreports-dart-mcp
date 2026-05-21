from kreports.analysis.readiness import (
    auditor_feature_readiness_snapshot,
    backfill_plan,
    readiness_verdict,
)
from kreports.cli.main import _select_policy_targets
from kreports.db.models import (
    AccountingNoteChapter,
    AccountingPolicyItem,
    AuditProcedureItem,
    Company,
    ReportSection,
    SourceDocument,
)


def test_full_backfill_runs_policies_before_financial_endpoint():
    script = open("scripts/run_full_dataset_backfill.sh", encoding="utf-8").read()

    business_pos = script.index("collect-business-report-sections")
    extractor_pos = script.index("run-document-extractors --source-type business_report")
    policy_pos = script.index("collect-policies --market KOSPI --year \"$year\"")
    financial_pos = script.index("collect-all --year-from")

    assert business_pos < extractor_pos < policy_pos < financial_pos


def test_source_documents_backfill_avoids_financial_endpoint():
    script = open("scripts/run_source_documents_backfill.sh", encoding="utf-8").read()

    assert "collect-business-report-sections" in script
    assert "run-document-extractors --source-type business_report" in script
    assert "collect-all --year-from" not in script
    assert "collect-audit-fees" not in script


def test_limit_aware_backfill_defaults_to_source_documents_script():
    script = open("scripts/dart_limit_aware_backfill.sh", encoding="utf-8").read()

    assert "KREPORTS_BACKFILL_SCRIPT" in script
    assert "scripts/run_source_documents_backfill.sh" in script


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
            session.add(
                Company(
                    corp_code=f"{idx:08d}",
                    stock_code=f"{idx:06d}",
                    corp_name=f"C{idx}",
                    market="KOSPI",
                    induty_code="264",
                )
            )

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
                    bsns_year=2025,
                    fs_div="CFS",
                    rcept_no="20260301000001",
                    source_type="business_report",
                    note_no="2",
                    note_title="재무제표 작성기준",
                    section_type="basis",
                    body="한국채택국제회계기준에 따라 작성되었습니다.",
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
    assert snapshot["counts"]["accounting_policy_items"] == 1
    assert snapshot["counts"]["audit_procedure_items"] == 1
    assert snapshot["missing_features"] == []
