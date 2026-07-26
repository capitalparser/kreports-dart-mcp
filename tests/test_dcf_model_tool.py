from decimal import Decimal
import json
from pathlib import Path

import pytest
from pydantic import ValidationError


def _model_result():
    from kreports.analysis.dcf_model import (
        DcfActualFact,
        DcfScenarioInput,
        build_dcf_valuation,
        dcf_result_to_dict,
    )

    amounts = {
        "revenue": "1000",
        "operating_profit": "100",
        "depreciation_amortization": "40",
        "purchase_ppe": "-30",
        "purchase_intangible_assets": "-10",
        "trade_receivables": "200",
        "inventories": "100",
        "trade_payables": "150",
        "cash_and_equivalents": "80",
        "interest_bearing_debt": "200",
    }
    facts = tuple(
        DcfActualFact(
            metric_key=key,
            amount=Decimal(value),
            unit="KRW",
            year=2024,
            fs_div="CFS",
            source_account_id=key,
            source_account_name=key,
            source_table="financial_facts_compact",
            fetched_at=None,
        )
        for key, value in amounts.items()
    )
    scenario = DcfScenarioInput(
        company="00126380",
        base_year=2024,
        fs_div="CFS",
        forecast_years=2,
        revenue_growth=Decimal("0.1"),
        operating_margin=Decimal("0.1"),
        tax_rate=Decimal("0.2"),
        da_to_revenue=Decimal("0.05"),
        capex_to_revenue=Decimal("0.04"),
        nwc_to_revenue=Decimal("0.2"),
        wacc=Decimal("0.1"),
        terminal_growth=Decimal("0.03"),
    )
    out = dcf_result_to_dict(build_dcf_valuation(scenario, facts))
    out["subject"] = {
        "corp_code": "00126380",
        "corp_name": "<script>주식회사</script>",
    }
    out["data_quality"] = {
        "status": "usable",
        "covered_years": [2024],
        "source": "financial_facts_compact",
    }
    return out


def test_build_dcf_model_pack_is_the_only_additive_32nd_tool():
    from kreports.mcp.catalog import TOOL_CATALOG
    from kreports.mcp.handlers import HANDLERS
    from kreports.mcp.tools import ALL_TOOLS

    assert len(TOOL_CATALOG) == 32
    assert list(TOOL_CATALOG)[-1] == "build_dcf_model_pack"
    assert [tool.name for tool in ALL_TOOLS][-1] == "build_dcf_model_pack"
    assert "build_dcf_model_pack" in HANDLERS
    assert "get_dcf_input_candidates" in HANDLERS


def test_dcf_tool_input_is_explicit_strict_and_defaults_to_five_years():
    from kreports.mcp.input_models import BuildDcfModelPackInput

    model = BuildDcfModelPackInput(
        company="00126380",
        base_year=2024,
        fs_div="CFS",
        wacc=0.1,
        terminal_growth=0.03,
    )
    assert model.forecast_years == 5
    with pytest.raises(ValidationError):
        BuildDcfModelPackInput(
            company="00126380",
            base_year=2024,
            fs_div="CFS",
            revenue_growth=True,
        )
    with pytest.raises(ValidationError):
        BuildDcfModelPackInput(
            company="00126380",
            base_year=2024,
            fs_div="CFS",
            forecast_years=True,
        )
    with pytest.raises(ValidationError):
        BuildDcfModelPackInput(
            company="00126380",
            base_year=2024,
            fs_div="CFS",
            wacc=0.03,
            terminal_growth=0.03,
        )
    with pytest.raises(ValidationError):
        BuildDcfModelPackInput(
            company="00126380",
            base_year=2024,
            wacc=1e20,
        )
    with pytest.raises(ValidationError):
        BuildDcfModelPackInput(
            company="회" * 201,
            base_year=2024,
        )
    with pytest.raises(ValidationError):
        BuildDcfModelPackInput(
            company="00126380",
            base_year=2024,
            normalized_revenue=1000,
            normalization_reason="근" * 1001,
        )
    schema = BuildDcfModelPackInput.model_json_schema()["properties"]
    assert schema["company"]["maxLength"] == 200
    assert schema["normalization_reason"]["anyOf"][0]["maxLength"] == 1000


