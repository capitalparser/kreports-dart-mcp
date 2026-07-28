import pytest

from kreports.mcp.contracts import enrich_answer_response


def test_enrichment_uses_one_canonical_status_across_layers():
    out = enrich_answer_response("compare_peer_risk_profile", {
        "verdict": "승인",
        "subject": {"corp_name": "A"},
        "subject_metrics": {"revenue": 100},
        "benchmarks": {"revenue": {"n": 10, "p50": 90}},
        "data_quality": {
            "status": "limited",
            "missing_fields": ["receivables"],
        },
    })

    assert out["data_quality"]["status"] == "limited"
    assert out["domain_verdict"] is None
    assert out["answer_pack"]["summary"]["status"] == "limited"
    assert "판정:\n- limited" in out["answer"]
    assert "승인" not in out["answer"]


def test_empty_upstream_usable_response_is_missing_across_response_and_pack():
    out = enrich_answer_response("get_business_overview", {
        "data_quality": {"status": "usable"},
    })

    assert out["quality_status"] == "missing"
    assert out["data_quality"]["status"] == "missing"
    assert out["answer_pack"]["summary"]["status"] == "missing"
    assert out["answer_pack"]["data_quality"]["status"] == "missing"


def test_analysis_without_public_facts_cannot_keep_upstream_usable_status():
    out = enrich_answer_response("get_business_overview", {
        "analysis": [{"statement": "근거 없는 해석"}],
        "data_quality": {"status": "usable"},
    })

    assert out["quality_status"] != "usable"


def test_arbitrary_metadata_list_cannot_keep_upstream_usable_status():
    from kreports.mcp.contracts import build_answer_envelope
    from kreports.mcp.resources import read_resource

    out = enrich_answer_response("get_business_overview", {
        "labels": ["x"],
        "data_quality": {"status": "usable"},
    })
    envelope = build_answer_envelope("get_business_overview", out)

    assert out["quality_status"] == "missing"
    assert out["data_quality"]["status"] == envelope.verdict == "missing"
    assert out["answer_pack"]["summary"]["status"] == "missing"
    assert out["answer_pack"]["data_quality"]["status"] == "missing"
    resource = read_resource(out["answer_pack"]["resource_uri"])
    assert "missing" in resource["text"]


def test_registered_business_records_keep_a_usable_status():
    out = enrich_answer_response("get_subsidiary_auditors", {
        "subject": {"corp_name": "A"},
        "subsidiaries": [{"name": "B", "qsc_status": "undetermined"}],
        "data_quality": {"status": "usable"},
    })

    assert out["quality_status"] == "usable"
    assert out["answer_pack"]["summary"]["status"] == "usable"


@pytest.mark.parametrize("payload", [
    {"items": ["x"]},
    {"inputs": {"debug": "x"}},
    {"results": {"debug": "x"}},
    {"assumptions": ["x"]},
])
def test_generic_payload_keys_cannot_keep_unknown_or_unrelated_tool_usable(payload):
    from kreports.mcp.contracts import build_answer_envelope
    from kreports.mcp.resources import read_resource

    out = enrich_answer_response("get_business_overview", {
        **payload,
        "data_quality": {"status": "usable"},
    })
    envelope = build_answer_envelope("get_business_overview", out)

    assert out["quality_status"] == "missing"
    assert out["data_quality"]["status"] == envelope.verdict == "missing"
    assert out["answer_pack"]["status"] == "missing"
    assert "missing" in read_resource(out["answer_pack"]["resource_uri"])["text"]


def test_other_tools_registered_key_cannot_be_used_by_this_tool():
    out = enrich_answer_response("get_business_overview", {
        "candidate_assumptions": {"revenue_growth": {"value": 0.1}},
        "data_quality": {"status": "usable"},
    })

    assert out["quality_status"] == "missing"


@pytest.mark.parametrize(("tool_name", "payload"), [
    ("get_subsidiary_auditors", {"subsidiaries": [{"name": "B"}]}),
    ("get_quality_of_earnings_pack", {"metrics": {"years": 3}}),
    ("get_dcf_input_candidates", {"candidate_assumptions": {"revenue_growth": {"value": 0.1}}}),
    ("search_dataset", {"companies": [{"corp_name": "A", "records": [{"year": 2025}]}]}),
])
def test_tool_registered_purpose_payloads_remain_usable(tool_name, payload):
    out = enrich_answer_response(tool_name, {
        **payload,
        "data_quality": {"status": "usable"},
    })

    assert out["quality_status"] == "usable"


