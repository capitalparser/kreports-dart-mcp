"""Structured visual answer packs for MCP responses.

The pack is an additive, renderer-friendly contract. It does not replace the
existing dict response or Korean narrative `answer`; clients that can render
charts/diagrams can use it, and plain MCP clients can ignore it.
"""
from __future__ import annotations

from decimal import Decimal
import html
import math
import re
from typing import Any

from kreports.analysis.evidence import evidence_reference_fields, parent_rcept_no
from kreports.mcp.auditor_public import public_kam_lifecycle_events
from kreports.mcp.contracts import (
    build_answer_envelope,
    normalize_answer_result,
)
from kreports.mcp.professional_surfaces import PACK_BUILDERS as PROFESSIONAL_PACK_BUILDERS


PACK_VERSION = "answer_pack.v1"
_DCF_CANDIDATE_METRICS = {
    "revenue_growth": ("매출 성장률", "ratio"),
    "operating_margin": ("영업이익률", "ratio"),
    "cash_conversion": ("현금전환율", "ratio"),
    "tax_rate": ("세율", "ratio"),
    "capex_to_revenue": ("매출 대비 CAPEX 비율", "ratio"),
    "da_to_revenue": ("매출 대비 감가상각비 비율", "ratio"),
    "nwc_to_revenue": ("매출 대비 운전자본 비율", "ratio"),
    "wacc": ("가중평균자본비용 WACC", "ratio"),
    "terminal_growth": ("영구성장률", "ratio"),
    "normalized_revenue": ("정규화 매출", "KRW"),
    "normalized_operating_profit": ("정규화 영업이익", "KRW"),
}
_DCF_READINESS_FIELDS = {
    "revenue_growth",
    "operating_margin",
    "tax_rate",
    "da_to_revenue",
    "capex_to_revenue",
    "nwc_to_revenue",
    "wacc",
    "terminal_growth",
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
}
_DCF_ANALYST_INPUT_FIELDS = {
    "revenue_growth",
    "operating_margin",
    "tax_rate",
    "da_to_revenue",
    "capex_to_revenue",
    "nwc_to_revenue",
    "wacc",
    "terminal_growth",
}


def _is_numeric_measure(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float, Decimal)):
        return not isinstance(value, float) or math.isfinite(value)
    if not isinstance(value, str) or len(value) > 128:
        return False
    if not re.fullmatch(
        r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?",
        value,
        re.ASCII,
    ):
        return False
    try:
        return Decimal(value).is_finite()
    except Exception:
        return False


def build_answer_pack(tool_name: str, result: dict[str, Any]) -> dict[str, Any] | None:
    """Return a visual answer pack for known tool outputs."""
    if not isinstance(result, dict):
        return None
    if (
        "error" in result
        and tool_name not in {
            "compare_to_industry_multi",
            "build_dcf_model_pack",
        }
    ):
        return None
    if tool_name == "build_dcf_model_pack":
        result = normalize_answer_result(tool_name, result)
    if tool_name == "compare_to_industry_multi":
        from kreports.mcp.professional_surfaces.investor import (
            publicize_peer_result_limitations,
        )

        result = publicize_peer_result_limitations(result)

    envelope = build_answer_envelope(tool_name, result)
    normalized_result = dict(result)
    normalized_quality = dict(result.get("data_quality") or {})
    normalized_quality.update(envelope.data_quality.model_dump())
    normalized_result["data_quality"] = normalized_quality

    # Peer-comparison errors still need an inspectable public availability
    # resource.  The canonical envelope supplies the localized limitation;
    # the raw structured error remains only on the programmatic result.
    if (
        envelope.data_quality.status == "error"
        and tool_name != "build_dcf_model_pack"
    ):
        error_result = (
            {"data_quality": envelope.data_quality.model_dump()}
            if tool_name == "compare_to_industry_multi"
            else normalized_result
        )
        raw_pack = _base_pack(
            "데이터 가용성",
            error_result,
            status="error",
        )
        raw_pack["tables"] = [{
            "id": "availability",
            "title": "데이터 가용성",
            "columns": [{"field": "status", "label": "상태"}],
            "rows": [{"status": "error"}],
            "status": "error",
        }]
        raw_pack["limitations"] = list(envelope.data_quality.limitations)
        from kreports.mcp.visual_contracts import build_visualization_pack

        return build_visualization_pack(raw_pack).model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
        )

    # A canonical cache-missing result may still carry a cited fact, subject,
    # selection policy, or cohort descriptor from a legacy handler.  None is a
    # current-tool result row, so it must render as the same empty availability
    # pack rather than attaching fact rows to a missing-status table.
    if (
        envelope.data_quality.status == "missing"
        and tool_name != "build_dcf_model_pack"
    ):
        raw_pack = _base_pack(
            "데이터 가용성",
            normalized_result,
            status="missing",
        )
        raw_pack["limitations"] = list(envelope.data_quality.limitations)
        from kreports.mcp.visual_contracts import build_visualization_pack

        return build_visualization_pack(raw_pack).model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
        )

    builders = {
        "search_dataset": _build_search_dataset_pack,
        "get_accounting_policy_changes": _build_policy_changes_pack,
        "get_dcf_input_candidates": _build_dcf_pack,
        "build_dcf_model_pack": _build_dcf_model_pack,
        "get_quality_of_earnings_pack": _build_quality_pack,
        "get_investor_signals": _build_investor_signals_pack,
        "get_subsidiary_auditors": _build_subsidiary_pack,
        "get_audit_history": _build_audit_fee_trend_pack,
        "compare_peer_audit_fees": _build_audit_fee_benchmark_pack,
        "get_kam_lifecycle": _build_kam_lifecycle_pack,
        "search_disclosure_events": _build_disclosure_events_pack,
        "search_audit_procedures": _build_audit_procedure_pack,
        "compare_to_industry_multi": _build_peer_benchmark_pack,
        **PROFESSIONAL_PACK_BUILDERS,
    }
    if (
        tool_name == "search_dataset"
        and isinstance(normalized_result.get("query"), dict)
        and normalized_result["query"].get("dataset") == "accounting_note_chapters"
    ):
        raw_pack = _build_accounting_note_evidence_pack(normalized_result)
    else:
        builder = builders.get(tool_name)
        raw_pack = (
            _build_generic_pack(tool_name, normalized_result)
            if builder is None
            else builder(normalized_result)
        )
    if raw_pack is None:
        return None
    meta = normalized_result.get("_meta")
    if (
        isinstance(meta, dict)
        and isinstance(meta.get("release_context"), dict)
    ):
        # Some professional builders predate the shared _base_pack.  Attach at
        # the public pack boundary so every resource exposes the same bounded
        # release context as the answer envelope.
        raw_pack["release_context"] = dict(meta["release_context"])
    from kreports.mcp.visual_contracts import build_visualization_pack

    return build_visualization_pack(raw_pack).model_dump(
        mode="json",
        by_alias=True,
        exclude_none=True,
    )


def _base_pack(title: str, result: dict[str, Any], *, status: str | None = None) -> dict[str, Any]:
    pack = {
        "kind": "answer_pack",
        "version": PACK_VERSION,
        "summary": {
            "title": title,
            "status": status or _status(result),
            "subject": _subject_label(result),
        },
        "tables": [],
        "charts": [],
        "diagrams": [],
        "timelines": [],
        "sources": _collect_sources(result),
        "data_quality": result.get("data_quality") or {},
    }
    meta = result.get("_meta")
    if isinstance(meta, dict) and isinstance(meta.get("release_context"), dict):
        pack["release_context"] = dict(meta["release_context"])
    return pack


