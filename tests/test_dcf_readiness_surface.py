from copy import deepcopy
import math

from kreports.db.models import AuditMatterItem


def test_candidate_history_is_usable_while_valuation_readiness_is_blocked(monkeypatch):
    """Removing the analyst/source blockers must not erase usable history."""
    from kreports.analysis import dcf_inputs

    monkeypatch.setattr(dcf_inputs, "_financial_series", lambda *_args, **_kwargs: [
        {
            "bsns_year": 2021, "revenue": 100, "operating_profit": 10,
            "net_income": 8, "operating_cf": 9, "tax_expense": 2,
            "purchase_ppe": 4, "purchase_intangible_assets": 1,
        },
        {
            "bsns_year": 2022, "revenue": 110, "operating_profit": 12,
            "net_income": 9, "operating_cf": 10, "tax_expense": 2,
            "purchase_ppe": 5, "purchase_intangible_assets": 1,
        },
        {
            "bsns_year": 2023, "revenue": 121, "operating_profit": 13,
            "net_income": 10, "operating_cf": 11, "tax_expense": 3,
            "purchase_ppe": 5, "purchase_intangible_assets": 1,
        },
        {
            "bsns_year": 2024, "revenue": 133, "operating_profit": 15,
            "net_income": 12, "operating_cf": 13, "tax_expense": 3,
            "purchase_ppe": 6, "purchase_intangible_assets": 1,
        },
        {
            "bsns_year": 2025, "revenue": 146, "operating_profit": 16,
            "net_income": 13, "operating_cf": 14, "tax_expense": 3,
            "purchase_ppe": 6, "purchase_intangible_assets": 1,
        },
    ])

    result = dcf_inputs.dcf_input_candidates("001", start_year=2021, end_year=2025)

    assert result["candidate_status"] == "usable"
    assert result["valuation_readiness"] == "blocked"
    assert result["data_quality"]["status"] == "usable"
    assert result["valuation_blockers"] == [
        {
            "field": "working_capital_delta",
            "kind": "source_fact_missing",
            "impact": "운전자본 증감에 따른 UFCF 계산 불가",
            "owner": "filing_data",
            "next_action": "기준연도 CFS 운전자본 관련 계정의 전년 대비 증감을 확인하세요.",
        },
        {
            "field": "wacc",
            "kind": "analyst_input_missing",
            "impact": "기업가치 할인 계산 불가",
            "owner": "analyst",
            "next_action": "자본구조·무위험수익률·베타·시장위험프리미엄으로 WACC를 산정하세요.",
        },
        {
            "field": "terminal_growth",
            "kind": "analyst_input_missing",
            "impact": "터미널가치 계산 불가",
            "owner": "analyst",
            "next_action": "장기 거시성장률과 사업 지속가능성을 근거로 영구성장률을 정하세요.",
        },
    ]


def test_tax_outliers_are_retained_but_excluded_from_candidate_median(monkeypatch):
    """Changing the median to include negative tax observations is a valuation bug."""
    from kreports.analysis import dcf_inputs

    monkeypatch.setattr(dcf_inputs, "_financial_series", lambda *_args, **_kwargs: [
        {
            "bsns_year": 2022, "revenue": 100, "operating_profit": 10,
            "net_income": 10, "operating_cf": 10, "tax_expense": -2,
            "purchase_ppe": 2, "purchase_intangible_assets": 0,
        },
        {
            "bsns_year": 2023, "revenue": 110, "operating_profit": 11,
            "net_income": 10, "operating_cf": 10, "tax_expense": 2,
            "purchase_ppe": 2, "purchase_intangible_assets": 0,
        },
        {
            "bsns_year": 2024, "revenue": 120, "operating_profit": 12,
            "net_income": -3, "operating_cf": 2, "tax_expense": 4,
            "purchase_ppe": 2, "purchase_intangible_assets": 0,
        },
    ])

    tax = dcf_inputs.dcf_input_candidates("001", start_year=2022, end_year=2024)["candidate_assumptions"]["tax_rate"]

    assert tax["value"] == 0.1667
    assert tax["included_observation_count"] == 1
    assert tax["excluded_observation_count"] == 2
    assert [observation["outlier"] for observation in tax["observations"]] == [True, False, True]
    assert tax["outlier_policy"] == "negative_or_greater_than_one_excluded_from_median"


