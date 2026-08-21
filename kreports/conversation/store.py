"""Bounded, identity-bound state and result storage for chat orchestration."""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from typing import Any

from kreports.conversation.contracts import (
    ConversationIdentity,
    ConversationState,
)


class StateHandleError(ValueError):
    """Malformed, forged, or otherwise unusable state handle."""


class StateExpiredError(StateHandleError):
    """State or page token is past its expiry."""


class StateAccessError(PermissionError):
    """A valid handle was presented by the wrong user/conversation."""


@dataclass
class _StoredState:
    state: ConversationState
    expires_at: float


@dataclass
class _StoredResult:
    result_id: str
    owner_hash: str
    rows: tuple[dict[str, Any], ...]
    metadata: dict[str, Any]
    expires_at: float


def _urlsafe(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unurlsafe(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except Exception as exc:
        raise StateHandleError("invalid_state_handle") from exc


class InMemoryConversationStore:
    """Thread-safe local store with signed opaque handles.

    This backend is appropriate for stdio and one-process development. A
    multi-worker HTTP deployment must use a shared backend or route all requests
    for a conversation to the same store. The handle format intentionally
    remains backend-neutral so a Redis implementation can be added without
    changing clients.
    """

    def __init__(
        self,
        *,
        signing_key: str | bytes | None = None,
        state_ttl_seconds: int = 86_400,
        result_ttl_seconds: int = 3_600,
        max_states: int = 2_000,
        max_results: int = 4_000,
    ) -> None:
        raw_key = (
            signing_key.encode("utf-8")
            if isinstance(signing_key, str)
            else signing_key
        )
        self._key = raw_key or secrets.token_bytes(32)
        if len(self._key) < 32:
            raise ValueError("signing_key must be at least 32 bytes")
        self.process_local_key = signing_key is None
        self.state_ttl_seconds = max(60, int(state_ttl_seconds))
        self.result_ttl_seconds = max(60, int(result_ttl_seconds))
        self.max_states = max(10, int(max_states))
        self.max_results = max(10, int(max_results))
        self._states: OrderedDict[str, _StoredState] = OrderedDict()
        self._results: OrderedDict[str, _StoredResult] = OrderedDict()
        self._lock = threading.RLock()

    @staticmethod
    def _owner_hash(identity: ConversationIdentity) -> str:
        payload = (
            f"{identity.user_key}\x00{identity.conversation_key}\x00"
            f"{identity.client_key}"
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _sign(self, payload: bytes) -> str:
        return _urlsafe(hmac.new(self._key, payload, hashlib.sha256).digest())

    def _encode_token(
        self,
        *,
        kind: str,
        identifier: str,
        identity: ConversationIdentity,
        expires_at: int,
        extra: dict[str, Any] | None = None,
    ) -> str:
        body = {
            "v": 1,
            "k": kind,
            "id": identifier,
            "owner": self._owner_hash(identity),
            "exp": expires_at,
            **(extra or {}),
        }
        payload = json.dumps(
            body,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return f"kr1.{_urlsafe(payload)}.{self._sign(payload)}"

    def _decode_token(
        self,
        token: str,
        *,
        expected_kind: str,
        identity: ConversationIdentity,
    ) -> dict[str, Any]:
        try:
            prefix, encoded, signature = token.split(".", 2)
        except ValueError:
            raise StateHandleError("invalid_state_handle") from None
        if prefix != "kr1":
            raise StateHandleError("invalid_state_handle")
        payload = _unurlsafe(encoded)
        if not hmac.compare_digest(signature, self._sign(payload)):
            raise StateHandleError("invalid_state_handle")
        try:
            body = json.loads(payload)
        except json.JSONDecodeError:
            raise StateHandleError("invalid_state_handle") from None
        if body.get("v") != 1 or body.get("k") != expected_kind:
            raise StateHandleError("invalid_state_handle")
        if body.get("owner") != self._owner_hash(identity):
            raise StateAccessError("state_handle_owner_mismatch")
        if int(body.get("exp") or 0) < int(time.time()):
            raise StateExpiredError("state_handle_expired")
        return body

    def _evict(self) -> None:
        now = time.time()
        for mapping in (self._states, self._results):
            expired = [
                key for key, value in mapping.items()
                if value.expires_at <= now
            ]
            for key in expired:
                mapping.pop(key, None)
        while len(self._states) > self.max_states:
            self._states.popitem(last=False)
        while len(self._results) > self.max_results:
            self._results.popitem(last=False)

    def create_state(
        self,
        identity: ConversationIdentity,
    ) -> tuple[str, ConversationState]:
        now = time.time()
        state_id = secrets.token_urlsafe(24)
        state = ConversationState(
            state_id=state_id,
            user_key=identity.user_key,
            conversation_key=identity.conversation_key,
        )
        with self._lock:
            self._states[state_id] = _StoredState(
                state=state,
                expires_at=now + self.state_ttl_seconds,
            )
            self._states.move_to_end(state_id)
            self._evict()
        handle = self._encode_token(
            kind="state",
            identifier=state_id,
            identity=identity,
            expires_at=int(now + self.state_ttl_seconds),
        )
        return handle, deepcopy(state)

    def get_state(
        self,
        handle: str,
        identity: ConversationIdentity,
    ) -> ConversationState:
        body = self._decode_token(
            handle,
            expected_kind="state",
            identity=identity,
        )
        state_id = str(body["id"])
        with self._lock:
            self._evict()
            stored = self._states.get(state_id)
            if stored is None:
                raise StateExpiredError("state_not_found_or_expired")
            if (
                stored.state.user_key != identity.user_key
                or stored.state.conversation_key != identity.conversation_key
            ):
                raise StateAccessError("state_handle_owner_mismatch")
            self._states.move_to_end(state_id)
            return deepcopy(stored.state)

    def save_state(
        self,
        handle: str,
        identity: ConversationIdentity,
        state: ConversationState,
    ) -> ConversationState:
        body = self._decode_token(
            handle,
            expected_kind="state",
            identity=identity,
        )
        state_id = str(body["id"])
        if state.state_id != state_id:
            raise StateAccessError("state_id_mismatch")
        if (
            state.user_key != identity.user_key
            or state.conversation_key != identity.conversation_key
        ):
            raise StateAccessError("state_owner_mismatch")
        updated = state.model_copy(
            update={
                "revision": state.revision + 1,
                "updated_at": datetime.now(timezone.utc),
            },
            deep=True,
        )
        with self._lock:
            stored = self._states.get(state_id)
            if stored is None:
                raise StateExpiredError("state_not_found_or_expired")
            stored.state = updated
            stored.expires_at = time.time() + self.state_ttl_seconds
            self._states.move_to_end(state_id)
            self._evict()
        return deepcopy(updated)

    def store_result(
        self,
        *,
        identity: ConversationIdentity,
        rows: list[dict[str, Any]],
        metadata: dict[str, Any] | None = None,
        page_size: int = 5,
    ) -> tuple[str, str | None]:
        page_size = max(1, min(int(page_size), 50))
        result_id = secrets.token_urlsafe(24)
        now = time.time()
        copied_rows = tuple(deepcopy(rows[:10_000]))
        with self._lock:
            self._results[result_id] = _StoredResult(
                result_id=result_id,
                owner_hash=self._owner_hash(identity),
                rows=copied_rows,
                metadata=deepcopy(metadata or {}),
                expires_at=now + self.result_ttl_seconds,
            )
            self._results.move_to_end(result_id)
            self._evict()
        first_page = self._encode_token(
            kind="page",
            identifier=result_id,
            identity=identity,
            expires_at=int(now + self.result_ttl_seconds),
            extra={"offset": 0, "size": page_size},
        )
        return result_id, first_page if copied_rows else None

    def get_page(
        self,
        page_token: str,
        identity: ConversationIdentity,
    ) -> dict[str, Any]:
        body = self._decode_token(
            page_token,
            expected_kind="page",
            identity=identity,
        )
        result_id = str(body["id"])
        offset = max(0, int(body.get("offset") or 0))
        size = max(1, min(int(body.get("size") or 5), 50))
        with self._lock:
            self._evict()
            stored = self._results.get(result_id)
            if stored is None:
                raise StateExpiredError("result_not_found_or_expired")
            if stored.owner_hash != self._owner_hash(identity):
                raise StateAccessError("result_owner_mismatch")
            self._results.move_to_end(result_id)
            total = len(stored.rows)
            rows = deepcopy(list(stored.rows[offset:offset + size]))
            expires_at = int(stored.expires_at)
            next_offset = offset + len(rows)
            previous_offset = max(0, offset - size)
            next_token = (
                self._encode_token(
                    kind="page",
                    identifier=result_id,
                    identity=identity,
                    expires_at=expires_at,
                    extra={"offset": next_offset, "size": size},
                )
                if next_offset < total
                else None
            )
            previous_token = (
                self._encode_token(
                    kind="page",
                    identifier=result_id,
                    identity=identity,
                    expires_at=expires_at,
                    extra={"offset": previous_offset, "size": size},
                )
                if offset > 0
                else None
            )
            return {
                "result_id": result_id,
                "rows": rows,
                "metadata": deepcopy(stored.metadata),
                "pagination": {
                    "offset": offset,
                    "page_size": size,
                    "returned": len(rows),
                    "total": total,
                    "has_more": next_token is not None,
                    "next_page_token": next_token,
                    "previous_page_token": previous_token,
                },
            }
