"""Structured visual answer packs for MCP responses.

The pack is an additive, renderer-friendly contract. It does not replace the
existing dict response or Korean narrative `answer`; clients that can render
charts/diagrams can use it, and plain MCP clients can ignore it.
"""
from __future__ import annotations

import html
from typing import Any

from kreports.analysis.evidence import parent_rcept_no
from kreports.mcp.contracts import build_answer_envelope


PACK_VERSION = "answer_pack.v1"


def build_answer_pack(tool_name: str, result: dict[str, Any]) -> dict[str, Any] | None:
    """Return a visual answer pack for known tool outputs."""
    if not isinstance(result, dict) or "error" in result:
        return None

    envelope = build_answer_envelope(tool_name, result)
    normalized_result = dict(result)
    normalized_quality = dict(result.get("data_quality") or {})
    normalized_quality.update(envelope.data_quality.model_dump())
    normalized_result["data_quality"] = normalized_quality

    builders = {
        "get_dcf_input_candidates": _build_dcf_pack,
        "get_quality_of_earnings_pack": _build_quality_pack,
        "get_investor_signals": _build_investor_signals_pack,
        "get_subsidiary_auditors": _build_subsidiary_pack,
        "search_disclosure_events": _build_disclosure_events_pack,
        "search_audit_procedures": _build_audit_procedure_pack,
        "compare_to_industry_multi": _build_peer_benchmark_pack,
    }
    builder = builders.get(tool_name)
    if builder is None:
        return _build_generic_pack(tool_name, normalized_result)
    return builder(normalized_result)