@pytest.mark.parametrize(
    "field",
    [
        "revenue_growth",
        "operating_margin",
        "tax_rate",
        "da_to_revenue",
        "capex_to_revenue",
        "nwc_to_revenue",
        "wacc",
        "terminal_growth",
        "normalized_revenue",
        "normalized_operating_profit",
    ],
)
def test_dcf_mcp_decimal_exponent_bound_matches_domain(field):
    from kreports.mcp.input_models import BuildDcfModelPackInput

    accepted = {field: "1e-31"}
    rejected = {field: "1e-10000"}
    if field == "wacc":
        accepted["terminal_growth"] = "-0.01"
        rejected["terminal_growth"] = "-0.01"
    if field in {"normalized_revenue", "normalized_operating_profit"}:
        accepted["normalization_reason"] = "정밀도 경계"
        rejected["normalization_reason"] = "정밀도 경계"

    model = BuildDcfModelPackInput(
        company="00126380",
        base_year=2024,
        **accepted,
    )
    assert Decimal(str(getattr(model, field))) == Decimal("1E-31")

    with pytest.raises(ValidationError, match=field):
        BuildDcfModelPackInput(
            company="00126380",
            base_year=2024,
            **rejected,
        )


def test_dcf_mcp_rejects_extreme_zero_quantum_before_float_coercion():
    from kreports.mcp.input_models import BuildDcfModelPackInput

    with pytest.raises(ValidationError, match="operating_margin"):
        BuildDcfModelPackInput(
            company="00126380",
            base_year=2024,
            operating_margin="0e-10000",
        )


def test_dcf_handler_forwards_all_explicit_layers(monkeypatch):
    import kreports.mcp.handlers.investor as investor_handler
    from kreports.mcp.input_models import BuildDcfModelPackInput

    seen = {}

    def fake(**kwargs):
        seen.update(kwargs)
        return {"status": "partial_model"}

    monkeypatch.setattr(investor_handler, "build_dcf_model_pack", fake)
    args = BuildDcfModelPackInput(
        company="00126380",
        base_year=2024,
        fs_div="OFS",
        forecast_years=1,
        revenue_growth=0.1,
        normalized_revenue=1000,
        normalization_reason="검토 조정",
    )

    assert investor_handler.handle_build_dcf_model_pack(args) == {"status": "partial_model"}
    assert seen == {
        "company": "00126380",
        "base_year": 2024,
        "fs_div": "OFS",
        "forecast_years": 1,
        "revenue_growth": 0.1,
        "operating_margin": None,
        "tax_rate": None,
        "da_to_revenue": None,
        "capex_to_revenue": None,
        "nwc_to_revenue": None,
        "wacc": None,
        "terminal_growth": None,
        "normalized_revenue": 1000.0,
        "normalized_operating_profit": None,
        "normalization_reason": "검토 조정",
    }


def test_dcf_handler_accepts_one_e_minus_31_and_fails_typed_on_money_rounding(
    temp_engine,
    monkeypatch,
):
    from kreports.analysis import dcf_source
    from kreports.analysis.dcf_source import DcfSourceResult
    from kreports.db.engine import get_session
    from kreports.db.models import Company
    from kreports.mcp.handlers.investor import handle_build_dcf_model_pack
    from kreports.mcp.input_models import BuildDcfModelPackInput

    with get_session() as session:
        session.add(Company(
            corp_code="00126380",
            corp_name="정확회사",
            stock_code="005930",
            market="KOSPI",
        ))
    monkeypatch.setattr(
        dcf_source,
        "load_dcf_actuals",
        lambda *_args, **_kwargs: DcfSourceResult(
            status="usable",
            facts=_facts_for_facade(),
            missing_metrics=(),
            limitations=(),
        ),
    )
    tiny_wacc = BuildDcfModelPackInput(
        company="005930",
        base_year=2024,
        forecast_years=2,
        revenue_growth=1e-31,
        operating_margin=1e-31,
        tax_rate=1e-31,
        da_to_revenue=1e-31,
        capex_to_revenue=1e-31,
        nwc_to_revenue=1e-31,
        wacc=1e-31,
        terminal_growth=-0.01,
    )

    valid = handle_build_dcf_model_pack(tiny_wacc)
    rounded_invalid = handle_build_dcf_model_pack(
        tiny_wacc.model_copy(update={
            "normalized_revenue": 1e-31,
            "normalization_reason": "KRW 반올림 경계",
        })
    )

    assert valid["status"] == "complete_model"
    assert valid["assumptions"][6]["value"] == (
        "0.0000000000000000000000000000001"
    )
    assert rounded_invalid["status"] == "invalid_model"
    assert rounded_invalid["missing_inputs"] == [
        "base_revenue_nonpositive"
    ]


