from __future__ import annotations

from kreports.collector.corp_sync import upsert_minimal_companies
from kreports.db.engine import get_session
from kreports.db.models import Company


def test_upsert_minimal_companies_creates_stock_code_less_rows(temp_engine):
    created = upsert_minimal_companies([
        {"corp_code": "00300001", "corp_name": "비상장외감법인"},
    ])

    assert created == 1
    with get_session() as session:
        row = session.get(Company, "00300001")
        assert row is not None
        assert row.corp_name == "비상장외감법인"
        assert row.stock_code is None
        assert row.market is None


def test_upsert_minimal_companies_does_not_clobber_existing_listed_data(temp_engine):
    with get_session() as session:
        session.add(Company(
            corp_code="00300002", corp_name="이미상장사", stock_code="123456", market="KOSDAQ",
        ))

    upsert_minimal_companies([{"corp_code": "00300002", "corp_name": "이미상장사(구명)"}])

    with get_session() as session:
        row = session.get(Company, "00300002")
        assert row.stock_code == "123456"
        assert row.market == "KOSDAQ"
        assert row.corp_name == "이미상장사"


def test_upsert_minimal_companies_handles_empty_list(temp_engine):
    assert upsert_minimal_companies([]) == 0