def test_missing_candidate_history_has_source_and_analyst_readiness_blockers(monkeypatch):
    """A cache miss is not a valuation-ready result merely because no median is shown."""
    from kreports.analysis import dcf_inputs

    monkeypatch.setattr(dcf_inputs, "_financial_series", lambda *_args, **_kwargs: [])

    result = dcf_inputs.dcf_input_candidates("001", start_year=2021, end_year=2025)

    assert result["candidate_status"] == "missing"
    assert result["valuation_readiness"] == "blocked"
    assert [blocker["field"] for blocker in result["valuation_blockers"]] == [
        "revenue",
        "operating_profit",
        "profit_loss",
        "operating_cash_flow",
        "tax_rate",
        "capex",
        "working_capital_delta",
        "wacc",
        "terminal_growth",
    ]


def test_unavailable_dcf_model_suppresses_value_bridge_and_chart():
    """A None enterprise value must never be rendered as a valuation result."""
    from kreports.mcp.answer_pack import build_answer_pack
    from kreports.mcp.renderers import render_answer

    result = {
        "subject": {"corp_name": "A"},
        "status": "partial_model",
        "enterprise_value": None,
        "equity_value": None,
        "actuals": [{"metric_key": "revenue", "amount": "100", "year": 2024, "fs_div": "CFS"}],
        "assumptions": [],
        "missing_inputs": ["accounts[2024,CFS]:cash_and_equivalents"],
        "valuation_bridge": {"enterprise_value": None, "equity_value": None},
        "data_quality": {"status": "limited"},
    }

    pack = build_answer_pack("build_dcf_model_pack", result)
    answer = render_answer("build_dcf_model_pack", result)

    assert pack is not None
    assert pack["summary"]["domain_status"] == "unavailable"
    assert {table["id"] for table in pack["tables"]}.isdisjoint({"dcf_valuation_bridge", "dcf_sensitivity"})
    assert not pack["charts"]
    assert answer.startswith("산출 불가: 필수 입력 또는 공시 실제값이 부족하여 기업가치를 계산하지 않았습니다.")
    assert "기업가치: None" not in answer


def test_dcf_candidate_public_answer_and_pack_keep_readiness_separate():
    """A renderer that opens with a value conclusion hides the blocked review step."""
    from kreports.mcp.answer_pack import build_answer_pack
    from kreports.mcp.renderers import render_answer

    result = {
        "subject": {"corp_name": "A"},
        "historical_actuals": [{"year": 2024, "revenue": 100, "operating_profit": 10, "operating_cf": 8}],
        "candidate_assumptions": {"revenue_growth": {"value": 0.1, "basis": "historical_median"}},
        "candidate_status": "usable",
        "valuation_readiness": "blocked",
        "valuation_blockers": [{
            "field": "wacc", "kind": "analyst_input_missing",
            "impact": "기업가치 할인 계산 불가", "owner": "analyst",
            "next_action": "자본구조·무위험수익률·베타·시장위험프리미엄으로 WACC를 산정하세요.",
        }],
        "data_quality": {"status": "usable"},
    }

    answer = render_answer("get_dcf_input_candidates", result)
    pack = build_answer_pack("get_dcf_input_candidates", result)

    assert answer.startswith("DCF 입력 후보 상태: usable\n가치평가 준비도: blocked")
    assert pack is not None
    assert pack["summary"]["domain_status"] == "blocked"
    assert any(table["id"] == "valuation_blockers" for table in pack["tables"])


