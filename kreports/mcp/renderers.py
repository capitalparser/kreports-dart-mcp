"""User-facing narrative renderers for MCP tool responses."""
from __future__ import annotations

import html
import re
from typing import Any

from kreports.analysis.evidence import parent_rcept_no, source_line
from kreports.mcp.auditor_public import (
    public_kam_lifecycle_events,
    public_kam_topic_label,
)
from kreports.mcp.contracts import (
    AnswerEnvelopeV1,
    build_answer_envelope,
    normalize_answer_result,
    public_domain_verdict_label,
)
from kreports.mcp.professional_surfaces import DETAIL_RENDERERS as PROFESSIONAL_DETAIL_RENDERERS
from kreports.mcp.professional_surfaces import (
    CONCLUSION_OVERRIDES as PROFESSIONAL_CONCLUSION_OVERRIDES,
)


_PUBLIC_SOURCE_LABELS = {
    "accounting_note_chapters": "회계정책 주석 캐시",
    "audit_matter_items": "감사보고서 항목 캐시",
    "audit_procedure_items": "감사절차 항목 캐시",
    "evidence_documents": "공시 근거 캐시",
    "financial_facts_compact": "재무 공시 캐시",
    "local_subsidiary_auditor_matrix": "연결실체 감사인 캐시",
    "report_sections": "보고서 섹션 캐시",
    "report_sections.audit_report": "감사보고서 캐시",
    "source_documents": "원문 문서 캐시",
}


def _public_source_label(value: Any, fallback: str = "로컬 공시 캐시") -> str:
    return _PUBLIC_SOURCE_LABELS.get(str(value or ""), fallback)


def _status(result: dict) -> str:
    data_quality = result.get("data_quality")
    if isinstance(data_quality, dict) and data_quality.get("status"):
        return str(data_quality["status"])
    return "usable" if "error" not in result else "fail"


def _subject_label(result: dict) -> str:
    subject = result.get("subject")
    if isinstance(subject, dict):
        return str(subject.get("corp_name") or subject.get("stock_code") or subject.get("corp_code") or "대상 회사")
    query = result.get("query")
    if isinstance(query, dict):
        return str(query.get("company") or query.get("market") or "대상 조건")
    meta = result.get("_meta")
    if isinstance(meta, dict):
        company = meta.get("company")
        if isinstance(company, dict):
            return str(company.get("corp_name") or company.get("stock_code") or company.get("corp_code") or "대상 회사")
    return "대상 조건"


def _fmt_qsc_status(value: Any) -> str:
    if value == "qsc":
        return "QSC"
    if value == "not_qsc":
        return "비QSC"
    if value == "undetermined":
        return "미판정"
    return "미판정"


def _first_record_line(company: dict) -> str | None:
    records = company.get("records") or []
    if not records:
        return None
    record = records[0]
    parts = [
        str(company.get("corp_name") or company.get("corp_code") or "-"),
        str(record.get("year") or record.get("bsns_year") or "-"),
    ]
    for key in ("rcept_no", "section_title", "section_key", "note_title", "item_key"):
        value = record.get(key)
        if value:
            parts.append(str(value))
    return " / ".join(parts)


def _dedupe_display_records(records: list[dict], *, text_key: str, title_key: str = "section_title") -> list[dict]:
    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for record in records:
        title = str(record.get(title_key) or record.get("section_key") or "")
        text = re.sub(r"\s+", " ", str(record.get(text_key) or "")).strip()[:220]
        key = (title, text)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return deduped


def _render_search_dataset(result: dict) -> str:
    query = result.get("query") or {}
    dataset = query.get("dataset") or "dataset"
    dataset_label = _public_source_label(dataset, "공시 데이터")
    status = _status(result)
    total_companies = result.get("total_companies", 0)
    total_records = result.get("total_records", 0)
    subject = _subject_label(result)

    lines = [
        f"판정: {status}",
        "",
        f"{subject} 조건으로 {dataset_label}에서 조회한 결과, 회사 {total_companies}개와 근거 레코드 {total_records}건이 확인됩니다.",
    ]
    year = query.get("year")
    if year:
        lines.append(f"조회 연도는 {year}년입니다.")

    evidence = []
    for company in (result.get("companies") or [])[:3]:
        line = _first_record_line(company)
        if line:
            evidence.append(line)
    lines.append("")
    lines.append("근거:")
    if evidence:
        lines.extend(f"- {line}" for line in evidence)
    else:
        lines.append("- 현재 로컬 캐시에서 조건에 맞는 근거 레코드를 찾지 못했습니다.")

    data_quality = result.get("data_quality") or {}
    lines.append("")
    lines.append("데이터 한계:")
    lines.append(f"- 출처: {_public_source_label(data_quality.get('source') or dataset)}")
    lines.append(f"- {data_quality.get('interpretation') or '현재 결과는 로컬 캐시 기준입니다.'}")
    return "\n".join(lines)


def _fmt_amount_m(value: Any) -> str:
    if value is None:
        return "미확보"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(number) >= 100:
        return f"{number:,.0f}"
    return f"{number:,.1f}"


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "미확보"
    try:
        return f"{float(value):,.1f}%"
    except (TypeError, ValueError):
        return str(value)


def _fmt_ownership(value: Any) -> str:
    return _fmt_pct(value) if value is not None else "미기재"


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