def _status(result: dict[str, Any]) -> str:
    data_quality = result.get("data_quality")
    if isinstance(data_quality, dict) and data_quality.get("status"):
        return str(data_quality["status"])
    if result.get("verdict"):
        return str(result["verdict"])
    return "usable"


def _subject_label(result: dict[str, Any]) -> str:
    subject = result.get("subject")
    if isinstance(subject, dict):
        return str(subject.get("corp_name") or subject.get("stock_code") or subject.get("corp_code") or "대상 회사")
    company = result.get("company")
    if isinstance(company, dict):
        return str(company.get("corp_name") or company.get("stock_code") or company.get("corp_code") or "대상 회사")
    meta = result.get("_meta")
    if isinstance(meta, dict):
        meta_company = meta.get("company")
        if isinstance(meta_company, dict):
            return str(
                meta_company.get("corp_name")
                or meta_company.get("stock_code")
                or meta_company.get("corp_code")
                or "대상 회사"
            )
    query = result.get("query")
    if isinstance(query, dict):
        return str(query.get("company") or query.get("market") or "대상 조건")
    return "대상 조건"


def _columns(
    fields: list[
        tuple[str, str]
        | tuple[str, str, str | None]
    ],
) -> list[dict[str, str | None]]:
    columns = []
    for definition in fields:
        field, label = definition[:2]
        column: dict[str, str | None] = {
            "field": field,
            "label": label,
        }
        if len(definition) == 3:
            column["unit"] = definition[2]
        columns.append(column)
    return columns


def _table(
    table_id: str,
    title: str,
    columns: list[
        tuple[str, str]
        | tuple[str, str, str | None]
    ],
    rows: list[dict[str, Any]],
    *,
    note: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": table_id,
        "title": title,
        "columns": _columns(columns),
        "rows": rows,
    }
    if note:
        out["note"] = note
    return out


def _chart(
    chart_id: str,
    chart_type: str,
    title: str,
    *,
    data_ref: str,
    encodings: dict[str, Any] | None = None,
    rows: list[dict[str, Any]] | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": chart_id,
        "type": chart_type,
        "title": title,
        "data_ref": data_ref,
        "encodings": encodings or {},
    }
    if rows is not None:
        out["rows"] = rows
    if note:
        out["note"] = note
    return out


def _source_from_rcept_no(rcept_no: Any, *, label: str | None = None) -> dict[str, Any] | None:
    normalized = parent_rcept_no(str(rcept_no or ""))
    if not normalized:
        return None
    return {
        "label": label or "DART 공시",
        "rcept_no": normalized,
        "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={normalized}",
    }


