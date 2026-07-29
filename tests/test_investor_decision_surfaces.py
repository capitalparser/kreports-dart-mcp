"""Public investor decision surfaces retain evidence and uncertainty."""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import re


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


def _seed_subject_annual_disclosures(*, years: range) -> None:
    from kreports.db.engine import get_session
    from kreports.db.models import Disclosure

    with get_session() as session:
        for index, year in enumerate(years, start=1):
            session.add(Disclosure(
                rcept_no=f"{year + 1}0101{index:06d}",
                corp_code="00000001",
                corp_name="대상",
                disc_date=date(year + 1, 3, 31),
                disc_type="A",
                report_nm=f"사업보고서 ({year}.12)",
                flr_nm="대상",
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
    from kreports.mcp.tools import _attach_meta

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
    assert metric["source"]["rcept_no"] is None
    assert metric["source"]["provenance_status"] == "requested_annual_report_not_cached"
    assert out["data_quality"]["status"] == "limited"
    assert any(
        "연간 재무값의 사업보고서 접수번호를 확인하지 못했습니다" in limitation
        for limitation in out["data_quality"]["limitations"]
    )
    enriched = _attach_meta("compare_to_industry_multi", out)
    assert enriched["data_quality"]["status"] == "limited"
    assert enriched["confirmed_facts"] == []
    assert "연간 재무값의 사업보고서 접수번호를 확인하지 못했습니다" in enriched["answer"]
    assert "subject_annual_source_missing" not in enriched["answer"]


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

    assert wide_count == narrow_count == 9


def test_public_peer_handler_binds_digest_to_full_selected_cohort_and_fs_basis(temp_engine):
    from kreports.mcp.contracts import build_answer_envelope
    from kreports.mcp.handlers.search import handle_compare_to_industry_multi
    from kreports.mcp.input_models import CompareToIndustryMultiInput
    from kreports.mcp.resources import read_resource
    from kreports.mcp.tools import _attach_meta

    _seed_public_peer_matrix(years=range(2023, 2025), peer_count=7)
    _seed_subject_annual_disclosures(years=range(2023, 2025))
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
    assert {
        year: values["source"]["rcept_no"]
        for year, metrics in out["results"].items()
        for values in metrics.values()
    } == {
        2023: "20240101000001",
        2024: "20250101000002",
    }
    enriched = _attach_meta("compare_to_industry_multi", out)
    envelope = build_answer_envelope("compare_to_industry_multi", enriched)
    pack = enriched["answer_pack"]
    resource = read_resource(pack["resource_uri"])
    matrix = next(table for table in pack["tables"] if table["id"] == "industry_metrics")
    assert {row["year"]: row["source"] for row in matrix["rows"]} == {
        2023: "20240101000001",
        2024: "20250101000002",
    }
    expected_receipts = {"20240101000001", "20250101000002"}
    assert len(enriched["confirmed_facts"]) == 2
    assert len(enriched["confirmed_facts"]) <= 5
    assert {
        fact["source"]["rcept_no"]
        for fact in enriched["confirmed_facts"]
    } == expected_receipts
    assert {reference.rcept_no for reference in envelope.evidence} == expected_receipts
    assert all(receipt in enriched["answer"] for receipt in expected_receipts)
    assert all(receipt in resource["text"] for receipt in expected_receipts)
    assert (
        enriched["data_quality"]["status"]
        == envelope.verdict
        == pack["summary"]["status"]
        == pack["data_quality"]["status"]
        == "limited"
    )


def test_public_peer_handler_withholds_digest_when_full_cohort_identity_is_not_returned(temp_engine):
    from kreports.mcp.contracts import build_answer_envelope
    from kreports.mcp.handlers.search import (
        handle_compare_to_industry_multi,
        handle_select_peer_group,
    )
    from kreports.mcp.input_models import (
        CompareToIndustryMultiInput,
        SelectPeerGroupInput,
    )
    from kreports.mcp.resources import read_resource
    from kreports.mcp.tools import _attach_meta

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
    metric = out["results"][2024]["ROE"]
    assert metric["cohort_digest"] is None
    assert metric["aggregate_status"] == "withheld_incomplete_cohort"
    assert metric["selection_truncated_n"] == 5
    assert metric["observed_n"] == 200
    assert metric["metric_n"] is None
    assert metric["missing_n"] is None
    assert metric["percentile"] is None
    assert metric["p25"] is None
    assert metric["p50"] is None
    assert metric["p75"] is None
    assert out["data_quality"]["status"] == "limited"
    assert any(
        "전체 비교군 식별자를 확보하지 못했습니다" in item
        for item in out["data_quality"]["limitations"]
    )

    enriched = _attach_meta("compare_to_industry_multi", out)
    envelope = build_answer_envelope("compare_to_industry_multi", enriched)
    pack = enriched["answer_pack"]
    resource = read_resource(pack["resource_uri"])
    matrix = next(table for table in pack["tables"] if table["id"] == "industry_metrics")
    matrix_row = matrix["rows"][0]
    assert matrix["title"] == "비교군 지표 비교"
    labels = {column["field"]: column["label"] for column in matrix["columns"]}
    assert labels["p25"] == "비교군 P25 값"
    assert labels["p50"] == "비교군 중앙값 P50"
    assert labels["p75"] == "비교군 P75 값"
    assert labels["n"] == "비교군 표본 수(개)"
    for field in ("n", "metric_n", "missing_n", "percentile", "p25", "p50", "p75"):
        assert matrix_row[field] == "집계 보류"
        assert not isinstance(matrix_row[field], (int, float))
    assert matrix_row["cohort_digest"] == "미제공"
    assert matrix_row["aggregate_status"] == "전체 비교군 미확보로 집계 보류"
    assert not pack["charts"]
    public_rendered = "\n".join([enriched["answer"], str(pack), resource["text"]])
    for internal_code in (
        "cohort_identity_incomplete",
        "subject_annual_source_missing",
        "withheld_incomplete_cohort",
    ):
        assert internal_code not in public_rendered
    machine_limitation = re.compile(
        r"^[a-z][a-z0-9_]*(?::[a-z0-9_,.-]+)+$", re.ASCII
    )
    public_limitations = [
        *(pack.get("limitations") or []),
        *((pack.get("data_quality") or {}).get("limitations") or []),
    ]
    assert public_limitations
    assert all(re.search(r"[가-힣]", limitation) for limitation in public_limitations)
    assert not any(machine_limitation.fullmatch(limitation) for limitation in public_limitations)
    assert not re.search(
        r"\b[a-z][a-z0-9_]*_suppressed:[a-z0-9_,.-]+\b",
        public_rendered,
    )
    visible_text = "\n".join([enriched["answer"], resource["text"]])
    visible_labels = " ".join(column["label"] for column in matrix["columns"])
    assert "Cohort" not in visible_text
    assert "cohort" not in visible_text
    assert "Cohort" not in visible_labels
    assert "cohort" not in visible_labels
    assert "| 연도 | 지표 | 대상회사 | 백분위 | P25 | P50 | P75 | 비교군 표본 수 |" in enriched["answer"]
    assert "Peer 수" not in enriched["answer"]
    assert "None" not in enriched["answer"]
    assert (
        enriched["data_quality"]["status"]
        == envelope.verdict
        == pack["summary"]["status"]
        == pack["data_quality"]["status"]
        == "limited"
    )

    selection = handle_select_peer_group(SelectPeerGroupInput(
        company="00000001", peer_limit=200, fs_strategy="CFS",
    ))
    selection_provenance = selection["cohort_provenance"]
    assert selection_provenance["cohort_n"] == 205
    assert selection_provenance["identifier_count"] == 200
    assert selection_provenance["identity_status"] == "incomplete"
    assert selection_provenance["cohort_digest"] is None
    selection_enriched = _attach_meta("select_peer_group", selection)
    selection_resource = read_resource(
        selection_enriched["answer_pack"]["resource_uri"]
    )
    selection_rendered = "\n".join([
        selection_enriched["answer"],
        str(selection_enriched["answer_pack"]),
        selection_resource["text"],
    ])
    assert "전체 비교군 식별자를 확보하지 못했습니다" in selection_rendered
    assert "cohort_identity_incomplete" not in selection_rendered
    assert "Cohort" not in selection_enriched["answer"]
    assert "cohort" not in selection_enriched["answer"]


def _public_peer_limitations(limitations: list[str]) -> tuple[str, dict, str]:
    from kreports.mcp.resources import read_resource
    from kreports.mcp.tools import _attach_meta

    out = _attach_meta("compare_to_industry_multi", {
        "subject": {"corp_name": "대상"},
        "metrics": ["ROE"],
        "results": {2024: {"ROE": {
            "subject_value": 0.12,
            "percentile": 70,
            "p25": 0.05,
            "p50": 0.10,
            "p75": 0.15,
            "n": 30,
            "unit": "ratio",
        }}},
        "data_quality": {"status": "limited", "limitations": limitations},
    })
    pack = out["answer_pack"]
    resource = read_resource(pack["resource_uri"])
    return out["answer"], pack, resource["text"]


def test_peer_limitation_localization_preserves_english_prose_across_public_surfaces():
    prose = "Peer receipt provenance is unavailable."

    answer, pack, resource = _public_peer_limitations([prose])

    assert prose in answer
    assert prose in str(pack)
    assert prose in resource


def test_peer_limitation_localization_strips_machine_prefix_across_public_surfaces():
    limitation = "cohort_identity_incomplete: 전체 비교군 확인 필요"
    nested_machine_code = "cohort_identity_incomplete: peer_identity_missing"

    answer, pack, resource = _public_peer_limitations([
        limitation,
        nested_machine_code,
    ])

    for public_surface in (answer, str(pack), resource):
        assert "전체 비교군 확인 필요" in public_surface
        assert "cohort_identity_incomplete" not in public_surface
        assert "peer_identity_missing" not in public_surface
        assert "세부 데이터 제한 사항이 있어 추가 확인이 필요합니다." in public_surface


def test_peer_limitation_localization_does_not_expose_nested_machine_code_in_direct_answer():
    from kreports.mcp.renderers import render_answer

    answer = render_answer("compare_to_industry_multi", {
        "subject": {"corp_name": "대상"},
        "metrics": ["ROE"],
        "results": {2024: {"ROE": {
            "subject_value": 0.12,
            "percentile": 70,
            "p25": 0.05,
            "p50": 0.10,
            "p75": 0.15,
            "n": 30,
            "unit": "ratio",
        }}},
        "data_quality": {
            "status": "limited",
            "limitations": [
                "cohort_identity_incomplete: peer_identity_missing",
            ],
        },
    })

    assert "peer_identity_missing" not in answer
    assert "세부 데이터 제한 사항이 있어 추가 확인이 필요합니다." in answer


def test_missing_peer_pack_strips_machine_prefix_before_resource_rendering():
    from kreports.mcp.answer_pack import build_answer_pack
    from kreports.mcp.resources import render_visualization_resource

    limitation = "cohort_identity_incomplete: 전체 비교군 확인 필요"
    pack = build_answer_pack("compare_to_industry_multi", {
        "data_quality": {"status": "missing", "limitations": [limitation]},
    })
    resource = render_visualization_resource(pack)

    for public_surface in (str(pack), resource["text"]):
        assert "전체 비교군 확인 필요" in public_surface
        assert "cohort_identity_incomplete" not in public_surface


def test_peer_limitation_publication_handles_mixed_case_and_unspaced_prefixes():
    from kreports.mcp.answer_pack import build_answer_pack
    from kreports.mcp.resources import render_visualization_resource

    variants = [
        "Identity_Query_Unavailable:OperationalError",
        "cohort_identity_incomplete:전체 비교군 확인 필요",
    ]
    answer, pack, resource = _public_peer_limitations(variants)
    direct_pack = build_answer_pack("compare_to_industry_multi", {
        "subject": {"corp_name": "대상"},
        "data_quality": {"status": "missing", "limitations": variants},
    })

    for public_surface in (
        answer, str(pack), resource,
        str(direct_pack), render_visualization_resource(direct_pack)["text"],
    ):
        assert "Identity_Query_Unavailable" not in public_surface
        assert "identity_query_unavailable" not in public_surface
        assert "OperationalError" not in public_surface
        assert "cohort_identity_incomplete" not in public_surface
        assert "전체 비교군 확인 필요" in public_surface
        assert "세부 데이터 제한 사항이 있어 추가 확인이 필요합니다." in public_surface


def test_peer_canonical_normalization_publicizes_promoted_coverage_and_error_codes():
    from kreports.mcp.answer_pack import build_answer_pack
    from kreports.mcp.contracts import (
        build_answer_envelope,
        enrich_answer_response,
        normalize_answer_result,
    )
    from kreports.mcp.renderers import render_answer
    from kreports.mcp.resources import read_resource

    base = {
        "subject": {"corp_name": "대상"},
        "metrics": ["ROE"],
        "results": {2024: {"ROE": {
            "subject_value": 0.12, "percentile": 70, "p25": 0.05,
            "p50": 0.10, "p75": 0.15, "n": 30, "unit": "ratio",
        }}},
        "data_quality": {
            "status": "limited",
            "coverage_note": "cohort_identity_incomplete:전체 비교군 확인 필요",
        },
    }
    raw_with_error = {
        **base,
        "data_quality": {"status": "limited"},
        "error": "identity_query_unavailable:OperationalError",
    }

    for raw, expected_limitation in (
        (base, "전체 비교군 확인 필요"),
        (raw_with_error, "세부 데이터 제한 사항이 있어 추가 확인이 필요합니다."),
    ):
        before = deepcopy(raw)
        normalized = normalize_answer_result("compare_to_industry_multi", raw)
        envelope = build_answer_envelope("compare_to_industry_multi", raw)
        rendered = render_answer("compare_to_industry_multi", raw)

        assert raw == before
        assert normalized["data_quality"]["status"] == envelope.verdict
        assert normalized["data_quality"]["limitations"] == envelope.data_quality.limitations
        public_text = "\n".join([
            str(normalized["data_quality"]), str(envelope.data_quality), rendered,
        ])
        assert "cohort_identity_incomplete" not in public_text
        assert "identity_query_unavailable" not in public_text
        assert "OperationalError" not in public_text
        assert expected_limitation in public_text

    direct_pack = build_answer_pack("compare_to_industry_multi", base)
    enriched = enrich_answer_response("compare_to_industry_multi", base)
    resource = read_resource(enriched["answer_pack"]["resource_uri"])
    assert direct_pack["summary"]["status"] == "limited"
    assert direct_pack["data_quality"]["limitations"] == enriched["data_quality"]["limitations"]
    assert enriched["data_quality"]["status"] == "limited"
    assert "cohort_identity_incomplete" not in str(enriched["answer_pack"])
    assert "cohort_identity_incomplete" not in resource["text"]
    assert "전체 비교군 확인 필요" in enriched["answer"]


def test_peer_error_quarantines_raw_exception_and_stale_evidence_across_public_surfaces():
    from kreports.mcp.answer_pack import build_answer_pack
    from kreports.mcp.contracts import (
        build_answer_envelope,
        normalize_answer_result,
    )
    from kreports.mcp.renderers import render_answer
    from kreports.mcp.resources import read_resource
    from kreports.mcp.tools import _attach_meta

    safe_limitation = "세부 데이터 제한 사항이 있어 추가 확인이 필요합니다."
    stale_receipt = "20250101000001"
    raw = {
        "subject": {"corp_name": "대상"},
        "error": "OperationalError: SELECT secret_column FROM internal_table",
        "answer": "stale peer answer",
        "metrics": ["ROE"],
        "results": {2024: {"ROE": {
            "subject_value": 0.12, "percentile": 70, "p25": 0.05,
            "p50": 0.10, "p75": 0.15, "n": 30, "unit": "ratio",
        }}},
        "confirmed_facts": [{
            "statement": "stale filing fact must not be public after an error",
            "source": {"rcept_no": stale_receipt},
        }],
        "analysis": [{
            "statement": "stale internal analysis must not be public after an error",
            "perspective": "investor",
        }],
        "next_checks": ["stale next check"],
        "data_quality": {
            "status": "usable",
            "coverage_note": "OperationalError: SELECT secret_column FROM internal_table",
            "limitations": ["OperationalError: SELECT secret_column FROM internal_table"],
        },
    }
    before = deepcopy(raw)

    normalized = normalize_answer_result("compare_to_industry_multi", raw)
    envelope = build_answer_envelope("compare_to_industry_multi", raw)
    answer = render_answer("compare_to_industry_multi", raw)
    direct_pack = build_answer_pack("compare_to_industry_multi", raw)
    enriched = _attach_meta("compare_to_industry_multi", raw)
    enriched_envelope = build_answer_envelope(
        "compare_to_industry_multi", enriched,
    )

    assert raw == before
    assert raw["error"] == "OperationalError: SELECT secret_column FROM internal_table"
    assert enriched["error"] == raw["error"]
    for field in (
        "confirmed_facts", "analysis", "next_checks", "metrics", "results",
        "rcept_no", "parent_rcept_no",
    ):
        assert field not in enriched
    assert envelope.verdict == "error"
    assert enriched_envelope.model_dump() == envelope.model_dump()
    assert normalized["data_quality"]["status"] == "error"
    assert normalized["data_quality"]["limitations"] == [safe_limitation]
    assert "coverage_note" not in normalized["data_quality"]
    assert envelope.answer == ""
    assert envelope.confirmed_facts == []
    assert envelope.analysis == []
    assert envelope.evidence == []
    assert envelope.next_checks == []
    assert direct_pack is not None
    assert enriched["answer_pack"] is not None
    assert direct_pack["summary"]["status"] == "error"
    assert direct_pack["data_quality"]["status"] == "error"
    assert [table["id"] for table in direct_pack["tables"]] == ["availability"]
    assert direct_pack["tables"][0]["rows"] == [{"status": "error"}]
    assert not direct_pack["charts"]
    assert not direct_pack["diagrams"]
    assert not direct_pack["timelines"]
    assert direct_pack["sources"] == []
    assert direct_pack["limitations"] == [safe_limitation]
    assert enriched["answer_pack"]["limitations"] == [safe_limitation]

    direct_resource = read_resource(direct_pack["resource_uri"])["text"]
    enriched_resource = read_resource(
        enriched["answer_pack"]["resource_uri"]
    )["text"]
    public_surfaces = (
        answer,
        str(direct_pack),
        direct_resource,
        enriched["answer"],
        str(enriched["answer_pack"]),
        enriched_resource,
    )
    for public_surface in public_surfaces:
        assert "OperationalError" not in public_surface
        assert "secret_column" not in public_surface
        assert "internal_table" not in public_surface
        assert "stale filing fact" not in public_surface
        assert "stale internal analysis" not in public_surface
        assert stale_receipt not in public_surface
        assert safe_limitation in public_surface
    assert direct_resource == enriched_resource


def test_peer_known_code_error_uses_the_same_safe_canonical_limitation():
    from kreports.mcp.contracts import build_answer_envelope

    for error in (
        "Identity_Query_Unavailable:OperationalError",
        {"type": "OperationalError", "query": "SELECT secret_column"},
    ):
        envelope = build_answer_envelope("compare_to_industry_multi", {
            "error": error,
        })

        assert envelope.verdict == "error"
        assert envelope.data_quality.limitations == [
            "세부 데이터 제한 사항이 있어 추가 확인이 필요합니다.",
        ]


def test_non_peer_error_results_still_do_not_build_answer_packs():
    from kreports.mcp.answer_pack import build_answer_pack
    from kreports.mcp.contracts import (
        build_answer_envelope,
        enrich_answer_response,
    )

    raw = {"error": "OperationalError: SELECT secret_column FROM internal_table"}

    assert build_answer_pack("search_disclosure_events", raw) is None
    assert "answer_pack" not in enrich_answer_response(
        "search_disclosure_events", raw,
    )
    assert raw["error"] in build_answer_envelope(
        "search_disclosure_events", raw,
    ).data_quality.limitations


def test_non_peer_canonical_normalization_preserves_raw_coverage_note():
    from kreports.mcp.contracts import normalize_answer_result

    raw = {
        "events": [{"event_title": "유상증자 결정"}],
        "data_quality": {
            "status": "limited",
            "coverage_note": "identity_query_unavailable:OperationalError",
        },
    }

    normalized = normalize_answer_result("search_disclosure_events", raw)

    assert normalized["data_quality"]["coverage_note"] == raw["data_quality"]["coverage_note"]
    assert normalized["data_quality"]["limitations"] == [
        "identity_query_unavailable:OperationalError",
    ]


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
    investor_result = {
        "subject": {"corp_name": "대상"},
        "quality_snapshot": {"checks": {}},
        "event_counts": {"capital_raise": 1},
        "takeaways": ["quality_profile_supportive"],
        "data_quality": {"status": "limited"},
    }

    peer_answer = render_answer("compare_to_industry_multi", peer_result)
    event_answer = render_answer("search_disclosure_events", event_result)
    investor_answer = render_answer("get_investor_signals", investor_result)
    peer_pack = build_answer_pack("compare_to_industry_multi", peer_result)
    event_pack = build_answer_pack("search_disclosure_events", event_result)
    investor_pack = build_answer_pack("get_investor_signals", investor_result)
    rendered = "\n".join([
        peer_answer, event_answer, investor_answer,
        str(peer_pack), str(event_pack), str(investor_pack),
    ])

    assert "베니시 M 점수" in rendered
    assert "유상증자" in rendered
    assert "현금전환을 포함한 필수 품질 점검 충족" in rendered
    assert "Beneish_M" not in rendered
    assert "capital_raise" not in rendered
    assert "quality_profile_supportive" not in rendered


def test_investor_signal_pack_status_and_labels_survive_resource_rendering():
    from kreports.mcp.resources import read_resource
    from kreports.mcp.tools import _attach_meta

    out = _attach_meta("get_investor_signals", {
        "subject": {"corp_name": "대상"},
        "quality_snapshot": {"checks": {}},
        "event_counts": {"capital_raise": 1},
        "takeaways": ["quality_profile_supportive"],
        "data_quality": {
            "status": "limited",
            "limitations": ["annual_source_missing"],
        },
    })
    resource = read_resource(out["answer_pack"]["resource_uri"])

    assert out["data_quality"]["status"] == "limited"
    assert out["answer_pack"]["summary"]["status"] == "limited"
    assert out["answer_pack"]["data_quality"]["status"] == "limited"
    assert "limited" in resource["text"]
    assert "유상증자" in resource["text"]
    assert "현금전환을 포함한 필수 품질 점검 충족" in resource["text"]
    assert "capital_raise" not in resource["text"]
    assert "quality_profile_supportive" not in resource["text"]