def _render_subsidiary_auditors(result: dict) -> str:
    subject = _subject_label(result)
    safe_subject = _markdown_cell(subject)
    year = result.get("bsns_year")
    graph = result.get("group_graph") if isinstance(result.get("group_graph"), dict) else {}
    canonical = bool(graph.get("entities"))
    subsidiaries = graph.get("entities") or result.get("subsidiaries") or []
    totals = result.get("consolidated_totals") or {}
    qsc_criterion = result.get("qsc_criterion") or {}
    data_quality = result.get("data_quality") or {}
    rcept_no = parent_rcept_no(str(result.get("parent_rcept_no") or "")) if result.get("parent_rcept_no") else None

    asset_total = _fmt_amount_m(totals.get("assets_amount_m"))
    revenue_total = _fmt_amount_m(totals.get("revenue_amount_m"))
    qsc_threshold = qsc_criterion.get("threshold_pct", 10.0)
    safe_status = _markdown_cell(_status(result))
    safe_year = _markdown_cell(year or "")
    safe_asset_total = _markdown_cell(asset_total)
    safe_revenue_total = _markdown_cell(revenue_total)
    safe_qsc_threshold = _markdown_cell(qsc_threshold)
    lines = [
        f"판정: {safe_status}",
        "",
        f"{safe_subject} {safe_year}년 연결·투자 실체 조회 결과입니다. 연결 총자산은 {safe_asset_total}백만원, 연결 매출은 {safe_revenue_total}백만원 기준으로 각 실체의 기여도를 표시합니다.",
        f"QSC 기준은 연결 총자산 또는 연결 총매출 대비 {safe_qsc_threshold}% 이상입니다.",
        "",
        "구조도:",
        "```mermaid",
        "flowchart TD",
        (
            f'  P["{_mermaid_label(subject)}<br/>'
            f'{_mermaid_label(year or "")}년 연결실체"]'
        ),
    ]
    visible = _hierarchy_closed_group_rows(subsidiaries, limit=8)
    node_ids = {
        str(item.get("entity_key") or f"row:{idx}"): f"N{idx}"
        for idx, item in enumerate(visible, start=1)
    }
    for idx, item in enumerate(visible, start=1):
        relation = item.get("relation") or "-"
        ownership = _fmt_ownership(item.get("ownership_pct"))
        asset_share = _fmt_pct(item.get("asset_share_pct"))
        revenue_share = _fmt_pct(item.get("revenue_share_pct"))
        qsc_status = _fmt_qsc_status(item.get("qsc_status"))
        parent_node = node_ids.get(str(item.get("parent_entity_key") or ""), "P")
        edge_label = (
            f"{_mermaid_label(relation)} / 지분율 "
            f"{_mermaid_label(ownership)}"
            "<br/>"
            f"자산 {_mermaid_label(asset_share)} / 매출 "
            f"{_mermaid_label(revenue_share)}"
        )
        lines.append(
            f'  {parent_node} -->|"{edge_label}"| '
            f'N{idx}["{_mermaid_label(item.get("name"))}<br/>'
            f'{_mermaid_label(qsc_status)}"]'
        )
    if len(subsidiaries) > len(visible):
        lines.append(
            f'  OMIT["{len(subsidiaries) - len(visible)}개 노드는 가독성을 위해 생략"]'
        )
    if not subsidiaries:
        lines.append('  P -->|"캐시 없음"| N0["연결/투자 실체 미확보"]')
    lines.extend(["```", "", "표:"])
    if canonical:
        lines.append("| 회사 | 관계 | 지분율 | 자산(백만원) | 자산비중 | 매출(백만원) | 매출비중 | QSC | 감사인 | 출처 접수번호 |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---|---|")
    else:
        lines.append("| 회사 | 관계 | 지분율 | 자산(백만원) | 자산비중 | 매출(백만원) | 매출비중 | QSC | 감사인 |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---|")
    for item in subsidiaries:
        auditor = item.get("auditor")
        auditor_name = auditor.get("auditor_nm") if isinstance(auditor, dict) else None
        asset_amount_m = item.get("asset_amount_m")
        if asset_amount_m is None and item.get("asset_amount") is not None:
            try:
                asset_amount_m = float(item["asset_amount"]) / 1_000_000
            except (TypeError, ValueError):
                asset_amount_m = item["asset_amount"]
        revenue_amount_m = item.get("revenue_amount_m")
        if revenue_amount_m is None and item.get("revenue_amount") is not None:
            try:
                revenue_amount_m = float(item["revenue_amount"]) / 1_000_000
            except (TypeError, ValueError):
                revenue_amount_m = item["revenue_amount"]
        lines.append(
            f"| {_markdown_cell(item.get('name'))} "
            f"| {_markdown_cell(item.get('relation'))} "
            f"| {_markdown_cell(_fmt_ownership(item.get('ownership_pct')))} "
            f"| {_markdown_cell(_fmt_amount_m(asset_amount_m))} "
            f"| {_markdown_cell(_fmt_pct(item.get('asset_share_pct')))} "
            f"| {_markdown_cell(_fmt_amount_m(revenue_amount_m))} "
            f"| {_markdown_cell(_fmt_pct(item.get('revenue_share_pct')))} "
            f"| {_markdown_cell(_fmt_qsc_status(item.get('qsc_status')))} "
            f"| {_markdown_cell(auditor_name)} "
            + (
                f"| {_markdown_cell(item.get('source_rcept_no'))} |"
                if canonical else "|"
            )
        )
    if not subsidiaries:
        lines.append(
            "| 미확보 | - | - | 미확보 | 미확보 | 미확보 | 미확보 | 미판정 | - | - |"
            if canonical
            else "| 미확보 | - | - | 미확보 | 미확보 | 미확보 | 미확보 | 미판정 | - |"
        )

    lines.append("")
    lines.append("근거:")
    if rcept_no:
        lines.append(f"- 접수번호: {rcept_no}")
        lines.append(f"- 공시 링크: https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}")
    lines.append(
        f"- 반환 {_markdown_cell(result.get('count', len(subsidiaries)))}건 "
        f"/ 전체 {_markdown_cell(result.get('total', len(subsidiaries)))}건"
    )
    if result.get("truncated"):
        lines.append("- 결과가 잘렸습니다. 전체 구조 확인이 필요하면 limit을 늘려 재조회해야 합니다.")
    if graph.get("truncated"):
        lines.append("- 상위 그래프 결과가 잘렸습니다. 현재 표는 반환된 행 전체만 포함합니다.")
    if len(subsidiaries) > len(visible):
        lines.append(
            f"- 구조도는 가독성을 위해 {len(subsidiaries) - len(visible)}개 노드를 생략했지만 표에는 반환 행 전체를 표시했습니다."
        )

    coverage_note = data_quality.get("coverage_note")
    lines.append("")
    lines.append("데이터 한계:")
    lines.append(
        "- 출처: "
        f"{_markdown_cell(_public_source_label(data_quality.get('source') or 'local_subsidiary_auditor_matrix'))}"
    )
    lines.append(
        f"- {_markdown_cell(coverage_note or '현재 결과는 로컬 사업보고서 파생 캐시 기준입니다.')}"
    )
    for limitation in result.get("limitations") or []:
        lines.append(f"- 제한: {_markdown_cell(limitation)}")
    return "\n".join(lines)


def _markdown_cell(value: Any) -> str:
    return (
        html.escape(_normalized_markup_text(value), quote=True)
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("[", "&#91;")
        .replace("]", "&#93;")
        .replace("\n", "<br/>")
    )


def _hierarchy_closed_group_rows(
    rows: list[dict],
    *,
    limit: int,
) -> list[dict]:
    child_keys = {
        str(row.get("entity_key") or "")
        for row in rows
        if row.get("entity_key")
    }
    visible: list[dict] = []
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


_PUBLIC_MATTER_LABELS = {
    "other_matter": "기타사항",
    "basis_for_opinion": "의견근거",
    "emphasis": "강조사항",
    "going_concern": "계속기업 관련 문단",
    "covid": "코로나19 영향",
    "subsequent_event": "후속사건",
    "restatement": "재작성·정정",
    "litigation": "소송·분쟁",
    "scope_limitation": "감사범위 제한",
    "uncertainty": "중요한 불확실성",
}


def _public_kam_topic(value: object) -> str:
    return public_kam_topic_label(value)


def _public_matter_label(value: object) -> str:
    return _PUBLIC_MATTER_LABELS.get(
        str(value or ""),
        "기타 감사보고서 사항",
    )


