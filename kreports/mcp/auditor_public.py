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
_MACHINE_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_.-]*", re.ASCII)
_ACRONYM_BOUNDARY = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])", re.ASCII)
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])", re.ASCII)


def _canonical_enum_key(text: str) -> str:
    tokenized = _ACRONYM_BOUNDARY.sub("_", text)
    tokenized = _CAMEL_BOUNDARY.sub("_", tokenized)
    return tokenized.replace("-", "_").replace(
        ".",
        "_",
    ).casefold()


def _is_machine_enum(text: str) -> bool:
    if not _MACHINE_TOKEN.fullmatch(text):
        return False
    return (
        any(separator in text for separator in "_.-")
        or _ACRONYM_BOUNDARY.search(text) is not None
        or _CAMEL_BOUNDARY.search(text) is not None
        or (
            any(character.isdigit() for character in text)
            and any(character.isalpha() for character in text)
        )
    )


def public_kam_topic_label(value: object) -> str:
    text = str(value or "").strip()
    key = _canonical_enum_key(text)
    if key in _KAM_TOPIC_LABELS:
        return _KAM_TOPIC_LABELS[key]
    if _is_machine_enum(text):
        return "기타 핵심감사사항"
    return text or "미분류"


def public_kam_lifecycle_label(value: object) -> str:
    text = str(value or "").strip()
    key = _canonical_enum_key(text)
    if key in _KAM_LIFECYCLE_LABELS:
        return _KAM_LIFECYCLE_LABELS[key]
    if _is_machine_enum(text):
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
