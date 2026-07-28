"""Public investor decision surfaces retain evidence and uncertainty."""
from __future__ import annotations


def _seed_public_peer_matrix(*, years: range, peer_count: int) -> None:
    from kreports.db.engine import get_session
    from kreports.db.models import Company, Financial

    with get_session() as session:
        for index in range(peer_count + 1):
            corp_code = f"{index + 1:08d}"
            session.add(Company(
                corp_code=corp_code,
                stock_code=f"{index + 1:06d}",
                corp_name="대상" if index == 0 else f"비교 {index}",
                market="KOSPI",
                induty_code="26410",
            ))
            for year in years:
                session.add(Financial(
                    corp_code=corp_code, year=year, quarter=4, fs_div="CFS",
                    revenue=1_000 + index * 20 + year,
                    operating_profit=100 + index * 3,
                    net_income=80 + index * 2,
                    total_assets=2_000 + index * 40,
                    total_debt=800 + index * 10,
                    total_equity=1_200 + index * 30,
                    revenue_yoy=0.03 + index / 1_000,
                    beneish_m_score=-2.5 + index / 100,
                ))


def _append_public_peers(*, years: range, first_peer: int, last_peer: int) -> None:
    from kreports.db.engine import get_session
    from kreports.db.models import Company, Financial

    with get_session() as session:
        for index in range(first_peer, last_peer + 1):
            corp_code = f"{index + 1:08d}"
            session.add(Company(
                corp_code=corp_code, stock_code=f"{index + 1:06d}",
                corp_name=f"비교 {index}", market="KOSPI", induty_code="26410",
            ))
            for year in years:
                session.add(Financial(
                    corp_code=corp_code, year=year, quarter=4, fs_div="CFS",
                    revenue=1_000 + index * 20 + year,
                    operating_profit=100 + index * 3, net_income=80 + index * 2,
                    total_assets=2_000 + index * 40, total_debt=800 + index * 10,
                    total_equity=1_200 + index * 30, revenue_yoy=0.03 + index / 1_000,
                    beneish_m_score=-2.5 + index / 100,
                ))


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


