from kreports.analysis.readiness import readiness_verdict
from kreports.cli.main import _select_policy_targets
from kreports.db.models import Company


def test_readiness_verdict_passes_core_thresholds():
    snapshot = {
        "markets": {
            "KOSPI": {"listed": 838, "financial_any_2025": 835, "audit_fee_2025": 835, "disclosure_recent": 838},
            "KOSDAQ": {"listed": 1817, "financial_any_2025": 1808, "audit_fee_2025": 1809, "disclosure_recent": 1817},
        },
        "policy_corps": 7,
        "auditor_2025_corps": 740,
    }
    out = readiness_verdict(snapshot)
    assert out["verdict"] == "conditional_pass"
    assert "accounting_policy" in out["recommended_gaps"]


def test_readiness_verdict_fails_when_financial_coverage_low():
    snapshot = {
        "markets": {
            "KOSPI": {"listed": 838, "financial_any_2025": 400, "audit_fee_2025": 835, "disclosure_recent": 838},
            "KOSDAQ": {"listed": 1817, "financial_any_2025": 1808, "audit_fee_2025": 1809, "disclosure_recent": 1817},
        },
        "policy_corps": 7,
        "auditor_2025_corps": 740,
    }
    out = readiness_verdict(snapshot)
    assert out["verdict"] == "fail"
    assert "financial_any_2025" in out["required_gaps"]


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