def _base_pack(title: str, result: dict[str, Any], *, status: str | None = None) -> dict[str, Any]:
    return {
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


def _columns(fields: list[tuple[str, str]]) -> list[dict[str, str]]:
    return [{"field": field, "label": label} for field, label in fields]


def _table(
    table_id: str,
    title: str,
    columns: list[tuple[str, str]],
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
    for fact in result.get("confirmed_facts") or []:
        if not isinstance(fact, dict):
            continue
        source = fact.get("source")
        if isinstance(source, dict):
            add(_source_from_rcept_no(source.get("rcept_no"), label=source.get("report_nm") or source.get("section_title")))
    for event in result.get("events") or []:
        if isinstance(event, dict):
            add(_source_from_rcept_no(event.get("rcept_no"), label=event.get("event_title") or event.get("report_nm")))
    return sources


def _build_dcf_pack(result: dict[str, Any]) -> dict[str, Any]:
    subject = _subject_label(result)
    pack = _base_pack(f"{subject} DCF 입력 후보", result)
    actuals = list(result.get("historical_actuals") or [])
    if actuals:
        pack["tables"].append(_table(
            "historical_actuals",
            "5개년 실적치",
            [
                ("year", "연도"),
                ("revenue", "매출"),
                ("operating_profit", "영업이익"),
                ("profit_loss", "순이익"),
                ("net_income", "순이익"),
                ("operating_cf", "영업현금흐름"),
                ("purchase_ppe", "유형자산 취득"),
            ],
            actuals,
        ))
        pack["charts"].append(_chart(
            "financial_trend",
            "line",
            "매출·영업이익·영업현금흐름 추이",
            data_ref="historical_actuals",
            encodings={
                "x": {"field": "year"},
                "y": {"fields": ["revenue", "operating_profit", "operating_cf"]},
            },
        ))

    assumption_rows = [
        {"metric": key, **(value if isinstance(value, dict) else {"value": value})}
        for key, value in (result.get("candidate_assumptions") or {}).items()
    ]
    if assumption_rows:
        pack["tables"].append(_table(
            "candidate_assumptions",
            "DCF 입력 후보",
            [("metric", "입력값"), ("value", "값"), ("basis", "근거")],
            assumption_rows,
        ))
        pack["charts"].append(_chart(
            "dcf_input_bridge",
            "bar",
            "공시 기반 입력 후보",
            data_ref="candidate_assumptions",
            encodings={"x": {"field": "metric"}, "y": {"field": "value"}},
            note="가치평가 결론이 아니라 공시 기반 입력 후보입니다.",
        ))
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
            [("event_type", "이벤트"), ("count", "건수")],
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
            rows[-1]["asset_amount_m"] = float(item["asset_amount"]) / 1_000_000
        if item.get("revenue_amount_m") is None and item.get("revenue_amount") is not None:
            rows[-1]["revenue_amount_m"] = float(item["revenue_amount"]) / 1_000_000
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
        pack["charts"].append(_chart(
            "entity_asset_contribution",
            "bar",
            "실체별 자산비중",
            data_ref="subsidiary_contribution",
            encodings={"x": {"field": "name"}, "y": {"field": "asset_share_pct"}},
        ))
        pack["charts"].append(_chart(
            "entity_revenue_contribution",
            "bar",
            "실체별 매출비중",
            data_ref="subsidiary_contribution",
            encodings={"x": {"field": "name"}, "y": {"field": "revenue_share_pct"}},
        ))
    pack["diagrams"].append({
        "id": "subsidiary_structure",
        "type": "mermaid",
        "title": "연결실체 구조도",
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
    lines = ["flowchart TD", f'  P["{_mermaid_label(subject)}<br/>{year or ""}년 연결실체"]']
    visible = _hierarchy_closed_rows(subsidiaries, limit=8)
    node_ids = {
        str(item.get("entity_key") or f"row:{idx}"): f"N{idx}"
        for idx, item in enumerate(visible, start=1)
    }
    for idx, item in enumerate(visible, start=1):
        label = (
            f"{item.get('relation') or '-'} / 지분율 {_fmt_pct(item.get('ownership_pct'))}<br/>"
            f"자산 {_fmt_pct(item.get('asset_share_pct'))} / 매출 {_fmt_pct(item.get('revenue_share_pct'))}"
        )
        qsc = _qsc_label(item.get("qsc_status"))
        parent_key = str(item.get("parent_entity_key") or "")
        parent_node = node_ids.get(parent_key, "P") if canonical else "P"
        lines.append(
            f'  {parent_node} -->|"{_mermaid_label(label)}"| N{idx}["{_mermaid_label(item.get("name"))}<br/>{_mermaid_label(qsc)}"]'
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


def _mermaid_label(value: Any) -> str:
    return (
        html.escape(str(value or "-"), quote=True)
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
    timeline_events = []
    for event in events:
        row = {
            "event_date": event.get("event_date"),
            "corp_name": event.get("corp_name"),
            "event_type": event.get("event_type"),
            "event_title": event.get("event_title"),
            "rcept_no": parent_rcept_no(str(event.get("rcept_no") or "")),
        }
        event_rows.append(row)
        timeline_events.append({
            "date": row["event_date"],
            "title": row["event_title"],
            "entity": row["corp_name"],
            "event_type": row["event_type"],
            "rcept_no": row["rcept_no"],
        })
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
            "events": timeline_events,
        })
    counts = result.get("event_type_counts") or {}
    if counts:
        rows = [{"event_type": key, "count": value} for key, value in counts.items()]
        pack["charts"].append(_chart(
            "event_type_distribution",
            "bar",
            "공시 이벤트 유형 분포",
            data_ref="event_type_counts",
            rows=rows,
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
                ("subject_value", "대상회사"),
                ("percentile", "백분위"),
                ("p25", "P25"),
                ("p50", "P50"),
                ("p75", "P75"),
                ("n", "Peer 수"),
            ],
            rows,
        ))
        pack["charts"].append(_chart(
            "peer_percentile_matrix",
            "heatmap",
            "Peer 백분위 매트릭스",
            data_ref="peer_metric_matrix",
            encodings={"x": {"field": "year"}, "y": {"field": "metric"}, "color": {"field": "percentile"}},
        ))
        pack["charts"].append(_chart(
            "peer_band",
            "band",
            "대상회사 vs Peer 사분위",
            data_ref="peer_metric_matrix",
            encodings={
                "x": {"field": "year"},
                "y": {"field": "subject_value"},
                "band": {"fields": ["p25", "p50", "p75"]},
                "series": {"field": "metric"},
            },
        ))
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