def _render_kam_topics(result: dict) -> str:
    status = _status(result)
    subject = _subject_label(result)
    year = result.get("year")
    topics = result.get("kam_topics") or []
    if isinstance(topics, dict):
        topic_items = list(topics.items())
    elif isinstance(topics, list):
        topic_items = topics
    else:
        topic_items = []
    section_count = len(topic_items)
    data_quality = result.get("data_quality") or {}

    lines = [
        f"판정: {status}",
        "",
        f"{subject} {year or ''}년 핵심감사사항(KAM) 비교 결과입니다. 현재 로컬 캐시 기준 KAM topic {section_count}건이 요약됩니다.",
        "",
        "근거:",
    ]
    if topic_items:
        for topic in topic_items[:5]:
            if isinstance(topic, dict):
                lines.append(
                    f"- {_public_kam_topic(topic.get('topic'))}"
                )
            elif isinstance(topic, tuple) and len(topic) == 2:
                lines.append(f"- {_public_kam_topic(topic[0])}: {topic[1]}")
            else:
                lines.append(f"- {_public_kam_topic(topic)}")
    else:
        summary = result.get("audit_report_events")
        if isinstance(summary, list):
            lines.append(f"- 감사보고서 이벤트 요약 {len(summary)}건이 제공되었습니다. 원 공시 확인이 필요합니다.")
        elif isinstance(summary, dict) and summary:
            lines.append("- 감사보고서 이벤트 요약이 제공되었습니다. 세부 항목은 원 공시로 확인해야 합니다.")
        else:
            lines.append("- 감사보고서 이벤트 요약이 현재 캐시에 충분하지 않습니다.")

    lines.append("")
    lines.append("데이터 한계:")
    lines.append(f"- 상태: {data_quality.get('status') or status}")
    for limitation in (result.get("limitations") or [])[:3]:
        lines.append(f"- {limitation}")
    if len(lines) < 8:
        lines.append("- 현재 결과는 로컬 감사보고서 캐시 기준입니다.")
    return "\n".join(lines)


def _render_acceptance_pack(result: dict) -> str:
    subject = _subject_label(result)
    year = result.get("year")
    signals = result.get("acceptance_signals") or []
    data_quality = result.get("data_quality") or {}
    status = _status(result)

    lines = [
        f"판정: {status}",
        "",
        f"{subject} {year or ''}년 수임/유지 검토용 외부근거 pack입니다. 이 결과는 감사판단을 대체하지 않고, 수임 전 위험 식별과 peer 비교의 출발점으로 사용해야 합니다.",
        "",
        "근거:",
    ]
    if signals:
        for signal in signals[:5]:
            if isinstance(signal, dict):
                lines.append(f"- {signal.get('signal') or signal.get('name') or signal}: {signal.get('description') or signal.get('note') or ''}".rstrip())
            else:
                lines.append(f"- {signal}")
    else:
        lines.append("- 현재 수임 관련 signal이 제한적으로 확인됩니다.")

    lines.append("")
    lines.append("데이터 한계:")
    for key, value in data_quality.items():
        if isinstance(value, dict):
            lines.append(f"- {key}: {value.get('status') or value}")
    if len(lines) < 8:
        lines.append("- 현재 결과는 로컬 캐시 기준입니다.")
    return "\n".join(lines)


def _render_audit_report_sections(result: dict) -> str:
    subject = _subject_label(result)
    year = result.get("year")
    section_key = result.get("section_key") or "감사보고서 섹션"
    count = result.get("section_count") or 0
    lines = [
        f"판정: {_status(result)}",
        "",
        f"{subject} {year or ''}년 `{section_key}` 조회 결과, 로컬 감사보고서 본문 섹션 {count}건이 확인됩니다.",
        "",
        "근거:",
    ]
    sections = _dedupe_display_records(result.get("sections") or [], text_key="body_excerpt")
    for section in sections[:3]:
        title = section.get("section_title") or section.get("section_key") or "섹션"
        excerpt = section.get("body_excerpt") or ""
        lines.append(f"- {title}: {excerpt[:220]}")
        analysis = section.get("kam_analysis") or {}
        if analysis:
            topics = ", ".join(
                _public_kam_topic(topic)
                for topic in analysis.get("topics") or []
            )
            if topics:
                lines.append(f"  KAM topic: {topics}")
            if analysis.get("has_procedure_hint"):
                lines.append("  감사절차 힌트가 확인됩니다.")
    if not sections:
        lines.append("- 현재 조건에 맞는 감사보고서 본문 섹션을 찾지 못했습니다.")
    lines.append("")
    lines.append("데이터 한계:")
    data_quality = result.get("data_quality") or {}
    lines.append(f"- 출처: {_public_source_label(data_quality.get('source') or 'report_sections.audit_report')}")
    lines.append(f"- {data_quality.get('interpretation') or '로컬 캐시 기준이며 원 공시 확인이 필요합니다.'}")
    evidence = _render_evidence_grounded_sections(result)
    if evidence:
        lines.append(evidence)
    return "\n".join(lines)


def _render_audit_report_matters(result: dict) -> str:
    query = result.get("query") or {}
    year = result.get("year") or query.get("year")
    lines = [
        f"판정: {_status(result)}",
        "",
        f"{year or ''}년 감사보고서 강조사항·기타사항·계속기업 문단 검색 결과입니다. 회사 {result.get('total_companies', 0)}개, 섹션 {result.get('total_sections', 0)}건이 확인됩니다.",
        "",
        "근거:",
    ]
    for company in (result.get("companies") or [])[:5]:
        counts = company.get("matter_counts") or {}
        count_text = ", ".join(
            f"{_public_matter_label(key)} {value}"
            for key, value in counts.items()
            if value
        )
        lines.append(f"- {company.get('corp_name') or company.get('corp_code')}: {count_text or 'matter count 없음'}")
        first = (company.get("sections") or [{}])[0]
        if first.get("severity_hint") or first.get("topic_tags"):
            tags = ", ".join(
                _public_matter_label(tag)
                for tag in first.get("topic_tags") or []
            )
            lines.append(
                f"  분류: {first.get('severity_hint')}"
                + (f", 주제={tags}" if tags else "")
            )
    if not result.get("companies"):
        lines.append("- 현재 조건에 맞는 matter 섹션을 찾지 못했습니다.")
    lines.append("")
    lines.append("데이터 한계:")
    data_quality = result.get("data_quality") or {}
    lines.append(f"- 출처: {_public_source_label(data_quality.get('source') or 'report_sections.audit_report')}")
    lines.append(f"- {data_quality.get('interpretation') or '없음은 공시 부재가 아니라 캐시 부재일 수 있습니다.'}")
    evidence = _render_evidence_grounded_sections(result)
    if evidence:
        lines.append(evidence)
    return "\n".join(lines)


def render_audit_matter_search(result: dict) -> str:
    return _render_audit_report_matters(result)


