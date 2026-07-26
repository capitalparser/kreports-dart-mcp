from pathlib import Path

import pytest
from sqlalchemy import create_engine, text


REQUIRED_ROWS = [
    ("revenue", "매출", 1000, "ifrs-full_Revenue"),
    ("operating_profit", "영업이익", 100, "ifrs-full_OperatingProfitLoss"),
    ("depreciation_amortization", "감가상각비", 40, "ifrs-full_DepreciationAndAmortisationExpense"),
    ("purchase_ppe", "유형자산 취득", -30, "ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"),
    ("purchase_intangible_assets", "무형자산 취득", -10, "ifrs-full_PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities"),
    ("trade_receivables", "매출채권", 200, "ifrs-full_TradeAndOtherCurrentReceivables"),
    ("inventories", "재고", 100, "ifrs-full_Inventories"),
    ("trade_payables", "매입채무", 150, "ifrs-full_TradePayables"),
    ("cash_and_equivalents", "현금", 80, "ifrs-full_CashAndCashEquivalents"),
    ("interest_bearing_debt", "차입금", 200, "ifrs-full_Borrowings"),
]


def _schema(engine, *, with_id: bool = True):
    id_column = "id INTEGER," if with_id else ""
    with engine.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE financial_facts_compact (
              {id_column}
              corp_code TEXT, bsns_year INTEGER, fs_div TEXT,
              metric_key TEXT, metric_name TEXT, amount TEXT,
              source_account_id TEXT, source_account_nm TEXT,
              fetched_at TEXT
            )
        """))


def _seed(engine, *, year: int = 2024, fs_div: str = "CFS"):
    with engine.begin() as conn:
        for index, (key, name, amount, account_id) in enumerate(REQUIRED_ROWS, 1):
            conn.execute(text("""
                INSERT INTO financial_facts_compact
                (id, corp_code, bsns_year, fs_div, metric_key, metric_name, amount,
                 source_account_id, source_account_nm, fetched_at)
                VALUES (:id, '00126380', :year, :fs_div, :key, :name, :amount,
                        :account_id, :name, '2025-03-31T00:00:00')
            """), {
                "id": index,
                "year": year,
                "fs_div": fs_div,
                "key": key,
                "name": name,
                "amount": amount,
                "account_id": account_id,
            })


def test_dcf_source_binds_exact_year_fs_and_preserves_compact_provenance(tmp_path):
    from kreports.analysis.dcf_source import load_dcf_actuals

    engine = create_engine(f"sqlite:///{tmp_path / 'facts.db'}")
    _schema(engine)
    _seed(engine, year=2023, fs_div="CFS")
    _seed(engine, year=2024, fs_div="OFS")
    _seed(engine, year=2024, fs_div="CFS")

    result = load_dcf_actuals("00126380", 2024, "CFS", read_engine=engine)

    assert result.status == "usable"
    assert len(result.facts) == 10
    assert {fact.year for fact in result.facts} == {2024}
    assert {fact.fs_div for fact in result.facts} == {"CFS"}
    revenue = next(fact for fact in result.facts if fact.metric_key == "revenue")
    assert revenue.unit == "KRW"
    assert revenue.source_account_id == "ifrs-full_Revenue"
    assert revenue.source_account_name == "매출"
    assert revenue.source_table == "financial_facts_compact"
    assert revenue.fetched_at == "2025-03-31T00:00:00"
    assert not hasattr(revenue, "rcept_no")


def test_dcf_source_does_not_fallback_to_another_year_or_fs(tmp_path):
    from kreports.analysis.dcf_source import load_dcf_actuals

    engine = create_engine(f"sqlite:///{tmp_path / 'facts.db'}")
    _schema(engine)
    _seed(engine, year=2023, fs_div="CFS")
    _seed(engine, year=2024, fs_div="OFS")

    result = load_dcf_actuals("00126380", 2024, "CFS", read_engine=engine)

    assert result.status == "missing"
    assert result.facts == ()
    assert "revenue" in result.missing_metrics


def test_dcf_source_selects_newest_duplicate_and_fails_closed_without_discriminator(tmp_path):
    from kreports.analysis.dcf_source import load_dcf_actuals

    deterministic = create_engine(f"sqlite:///{tmp_path / 'deterministic.db'}")
    _schema(deterministic)
    _seed(deterministic)
    with deterministic.begin() as conn:
        conn.execute(text("""
            INSERT INTO financial_facts_compact
            (id, corp_code, bsns_year, fs_div, metric_key, metric_name, amount,
             source_account_id, source_account_nm, fetched_at)
            VALUES
            (99, '00126380', 2024, 'CFS', 'revenue', '매출', 1200,
             'ifrs-full_Revenue', '매출', '2025-04-01T00:00:00')
        """))
    selected = load_dcf_actuals("00126380", 2024, "CFS", read_engine=deterministic)
    assert next(f.amount for f in selected.facts if f.metric_key == "revenue") == 1200

    ambiguous = create_engine(f"sqlite:///{tmp_path / 'ambiguous.db'}")
    _schema(ambiguous, with_id=False)
    with ambiguous.begin() as conn:
        conn.execute(text("""
            INSERT INTO financial_facts_compact
            (corp_code, bsns_year, fs_div, metric_key, metric_name, amount,
             source_account_id, source_account_nm, fetched_at)
            VALUES
            ('00126380', 2024, 'CFS', 'revenue', '매출', 1000,
             'ifrs-full_Revenue', '매출', NULL),
            ('00126380', 2024, 'CFS', 'revenue', '매출', 1200,
             'ifrs-full_Revenue', '매출', NULL)
        """))
    failed = load_dcf_actuals("00126380", 2024, "CFS", read_engine=ambiguous)
    assert failed.status == "partial"
    assert "revenue" in failed.missing_metrics
    assert "duplicate_ambiguous:revenue" in failed.limitations


def test_dcf_source_fails_closed_when_duplicate_recency_discriminators_tie(
    tmp_path,
):
    from kreports.analysis.dcf_source import load_dcf_actuals

    engine = create_engine(f"sqlite:///{tmp_path / 'tied.db'}")
    _schema(engine)
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO financial_facts_compact
            (id, corp_code, bsns_year, fs_div, metric_key, metric_name, amount,
             source_account_id, source_account_nm, fetched_at)
            VALUES
            (1, '00126380', 2024, 'CFS', 'revenue', '매출', 1000,
             'ifrs-full_Revenue', '매출', '2025-04-01T00:00:00'),
            (1, '00126380', 2024, 'CFS', 'revenue', '매출', 1200,
             'ifrs-full_Revenue', '매출', '2025-04-01T00:00:00')
        """))

    result = load_dcf_actuals("00126380", 2024, "CFS", read_engine=engine)

    assert result.status == "partial"
    assert result.facts == ()
    assert "duplicate_ambiguous:revenue" in result.limitations


