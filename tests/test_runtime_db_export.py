from sqlalchemy import inspect


def test_financial_facts_compact_schema(temp_engine):
    inspector = inspect(temp_engine)
    columns = {column["name"] for column in inspector.get_columns("financial_facts_compact")}
    assert {
        "corp_code",
        "bsns_year",
        "fs_div",
        "metric_key",
        "metric_name",
        "amount",
        "source_account_id",
        "source_account_nm",
    }.issubset(columns)
