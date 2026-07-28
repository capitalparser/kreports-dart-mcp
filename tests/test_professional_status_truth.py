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
            "confirmed_facts": [{
                "statement": "공시로 확인된 사실",
                "source": {"rcept_no": "20250301000001"},
            }],
        })

        assert injected not in out["answer"]
        assert "판정:\n- usable" in out["answer"]


def test_cited_complete_result_stays_usable_and_uncited_fact_is_limited():
    cited = enrich_answer_response("get_business_overview", {
        "confirmed_facts": [{
            "statement": "공시로 확인된 사실",
            "source": {"rcept_no": "20250301000001"},
        }],
        "data_quality": {"status": "usable"},
    })
    uncited = enrich_answer_response("get_business_overview", {
        "confirmed_facts": [{"statement": "근거 없는 사실"}],
        "data_quality": {"status": "usable"},
    })

    assert cited["quality_status"] == "usable"
    assert uncited["quality_status"] == "limited"


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