def test_enriched_dcf_candidate_keeps_public_readiness_and_input_immutable():
    """Enrichment must not reclassify candidates or mutate the raw handler result."""
    from kreports.mcp.tools import _attach_meta

    raw = {
        "subject": {"corp_name": "A"},
        "historical_actuals": [{"year": 2024, "revenue": 100, "operating_profit": 10, "operating_cf": 8}],
        "candidate_assumptions": {"revenue_growth": {"value": 0.1, "basis": "historical_median"}},
        "candidate_status": "usable",
        "valuation_readiness": "blocked",
        "valuation_blockers": [{
            "field": "wacc", "kind": "analyst_input_missing",
            "impact": "기업가치 할인 계산 불가", "owner": "analyst",
            "next_action": "자본구조·무위험수익률·베타·시장위험프리미엄으로 WACC를 산정하세요.",
        }],
        "data_quality": {"status": "usable"},
    }
    before = deepcopy(raw)

    out = _attach_meta("get_dcf_input_candidates", raw)

    assert raw == before
    assert out["answer"].startswith("DCF 입력 후보 상태: usable\n가치평가 준비도: blocked")
    assert out["answer_pack"]["summary"]["domain_status"] == "blocked"
    assert any(table["id"] == "valuation_blockers" for table in out["answer_pack"]["tables"])


def test_enterprise_value_none_overrides_stale_calculated_public_fields():
    """A caller-supplied status must not resurrect a valuation with no EV."""
    from kreports.mcp.answer_pack import build_answer_pack
    from kreports.mcp.contracts import enrich_answer_response, normalize_answer_result
    from kreports.mcp.renderers import render_answer
    from kreports.mcp.resources import read_resource

    stale_value = "999999999"
    raw = {
        "subject": {"corp_name": "A"},
        "status": "partial_model",
        "enterprise_value": None,
        "equity_value": stale_value,
        "calculation_status": "calculated",
        "domain_verdict": "reviewable_model",
        "actuals": [{
            "metric_key": "revenue", "amount": "100", "unit": "KRW",
            "year": 2024, "fs_div": "CFS",
        }],
        "assumptions": [{
            "key": "wacc", "value": None, "unit": "ratio",
            "basis": "analyst_input",
        }],
        "missing_inputs": ["cash_and_equivalents"],
        "missing_accounts": [{
            "field": "cash_and_equivalents", "year": 2024,
            "fs_div": "CFS", "basis": "requested_dcf_source_actual",
        }],
        "valuation_bridge": {
            "enterprise_value": stale_value,
            "equity_value": stale_value,
        },
        "sensitivity": [{
            "wacc": "0.10", "terminal_growth": "0.03",
            "status": "valid", "enterprise_value": stale_value,
        }],
        "tables": [{
            "id": "dcf_valuation_bridge",
            "rows": [{"enterprise_value": stale_value}],
        }],
        "charts": [{
            "id": "dcf_sensitivity_matrix",
            "rows": [{"enterprise_value": stale_value}],
        }],
        "data_quality": {"status": "limited"},
    }
    before = deepcopy(raw)

    normalized = normalize_answer_result("build_dcf_model_pack", raw)
    direct_pack = build_answer_pack("build_dcf_model_pack", raw)
    direct_answer = render_answer("build_dcf_model_pack", raw)
    enriched = enrich_answer_response("build_dcf_model_pack", raw)

    assert raw == before
    assert normalized["calculation_status"] == "unavailable"
    assert normalized["domain_verdict"] == "calculation_unavailable"
    assert "valuation_bridge" not in normalized
    assert "sensitivity" not in normalized
    assert "tables" not in normalized
    assert "charts" not in normalized
    assert normalized["equity_value"] is None
    assert direct_pack is not None
    assert enriched["answer_pack"] is not None
    for pack in (direct_pack, enriched["answer_pack"]):
        assert pack["summary"]["domain_status"] == "unavailable"
        assert {table["id"] for table in pack["tables"]}.isdisjoint({
            "dcf_valuation_bridge", "dcf_sensitivity",
        })
        assert not pack["charts"]
        resource = read_resource(pack["resource_uri"])["text"]
        assert stale_value not in resource
        assert "누락 공시 실제값" in resource
    assert direct_answer.startswith("산출 불가:")
    assert enriched["answer"].startswith("산출 불가:")
    assert "누락 공시 실제값" in direct_answer
    assert stale_value not in str(direct_pack)
    assert stale_value not in str(enriched)