def _render_audit_procedures(result: dict) -> str:
    query = result.get("query") or {}
    subject = _subject_label(result)
    total = result.get("total_procedures")
    if total is None:
        total = sum((result.get("subject_procedure_type_counts") or {}).values()) + sum((result.get("peer_procedure_type_counts") or {}).values())
    lines = [
        f"판정: {_status(result)}",
        "",
        f"{subject} 조건의 KAM 감사절차 조회 결과입니다. 절차 항목 {total or 0}건이 확인됩니다.",
    ]
    if query.get("kam_topic"):
        lines.append(f"KAM topic 필터는 `{query.get('kam_topic')}`입니다.")
    lines.extend(["", "근거:"])
    type_counts = result.get("procedure_type_counts") or result.get("peer_procedure_type_counts") or {}
    if type_counts:
        lines.append("- 절차 유형 분포: " + ", ".join(f"{k} {v}" for k, v in type_counts.items()))
    for company in (result.get("companies") or [])[:3]:
        first = (company.get("records") or [{}])[0]
        if first:
            lines.append(f"- {company.get('corp_name') or company.get('corp_code')}: {first.get('procedure_type')} / {first.get('procedure_excerpt', '')[:220]}")
    if not type_counts and not result.get("companies"):
        lines.append("- 현재 조건에 맞는 감사절차 항목을 찾지 못했습니다.")
    lines.append("")
    lines.append("데이터 한계:")
    data_quality = result.get("data_quality") or {}
    lines.append(f"- 출처: {_public_source_label(data_quality.get('source') or 'audit_procedure_items')}")
    lines.append(f"- {data_quality.get('interpretation') or data_quality.get('coverage_note') or 'KAM 본문에서 rule 기반으로 분리한 절차 힌트입니다.'}")
    evidence = _render_evidence_grounded_sections(result)
    if evidence:
        lines.append(evidence)
    return "\n".join(lines)


def _render_kam_lifecycle(result: dict) -> str:
    subject = _subject_label(result)
    events = public_kam_lifecycle_events(result.get("events"))
    changed = [
        event for event in events
        if event.get("status") == "반복·문구 변경"
    ]
    lines = [
        f"판정: {_status(result)}",
        "",
        f"{subject} KAM lifecycle 조회 결과입니다. {result.get('start_year')}~{result.get('end_year')}년 범위에서 KAM 이벤트 {len(events)}건이 확인됩니다.",
        "",
        "근거:",
    ]
    if events:
        for event in events[:5]:
            lines.append(
                f"- {_markdown_cell(event.get('year'))}년 "
                f"{_markdown_cell(event.get('topic'))}: "
                f"{_markdown_cell(event.get('status'))} / "
                f"{_markdown_cell(event.get('title') or 'KAM')}"
            )
    else:
        lines.append("- 현재 로컬 캐시에서 KAM 본문을 찾지 못했습니다.")
    lines.append("")
    lines.append("왜 중요한가:")
    lines.append(f"- KAM 변경 이벤트는 {len(changed)}건입니다. 반복 KAM의 문구 변화는 감사위험 또는 공시 표현 변화의 검토 후보입니다.")
    data_quality = result.get("data_quality") or {}
    lines.append("")
    lines.append("데이터 한계:")
    lines.append(f"- 출처: {_public_source_label(data_quality.get('source') or 'report_sections.audit_report')}")
    lines.append(f"- {data_quality.get('interpretation') or '원 감사보고서 확인이 필요합니다.'}")
    return "\n".join(lines)


def _render_policy_changes(result: dict) -> str:
    subject = _subject_label(result)
    changed = result.get("changed_items") or []
    lines = [
        f"판정: {_status(result)}",
        "",
        f"{subject} 회계정책/추정 주석 변화 조회 결과입니다. {result.get('start_year')}~{result.get('end_year')}년 범위에서 변경 후보 {len(changed)}건이 확인됩니다.",
        "",
        "근거:",
    ]
    for item in changed[:5]:
        lines.append(f"- {item.get('year')}년 주석 {item.get('note_no')} {item.get('section_type')}: similarity={item.get('similarity_to_previous')}")
    if not changed:
        lines.append("- 현재 비교 가능한 주석 변화 후보가 제한적입니다.")
    lines.append("")
    lines.append("데이터 한계:")
    data_quality = result.get("data_quality") or {}
    lines.append(f"- 출처: {_public_source_label(data_quality.get('source') or 'accounting_note_chapters')}")
    lines.append(f"- {data_quality.get('interpretation') or '문구 변화는 정책 변경 결론이 아니라 검토 후보입니다.'}")
    return "\n".join(lines)


def _render_quality_of_earnings(result: dict) -> str:
    subject = _subject_label(result)
    signals = result.get("signals") or []
    lines = [
        f"판정: {result.get('verdict') or _status(result)}",
        "",
        f"{subject} 이익의 질 점검 결과입니다. 질문은 '{result.get('investment_question') or '보고이익이 현금흐름으로 뒷받침되는가?'}'입니다.",
        "",
        "근거:",
    ]
    if signals:
        for signal in signals[:5]:
            lines.append(f"- {signal.get('signal')}: {signal.get('meaning') or signal.get('severity')}")
    else:
        metrics = result.get("metrics") or {}
        lines.append(f"- 확인 연도 {metrics.get('years')}개년, 낮은 현금전환 연도 {metrics.get('low_cash_conversion_years')}건")
    lines.append("")
    lines.append("데이터 한계:")
    for limitation in (result.get("limitations") or [])[:3]:
        lines.append(f"- {limitation}")
    evidence = _render_evidence_grounded_sections(result)
    if evidence:
        lines.append(evidence)
    return "\n".join(lines)


def _render_dcf_inputs(result: dict) -> str:
    subject = _subject_label(result)
    assumptions = result.get("candidate_assumptions") or {}
    assumption_labels = {
        "revenue_growth": "매출 성장률",
        "operating_margin": "영업이익률",
        "cash_conversion": "현금전환",
    }
    basis_labels = {
        "historical_median": "과거 중앙값",
        "operating_cf_to_net_income": "영업현금흐름 대비 순이익",
    }
    lines = [
        f"판정: {_status(result)}",
        "",
        f"{subject} DCF 입력 후보입니다. 이 결과는 valuation 결론이 아니라 공시 기반 입력값 후보입니다.",
        "",
        "근거:",
    ]
    for key in ("revenue_growth", "operating_margin", "cash_conversion"):
        item = assumptions.get(key) or {}
        basis = item.get("basis")
        basis_text = basis_labels.get(str(basis), "산정 근거 미확보") if basis else "산정 근거 미확보"
        lines.append(f"- {assumption_labels[key]}: {item.get('value')} ({basis_text})")
    missing = result.get("missing_inputs") or []
    if missing:
        lines.append("")
        lines.append("추가 판단 필요:")
        lines.append("- " + ", ".join(str(x) for x in missing[:8]))
    lines.append("")
    lines.append("데이터 한계:")
    for limitation in (result.get("limitations") or [])[:3]:
        lines.append(f"- {limitation}")
    evidence = _render_evidence_grounded_sections(result)
    if evidence:
        lines.append(evidence)
    return "\n".join(lines)