def test_purpose_registry_covers_the_public_tool_catalog_exactly():
    from kreports.mcp.catalog import TOOL_CATALOG
    from kreports.mcp.contracts import _TOOL_PURPOSE_PREDICATES

    assert set(_TOOL_PURPOSE_PREDICATES) == set(TOOL_CATALOG)


@pytest.mark.parametrize("renderer_result", [None, ""])
def test_renderer_empty_result_replaces_injected_raw_answer(renderer_result, monkeypatch):
    import kreports.mcp.renderers as renderers

    monkeypatch.setattr(renderers, "render_answer", lambda *_args: renderer_result)
    out = enrich_answer_response("get_business_overview", {
        "answer": "기존 결론: 승인 및 매수, 적정 의견 확정",
        "confirmed_facts": [{
            "statement": "공시로 확인된 사실",
            "source": {"rcept_no": "20250301000001"},
        }],
        "data_quality": {"status": "usable"},
    })

    assert out["answer"].startswith("판정:")
    assert "승인" not in out["answer"]
    assert "매수" not in out["answer"]
    assert "적정 의견" not in out["answer"]


def test_renderer_failure_uses_nonempty_canonical_fallback(monkeypatch):
    import kreports.mcp.renderers as renderers

    def fail(*_args):
        raise RuntimeError("renderer unavailable")

    monkeypatch.setattr(renderers, "render_answer", fail)
    out = enrich_answer_response("get_business_overview", {
        "answer": "적정 의견 확정",
        "confirmed_facts": [{
            "statement": "공시로 확인된 사실",
            "source": {"rcept_no": "20250301000001"},
        }],
        "data_quality": {"status": "usable"},
    })

    assert out["answer"].startswith("판정:")
    assert out["answer"].strip()
    assert "적정 의견" not in out["answer"]


@pytest.mark.parametrize(("tool_name", "verdict", "expected_label"), [
    ("get_dcf_input_candidates", "screen_grade", "입력 후보 선별 결과"),
    ("build_dcf_model_pack", "reviewable_model", "검토 가능한 모델"),
    ("build_dcf_model_pack", "calculation_unavailable", "계산 불가"),
])
def test_allowlisted_domain_verdict_uses_public_korean_label_not_snake_case(
    tool_name, verdict, expected_label,
):
    out = enrich_answer_response(tool_name, {
        "verdict": verdict,
        "inputs": {"wacc": 0.1},
        "data_quality": {"status": "usable"},
    })

    assert f"- {expected_label}" in out["answer"]
    assert verdict not in out["answer"]


def test_enrichment_replaces_injected_professional_verdict_prose():
    for injected in ("승인", "거절", "매수", "매도", "적정 의견 확정"):
        out = enrich_answer_response("get_business_overview", {
            "answer": f"기존 결론: {injected}",
            "verdict": injected,
            "data_quality": {"status": "usable"},
            "sections": {
                "business_overview": {"body_text": "공시된 사업 개요입니다."},
            },
            "confirmed_facts": [{
                "statement": "공시로 확인된 사실",
                "source": {"rcept_no": "20250301000001"},
            }],
        })

        assert injected not in out["answer"]
        assert "판정:\n- usable" in out["answer"]


def test_business_overview_result_stays_usable_when_its_sections_are_present():
    cited = enrich_answer_response("get_business_overview", {
        "sections": {
            "business_overview": {
                "title": "사업 개요",
                "body_text": "반도체 설계와 판매를 수행합니다.",
            },
        },
        "confirmed_facts": [{
            "statement": "공시로 확인된 사실",
            "source": {"rcept_no": "20250301000001"},
        }],
        "data_quality": {"status": "usable"},
    })

    assert cited["quality_status"] == "usable"


def test_cited_cross_tool_fact_cannot_make_business_overview_usable():
    """Re-allowing generic confirmed_facts would make this usable again."""
    from kreports.mcp.contracts import build_answer_envelope
    from kreports.mcp.resources import read_resource

    out = enrich_answer_response("get_business_overview", {
        "confirmed_facts": [{
            "statement": "DCF 할인율 후보는 8.5%입니다.",
            "source": {"rcept_no": "20250301000001"},
            "tool_name": "get_dcf_input_candidates",
        }],
        "data_quality": {"status": "usable"},
    })
    envelope = build_answer_envelope("get_business_overview", out)
    resource = read_resource(out["answer_pack"]["resource_uri"])

    assert out["quality_status"] == out["data_quality"]["status"] == "missing"
    assert envelope.verdict == out["answer_pack"]["summary"]["status"] == "missing"
    assert "missing" in resource["text"]


