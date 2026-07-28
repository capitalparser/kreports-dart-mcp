from collections.abc import Callable
from typing import Any

PackBuilder = Callable[[dict[str, Any]], dict[str, Any] | None]
DetailRenderer = Callable[[dict[str, Any]], str]

_SECTION_LABELS = {
    "peer_group": "Peer 그룹",
    "audit_effort": "감사 노력",
    "financial_risk": "재무 위험",
    "audit_history": "감사인 이력",
    "accounting_policy": "회계정책",
    "kam": "핵심감사사항",
    "audit_report_matters": "감사보고서 사항",
}


def _history_pack(result: dict[str, Any]) -> dict[str, Any]:
    from kreports.mcp.answer_pack import _base_pack, _subject_label, _table

    pack = _base_pack(f"{_subject_label(result)} 감사인 이력", result)
    rows = [row for row in (result.get("history") or []) if isinstance(row, dict)]
    if rows:
        pack["tables"].append(_table(
            "audit_history",
            "감사인 이력",
            [
                ("year", "연도"), ("fs_div", "FS"), ("auditor_nm", "감사인"),
                ("audit_opinion", "감사의견"), ("auditor_changed", "변경 여부"),
                ("consecutive_years", "연속감사연수"), ("rcept_no", "접수번호"),
            ],
            rows,
        ))
    return pack


def _risk_pack(result: dict[str, Any]) -> dict[str, Any]:
    from kreports.mcp.answer_pack import _base_pack, _subject_label, _table

    pack = _base_pack(f"{_subject_label(result)} 재무 위험 Peer 비교", result)
    rows = [row for row in (result.get("metric_rows") or []) if isinstance(row, dict)]
    if rows:
        pack["tables"].append(_table(
            "peer_risk_metrics",
            "Peer 위험지표 분포",
            [
                ("metric", "지표"), ("peer_n", "Peer n"), ("p25", "P25"),
                ("p50", "P50"), ("p75", "P75"), ("subject_value", "대상 값"),
                ("limitation", "한계"),
            ],
            rows,
        ))
    return pack


def _acceptance_pack(result: dict[str, Any]) -> dict[str, Any]:
    from kreports.mcp.answer_pack import _base_pack, _subject_label, _table

    pack = _base_pack(f"{_subject_label(result)} 감사 검토 근거", result)
    quality = result.get("data_quality") if isinstance(result.get("data_quality"), dict) else {}
    sections = quality.get("section_statuses") if isinstance(quality.get("section_statuses"), dict) else {}
    rows = []
    for section_key in _SECTION_LABELS:
        section = sections.get(section_key) if isinstance(sections.get(section_key), dict) else {}
        sources = section.get("sources") if isinstance(section.get("sources"), list) else []
        receipt = next((source.get("rcept_no") for source in sources if isinstance(source, dict) and source.get("rcept_no")), None)
        coverage = section.get("coverage") if isinstance(section.get("coverage"), dict) else {}
        facts = "공시 근거 확인" if sources else "공시 근거 추가 확인 필요"
        rows.append({
            "review_area": _SECTION_LABELS[section_key],
            "status": section.get("status") or "limited",
            "confirmed_facts": facts,
            "coverage": ", ".join(f"{key}={value}" for key, value in coverage.items()) or "-",
            "rcept_no": receipt or "-",
            "next_check": "공시 근거와 최소 coverage를 추가 확인하세요." if section.get("blockers") else "추가 확인 없음",
        })
    pack["tables"].append(_table(
        "audit_acceptance_evidence",
        "수임·유지 검토 영역",
        [
            ("review_area", "검토영역"), ("status", "상태"),
            ("confirmed_facts", "확인 사실"), ("coverage", "값/coverage"),
            ("rcept_no", "접수번호"), ("next_check", "필수 후속 확인"),
        ],
        rows,
    ))
    return pack


def _history_detail(result: dict[str, Any]) -> str:
    rows = [row for row in (result.get("history") or []) if isinstance(row, dict)]
    lines = ["감사인 이력:"]
    for row in rows[:10]:
        changed = "변경" if row.get("auditor_changed") else "유지"
        lines.append(
            f"- {row.get('year')}년 {row.get('fs_div')}: {row.get('auditor_nm')} / "
            f"의견 {row.get('audit_opinion')} / {changed} / 연속 {row.get('consecutive_years')}년 / 접수번호 {row.get('rcept_no') or '-'}"
        )
    return "\n".join(lines)


def _risk_detail(result: dict[str, Any]) -> str:
    rows = [row for row in (result.get("metric_rows") or []) if isinstance(row, dict)]
    lines = ["Peer 위험지표:"]
    for row in rows[:8]:
        lines.append(
            f"- {row.get('metric')}: 대상 {row.get('subject_value')}, Peer n={row.get('peer_n')}, "
            f"P25/P50/P75={row.get('p25')}/{row.get('p50')}/{row.get('p75')}"
        )
    return "\n".join(lines)


def _acceptance_detail(result: dict[str, Any]) -> str:
    quality = result.get("data_quality") if isinstance(result.get("data_quality"), dict) else {}
    sections = quality.get("section_statuses") if isinstance(quality.get("section_statuses"), dict) else {}
    lines = ["검토 근거 매트릭스:"]
    for key, label in _SECTION_LABELS.items():
        section = sections.get(key) if isinstance(sections.get(key), dict) else {}
        lines.append(f"- {label}: {section.get('status') or 'limited'}")
    for signal in (result.get("acceptance_signals") or [])[:5]:
        if isinstance(signal, dict) and signal.get("label"):
            lines.append(f"- 관찰사항: {signal['label']}")
    return "\n".join(lines)


PACK_BUILDERS: dict[str, PackBuilder] = {
    "get_audit_history": _history_pack,
    "compare_peer_risk_profile": _risk_pack,
    "build_audit_acceptance_pack": _acceptance_pack,
}
DETAIL_RENDERERS: dict[str, DetailRenderer] = {
    "get_audit_history": _history_detail,
    "compare_peer_risk_profile": _risk_detail,
    "build_audit_acceptance_pack": _acceptance_detail,
}
