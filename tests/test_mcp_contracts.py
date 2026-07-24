import pytest


def sample_result() -> dict:
    return {
        "verdict": "conditional",
        "confirmed_facts": [{
            "statement": "핵심감사사항 본문이 확인되었습니다.",
            "source": {"rcept_no": "20250301000001", "source_table": "report_sections"},
        }],
        "analysis": [{"statement": "수익인식 관련 위험 단서입니다.", "basis": "kam"}],
        "data_quality": {
            "status": "usable",
            "schema_version": "20260711_01_quality_contract",
            "dataset_version": "fixture-v1",
        },
        "limitations": ["감사 결론이 아닌 공시 근거 요약입니다."],
    }


def empty_cache_result() -> dict:
    return {
        "verdict": "insufficient_data",
        "confirmed_facts": [],
        "analysis": [],
        "data_quality": {
            "status": "missing",
            "schema_version": "20260711_01_quality_contract",
            "dataset_version": "fixture-v1",
        },
        "limitations": ["캐시 부재는 공시 본문 부재를 의미하지 않습니다."],
    }


def test_professional_answer_separates_fact_analysis_and_gap():
    from kreports.mcp.contracts import build_answer_envelope

    envelope = build_answer_envelope("get_audit_report_sections", sample_result())

    assert envelope.schema_version == "1.0"
    assert envelope.confirmed_facts
    assert envelope.analysis
    assert envelope.data_quality.status in {"usable", "limited", "missing", "error"}
    assert envelope.evidence[0].source_url.startswith("https://dart.fss.or.kr/")
    assert envelope.warnings


def test_missing_cache_does_not_claim_filing_absence():
    from kreports.mcp.contracts import build_answer_envelope

    envelope = build_answer_envelope("search_audit_report_matters", empty_cache_result())

    assert envelope.data_quality.status == "missing"
    assert all("공시에 없음" not in warning for warning in envelope.warnings)


def test_adapter_rejects_unknown_quality_status_instead_of_relabeling_it_missing():
    from kreports.mcp.contracts import build_answer_envelope

    with pytest.raises(ValueError, match="unsupported data quality status"):
        build_answer_envelope("get_audit_report_sections", {
            "data_quality": {"status": "cache_missing"},
        })


def test_explicit_tool_error_remains_an_error_quality_status():
    from kreports.mcp.contracts import build_answer_envelope

    envelope = build_answer_envelope("get_audit_report_sections", {"error": "database unavailable"})

    assert envelope.data_quality.status == "error"
    assert "database unavailable" in envelope.warnings[0]


def test_empty_legacy_result_without_quality_is_missing_not_implicitly_usable():
    from kreports.mcp.contracts import build_answer_envelope

    envelope = build_answer_envelope("get_accounting_policy", {
        "has_data": False,
        "history": [],
        "confirmed_facts": [],
    })

    assert envelope.data_quality.status == "missing"
    assert any("원 공시 부재" in warning for warning in envelope.warnings)