def test_dcf_source_error_keeps_safe_readiness_pack_and_quarantines_exception():
    """A source error still needs a DCF-scoped public pack, not raw diagnostics."""
    from kreports.mcp.answer_pack import build_answer_pack
    from kreports.mcp.contracts import enrich_answer_response
    from kreports.mcp.renderers import render_answer
    from kreports.mcp.resources import read_resource

    raw = {
        "error": "OperationalError: SELECT secret_column FROM internal_table",
        "error_code": "dcf_source_unavailable",
        "company": "00126380",
        "base_year": 2024,
        "fs_div": "OFS",
        "enterprise_value": None,
        "calculation_status": "unavailable",
        "domain_verdict": "calculation_unavailable",
        "actuals": [],
        "assumptions": [{
            "key": "wacc", "value": None, "unit": "ratio",
            "basis": "analyst_input",
        }],
        "missing_inputs": ["revenue"],
        "missing_accounts": [{
            "field": "revenue", "year": 2024, "fs_div": "OFS",
            "basis": "requested_dcf_source_actual",
        }],
        "data_quality": {
            "status": "missing",
            "source": "financial_facts_compact",
            "limitations": ["identity_query_unavailable:OperationalError"],
        },
    }

    direct_pack = build_answer_pack("build_dcf_model_pack", raw)
    enriched = enrich_answer_response("build_dcf_model_pack", raw)
    direct_answer = render_answer("build_dcf_model_pack", raw)

    assert direct_pack is not None
    assert enriched["answer_pack"] is not None
    for answer in (direct_answer, enriched["answer"]):
        assert answer.startswith("산출 불가:")
        assert "누락 공시 실제값" in answer
    for pack in (direct_pack, enriched["answer_pack"]):
        assert any(table["id"] == "dcf_missing_accounts" for table in pack["tables"])
        public = str(pack) + read_resource(pack["resource_uri"])["text"]
        assert "OperationalError" not in public
        assert "secret_column" not in public
        assert "internal_table" not in public
        assert "identity_query_unavailable" not in public


def test_missing_dcf_status_is_identical_across_every_public_surface():
    """A remediation table must not silently upgrade a missing model to limited."""
    from kreports.mcp.answer_pack import build_answer_pack
    from kreports.mcp.contracts import (
        build_answer_envelope,
        enrich_answer_response,
        normalize_answer_result,
    )
    from kreports.mcp.renderers import render_answer
    from kreports.mcp.resources import read_resource

    raw = {
        "error": "OperationalError: private database path",
        "error_code": "dcf_source_unavailable",
        "company": "00126380",
        "base_year": 2024,
        "fs_div": "OFS",
        "enterprise_value": None,
        "equity_value": None,
        "calculation_status": "unavailable",
        "domain_verdict": "calculation_unavailable",
        "actuals": [],
        "assumptions": [{
            "key": "wacc", "value": 0.09, "unit": "ratio",
            "basis": "analyst_input",
        }],
        "missing_inputs": ["revenue"],
        "missing_accounts": [{
            "field": "revenue", "year": 2024, "fs_div": "OFS",
            "basis": "requested_dcf_source_actual",
        }],
        "data_quality": {
            "status": "missing",
            "source": "financial_facts_compact",
        },
    }
    before = deepcopy(raw)

    normalized = normalize_answer_result("build_dcf_model_pack", raw)
    envelope = build_answer_envelope("build_dcf_model_pack", raw)
    pack = build_answer_pack("build_dcf_model_pack", raw)
    enriched = enrich_answer_response("build_dcf_model_pack", raw)
    answer = render_answer("build_dcf_model_pack", raw)

    assert raw == before
    assert pack is not None
    assert normalized["data_quality"]["status"] == "missing"
    assert normalized["quality_status"] == "missing"
    assert envelope.verdict == "missing"
    assert envelope.data_quality.status == "missing"
    for public_pack in (pack, enriched["answer_pack"]):
        assert public_pack["status"] == "missing"
        assert public_pack["summary"]["status"] == "missing"
        assert public_pack["summary"]["domain_status"] == "unavailable"
        assert public_pack["data_quality"]["status"] == "missing"
        assert {table["id"] for table in public_pack["tables"]} <= {
            "dcf_actuals",
            "dcf_assumptions",
            "dcf_missing_accounts",
        }
        assert not public_pack["charts"]
        assert not public_pack["diagrams"]
        assert not public_pack["timelines"]
        assert not public_pack["sources"]
        resource = read_resource(public_pack["resource_uri"])["text"]
        assert "시각화 데이터 상태: missing" in resource
    for public_answer in (answer, enriched["answer"]):
        assert "산출 불가:" in public_answer
        assert "- 상태: missing" in public_answer


