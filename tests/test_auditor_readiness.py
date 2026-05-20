from kreports.analysis.readiness import backfill_plan, readiness_verdict
from kreports.cli.main import _select_policy_targets
from kreports.db.models import Company


def test_full_backfill_runs_policies_before_financial_endpoint():
    script = open("scripts/run_full_dataset_backfill.sh", encoding="utf-8").read()

    business_pos = script.index("collect-business-report-sections")
    extractor_pos = script.index("run-document-extractors --source-type business_report")
    policy_pos = script.index("collect-policies --market KOSPI --year \"$year\"")
    financial_pos = script.index("collect-all --year-from")

    assert business_pos < extractor_pos < policy_pos < financial_pos


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
