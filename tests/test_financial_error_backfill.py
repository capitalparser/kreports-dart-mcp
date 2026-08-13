import importlib.util
from datetime import datetime
from pathlib import Path

from kreports.db.models import Company, FetchLog, Financial


def _load_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "backfill_error_financials.py"
    spec = importlib.util.spec_from_file_location("backfill_error_financials", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_error_financial_backfill_targets_only_retryable_missing_rows(temp_engine):
    from kreports.db.engine import get_session

    script = _load_script()
    with get_session() as session:
        session.add_all([
            Company(corp_code="001", corp_name="Retry", stock_code="000001", market="KOSPI"),
            Company(corp_code="002", corp_name="Other", stock_code="000002", market="KOSPI"),
            Company(corp_code="003", corp_name="Done", stock_code="000003", market="KOSPI"),
        ])
        session.add_all([
            FetchLog(
                task_type="financial",
                corp_code="001",
                year=2024,
                quarter=4,
                status="error",
                error_msg="[Errno 8] nodename nor servname provided, or not known",
                fetched_at=datetime(2026, 1, 1),
            ),
            FetchLog(
                task_type="financial",
                corp_code="002",
                year=2024,
                quarter=4,
                status="error",
                error_msg="parser failed in an unexpected way",
                fetched_at=datetime(2026, 1, 1),
            ),
            FetchLog(
                task_type="financial",
                corp_code="003",
                year=2024,
                quarter=4,
                status="error",
                error_msg="사용한도를 초과하였습니다.",
                fetched_at=datetime(2026, 1, 1),
            ),
            Financial(corp_code="003", year=2024, quarter=4, fs_div="CFS"),
        ])

    targets = script.list_targets(market="KOSPI", year_from=2024, year_to=2024)

    assert [(row.corp_code, row.year, row.quarter) for row in targets] == [("001", 2024, 4)]


def test_error_bucket_classifies_key_limit_and_dns():
    script = _load_script()

    assert script._error_bucket("사용한도를 초과하였습니다.") == "dart_limit"
    assert script._error_bucket("등록되지 않은 인증키입니다.") == "auth_key"
    assert script._error_bucket("[Errno 8] nodename nor servname provided") == "dns"
