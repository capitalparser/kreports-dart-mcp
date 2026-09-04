"""Owner-only DART API key rotation without persisting key material."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import stat


_SCHEMA = "dart-api-key-rotation.v1"
_MAX_KEYS = 32


def _key_id(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


@dataclass
class DartApiKeyRing:
    """Sequential key ring whose durable state contains identifiers only."""

    primary_key: str
    key_file: Path | None
    state_file: Path
    _keys: tuple[str, ...] = field(init=False, repr=False)
    _current_key_id: str = field(init=False, repr=False)
    _quota_limited_ids: set[str] = field(init=False, repr=False, default_factory=set)
    _invalid_ids: set[str] = field(init=False, repr=False, default_factory=set)

    def __post_init__(self) -> None:
        self.primary_key = self.primary_key.strip()
        self._keys = self._load_keys()
        if not self._keys:
            raise ValueError("at least one DART API key is required")
        self._current_key_id = _key_id(self._keys[0])
        self._load_state()

    @classmethod
    def from_values(
        cls,
        *,
        primary_key: str,
        key_file: Path | None,
        state_file: Path,
    ) -> "DartApiKeyRing":
        return cls(primary_key=primary_key, key_file=key_file, state_file=state_file)

    @classmethod
    def from_runtime(cls, *, state_dir: Path) -> "DartApiKeyRing":
        from kreports.config import settings

        configured_file = os.environ.get("DART_API_KEYS_FILE", "").strip()
        return cls.from_values(
            primary_key=settings.dart_api_key,
            key_file=Path(configured_file).expanduser() if configured_file else None,
            state_file=Path(state_dir) / "dart-api-key-rotation.json",
        )

    @property
    def current_key(self) -> str:
        for key in self._keys:
            if _key_id(key) == self._current_key_id:
                return key
        raise ValueError("selected DART API key is no longer configured")

    @property
    def current_key_id(self) -> str:
        return self._current_key_id

    @property
    def key_count(self) -> int:
        return len(self._keys)

    def advance_after_quota(self) -> bool:
        self._refresh_keys()
        self._quota_limited_ids.add(self._current_key_id)
        advanced = self._select_next_available()
        self._persist_state("quota_rotate" if advanced else "all_keys_quota_limited")
        return advanced

    def begin_new_quota_cycle(self) -> bool:
        self._refresh_keys()
        previous = self._current_key_id
        self._quota_limited_ids.clear()
        if not self._select_next_available():
            raise ValueError("all configured DART API keys are invalid")
        self._persist_state("quota_probe_cycle")
        return self._current_key_id != previous

    def advance_after_auth_failure(self) -> bool:
        self._refresh_keys()
        self._invalid_ids.add(self._current_key_id)
        self._quota_limited_ids.discard(self._current_key_id)
        advanced = self._select_next_available()
        self._persist_state("auth_rotate" if advanced else "all_keys_invalid")
        return advanced

    def _refresh_keys(self) -> None:
        refreshed = self._load_keys()
        if not refreshed:
            raise ValueError("at least one DART API key is required")
        self._keys = refreshed
        configured_ids = {_key_id(key) for key in refreshed}
        self._quota_limited_ids.intersection_update(configured_ids)
        self._invalid_ids.intersection_update(configured_ids)
        if self._current_key_id not in configured_ids:
            self._current_key_id = _key_id(refreshed[0])

    def _load_keys(self) -> tuple[str, ...]:
        values = [self.primary_key] if self.primary_key else []
        if self.key_file is not None:
            _validate_key_file(self.key_file)
            values.extend(
                line.strip()
                for line in self.key_file.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            )
        deduplicated = tuple(dict.fromkeys(values))
        if len(deduplicated) > _MAX_KEYS:
            raise ValueError(f"DART API key count exceeds {_MAX_KEYS}")
        return deduplicated

    def _load_state(self) -> None:
        if not self.state_file.exists():
            return
        try:
            payload = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("invalid DART API key rotation state") from exc
        if payload.get("schema") != _SCHEMA:
            raise ValueError("unsupported DART API key rotation state")
        configured_ids = {_key_id(key) for key in self._keys}
        self._quota_limited_ids = set(payload.get("quota_limited_key_ids", ())) & configured_ids
        self._invalid_ids = set(payload.get("invalid_key_ids", ())) & configured_ids
        current = payload.get("current_key_id")
        if current in configured_ids and current not in self._invalid_ids:
            self._current_key_id = str(current)
        elif not self._select_next_available():
            raise ValueError("all configured DART API keys are invalid")

    def _select_next_available(self) -> bool:
        ids = [_key_id(key) for key in self._keys]
        try:
            start = ids.index(self._current_key_id)
        except ValueError:
            start = -1
        for offset in range(1, len(ids) + 1):
            candidate = ids[(start + offset) % len(ids)]
            if candidate not in self._quota_limited_ids and candidate not in self._invalid_ids:
                self._current_key_id = candidate
                return True
        return False

    def _persist_state(self, event: str) -> None:
        payload = {
            "schema": _SCHEMA,
            "current_key_id": self._current_key_id,
            "quota_limited_key_ids": sorted(self._quota_limited_ids),
            "invalid_key_ids": sorted(self._invalid_ids),
            "event": event,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_file.with_suffix(self.state_file.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(self.state_file)


def _validate_key_file(path: Path) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ValueError("DART API key file is unavailable") from exc
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        raise ValueError("DART API key file must be a regular file")
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("DART API key file must be owner-only")