def _render_dcf_model_pack(result: dict) -> str:
    def safe(value: object) -> str:
        return html.escape(str(value)[:1000], quote=True)

    subject = html.escape(_subject_label(result), quote=True)
    status = str(result.get("status") or _status(result))
    bridge = result.get("valuation_bridge") or {}
    projections = [
        row for row in (result.get("projections") or [])
        if isinstance(row, dict)
    ]
    assumptions = [
        row for row in (result.get("assumptions") or [])
        if isinstance(row, dict)
    ]
    lines = [
        f"판정: {status}",
        "",
        f"{subject}의 검토 가능한 DCF 모델입니다.",
        "",
        "모델 레이어:",
        f"- 실제값: {result.get('base_year')}년 {result.get('fs_div')} 로컬 재무 공시 캐시",
        f"- 정규화: {len(result.get('normalization') or [])}개 항목을 실제값과 분리",
        f"- 분석가 가정: {len(assumptions)}개 항목, 누락값 자동 대체 없음",
        "- UFCF 공식: EBIT * (1-tax) + D&A - capex - change_in_NWC",
    ]
    if projections:
        lines.extend(["", "연도별 예측:"])
        for row in projections[:10]:
            lines.append(
                f"- {safe(row.get('year'))}: 매출 {safe(row.get('revenue'))}, "
                f"EBIT {safe(row.get('ebit'))}, UFCF {safe(row.get('ufcf'))}, "
                f"현재가치 {safe(row.get('present_value'))}"
            )
    lines.extend([
        "",
        "가치 브리지:",
        f"- 예측기간 현재가치: {safe(bridge.get('forecast_period_present_value'))}",
        f"- 터미널가치: {safe(bridge.get('terminal_value'))}",
        "- Gordon 공식: final_UFCF * (1+g) / (wacc-g)",
        f"- 최종연도 할인계수: {safe(bridge.get('final_year_discount_factor'))}",
        f"- 터미널가치 현재가치: {safe(bridge.get('terminal_value_present_value'))}",
        "- 기업가치 = 예측기간 현재가치 + 터미널가치 현재가치",
        f"- 기업가치: {safe(bridge.get('enterprise_value'))}",
        f"- 순부채: {safe(bridge.get('net_debt'))}",
        f"- 자기자본가치: {safe(bridge.get('equity_value'))}",
    ])
    missing = result.get("missing_inputs") or []
    if missing:
        lines.extend([
            "",
            "누락 입력:",
            "- " + ", ".join(html.escape(str(item), quote=True) for item in missing[:32]),
        ])
    lines.extend([
        "",
        "용도 제한:",
        "- 이 결과는 투자 권유가 아닙니다.",
        "- 공정성 의견이 아닙니다.",
        "- 승인된 예측이 아닙니다.",
        "- 감사 결론이 아닙니다.",
    ])
    for limitation in (result.get("limitations") or [])[:8]:
        lines.append(f"- {html.escape(str(limitation), quote=True)}")
    return "\n".join(lines)[:20_000]


def _render_disclosure_events(result: dict) -> str:
    query = result.get("query") or {}
    lines = [
        f"판정: {_status(result)}",
        "",
        f"{query.get('start_date') or ''}~{query.get('end_date') or ''} 공시 이벤트 검색 결과입니다. 이벤트 {result.get('total_events', 0)}건이 확인됩니다.",
        "",
        "근거:",
    ]
    for event in (result.get("events") or [])[:5]:
        lines.append(f"- {event.get('event_date')} {event.get('corp_name')}: {event.get('event_type')} / {event.get('event_title')}")
    if not result.get("events"):
        lines.append("- 현재 조건에 맞는 이벤트를 찾지 못했습니다.")
    lines.append("")
    lines.append("다음 행동:")
    lines.append("- 중요한 이벤트는 접수번호 기준으로 fetch_disclosure_on_demand를 호출해 원문을 확인하세요.")
    evidence = _render_evidence_grounded_sections(result)
    if evidence:
        lines.append(evidence)
    return "\n".join(lines)


def _render_investor_signals(result: dict) -> str:
    subject = _subject_label(result)
    quality = result.get("quality_snapshot") or {}
    risk = result.get("accounting_risk") or {}
    event_counts = result.get("event_counts") or {}
    active_events = {key: value for key, value in event_counts.items() if value}
    lines = [
        f"판정: {_status(result)}",
        "",
        f"{subject} 투자자 신호 요약입니다. 재무 품질, 회계 리스크, 최근 공시 이벤트를 함께 본 1차 점검 결과입니다.",
        "",
        "요약:",
        f"- 품질 체크: {quality.get('passed_checks', 0)}/{quality.get('total_checks', 0)} 통과"
        + (f", 최근 연도 {quality.get('latest_year')}" if quality.get("latest_year") else ""),
        f"- 회계 리스크: {risk.get('verdict') or 'unknown'}"
        + (f" / score {risk.get('score')}" if risk.get("score") is not None else ""),
    ]
    if active_events:
        event_text = ", ".join(f"{key} {value}" for key, value in active_events.items())
        lines.append(f"- 최근 이벤트: {event_text}")
    else:
        lines.append("- 최근 이벤트: 주요 이벤트 분류 결과가 제한적입니다.")
    takeaways = result.get("takeaways") or []
    if takeaways:
        lines.append("- 관찰 포인트: " + ", ".join(str(item) for item in takeaways[:5]))

    limitations = result.get("limitations") or []
    if limitations:
        lines.extend(["", "데이터 한계:"])
        for limitation in limitations[:3]:
            lines.append(f"- {limitation}")

    evidence = _render_evidence_grounded_sections(result)
    if evidence:
        lines.append(evidence)
    return "\n".join(lines)