def test_dcf_source_treats_malformed_conflicting_recency_as_ambiguous(
    tmp_path,
):
    from kreports.analysis.dcf_source import load_dcf_actuals

    engine = create_engine(f"sqlite:///{tmp_path / 'malformed-recency.db'}")
    _schema(engine)
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO financial_facts_compact
            (id, corp_code, bsns_year, fs_div, metric_key, metric_name, amount,
             source_account_id, source_account_nm, fetched_at)
            VALUES
            (1, '00126380', 2024, 'CFS', 'revenue', '매출', 1000,
             'ifrs-full_Revenue', '매출', '2025-04-01T00:00:00'),
            (2, '00126380', 2024, 'CFS', 'revenue', '매출', 1200,
             'ifrs-full_Revenue', '매출', 'not-an-iso-timestamp')
        """))

    result = load_dcf_actuals("00126380", 2024, "CFS", read_engine=engine)

    assert result.status == "partial"
    assert result.facts == ()
    assert "revenue" in result.missing_metrics
    assert "duplicate_ambiguous:revenue" in result.limitations


def test_dcf_source_uses_integer_id_only_after_comparable_timestamp_tie(
    tmp_path,
):
    from kreports.analysis.dcf_source import load_dcf_actuals

    cases = (
        (
            "mixed-awareness",
            "1",
            "2",
            "2025-04-01T00:00:00",
            "2025-04-01T00:00:00+09:00",
            None,
        ),
        (
            "malformed-id",
            "bad-id",
            "2",
            "2025-04-01T00:00:00",
            "2025-04-01T00:00:00",
            None,
        ),
        (
            "integer-tie-break",
            "1",
            "2",
            "2025-04-01T00:00:00",
            "2025-04-01T00:00:00",
            1200,
        ),
    )
    for name, first_id, second_id, first_time, second_time, expected in cases:
        engine = create_engine(f"sqlite:///{tmp_path / f'{name}.db'}")
        _schema(engine)
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO financial_facts_compact
                (id, corp_code, bsns_year, fs_div, metric_key, metric_name,
                 amount, source_account_id, source_account_nm, fetched_at)
                VALUES
                (:first_id, '00126380', 2024, 'CFS', 'revenue', '매출',
                 1000, 'ifrs-full_Revenue', '매출', :first_time),
                (:second_id, '00126380', 2024, 'CFS', 'revenue', '매출',
                 1200, 'ifrs-full_Revenue', '매출', :second_time)
            """), {
                "first_id": first_id,
                "second_id": second_id,
                "first_time": first_time,
                "second_time": second_time,
            })

        result = load_dcf_actuals(
            "00126380",
            2024,
            "CFS",
            read_engine=engine,
        )

        revenue = next(
            (
                fact.amount
                for fact in result.facts
                if fact.metric_key == "revenue"
            ),
            None,
        )
        assert revenue == expected
        if expected is None:
            assert "duplicate_ambiguous:revenue" in result.limitations
        else:
            assert "duplicate_resolved_latest:revenue" in result.limitations


