from datetime import datetime
from unittest.mock import patch

from kreports.analysis.api import get_accounting_policy
from kreports.db.models import AccountingPolicyItem, Company


def test_get_accounting_policy_reads_cache_without_dart_key(temp_engine):
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(corp_code="00126380", stock_code="005930", corp_name="삼성전자", market="KOSPI", induty_code="264"))
        session.add(AccountingPolicyItem(
            corp_code="00126380",
            bsns_year=2025,
            fs_div="CFS",
            rcept_no="20260301000001",
            item_key="revenue_recognition",
            heading="수익인식",
            body="고객과의 계약에서 생기는 수익은 수행의무 이행 시 인식한다.",
            body_hash="abc",
            body_length=35,
            fetched_at=datetime.utcnow(),
        ))

    with patch("kreports.analysis.queries.get_accounting_policy") as live_fetch:
        out = get_accounting_policy("005930", 2025, fs_div="CFS")
        live_fetch.assert_not_called()

    assert out["corp_code"] == "00126380"
    assert out["item_count"] == 1
    assert out["items"]["revenue_recognition"]["body"].startswith("고객과의 계약")


def test_get_accounting_policy_cache_miss_does_not_request_dart_key(temp_engine):
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(corp_code="00126380", stock_code="005930", corp_name="삼성전자", market="KOSPI", induty_code="264"))

    with patch("kreports.analysis.queries.get_accounting_policy") as live_fetch:
        out = get_accounting_policy("005930", 2025, fs_div="CFS")
        live_fetch.assert_not_called()

    assert out["item_count"] == 0
    assert "pre-built DB" in out["note"]
    assert "DART_API_KEY" not in out["note"]