def _render_peer_benchmark(result: dict) -> str:
    subject = _subject_label(result)
    results = result.get("results") or {}
    rows: list[dict[str, Any]] = []
    for year in sorted(results.keys(), key=lambda value: str(value)):
        metrics = results.get(year) or {}
        if not isinstance(metrics, dict):
            continue
        for metric, values in metrics.items():
            if not isinstance(values, dict):
                continue
            rows.append({
                "year": year,
                "metric": metric,
                "subject_value": values.get("subject_value"),
                "percentile": values.get("percentile"),
                "p25": values.get("p25"),
                "p50": values.get("p50"),
                "p75": values.get("p75"),
                "n": values.get("n"),
            })

    lines = [
        f"판정: {_status(result)}",
        "",
        f"{subject} Peer 벤치마크 결과입니다. 동종업종 peer {result.get('n_peers', 0)}개 기준으로 대상회사 값과 업종 사분위·백분위를 비교합니다.",
    ]
    if result.get("confidence"):
        lines.append(f"Peer 선정 신뢰도는 {result.get('confidence')}입니다.")
    lines.extend([
        "",
        "표:",
        "| 연도 | 지표 | 대상회사 | 백분위 | P25 | P50 | P75 | Peer 수 |",
        "|---:|---|---:|---:|---:|---:|---:|---:|",
    ])
    for row in rows[:24]:
        lines.append(
            f"| {row.get('year')} "
            f"| {row.get('metric')} "
            f"| {row.get('subject_value')} "
            f"| {row.get('percentile')} "
            f"| {row.get('p25')} "
            f"| {row.get('p50')} "
            f"| {row.get('p75')} "
            f"| {row.get('n')} |"
        )
    if not rows:
        lines.append("| 미확보 | - | - | - | - | - | - | - |")

    lines.extend([
        "",
        "그래픽 렌더링 후보:",
        "- `answer_pack.charts.peer_percentile_matrix`: 연도×지표 백분위 heatmap",
        "- `answer_pack.charts.peer_band`: 대상회사 값과 peer P25/P50/P75 band 비교",
        "",
        "데이터 한계:",
        "- 현재 결과는 로컬 kreports.db의 재무/업종 캐시 기준입니다.",
        "- peer 비교는 투자판단 결론이 아니라 업종 내 상대 위치를 확인하는 screening입니다.",
    ])
    return "\n".join(lines)


def _analysis_heading(perspective: str | None) -> str:
    if perspective == "auditor":
        return "감사인 관점 해석"
    if perspective == "investor":
        return "투자자 관점 해석"
    return "분석"