def test_dcf_source_fails_closed_for_missing_partial_corrupt_and_nonempty_wal(
    tmp_path,
    monkeypatch,
):
    from kreports.analysis.dcf_source import load_dcf_actuals
    import kreports.db.engine as engine_module

    missing_path = tmp_path / "missing.db"
    monkeypatch.setattr(engine_module, "engine", create_engine(f"sqlite:///{missing_path}"))
    missing = load_dcf_actuals("00126380", 2024, "CFS")
    assert missing.status == "missing"
    assert not missing_path.exists()

    partial_path = tmp_path / "partial.db"
    partial_engine = create_engine(f"sqlite:///{partial_path}")
    with partial_engine.begin() as conn:
        conn.execute(text("CREATE TABLE financial_facts_compact (corp_code TEXT)"))
    monkeypatch.setattr(engine_module, "engine", partial_engine)
    partial = load_dcf_actuals("00126380", 2024, "CFS")
    assert partial.status == "missing"
    assert any("missing_columns" in item for item in partial.limitations)

    corrupt_path = tmp_path / "corrupt.db"
    corrupt_path.write_bytes(b"not sqlite")
    monkeypatch.setattr(engine_module, "engine", create_engine(f"sqlite:///{corrupt_path}"))
    corrupt = load_dcf_actuals("00126380", 2024, "CFS")
    assert corrupt.status == "missing"

    wal_path = tmp_path / "wal.db"
    wal_engine = create_engine(f"sqlite:///{wal_path}")
    _schema(wal_engine)
    _seed(wal_engine)
    Path(f"{wal_path}-wal").write_bytes(b"uncheckpointed")
    monkeypatch.setattr(engine_module, "engine", wal_engine)
    wal = load_dcf_actuals("00126380", 2024, "CFS")
    assert wal.status == "missing"
    assert "uncheckpointed_wal" in " ".join(wal.limitations)


def test_dcf_source_withholds_null_provenance_but_allows_named_summary_source(
    tmp_path,
):
    from kreports.analysis.dcf_source import load_dcf_actuals

    engine = create_engine(f"sqlite:///{tmp_path / 'provenance.db'}")
    _schema(engine)
    _seed(engine)
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE financial_facts_compact
            SET source_account_id=NULL, source_account_nm=NULL
            WHERE metric_key='revenue'
        """))
    missing = load_dcf_actuals("00126380", 2024, "CFS", read_engine=engine)
    assert missing.status == "partial"
    assert "revenue" in missing.missing_metrics
    assert "provenance_gap:revenue" in missing.limitations

    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE financial_facts_compact
            SET source_account_id='financials.revenue',
                source_account_nm='매출액'
            WHERE metric_key='revenue'
        """))
    synthetic = load_dcf_actuals("00126380", 2024, "CFS", read_engine=engine)
    assert synthetic.status == "usable"
    assert next(
        fact.source_account_id
        for fact in synthetic.facts
        if fact.metric_key == "revenue"
    ) == "financials.revenue"