def test_peer_selection_metadata_without_returned_peers_is_missing():
    """Treating selection_policy as a result would make this usable again."""
    out = enrich_answer_response("select_peer_group", {
        "selection_policy": {
            "criteria": ["industry"],
            "matched_prefix_len": 3,
        },
        "peers": [],
        "returned_peer_count": 0,
        "data_quality": {"status": "usable"},
    })

    assert out["quality_status"] == "missing"
    assert out["answer_pack"]["summary"]["status"] == "missing"


def test_multi_industry_cohort_metadata_without_results_is_missing():
    """Treating cohort_metadata as a result would make this usable again."""
    out = enrich_answer_response("compare_to_industry_multi", {
        "cohort_metadata": {
            "profile": "investor",
            "selected_count": 0,
        },
        "n_peers": 0,
        "results": {},
        "data_quality": {"status": "usable"},
    })

    assert out["quality_status"] == "missing"
    assert out["answer_pack"]["summary"]["status"] == "missing"


def test_multi_industry_zero_observation_stubs_are_missing_across_layers():
    """A populated matrix without peer observations is not a benchmark result."""
    from kreports.mcp.contracts import build_answer_envelope
    from kreports.mcp.resources import read_resource

    out = enrich_answer_response("compare_to_industry_multi", {
        "subject": {"corp_code": "00000001", "corp_name": "A"},
        "n_peers": 3,
        "years": [2025],
        "metrics": ["ROE", "부채비율"],
        "results": {
            2025: {
                "ROE": {
                    "p25": None,
                    "p50": None,
                    "p75": None,
                    "n": 0,
                    "subject_value": None,
                    "percentile": None,
                    "unit": "%",
                },
                "부채비율": {
                    "p25": None,
                    "p50": None,
                    "p75": None,
                    "n": 0,
                    "subject_value": None,
                    "percentile": None,
                    "unit": "%",
                },
            },
        },
        "data_quality": {"status": "usable"},
    })
    envelope = build_answer_envelope("compare_to_industry_multi", out)
    resource = read_resource(out["answer_pack"]["resource_uri"])

    assert out["quality_status"] == out["data_quality"]["status"] == "missing"
    assert envelope.verdict == "missing"
    assert out["answer_pack"]["summary"]["status"] == "missing"
    assert "missing" in resource["text"]


def test_multi_industry_observed_metric_remains_usable_without_outer_quantiles():
    """One observed peer metric is enough when small samples omit P25/P75."""
    out = enrich_answer_response("compare_to_industry_multi", {
        "subject": {"corp_code": "00000001", "corp_name": "A"},
        "n_peers": 1,
        "years": [2025],
        "metrics": ["ROE"],
        "results": {
            2025: {
                "ROE": {
                    "p25": None,
                    "p50": 8.5,
                    "p75": None,
                    "n": 1,
                    "subject_value": None,
                    "percentile": None,
                    "unit": "%",
                },
            },
        },
        "data_quality": {"status": "usable"},
    })

    assert out["quality_status"] == "usable"
    assert out["answer_pack"]["summary"]["status"] == "usable"


@pytest.mark.parametrize("count", [float("nan"), float("inf"), float("-inf")])
def test_multi_industry_nonfinite_observation_counts_are_missing(count):
    """A metric row needs a finite positive peer-observation count."""
    from kreports.mcp.contracts import normalize_answer_result

    out = normalize_answer_result("compare_to_industry_multi", {
        "results": {
            2025: {
                "ROE": {
                    "p25": None,
                    "p50": None,
                    "p75": None,
                    "n": count,
                    "subject_value": None,
                    "percentile": None,
                    "unit": "%",
                },
            },
        },
        "data_quality": {"status": "usable"},
    })

    assert out["quality_status"] == "missing"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1, True),
        (10**1000, True),
        (0, False),
        (float("nan"), False),
        (float("inf"), False),
        (float("-inf"), False),
    ],
)
def test_positive_counts_require_finite_positive_values(value, expected):
    from kreports.mcp.contracts import _positive_number

    assert _positive_number(value) is expected


@pytest.mark.parametrize(("tool_name", "payload"), [
    (
        "compare_peer_audit_fees",
        {
            "subject_metrics": {"corp_code": "00000001"},
            "benchmarks": {
                "audit_fee_m": {"n": 0, "p50": None},
            },
            "peer_count": 0,
        },
    ),
    (
        "get_investor_signals",
        {
            "quality_snapshot": {
                "latest_year": None,
                "avg_roe": None,
                "passed_checks": 0,
            },
            "accounting_risk": {"score": 0, "factors": []},
            "event_counts": {"capital_raise": 0},
            "recent_events": [],
        },
    ),
])
def test_auditor_and_investor_no_data_shapes_cannot_keep_usable(tool_name, payload):
    """A metadata-shaped no-data response is not a purpose result."""
    out = enrich_answer_response(tool_name, {
        **payload,
        "data_quality": {"status": "usable"},
    })

    assert out["quality_status"] == "missing"
    assert out["answer_pack"]["summary"]["status"] == "missing"


