"""Render exact peer criteria and company-level selection evidence.

The canonical selector decides membership and order. This adapter only presents
``selection_explanation`` in business language so the user can see which
customized criteria were applied, how many companies qualified, why each shown
company is present, and whether the display order is a relevance ranking or a
simple deterministic order.
"""
from __future__ import annotations

from typing import Any

from kreports.mcp.chatbot_contracts import (
    ChatbotColumnV1,
    ChatbotTableV1,
    ChatbotViewV1,
)


_CRITERIA_TABLE_ID = "peer_applied_criteria"
_STATUS_SUFFIXES = {
    "not_applied": " · 현재 선별에 미반영",
    "informational": " · 참고 정보",
    "unsupported": " · 현재 지원되지 않음",
}
_CRITERIA_GROUPS = (
    (
        "분석 기준",
        ("origin", "year", "fs_basis"),
    ),
    (
        "업종 범위",
        ("industry", "excluded_sectors"),
    ),
    (
        "회사 규모",
        ("size",),
    ),
    (
        "자료·직접 지정",
        (
            "required_features",
            "business_tags",
            "included_companies",
            "excluded_companies",
        ),
    ),
    (
        "선정·표시 순서",
        ("selection_mode", "weights"),
    ),
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _explanation(result: dict[str, Any]) -> dict[str, Any]:
    direct = _dict(result.get("selection_explanation"))
    if direct:
        return direct
    return _dict(_dict(result.get("peer_group")).get("selection_explanation"))


def _criterion_text(item: dict[str, Any]) -> str:
    label = str(item.get("label") or item.get("key") or "기준")
    value = str(item.get("value") or "확인 필요")
    value += _STATUS_SUFFIXES.get(str(item.get("status") or ""), "")
    return f"{label}: {value}"


def _criteria_table(explanation: dict[str, Any]) -> ChatbotTableV1:
    items = {
        str(item.get("key")): item
        for item in explanation.get("applied_criteria") or []
        if isinstance(item, dict) and item.get("key")
    }
    ordering = _dict(explanation.get("ordering"))
    rows: list[dict[str, Any]] = []
    for group_label, keys in _CRITERIA_GROUPS:
        parts = [
            _criterion_text(items[key])
            for key in keys
            if key in items
        ]
        if group_label == "선정·표시 순서" and ordering.get("label"):
            parts.append(f"표시 순서: {ordering['label']}")
            if ordering.get("detail"):
                parts.append(f"순서 산정 참고: {ordering['detail']}")
        if parts:
            rows.append({
                "criterion": group_label,
                "applied_value": " · ".join(parts),
            })

    note = (
        "아래 회사들은 표시된 실제 적용 기준으로 선정됐습니다. 기준을 "
        "변경하면 비교회사 모집단과 그에 따른 분석 결과도 다시 계산됩니다."
    )
    if ordering.get("is_relevance_ranking"):
        note += (
            " 기준 적합도는 표시된 계산 기준만을 의미하며 사업모델 전체의 "
            "유사성을 의미하지 않습니다."
        )

    return ChatbotTableV1(
        id=_CRITERIA_TABLE_ID,
        title="적용한 비교 기준",
        columns=[
            ChatbotColumnV1(key="criterion", label="기준"),
            ChatbotColumnV1(key="applied_value", label="실제 적용 내용"),
        ],
        rows=rows[:5],
        note=note,
    )


def _company_explanation_map(
    explanation: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for item in explanation.get("company_explanations") or []:
        if not isinstance(item, dict):
            continue
        for key in (item.get("company"), item.get("corp_code")):
            if key:
                mapping[str(key)] = item
    return mapping


def _update_peer_tables(
    tables: list[ChatbotTableV1],
    explanation: dict[str, Any],
) -> list[ChatbotTableV1]:
    mapping = _company_explanation_map(explanation)
    ordering = _dict(explanation.get("ordering"))
    updated: list[ChatbotTableV1] = []

    for table in tables:
        if table.id == _CRITERIA_TABLE_ID:
            continue
        if not any(column.key == "reason" for column in table.columns):
            updated.append(table)
            continue

        rows: list[dict[str, Any]] = []
        for raw_row in table.rows:
            row = dict(raw_row)
            company = str(row.get("company") or "")
            item = mapping.get(company)
            if item:
                row["reason"] = item.get("criteria_reason_text") or row.get("reason")
            rows.append(row)

        columns = [
            (
                ChatbotColumnV1(
                    key="reason",
                    label="기준 충족 근거",
                )
                if column.key == "reason"
                else column
            )
            for column in table.columns
        ]
        note_parts = [
            str(table.note or "").strip(),
            f"표시 순서: {ordering.get('label') or '고정된 순서'}.",
        ]
        if ordering.get("detail"):
            note_parts.append(str(ordering["detail"]))
        updated.append(table.model_copy(update={
            "columns": columns,
            "rows": rows,
            "note": " ".join(part for part in note_parts if part),
        }))
    return updated


def _summary_for_peer_group(
    explanation: dict[str, Any],
) -> str:
    population = _dict(explanation.get("population"))
    total = int(population.get("eligible_company_count") or 0)
    sentence = str(explanation.get("criteria_sentence") or "").strip()
    ordering = _dict(explanation.get("ordering"))
    if total:
        return (
            f"조건에 해당하는 회사 {total}개를 찾았습니다. {sentence} "
            f"아래 회사는 {ordering.get('label') or '고정된 순서'}로 보여드립니다."
        )
    return (
        "현재 확보된 자료에서는 조건에 해당하는 회사를 찾지 못했습니다. "
        f"{sentence}"
    )


def _summary_for_analysis(
    view: ChatbotViewV1,
    explanation: dict[str, Any],
) -> str:
    population = _dict(explanation.get("population"))
    total = int(population.get("eligible_company_count") or 0)
    sentence = str(explanation.get("criteria_sentence") or "").strip()
    suffix = (
        f" 이 분석은 위 기준으로 선정한 {total}개사를 비교 모집단으로 사용합니다."
        if total
        else " 이 분석은 위 기준으로 선정한 비교회사를 사용합니다."
    )
    return f"{view.summary} {sentence}{suffix}".strip()


def polish_peer_selection_transparency(
    tool_name: str,
    view: ChatbotViewV1,
    result: dict[str, Any],
) -> ChatbotViewV1:
    """Prepend applied criteria and synchronize company-level reasons."""
    explanation = _explanation(result)
    if not explanation:
        return view

    criteria_table = _criteria_table(explanation)
    peer_tables = _update_peer_tables(list(view.tables), explanation)
    summary = (
        _summary_for_peer_group(explanation)
        if tool_name == "select_peer_group"
        else _summary_for_analysis(view, explanation)
    )
    warnings = list(view.warnings)
    for limitation in explanation.get("limitations") or []:
        text = str(limitation)
        if text and text not in warnings:
            warnings.append(text)

    next_actions = list(view.next_actions)
    for action in (
        "비교 기준을 변경해 다시 선정해줘.",
        "각 회사가 어떤 기준을 충족했는지 설명해줘.",
    ):
        if action not in next_actions:
            next_actions.append(action)

    return view.model_copy(update={
        "summary": summary,
        "tables": [criteria_table, *peer_tables],
        "warnings": warnings[:8],
        "next_actions": next_actions[:6],
    })


__all__ = ["polish_peer_selection_transparency"]