def test_dcf_answer_pack_preserves_all_review_layers_and_escapes_subject():
    from kreports.mcp.answer_pack import build_answer_pack

    pack = build_answer_pack("build_dcf_model_pack", _model_result())

    assert pack["summary"]["title"].startswith("&lt;script&gt;")
    assert pack["summary"]["subject"] == "&lt;script&gt;주식회사&lt;/script&gt;"
    assert "<script>" not in json.dumps(pack, ensure_ascii=False)
    table_ids = {table["id"] for table in pack["tables"]}
    assert {
        "dcf_actuals",
        "dcf_normalization",
        "dcf_assumptions",
        "dcf_projections",
        "dcf_valuation_bridge",
        "dcf_sensitivity",
    } <= table_ids
    assert len(next(t for t in pack["tables"] if t["id"] == "dcf_sensitivity")["rows"]) == 25


def test_dcf_narrative_is_bounded_reviewable_and_not_a_conclusion():
    from kreports.mcp.renderers import render_answer

    text = render_answer("build_dcf_model_pack", _model_result())

    assert "검토 가능한 DCF 모델" in text
    assert "투자 권유" in text
    assert "공정성 의견" in text
    assert "승인된 예측" in text
    assert "감사 결론" in text
    assert "EBIT * (1-tax) + D&A - capex - change_in_NWC" in text
    assert "final_UFCF * (1+g) / (wacc-g)" in text
    assert "터미널가치:" in text
    assert "최종연도 할인계수:" in text
    assert "기업가치 = 예측기간 현재가치 + 터미널가치 현재가치" in text
    assert len(text) < 20_000


def test_dcf_legacy_candidates_and_runtime_facade_identity_remain_compatible():
    from kreports.analysis import api
    from kreports.analysis import financial_analysis

    assert api.get_dcf_input_candidates is financial_analysis.get_dcf_input_candidates
    assert api.build_dcf_model_pack is financial_analysis.build_dcf_model_pack


def test_dcf_model_result_has_no_binary_float_drift():
    payload = _model_result()
    encoded = json.dumps(payload, ensure_ascii=False)
    assert "0.30000000000000004" not in encoded
    assert payload["assumptions"][0]["value"] == "0.10"


def test_dcf_bridge_exposes_raw_terminal_formula_discount_and_ev_reconciliation():
    payload = _model_result()
    bridge = payload["valuation_bridge"]

    assert bridge["terminal_value"] == payload["terminal_value"]
    assert bridge["gordon_growth_formula"] == "final_UFCF * (1+g) / (wacc-g)"
    assert bridge["final_year_discount_factor"] == payload["projections"][-1][
        "discount_factor"
    ]
    assert bridge["enterprise_value_formula"] == (
        "enterprise_value = forecast_period_present_value + "
        "terminal_value_present_value"
    )


def test_dcf_facade_marks_enterprise_only_or_source_partial_as_limited(
    temp_engine,
    monkeypatch,
):
    from kreports.analysis import dcf_source, financial_analysis
    from kreports.analysis.dcf_source import DcfSourceResult
    from kreports.db.engine import get_session
    from kreports.db.models import Company

    with get_session() as session:
        session.add(Company(
            corp_code="00126380",
            corp_name="정확회사",
            stock_code="005930",
            market="KOSPI",
        ))
    facts = tuple(
        fact
        for fact in _facts_for_facade()
        if fact.metric_key != "cash_and_equivalents"
    )
    monkeypatch.setattr(
        dcf_source,
        "load_dcf_actuals",
        lambda *_args, **_kwargs: DcfSourceResult(
            status="partial",
            facts=facts,
            missing_metrics=("cash_and_equivalents",),
            limitations=("source_partial",),
        ),
    )

    result = financial_analysis.build_dcf_model_pack(
        "005930",
        2024,
        revenue_growth=0.1,
        operating_margin=0.1,
        tax_rate=0.2,
        da_to_revenue=0.05,
        capex_to_revenue=0.04,
        nwc_to_revenue=0.2,
        wacc=0.1,
        terminal_growth=0.03,
    )

    assert result["status"] == "complete_model"
    assert result["confidence"] == "enterprise_complete_equity_partial"
    assert result["data_quality"]["status"] == "limited"
    assert result["data_quality"]["enterprise_completion"] == "complete"
    assert result["data_quality"]["equity_completion"] == "partial"