def test_peer_metric_rows_have_denominators_digest_and_provenance_limit(temp_engine):
    from kreports.analysis import investor_peer_evidence

    _seed_public_peer_matrix(years=range(2024, 2025), peer_count=40)
    out = investor_peer_evidence.compare_to_industry_multi_with_evidence(
        company="00000001", metrics=["ROE"], years_back=1,
        fs_div="CFS", fs_strategy="CFS",
    )
    metric = out["results"][2024]["ROE"]

    assert metric["metric_n"] == 40
    assert metric["cohort_n"] == 40
    assert metric["missing_n"] == 0
    assert metric["cohort_digest"]
    assert metric["cohort_digest"] == investor_peer_evidence._cohort_digest(
        [f"{index:08d}" for index in range(2, 42)], year=2024, fs_div="CFS",
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


def test_public_peer_handler_query_count_is_constant_as_matrix_grows(temp_engine):
    from sqlalchemy import event

    from kreports.mcp.handlers.search import handle_compare_to_industry_multi
    from kreports.mcp.input_models import CompareToIndustryMultiInput

    _seed_public_peer_matrix(years=range(2020, 2025), peer_count=5)

    def count_for(*, metrics: list[str], years_back: int) -> int:
        statements: list[str] = []

        def count_statement(*args):
            statements.append(str(args[2]))

        event.listen(temp_engine, "before_cursor_execute", count_statement)
        try:
            out = handle_compare_to_industry_multi(CompareToIndustryMultiInput(
                company="00000001", metrics=metrics, years_back=years_back,
                fs_div="CFS", fs_strategy="CFS",
            ))
        finally:
            event.remove(temp_engine, "before_cursor_execute", count_statement)
        assert out["results"]
        return len(statements)

    narrow_count = count_for(metrics=["ROE"], years_back=1)
    _append_public_peers(
        years=range(2020, 2025), first_peer=6, last_peer=12,
    )
    wide_count = count_for(
        metrics=["영업이익률", "순이익률", "부채비율", "ROE", "ROA", "자기자본비율", "매출성장률", "Beneish_M"],
        years_back=5,
    )

    assert wide_count == narrow_count == 8


def test_public_peer_handler_binds_digest_to_full_selected_cohort_and_fs_basis(temp_engine):
    from kreports.mcp.handlers.search import handle_compare_to_industry_multi
    from kreports.mcp.input_models import CompareToIndustryMultiInput

    _seed_public_peer_matrix(years=range(2023, 2025), peer_count=7)
    out = handle_compare_to_industry_multi(CompareToIndustryMultiInput(
        company="00000001", metrics=["ROE"], years_back=2,
        fs_div="CFS", fs_strategy="CFS",
    ))

    provenance = out["cohort_provenance"]
    assert provenance["fs_div"] == out["fs_div_used"] == "CFS"
    assert provenance["identifier_count"] == provenance["cohort_n"] == 7
    assert provenance["identity_status"] == "complete"
    assert all(
        values["cohort_digest"]
        for metrics in out["results"].values()
        for values in metrics.values()
    )


def test_public_peer_handler_withholds_digest_when_full_cohort_identity_is_not_returned(temp_engine):
    from kreports.mcp.handlers.search import (
        handle_compare_to_industry_multi,
        handle_select_peer_group,
    )
    from kreports.mcp.input_models import (
        CompareToIndustryMultiInput,
        SelectPeerGroupInput,
    )

    _seed_public_peer_matrix(years=range(2024, 2025), peer_count=205)
    out = handle_compare_to_industry_multi(CompareToIndustryMultiInput(
        company="00000001", metrics=["ROE"], years_back=1,
        fs_div="CFS", fs_strategy="CFS",
    ))

    provenance = out["cohort_provenance"]
    assert provenance["cohort_n"] == 205
    assert provenance["identifier_count"] == 200
    assert provenance["identity_status"] == "incomplete"
    assert provenance["digest_status"] == "withheld"
    assert out["results"][2024]["ROE"]["cohort_digest"] is None
    assert out["data_quality"]["status"] == "limited"
    assert any("cohort_identity_incomplete" in item for item in out["data_quality"]["limitations"])

    selection = handle_select_peer_group(SelectPeerGroupInput(
        company="00000001", peer_limit=200, fs_strategy="CFS",
    ))
    selection_provenance = selection["cohort_provenance"]
    assert selection_provenance["cohort_n"] == 205
    assert selection_provenance["identifier_count"] == 200
    assert selection_provenance["identity_status"] == "incomplete"
    assert selection_provenance["cohort_digest"] is None


def test_public_peer_handler_does_not_digest_an_empty_cohort(temp_engine):
    from kreports.mcp.handlers.search import handle_compare_to_industry_multi
    from kreports.mcp.input_models import CompareToIndustryMultiInput

    _seed_public_peer_matrix(years=range(2024, 2025), peer_count=0)
    out = handle_compare_to_industry_multi(CompareToIndustryMultiInput(
        company="00000001", metrics=["ROE"], years_back=1,
        fs_div="CFS", fs_strategy="CFS",
    ))

    provenance = out["cohort_provenance"]
    assert provenance["cohort_n"] == provenance["identifier_count"] == 0
    assert provenance["identity_status"] == "empty"
    assert provenance["digest_status"] == "withheld"
    assert out["results"][2024]["ROE"]["cohort_digest"] is None
    assert out["data_quality"]["status"] == "missing"


def test_public_answers_and_packs_do_not_leak_internal_metric_or_event_keys():
    from kreports.mcp.answer_pack import build_answer_pack
    from kreports.mcp.renderers import render_answer

    peer_result = {
        "subject": {"corp_name": "대상"}, "n_peers": 5, "fs_div_used": "CFS",
        "results": {2024: {"Beneish_M": {
            "subject_value": -2.1, "percentile": 40, "p25": -2.8,
            "p50": -2.3, "p75": -1.9, "n": 5, "metric_n": 5,
            "cohort_n": 5, "missing_n": 0, "cohort_digest": "abc", "unit": "score",
        }}},
        "data_quality": {"status": "limited"},
    }
    event_result = {
        "events": [{"event_date": "2025-01-01", "corp_name": "대상",
                    "event_type": "capital_raise", "event_title": "유상증자 결정"}],
        "total_events": 1, "data_quality": {"status": "usable"},
    }

    peer_answer = render_answer("compare_to_industry_multi", peer_result)
    event_answer = render_answer("search_disclosure_events", event_result)
    peer_pack = build_answer_pack("compare_to_industry_multi", peer_result)
    event_pack = build_answer_pack("search_disclosure_events", event_result)
    rendered = "\n".join([peer_answer, event_answer, str(peer_pack), str(event_pack)])

    assert "베니시 M 점수" in rendered
    assert "유상증자" in rendered
    assert "Beneish_M" not in rendered
    assert "capital_raise" not in rendered
