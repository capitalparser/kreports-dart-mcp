from collections.abc import Callable
from typing import Any

PackBuilder = Callable[[dict[str, Any]], dict[str, Any] | None]
DetailRenderer = Callable[[dict[str, Any]], str]

def _base_pack(title: str, result: dict[str, Any]) -> dict[str, Any]:
    quality = result.get("data_quality") or {}
    subject = result.get("subject") or {}
    return {
        "kind": "answer_pack",
        "version": "answer_pack.v1",
        "summary": {
            "title": title,
            "status": quality.get("status") or "limited",
            "subject": subject.get("corp_name") or subject.get("corp_code") or "대상 회사",
        },
        "tables": [],
        "charts": [],
        "diagrams": [],
        "timelines": [],
        "sources": [],
        "data_quality": quality,
    }


def _subject_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [{
        "year": row.get("year"),
        "fs": row.get("fs_div"),
        "total_assets_100m": row.get("total_assets_100m"),
        "revenue_100m": row.get("revenue_100m"),
        "audit_fee_m": row.get("audit_fee_m"),
        "audit_hours": row.get("audit_hours"),
        "hours_basis": row.get("hours_basis"),
        "input_status": row.get("input_status"),
        "missing_fields": ", ".join(row.get("missing_fields") or []) or "-",
    } for row in result.get("rows") or [] if isinstance(row, dict)]


def _build_prepare_inputs_pack(result: dict[str, Any]) -> dict[str, Any]:
    pack = _base_pack("표준감사시간 입력 준비", result)
    rows = _subject_rows(result)
    if rows:
        pack["tables"].append({
            "id": "audit_effort_subject_inputs",
            "title": "최근 3개년 공개자료 입력",
            "columns": [
                {"field": "year", "label": "연도"},
                {"field": "fs", "label": "FS"},
                {"field": "total_assets_100m", "label": "총자산(억원)"},
                {"field": "revenue_100m", "label": "매출(억원)"},
                {"field": "audit_fee_m", "label": "감사보수(백만원)"},
                {"field": "audit_hours", "label": "감사시간"},
                {"field": "hours_basis", "label": "기준"},
                {"field": "input_status", "label": "입력상태"},
                {"field": "missing_fields", "label": "미확보 항목"},
            ],
            "rows": rows,
            "status": (result.get("data_quality") or {}).get("status", "limited"),
        })
    return pack


def _build_fee_comparison_pack(result: dict[str, Any]) -> dict[str, Any]:
    pack = _base_pack("감사보수·감사시간 비교", result)
    rows = []
    subject = result.get("subject_metrics")
    if isinstance(subject, dict):
        rows.append({"role": "subject", **subject})
    for peer in result.get("peers") or []:
        if isinstance(peer, dict):
            rows.append({"role": "peer", **peer})
    if rows:
        pack["tables"].append({
            "id": "audit_effort_peer_fees",
            "title": "대상회사 후 peer 감사보수 비교",
            "columns": [
                {"field": "role", "label": "구분"},
                {"field": "corp_name", "label": "회사"},
                {"field": "audit_fee_m", "label": "감사보수(백만원)"},
                {"field": "audit_hours", "label": "감사시간"},
            ],
            "rows": [
                {key: row.get(key) for key in ("role", "corp_name", "audit_fee_m", "audit_hours")}
                for row in rows
            ],
            "status": (result.get("data_quality") or {}).get("status", "limited"),
        })
    return pack


def _build_hours_proxy_pack(result: dict[str, Any]) -> dict[str, Any]:
    pack = _base_pack("감사시간 proxy", result)
    metrics = result.get("subject_metrics")
    if isinstance(metrics, dict):
        pack["tables"].append({
            "id": "audit_effort_proxy",
            "title": "감사시간 공개자료 proxy",
            "columns": [
                {"field": "audit_fee_m", "label": "감사보수(백만원)"},
                {"field": "audit_hours", "label": "감사시간"},
                {"field": "total_assets", "label": "총자산"},
            ],
            "rows": [{key: metrics.get(key) for key in ("audit_fee_m", "audit_hours", "total_assets")}],
            "status": (result.get("data_quality") or {}).get("status", "limited"),
        })
    return pack


def _render_prepare_inputs(result: dict[str, Any]) -> str:
    lines = ["표준감사시간 결론: 산정하지 않음", "", "최근 3개년 공개자료 입력:"]
    for row in _subject_rows(result):
        lines.append(
            f"- {row['year']} | {row['fs']} | 자산 {row['total_assets_100m'] or '미확보'}억원 "
            f"| 매출 {row['revenue_100m'] or '미확보'}억원 | 보수 {row['audit_fee_m'] or '미확보'}백만원 "
            f"| 시간 {row['audit_hours'] or '미확보'} | {row['input_status']}"
        )
    return "\n".join(lines)


PACK_BUILDERS: dict[str, PackBuilder] = {
    "prepare_standard_audit_hours_inputs": _build_prepare_inputs_pack,
    "compare_peer_audit_fees": _build_fee_comparison_pack,
    "estimate_audit_hours_proxy": _build_hours_proxy_pack,
}
DETAIL_RENDERERS: dict[str, DetailRenderer] = {
    "prepare_standard_audit_hours_inputs": _render_prepare_inputs,
}
