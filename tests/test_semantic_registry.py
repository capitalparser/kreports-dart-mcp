from dataclasses import FrozenInstanceError

import pytest


REQUIRED_METRIC_KEYS = {
    "revenue",
    "operating_profit",
    "profit_loss",
    "operating_cash_flow",
    "assets",
    "liabilities",
    "equity",
    "cash_and_equivalents",
    "interest_bearing_debt",
    "depreciation_amortization",
    "purchase_ppe",
    "purchase_intangible_assets",
    "trade_receivables",
    "inventories",
    "trade_payables",
    "tax_expense",
    "audit_fee",
    "audit_hours",
}


def test_revenue_semantics_are_explicit():
    """Changing revenue to a point-in-time, OFS, or non-compact metric is a bug."""
    from kreports.semantic.metrics import metric_definition

    metric = metric_definition("revenue")

    assert metric.unit == "KRW"
    assert metric.period_type == "duration"
    assert metric.preferred_fs_div == "CFS"
    assert "financial_facts_compact" in metric.source_tables


def test_required_financial_metrics_have_definitions():
    """Dropping a QoE, DCF, or audit metric from the registry is a bug."""
    from kreports.semantic.metrics import METRICS

    assert REQUIRED_METRIC_KEYS.issubset(METRICS)


def test_semantic_definitions_are_immutable():
    """Mutating a shared metric or dataset definition at runtime is a bug."""
    from kreports.semantic.datasets import dataset_definition
    from kreports.semantic.metrics import metric_definition

    with pytest.raises(FrozenInstanceError):
        metric_definition("revenue").label_ko = "변경된 매출액"
    with pytest.raises(FrozenInstanceError):
        dataset_definition("audit_matter_items").grain = "변경된 그레인"


def test_unknown_metric_fails_closed():
    """An invented metric must not silently acquire generic semantics."""
    from kreports.semantic.metrics import metric_definition

    with pytest.raises(KeyError):
        metric_definition("invented_metric")


def test_dataset_definition_records_cache_absence_limit():
    """An absent cached audit-matter row must not be treated as absent in its filing."""
    from kreports.semantic.datasets import dataset_definition

    dataset = dataset_definition("audit_matter_items")

    assert dataset.unique_key == ("rcept_no", "matter_type", "ordinal")
    assert dataset.absence_semantics == "cache_absence_not_filing_absence"


def test_unknown_dataset_fails_closed():
    """An invented dataset must not silently acquire generic absence semantics."""
    from kreports.semantic.datasets import dataset_definition

    with pytest.raises(KeyError):
        dataset_definition("invented_dataset")


def test_financial_series_preserves_legacy_quality_aliases(temp_engine, monkeypatch):
    """Removing net_income or operating_cf from QoE rows breaks existing consumers."""
    from kreports.analysis import investor_quality
    from kreports.db.engine import get_session
    from sqlalchemy import text

    monkeypatch.setattr(investor_quality, "engine", temp_engine)
    with get_session() as session:
        session.execute(text("""
            INSERT INTO financial_facts_compact
            (corp_code, bsns_year, fs_div, metric_key, metric_name, amount, fetched_at)
            VALUES
            ('00126380', 2024, 'CFS', 'profit_loss', '당기순손익', 80, CURRENT_TIMESTAMP),
            ('00126380', 2024, 'CFS', 'operating_cash_flow', '영업활동현금흐름', 90, CURRENT_TIMESTAMP)
        """))
        session.commit()

    assert investor_quality._financial_series("00126380", 2024, 2024) == [{
        "bsns_year": 2024,
        "net_income": 80,
        "operating_cf": 90,
    }]
