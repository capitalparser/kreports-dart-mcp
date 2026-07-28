from collections.abc import Callable
from typing import Any

PackBuilder = Callable[[dict[str, Any]], dict[str, Any] | None]
DetailRenderer = Callable[[dict[str, Any]], str]

_VERSION = "answer_pack.v1"


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

    pack = _build_peer_benchmark_pack(result)
    for table in pack.get("tables") or []:
        if table.get("id") != "peer_metric_matrix":
            continue
        fields = [
            ("fs_div", "FS", None), ("metric_n", "지표 표본수", "개"),
            ("cohort_n", "Cohort 표본수", "개"), ("missing_n", "누락/제외", "개"),
            ("cohort_digest", "Cohort 재현키", None),
        ]
        existing = {column["field"] for column in table["columns"]}
        table["columns"].extend(
            {"field": field, "label": label, **({"unit": unit} if unit else {})}
            for field, label, unit in fields if field not in existing
        )
        for row in table["rows"]:
            metric_values = ((result.get("results") or {}).get(row.get("year"), {}) or {}).get(row.get("metric"), {})
            row["fs_div"] = result.get("fs_div_used") or result.get("fs_div")
            row["metric_n"] = metric_values.get("metric_n", row.get("n"))
            row["cohort_n"] = metric_values.get("cohort_n", result.get("n_peers"))
            row["missing_n"] = metric_values.get("missing_n")
            row["cohort_digest"] = metric_values.get("cohort_digest")
    return pack


def _investor_signals_pack(result: dict[str, Any]) -> dict[str, Any]:
    from kreports.mcp.answer_pack import _build_investor_signals_pack

    pack = _build_investor_signals_pack(result)
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


def _disclosure_events_pack(result: dict[str, Any]) -> dict[str, Any]:
    from kreports.mcp.answer_pack import _build_disclosure_events_pack

    pack = _build_disclosure_events_pack(result)
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
    lines = [f"판정: {_status(result)}", "", f"{_subject(result)} Peer 벤치마크 결과입니다. 동종업종 상대 위치를 확인하는 스크리닝입니다.", "", "| 연도 | 지표 | 대상회사 | 백분위 | P25 | P50 | P75 | Peer 수 |", "|---:|---|---:|---:|---:|---:|---:|---:|"]
    for year, metrics in (result.get("results") or {}).items():
        for metric, values in (metrics or {}).items():
            if isinstance(values, dict):
                lines.append(f"| {year} | {metric} | {values.get('subject_value')} | {values.get('percentile')} | {values.get('p25')} | {values.get('p50')} | {values.get('p75')} | {values.get('metric_n', values.get('n'))} |")
    lines.extend(["", "표본/출처:", "- 지표 표본수, cohort 표본수, 누락/제외 수와 cohort 재현키는 answer_pack에서 확인할 수 있습니다.", "- peer 개별 식별자와 내부 계산키는 표시하지 않습니다."])
    return "\n".join(lines)


def _render_disclosure_events(result: dict[str, Any]) -> str:
    lines = [f"판정: {_status(result)}", "", "캐시된 공시 제목·일자·접수번호로 만든 이벤트 목록입니다.", "- event_type은 KReports 스크리닝 분류이며, 지배구조 변경의 확정 정보가 아닙니다."]
    for event in (result.get("events") or [])[:5]:
        lines.append(f"- {event.get('event_date')} {event.get('corp_name')}: KReports 스크리닝 분류 {event.get('event_type')} / {event.get('event_title')}")
    return "\n".join(lines)


PACK_BUILDERS: dict[str, PackBuilder] = {
    "get_financial_snapshot": _financial_snapshot_pack,
    "select_peer_group": _peer_selection_pack,
    "compare_to_industry_multi": _peer_benchmark_pack,
    "get_investor_signals": _investor_signals_pack,
    "search_disclosure_events": _disclosure_events_pack,
}
DETAIL_RENDERERS: dict[str, DetailRenderer] = {
    "get_financial_snapshot": _render_financial_snapshot,
    "select_peer_group": _render_peer_selection,
    "compare_to_industry_multi": _render_peer_benchmark,
    "get_investor_signals": _render_investor_signals,
    "search_disclosure_events": _render_disclosure_events,
}