def test_unavailable_dcf_error_quarantines_stale_evidence_at_public_boundary():
    """A top-level source error must not reuse facts from an earlier DCF result."""
    from kreports.mcp.answer_pack import build_answer_pack
    from kreports.mcp.contracts import (
        build_answer_envelope,
        enrich_answer_response,
        normalize_answer_result,
    )
    from kreports.mcp.renderers import render_answer
    from kreports.mcp.resources import read_resource

    stale_receipt = "20250101009999"
    stale_tokens = {
        "STALE_FACT",
        "STALE_ANALYSIS",
        "STALE_NEXT_CHECK",
        "STALE_RESULT",
    }
    raw = {
        "error": "OperationalError: SELECT private_secret FROM hidden_table",
        "error_code": "dcf_source_unavailable",
        "company": "00126380",
        "base_year": 2024,
        "fs_div": "OFS",
        "actuals": [{"metric_key": "revenue", "amount": "STALE_RESULT"}],
        "assumptions": [{
            "key": "wacc", "value": 0.09, "unit": "ratio",
            "basis": "analyst_input",
        }],
        "missing_inputs": ["revenue"],
        "missing_accounts": [{
            "field": "revenue", "year": 2024, "fs_div": "OFS",
            "basis": "requested_dcf_source_actual",
        }],
        "confirmed_facts": [{
            "statement": "STALE_FACT",
            "source": {"rcept_no": stale_receipt},
        }],
        "analysis": [{"statement": "STALE_ANALYSIS"}],
        "next_checks": ["STALE_NEXT_CHECK"],
        "rcept_no": stale_receipt,
        "parent_rcept_no": stale_receipt,
        "_meta": {"source_rcept_no": stale_receipt},
        "results": [{"value": "STALE_RESULT"}],
        "events": [{"rcept_no": stale_receipt}],
        "history": [{"rcept_no": stale_receipt}],
        "data_quality": {
            "status": "missing",
            "source": "OperationalError:hidden_source",
            "limitations": ["OperationalError:hidden_table"],
        },
    }
    before = deepcopy(raw)

    normalized = normalize_answer_result("build_dcf_model_pack", raw)
    envelope = build_answer_envelope("build_dcf_model_pack", raw)
    pack = build_answer_pack("build_dcf_model_pack", raw)
    enriched = enrich_answer_response("build_dcf_model_pack", raw)
    answer = render_answer("build_dcf_model_pack", raw)

    assert raw == before
    assert normalized["error"] == raw["error"]
    assert normalized["enterprise_value"] is None
    assert normalized["equity_value"] is None
    assert normalized["calculation_status"] == "unavailable"
    assert normalized["domain_verdict"] == "calculation_unavailable"
    assert normalized["actuals"] == []
    for field in (
        "confirmed_facts",
        "analysis",
        "next_checks",
        "rcept_no",
        "parent_rcept_no",
        "_meta",
        "results",
        "events",
        "history",
    ):
        assert field not in normalized
    assert envelope.confirmed_facts == []
    assert envelope.analysis == []
    assert envelope.evidence == []
    assert envelope.next_checks == []
    assert pack is not None
    public = (
        str(envelope.model_dump())
        + str(pack)
        + answer
        + enriched["answer"]
        + str(enriched["answer_pack"])
        + read_resource(pack["resource_uri"])["text"]
    )
    for stale in {*stale_tokens, stale_receipt}:
        assert stale not in public
    for diagnostic in (
        "OperationalError",
        "private_secret",
        "hidden_table",
        "hidden_source",
    ):
        assert diagnostic not in public
    assert any(
        table["id"] == "dcf_assumptions"
        and table["rows"][0]["key"] == "wacc"
        for table in pack["tables"]
    )
    assert any(
        table["id"] == "dcf_missing_accounts"
        for table in pack["tables"]
    )
    assert not pack["sources"]