def test_investor_year_and_coverage_metadata_without_signals_is_missing():
    """The real no-data shape must not promote a reporting-year marker."""
    from kreports.mcp.contracts import build_answer_envelope
    from kreports.mcp.resources import read_resource

    out = enrich_answer_response("get_investor_signals", {
        "has_data": True,
        "unit": "억원",
        "years": 5,
        "window_days": 365,
        "quality_snapshot": {
            "avg_roe": None,
            "avg_operating_margin": None,
            "avg_revenue_growth": None,
            "latest_debt_ratio": None,
            "latest_fcf": None,
            "latest_cfo_ni": None,
            "checks": {
                "positive_avg_roe": False,
                "positive_avg_op_margin": False,
                "positive_revenue_growth": False,
                "debt_ratio_under_100": False,
                "positive_latest_fcf": False,
                "cfo_covers_net_income": False,
            },
            "passed_checks": 0,
            "total_checks": 6,
            "latest_year": 2025,
        },
        "accounting_risk": {
            "score": 0,
            "verdict": "clean",
            "factors": [],
            "raw_summary": {"has_data": False},
        },
        "recent_events": [],
        "event_counts": {
            "treasury_buy": 0,
            "capital_raise": 0,
            "convertible_bond": 0,
            "merger_split": 0,
            "major_contract": 0,
            "litigation": 0,
            "amendment": 0,
        },
        "takeaways": ["quality_profile_mixed"],
        "data_quality": {"status": "usable"},
    })
    envelope = build_answer_envelope("get_investor_signals", out)
    resource = read_resource(out["answer_pack"]["resource_uri"])

    assert out["quality_status"] == out["data_quality"]["status"] == "missing"
    assert envelope.verdict == "missing"
    assert out["answer_pack"]["summary"]["status"] == "missing"
    assert "missing" in resource["text"]


def test_investor_finite_signal_measure_is_usable_without_year_metadata():
    """An actual quality measure, unlike counts, is a purpose result."""
    out = enrich_answer_response("get_investor_signals", {
        "quality_snapshot": {
            "avg_roe": 12.5,
            "avg_operating_margin": None,
            "avg_revenue_growth": None,
            "latest_debt_ratio": None,
            "latest_fcf": None,
            "latest_cfo_ni": None,
            "checks": {},
            "passed_checks": 1,
            "total_checks": 6,
            "latest_year": None,
        },
        "accounting_risk": {
            "score": 0,
            "verdict": "clean",
            "factors": [],
            "raw_summary": {"has_data": False},
        },
        "recent_events": [],
        "event_counts": {},
        "data_quality": {"status": "usable"},
    })

    assert out["quality_status"] == "usable"


def test_investor_evidenced_risk_output_is_usable_without_year_metadata():
    """A risk summary must carry its own positive source-data evidence."""
    out = enrich_answer_response("get_investor_signals", {
        "quality_snapshot": {
            "avg_roe": None,
            "avg_operating_margin": None,
            "avg_revenue_growth": None,
            "latest_debt_ratio": None,
            "latest_fcf": None,
            "latest_cfo_ni": None,
            "checks": {},
            "passed_checks": 0,
            "total_checks": 6,
            "latest_year": None,
        },
        "accounting_risk": {
            "score": 0,
            "verdict": "clean",
            "factors": [],
            "raw_summary": {"has_data": True},
        },
        "recent_events": [],
        "event_counts": {},
        "data_quality": {"status": "usable"},
    })

    assert out["quality_status"] == "usable"


