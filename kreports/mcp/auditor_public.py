"""Public labels for auditor-facing MCP payloads."""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from kreports.analysis.evidence import parent_rcept_no

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
        text.isupper()
        or
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


def public_auditor_result(result: object) -> object:
    """Copy and sanitize auditor enum/receipt fields at the public boundary."""
    public = deepcopy(result)

    def transform(
        value: object,
        key: str | None = None,
        *,
        in_kam_analysis: bool = False,
    ) -> object:
        if isinstance(value, list):
            if key == "topic_hints" or (
                in_kam_analysis and key == "topics"
            ):
                return [public_kam_topic_label(item) for item in value]
            return [
                transform(item, in_kam_analysis=in_kam_analysis)
                for item in value
            ]
        if not isinstance(value, dict):
            if key in {"topic", "kam_topic"}:
                return public_kam_topic_label(value)
            if key == "lifecycle":
                return public_kam_lifecycle_label(value)
            return value
        if key == "kam_topics":
            return {
                public_kam_topic_label(topic): transform(count)
                for topic, count in value.items()
            }
        transformed = {}
        for field, item in value.items():
            if field == "kam_item_id":
                continue
            public_field = (
                "key_audit_matters" if field == "kam_items" else field
            )
            if field == "rcept_no":
                transformed[public_field] = parent_rcept_no(str(item or ""))
            else:
                transformed[public_field] = transform(
                    item,
                    field,
                    in_kam_analysis=(
                        in_kam_analysis or field == "kam_analysis"
                    ),
                )
        return transformed

    return transform(public)