def test_exact_company_resolution_error_has_canonical_dcf_availability_pack(
    temp_engine,
):
    """Resolution failure has known year/FS context even without a company match."""
    from kreports.analysis.financial_analysis import build_dcf_model_pack
    from kreports.mcp.answer_pack import build_answer_pack
    from kreports.mcp.contracts import enrich_answer_response
    from kreports.mcp.resources import read_resource

    result = build_dcf_model_pack(
        "없는 회사", 2024, fs_div="OFS", wacc=0.09,
    )
    direct_pack = build_answer_pack("build_dcf_model_pack", result)
    enriched = enrich_answer_response("build_dcf_model_pack", result)

    assert result["enterprise_value"] is None
    assert result["calculation_status"] == "unavailable"
    assert result["domain_verdict"] == "calculation_unavailable"
    assert result["base_year"] == 2024
    assert result["fs_div"] == "OFS"
    assert result["missing_accounts"]
    assert result["assumptions"] == [{
        "key": "wacc",
        "value": 0.09,
        "unit": "ratio",
        "basis": "analyst_input",
    }]
    assert all(
        row["year"] == 2024 and row["fs_div"] == "OFS"
        for row in result["missing_accounts"]
    )
    assert direct_pack is not None
    assert enriched["answer_pack"] is not None
    assert enriched["answer"].startswith("산출 불가:")
    resource = read_resource(enriched["answer_pack"]["resource_uri"])["text"]
    assert "누락 공시 실제값" in resource
    assert "OFS" in resource


def test_nonfinite_tax_observations_are_finite_safe_and_block_readiness(monkeypatch):
    """NaN observations must be visible as exclusions, never median inputs."""
    from kreports.analysis import dcf_inputs

    monkeypatch.setattr(dcf_inputs, "_financial_series", lambda *_args, **_kwargs: [
        {
            "bsns_year": year, "revenue": 100 + year, "operating_profit": 10,
            "net_income": 8, "operating_cf": 9, "tax_expense": float("nan"),
            "purchase_ppe": 4, "purchase_intangible_assets": 1,
        }
        for year in range(2021, 2026)
    ])

    result = dcf_inputs.dcf_input_candidates("001", start_year=2021, end_year=2025)
    tax = result["candidate_assumptions"]["tax_rate"]

    assert result["candidate_status"] == "limited"
    assert tax["value"] is None
    assert tax["included_observation_count"] == 0
    assert tax["excluded_observation_count"] == 5
    assert all(observation == {
        "year": year,
        "value": None,
        "outlier": True,
        "exclusion_reason": "nonfinite",
        "raw_value_marker": "nonfinite",
    } for year, observation in zip(range(2021, 2026), tax["observations"]))
    assert any(
        blocker["field"] == "tax_rate"
        and blocker["kind"] == "source_fact_missing"
        for blocker in result["valuation_blockers"]
    )
    def assert_finite_safe(value: object) -> None:
        if isinstance(value, float):
            assert math.isfinite(value)
        elif isinstance(value, dict):
            for nested in value.values():
                assert_finite_safe(nested)
        elif isinstance(value, list):
            for nested in value:
                assert_finite_safe(nested)

    assert_finite_safe(result)