def _dedupe_confirmed_facts_for_render(facts: list) -> list:
    seen: set[tuple[str, ...]] = set()
    deduped: list = []
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        source = fact.get("source")
        source = source if isinstance(source, dict) else {}
        raw_rcept = source.get("rcept_no")
        receipt_key = parent_rcept_no(str(raw_rcept)) or str(raw_rcept or "")
        excerpt = re.sub(r"\s+", " ", str(fact.get("excerpt") or fact.get("statement") or "")).strip()[:160]
        key = (
            str(source.get("corp_code") or source.get("corp_name") or ""),
            str(source.get("bsns_year") or ""),
            receipt_key,
            str(source.get("section_title") or source.get("section_key") or ""),
            excerpt,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(fact)
    return deduped


def _render_evidence_grounded_sections(result: dict) -> str:
    """Render confirmed facts and analysis without rigid numbered fact labels."""
    confirmed_facts = _dedupe_confirmed_facts_for_render(result.get("confirmed_facts") or [])
    analysis_items = result.get("analysis") or []
    next_checks = result.get("next_checks") or []
    if not confirmed_facts and not analysis_items and not next_checks:
        return ""

    lines: list[str] = []
    if confirmed_facts:
        lines.extend(["", "공시에서 확인되는 내용:"])
        for fact in confirmed_facts[:6]:
            if not isinstance(fact, dict):
                continue
            statement = str(fact.get("statement") or "").strip()
            if statement:
                lines.append(f"- {statement}")
            source = fact.get("source")
            if isinstance(source, dict):
                rendered_source = source_line(source)
                for source_part in rendered_source.splitlines():
                    lines.append(f"  {source_part}")

    if analysis_items:
        grouped: dict[str, list[dict]] = {}
        for item in analysis_items:
            if isinstance(item, dict):
                grouped.setdefault(str(item.get("perspective") or "both"), []).append(item)
        for perspective, items in grouped.items():
            lines.extend(["", f"{_analysis_heading(perspective)}:"])
            for item in items[:5]:
                statement = str(item.get("statement") or "").strip()
                if statement:
                    lines.append(f"- {statement}")

    if next_checks:
        lines.extend(["", "확인 한계와 다음 확인:"])
        for check in next_checks[:6]:
            lines.append(f"- {check}")

    return "\n".join(lines)


def _render_company_search(result: dict) -> str:
    """Render public company-search fields without exposing DART identifiers."""
    query = str(result.get("query") or "").strip()
    matches = [item for item in result.get("results") or [] if isinstance(item, dict)]
    lines = ["기업 검색 결과:"]
    if query:
        lines.append(f"- 검색어: {query}")
    for company in matches[:10]:
        name = str(company.get("corp_name") or "회사명 미확인")
        stock_code = str(company.get("stock_code") or "").strip()
        market = str(company.get("market") or "").strip()
        descriptors = []
        if stock_code:
            descriptors.append(f"종목코드 {stock_code}")
        if market:
            descriptors.append(market)
        suffix = f" ({', '.join(descriptors)})" if descriptors else ""
        lines.append(f"- {name}{suffix}")
    if not matches:
        lines.append("- 현재 검색 조건과 일치하는 상장사를 확인하지 못했습니다.")
    return "\n".join(lines)


def _render_going_concern(result: dict) -> str:
    """Render a conservative going-concern screening summary."""
    lines = ["계속기업 위험 스크리닝:"]
    grade = str(result.get("grade") or "").strip()
    score = result.get("score")
    if grade and grade != "-":
        lines.append(f"- 스크리닝 등급: {grade}")
    if isinstance(score, (int, float)):
        lines.append(f"- 스크리닝 점수: {score:g}/100")

    factors = [item for item in result.get("factors") or [] if isinstance(item, dict)]
    flagged = [item for item in factors if item.get("hit") is True]
    if flagged:
        lines.append("- 감점 요인:")
        for factor in flagged[:6]:
            name = str(factor.get("name") or "위험 요인")
            detail = str(factor.get("detail") or "").strip()
            penalty = factor.get("penalty")
            suffix_parts = []
            if detail:
                suffix_parts.append(detail)
            if isinstance(penalty, (int, float)) and penalty > 0:
                suffix_parts.append(f"{penalty:g}점 감점")
            suffix = f" — {', '.join(suffix_parts)}" if suffix_parts else ""
            lines.append(f"  - {name}{suffix}")
    elif factors:
        lines.append("- 현재 스크리닝에서 감점 요인은 확인되지 않았습니다.")
    lines.append("- 이 결과는 제한된 정량 지표를 이용한 선별 결과이며, 감사인의 계속기업 결론을 대체하지 않습니다.")
    return "\n".join(lines)


def _render_generic(tool_name: str, result: dict) -> str:
    status = _status(result)
    subject = _subject_label(result)
    lines = [
        f"판정: {status}",
        "",
        f"{subject}에 대한 `{tool_name}` 조회 결과입니다. 구조화 데이터는 응답 본문에 함께 포함되어 있으며, 아래 근거와 한계를 우선 확인해야 합니다.",
        "",
        "근거:",
    ]
    if result.get("rcept_no"):
        lines.append(f"- 접수번호: {result.get('rcept_no')}")
    elif result.get("section_count") is not None:
        lines.append(f"- 섹션 수: {result.get('section_count')}")
    elif result.get("peer_count") is not None:
        lines.append(f"- peer 수: {result.get('peer_count')}")
    else:
        lines.append("- 세부 근거는 구조화 필드와 `_meta`를 확인하세요.")

    lines.append("")
    lines.append("데이터 한계:")
    lines.append("- 현재 결과는 로컬 kreports.db 캐시 기준입니다.")
    evidence = _render_evidence_grounded_sections(result)
    if evidence:
        lines.append(evidence)
    return "\n".join(lines)


def _render_professional_envelope(envelope: AnswerEnvelopeV1, *, detail: str | None = None) -> str:
    """Render the stable V1 prose sections from an AnswerEnvelopeV1."""
    conclusion_override = PROFESSIONAL_CONCLUSION_OVERRIDES.get(
        envelope.tool_name,
    )
    lines = ["판정:", f"- {envelope.verdict}", ""]
    if conclusion_override:
        lines.extend([conclusion_override, ""])
    else:
        lines.extend([
            "업무 결론:",
            f"- {public_domain_verdict_label(envelope.tool_name, envelope.domain_verdict)}",
            "",
        ])
    context = envelope.release_context
    lines.extend([
        "배포 준비 상태:",
        f"- release_ready: {context.release_ready}",
        f"- manifest_available: {context.manifest_available}",
        f"- snapshot_version: {context.snapshot_version or '-'}",
    ])
    lines.extend(
        f"- required_failure: {value}"
        for value in context.required_failures
    )
    lines.extend(
        f"- degraded_feature: {value}"
        for value in context.degraded_features
    )
    lines.append("")
    lines.append("확인된 내용 (공시에서 확인되는 내용):")
    if envelope.confirmed_facts:
        for fact in _dedupe_confirmed_facts_for_render(envelope.confirmed_facts)[:6]:
            statement = str(fact.get("statement") or "").strip()
            if statement:
                lines.append(f"- {statement}")
    else:
        lines.append("- 확인 가능한 사실이 현재 결과에 포함되지 않았습니다.")

    lines.extend(["", "분석:"])
    if envelope.analysis:
        for item in envelope.analysis[:5]:
            heading = _analysis_heading(item.perspective)
            lines.append(f"- {heading}: {item.statement}")
    else:
        lines.append("- 추가 해석은 확인된 근거 범위 안에서만 수행해야 합니다.")

    lines.extend(["", "출처:"])
    if envelope.evidence:
        for reference in envelope.evidence[:6]:
            label = reference.source_label
            if reference.section_title:
                label = f"{label}, {reference.section_title}"
            if reference.rcept_no:
                label = f"{label}, 접수번호 {reference.rcept_no}"
            lines.append(f"- 출처: {label}")
            lines.append(f"  공시 링크: {reference.source_url}")
    else:
        lines.append("- 연결 가능한 공시 접수번호가 현재 결과에 포함되지 않았습니다.")

    lines.extend(["", "데이터 한계:"])
    limitations = list(dict.fromkeys(envelope.data_quality.limitations + envelope.warnings))
    lines.append(f"- 상태: {envelope.data_quality.status}")
    if limitations:
        lines.extend(f"- {limitation}" for limitation in limitations[:5])
    else:
        lines.append("- 현재 결과의 범위와 최신성은 원 공시로 추가 확인해야 합니다.")

    lines.extend(["", "추가 확인사항:"])
    if envelope.next_checks:
        lines.extend(f"- {check}" for check in envelope.next_checks[:6])
    else:
        lines.append("- 중요 판단 전 원 공시 본문과 최신 공시를 확인하세요.")

    if detail:
        lines.extend(["", "세부 결과:", detail])
    return "\n".join(lines)


def _sanitize_legacy_detail(detail: str) -> str:
    """Keep legacy summaries readable without exposing implementation field names."""
    replacements = {
        "accounting_note_chapters": "회계정책 주석 캐시",
        "answer_pack.charts.peer_percentile_matrix": "연도별 지표 백분위 히트맵",
        "answer_pack.charts.peer_band": "대상회사와 peer 사분위 비교",
        "local_subsidiary_auditor_matrix": "연결실체 감사인 캐시",
        "report_sections.audit_report": "로컬 감사보고서 캐시",
        "report_sections": "보고서 섹션 캐시",
        "audit_matter_items": "감사보고서 항목 캐시",
        "audit_procedure_items": "감사절차 항목 캐시",
        "financial_facts_compact": "재무 공시 캐시",
    }
    for internal_name, public_name in replacements.items():
        detail = detail.replace(f"`{internal_name}`", public_name).replace(internal_name, public_name)
    return detail.replace("`_meta`", "응답 메타데이터").replace("_meta", "응답 메타데이터")


def _note_search_presentation_envelope(envelope: AnswerEnvelopeV1) -> AnswerEnvelopeV1:
    """Summarize note-search facts for prose while retaining raw evidence elsewhere."""
    grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for fact in envelope.confirmed_facts:
        source = fact.get("source") if isinstance(fact.get("source"), dict) else {}
        note_reference = str(
            fact.get("note_reference")
            or source.get("section_title")
            or "주석"
        )
        topic = str(fact.get("topic") or "요청 주제")
        key = (
            str(source.get("corp_code") or source.get("corp_name") or ""),
            str(source.get("rcept_no") or ""),
            note_reference,
            topic,
        )
        summary = grouped.setdefault(key, {
            "note_reference": note_reference,
            "topic": topic,
            "source": source,
            "count": 0,
        })
        summary["count"] += 1

    summaries = [{
        "statement": (
            f"{summary['note_reference']}에서 {summary['topic']} 관련 일치 문구 "
            f"{summary['count']}건을 확인했습니다. 원문 발췌는 아래 표에 표시합니다."
        ),
        "source": summary["source"],
    } for summary in grouped.values()]
    return envelope.model_copy(update={"confirmed_facts": summaries})


def _on_demand_presentation_envelope(
    envelope: AnswerEnvelopeV1,
) -> AnswerEnvelopeV1:
    """Localize the user-key remediation while preserving its machine contract."""
    raw_message = "user_dart_api_key is required"
    public_message = "온디맨드 수시공시 조회에는 사용자 DART API key가 필요합니다."
    data_quality = envelope.data_quality.model_copy(update={
        "limitations": [
            public_message if limitation == raw_message else limitation
            for limitation in envelope.data_quality.limitations
        ],
    })
    return envelope.model_copy(update={
        "data_quality": data_quality,
        "warnings": [
            public_message if warning == raw_message else warning
            for warning in envelope.warnings
        ],
    })


def _is_accounting_note_search(tool_name: str, result: dict[str, Any]) -> bool:
    query = result.get("query")
    return (
        tool_name == "search_dataset"
        and isinstance(query, dict)
        and query.get("dataset") == "accounting_note_chapters"
    )


def render_answer(tool_name: str, result: Any) -> str | None:
    """Return Korean narrative text for a structured tool result."""
    if not isinstance(result, dict):
        return None
    if tool_name == "compare_to_industry_multi":
        from kreports.mcp.professional_surfaces.investor import (
            publicize_peer_result_limitations,
        )

        result = publicize_peer_result_limitations(result)
    # Direct callers bypass enrich_answer_response(), so establish the same
    # canonical state before this renderer or its visual-table helper reads
    # any legacy presentation fields.
    result = normalize_answer_result(tool_name, result)
    candidate_opening: str | None = None
    if tool_name == "get_dcf_input_candidates":
        lines = [
            "DCF 입력 후보 상태: " + str(
                result.get("candidate_status")
                or (result.get("data_quality") or {}).get("candidate_status")
                or (result.get("data_quality") or {}).get("status")
                or "missing"
            ),
            "가치평가 준비도: " + str(
                result.get("valuation_readiness")
                or (result.get("data_quality") or {}).get("valuation_readiness")
                or "blocked"
            ),
        ]
        for blocker in result.get("valuation_blockers") or []:
            if isinstance(blocker, dict):
                lines.append(
                    "- " + str(blocker.get("impact") or blocker.get("field"))
                    + ": " + str(blocker.get("next_action") or "추가 확인이 필요합니다.")
                )
        candidate_opening = "\n".join(lines)
    dcf_unavailable = (
        tool_name == "build_dcf_model_pack"
        and "enterprise_value" in result
        and result.get("enterprise_value") is None
    )
    dcf_opening = (
        (
            "산출 불가: 필수 입력 또는 공시 실제값이 부족하여 기업가치를 계산하지 않았습니다.\n\n"
            "누락 입력: "
            + ", ".join(
                str(value)
                for value in result.get("missing_inputs") or []
            )
        )
        if dcf_unavailable
        else None
    )
    envelope = build_answer_envelope(tool_name, result)
    presentation_envelope = envelope
    if _is_accounting_note_search(tool_name, result):
        presentation_envelope = _note_search_presentation_envelope(envelope)
    elif (
        tool_name == "fetch_disclosure_on_demand"
        and result.get("error") == "user_dart_api_key is required"
    ):
        presentation_envelope = _on_demand_presentation_envelope(envelope)
    legacy_result = dict(result)
    legacy_result["data_quality"] = envelope.data_quality.model_dump()
    legacy_result["verdict"] = envelope.verdict
    for field in ("confirmed_facts", "analysis", "next_checks"):
        legacy_result.pop(field, None)
    detail: str | None = None
    if envelope.data_quality.status in {"missing", "error"}:
        rendered = _render_professional_envelope(presentation_envelope)
        if dcf_opening:
            rendered = rendered + "\n\n" + dcf_opening
        return _append_visual_table(tool_name, result, rendered)
    if tool_name in PROFESSIONAL_DETAIL_RENDERERS:
        detail = PROFESSIONAL_DETAIL_RENDERERS[tool_name](legacy_result)
    elif tool_name == "search_company":
        detail = _render_company_search(legacy_result)
    elif tool_name == "score_going_concern":
        detail = _render_going_concern(legacy_result)
    elif tool_name == "search_dataset":
        detail = _render_search_dataset(legacy_result)
    elif tool_name == "get_subsidiary_auditors":
        detail = _render_subsidiary_auditors(legacy_result)
    elif tool_name == "compare_peer_kam_topics":
        detail = _render_kam_topics(legacy_result)
    elif tool_name in {"get_audit_report_sections"}:
        detail = _render_audit_report_sections(legacy_result)
    elif tool_name in {"search_audit_report_matters", "compare_peer_audit_report_matters"}:
        detail = _render_audit_report_matters(legacy_result)
    elif tool_name in {"search_audit_procedures", "compare_peer_audit_procedures"}:
        detail = _render_audit_procedures(legacy_result)
    elif tool_name == "get_kam_lifecycle":
        detail = _render_kam_lifecycle(legacy_result)
    elif tool_name == "get_accounting_policy_changes":
        detail = _render_policy_changes(legacy_result)
    elif tool_name == "get_quality_of_earnings_pack":
        detail = _render_quality_of_earnings(legacy_result)
    elif tool_name == "get_dcf_input_candidates":
        detail = _render_dcf_inputs(legacy_result)
    elif tool_name == "build_dcf_model_pack":
        detail = _render_dcf_model_pack(legacy_result)
    elif tool_name == "search_disclosure_events":
        detail = _render_disclosure_events(legacy_result)
    elif tool_name == "get_investor_signals":
        detail = _render_investor_signals(legacy_result)
    elif tool_name == "compare_to_industry_multi":
        detail = _render_peer_benchmark(legacy_result)
    elif tool_name == "build_audit_acceptance_pack":
        detail = _render_acceptance_pack(legacy_result)
    rendered = _render_professional_envelope(
        presentation_envelope,
        detail=_sanitize_legacy_detail(detail) if detail else None,
    )
    if candidate_opening:
        rendered = rendered + "\n\n" + candidate_opening
    if dcf_opening:
        rendered = rendered + "\n\n" + dcf_opening
    return _append_visual_table(tool_name, result, rendered)


def _append_visual_table(
    tool_name: str,
    result: dict[str, Any],
    narrative: str,
) -> str:
    """Append the validated canonical table used by every visual capability."""
    visual_tools = {
        "get_dcf_input_candidates",
        "build_dcf_model_pack",
        "compare_to_industry_multi",
        "get_subsidiary_auditors",
        "get_audit_history",
        "compare_peer_audit_fees",
        "build_audit_acceptance_pack",
        "get_kam_lifecycle",
        "search_disclosure_events",
        "search_dataset",
    }
    if tool_name not in visual_tools:
        return narrative
    from kreports.mcp.answer_pack import build_answer_pack
    from kreports.mcp.visual_contracts import (
        VisualizationPackV1,
        render_visualization_markdown,
    )

    raw_pack = result.get("answer_pack")
    if isinstance(raw_pack, dict):
        try:
            pack = VisualizationPackV1.model_validate(raw_pack)
        except (TypeError, ValueError):
            trusted_result = dict(result)
            trusted_result.pop("answer_pack", None)
            built = build_answer_pack(tool_name, trusted_result)
            if built is None:
                return narrative
            pack = VisualizationPackV1.model_validate(built)
    else:
        built = build_answer_pack(tool_name, result)
        if built is None:
            return narrative
        pack = VisualizationPackV1.model_validate(built)
    if tool_name == "build_audit_acceptance_pack":
        scale_tables = [
            table
            for table in pack.tables
            if table.id == "subject_scale_history"
        ]
        if not scale_tables:
            return narrative
        pack = pack.model_copy(update={
            "tables": scale_tables,
            "charts": [],
            "diagrams": [],
            "timelines": [],
            "resource_uri": None,
        })
    if tool_name == "search_dataset" and not any(
        table.id == "accounting_note_evidence" for table in pack.tables
    ):
        return narrative
    table_markdown = render_visualization_markdown(pack, mermaid=False)
    heading = "표 형태 결과" if tool_name == "search_dataset" else "시각화 대체 표"
    return f"{narrative}\n\n{heading}:\n\n{table_markdown}"
