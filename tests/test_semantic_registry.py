from dataclasses import FrozenInstanceError

import pytest


REQUIRED_METRIC_KEYS = {
    "revenue",
    "operating_profit",
    "profit_loss",
    "profit_before_tax",
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
    assert metric.source_account_ids[:3] == (
        "ifrs-full_Revenue",
        "ifrs-full_RevenueFromContractsWithCustomers",
        "dart_Revenue",
    )


def test_representative_metric_semantics_are_complete():
    """Changing representative metric units, periods, sources, or null meanings is a bug."""
    from kreports.semantic.metrics import canonical_amount, metric_definition

    assets = metric_definition("assets")
    debt = metric_definition("interest_bearing_debt")
    fee = metric_definition("audit_fee")

    assert (assets.unit, assets.period_type, assets.aggregation, assets.null_meaning) == (
        "KRW", "instant", "last", "missing"
    )
    assert debt.aggregation == "sum"
    assert debt.source_account_groups[0] == (
        "ifrs-full_CurrentBorrowings",
        "ifrs-full_NoncurrentBorrowings",
        "ifrs-full_CurrentBondsIssued",
        "ifrs-full_NoncurrentBondsIssued",
    )
    assert (fee.unit, fee.source_unit, fee.source_multiplier) == (
        "KRW", "million_KRW", 1_000_000
    )
    assert canonical_amount("audit_fee", 7) == 7_000_000


def test_required_financial_metrics_have_definitions():
    """Dropping a QoE, DCF, or audit metric from the registry is a bug."""
    from kreports.semantic.metrics import METRICS

    assert REQUIRED_METRIC_KEYS.issubset(METRICS)


def test_all_compact_metrics_have_unscaled_krw_storage_and_financial_periods():
    """A compact metric with a scaled/unknown source or event period is uncitable."""
    from kreports.semantic.metrics import compact_metric_definitions

    metrics = compact_metric_definitions()

    assert metrics
    assert all(
        (
            metric.unit,
            metric.source_unit,
            metric.source_multiplier,
            metric.period_type,
        )
        in {
            ("KRW", "KRW", 1, "instant"),
            ("KRW", "KRW", 1, "duration"),
        }
        for metric in metrics
    )


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

    assert dataset.unique_key == ("rcept_no", "matter_type", "section_ordinal")
    assert dataset.absence_semantics == "cache_absence_not_filing_absence"


def test_dataset_unique_keys_match_orm_schema():
    """Changing a registry key away from the database uniqueness contract is a bug."""
    from sqlalchemy import UniqueConstraint

    from kreports.db.models import AuditMatterItem, FinancialFact
    from kreports.semantic.datasets import dataset_definition

    def unique_key(model):
        constraint = next(
            constraint
            for constraint in model.__table__.constraints
            if isinstance(constraint, UniqueConstraint)
        )
        return tuple(column.name for column in constraint.columns)

    assert dataset_definition("financial_facts").unique_key == unique_key(FinancialFact)
    assert dataset_definition("audit_matter_items").unique_key == unique_key(AuditMatterItem)


def test_unknown_dataset_fails_closed():
    """An invented dataset must not silently acquire generic absence semantics."""
    from kreports.semantic.datasets import dataset_definition

    with pytest.raises(KeyError):
        dataset_definition("invented_dataset")


def test_financial_series_preserves_legacy_quality_aliases(temp_engine):
    """Removing net_income or operating_cf from QoE rows breaks existing consumers."""
    from kreports.analysis import investor_quality
    from kreports.db.engine import get_session
    from sqlalchemy import text

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


def test_financial_series_fetches_explicit_dcf_support_metric(temp_engine):
    """Dropping tax expense from the explicit DCF metric query breaks tax-rate inputs."""
    from sqlalchemy import text

    from kreports.analysis import investor_quality
    from kreports.db.engine import get_session
    from kreports.semantic.metrics import DCF_SUPPORT_METRICS

    with get_session() as session:
        session.execute(text("""
            INSERT INTO financial_facts_compact
            (corp_code, bsns_year, fs_div, metric_key, metric_name, amount, fetched_at)
            VALUES ('00126380', 2024, 'CFS', 'tax_expense', '법인세비용', 7, CURRENT_TIMESTAMP)
        """))
        session.commit()

    assert investor_quality._financial_series(
        "00126380", 2024, 2024, metric_keys=DCF_SUPPORT_METRICS
    ) == [{"bsns_year": 2024, "tax_expense": 7}]


def test_compact_rebuild_keeps_non_null_fallback_and_sums_debt_components(temp_engine):
    """A NULL preferred fact must not erase a fallback, and debt components must sum."""
    from sqlalchemy import text

    from kreports.db.engine import get_session
    from kreports.maintenance.financial_compact import rebuild_financial_facts_compact

    with get_session() as session:
        session.execute(text("""
            INSERT INTO financial_facts
            (corp_code, bsns_year, reprt_code, fs_div, sj_div, account_id, account_nm,
             ord, thstrm_amount, fetched_at)
            VALUES
            ('00126380', 2024, '11011', 'CFS', 'IS', 'ifrs-full_OperatingProfitLoss', '영업손익', 1, NULL, CURRENT_TIMESTAMP),
            ('00126380', 2024, '11011', 'CFS', 'IS', 'dart_OperatingIncomeLoss', '영업손익', 2, 30, CURRENT_TIMESTAMP),
            ('00126380', 2024, '11011', 'CFS', 'IS', 'ifrs-full_Revenue', '매출액', 3, 100, CURRENT_TIMESTAMP),
            ('00126380', 2024, '11011', 'CFS', 'IS', 'dart_Revenue', '매출액', 4, 90, CURRENT_TIMESTAMP),
            ('00126380', 2024, '11011', 'CFS', 'BS', 'ifrs-full_CurrentBorrowings', '유동차입금', 5, 10, CURRENT_TIMESTAMP),
            ('00126380', 2024, '11011', 'CFS', 'BS', 'ifrs-full_NoncurrentBorrowings', '비유동차입금', 6, 30, CURRENT_TIMESTAMP),
            ('00126380', 2024, '11011', 'CFS', 'BS', 'ifrs-full_CurrentBondsIssued', '유동사채', 7, 5, CURRENT_TIMESTAMP),
            ('00126380', 2024, '11011', 'CFS', 'BS', 'ifrs-full_NoncurrentBondsIssued', '비유동사채', 8, 15, CURRENT_TIMESTAMP)
        """))
        session.commit()

    rebuild_financial_facts_compact(year_from=2024, year_to=2024)

    with get_session() as session:
        rows = dict(session.execute(text("""
            SELECT metric_key, amount FROM financial_facts_compact
            WHERE corp_code='00126380' ORDER BY metric_key
        """)).all())
    assert rows["operating_profit"] == 30
    assert rows["revenue"] == 100
    assert rows["interest_bearing_debt"] == 60


def test_compact_debt_prefers_complete_total_over_partial_components(temp_engine):
    """A partial component group must lose to a complete later total fallback."""
    from sqlalchemy import text

    from kreports.db.engine import get_session
    from kreports.maintenance.financial_compact import rebuild_financial_facts_compact

    with get_session() as session:
        session.execute(text("""
            INSERT INTO financial_facts
            (corp_code, bsns_year, reprt_code, fs_div, sj_div, account_id, account_nm,
             ord, thstrm_amount, fetched_at)
            VALUES
            ('00126381', 2024, '11011', 'CFS', 'BS', 'ifrs-full_CurrentBorrowings', '유동차입금', 1, 10, CURRENT_TIMESTAMP),
            ('00126381', 2024, '11011', 'CFS', 'BS', 'ifrs-full_Borrowings', '차입금', 2, 100, CURRENT_TIMESTAMP)
        """))
        session.commit()

    rebuild_financial_facts_compact(year_from=2024, year_to=2024)

    with get_session() as session:
        amount = session.execute(text("""
            SELECT amount FROM financial_facts_compact
            WHERE corp_code='00126381' AND metric_key='interest_bearing_debt'
        """)).scalar_one()
    assert amount == 100


def test_compact_debt_uses_partial_components_only_without_complete_group(temp_engine):
    """A partial component amount remains an explicit last-resort coverage-limited value."""
    from sqlalchemy import text

    from kreports.db.engine import get_session
    from kreports.maintenance.financial_compact import rebuild_financial_facts_compact

    with get_session() as session:
        session.execute(text("""
            INSERT INTO financial_facts
            (corp_code, bsns_year, reprt_code, fs_div, sj_div, account_id, account_nm,
             ord, thstrm_amount, fetched_at)
            VALUES
            ('00126382', 2024, '11011', 'CFS', 'BS', 'ifrs-full_CurrentBorrowings', '유동차입금', 1, 10, CURRENT_TIMESTAMP)
        """))
        session.commit()

    rebuild_financial_facts_compact(year_from=2024, year_to=2024)

    with get_session() as session:
        amount = session.execute(text("""
            SELECT amount FROM financial_facts_compact
            WHERE corp_code='00126382' AND metric_key='interest_bearing_debt'
        """)).scalar_one()
    assert amount == 10


def test_compact_uses_registered_statement_division_preference(temp_engine):
    """The same account must select the registry-ranked CIS amount, not SQL row order."""
    from sqlalchemy import text

    from kreports.db.engine import get_session
    from kreports.maintenance.financial_compact import rebuild_financial_facts_compact

    with get_session() as session:
        session.execute(text("""
            INSERT INTO financial_facts
            (corp_code, bsns_year, reprt_code, fs_div, sj_div, account_id, account_nm,
             ord, thstrm_amount, fetched_at)
            VALUES
            ('00126383', 2024, '11011', 'CFS', 'IS', 'ifrs-full_Revenue', '매출액', 1, 90, CURRENT_TIMESTAMP),
            ('00126383', 2024, '11011', 'CFS', 'CIS', 'ifrs-full_Revenue', '매출액', 1, 100, CURRENT_TIMESTAMP)
        """))
        session.commit()

    rebuild_financial_facts_compact(year_from=2024, year_to=2024)

    with get_session() as session:
        amount = session.execute(text("""
            SELECT amount FROM financial_facts_compact
            WHERE corp_code='00126383' AND metric_key='revenue'
        """)).scalar_one()
    assert amount == 100