def _collect_sources(result: dict[str, Any]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(source: dict[str, Any] | None) -> None:
        if not source:
            return
        key = str(source.get("rcept_no") or source.get("url") or source.get("label") or source)
        if key in seen:
            return
        seen.add(key)
        sources.append(source)

    add(_source_from_rcept_no(result.get("rcept_no")))
    add(_source_from_rcept_no(result.get("parent_rcept_no")))
    meta = result.get("_meta")
    if isinstance(meta, dict):
        add(_source_from_rcept_no(meta.get("source_rcept_no")))
    def add_reference(source: object) -> None:
        if not isinstance(source, dict):
            return
        reference = evidence_reference_fields({
            **source,
            "source_label": source.get("source_label") or source.get("label"),
            "source_url": source.get("source_url") or source.get("url"),
        })
        if not reference:
            return
        receipt_source = _source_from_rcept_no(
            reference.get("rcept_no"),
            label=reference.get("source_label"),
        )
        add(receipt_source or {
            "label": reference["source_label"],
            "url": reference["source_url"],
        })

    for fact in result.get("confirmed_facts") or []:
        if not isinstance(fact, dict):
            continue
        add_reference(fact.get("source"))
        fact_sources = fact.get("sources")
        if isinstance(fact_sources, list):
            for source in fact_sources[:64]:
                add_reference(source)
    for event in result.get("events") or []:
        if isinstance(event, dict):
            add(_source_from_rcept_no(event.get("rcept_no"), label=event.get("event_title") or event.get("report_nm")))
    for row in result.get("subject_scale_history") or []:
        if isinstance(row, dict):
            add(_source_from_rcept_no(
                row.get("audit_source_rcept_no"),
                label=f"{row.get('year') or ''}년 감사보수·시간 공시".strip(),
            ))
    return sources


def _build_dcf_pack(result: dict[str, Any]) -> dict[str, Any]:
    subject = _subject_label(result)
    pack = _base_pack(f"{subject} DCF 입력 후보", result)
    actuals: list[dict[str, Any]] = []
    for raw_actual in result.get("historical_actuals") or []:
        if not isinstance(raw_actual, dict):
            continue
        actual = dict(raw_actual)
        source = actual.get("source")
        reference = (
            evidence_reference_fields(source)
            if isinstance(source, dict)
            else None
        )
        actual["source"] = (
            reference.get("rcept_no")
            if reference and reference.get("rcept_no")
            else "사업보고서 접수번호 미확보"
        )
        actuals.append(actual)
    if actuals:
        pack["tables"].append(_table(
            "historical_actuals",
            "5개년 실적치",
            [
                ("year", "연도"),
                ("revenue", "매출(원)", "KRW"),
                ("operating_profit", "영업이익(원)", "KRW"),
                ("net_income", "순이익(원)", "KRW"),
                ("operating_cf", "영업현금흐름(원)", "KRW"),
                ("purchase_ppe", "유형자산 취득(원)", "KRW"),
                ("source", "출처 접수번호"),
            ],
            actuals,
        ))
        trend_fields = [
            field
            for field in ("revenue", "operating_profit", "operating_cf")
            if any(_is_numeric_measure(row.get(field)) for row in actuals)
        ]
        if trend_fields:
            pack["charts"].append(_chart(
                "financial_trend",
                "line",
                "매출·영업이익·영업현금흐름 추이",
                data_ref="historical_actuals",
                encodings={
                    "x": {"field": "year"},
                    "y": {"fields": trend_fields},
                },
            ))
        else:
            pack["limitations"] = [
                *pack.get("limitations", []),
                "dcf_actuals_chart_suppressed:no_numeric_facts",
            ]

    basis_labels = {
        "historical_median": "과거 중앙값",
        "operating_cf_to_net_income": "영업현금흐름 대비 순이익",
        "analyst_input": "분석가 입력",
    }
    assumption_rows = []
    unknown_candidate_count = 0
    for key, raw_value in (result.get("candidate_assumptions") or {}).items():
        value = raw_value if isinstance(raw_value, dict) else {"value": raw_value}
        normalized_key = str(key)
        registered = _DCF_CANDIDATE_METRICS.get(normalized_key)
        label = registered[0] if registered else "사용자 정의 입력 후보"
        if registered is None:
            unknown_candidate_count += 1
        unit = (
            registered[1]
            if registered
            else None
        )
        assumption_rows.append({
            "metric": label,
            "value": value.get("value"),
            "unit": unit,
            "basis": (
                basis_labels.get(str(value.get("basis")), "산정 근거 미확보")
                if value.get("basis")
                else "산정 근거 미확보"
            ),
        })
    if unknown_candidate_count:
        pack["limitations"] = [
            *pack.get("limitations", []),
            (
                "dcf_candidate_unknown_metrics_redacted:"
                f"{unknown_candidate_count}"
            ),
        ]
    if assumption_rows:
        candidate_units = {
            str(row.get("unit"))
            for row in assumption_rows
            if row.get("unit") not in {None, ""}
        }
        missing_units = any(
            row.get("unit") in {None, ""}
            for row in assumption_rows
        )
        homogeneous_unit = (
            next(iter(candidate_units))
            if len(candidate_units) == 1 and not missing_units
            else None
        )
        pack["tables"].append(_table(
            "candidate_assumptions",
            "DCF 입력 후보",
            [
                ("metric", "입력값"),
                ("value", "값", homogeneous_unit),
                ("unit", "값 단위"),
                ("basis", "근거"),
            ],
            assumption_rows,
        ))
        if homogeneous_unit:
            pack["charts"].append(_chart(
                "dcf_input_bridge",
                "bar",
                f"공시 기반 입력 후보 ({homogeneous_unit})",
                data_ref="candidate_assumptions",
                encodings={
                    "x": {"field": "metric"},
                    "y": {"field": "value"},
                    "color": {"field": "unit"},
                },
                note="가치평가 결론이 아니라 공시 기반 입력 후보입니다.",
            ))
        else:
            limitation = (
                "dcf_candidate_chart_suppressed:missing_units"
                if missing_units
                else "dcf_candidate_chart_suppressed:mixed_units:"
                + ",".join(sorted(candidate_units))
            )
            pack["limitations"] = [
                *pack.get("limitations", []),
                limitation,
            ]
    return pack


def _safe_dcf_rows(value: Any, *, limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in (value if isinstance(value, list) else [])[:limit]:
        if not isinstance(raw, dict):
            continue
        rows.append(_safe_dcf_value(raw, depth=0))
    return rows


def _safe_dcf_value(value: Any, *, depth: int) -> Any:
    if depth > 4:
        return None
    if isinstance(value, str):
        return html.escape(value[:1000], quote=True)
    if isinstance(value, dict):
        return {
            html.escape(str(key)[:80], quote=True): _safe_dcf_value(
                item,
                depth=depth + 1,
            )
            for key, item in list(value.items())[:64]
        }
    if isinstance(value, (list, tuple)):
        return [
            _safe_dcf_value(item, depth=depth + 1)
            for item in value[:64]
        ]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return html.escape(str(value)[:1000], quote=True)


def _dcf_assumption_unit(key: Any, current: Any) -> str | None:
    if current not in {None, ""}:
        return str(current)
    normalized = str(key or "")
    if normalized in {
        "revenue_growth",
        "operating_margin",
        "tax_rate",
        "da_to_revenue",
        "capex_to_revenue",
        "nwc_to_revenue",
        "wacc",
        "terminal_growth",
    }:
        return "ratio"
    if normalized.startswith("normalized_") or normalized.endswith("_amount"):
        return "KRW"
    return None


def _build_dcf_model_pack(result: dict[str, Any]) -> dict[str, Any]:
    subject = html.escape(_subject_label(result), quote=True)
    pack = _base_pack(
        f"{subject} 검토 가능한 DCF 모델",
        result,
        status=str(result.get("status") or _status(result)),
    )
    pack["summary"]["subject"] = subject
    pack["summary"]["status"] = _safe_dcf_value(
        str(pack["summary"]["status"]),
        depth=0,
    )
    calculation_status = (
        "calculated"
        if result.get("enterprise_value") is not None
        else "calculation_unavailable"
    )
    # The visual contract derives a domain status from a non-canonical summary
    # status while preserving data_quality.status as the canonical availability.
    pack["summary"]["status"] = calculation_status
    pack["sources"] = _safe_dcf_value(pack["sources"], depth=0)
    pack["data_quality"] = _safe_dcf_value(pack["data_quality"], depth=0)
    actuals = _safe_dcf_rows(result.get("actuals"), limit=20)
    normalization = _safe_dcf_rows(result.get("normalization"), limit=2)
    assumptions = _safe_dcf_rows(result.get("assumptions"), limit=8)
    for assumption in assumptions:
        assumption["unit"] = _dcf_assumption_unit(
            assumption.get("key"),
            assumption.get("unit"),
        )
    projections = _safe_dcf_rows(result.get("projections"), limit=10)
    sensitivity = _safe_dcf_rows(result.get("sensitivity"), limit=25)
    bridge = result.get("valuation_bridge")
    bridge_rows = _safe_dcf_rows(
        [bridge] if isinstance(bridge, dict) else [],
        limit=1,
    )

    if calculation_status == "calculation_unavailable":
        pack["tool_name"] = "build_dcf_model_pack"
        pack["request_context"] = {
            "base_year": result.get("base_year"),
            "fs_div": result.get("fs_div"),
        }
        pack["sources"] = []
        missing_accounts = _safe_dcf_rows(result.get("missing_accounts"), limit=20)
        missing_input_fields = [
            str(field)
            for field in result.get("missing_inputs") or []
            if isinstance(field, str) and field in _DCF_READINESS_FIELDS
        ]
        accounted_fields = {
            str(row.get("field") or "")
            for row in missing_accounts
            if isinstance(row, dict)
        }
        readiness_records = list(missing_accounts)
        for field in missing_input_fields:
            if field in accounted_fields:
                continue
            readiness_records.append({
                "field": field,
                "year": result.get("base_year"),
                "fs_div": result.get("fs_div"),
                "basis": (
                    "analyst_input"
                    if field in _DCF_ANALYST_INPUT_FIELDS
                    else "requested_dcf_source_actual"
                ),
            })
            accounted_fields.add(field)
        readiness_rows = [
            {
                "field": row.get("field"),
                "status": "blocked",
                "year": row.get("year"),
                "fs_div": row.get("fs_div"),
                "basis": row.get("basis"),
            }
            for row in readiness_records
        ]
        pack["tables"].extend([
            _table(
                "dcf_model_readiness",
                "DCF 모델 준비도 차단 입력",
                [
                    ("field", "필수 입력"), ("status", "준비도"),
                    ("year", "사업연도"), ("fs_div", "재무제표"),
                    ("basis", "확인 기준"),
                ], readiness_rows,
            ),
            _table(
                "dcf_assumptions",
                "명시적 분석가 가정",
                [
                    ("key", "가정"), ("value", "값"),
                    ("unit", "값 단위"), ("basis", "근거 구분"),
                ], assumptions,
            ),
            _table(
                "dcf_missing_accounts",
                "누락 공시 실제값",
                [
                    ("field", "계정"), ("year", "사업연도"),
                    ("fs_div", "재무제표"), ("basis", "확인 기준"),
                ], missing_accounts,
            ),
        ])
        pack["limitations"] = [
            *pack.get("limitations", []),
            "기업가치 계산에 필요한 입력 또는 공시 실제값이 부족하여 가치 브리지와 민감도를 제공하지 않습니다.",
        ]
        return pack

    pack["tables"].extend([
        _table(
            "dcf_model_readiness",
            "DCF 모델 준비도",
            [
                ("field", "검토 항목"), ("status", "준비도"),
                ("year", "사업연도"), ("fs_div", "재무제표"),
                ("basis", "확인 기준"),
            ], [{
                "field": "enterprise_value",
                "status": "calculated",
                "year": result.get("base_year"),
                "fs_div": result.get("fs_div"),
                "basis": "calculated_dcf_model",
            }],
        ),
        _table(
            "dcf_actuals",
            "요청 기준연도 공시 실제값",
            [
                ("metric_key", "지표"),
                ("amount", "금액(KRW)"),
                ("unit", "단위"),
                ("year", "연도"),
                ("fs_div", "재무제표"),
                ("source_account_id", "원천 계정 ID"),
                ("source_account_name", "원천 계정명"),
                ("source_table", "원천 테이블"),
                ("fetched_at", "수집시각"),
            ],
            actuals,
        ),
        _table(
            "dcf_normalization",
            "정규화 레이어",
            [
                ("metric_key", "지표"),
                ("original_actual", "원 실제값(KRW)"),
                ("normalized_amount", "정규화값(KRW)"),
                ("basis", "구분"),
                ("reason", "분석가 근거"),
            ],
            normalization,
        ),
        _table(
            "dcf_assumptions",
            "명시적 분석가 가정",
            [
                ("key", "가정"),
                ("value", "값"),
                ("unit", "값 단위"),
                ("basis", "근거 구분"),
            ],
            assumptions,
        ),
        _table(
            "dcf_projections",
            "연도별 UFCF 예측",
            [
                ("year", "연도"),
                ("revenue", "매출(KRW)"),
                ("ebit", "EBIT(KRW)"),
                ("tax_rate", "세율"),
                ("after_tax_ebit", "세후 EBIT(KRW)"),
                ("depreciation_amortization", "D&A(KRW)"),
                ("capex", "CAPEX(KRW)"),
                ("nwc_balance", "NWC(KRW)"),
                ("nwc_change", "NWC 증감(KRW)"),
                ("ufcf", "UFCF(KRW)"),
                ("discount_factor", "할인계수"),
                ("present_value", "현재가치(KRW)"),
                ("formula", "공식"),
            ],
            projections,
        ),
        _table(
            "dcf_valuation_bridge",
            "기업가치·순부채·자기자본 브리지",
            [
                ("forecast_period_present_value", "예측기간 PV(KRW)"),
                ("terminal_value", "할인 전 터미널가치(KRW)"),
                ("terminal_value_present_value", "터미널가치 PV(KRW)"),
                ("gordon_growth_formula", "Gordon 성장 공식"),
                ("final_year_discount_factor", "최종연도 할인계수"),
                ("enterprise_value", "기업가치(KRW)"),
                ("enterprise_value_formula", "기업가치 조정 공식"),
                ("debt", "이자부부채(KRW)"),
                ("cash", "현금(KRW)"),
                ("net_debt", "순부채(KRW)"),
                ("equity_value", "자기자본가치(KRW)"),
                ("formula", "브리지 공식"),
            ],
            bridge_rows,
        ),
        _table(
            "dcf_sensitivity",
            "WACC·영구성장률 5x5 민감도",
            [
                ("wacc", "WACC"),
                ("terminal_growth", "영구성장률"),
                ("status", "상태"),
                ("enterprise_value", "기업가치(KRW)"),
            ],
            sensitivity,
        ),
    ])
    pack["charts"].extend([
        _chart(
            "dcf_ufcf_projection",
            "line",
            "연도별 UFCF와 현재가치",
            data_ref="dcf_projections",
            encodings={
                "x": {"field": "year"},
                "y": {"fields": ["ufcf", "present_value"]},
            },
        ),
        _chart(
            "dcf_sensitivity_matrix",
            "heatmap",
            "WACC·영구성장률 기업가치 민감도",
            data_ref="dcf_sensitivity",
            encodings={
                "x": {"field": "wacc"},
                "y": {"field": "terminal_growth"},
                "color": {"field": "enterprise_value"},
            },
            note="terminal_growth >= wacc 조합은 invalid_rate_pair로 비워 둡니다.",
        ),
    ])
    return pack


def _build_quality_pack(result: dict[str, Any]) -> dict[str, Any]:
    subject = _subject_label(result)
    pack = _base_pack(f"{subject} 이익의 질 점검", result, status=str(result.get("verdict") or _status(result)))
    rows = [row for row in result.get("signals") or [] if isinstance(row, dict)]
    if rows:
        pack["tables"].append(_table(
            "quality_signals",
            "이익의 질 신호",
            [("signal", "신호"), ("severity", "강도"), ("meaning", "의미")],
            rows,
        ))
    metrics = result.get("metrics")
    if isinstance(metrics, dict):
        pack["tables"].append(_table(
            "quality_metrics",
            "요약 지표",
            [("metric", "지표"), ("value", "값")],
            [{"metric": key, "value": value} for key, value in metrics.items()],
        ))
    return pack


def _build_investor_signals_pack(result: dict[str, Any]) -> dict[str, Any]:
    subject = _subject_label(result)
    pack = _base_pack(f"{subject} 투자자 신호", result)
    event_rows = [
        {"event_type": key, "count": value}
        for key, value in (result.get("event_counts") or {}).items()
        if value
    ]
    if event_rows:
        pack["tables"].append(_table(
            "event_counts",
            "최근 공시 이벤트 분포",
            [("event_type", "이벤트"), ("count", "건수", "건")],
            event_rows,
        ))
        pack["charts"].append(_chart(
            "event_counts_bar",
            "bar",
            "최근 공시 이벤트",
            data_ref="event_counts",
            encodings={"x": {"field": "event_type"}, "y": {"field": "count"}},
        ))
    takeaways = result.get("takeaways") or []
    if takeaways:
        pack["tables"].append(_table(
            "takeaways",
            "관찰 포인트",
            [("takeaway", "관찰 포인트")],
            [{"takeaway": item} for item in takeaways],
        ))
    return pack


def _build_audit_fee_trend_pack(result: dict[str, Any]) -> dict[str, Any]:
    pack = _base_pack(f"{_subject_label(result)} 감사보수 추이", result)
    history = [
        row for row in (result.get("history") or [])
        if isinstance(row, dict)
    ]
    if history:
        pack["tables"].append(_table(
            "audit_fee_trend",
            "감사보수·감사시간 추이",
            [
                ("year", "연도"),
                ("audit_fee_m", "감사보수(백만원)"),
                ("audit_hours", "감사시간(시간)"),
                ("non_audit_fee_m", "비감사보수(백만원)"),
                ("auditor_nm", "감사인"),
            ],
            history,
        ))
        fee_fields = [
            field
            for field in ("audit_fee_m", "non_audit_fee_m")
            if any(_is_numeric_measure(row.get(field)) for row in history)
        ]
        if fee_fields:
            pack["charts"].append(_chart(
                "audit_fee_trend_chart",
                "line",
                "감사보수 추이",
                data_ref="audit_fee_trend",
                encodings={
                    "x": {"field": "year"},
                    "y": {"fields": fee_fields},
                },
            ))
        else:
            pack["limitations"] = [
                *pack.get("limitations", []),
                "audit_fee_chart_suppressed:no_numeric_facts",
            ]
    return pack


def _build_audit_fee_benchmark_pack(result: dict[str, Any]) -> dict[str, Any]:
    pack = _base_pack(f"{_subject_label(result)} 감사보수 Peer 비교", result)
    _append_subject_scale_history(pack, result)
    subject = result.get("subject_metrics")
    peers = result.get("peers")
    rows = []
    if isinstance(subject, dict):
        rows.append({"role": "subject", **subject})
    for peer in peers if isinstance(peers, list) else []:
        if isinstance(peer, dict):
            rows.append({"role": "peer", **peer})
    if rows:
        pack["tables"].append(_table(
            "audit_fee_peer_distribution",
            "감사보수 Peer 분포",
            [
                ("role", "구분"),
                ("corp_name", "회사"),
                ("audit_fee_m", "감사보수(백만원)"),
                ("audit_hours", "감사시간(시간)"),
                ("non_audit_fee_m", "비감사보수(백만원)"),
                ("nas_ratio", "비감사보수 비율", "ratio"),
            ],
            rows,
        ))
        if any(
            _is_numeric_measure(row.get("audit_fee_m"))
            for row in rows
        ):
            pack["charts"].append(_chart(
                "audit_fee_peer_chart",
                "bar",
                "감사보수 Peer 분포",
                data_ref="audit_fee_peer_distribution",
                encodings={
                    "x": {"field": "corp_name"},
                    "y": {"field": "audit_fee_m"},
                    "series": {"field": "role"},
                },
            ))
        else:
            pack["limitations"] = [
                *pack.get("limitations", []),
                "audit_fee_peer_chart_suppressed:no_numeric_facts",
            ]
    return pack


def _append_subject_scale_history(
    pack: dict[str, Any],
    result: dict[str, Any],
) -> None:
    history = [
        row
        for row in (result.get("subject_scale_history") or [])
        if isinstance(row, dict)
    ]
    if not history:
        return
    caveat = (
        "동일한 연결·별도 기준의 요청연도 포함 3개년입니다. "
        "공개자료 기반 산정 입력·비교 자료이며 표준감사시간 결론이 아닙니다."
    )
    pack["tables"].append(_table(
        "subject_scale_history",
        "대상회사 3개년 규모·감사투입 추이",
        [
            ("year", "연도"),
            ("fs_div", "재무제표 기준"),
            ("total_assets_100m", "총자산", "억원"),
            ("revenue_100m", "매출액", "억원"),
            ("audit_fee_m", "감사보수", "백만원"),
            ("audit_hours", "감사시간", "시간"),
            ("auditor_nm", "감사인"),
            ("missing_fields_label", "미확보 항목"),
        ],
        history,
        note=caveat,
    ))
    pack["limitations"] = list(dict.fromkeys([
        *pack.get("limitations", []),
        caveat,
    ]))


def _build_acceptance_pack(result: dict[str, Any]) -> dict[str, Any]:
    pack = _base_pack(f"{_subject_label(result)} 수임·유지 검토", result)
    _append_subject_scale_history(pack, result)
    signals = [
        signal
        for signal in (result.get("acceptance_signals") or [])
        if isinstance(signal, dict)
    ]
    if signals:
        pack["tables"].append(_table(
            "acceptance_signals",
            "수임·유지 검토 신호",
            [
                ("area", "검토영역"),
                ("severity", "중요도"),
                ("signal", "확인 신호"),
            ],
            signals,
        ))
    return pack


def _build_kam_lifecycle_pack(result: dict[str, Any]) -> dict[str, Any]:
    pack = _base_pack(f"{_subject_label(result)} KAM lifecycle", result)
    rows = public_kam_lifecycle_events(result.get("events"))
    if rows:
        pack["tables"].append(_table(
            "kam_timeline",
            "KAM 생애주기",
            [
                ("year", "연도"),
                ("topic", "주제"),
                ("status", "상태"),
                ("title", "KAM"),
                ("reason_hint", "선정 이유"),
                ("procedure_hint", "감사절차"),
            ],
            rows,
        ))
        pack["charts"].append(_chart(
            "kam_lifecycle_chart",
            "bar",
            "KAM 생애주기",
                data_ref="kam_timeline",
            encodings={
                "x": {"field": "year"},
                "y": {"field": "status"},
                "series": {"field": "topic"},
            },
        ))
    return pack


def _build_subsidiary_pack(result: dict[str, Any]) -> dict[str, Any]:
    subject = _subject_label(result)
    year = result.get("bsns_year")
    graph = result.get("group_graph") if isinstance(result.get("group_graph"), dict) else {}
    graph_rows = graph.get("entities") if isinstance(graph.get("entities"), list) else []
    subsidiaries = [
        row for row in (graph_rows or result.get("subsidiaries") or [])
        if isinstance(row, dict)
    ]
    pack = _base_pack(f"{subject} 연결실체 구조", result)
    visible_diagram_rows = _hierarchy_closed_rows(
        subsidiaries,
        limit=8,
    )
    omitted_count = len(subsidiaries) - len(visible_diagram_rows)
    rows = []
    for item in subsidiaries:
        auditor = item.get("auditor") if isinstance(item.get("auditor"), dict) else {}
        rows.append({
            "name": item.get("name"),
            "relation": item.get("relation"),
            "ownership_pct": item.get("ownership_pct"),
            "asset_amount_m": item.get("asset_amount_m"),
            "asset_share_pct": item.get("asset_share_pct"),
            "revenue_amount_m": item.get("revenue_amount_m"),
            "revenue_share_pct": item.get("revenue_share_pct"),
            "qsc_status": item.get("qsc_status"),
            "auditor_nm": auditor.get("auditor_nm"),
            **({
                "source_rcept_no": item.get("source_rcept_no"),
                "parent_entity_key": item.get("parent_entity_key"),
                "entity_key": item.get("entity_key"),
            } if graph_rows else {}),
        })
        if item.get("asset_amount_m") is None and item.get("asset_amount") is not None:
            try:
                rows[-1]["asset_amount_m"] = (
                    float(item["asset_amount"]) / 1_000_000
                )
            except (TypeError, ValueError):
                rows[-1]["asset_amount_m"] = item["asset_amount"]
        if item.get("revenue_amount_m") is None and item.get("revenue_amount") is not None:
            try:
                rows[-1]["revenue_amount_m"] = (
                    float(item["revenue_amount"]) / 1_000_000
                )
            except (TypeError, ValueError):
                rows[-1]["revenue_amount_m"] = item["revenue_amount"]
    if rows:
        pack["tables"].append(_table(
            "subsidiary_contribution",
            "연결실체별 자산·매출 기여도",
            [
                ("name", "회사"),
                ("relation", "관계"),
                ("ownership_pct", "지분율"),
                ("asset_amount_m", "자산(백만원)"),
                ("asset_share_pct", "자산비중"),
                ("revenue_amount_m", "매출(백만원)"),
                ("revenue_share_pct", "매출비중"),
                ("qsc_status", "QSC"),
                ("auditor_nm", "감사인"),
                *((("source_rcept_no", "출처 접수번호"),) if graph_rows else ()),
            ],
            rows,
        ))
        if any(
            _is_numeric_measure(row.get("asset_share_pct"))
            for row in rows
        ):
            pack["charts"].append(_chart(
                "entity_asset_contribution",
                "bar",
                "실체별 자산비중",
                data_ref="subsidiary_contribution",
                encodings={
                    "x": {"field": "name"},
                    "y": {"field": "asset_share_pct"},
                },
            ))
        else:
            pack["limitations"] = [
                *pack.get("limitations", []),
                "group_chart_suppressed:no_asset_share_facts",
            ]
        if any(
            _is_numeric_measure(row.get("revenue_share_pct"))
            for row in rows
        ):
            pack["charts"].append(_chart(
                "entity_revenue_contribution",
                "bar",
                "실체별 매출비중",
                data_ref="subsidiary_contribution",
                encodings={
                    "x": {"field": "name"},
                    "y": {"field": "revenue_share_pct"},
                },
            ))
        else:
            pack["limitations"] = [
                *pack.get("limitations", []),
                "group_chart_suppressed:no_revenue_share_facts",
            ]
        visible_row_ids = {
            str(row.get("entity_key")): str(index)
            for index, row in enumerate(visible_diagram_rows, start=1)
            if row.get("entity_key") not in {None, ""}
        }
        structure_rows = [{
            "row_id": "0",
            "parent_row_id": None,
            "name": subject,
            "relation": "root",
            "ownership_pct": None,
            "year": year,
            "qsc_status": None,
        }]
        for index, row in enumerate(visible_diagram_rows, start=1):
            parent_key = str(row.get("parent_entity_key") or "")
            structure_rows.append({
                "row_id": str(index),
                "parent_row_id": (
                    "0"
                    if row.get("parent_is_root")
                    else visible_row_ids.get(parent_key, "0")
                ),
                "name": row.get("name"),
                "relation": row.get("relation"),
                "ownership_pct": row.get("ownership_pct"),
                "year": year,
                "qsc_status": row.get("qsc_status"),
            })
        if omitted_count > 0:
            structure_rows.append({
                "row_id": str(len(visible_diagram_rows) + 1),
                "parent_row_id": "0",
                "name": f"{omitted_count}개 노드는 가독성을 위해 생략",
                "relation": "omitted",
                "ownership_pct": None,
                "year": year,
                "qsc_status": None,
            })
        pack["tables"].append(_table(
            "subsidiary_structure_facts",
            "연결실체 구조도 근거",
            [
                ("row_id", "행 ID"),
                ("parent_row_id", "상위 행 ID"),
                ("name", "회사"),
                ("relation", "관계"),
                ("ownership_pct", "지분율(%)"),
                ("year", "연도"),
                ("qsc_status", "QSC"),
            ],
            structure_rows,
            note="구조도 노드와 간선은 이 표의 행만 사용합니다.",
        ))
    pack["diagrams"].append({
        "id": "subsidiary_structure",
        "type": "mermaid",
        "title": "연결실체 구조도",
        "table_ref": "subsidiary_structure_facts",
        "definition": _subsidiary_mermaid(
            subject, year, subsidiaries,
            canonical=bool(graph_rows),
        ),
    })
    warnings = []
    visible_count = len(_hierarchy_closed_rows(subsidiaries, limit=8))
    if len(subsidiaries) > visible_count:
        warnings.append(
            f"graph_nodes_omitted:{len(subsidiaries) - visible_count}"
        )
    if result.get("truncated") or graph.get("truncated"):
        warnings.append("upstream_result_truncated")
    for limitation in (
        *(result.get("limitations") or []),
        *(graph.get("limitations") or []),
    ):
        if limitation not in warnings:
            warnings.append(str(limitation))
    if warnings:
        pack["warnings"] = warnings
    return pack


def _subsidiary_mermaid(
    subject: str,
    year: Any,
    subsidiaries: list[dict[str, Any]],
    *,
    canonical: bool = False,
) -> str:
    lines = [
        "flowchart TD",
        (
            f'  P["{_mermaid_label(subject)}<br/>'
            f'{_mermaid_label(year or "")}년 연결실체"]'
        ),
    ]
    visible = _hierarchy_closed_rows(subsidiaries, limit=8)
    node_ids = {
        str(item.get("entity_key") or f"row:{idx}"): f"N{idx}"
        for idx, item in enumerate(visible, start=1)
    }
    for idx, item in enumerate(visible, start=1):
        label = (
            f"{_mermaid_label(item.get('relation') or '-')} / 지분율 "
            f"{_mermaid_label(_fmt_pct(item.get('ownership_pct')))}"
            "<br/>"
            f"자산 {_mermaid_label(_fmt_pct(item.get('asset_share_pct')))}"
            " / 매출 "
            f"{_mermaid_label(_fmt_pct(item.get('revenue_share_pct')))}"
        )
        qsc = _qsc_label(item.get("qsc_status"))
        parent_key = str(item.get("parent_entity_key") or "")
        parent_node = node_ids.get(parent_key, "P") if canonical else "P"
        lines.append(
            f'  {parent_node} -->|"{label}"| '
            f'N{idx}["{_mermaid_label(item.get("name"))}<br/>'
            f'{_mermaid_label(qsc)}"]'
        )
    if len(subsidiaries) > len(visible):
        lines.append(
            f'  OMIT["{len(subsidiaries) - len(visible)}개 노드는 가독성을 위해 생략"]'
        )
    if not subsidiaries:
        lines.append('  P -->|"캐시 없음"| N0["연결/투자 실체 미확보"]')
    return "\n".join(lines)


def _hierarchy_closed_rows(
    rows: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Choose a bounded subgraph without inventing a root for descendants."""
    child_keys = {
        str(row.get("entity_key") or "")
        for row in rows
        if row.get("entity_key")
    }
    visible: list[dict[str, Any]] = []
    visible_keys: set[str] = set()
    pending = list(rows)
    while pending and len(visible) < limit:
        progressed = False
        for row in list(pending):
            parent_key = str(row.get("parent_entity_key") or "")
            parent_is_root = (
                row.get("parent_is_root") is True
                or (
                    "parent_is_root" not in row
                    and parent_key not in child_keys
                )
            )
            if (
                parent_key
                and not parent_is_root
                and parent_key not in visible_keys
            ):
                continue
            pending.remove(row)
            visible.append(row)
            if row.get("entity_key"):
                visible_keys.add(str(row["entity_key"]))
            progressed = True
            if len(visible) == limit:
                break
        if not progressed:
            break
    return visible


_UNSAFE_C0 = re.compile(r"[\x00-\x09\x0b\x0c\x0e-\x1f\x7f]")


def _normalized_markup_text(value: Any) -> str:
    normalized = str(value or "-").replace("\r\n", "\n").replace("\r", "\n")
    return _UNSAFE_C0.sub("\ufffd", normalized)


def _mermaid_label(value: Any) -> str:
    return (
        html.escape(_normalized_markup_text(value), quote=True)
        .replace("\\", "&#92;")
        .replace("|", "&#124;")
        .replace("[", "&#91;")
        .replace("]", "&#93;")
        .replace("{", "&#123;")
        .replace("}", "&#125;")
        .replace("\n", "<br/>")
    )


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "미확보"
    try:
        return f"{float(value):,.1f}%"
    except (TypeError, ValueError):
        return str(value)


def _qsc_label(value: Any) -> str:
    if value == "qsc":
        return "QSC"
    if value == "not_qsc":
        return "비QSC"
    return "미판정"


def _build_disclosure_events_pack(result: dict[str, Any]) -> dict[str, Any]:
    pack = _base_pack("공시 이벤트 타임라인", result)
    events = [event for event in result.get("events") or [] if isinstance(event, dict)]
    event_rows = []
    for event in events:
        row = {
            "event_date": event.get("event_date"),
            "corp_name": event.get("corp_name"),
            "event_type": event.get("event_type"),
            "event_title": event.get("event_title"),
            "rcept_no": parent_rcept_no(str(event.get("rcept_no") or "")),
        }
        event_rows.append(row)
    if event_rows:
        pack["tables"].append(_table(
            "disclosure_events",
            "공시 이벤트",
            [
                ("event_date", "일자"),
                ("corp_name", "회사"),
                ("event_type", "이벤트 유형"),
                ("event_title", "공시명"),
                ("rcept_no", "접수번호"),
            ],
            event_rows,
        ))
        pack["timelines"].append({
            "id": "disclosure_event_timeline",
            "title": "공시 이벤트 타임라인",
            "table_ref": "disclosure_events",
        })
    counts = result.get("event_type_counts") or {}
    if counts:
        rows = [{"event_type": key, "count": value} for key, value in counts.items()]
        pack["tables"].append(_table(
            "event_type_counts",
            "공시 이벤트 유형별 건수",
            [
                ("event_type", "이벤트 유형"),
                ("count", "공시 건수", "건"),
            ],
            rows,
        ))
        pack["charts"].append(_chart(
            "event_type_distribution",
            "bar",
            "공시 이벤트 유형 분포",
            data_ref="event_type_counts",
            encodings={"x": {"field": "event_type"}, "y": {"field": "count"}},
        ))
    return pack


def _build_peer_benchmark_pack(result: dict[str, Any]) -> dict[str, Any]:
    subject = _subject_label(result)
    pack = _base_pack(f"{subject} Peer 벤치마크", result)
    rows: list[dict[str, Any]] = []
    for year, metrics in (result.get("results") or {}).items():
        if not isinstance(metrics, dict):
            continue
        for metric, values in metrics.items():
            if not isinstance(values, dict):
                continue
            rows.append({
                "year": int(year),
                "metric": metric,
                "subject_value": values.get("subject_value"),
                "percentile": values.get("percentile"),
                "p25": values.get("p25"),
                "p50": values.get("p50"),
                "p75": values.get("p75"),
                "n": values.get("n"),
                "unit": values.get("unit"),
            })
    if rows:
        pack["tables"].append(_table(
            "peer_metric_matrix",
            "Peer 지표 비교",
            [
                ("year", "연도"),
                ("metric", "지표"),
                ("subject_value", "대상회사 값"),
                ("percentile", "대상회사 백분위"),
                ("p25", "Peer P25 값"),
                ("p50", "Peer 중앙값 P50"),
                ("p75", "Peer P75 값"),
                ("n", "Peer 표본 수(개)"),
                ("unit", "값 단위"),
            ],
            rows,
        ))
        has_percentile_facts = any(
            _is_numeric_measure(row.get("percentile"))
            for row in rows
        )
        if has_percentile_facts:
            pack["charts"].append(_chart(
                "peer_percentile_matrix",
                "heatmap",
                "Peer 백분위 매트릭스",
                data_ref="peer_metric_matrix",
                encodings={
                    "x": {"field": "year"},
                    "y": {"field": "metric"},
                    "color": {"field": "percentile"},
                },
            ))
        has_band_facts = all(
            any(_is_numeric_measure(row.get(field)) for row in rows)
            for field in ("subject_value", "p25", "p50", "p75")
        )
        peer_units = {
            str(row.get("unit"))
            for row in rows
            if row.get("unit") not in {None, ""}
        }
        missing_units = any(
            row.get("unit") in {None, ""}
            for row in rows
        )
        if has_band_facts and len(peer_units) == 1 and not missing_units:
            unit = next(iter(peer_units))
            pack["charts"].append(_chart(
                "peer_band",
                "band",
                f"대상회사 vs Peer 사분위 ({unit})",
                data_ref="peer_metric_matrix",
                encodings={
                    "x": {"field": "year"},
                    "y": {"field": "subject_value"},
                    "band": {"fields": ["p25", "p50", "p75"]},
                    "series": {"field": "metric"},
                    "color": {"field": "unit"},
                },
            ))
        elif has_band_facts:
            limitation = (
                "peer_band_suppressed:missing_units"
                if missing_units
                else "peer_band_suppressed:mixed_units:"
                + ",".join(sorted(peer_units))
            )
            pack["limitations"] = [
                *pack.get("limitations", []),
                limitation,
            ]
        if not has_percentile_facts and not has_band_facts:
            pack["limitations"] = [
                *pack.get("limitations", []),
                "peer_chart_suppressed:no_numeric_facts",
            ]
    cohort_metadata = result.get("cohort_metadata")
    if isinstance(cohort_metadata, dict):
        exclusion_counts = cohort_metadata.get("exclusion_counts") or {}
        metadata_rows = [
            {
                "profile": cohort_metadata.get("profile"),
                "requested_year": cohort_metadata.get("requested_year"),
                "fs_div": cohort_metadata.get("fs_div"),
                "total_candidates": cohort_metadata.get("total_candidates"),
                "eligible_count": cohort_metadata.get("eligible_count"),
                "selected_count": cohort_metadata.get("selected_count"),
                "exclusion_reason": reason,
                "exclusion_count": count,
                "exclusion_scope": (
                    "presentation"
                    if reason == "outside_limit"
                    else "universe"
                    if reason == "subject"
                    else "common_eligibility"
                ),
            }
            for reason, count in sorted(exclusion_counts.items())
        ] or [{
            "profile": cohort_metadata.get("profile"),
            "requested_year": cohort_metadata.get("requested_year"),
            "fs_div": cohort_metadata.get("fs_div"),
            "total_candidates": cohort_metadata.get("total_candidates"),
            "eligible_count": cohort_metadata.get("eligible_count"),
            "selected_count": cohort_metadata.get("selected_count"),
            "exclusion_reason": None,
            "exclusion_count": 0,
            "exclusion_scope": None,
        }]
        pack["tables"].append(_table(
            "peer_cohort_metadata",
            "Peer cohort 선정 근거",
            [
                ("profile", "프로필"),
                ("requested_year", "요청연도"),
                ("fs_div", "재무제표 기준"),
                ("total_candidates", "전체 후보"),
                ("eligible_count", "적격 후보"),
                ("selected_count", "선정 후보"),
                ("exclusion_reason", "제외 사유"),
                ("exclusion_count", "제외 수"),
                ("exclusion_scope", "제외 단계"),
            ],
            metadata_rows,
        ))
    return pack


def _build_generic_pack(tool_name: str, result: dict[str, Any]) -> dict[str, Any] | None:
    if not result.get("confirmed_facts") and not result.get("data_quality"):
        return None
    title = f"{_subject_label(result)} {tool_name}"
    pack = _base_pack(title, result)
    facts = [
        {"statement": fact.get("statement"), "source": fact.get("source")}
        for fact in result.get("confirmed_facts") or []
        if isinstance(fact, dict)
    ]
    if facts:
        pack["tables"].append(_table(
            "confirmed_facts",
            "공시에서 확인되는 내용",
            [("statement", "확인 내용"), ("source", "출처")],
            facts,
        ))
    return pack


_SEARCH_TABLE_FIELDS: dict[str, list[tuple[str, str, str | None]]] = {
    "audit_fees": [
        ("corp_name", "회사", None),
        ("year", "연도", None),
        ("auditor_nm", "감사인", None),
        ("audit_fee_m", "감사보수(백만원)", "KRW million"),
        ("audit_hours", "감사시간", "hours"),
        ("rcept_no", "접수번호", None),
    ],
    "financials": [
        ("corp_name", "회사", None),
        ("year", "연도", None),
        ("quarter", "분기", None),
        ("fs_div", "재무제표 기준", None),
        ("revenue", "매출", "KRW"),
        ("operating_profit", "영업이익", "KRW"),
        ("total_assets", "총자산", "KRW"),
        ("rcept_no", "접수번호", None),
    ],
}
_DEFAULT_SEARCH_TABLE_FIELDS = [
    ("corp_name", "회사", None),
    ("year", "연도", None),
    ("report_nm", "보고서", None),
    ("section_title", "섹션", None),
    ("note_title", "주석", None),
    ("rcept_no", "접수번호", None),
]


def _build_search_dataset_pack(result: dict[str, Any]) -> dict[str, Any]:
    query = result.get("query") if isinstance(result.get("query"), dict) else {}
    dataset = str(query.get("dataset") or "dataset")
    fields = _SEARCH_TABLE_FIELDS.get(dataset, _DEFAULT_SEARCH_TABLE_FIELDS)
    rows: list[dict[str, Any]] = []
    for company in result.get("companies") or []:
        if not isinstance(company, dict):
            continue
        for record in company.get("records") or []:
            if not isinstance(record, dict):
                continue
            merged = {
                "corp_name": company.get("corp_name"),
                **record,
            }
            rows.append({
                field: merged.get(field)
                for field, _label, _unit in fields
            })
            if len(rows) >= 50:
                break
        if len(rows) >= 50:
            break
    pack = _base_pack(
        f"{_subject_label(result)} {dataset} 검색 결과",
        result,
    )
    pack["tables"].append(_table(
        "search_results",
        "공시 데이터 검색 결과",
        fields,
        rows,
        note=(
            "접수번호가 없는 행은 원 공시와 직접 연결되지 않은 로컬 캐시 "
            "결과이므로 제한적으로만 사용하세요."
        ),
    ))
    return pack


def _build_policy_changes_pack(result: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for item in result.get("changed_items") or []:
        if not isinstance(item, dict):
            continue
        row = {
            "year": item.get("year"),
            "fs_div": item.get("fs_div"),
            "note_no": item.get("note_no"),
            "note_title": item.get("note_title"),
            "section_type": item.get("section_type"),
            "change_type": item.get("change_type"),
            "similarity_to_previous": item.get("similarity_to_previous"),
            "rcept_no": item.get("rcept_no"),
        }
        if item.get("provenance_status") is not None:
            row["provenance_status"] = item.get("provenance_status")
        rows.append(row)
        if len(rows) == 50:
            break
    pack = _base_pack(
        f"{_subject_label(result)} 회계정책 변경 후보",
        result,
    )
    pack["tables"].append(_table(
        "accounting_policy_changes",
        "회계정책 텍스트 변경 후보",
        [
            ("year", "연도"),
            ("fs_div", "재무제표 기준"),
            ("note_no", "주석"),
            ("note_title", "주석명"),
            ("section_type", "구분"),
            ("change_type", "변경 후보 유형"),
            ("similarity_to_previous", "전기 유사도"),
            ("rcept_no", "접수번호"),
            ("provenance_status", "접수번호 검증"),
        ],
        rows,
        note=(
            "텍스트 차이는 회계정책 변경 스크리닝 후보이며 실제 정책 변경 "
            "결론은 원문 비교와 감사인 검토가 필요합니다."
        ),
    ))
    return pack


def _build_accounting_note_evidence_pack(result: dict[str, Any]) -> dict[str, Any]:
    """Build the compact table from handler-enriched note facts only."""
    pack = _base_pack(f"{_subject_label(result)} 회계주석 근거", result)
    audit_implication = next(
        (
            str(item.get("statement") or "")
            for item in result.get("analysis") or []
            if isinstance(item, dict) and item.get("perspective") == "auditor"
        ),
        "감사 관점의 스크리닝 근거를 확인하지 못했습니다.",
    )
    rows: list[dict[str, Any]] = []
    for fact in result.get("confirmed_facts") or []:
        if not isinstance(fact, dict):
            continue
        source = fact.get("source") if isinstance(fact.get("source"), dict) else {}
        rows.append({
            "topic": fact.get("topic") or (result.get("query") or {}).get("keyword") or "회계주석",
            "year": fact.get("year"),
            "fs_div": fact.get("fs_div"),
            "note_reference": fact.get("note_reference") or source.get("section_title") or "주석",
            "confirmed_statement": (
                f"{fact.get('note_reference') or source.get('section_title') or '주석'}에서 "
                f"{fact.get('topic') or (result.get('query') or {}).get('keyword') or '요청'} "
                "관련 주석 문구가 확인되었습니다."
            ),
            "matched_excerpt": fact.get("excerpt") or "발췌문 미확보",
            "audit_implication": audit_implication,
            "rcept_no": parent_rcept_no(str(source.get("rcept_no") or "")) or "미확보",
        })
    pack["tables"].append(_table(
        "accounting_note_evidence",
        "회계주석 확인 근거",
        [
            ("topic", "주제"),
            ("year", "연도"),
            ("fs_div", "재무제표 기준"),
            ("note_reference", "주석"),
            ("confirmed_statement", "확인된 내용"),
            ("matched_excerpt", "일치 발췌문"),
            ("audit_implication", "감사 관점"),
            ("rcept_no", "접수번호"),
        ],
        rows,
        note="주석 발췌문은 스크리닝 근거이며 감사 결론이나 금액 검증을 대체하지 않습니다.",
    ))
    return pack


def _build_audit_procedure_pack(
    result: dict[str, Any],
) -> dict[str, Any]:
    pack = _base_pack(f"{_subject_label(result)} KAM 감사절차", result)
    rows: list[dict[str, Any]] = []
    for company in result.get("companies") or []:
        if not isinstance(company, dict):
            continue
        for record in company.get("records") or []:
            if not isinstance(record, dict):
                continue
            rows.append(
                {
                    "corp_name": company.get("corp_name"),
                    "year": record.get("year"),
                    "kam_topic": record.get("kam_topic"),
                    "method": record.get("method"),
                    "procedure_type": record.get("procedure_type"),
                    "procedure_excerpt": record.get("procedure_excerpt"),
                    "assertion_hints": record.get("assertion_hints"),
                    "linked_metric_keys": record.get("linked_metric_keys"),
                    "linked_note_keys": record.get("linked_note_keys"),
                    "linked_event_keys": record.get("linked_event_keys"),
                    "source_kam": record.get("source_kam"),
                }
            )
    if rows:
        pack["tables"].append(
            _table(
                "audit_procedures",
                "KAM 감사절차와 탐색 링크",
                [
                    ("corp_name", "회사"),
                    ("year", "연도"),
                    ("kam_topic", "KAM 주제"),
                    ("method", "감사절차 방법"),
                    ("procedure_excerpt", "감사절차"),
                    ("assertion_hints", "감사주장 힌트"),
                    ("linked_metric_keys", "연결 지표"),
                    ("linked_note_keys", "연결 주석"),
                    ("linked_event_keys", "연결 공시"),
                    ("source_kam", "원천 KAM"),
                ],
                rows,
                note=(
                    "연결 항목은 탐색을 위한 navigation aid이며 감사절차가 충분하고 "
                    "적절하게 수행되었다는 증거가 아닙니다."
                ),
            )
        )
    return pack