def test_qoe_matters_survive_missing_financial_series_across_public_surfaces(
    temp_engine,
    monkeypatch,
):
    """Audit-report evidence is independently useful when financial facts are absent."""
    from kreports.analysis import investor_quality
    from kreports.db.engine import get_session
    from kreports.mcp.contracts import (
        build_answer_envelope,
        enrich_answer_response,
    )
    from kreports.mcp.resources import read_resource

    monkeypatch.setattr(investor_quality, "engine", temp_engine)
    monkeypatch.setattr(
        investor_quality,
        "_financial_series",
        lambda *_args, **_kwargs: [],
    )
    with get_session() as session:
        session.add(AuditMatterItem(
            rcept_no="20250318001234-01",
            corp_code="001",
            bsns_year=2024,
            matter_type="going_concern",
            matter_text="계속기업 관련 중요한 불확실성",
            severity_hint="high",
            source_type="audit_report",
            section_ordinal=0,
        ))

    result = investor_quality.quality_of_earnings_pack(
        "001", start_year=2024, end_year=2024,
    )
    stale_receipt = "20241231009999"
    result["confirmed_facts"] = [{
        "statement": "latest financial substitution",
        "source": {
            "rcept_no": stale_receipt,
            "source_table": "financial_facts_compact",
        },
    }]
    result["_meta"] = {"source_rcept_no": stale_receipt}
    result["history"] = [{"rcept_no": stale_receipt}]
    result["events"] = [{"rcept_no": stale_receipt}]
    before = deepcopy(result)
    envelope = build_answer_envelope(
        "get_quality_of_earnings_pack", result,
    )
    enriched = enrich_answer_response("get_quality_of_earnings_pack", result)
    pack = enriched["answer_pack"]
    resource = read_resource(pack["resource_uri"])["text"]

    assert result == before
    assert result["metrics"]["years"] == 0
    assert result["data_quality"]["status"] == "limited"
    assert result["audit_matter_summary"]["unique_receipt_count"] == 1
    assert result["audit_matter_summary"]["section_count"] == 1
    summary = next(
        table for table in pack["tables"]
        if table["id"] == "audit_matter_summary"
    )
    assert summary["rows"] == [{
        "unique_receipt_count": 1,
        "section_count": 1,
        "dedupe_basis": "parent_rcept_no + matter_type + normalized_excerpt",
    }]
    assert "20250318001234" in enriched["answer"]
    assert (
        "parent_rcept_no + matter_type + normalized_excerpt"
        in enriched["answer"]
    )
    assert (
        "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250318001234"
        in enriched["answer"]
    )
    assert "20250318001234" in resource
    assert "latest financial" not in enriched["answer"].casefold()
    assert stale_receipt not in enriched["answer"]
    assert stale_receipt not in resource
    assert [item.rcept_no for item in envelope.evidence] == [
        "20250318001234",
    ]
    assert {
        fact["source"]["rcept_no"]
        for fact in envelope.confirmed_facts
    } == {"20250318001234"}
    assert (
        "연결 가능한 공시 접수번호가 현재 결과에 포함되지 않았습니다."
        not in enriched["answer"]
    )
    assert {source["rcept_no"] for source in pack["sources"]} == {
        "20250318001234",
    }


def test_empty_dcf_history_has_one_exact_blocker_per_required_input(monkeypatch):
    """An aggregate cache blocker hides the exact remediation work."""
    from kreports.analysis import dcf_inputs

    monkeypatch.setattr(dcf_inputs, "_financial_series", lambda *_args, **_kwargs: [])

    result = dcf_inputs.dcf_input_candidates(
        "001", start_year=2021, end_year=2025,
    )
    blockers = result["valuation_blockers"]

    assert [blocker["field"] for blocker in blockers] == [
        "revenue",
        "operating_profit",
        "profit_loss",
        "operating_cash_flow",
        "tax_rate",
        "capex",
        "working_capital_delta",
        "wacc",
        "terminal_growth",
    ]
    assert all(
        blocker["kind"] == "source_fact_missing"
        for blocker in blockers[:-2]
    )
    assert all(
        blocker["kind"] == "analyst_input_missing"
        for blocker in blockers[-2:]
    )
    assert not any(
        blocker["field"] == "financial_facts_compact"
        for blocker in blockers
    )


