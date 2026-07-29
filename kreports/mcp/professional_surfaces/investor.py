from collections.abc import Callable
from copy import deepcopy
import re
from typing import Any

PackBuilder = Callable[[dict[str, Any]], dict[str, Any] | None]
DetailRenderer = Callable[[dict[str, Any]], str]

_VERSION = "answer_pack.v1"
_PUBLIC_METRIC_LABELS = {
    "영업이익률": "영업이익률",
    "순이익률": "순이익률",
    "부채비율": "부채비율",
    "ROE": "자기자본이익률(ROE)",
    "ROA": "총자산이익률(ROA)",
    "자기자본비율": "자기자본비율",
    "매출성장률": "매출성장률",
    "Beneish_M": "베니시 M 점수",
    "감사보수": "감사보수",
}
_PUBLIC_EVENT_LABELS = {
    "treasury_buy": "자기주식 취득",
    "capital_raise": "유상증자",
    "convertible_bond": "전환사채·신주인수권부사채·교환사채",
    "merger_split": "합병·분할",
    "major_contract": "대규모 계약",
    "litigation": "소송·분쟁",
    "amendment": "정정공시",
    "control_change": "최대주주 변경",
}
_PUBLIC_TAKEAWAY_LABELS = {
    "quality_profile_supportive": "현금전환을 포함한 필수 품질 점검 충족",
    "quality_profile_mixed": "재무 품질 점검 결과 혼재",
    "financial_data_missing": "재무 데이터 미확보",
    "accounting_or_governance_risk_needs_review": "회계·지배구조 리스크 추가 검토 필요",
    "dilution_events_present": "희석 가능 자본조달 이벤트 확인",
    "shareholder_return_event_present": "주주환원 관련 이벤트 확인",
}
_PUBLIC_AGGREGATE_STATUS_LABELS = {
    "available": "집계 가능",
    "withheld_empty_cohort": "비교군 없음으로 집계 보류",
    "withheld_incomplete_cohort": "전체 비교군 미확보로 집계 보류",
}
_MACHINE_CODE = r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+"
_MACHINE_LIMITATION = re.compile(
    rf"^{_MACHINE_CODE}(?::[a-z0-9][a-z0-9_,.-]*)+$",
    re.ASCII | re.IGNORECASE,
)
_MACHINE_CODE_ONLY = re.compile(rf"^{_MACHINE_CODE}$", re.ASCII | re.IGNORECASE)
_MACHINE_PREFIX_WITH_SUFFIX = re.compile(
    rf"^(?P<code>{_MACHINE_CODE}):\s*(?P<suffix>.+)$",
    re.ASCII | re.IGNORECASE,
)
_STRUCTURED_SUPPRESSION = re.compile(
    rf"^{_MACHINE_CODE}_suppressed(?::[A-Za-z0-9][A-Za-z0-9_,.-]*)+$",
    re.ASCII | re.IGNORECASE,
)
_OPAQUE_MACHINE_SUFFIX = re.compile(
    r"^(?:[a-z][a-z0-9_]*|[A-Z][A-Za-z0-9]*(?:Error|Exception))$",
    re.ASCII,
)
_PUBLIC_VISUALIZATION_LIMITATION = (
    "표시 가능한 수치 또는 일관된 단위를 확보하지 못해 시각화를 제공하지 않습니다."
)


def _public_metric_label(value: Any) -> str:
    return _PUBLIC_METRIC_LABELS.get(str(value), "기타 재무지표")


def _public_event_label(value: Any) -> str:
    return _PUBLIC_EVENT_LABELS.get(str(value), "기타 공시 이벤트")


def _public_takeaway_label(value: Any) -> str:
    return _PUBLIC_TAKEAWAY_LABELS.get(str(value), "기타 관찰 포인트")


def _public_aggregate_status_label(value: Any) -> str:
    return _PUBLIC_AGGREGATE_STATUS_LABELS.get(str(value), "집계 상태 미확인")