def _facts_for_facade():
    from kreports.analysis.dcf_model import DcfActualFact

    values = {
        "revenue": "1000",
        "operating_profit": "100",
        "depreciation_amortization": "40",
        "purchase_ppe": "-30",
        "purchase_intangible_assets": "-10",
        "trade_receivables": "200",
        "inventories": "100",
        "trade_payables": "150",
        "cash_and_equivalents": "80",
        "interest_bearing_debt": "200",
    }
    return tuple(
        DcfActualFact(
            metric_key=key,
            amount=Decimal(value),
            unit="KRW",
            year=2024,
            fs_div="CFS",
            source_account_id=f"ifrs-full_{key}",
            source_account_name=key,
            source_table="financial_facts_compact",
            fetched_at=None,
        )
        for key, value in values.items()
    )


def test_dcf_direct_api_rejects_fuzzy_or_ambiguous_names_and_numeric_coercion(
    temp_engine,
):
    from kreports.analysis.financial_analysis import build_dcf_model_pack
    from kreports.db.engine import get_session
    from kreports.db.models import Company

    with get_session() as session:
        session.add_all([
            Company(
                corp_code="00000001",
                corp_name="알파 전자",
                stock_code="000001",
                market="KOSPI",
            ),
            Company(
                corp_code="00000002",
                corp_name="알파 화학",
                stock_code="000002",
                market="KOSPI",
            ),
            Company(
                corp_code="00000003",
                corp_name="중복 회사",
                stock_code="000003",
                market="KOSPI",
            ),
            Company(
                corp_code="00000004",
                corp_name="  중복   회사  ",
                stock_code="000004",
                market="KOSPI",
            ),
        ])

    fuzzy = build_dcf_model_pack("알파", 2024)
    assert "error" in fuzzy
    assert "정확" in fuzzy["error"]

    ambiguous = build_dcf_model_pack("중복 회사", 2024)
    assert "error" in ambiguous
    assert "둘 이상" in ambiguous["error"]

    with pytest.raises((TypeError, ValueError), match="base_year"):
        build_dcf_model_pack("000001", True)


