"""Public labels for auditor-facing MCP payloads."""
from __future__ import annotations

import re
from typing import Any


_KAM_TOPIC_LABELS = {
    "revenue": "수익인식",
    "revenue_recognition": "수익인식",
    "inventory": "재고자산",
    "impairment": "손상평가",
    "fair_value": "공정가치",
    "provision": "충당부채 및 우발사항",
    "provisions": "충당부채 및 우발사항",
    "going_concern": "계속기업",
    "consolidation": "연결범위",
    "tax": "법인세",
    "development_cost": "개발비",
    "unknown": "미분류",
}
_KAM_LIFECYCLE_LABELS = {
    "new": "신규",
    "repeated_changed": "반복·문구 변경",
    "repeated_stable": "반복·문구 안정",
}
_MACHINE_ENUM = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", re.ASCII)


def public_kam_topic_label(value: object) -> str:
    text = str(value or "").strip()
    if text in _KAM_TOPIC_LABELS:
        return _KAM_TOPIC_LABELS[text]
    if _MACHINE_ENUM.fullmatch(text):
        return "기타 핵심감사사항"
    return text or "미분류"


def public_kam_lifecycle_label(value: object) -> str:
    text = str(value or "").strip()
    if text in _KAM_LIFECYCLE_LABELS:
        return _KAM_LIFECYCLE_LABELS[text]
    if _MACHINE_ENUM.fullmatch(text):
        return "상태 미분류"
    return text or "미분류"


def public_kam_lifecycle_events(events: object) -> list[dict[str, Any]]:
    public_events: list[dict[str, Any]] = []
    for event in events if isinstance(events, list) else []:
        if not isinstance(event, dict):
            continue
        public_event = dict(event)
        if event.get("topic") not in {None, ""}:
            public_event["topic"] = public_kam_topic_label(event.get("topic"))
        if event.get("status") not in {None, ""}:
            public_event["status"] = public_kam_lifecycle_label(
                event.get("status"),
            )
        public_events.append(public_event)
    return public_events
