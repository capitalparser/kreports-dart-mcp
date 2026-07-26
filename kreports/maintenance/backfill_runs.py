"""Durable ownership, checkpoint, and recovery for long-running backfills."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import socket
import subprocess
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError

from kreports.db.engine import get_session
from kreports.db.models import BackfillRun


BACKFILL_OUTCOMES = (
    "success",
    "no_data",
    "quota_exceeded",
    "transport_error",
    "parse_error",
    "storage_error",
    "stale_failed",
    "interrupted",
)
FAILURE_OUTCOMES = frozenset(BACKFILL_OUTCOMES[2:])
MAX_CHECKPOINT_JSON_BYTES = 16_384
MAX_PARAMS_JSON_BYTES = 16_384
MAX_SUMMARY_JSON_BYTES = 32_768
MAX_ERROR_MESSAGE_CHARS = 4_000
_CURRENT_PROCESS_START: str | None = None
_UNVERIFIABLE_PROCESS_START = f"unverifiable:{uuid4().hex}"


class BackfillAlreadyRunning(RuntimeError):
    """Raised when the same logical backfill already has an active owner."""


class LeaseOwnershipError(RuntimeError):
    """Raised when a process attempts to mutate a lease it does not own."""


class BackfillRunError(RuntimeError):
    """Failure carrying one exact durable backfill outcome."""

    def __init__(self, outcome: str, message: str):
        if outcome not in FAILURE_OUTCOMES:
            raise ValueError(
                f"outcome must be one of {sorted(FAILURE_OUTCOMES)}"
            )
        self.outcome = outcome
        super().__init__(message)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _validate_json_value(value: Any, *, path: str = "$") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"JSON value at {path} must be finite")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"JSON object key at {path} must be a string")
            _validate_json_value(item, path=f"{path}.{key}")
        return
    raise ValueError(
        f"JSON value at {path} has unsupported type {type(value).__name__}"
    )


def _canonical_json(value: Any, *, max_bytes: int, label: str) -> str:
    _validate_json_value(value)
    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be JSON serializable") from exc
    if len(payload.encode("utf-8")) > max_bytes:
        raise ValueError(f"{label} JSON exceeds {max_bytes} bytes")
    return payload


def _logical_lease_key(
    task_type: str,
    year: int | None,
    market: str | None,
) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", task_type):
        raise ValueError(
            "task_type may contain only letters, digits, underscore, dot, and hyphen"
        )
    if market is not None and not re.fullmatch(r"[A-Za-z0-9_.-]+", market):
        raise ValueError(
            "market may contain only letters, digits, underscore, dot, and hyphen"
        )
    return f"{task_type}|{year if year is not None else ''}|{market or ''}"


def _parse_json(payload: str | None) -> dict[str, Any]:
    if not payload:
        return {}
    try:
        value = json.loads(
            payload,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant: {token}")
            ),
        )
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _bounded_json(
    payload: str | None,
    *,
    max_bytes: int,
) -> dict[str, Any]:
    if payload and len(payload.encode("utf-8")) > max_bytes:
        return {
            "_truncated": True,
            "bytes": len(payload.encode("utf-8")),
        }
    return _parse_json(payload)


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _summary_failure_outcome(summary: dict[str, Any]) -> str | None:
    declared = summary.get("error_class")
    if isinstance(declared, str) and declared in FAILURE_OUTCOMES:
        return declared
    for outcome in sorted(FAILURE_OUTCOMES):
        count = summary.get(outcome)
        if isinstance(count, int) and count > 0:
            return outcome
    for key in ("errors", "error"):
        value = summary.get(key)
        if value not in (None, 0, False, "", [], {}):
            return "storage_error"
    return None


@dataclass(frozen=True)
class BackfillLease:
    """Capability object whose token is required for every run mutation."""

    id: int
    owner_token: str

    @classmethod
    def start(
        cls,
        task_type: str,
        year: int | None,
        market: str | None,
        params: dict,
        *,
        force: bool = False,
    ) -> "BackfillLease":
        if not task_type or not task_type.strip():
            raise ValueError("task_type is required")
        params_json = _canonical_json(
            params,
            max_bytes=MAX_PARAMS_JSON_BYTES,
            label="params",
        )
        normalized_market = market.upper() if market else None
        lease_key = _logical_lease_key(task_type, year, normalized_market)
        now = _utcnow()
        owner_token = uuid4().hex
        owner_host = socket.gethostname()
        owner_process_start = current_process_start_identity()

        try:
            with get_session() as session:
                active = (
                    session.query(BackfillRun)
                    .filter(
                        BackfillRun.lease_key == lease_key,
                        BackfillRun.status == "running",
                    )
                    .order_by(
                        BackfillRun.started_at.desc(),
                        BackfillRun.id.desc(),
                    )
                    .first()
                )
                if active is not None:
                    raise BackfillAlreadyRunning(
                        "backfill already running: "
                        f"run_id={active.id}, task={task_type}, "
                        f"year={year}, market={normalized_market}, pid={active.pid}"
                    )
                prior = None
                if not force:
                    prior = (
                        session.query(BackfillRun)
                        .filter(
                            BackfillRun.lease_key == lease_key,
                            BackfillRun.params_json == params_json,
                            BackfillRun.status.in_(
                                tuple(sorted(FAILURE_OUTCOMES | {"error"}))
                            ),
                        )
                        .order_by(
                            BackfillRun.finished_at.desc(),
                            BackfillRun.id.desc(),
                        )
                        .first()
                    )
                run = BackfillRun(
                    task_type=task_type,
                    year=year,
                    market=normalized_market,
                    status="running",
                    pid=os.getpid(),
                    lease_key=lease_key,
                    owner_token=owner_token,
                    owner_host=owner_host,
                    owner_process_start=owner_process_start,
                    heartbeat_at=now,
                    checkpoint_json=(
                        prior.checkpoint_json
                        if prior and prior.checkpoint_json
                        else "{}"
                    ),
                    attempted_count=(prior.attempted_count or 0) if prior else 0,
                    saved_count=(prior.saved_count or 0) if prior else 0,
                    no_data_count=(prior.no_data_count or 0) if prior else 0,
                    error_count=(prior.error_count or 0) if prior else 0,
                    params_json=params_json,
                    started_at=now,
                )
                session.add(run)
                session.flush()
                run_id = int(run.id)
        except IntegrityError as exc:
            raise BackfillAlreadyRunning(
                "backfill already running: "
                f"task={task_type}, year={year}, market={normalized_market}"
            ) from exc
        return cls(id=run_id, owner_token=owner_token)

    def _owned_update(self, values: dict[str, Any]) -> None:
        with get_session() as session:
            changed = (
                session.query(BackfillRun)
                .filter(
                    BackfillRun.id == self.id,
                    BackfillRun.owner_token == self.owner_token,
                    BackfillRun.status == "running",
                )
                .update(values, synchronize_session=False)
            )
            if changed != 1:
                raise LeaseOwnershipError(
                    f"run {self.id} is not owned by this lease or is no longer running"
                )

    def heartbeat(self) -> None:
        self._owned_update({"heartbeat_at": _utcnow()})

    def checkpoint(
        self,
        state: dict,
        attempted: int,
        saved: int,
        no_data: int,
        errors: int,
    ) -> None:
        counts = (attempted, saved, no_data, errors)
        if any(not isinstance(value, int) or value < 0 for value in counts):
            raise ValueError("checkpoint counters must be non-negative integers")
        checkpoint_json = _canonical_json(
            state,
            max_bytes=MAX_CHECKPOINT_JSON_BYTES,
            label="checkpoint",
        )
        self._owned_update(
            {
                "checkpoint_json": checkpoint_json,
                "attempted_count": attempted,
                "saved_count": saved,
                "no_data_count": no_data,
                "error_count": errors,
                "heartbeat_at": _utcnow(),
            }
        )

    def succeed(self, summary: dict) -> None:
        failure_outcome = _summary_failure_outcome(summary)
        if failure_outcome is not None:
            raise BackfillRunError(
                failure_outcome,
                "cannot succeed a backfill whose summary contains errors",
            )
        summary_json = _canonical_json(
            summary,
            max_bytes=MAX_SUMMARY_JSON_BYTES,
            label="summary",
        )
        now = _utcnow()
        self._owned_update(
            {
                "status": "success",
                "summary_json": summary_json,
                "heartbeat_at": now,
                "finished_at": now,
            }
        )

    def fail(self, error_class: str, error_message: str) -> None:
        if error_class not in FAILURE_OUTCOMES:
            raise ValueError(
                f"error_class must be one of {sorted(FAILURE_OUTCOMES)}"
            )
        now = _utcnow()
        self._owned_update(
            {
                "status": error_class,
                "error_msg": str(error_message)[:MAX_ERROR_MESSAGE_CHARS],
                "heartbeat_at": now,
                "finished_at": now,
            }
        )

    @staticmethod
    def resume_point(run_id: int) -> dict:
        with get_session() as session:
            run = session.get(BackfillRun, run_id)
            if run is None:
                raise KeyError(f"backfill run not found: {run_id}")
            return _bounded_json(
                run.checkpoint_json,
                max_bytes=MAX_CHECKPOINT_JSON_BYTES,
            )


def pid_is_alive(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def process_start_identity(pid: int | None) -> str | None:
    """Return an OS-derived process incarnation identity, not only its PID."""
    if pid is None or pid <= 0:
        return None
    stat_path = Path(f"/proc/{pid}/stat")
    try:
        raw = stat_path.read_text(encoding="utf-8")
        after_name = raw[raw.rfind(")") + 2 :].split()
        return f"proc:{after_name[19]}"
    except (OSError, IndexError):
        pass
    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    started = " ".join(result.stdout.split())
    return f"ps:{started}" if result.returncode == 0 and started else None


def current_process_start_identity() -> str:
    """Return one stable identity for this Python process.

    If the OS cannot expose a start time, the fallback is deliberately marked
    unverifiable so stale repair preserves a live PID instead of guessing.
    """
    global _CURRENT_PROCESS_START
    if _CURRENT_PROCESS_START is None:
        _CURRENT_PROCESS_START = (
            process_start_identity(os.getpid())
            or _UNVERIFIABLE_PROCESS_START
        )
    return _CURRENT_PROCESS_START


def repair_stale_backfills(
    now: datetime,
    timeout_seconds: int = 3600,
) -> dict:
    """Fail dead runs whose last heartbeat is at or before the timeout."""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = now.astimezone(timezone.utc) - timedelta(seconds=timeout_seconds)

    with get_session() as session:
        candidates = (
            session.query(BackfillRun)
            .filter(
                BackfillRun.status == "running",
                or_(
                    BackfillRun.heartbeat_at <= cutoff,
                    and_(
                        BackfillRun.heartbeat_at.is_(None),
                        BackfillRun.started_at <= cutoff,
                    ),
                ),
            )
            .order_by(BackfillRun.id.asc())
            .all()
        )
        candidate_facts = [
            (
                int(run.id),
                run.pid,
                run.owner_token,
                run.owner_host,
                run.owner_process_start,
                run.heartbeat_at,
                run.started_at,
            )
            for run in candidates
        ]

    repaired_ids: list[int] = []
    local_host = socket.gethostname()
    for (
        run_id,
        pid,
        owner_token,
        owner_host,
        owner_process_start,
        heartbeat_at,
        started_at,
    ) in candidate_facts:
        is_legacy_owner = owner_host is None and owner_process_start is None
        if is_legacy_owner:
            # Pre-identity rows came from the historical local-only collector.
            # A missing PID is sufficient; a live/reused PID remains fail-safe.
            if pid_is_alive(pid):
                continue
        else:
            # Partial identity and explicit remote owners cannot be proven dead
            # from this host.
            if (
                not owner_host
                or not owner_process_start
                or owner_host != local_host
            ):
                continue
            if pid_is_alive(pid):
                if owner_process_start.startswith("unverifiable:"):
                    continue
                current_process_start = process_start_identity(pid)
                if current_process_start is None:
                    continue
                if current_process_start == owner_process_start:
                    continue
        last_seen = heartbeat_at or started_at
        with get_session() as session:
            changed = (
                session.query(BackfillRun)
                .filter(
                    BackfillRun.id == run_id,
                    BackfillRun.status == "running",
                    BackfillRun.owner_token == owner_token,
                    BackfillRun.owner_host == owner_host,
                    BackfillRun.owner_process_start == owner_process_start,
                    (
                        BackfillRun.heartbeat_at == heartbeat_at
                        if heartbeat_at is not None
                        else BackfillRun.heartbeat_at.is_(None)
                    ),
                    BackfillRun.started_at == started_at,
                )
                .update(
                    {
                        "status": "stale_failed",
                        "error_msg": (
                            "owner process is not alive after heartbeat timeout; "
                            f"last_seen={_isoformat(last_seen)}"
                        ),
                        "finished_at": now,
                    },
                    synchronize_session=False,
                )
            )
            if changed == 1:
                repaired_ids.append(run_id)

    return {
        "repaired_count": len(repaired_ids),
        "repaired_ids": repaired_ids,
    }


def list_backfill_status(limit: int = 50) -> dict[str, Any]:
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    with get_session() as session:
        runs = (
            session.query(BackfillRun)
            .order_by(BackfillRun.id.desc())
            .limit(limit)
            .all()
        )
        rows = [
            {
                "id": int(run.id),
                "task_type": run.task_type,
                "year": run.year,
                "market": run.market,
                "status": run.status,
                "pid": run.pid,
                "params": _bounded_json(
                    run.params_json,
                    max_bytes=MAX_PARAMS_JSON_BYTES,
                ),
                "checkpoint": _bounded_json(
                    run.checkpoint_json,
                    max_bytes=MAX_CHECKPOINT_JSON_BYTES,
                ),
                "attempted": run.attempted_count or 0,
                "saved": run.saved_count or 0,
                "no_data": run.no_data_count or 0,
                "errors": run.error_count or 0,
                "summary": _bounded_json(
                    run.summary_json,
                    max_bytes=MAX_SUMMARY_JSON_BYTES,
                ),
                "error_message": (
                    run.error_msg[:MAX_ERROR_MESSAGE_CHARS]
                    if run.error_msg
                    else None
                ),
                "started_at": _isoformat(run.started_at),
                "heartbeat_at": _isoformat(run.heartbeat_at),
                "finished_at": _isoformat(run.finished_at),
            }
            for run in runs
        ]
    return {"count": len(rows), "runs": rows}


def classify_backfill_error(exc: BaseException) -> str:
    """Map failures to the public taxonomy without collapsing them to no_data."""
    if isinstance(exc, BackfillRunError):
        return exc.outcome
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
        return "interrupted"
    if "limit" in name or "quota" in name or "사용한도" in message:
        return "quota_exceeded"
    if isinstance(exc, (ConnectionError, TimeoutError)) or any(
        token in name or token in message
        for token in ("transport", "connection", "timeout", "network", "dns")
    ):
        return "transport_error"
    if any(token in name or token in message for token in ("parse", "decode", "xml", "json")):
        return "parse_error"
    return "storage_error"