def _public_limitation(value: Any) -> str:
    """Translate inherited machine limitations without exposing implementation codes."""
    text = str(value or "").strip()
    if _STRUCTURED_SUPPRESSION.fullmatch(text):
        return _PUBLIC_VISUALIZATION_LIMITATION
    if _MACHINE_LIMITATION.fullmatch(text) or _MACHINE_CODE_ONLY.fullmatch(text):
        return "세부 데이터 제한 사항이 있어 추가 확인이 필요합니다."
    if match := _MACHINE_PREFIX_WITH_SUFFIX.fullmatch(text):
        suffix = match["suffix"].strip()
        if _STRUCTURED_SUPPRESSION.fullmatch(suffix):
            return _PUBLIC_VISUALIZATION_LIMITATION
        if (
            _MACHINE_LIMITATION.fullmatch(suffix)
            or _MACHINE_CODE_ONLY.fullmatch(suffix)
            or _OPAQUE_MACHINE_SUFFIX.fullmatch(suffix)
        ):
            return "세부 데이터 제한 사항이 있어 추가 확인이 필요합니다."
        return suffix
    return text


def _public_limitations(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    return [
        _public_limitation(value)
        for value in values
        if str(value or "").strip()
    ]


def publicize_peer_result_limitations(result: dict[str, Any]) -> dict[str, Any]:
    """Copy only peer-comparison display limitations into public wording."""
    public_result = dict(result)
    if "limitations" in public_result:
        public_result["limitations"] = _public_limitations(
            public_result.get("limitations")
        )
    quality = public_result.get("data_quality")
    if isinstance(quality, dict) and "limitations" in quality:
        public_result["data_quality"] = {
            **quality,
            "limitations": _public_limitations(quality.get("limitations")),
            **(
                {"coverage_note": _public_limitation(quality["coverage_note"])}
                if quality.get("coverage_note") is not None
                else {}
            ),
        }
    elif isinstance(quality, dict) and quality.get("coverage_note") is not None:
        public_result["data_quality"] = {
            **quality,
            "coverage_note": _public_limitation(quality["coverage_note"]),
        }
    return public_result


def _publicize_pack_limitations(pack: dict[str, Any]) -> dict[str, Any]:
    """Keep every professional peer-pack limitation in Korean public language."""
    pack["limitations"] = _public_limitations(pack.get("limitations"))
    quality = pack.get("data_quality")
    if isinstance(quality, dict):
        public_quality = dict(quality)
        public_quality["limitations"] = _public_limitations(
            quality.get("limitations")
        )
        pack["data_quality"] = public_quality
    return pack


def _public_aggregate_value(value: Any, aggregate_status: Any) -> Any:
    if value is not None:
        return value
    if str(aggregate_status).startswith("withheld_"):
        return "집계 보류"
    return "미제공"


def _status(result: dict[str, Any]) -> str:
    return str((result.get("data_quality") or {}).get("status") or "usable")


def _subject(result: dict[str, Any]) -> str:
    subject = result.get("subject") or {}
    return str(subject.get("corp_name") or subject.get("stock_code") or "대상 회사")


def _pack(title: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "answer_pack", "version": _VERSION,
        "summary": {"title": title, "status": _status(result), "subject": _subject(result)},
        "tables": [], "charts": [], "diagrams": [], "timelines": [],
        "sources": [], "data_quality": result.get("data_quality") or {},
    }


def _table(table_id: str, title: str, fields: list[tuple[str, str, str | None]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": table_id, "title": title,
        "columns": [
            {"field": field, "label": label, **({"unit": unit} if unit else {})}
            for field, label, unit in fields
        ],
        "rows": rows,
    }


def _financial_snapshot_pack(result: dict[str, Any]) -> dict[str, Any]:
    pack = _pack(f"{_subject(result)} 재무 추이", result)
    unit = str(result.get("unit") or "억원")
    rows = [
        {
            "year": row.get("연도"), "fs_div": row.get("구분"),
            "revenue": row.get("매출액"), "operating_profit": row.get("영업이익"),
            "net_income": row.get("순이익"), "operating_cf": row.get("영업CF"),
            "revenue_growth": row.get("매출성장률"), "operating_margin": row.get("영업이익률"),
            "source": (row.get("source") or {}).get("rcept_no") or "사업보고서 접수번호 미확보",
        }
        for row in (result.get("rows") or [])
    ]
    if rows:
        pack["tables"].append(_table("financial_trend", "연도별 재무 추이", [
            ("year", "연도", None), ("fs_div", "FS", None),
            ("revenue", "매출", unit), ("operating_profit", "영업이익", unit),
            ("net_income", "순이익", unit), ("operating_cf", "영업현금흐름", unit),
            ("revenue_growth", "매출성장률", "%"), ("operating_margin", "영업이익률", "%"),
            ("source", "출처", None),
        ], rows))
    return pack


def _peer_selection_pack(result: dict[str, Any]) -> dict[str, Any]:
    pack = _pack(f"{_subject(result)} 비교기업 선정", result)
    rows = list(result.get("peer_selection") or [])
    if rows:
        pack["tables"].append(_table("peer_selection", "비교기업 선정 근거", [
            ("company_name", "회사", None), ("ksic", "KSIC", None),
            ("scale", "규모", "억원"), ("include_reason", "포함 근거", None),
        ], rows))
    return pack


def _peer_benchmark_pack(result: dict[str, Any]) -> dict[str, Any]:
    # Keep the established visual/chart contract, then add Task 6 coverage
    # columns rather than replacing the peer pack with a narrower variant.
    from kreports.mcp.answer_pack import _build_peer_benchmark_pack

    public_result = deepcopy(publicize_peer_result_limitations(result))
    public_result["metrics"] = [
        _public_metric_label(metric)
        for metric in (result.get("metrics") or [])
    ]
    public_result["results"] = {
        year: {
            _public_metric_label(metric): values
            for metric, values in (metrics or {}).items()
        }
        for year, metrics in (result.get("results") or {}).items()
    }
    result = public_result
    pack = _publicize_pack_limitations(_build_peer_benchmark_pack(public_result))
    for table in pack.get("tables") or []:
        if table.get("id") != "peer_metric_matrix":
            continue
        table["id"] = "industry_metrics"
        table["title"] = "비교군 지표 비교"
        for column in table["columns"]:
            label = {
                "p25": "비교군 P25 값",
                "p50": "비교군 중앙값 P50",
                "p75": "비교군 P75 값",
                "n": "비교군 표본 수(개)",
            }.get(column.get("field"))
            if label:
                column["label"] = label
        fields = [
            ("fs_div", "FS", None), ("metric_n", "지표 표본수", "개"),
            ("cohort_n", "비교군 표본수", "개"), ("missing_n", "누락/제외", "개"),
            ("observed_n", "조회된 지표 관측수", "개"),
            ("selection_truncated_n", "선정 미조회 수", "개"),
            ("aggregate_status", "집계 상태", None),
            ("cohort_digest", "비교군 재현키", None),
            ("source", "대상회사 연간 출처", None),
        ]
        existing = {column["field"] for column in table["columns"]}
        table["columns"].extend(
            {"field": field, "label": label, **({"unit": unit} if unit else {})}
            for field, label, unit in fields if field not in existing
        )
        for row in table["rows"]:
            metric_values = ((result.get("results") or {}).get(row.get("year"), {}) or {}).get(row.get("metric"), {})
            aggregate_status = metric_values.get("aggregate_status")
            row["fs_div"] = result.get("fs_div_used") or result.get("fs_div")
            row["metric_n"] = metric_values.get("metric_n", row.get("n"))
            row["cohort_n"] = metric_values.get("cohort_n", result.get("n_peers"))
            row["missing_n"] = metric_values.get("missing_n")
            row["observed_n"] = metric_values.get("observed_n")
            row["selection_truncated_n"] = metric_values.get("selection_truncated_n")
            row["aggregate_status"] = _public_aggregate_status_label(
                aggregate_status
            )
            for field in ("n", "metric_n", "missing_n", "percentile", "p25", "p50", "p75"):
                row[field] = _public_aggregate_value(
                    row.get(field), aggregate_status
                )
            row["cohort_digest"] = _public_aggregate_value(
                metric_values.get("cohort_digest"), ""
            )
            row["source"] = (
                (metric_values.get("source") or {}).get("rcept_no")
                or "사업보고서 접수번호 미확보"
            )
    for chart in pack.get("charts") or []:
        if chart.get("data_ref") == "peer_metric_matrix":
            chart["data_ref"] = "industry_metrics"
        if isinstance(chart.get("title"), str):
            chart["title"] = chart["title"].replace("Peer", "비교군")
    return pack


def _investor_signals_pack(result: dict[str, Any]) -> dict[str, Any]:
    from kreports.mcp.answer_pack import _build_investor_signals_pack

    public_result = deepcopy(result)
    public_result["event_counts"] = {
        _public_event_label(event_type): count
        for event_type, count in (result.get("event_counts") or {}).items()
    }
    public_result["takeaways"] = [
        _public_takeaway_label(takeaway)
        for takeaway in (result.get("takeaways") or [])
    ]
    pack = _build_investor_signals_pack(public_result)
    quality = result.get("quality_snapshot") or {}
    checks = quality.get("checks") or {}
    rows = [
        {"name": value.get("name") or key, "value": value.get("value"),
         "status": value.get("status"), "meaning": value.get("meaning")}
        for key, value in checks.items() if isinstance(value, dict)
    ]
    if rows:
        pack["tables"].append(_table("investor_checks", "재무 품질 점검", [
            ("name", "점검", None), ("value", "값", None),
            ("status", "상태", None), ("meaning", "의미", None),
        ], rows))
    return pack


def _dcf_candidates_pack(result: dict[str, Any]) -> dict[str, Any]:
    from kreports.mcp.answer_pack import _build_dcf_pack

    pack = _build_dcf_pack(result)
    for table in pack.get("tables") or []:
        if table.get("id") == "candidate_assumptions":
            table["id"] = "dcf_candidates"
    for chart in pack.get("charts") or []:
        if chart.get("data_ref") == "candidate_assumptions":
            chart["data_ref"] = "dcf_candidates"
    blockers = [
        blocker for blocker in result.get("valuation_blockers") or []
        if isinstance(blocker, dict)
    ]
    if blockers:
        pack["tables"].append(_table(
            "valuation_blockers",
            "가치평가 준비도 차단 요인",
            [
                ("field", "필수 입력", None), ("kind", "차단 유형", None),
                ("impact", "영향", None), ("owner", "담당", None),
                ("next_action", "다음 조치", None),
            ], blockers,
        ))
    pack["summary"]["status"] = str(
        result.get("valuation_readiness") or "blocked"
    )
    return pack


def _dcf_model_pack(result: dict[str, Any]) -> dict[str, Any]:
    from kreports.mcp.answer_pack import _build_dcf_model_pack

    return _build_dcf_model_pack(result)


def _quality_of_earnings_pack(result: dict[str, Any]) -> dict[str, Any]:
    from kreports.mcp.answer_pack import _build_quality_pack

    pack = _build_quality_pack(result)
    for table in pack.get("tables") or []:
        if table.get("id") == "quality_metrics":
            table["id"] = "quality_of_earnings"
            break
    else:
        for table in pack.get("tables") or []:
            if table.get("id") == "quality_signals":
                table["id"] = "quality_of_earnings"
                break
    summary = result.get("audit_matter_summary") or {}
    if summary:
        pack["tables"].append(_table(
            "audit_matter_summary",
            "감사보고서 matter 집계 기준",
            [
                ("unique_receipt_count", "고유 접수번호 수", "건"),
                ("section_count", "원 문단 수", "건"),
                ("dedupe_basis", "중복 제거 기준", None),
            ],
            [{
                "unique_receipt_count": summary.get(
                    "unique_receipt_count", 0,
                ),
                "section_count": summary.get("section_count", 0),
                "dedupe_basis": summary.get("dedupe_basis"),
            }],
        ))
    groups = [
        {
            "year": group.get("year"),
            "matter_type": group.get("matter_type"),
            "severity": group.get("severity"),
            "section_count": group.get("section_count"),
            "rcept_no": (group.get("source") or {}).get("rcept_no"),
        }
        for group in summary.get("groups") or []
        if isinstance(group, dict)
    ]
    if groups:
        pack["tables"].append(_table(
            "audit_matter_groups",
            "감사보고서 matter 수신처별 집계",
            [
                ("year", "사업연도", None), ("matter_type", "matter 유형", None),
                ("severity", "강도", None), ("section_count", "문단 수", "건"),
                ("rcept_no", "감사보고서 접수번호", None),
            ], groups,
        ))
        known = {str(source.get("rcept_no") or "") for source in pack["sources"]}
        for group in groups:
            receipt = str(group.get("rcept_no") or "")
            if not receipt or receipt in known:
                continue
            known.add(receipt)
            pack["sources"].append({
                "label": "감사보고서 matter",
                "rcept_no": receipt,
                "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}",
            })
    return pack


def _render_dcf_candidates(result: dict[str, Any]) -> str:
    return "\n".join([
        f"DCF 입력 후보 상태: {result.get('candidate_status') or _status(result)}",
        f"가치평가 준비도: {result.get('valuation_readiness') or 'blocked'}",
    ])


def _render_dcf_model(result: dict[str, Any]) -> str:
    if result.get("enterprise_value") is None:
        return "산출 불가: 필수 입력 또는 공시 실제값이 부족하여 기업가치를 계산하지 않았습니다."
    from kreports.mcp.renderers import _render_dcf_model_pack

    return _render_dcf_model_pack(result)


def _render_quality_of_earnings(result: dict[str, Any]) -> str:
    summary = result.get("audit_matter_summary") or {}
    status = (result.get("data_quality") or {}).get("status") or "limited"
    lines = [
        f"판정: {status}",
        f"감사보고서 matter: 고유 접수번호 {summary.get('unique_receipt_count', 0)}건, "
        f"문단 {summary.get('section_count', 0)}건입니다.",
        (
            "중복 제거 기준: "
            f"{summary.get('dedupe_basis') or '확인 불가'}"
        ),
    ]
    for group in summary.get("groups") or []:
        if not isinstance(group, dict):
            continue
        receipt = str((group.get("source") or {}).get("rcept_no") or "")
        if not receipt:
            continue
        lines.append(
            f"- {group.get('year')}년 {group.get('matter_type')}: "
            f"감사보고서 접수번호 {receipt} "
            f"(https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt})"
        )
    return "\n".join(lines)


def _disclosure_events_pack(result: dict[str, Any]) -> dict[str, Any]:
    from kreports.mcp.answer_pack import _build_disclosure_events_pack

    public_result = deepcopy(result)
    public_result["events"] = [
        {**event, "event_type": _public_event_label(event.get("event_type"))}
        for event in (result.get("events") or []) if isinstance(event, dict)
    ]
    public_result["event_type_counts"] = {
        _public_event_label(event_type): count
        for event_type, count in (result.get("event_type_counts") or {}).items()
    }
    pack = _build_disclosure_events_pack(public_result)
    for table in pack.get("tables") or []:
        if table.get("id") == "disclosure_events":
            for column in table["columns"]:
                if column["field"] == "event_type":
                    column["label"] = "KReports 스크리닝 분류"
    return pack


def _render_financial_snapshot(result: dict[str, Any]) -> str:
    rows = (result.get("rows") or [])[-5:]
    lines = [f"판정: {_status(result)}", "", f"{_subject(result)}의 최근 연간 재무 추이입니다.", "", "| 연도 | FS | 매출 | 영업이익 | 순이익 | 영업현금흐름 | 매출성장률 | 영업이익률 |", "|---:|---|---:|---:|---:|---:|---:|---:|"]
    for row in rows:
        lines.append("| {0} | {1} | {2} | {3} | {4} | {5} | {6} | {7} |".format(
            row.get("연도"), row.get("구분"), row.get("매출액"), row.get("영업이익"),
            row.get("순이익"), row.get("영업CF"), row.get("매출성장률"), row.get("영업이익률"),
        ))
    return "\n".join(lines)


def _render_investor_signals(result: dict[str, Any]) -> str:
    quality = result.get("quality_snapshot") or {}
    checks = quality.get("checks") or {}
    lines = [f"판정: {_status(result)}", "", f"{_subject(result)} 투자자 신호 요약입니다. 재무 품질, 회계 리스크, 최근 공시 이벤트를 함께 보는 1차 점검입니다.", f"- 평가 완료 {quality.get('evaluated_count', quality.get('passed_checks', 0))}건, 미확보 {quality.get('unknown_count', 0)}건", "", "| 점검 | 상태 | 값 |", "|---|---:|---:|"]
    for key, check in checks.items():
        if isinstance(check, dict):
            lines.append(f"| {check.get('name') or key} | {check.get('status')} | {check.get('value')} |")
    lines.extend(["", "리스크/이벤트 요약:", "- 현금전환 미확보 항목은 긍정적 품질 결론에 사용하지 않습니다."])
    return "\n".join(lines)


def _render_peer_selection(result: dict[str, Any]) -> str:
    lines = [f"판정: {_status(result)}", "", f"{_subject(result)} 비교기업 선정 결과입니다.", "", "| 회사 | KSIC | 규모 | 포함 근거 |", "|---|---|---:|---|"]
    for row in (result.get("peer_selection") or [])[:20]:
        lines.append(f"| {row.get('company_name')} | {row.get('ksic')} | {row.get('scale')} | {row.get('include_reason')} |")
    return "\n".join(lines)


def _render_peer_benchmark(result: dict[str, Any]) -> str:
    lines = [f"판정: {_status(result)}", "", f"{_subject(result)} Peer 벤치마크 결과입니다. 동종업종 상대 위치를 확인하는 스크리닝입니다.", "", "| 연도 | 지표 | 대상회사 | 백분위 | P25 | P50 | P75 | 비교군 표본 수 |", "|---:|---|---:|---:|---:|---:|---:|---:|"]
    for year, metrics in (result.get("results") or {}).items():
        for metric, values in (metrics or {}).items():
            if isinstance(values, dict):
                aggregate_status = values.get("aggregate_status")
                lines.append(
                    f"| {year} | {_public_metric_label(metric)} | "
                    f"{_public_aggregate_value(values.get('subject_value'), '')} | "
                    f"{_public_aggregate_value(values.get('percentile'), aggregate_status)} | "
                    f"{_public_aggregate_value(values.get('p25'), aggregate_status)} | "
                    f"{_public_aggregate_value(values.get('p50'), aggregate_status)} | "
                    f"{_public_aggregate_value(values.get('p75'), aggregate_status)} | "
                    f"{_public_aggregate_value(values.get('metric_n', values.get('n')), aggregate_status)} |"
                )
    lines.extend(["", "표본/출처:", "- 지표 표본수, 비교군 표본수, 누락/제외 수와 비교군 재현키는 표에서 확인할 수 있습니다.", "- 비교기업 개별 식별자와 내부 계산키는 표시하지 않습니다."])
    return "\n".join(lines)


def _render_disclosure_events(result: dict[str, Any]) -> str:
    lines = [f"판정: {_status(result)}", "", "캐시된 공시 제목·일자·접수번호로 만든 이벤트 목록입니다.", "- event_type은 KReports 스크리닝 분류이며, 지배구조 변경의 확정 정보가 아닙니다."]
    for event in (result.get("events") or [])[:5]:
        lines.append(f"- {event.get('event_date')} {event.get('corp_name')}: KReports 스크리닝 분류 {_public_event_label(event.get('event_type'))} / {event.get('event_title')}")
    return "\n".join(lines)


PACK_BUILDERS: dict[str, PackBuilder] = {
    "get_financial_snapshot": _financial_snapshot_pack,
    "select_peer_group": _peer_selection_pack,
    "compare_to_industry_multi": _peer_benchmark_pack,
    "get_investor_signals": _investor_signals_pack,
    "search_disclosure_events": _disclosure_events_pack,
    "get_dcf_input_candidates": _dcf_candidates_pack,
    "build_dcf_model_pack": _dcf_model_pack,
    "get_quality_of_earnings_pack": _quality_of_earnings_pack,
}
DETAIL_RENDERERS: dict[str, DetailRenderer] = {
    "get_financial_snapshot": _render_financial_snapshot,
    "select_peer_group": _render_peer_selection,
    "compare_to_industry_multi": _render_peer_benchmark,
    "get_investor_signals": _render_investor_signals,
    "search_disclosure_events": _render_disclosure_events,
    "get_dcf_input_candidates": _render_dcf_candidates,
    "build_dcf_model_pack": _render_dcf_model,
    "get_quality_of_earnings_pack": _render_quality_of_earnings,
}