def test_dcf_exact_identity_resolution_uses_immutable_sqlite_reads(
    tmp_path,
    monkeypatch,
):
    from sqlalchemy import create_engine, text

    import kreports.db.engine as engine_module
    from kreports.analysis.financial_analysis import _resolve_dcf_company_exact
    from kreports.db.models import Base

    database_path = tmp_path / "immutable-identity.db"
    engine = create_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    with engine.connect() as connection:
        connection.execute(text("PRAGMA journal_mode=WAL"))
        connection.commit()
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO companies
            (corp_code, stock_code, corp_name, market, updated_at)
            VALUES
            ('00126380', '005930', '삼성전자', 'KOSPI', CURRENT_TIMESTAMP)
        """))
    with engine.connect() as connection:
        connection.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
        connection.commit()
    engine.dispose()
    monkeypatch.setattr(engine_module, "engine", engine)

    tracked_paths = (
        database_path,
        Path(f"{database_path}-wal"),
        Path(f"{database_path}-shm"),
    )
    before = {
        path: (path.stat().st_size, path.stat().st_mtime_ns)
        for path in tracked_paths
        if path.exists()
    }

    corp_code, subject, error = _resolve_dcf_company_exact("00126380")

    after = {
        path: (path.stat().st_size, path.stat().st_mtime_ns)
        for path in tracked_paths
        if path.exists()
    }
    assert error is None
    assert corp_code == "00126380"
    assert subject["corp_name"] == "삼성전자"
    assert after == before


def test_dcf_public_facade_contains_missing_db_without_creating_files(
    tmp_path,
    monkeypatch,
):
    from sqlalchemy import create_engine

    import kreports.db.engine as engine_module
    from kreports.analysis.financial_analysis import build_dcf_model_pack

    missing_path = tmp_path / "missing-runtime.db"
    monkeypatch.setattr(
        engine_module,
        "engine",
        create_engine(f"sqlite:///{missing_path}"),
    )

    result = build_dcf_model_pack("00126380", 2024)

    assert result["error_code"] == "dcf_source_unavailable"
    assert result["data_quality"]["status"] == "missing"
    assert result["data_quality"]["limitations"] == [
        "runtime_db_unavailable"
    ]
    assert not missing_path.exists()
    assert not Path(f"{missing_path}-wal").exists()
    assert not Path(f"{missing_path}-shm").exists()


def test_dcf_public_facade_contains_pre_rebuild_schema_without_writes(
    tmp_path,
    monkeypatch,
):
    from sqlalchemy import create_engine, text

    import kreports.db.engine as engine_module
    from kreports.analysis.financial_analysis import build_dcf_model_pack

    database_path = tmp_path / "pre-rebuild.db"
    engine = create_engine(f"sqlite:///{database_path}")
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE companies (
                corp_code TEXT PRIMARY KEY,
                stock_code TEXT,
                corp_name TEXT,
                market TEXT,
                induty_code TEXT
            )
        """))
        connection.execute(text("""
            INSERT INTO companies
            VALUES ('00126380', '005930', '삼성전자', 'KOSPI', '26110')
        """))
    engine.dispose()
    monkeypatch.setattr(engine_module, "engine", engine)
    before = (
        database_path.stat().st_size,
        database_path.stat().st_mtime_ns,
        database_path.read_bytes(),
    )

    result = build_dcf_model_pack("00126380", 2024)

    after = (
        database_path.stat().st_size,
        database_path.stat().st_mtime_ns,
        database_path.read_bytes(),
    )
    assert result["error_code"] == "dcf_source_unavailable"
    assert result["data_quality"]["limitations"] == [
        "missing_schema:financial_facts_compact"
    ]
    assert after == before
    assert not Path(f"{database_path}-wal").exists()
    assert not Path(f"{database_path}-shm").exists()


def test_dcf_public_facade_contains_identity_query_failure(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, text

    import kreports.db.engine as engine_module
    from kreports.analysis.financial_analysis import build_dcf_model_pack

    database_path = tmp_path / "missing-identity-schema.db"
    engine = create_engine(f"sqlite:///{database_path}")
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE financial_facts_compact (
                corp_code TEXT, bsns_year INTEGER, fs_div TEXT,
                metric_key TEXT, metric_name TEXT, amount TEXT,
                source_account_id TEXT, source_account_nm TEXT,
                fetched_at TEXT
            )
        """))
    engine.dispose()
    monkeypatch.setattr(engine_module, "engine", engine)

    result = build_dcf_model_pack("00126380", 2024)

    assert result["error_code"] == "dcf_source_unavailable"
    assert result["data_quality"]["limitations"] == [
        "identity_query_unavailable:OperationalError"
    ]


def test_dcf_public_facade_contains_nonempty_wal_without_writes(
    tmp_path,
    monkeypatch,
):
    from sqlalchemy import create_engine

    import kreports.db.engine as engine_module
    from kreports.analysis.financial_analysis import build_dcf_model_pack

    database_path = tmp_path / "uncheckpointed.db"
    engine = create_engine(f"sqlite:///{database_path}")
    with engine.begin():
        pass
    engine.dispose()
    wal_path = Path(f"{database_path}-wal")
    wal_path.write_bytes(b"uncheckpointed")
    before = {
        path: (path.stat().st_size, path.stat().st_mtime_ns, path.read_bytes())
        for path in (database_path, wal_path)
    }
    monkeypatch.setattr(engine_module, "engine", engine)

    result = build_dcf_model_pack("00126380", 2024)

    after = {
        path: (path.stat().st_size, path.stat().st_mtime_ns, path.read_bytes())
        for path in (database_path, wal_path)
    }
    assert result["error_code"] == "dcf_source_unavailable"
    assert result["data_quality"]["limitations"] == [
        "runtime_db_unavailable:uncheckpointed_wal"
    ]
    assert after == before


def test_dcf_public_facade_bounds_direct_company_error_payload():
    from kreports.analysis.financial_analysis import build_dcf_model_pack

    result = build_dcf_model_pack("회" * 201, 2024)

    assert "200자" in result["error"]
    assert result["company"] == "회" * 200