def test_unavailable_model_reports_missing_accounts_with_requested_year_and_basis(
    temp_engine,
    monkeypatch,
):
    """A bare account name lets callers accidentally fill it from another year or FS."""
    from kreports.analysis import dcf_source, financial_analysis
    from kreports.analysis.dcf_model import DcfActualFact
    from kreports.analysis.dcf_source import DcfSourceResult
    from kreports.db.engine import get_session
    from kreports.db.models import Company

    with get_session() as session:
        session.add(Company(corp_code="00126380", corp_name="A", stock_code="005930", market="KOSPI"))
    facts = tuple(
        DcfActualFact(
            metric_key=key, amount=value, unit="KRW", year=2024, fs_div="OFS",
            source_account_id=key, source_account_name=key,
            source_table="financial_facts_compact", fetched_at=None,
        )
        for key, value in (("revenue", 100), ("operating_profit", 10))
    )
    monkeypatch.setattr(dcf_source, "load_dcf_actuals", lambda *_args, **_kwargs: DcfSourceResult(
        status="partial", facts=facts,
        missing_metrics=("cash_and_equivalents",), limitations=("source_partial",),
    ))

    result = financial_analysis.build_dcf_model_pack(
        "005930", 2024, fs_div="OFS", revenue_growth=0.1,
        operating_margin=0.1, tax_rate=0.2, da_to_revenue=0.05,
        capex_to_revenue=0.04, nwc_to_revenue=0.2, wacc=0.1,
        terminal_growth=0.03,
    )

    assert result["enterprise_value"] is None
    assert result["calculation_status"] == "unavailable"
    assert result["domain_verdict"] == "calculation_unavailable"
    assert {tuple(row.items()) for row in result["missing_accounts"]} >= {
        tuple({
            "field": "cash_and_equivalents", "year": 2024, "fs_div": "OFS",
            "basis": "requested_dcf_source_actual",
        }.items()),
    }


def test_qoe_matters_dedupe_parent_receipts_and_keep_receipt_sources(temp_engine, monkeypatch):
    """Collapsing section rows into one count must retain their audit-report receipt."""
    from kreports.analysis import investor_quality
    from kreports.mcp.answer_pack import build_answer_pack
    from kreports.db.engine import get_session

    monkeypatch.setattr(investor_quality, "engine", temp_engine)
    monkeypatch.setattr(investor_quality, "_financial_series", lambda *_args, **_kwargs: [
        {"bsns_year": 2024, "revenue": 100, "operating_profit": 10, "net_income": 8, "operating_cf": 9},
    ])
    with get_session() as session:
        session.add_all([
            AuditMatterItem(
                rcept_no="20250318001234-01", corp_code="001", bsns_year=2024,
                matter_type="going_concern", matter_text="계속기업 관련 중요한 불확실성",
                severity_hint="high", source_type="audit_report", section_ordinal=0,
            ),
            AuditMatterItem(
                rcept_no="20250318001234-02", corp_code="001", bsns_year=2024,
                matter_type="going_concern", matter_text="계속기업 관련 중요한 불확실성",
                severity_hint="high", source_type="audit_report", section_ordinal=1,
            ),
        ])

    result = investor_quality.quality_of_earnings_pack("001", start_year=2024, end_year=2024)
    pack = build_answer_pack("get_quality_of_earnings_pack", result)

    summary = result["audit_matter_summary"]
    assert summary["unique_receipt_count"] == 1
    assert summary["section_count"] == 2
    assert summary["dedupe_basis"] == "parent_rcept_no + matter_type + normalized_excerpt"
    assert summary["groups"][0]["source"]["rcept_no"] == "20250318001234"
    assert pack is not None
    matter_table = next(table for table in pack["tables"] if table["id"] == "audit_matter_groups")
    assert matter_table["rows"][0]["rcept_no"] == "20250318001234"
    assert {source["rcept_no"] for source in pack["sources"]} == {"20250318001234"}