@pytest.mark.parametrize(
    "metric_key",
    ["revenue", "cash_and_equivalents"],
)
def test_dcf_source_withholds_impossible_negative_fact_without_distortion(
    tmp_path,
    metric_key,
):
    from kreports.analysis.dcf_source import load_dcf_actuals

    engine = create_engine(f"sqlite:///{tmp_path / 'negative-balance.db'}")
    _schema(engine)
    _seed(engine)
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE financial_facts_compact
            SET amount='-1'
            WHERE metric_key=:metric_key
        """), {"metric_key": metric_key})

    result = load_dcf_actuals(
        "00126380",
        2024,
        "CFS",
        read_engine=engine,
    )

    assert result.status == "partial"
    assert metric_key in result.missing_metrics
    assert f"invalid_fact_contract:{metric_key}" in result.limitations
    assert all(
        fact.metric_key != metric_key
        for fact in result.facts
    )


def test_compact_registry_exposes_all_dcf_source_metrics_without_debt_double_counting():
    from kreports.semantic.metrics import DCF_MODEL_METRICS, METRICS

    assert DCF_MODEL_METRICS == (
        "revenue",
        "operating_profit",
        "depreciation_amortization",
        "purchase_ppe",
        "purchase_intangible_assets",
        "trade_receivables",
        "inventories",
        "trade_payables",
        "cash_and_equivalents",
        "interest_bearing_debt",
    )
    debt = METRICS["interest_bearing_debt"]
    assert debt.source_account_groups[0] == (
        "ifrs-full_CurrentBorrowings",
        "ifrs-full_NoncurrentBorrowings",
        "ifrs-full_CurrentBondsIssued",
        "ifrs-full_NoncurrentBondsIssued",
    )
    assert ("ifrs-full_Borrowings",) in debt.source_account_groups


def test_compact_rebuild_covers_every_dcf_model_source_metric(temp_engine):
    from kreports.db.engine import get_session
    from kreports.maintenance.financial_compact import (
        rebuild_financial_facts_compact,
    )
    from kreports.semantic.metrics import DCF_MODEL_METRICS, METRICS

    rows = []
    for index, metric_key in enumerate(DCF_MODEL_METRICS, 1):
        definition = METRICS[metric_key]
        account_group = definition.source_account_groups[0]
        for component_index, account_id in enumerate(account_group, 1):
            rows.append({
                "account_id": account_id,
                "account_nm": f"{metric_key}-{component_index}",
                "sj_div": definition.statement_division_preference[0],
                "amount": index * 100 + component_index,
                "ord": len(rows) + 1,
            })
    with get_session() as session:
        for row in rows:
            session.execute(text("""
                INSERT INTO financial_facts
                (corp_code, bsns_year, reprt_code, fs_div, sj_div,
                 account_id, account_nm, ord, thstrm_amount, fetched_at)
                VALUES
                ('00126380', 2024, '11011', 'CFS', :sj_div,
                 :account_id, :account_nm, :ord, :amount, CURRENT_TIMESTAMP)
            """), row)
        session.commit()

    rebuild_financial_facts_compact(year_from=2024, year_to=2024)

    with get_session() as session:
        rebuilt = {
            row[0]
            for row in session.execute(text("""
                SELECT metric_key
                FROM financial_facts_compact
                WHERE corp_code='00126380'
                  AND bsns_year=2024
                  AND fs_div='CFS'
            """))
        }
    assert set(DCF_MODEL_METRICS) <= rebuilt


def test_compact_rebuild_fails_closed_on_conflicting_same_statement_duplicates():
    from kreports.maintenance.financial_compact import _compact_rows

    common = {
        "corp_code": "00126380",
        "bsns_year": 2024,
        "fs_div": "CFS",
        "sj_div": "IS",
        "account_id": "ifrs-full_Revenue",
        "account_nm": "매출",
    }
    rows = _compact_rows([
        {**common, "thstrm_amount": 1000},
        {**common, "thstrm_amount": 1200},
    ])

    assert rows == []


def test_compact_rebuild_dedupes_identical_same_statement_rows():
    from kreports.maintenance.financial_compact import _compact_rows

    common = {
        "corp_code": "00126380",
        "bsns_year": 2024,
        "fs_div": "CFS",
        "sj_div": "IS",
        "account_id": "ifrs-full_Revenue",
        "account_nm": "매출",
        "thstrm_amount": 1000,
    }

    assert _compact_rows([common, dict(common)]) == [{
        "corp_code": "00126380",
        "bsns_year": 2024,
        "fs_div": "CFS",
        "metric_key": "revenue",
        "metric_name": "매출액",
        "amount": 1000,
        "source_account_id": "ifrs-full_Revenue",
        "source_account_nm": "매출",
    }]


def test_compact_rebuild_reconciles_stale_conflict_without_summary_fallback(
    temp_engine,
):
    from kreports.db.engine import get_session
    from kreports.db.models import Financial
    from kreports.maintenance.financial_compact import (
        rebuild_financial_facts_compact,
    )

    with get_session() as session:
        session.execute(text("""
            INSERT INTO financial_facts
            (corp_code, bsns_year, reprt_code, fs_div, sj_div, account_id,
             account_nm, ord, thstrm_amount, fetched_at)
            VALUES
            ('00126380', 2024, '11011', 'CFS', 'IS',
             'ifrs-full_Revenue', '매출', 1, 1000, CURRENT_TIMESTAMP)
        """))
        session.add(Financial(
            corp_code="00126380",
            year=2024,
            quarter=4,
            fs_div="CFS",
            revenue=777,
        ))
        session.execute(text("""
            INSERT INTO financial_facts_compact
            (corp_code, bsns_year, fs_div, metric_key, metric_name, amount,
             source_account_id, source_account_nm, fetched_at)
            VALUES
            ('outside', 2023, 'CFS', 'revenue', '매출', 55,
             'manual.outside', '외부', CURRENT_TIMESTAMP),
            ('00126380', 2024, 'CFS', 'custom_metric', '사용자 지표', 9,
             'manual.custom', '사용자 지표', CURRENT_TIMESTAMP)
        """))
        session.commit()

    rebuild_financial_facts_compact(year_from=2024, year_to=2024)
    with get_session() as session:
        assert session.execute(text("""
            SELECT amount FROM financial_facts_compact
            WHERE corp_code='00126380' AND bsns_year=2024
              AND fs_div='CFS' AND metric_key='revenue'
        """)).scalar_one() == 1000
        session.execute(text("""
            UPDATE financial_facts
            SET thstrm_amount=NULL
            WHERE corp_code='00126380' AND bsns_year=2024
              AND account_id='ifrs-full_Revenue'
        """))
        session.commit()

    rebuild_financial_facts_compact(year_from=2024, year_to=2024)
    with get_session() as session:
        assert session.execute(text("""
            SELECT amount FROM financial_facts_compact
            WHERE corp_code='00126380' AND bsns_year=2024
              AND fs_div='CFS' AND metric_key='revenue'
        """)).scalar_one_or_none() is None
        assert session.execute(text("""
            SELECT amount FROM financial_facts_compact
            WHERE corp_code='outside' AND bsns_year=2023
              AND metric_key='revenue'
        """)).scalar_one() == 55
        assert session.execute(text("""
            SELECT amount FROM financial_facts_compact
            WHERE corp_code='00126380' AND bsns_year=2024
              AND metric_key='custom_metric'
        """)).scalar_one() == 9


def test_compact_rebuild_removes_stale_rows_when_source_is_deleted(temp_engine):
    from kreports.db.engine import get_session
    from kreports.maintenance.financial_compact import (
        rebuild_financial_facts_compact,
    )

    with get_session() as session:
        session.execute(text("""
            INSERT INTO financial_facts
            (corp_code, bsns_year, reprt_code, fs_div, sj_div, account_id,
             account_nm, ord, thstrm_amount, fetched_at)
            VALUES
            ('00126380', 2024, '11011', 'CFS', 'IS',
             'ifrs-full_Revenue', '매출', 1, 1000, CURRENT_TIMESTAMP)
        """))
        session.commit()
    rebuild_financial_facts_compact(year_from=2024, year_to=2024)
    with get_session() as session:
        session.execute(text("DELETE FROM financial_facts"))
        session.commit()

    rebuild_financial_facts_compact(year_from=2024, year_to=2024)

    with get_session() as session:
        assert session.execute(text("""
            SELECT amount FROM financial_facts_compact
            WHERE corp_code='00126380' AND bsns_year=2024
              AND metric_key='revenue'
        """)).scalar_one_or_none() is None
