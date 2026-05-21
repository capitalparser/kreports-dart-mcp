"""User-facing narrative renderers for MCP tool responses."""
from __future__ import annotations

from typing import Any


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


def _render_search_dataset(result: dict) -> str:
    query = result.get("query") or {}
    dataset = query.get("dataset") or "dataset"
    status = _status(result)
    total_companies = result.get("total_companies", 0)
    total_records = result.get("total_records", 0)
    subject = _subject_label(result)

    lines = [
        f"판정: {status}",
        "",
        f"{subject} 조건으로 `{dataset}` 데이터셋을 조회한 결과, 회사 {total_companies}개와 근거 레코드 {total_records}건이 확인됩니다.",
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
    lines.append(f"- 출처: {data_quality.get('source') or dataset}")
    lines.append(f"- {data_quality.get('interpretation') or '현재 결과는 로컬 캐시 기준입니다.'}")
    return "\n".join(lines)


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
                lines.append(f"- {topic.get('topic') or topic.get('section_title') or topic}")
            elif isinstance(topic, tuple) and len(topic) == 2:
                lines.append(f"- {topic[0]}: {topic[1]}")
            else:
                lines.append(f"- {topic}")
    else:
        summary = result.get("audit_report_events") or {}
        lines.append(f"- 감사보고서 이벤트/요약 근거: {summary}")

    lines.append("")
    lines.append("데이터 한계:")
    lines.append(f"- 상태: {data_quality.get('status') or status}")
    for limitation in (result.get("limitations") or [])[:3]:
        lines.append(f"- {limitation}")
    if len(lines) < 8:
        lines.append("- 현재 결과는 로컬 report_sections 캐시 기준입니다.")
    return "\n".join(lines)


def _render_acceptance_pack(result: dict) -> str:
    subject = _subject_label(result)
    year = result.get("year")
    signals = result.get("acceptance_signals") or []
    data_quality = result.get("data_quality") or {}
    status = "usable"
    if isinstance(data_quality, dict):
        if any((v or {}).get("status") in {"missing", "limited"} for v in data_quality.values() if isinstance(v, dict)):
            status = "limited"

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
    return "\n".join(lines)


def render_answer(tool_name: str, result: Any) -> str | None:
    """Return Korean narrative text for a structured tool result."""
    if not isinstance(result, dict) or result.get("error"):
        return None
    if tool_name == "search_dataset":
        return _render_search_dataset(result)
    if tool_name == "compare_peer_kam_topics":
        return _render_kam_topics(result)
    if tool_name == "build_audit_acceptance_pack":
        return _render_acceptance_pack(result)
    return _render_generic(tool_name, result)
