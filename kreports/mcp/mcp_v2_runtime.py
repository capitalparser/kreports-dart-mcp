"""Shared runtime helpers for the optional MCP 2026-07-28 adapter."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
import time
from typing import Any, Awaitable, Callable

from kreports.conversation.contracts import ConversationIdentity
from kreports.conversation.orchestrator import PeerConversationOrchestrator
from kreports.conversation.store import InMemoryConversationStore
from kreports.db.engine import get_session
from kreports.db.models import DatasetManifest


_VENDOR_META_KEY = "io.kreports/context"
_HEAVY_TOOLS = {
    "select_peer_group",
    "compare_to_industry_multi",
    "compare_peer_accounting_notes",
    "compare_peer_accounting_policies",
    "compare_peer_audit_fees",
    "compare_peer_risk_profile",
    "compare_peer_kam_topics",
    "compare_peer_audit_report_matters",
    "compare_peer_audit_procedures",
}
_NON_CACHEABLE_TOOLS = {"fetch_disclosure_on_demand"}
_DEFAULT_CACHE_TTL_SECONDS = 300
_DEFAULT_HEAVY_CONCURRENCY = 4


def _state_signing_key() -> bytes | None:
    value = os.environ.get("KREPORTS_STATE_SIGNING_KEY")
    if value is None:
        return None
    raw = value.encode("utf-8")
    if len(raw) < 32:
        raise RuntimeError(
            "KREPORTS_STATE_SIGNING_KEY must be at least 32 UTF-8 bytes"
        )
    return raw


conversation_store = InMemoryConversationStore(
    signing_key=_state_signing_key(),
    state_ttl_seconds=int(
        os.environ.get("KREPORTS_CONVERSATION_TTL_SECONDS", "86400")
    ),
    result_ttl_seconds=int(
        os.environ.get("KREPORTS_RESULT_TTL_SECONDS", "3600")
    ),
)
conversation_orchestrator = PeerConversationOrchestrator(conversation_store)


def _plain_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="python", by_alias=True)
        return dict(dumped) if isinstance(dumped, dict) else {}
    return {}


def request_meta(ctx: Any) -> dict[str, Any]:
    return _plain_mapping(getattr(ctx, "meta", None))


def vendor_context(ctx: Any) -> dict[str, Any]:
    value = request_meta(ctx).get(_VENDOR_META_KEY)
    return _plain_mapping(value)


def request_identity(ctx: Any) -> ConversationIdentity:
    """Read identity asserted by the trusted chatbot host.

    Production HTTP deployments should require these fields at the gateway. The
    fallback identity exists only for local/in-memory clients and deliberately
    carries no user-specific preference beyond the current process.
    """

    context = vendor_context(ctx)
    user_key = str(context.get("userId") or "anonymous-local")
    conversation_key = str(
        context.get("conversationId") or "anonymous-conversation"
    )
    client_key = str(context.get("clientId") or "generic-mcp-client")
    return ConversationIdentity(
        user_key=user_key[:200],
        conversation_key=conversation_key[:200],
        client_key=client_key[:160],
    )


def interactive_requested(ctx: Any) -> bool:
    return vendor_context(ctx).get("interactive") is True


def save_preferences_requested(ctx: Any) -> bool:
    return vendor_context(ctx).get("savePreferences") is True


def recent_turns(ctx: Any) -> list[dict[str, str]]:
    value = vendor_context(ctx).get("recentTurns")
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    for item in value[-8:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "user")[:20]
        content = " ".join(str(item.get("content") or "").split())[:700]
        if content:
            result.append({"role": role, "content": content})
    return result


def supplied_state_handle(ctx: Any) -> str | None:
    value = vendor_context(ctx).get("stateHandle")
    return str(value) if value else None


def supplied_page_token(ctx: Any) -> str | None:
    value = vendor_context(ctx).get("pageToken")
    return str(value) if value else None


def dataset_fingerprint() -> str:
    """Return one stable cache namespace for the current prepared dataset."""

    try:
        with get_session() as session:
            row = (
                session.query(DatasetManifest)
                .order_by(
                    DatasetManifest.generated_at.desc(),
                    DatasetManifest.manifest_id.desc(),
                )
                .first()
            )
            if row is None:
                return "dataset:unknown"
            return (
                f"dataset:{row.dataset_version}:"
                f"schema:{row.schema_version}:"
                f"manifest:{row.manifest_id}"
            )
    except Exception:
        return "dataset:unknown"


def canonical_cache_key(
    tool_name: str,
    arguments: dict[str, Any],
) -> str:
    payload = json.dumps(
        {
            "dataset": dataset_fingerprint(),
            "tool": tool_name,
            "arguments": arguments,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class ExecutionEvidence:
    cache_hit: bool
    shared_execution: bool
    duration_ms: float
    cache_key: str


@dataclass
class _CacheEntry:
    value: dict[str, Any]
    expires_at: float


class ToolExecutionCoordinator:
    """Bounded TTL cache plus single-flight and heavy-tool concurrency control."""

    def __init__(
        self,
        *,
        ttl_seconds: int = _DEFAULT_CACHE_TTL_SECONDS,
        heavy_concurrency: int = _DEFAULT_HEAVY_CONCURRENCY,
        max_entries: int = 512,
    ) -> None:
        self.ttl_seconds = max(1, int(ttl_seconds))
        self.max_entries = max(16, int(max_entries))
        self._cache: dict[str, _CacheEntry] = {}
        self._inflight: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._lock = asyncio.Lock()
        self._heavy_semaphore = asyncio.Semaphore(
            max(1, int(heavy_concurrency))
        )

    def _purge(self, now: float) -> None:
        expired = [
            key for key, entry in self._cache.items()
            if entry.expires_at <= now
        ]
        for key in expired:
            self._cache.pop(key, None)
        if len(self._cache) > self.max_entries:
            ordered = sorted(
                self._cache.items(),
                key=lambda item: item[1].expires_at,
            )
            for key, _entry in ordered[: len(self._cache) - self.max_entries]:
                self._cache.pop(key, None)

    async def execute(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        runner: Callable[[], Awaitable[dict[str, Any]]],
    ) -> tuple[dict[str, Any], ExecutionEvidence]:
        started = time.perf_counter()
        key = canonical_cache_key(tool_name, arguments)
        cacheable = tool_name not in _NON_CACHEABLE_TOOLS
        owner = False

        async with self._lock:
            now = time.monotonic()
            self._purge(now)
            if cacheable:
                entry = self._cache.get(key)
                if entry is not None and entry.expires_at > now:
                    return deepcopy(entry.value), ExecutionEvidence(
                        cache_hit=True,
                        shared_execution=False,
                        duration_ms=round(
                            (time.perf_counter() - started) * 1000,
                            2,
                        ),
                        cache_key=key,
                    )
            future = self._inflight.get(key)
            if future is None:
                future = asyncio.get_running_loop().create_future()
                self._inflight[key] = future
                owner = True

        if not owner:
            value = await asyncio.shield(future)
            return deepcopy(value), ExecutionEvidence(
                cache_hit=False,
                shared_execution=True,
                duration_ms=round(
                    (time.perf_counter() - started) * 1000,
                    2,
                ),
                cache_key=key,
            )

        try:
            if tool_name in _HEAVY_TOOLS:
                async with self._heavy_semaphore:
                    value = await runner()
            else:
                value = await runner()
            if not isinstance(value, dict):
                value = {"value": value}
            stored = deepcopy(value)
            async with self._lock:
                if cacheable:
                    self._cache[key] = _CacheEntry(
                        value=stored,
                        expires_at=(
                            time.monotonic() + self.ttl_seconds
                        ),
                    )
                    self._purge(time.monotonic())
                future = self._inflight.pop(key, None)
                if future is not None and not future.done():
                    future.set_result(deepcopy(stored))
            return value, ExecutionEvidence(
                cache_hit=False,
                shared_execution=False,
                duration_ms=round(
                    (time.perf_counter() - started) * 1000,
                    2,
                ),
                cache_key=key,
            )
        except BaseException as exc:
            async with self._lock:
                future = self._inflight.pop(key, None)
                if future is not None and not future.done():
                    future.set_exception(exc)
                    # Consume the exception when there were no waiters.
                    try:
                        future.exception()
                    except BaseException:
                        pass
            raise

    async def clear(self) -> None:
        async with self._lock:
            self._cache.clear()


execution_coordinator = ToolExecutionCoordinator(
    ttl_seconds=int(
        os.environ.get(
            "KREPORTS_TOOL_CACHE_TTL_SECONDS",
            str(_DEFAULT_CACHE_TTL_SECONDS),
        )
    ),
    heavy_concurrency=int(
        os.environ.get(
            "KREPORTS_HEAVY_TOOL_CONCURRENCY",
            str(_DEFAULT_HEAVY_CONCURRENCY),
        )
    ),
)


def _peer_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in result.get("peers") or []
        if isinstance(row, dict)
    ]


def _note_search_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for company in result.get("companies") or []:
        if not isinstance(company, dict):
            continue
        records = [
            record for record in company.get("records") or []
            if isinstance(record, dict)
        ]
        rows.append(
            {
                "company": company.get("corp_name")
                or company.get("corp_code"),
                "corp_code": company.get("corp_code"),
                "stock_code": company.get("stock_code"),
                "market": company.get("market"),
                "record_count": company.get("record_count")
                or len(records),
                "records": deepcopy(records[:10]),
            }
        )
    return rows


def _note_comparison_rows(
    result: dict[str, Any],
) -> list[dict[str, Any]]:
    subject_code = str(
        (result.get("subject") or {}).get("corp_code") or ""
    )
    by_company: dict[str, dict[str, Any]] = {}
    for topic in result.get("topics") or []:
        if not isinstance(topic, dict):
            continue
        topic_key = str(topic.get("topic") or "")
        for row in topic.get("rows") or []:
            if not isinstance(row, dict):
                continue
            company = row.get("company") or {}
            code = str(company.get("corp_code") or "")
            if not code or code == subject_code:
                continue
            item = by_company.setdefault(
                code,
                {
                    "company": company.get("corp_name") or code,
                    "corp_code": code,
                    "topics": [],
                },
            )
            item["topics"].append(
                {
                    "topic": topic_key,
                    "availability": row.get("availability"),
                    "fs_div": row.get("fs_div"),
                    "fs_div_selection": deepcopy(
                        row.get("fs_div_selection")
                    ),
                    "note_title": row.get("note_title"),
                    "excerpt": row.get("comparison_text")
                    or row.get("value_or_excerpt"),
                    "rcept_no": row.get("rcept_no"),
                }
            )
    return list(by_company.values())


def extract_page_rows(
    tool_name: str,
    result: dict[str, Any],
) -> list[dict[str, Any]]:
    if tool_name == "select_peer_group":
        return _peer_rows(result)
    if (
        tool_name == "search_dataset"
        and (result.get("query") or {}).get("dataset")
        == "accounting_note_chapters"
    ):
        return _note_search_rows(result)
    if tool_name == "compare_peer_accounting_notes":
        return _note_comparison_rows(result)
    return []


def page_answer(page: dict[str, Any]) -> str:
    pagination = page["pagination"]
    start = pagination["offset"] + 1 if pagination["returned"] else 0
    end = pagination["offset"] + pagination["returned"]
    total = pagination["total"]
    if not pagination["returned"]:
        return "현재 페이지에 표시할 회사가 없습니다."
    return (
        f"전체 {total}개 회사 중 {start}~{end}번째 회사를 "
        "보여드립니다."
    )
