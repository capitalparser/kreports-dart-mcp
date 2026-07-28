"""Public investor decision surfaces retain evidence and uncertainty."""
from __future__ import annotations


def test_investor_check_keeps_missing_cash_conversion_unknown():
    from kreports.analysis.investor_peer_evidence import evaluate_investor_check

    check = evaluate_investor_check(
        name="잉여현금흐름 흑자",
        value=None,
        predicate=lambda value: value > 0,
        meaning="영업현금흐름이 투자지출을 뒷받침하는지 확인합니다.",
    )

    assert check["status"] == "unknown"
    assert check["value"] is None


def test_investor_signal_coverage_and_supportive_guard(monkeypatch):
    from kreports.analysis import financial_analysis

    monkeypatch.setattr(financial_analysis, "resolve_company_identifier", lambda _: "001")
    monkeypatch.setattr(financial_analysis, "get_company_summary", lambda _: {"corp_name": "대상"})
    monkeypatch.setattr(financial_analysis, "get_financial_snapshot", lambda *args, **kwargs: {
        "rows": [{"연도": 2024, "ROE": 12.0, "영업이익률": 5.0,
                  "매출성장률": 3.0, "부채비율": 50.0,
                  "FCF": None, "CFO_NI": None}],
    })
    monkeypatch.setattr(financial_analysis._queries, "get_risk_summary", lambda _: {"has_data": False})
    monkeypatch.setattr(financial_analysis, "_recent_investor_events", lambda *args: ([], {}))
    monkeypatch.setattr(financial_analysis, "_investor_signal_evidence", lambda *args: {})

    out = financial_analysis.get_investor_signals("001", years=1)
    quality = out["quality_snapshot"]

    assert quality["evaluated_count"] == 4
    assert quality["unknown_count"] == 2
    assert quality["coverage_status"] == "limited"
    assert quality["checks"]["positive_latest_fcf"]["status"] == "unknown"
    assert "quality_profile_supportive" not in out["takeaways"]


def test_financial_snapshot_pack_preserves_five_rows_and_per_year_sources():
    from kreports.mcp.professional_surfaces.investor import PACK_BUILDERS

    result = {
        "subject": {"corp_name": "대상"}, "unit": "억원",
        "rows": [
            {"연도": 2020 + index, "구분": "CFS", "매출액": 100 + index,
             "영업이익": 10, "순이익": 8, "영업CF": 12,
             "매출성장률": 1.0, "영업이익률": 10.0,
             "source": {"rcept_no": f"202{index}0101000001"}}
            for index in range(5)
        ],
        "data_quality": {"status": "usable"},
    }

    pack = PACK_BUILDERS["get_financial_snapshot"](result)
    table = next(table for table in pack["tables"] if table["id"] == "financial_trend")

    assert len(table["rows"]) == 5
    assert all(row["source"] for row in table["rows"])


def test_peer_enrichment_retains_selection_basis_and_coverage(monkeypatch):
    from kreports.analysis import investor_peer_evidence

    monkeypatch.setattr(investor_peer_evidence.peer_benchmarks, "select_peer_group", lambda **_: {
        "subject": {"corp_code": "001", "corp_name": "대상", "induty_code": "123"},
        "selection_policy": {"resolved_year": 2024, "fs_div_used": "CFS", "criteria": ["industry"]},
        "peers": [{"corp_code": "002", "corp_name": "비교", "induty_code": "123",
                   "total_assets": 100, "include_reasons": ["same_ksic_prefix"]}],
        "peer_count": 1,
    })

    out = investor_peer_evidence.select_peer_group_with_evidence(company="001")

    assert out["peer_selection"][0] == {
        "company_name": "비교", "ksic": "123", "scale": 100,
        "include_reason": "same_ksic_prefix",
    }
    assert out["cohort_provenance"]["cohort_digest"]


def test_peer_metric_rows_have_denominators_digest_and_provenance_limit(monkeypatch):
    from kreports.analysis import investor_peer_evidence

    monkeypatch.setattr(investor_peer_evidence.peer_benchmarks, "compare_to_industry_multi", lambda **_: {
        "subject": {"corp_code": "001", "corp_name": "대상"}, "fs_div": "CFS",
        "n_peers": 40, "years": [2024], "metrics": ["ROE"],
        "results": {2024: {"ROE": {"n": 35, "unit": "%", "subject_value": 12.0}}},
    })
    monkeypatch.setattr(investor_peer_evidence.peer_benchmarks, "select_peer_group", lambda **_: {
        "subject": {"corp_code": "001", "corp_name": "대상"},
        "selection_policy": {"resolved_year": 2024, "fs_div_used": "CFS"},
        "peers": [{"corp_code": "003"}, {"corp_code": "002"}],
    })

    out = investor_peer_evidence.compare_to_industry_multi_with_evidence(company="001")
    metric = out["results"][2024]["ROE"]

    assert metric["metric_n"] == 35
    assert metric["cohort_n"] == 40
    assert metric["missing_n"] == 5
    assert metric["cohort_digest"]
    assert metric["cohort_digest"] == investor_peer_evidence._cohort_digest(
        ["002", "003"], year=2024, fs_div="CFS",
        selection_policy=out["cohort_provenance"]["selection_policy"],
    )
    assert out["data_quality"]["status"] == "limited"
    assert out["data_quality"]["limitations"]


def test_cached_event_is_screening_classification_not_confirmed_control_change():
    from kreports.mcp.professional_surfaces.investor import DETAIL_RENDERERS

    text = DETAIL_RENDERERS["search_disclosure_events"]({
        "events": [{"event_date": "2025-01-01", "corp_name": "대상",
                    "event_type": "capital_raise", "event_title": "유상증자",
                    "rcept_no": "20250101000001"}],
        "total_events": 1, "data_quality": {"status": "usable"},
    })

    assert "KReports 스크리닝 분류" in text
    assert "확정된 지배구조 변경" not in text