def test_missing_normalization_rebuilds_a_stale_answer_pack_across_layers():
    """A prebuilt usable pack cannot outlive a missing canonical result."""
    from kreports.mcp.answer_pack import build_answer_pack
    from kreports.mcp.contracts import (
        build_answer_envelope,
        normalize_answer_result,
    )
    from kreports.mcp.resources import read_resource

    stale_pack = build_answer_pack("get_dcf_input_candidates", {
        "candidate_assumptions": {"revenue_growth": {"value": 0.085}},
        "data_quality": {"status": "usable"},
    })
    assert stale_pack is not None
    stale_uri = stale_pack["resource_uri"]
    assert stale_pack["summary"]["status"] == "usable"
    assert "usable" in read_resource(stale_uri)["text"]

    raw = {
        "confirmed_facts": [{
            "statement": "DCF 할인율 후보는 8.5%입니다.",
            "source": {"rcept_no": "20250301000001"},
            "tool_name": "get_dcf_input_candidates",
        }],
        "answer_pack": stale_pack,
        "data_quality": {"status": "usable"},
    }
    normalized = normalize_answer_result("get_business_overview", raw)
    out = enrich_answer_response("get_business_overview", raw)
    envelope = build_answer_envelope("get_business_overview", out)
    resource = read_resource(out["answer_pack"]["resource_uri"])

    assert normalized["quality_status"] == normalized["data_quality"]["status"] == "missing"
    assert out["quality_status"] == out["data_quality"]["status"] == "missing"
    assert envelope.verdict == envelope.data_quality.status == "missing"
    assert envelope.answer_pack["status"] == "missing"
    assert envelope.answer_pack["summary"]["status"] == "missing"
    assert out["answer_pack"]["status"] == "missing"
    assert out["answer_pack"]["summary"]["status"] == "missing"
    assert out["answer_pack"]["data_quality"]["status"] == "missing"
    assert out["answer_pack"]["resource_uri"] != stale_uri
    assert "missing" in resource["text"]
    assert "usable" not in resource["text"]


def test_nonempty_limited_result_never_becomes_missing_availability_pack():
    out = enrich_answer_response("compare_peer_risk_profile", {
        "subject": {"corp_name": "A"},
        "subject_metrics": {"revenue": 100},
        "data_quality": {"status": "limited"},
    })

    assert out["answer_pack"]["summary"]["status"] == "limited"
    assert out["answer_pack"]["status"] == "limited"


def test_missing_and_error_retain_canonical_status_and_cache_disclaimer():
    missing = enrich_answer_response("get_business_overview", {
        "data_quality": {"status": "missing"},
    })
    error = enrich_answer_response("get_business_overview", {"error": "database unavailable"})

    assert missing["quality_status"] == "missing"
    assert "원 공시 부재를 뜻하지 않습니다" in missing["answer"]
    assert error["quality_status"] == "error"


def test_legacy_verdict_is_never_promoted_and_optional_domain_verdict_is_additive():
    from kreports.mcp.contracts import AnswerEnvelopeV1, build_answer_envelope

    legacy = AnswerEnvelopeV1.model_validate({
        "tool_name": "get_business_overview",
        "verdict": "usable",
        "answer": "",
        "confirmed_facts": [],
        "analysis": [],
        "evidence": [],
        "data_quality": {
            "status": "usable", "dataset_version": "v1", "schema_version": "v1",
        },
        "warnings": [],
        "next_checks": [],
    })

    assert legacy.schema_version == "1.0"
    assert legacy.domain_verdict is None
    for legacy_verdict in ("승인", "거절", "매수", "매도", "적정 의견 확정"):
        out = build_answer_envelope("get_business_overview", {
            "verdict": legacy_verdict,
            "data_quality": {"status": "usable"},
        })
        assert out.verdict == "missing"
        assert out.domain_verdict is None


def test_section_statuses_are_preserved_across_envelope_pack_and_visualization():
    from kreports.mcp.contracts import build_answer_envelope

    result = {
        "subject": {"corp_name": "A"},
        "subject_metrics": {"revenue": 100},
        "data_quality": {
            "status": "limited",
            "section_statuses": {
                "receivables": {
                    "status": "limited", "required": True,
                    "applicability": "applicable", "coverage": {"years": 2},
                    "blockers": ["missing_2024"],
                    "sources": [{"source_label": "DART", "source_url": "https://dart.fss.or.kr/"}],
                },
            },
        },
    }
    normalized = enrich_answer_response("compare_peer_risk_profile", result)
    envelope = build_answer_envelope("compare_peer_risk_profile", normalized)
    expected = normalized["data_quality"]["section_statuses"]

    assert envelope.data_quality.model_dump()["section_statuses"] == expected
    assert normalized["answer_pack"]["data_quality"]["section_statuses"] == expected


def test_empty_professional_surface_registries_import_without_claiming_routes():
    from kreports.mcp.professional_surfaces import (
        DETAIL_RENDERERS,
        PACK_BUILDERS,
    )
    from kreports.mcp.professional_surfaces import audit_effort, auditor, investor

    assert PACK_BUILDERS == DETAIL_RENDERERS == {}
    assert audit_effort.PACK_BUILDERS == auditor.PACK_BUILDERS == investor.PACK_BUILDERS == {}
