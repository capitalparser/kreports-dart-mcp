"""Fail-closed orchestration for a retained KAM schema backfill rehearsal."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import importlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
from typing import Literal


REHEARSAL_YEARS = (2021, 2022, 2023, 2024, 2025)
PHASES = (
    "source_preflight",
    "clone_created",
    "schema_migrated",
    "kam_dry_run_complete",
    "kam_rebuild_complete",
    "procedure_reconcile_complete",
    "idempotency_verified",
    "mcp_validation_complete",
    "live_immutability_verified",
)
MIN_FREE_BYTES = 10 * 1024**3
MAX_WORKER_OUTPUT_BYTES = 2 * 1024**2
MARKER_FILENAME = "kam-schema-backfill-rehearsal-marker.json"
_SEMANTIC_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_INTEGRITY_FIELDS = {
    "orphan_procedure_count",
    "cross_receipt_source_ordinal_link_count",
    "usable_response_without_procedure_count",
}
_CANONICAL_MCP_STATUSES = {"usable", "limited", "missing", "error"}
_EXPECTED_PROFESSIONAL_TOOLS = (
    "prepare_standard_audit_hours_inputs",
    "compare_peer_audit_fees",
    "build_audit_acceptance_pack",
    "compare_peer_risk_profile",
    "get_audit_history",
    "get_audit_report_sections",
    "search_audit_report_matters",
    "compare_peer_audit_report_matters",
    "get_kam_lifecycle",
    "compare_peer_kam_topics",
    "get_financial_snapshot",
    "compare_to_industry_multi",
    "get_investor_signals",
    "search_disclosure_events",
    "get_quality_of_earnings_pack",
    "get_dcf_input_candidates",
    "build_dcf_model_pack",
)
_MCP_ROW_FIELDS = {
    "tool",
    "status",
    "domain_verdict",
    "fact_count",
    "evidence_count",
    "pack_status",
    "table_ids",
    "source_count",
    "resource_checked",
    "first_answer_paragraph",
    "limitation_count",
}
_WORKER_TIMEOUT_SECONDS = {
    "migrate": 600,
    "kam-dry-run": 900,
    "kam-rebuild": 3600,
    "procedure-index": 3600,
    "semantic-snapshot": 600,
    "mcp-validate": 900,
}


@dataclass(frozen=True)
class WorkerInvocation:
    action: str
    runtime_mode: Literal["collector", "readonly"]
    year: int | None = None


class RehearsalRunError(RuntimeError):
    """Bounded operator error with a stable machine code and optional report."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        report_path: Path | None = None,
    ) -> None:
        self.code = code
        self.report_path = report_path
        super().__init__(message[:500])


def _decode_worker_payload(stdout: str) -> dict[str, object]:
    if not stdout.strip():
        raise RehearsalRunError(
            "worker_output_empty",
            "Worker produced no JSON result.",
        )
    decoder = json.JSONDecoder()
    try:
        payload, end = decoder.raw_decode(stdout.lstrip())
    except json.JSONDecodeError as exc:
        raise RehearsalRunError(
            "worker_output_invalid",
            "Worker output was not one JSON document.",
        ) from exc
    if stdout.lstrip()[end:].strip():
        raise RehearsalRunError(
            "worker_output_multiple",
            "Worker produced more than one JSON document.",
        )
    if not isinstance(payload, dict):
        raise RehearsalRunError(
            "worker_output_invalid",
            "Worker JSON result must be an object.",
        )
    if payload.get("ok") is not True:
        raise RehearsalRunError(
            "worker_reported_failure",
            "Worker reported a bounded rehearsal failure.",
        )
    return payload


def invoke_worker(
    *,
    python_executable: Path,
    database: Path,
    marker_path: Path,
    capability: str,
    invocation: WorkerInvocation,
) -> dict[str, object]:
    """Invoke one fresh worker with an explicit database and minimal env."""
    if (
        not marker_path.is_absolute()
        or marker_path.is_symlink()
        or not marker_path.is_file()
    ):
        raise RehearsalRunError(
            "rehearsal_marker_invalid",
            "Worker rehearsal marker must be an absolute regular file.",
        )
    try:
        capability_bytes = bytes.fromhex(capability)
    except ValueError as exc:
        raise RehearsalRunError(
            "rehearsal_capability_invalid",
            "Worker rehearsal capability must be a 32-byte hex value.",
        ) from exc
    if len(capability) != 64 or len(capability_bytes) != 32:
        raise RehearsalRunError(
            "rehearsal_capability_invalid",
            "Worker rehearsal capability must be a 32-byte hex value.",
        )
    command = [
        str(python_executable),
        "-m",
        "kreports.maintenance.kam_rehearsal_worker",
        invocation.action,
    ]
    if invocation.year is not None:
        command.extend(["--year", str(invocation.year)])
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
        "DB_URL": f"sqlite:///{database}",
        "KREPORTS_RUNTIME_MODE": invocation.runtime_mode,
        "KREPORTS_REHEARSAL_MARKER": str(marker_path),
        "KREPORTS_REHEARSAL_CAPABILITY": capability,
        "DART_API_KEY": "",
        "KREPORTS_HEADLESS": "1",
        "DART_HEADLESS": "1",
    }
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            env=environment,
            cwd=database.parent,
            timeout=_WORKER_TIMEOUT_SECONDS.get(invocation.action, 900),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RehearsalRunError(
            "worker_execution_failed",
            "Worker could not complete within its bounded execution window.",
        ) from exc
    serialized_output = f"{completed.stdout}\n{completed.stderr}".lower()
    if capability.lower() in serialized_output:
        raise RehearsalRunError(
            "worker_capability_disclosed",
            "Worker output disclosed its one-run rehearsal capability.",
        )
    if completed.returncode != 0:
        raise RehearsalRunError(
            "worker_exit_nonzero",
            "Worker exited unsuccessfully.",
        )
    if len(completed.stdout.encode("utf-8")) > MAX_WORKER_OUTPUT_BYTES:
        raise RehearsalRunError(
            "worker_output_too_large",
            "Worker JSON result exceeded the 2 MiB boundary.",
        )
    return _decode_worker_payload(completed.stdout)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime | None = None) -> str:
    current = value or _utc_now()
    return current.isoformat().replace("+00:00", "Z")


def _load_safety():
    """Load Task 1 only when a rehearsal is explicitly requested."""
    return importlib.import_module("kreports.maintenance.rehearsal_safety")


def _identity_payload(identity: object) -> dict[str, object]:
    try:
        allocated_size = (
            Path(getattr(identity, "path")).stat().st_blocks * 512
        )
    except (OSError, TypeError):
        allocated_size = None
    return {
        "size": int(getattr(identity, "size")),
        "allocated_size": allocated_size,
        "inode": int(getattr(identity, "inode")),
        "device": int(getattr(identity, "device")),
        "mtime_ns": int(getattr(identity, "mtime_ns")),
        "sha256": str(getattr(identity, "sha256")),
    }


def _bounded_evidence(value: object, *, depth: int = 0) -> object:
    if depth >= 4:
        return "bounded"
    if isinstance(value, dict):
        return {
            str(key)[:100]: _bounded_evidence(item, depth=depth + 1)
            for key, item in list(value.items())[:20]
        }
    if isinstance(value, (list, tuple)):
        return [
            _bounded_evidence(item, depth=depth + 1)
            for item in value[:20]
        ]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:500]


class _ReportWriter:
    def __init__(self, rehearsal_dir: Path) -> None:
        timestamp = _utc_now().strftime("%Y%m%dT%H%M%SZ")
        self.run_id = timestamp
        self.path = (
            rehearsal_dir
            / f"kam-schema-backfill-rehearsal-{timestamp}.json"
        )
        self._temporary_path = self.path.with_suffix(".json.tmp")
        self.markdown_path = self.path.with_suffix(".md")
        self._created = False
        if (
            self.path.exists()
            or self._temporary_path.exists()
            or self.markdown_path.exists()
        ):
            raise RehearsalRunError(
                "report_exists",
                "The exact rehearsal report target already exists.",
            )

    def write(self, report: dict[str, object]) -> None:
        serialized = json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        if not self._created:
            with self.path.open("x", encoding="utf-8") as handle:
                handle.write(serialized)
            self._created = True
            return
        with self._temporary_path.open("x", encoding="utf-8") as handle:
            handle.write(serialized)
        os.replace(self._temporary_path, self.path)


def _create_rehearsal_marker(
    *,
    run_id: str,
    source_identity: object,
    clone_identity: object,
    repository_root: Path,
    rehearsal_dir: Path,
    filesystem_type: str,
    min_free_bytes: int,
    capability: str,
) -> Path:
    database_path = Path(getattr(clone_identity, "path")).resolve()
    marker_path = database_path.parent / MARKER_FILENAME
    if not database_path.is_absolute() or marker_path.is_symlink():
        raise RehearsalRunError(
            "rehearsal_marker_invalid",
            "Rehearsal marker target is unsafe.",
        )
    marker = {
        "schema_version": "kam-schema-backfill-rehearsal-marker.v1",
        "run_id": run_id,
        "database_path": str(database_path),
        "database_inode": int(getattr(clone_identity, "inode")),
        "database_device": int(getattr(clone_identity, "device")),
        "source_sha256": str(getattr(source_identity, "sha256")),
        "clone_initial_sha256": str(getattr(clone_identity, "sha256")),
        "source_path": str(
            Path(getattr(source_identity, "path")).resolve(),
        ),
        "source_inode": int(getattr(source_identity, "inode")),
        "source_device": int(getattr(source_identity, "device")),
        "repository_root": str(repository_root.resolve()),
        "rehearsal_dir": str(rehearsal_dir.resolve()),
        "filesystem_type": filesystem_type,
        "min_free_bytes": min_free_bytes,
    }
    canonical_marker = json.dumps(
        marker,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    marker["hmac_sha256"] = hmac.new(
        bytes.fromhex(capability),
        canonical_marker,
        hashlib.sha256,
    ).hexdigest()
    with marker_path.open("x", encoding="utf-8") as handle:
        json.dump(
            marker,
            handle,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    if marker_path.is_symlink() or not marker_path.is_file():
        raise RehearsalRunError(
            "rehearsal_marker_invalid",
            "Rehearsal marker was not retained as a regular file.",
        )
    return marker_path.resolve()


def render_rehearsal_markdown(
    report: dict[str, object],
    *,
    redact_paths: bool = True,
) -> str:
    """Render a bounded operator handoff without raw phase evidence."""
    source = report.get("source")
    source_identity = source if isinstance(source, dict) else {}
    clone = report.get("clone")
    clone_identity = clone if isinstance(clone, dict) else {}
    clone_path = Path(str(report.get("clone_path") or "clone-unavailable"))
    clone_label = clone_path.name if redact_paths else str(clone_path)
    phases = report.get("phases")
    phase_rows = phases if isinstance(phases, list) else []
    lines = [
        "# KAM schema backfill rehearsal",
        "",
        f"Final status: `{report.get('status')}`",
        f"Last phase: `{report.get('last_phase') or '-'}`",
        (
            "Live SHA-256 unchanged: "
            f"`{str(bool(report.get('live_sha256_unchanged'))).lower()}`"
        ),
        f"Retained clone: `{clone_label}`",
        "",
        "## Identity and size",
        "",
        f"- Source logical bytes: `{source_identity.get('size', '-')}`",
        (
            "- Source allocated bytes: "
            f"`{source_identity.get('allocated_size', '-')}`"
        ),
        f"- Clone logical bytes: `{clone_identity.get('size', '-')}`",
        (
            "- Clone allocated bytes: "
            f"`{clone_identity.get('allocated_size', '-')}`"
        ),
        f"- Source SHA-256: `{source_identity.get('sha256', '-')}`",
        f"- Clone SHA-256: `{clone_identity.get('sha256', '-')}`",
        "",
        "## Phase summary",
        "",
        "| Phase | Status | Started | Finished |",
        "| --- | --- | --- | --- |",
    ]
    for phase in phase_rows[: len(PHASES)]:
        if not isinstance(phase, dict):
            continue
        lines.append(
            f"| {str(phase.get('name') or '-')[:80]} "
            f"| {str(phase.get('status') or '-')[:30]} "
            f"| {str(phase.get('started_at') or '-')[:40]} "
            f"| {str(phase.get('finished_at') or '-')[:40]} |"
        )
    lines.extend([
        "",
        "Retained clone cleanup is an explicit operator action.",
        "",
    ])
    return "\n".join(lines)


def _finalize_report(
    report: dict[str, object],
    writer: _ReportWriter,
) -> dict[str, object]:
    report["markdown_report_path"] = str(writer.markdown_path.resolve())
    markdown = render_rehearsal_markdown(report)
    with writer.markdown_path.open("x", encoding="utf-8") as handle:
        handle.write(markdown)
    writer.write(report)
    return report


def _classify_completed_mcp(payload: dict[str, object]) -> str:
    matrix = payload.get("matrix")
    rows = matrix if isinstance(matrix, list) else []
    statuses = {
        row.get("status")
        for row in rows
        if isinstance(row, dict)
    }
    if statuses & {"limited", "missing"}:
        return "data_quality_limited"
    if "error" in statuses:
        return "mcp_schema_closed"
    return "complete"


def _is_nonnegative_int(value: object) -> bool:
    return type(value) is int and value >= 0


def _valid_quality_distribution(
    value: object,
    *,
    expected_count: int,
) -> bool:
    if not isinstance(value, dict) or not value:
        return False
    total = 0
    for year, statuses in value.items():
        if not isinstance(year, str) or not year:
            return False
        if not isinstance(statuses, dict) or not statuses:
            return False
        for status, count in statuses.items():
            if (
                not isinstance(status, str)
                or not status
                or not _is_nonnegative_int(count)
                or int(count) == 0
            ):
                return False
            total += int(count)
    return total == expected_count


def _validate_semantic_snapshot(snapshot: dict[str, object]) -> None:
    digest = snapshot.get("semantic_sha256")
    kam_count = snapshot.get("kam_count")
    procedure_count = snapshot.get("procedure_count")
    integrity = snapshot.get("integrity")
    duplicates = snapshot.get("duplicate_logical_identities")
    if (
        not isinstance(digest, str)
        or not _SEMANTIC_SHA256.fullmatch(digest)
        or not _is_nonnegative_int(kam_count)
        or not _is_nonnegative_int(procedure_count)
        or int(kam_count) == 0
        or int(procedure_count) == 0
        or not _valid_quality_distribution(
            snapshot.get("kam_quality_by_year"),
            expected_count=int(kam_count),
        )
        or not _valid_quality_distribution(
            snapshot.get("procedure_quality_by_year"),
            expected_count=int(procedure_count),
        )
        or not isinstance(integrity, dict)
        or set(integrity) != _INTEGRITY_FIELDS
        or not all(
            _is_nonnegative_int(integrity[field])
            for field in _INTEGRITY_FIELDS
        )
        or not isinstance(duplicates, list)
    ):
        raise RehearsalRunError(
            "semantic_snapshot_invalid",
            "Semantic snapshot evidence is missing or malformed.",
        )
    if duplicates or any(
        int(integrity[field]) > 0
        for field in _INTEGRITY_FIELDS
    ):
        raise RehearsalRunError(
            "semantic_snapshot_blocked",
            "Semantic snapshot contains an integrity blocker.",
        )


def _validate_mcp_payload(payload: dict[str, object]) -> None:
    matrix = payload.get("matrix")
    if (
        payload.get("tool_count") != len(_EXPECTED_PROFESSIONAL_TOOLS)
        or payload.get("schema_error_closed") is not True
        or payload.get("all_boundary_parity") is not True
        or not isinstance(matrix, list)
        or len(matrix) != len(_EXPECTED_PROFESSIONAL_TOOLS)
    ):
        raise RehearsalRunError(
            "mcp_schema_not_closed",
            "Professional MCP schema and parity gates did not close.",
        )

    observed_tools: set[str] = set()
    for row in matrix:
        if not isinstance(row, dict) or set(row) != _MCP_ROW_FIELDS:
            raise RehearsalRunError(
                "mcp_schema_not_closed",
                "Professional MCP matrix row is malformed.",
            )
        tool = row.get("tool")
        status = row.get("status")
        pack_status = row.get("pack_status")
        table_ids = row.get("table_ids")
        if (
            not isinstance(tool, str)
            or tool not in _EXPECTED_PROFESSIONAL_TOOLS
            or tool in observed_tools
            or not isinstance(status, str)
            or status not in _CANONICAL_MCP_STATUSES
            or (
                pack_status is not None
                and (
                    not isinstance(pack_status, str)
                    or pack_status != status
                )
            )
            or type(row.get("resource_checked")) is not bool
            or not isinstance(row.get("first_answer_paragraph"), str)
            or not isinstance(table_ids, list)
            or not all(
                isinstance(table_id, str) and bool(table_id)
                for table_id in table_ids
            )
            or not all(
                _is_nonnegative_int(row.get(field))
                for field in (
                    "fact_count",
                    "evidence_count",
                    "source_count",
                    "limitation_count",
                )
            )
            or (
                status in {"limited", "missing"}
                and int(row["limitation_count"]) == 0
            )
        ):
            raise RehearsalRunError(
                "mcp_schema_not_closed",
                "Professional MCP matrix row failed its closure contract.",
            )
        observed_tools.add(tool)

    if observed_tools != set(_EXPECTED_PROFESSIONAL_TOOLS):
        raise RehearsalRunError(
            "mcp_schema_not_closed",
            "Professional MCP matrix does not cover the expected tools.",
        )


def _phase_record(
    name: str,
    status: str,
    started_at: str,
    evidence: object,
) -> dict[str, object]:
    return {
        "name": name,
        "status": status,
        "started_at": started_at,
        "finished_at": _timestamp(),
        "evidence": _bounded_evidence(evidence),
    }


def _failure_status(phase: str, error: BaseException) -> str:
    if getattr(error, "code", "") == "source_changed":
        return "live_digest_changed"
    if phase in {"source_preflight", "clone_created"}:
        return "preflight_blocked"
    if phase == "schema_migrated":
        return "migration_failed"
    if phase == "live_immutability_verified":
        return "live_digest_changed"
    return "backfill_failed"


def _snapshot_integrity(snapshot: dict[str, object]) -> dict[str, object]:
    return {
        key: snapshot.get(key)
        for key in (
            "kam_count",
            "procedure_count",
            "kam_quality_by_year",
            "procedure_quality_by_year",
            "duplicate_logical_identities",
            "integrity",
        )
    }


def run_kam_schema_backfill_rehearsal(
    *,
    source_db: Path,
    rehearsal_dir: Path,
    repository_root: Path,
    python_executable: Path,
    min_free_bytes: int = MIN_FREE_BYTES,
) -> dict[str, object]:
    """Run the exact retained-clone rehearsal state machine."""
    if min_free_bytes < MIN_FREE_BYTES:
        raise RehearsalRunError(
            "min_free_bytes_below_floor",
            "Rehearsal free-space floor cannot be lower than 10 GiB.",
        )
    report: dict[str, object] = {
        "schema_version": "kam-schema-backfill-rehearsal.v1",
        "status": "running",
        "last_phase": None,
        "started_at": _timestamp(),
        "finished_at": None,
        "source": None,
        "clone": None,
        "clone_path": None,
        "report_path": "",
        "markdown_report_path": "",
        "marker_path": None,
        "phases": [],
    }
    safety = None
    expected_source = None
    preflight_result = None
    clone_path = rehearsal_dir / "kreports-rehearsal.db"
    marker_path: Path | None = None

    def preflight_operation() -> dict[str, object]:
        nonlocal safety, expected_source, preflight_result
        safety = _load_safety()
        preflight_result = safety.preflight_rehearsal(
            source_db,
            rehearsal_dir,
            repository_root=repository_root,
            min_free_bytes=min_free_bytes,
        )
        expected_source = preflight_result.source
        report["source"] = _identity_payload(preflight_result.source)
        return {
            "free_bytes": preflight_result.free_bytes,
            "filesystem_type": preflight_result.filesystem_type,
            "source": _identity_payload(preflight_result.source),
        }

    preflight_started_at = _timestamp()
    report["last_phase"] = "source_preflight"
    try:
        preflight_evidence = preflight_operation()
    except Exception as exc:
        report["phases"].append(_phase_record(
            "source_preflight",
            "failed",
            preflight_started_at,
            {"error_code": getattr(exc, "code", "unexpected_failure")},
        ))
        report["status"] = _failure_status("source_preflight", exc)
        report["finished_at"] = _timestamp()
        return report

    report["phases"].append(_phase_record(
        "source_preflight",
        "complete",
        preflight_started_at,
        preflight_evidence,
    ))
    writer = _ReportWriter(rehearsal_dir)
    report["report_path"] = str(writer.path.resolve())
    capability = secrets.token_hex(32)
    writer.write(report)

    def persist_phase(
        name: str,
        operation,
    ) -> object | None:
        started_at = _timestamp()
        report["last_phase"] = name
        try:
            evidence = operation()
        except Exception as exc:
            report["phases"].append(_phase_record(
                name,
                "failed",
                started_at,
                {"error_code": getattr(exc, "code", "unexpected_failure")},
            ))
            report["status"] = _failure_status(name, exc)
            report["finished_at"] = _timestamp()
            writer.write(report)
            return None
        report["phases"].append(_phase_record(
            name,
            "complete",
            started_at,
            evidence,
        ))
        writer.write(report)
        return evidence

    def clone_operation() -> dict[str, object]:
        nonlocal clone_path, marker_path
        clone_identity = safety.create_apfs_clone(preflight_result)
        clone_path = Path(clone_identity.path).resolve()
        report["clone"] = _identity_payload(clone_identity)
        report["clone_path"] = str(clone_path)
        marker_path = _create_rehearsal_marker(
            run_id=writer.run_id,
            source_identity=expected_source,
            clone_identity=clone_identity,
            repository_root=repository_root,
            rehearsal_dir=preflight_result.rehearsal_dir,
            filesystem_type=preflight_result.filesystem_type,
            min_free_bytes=MIN_FREE_BYTES,
            capability=capability,
        )
        report["marker_path"] = str(marker_path)
        safety.assert_source_unchanged(expected_source)
        return {
            "clone": _identity_payload(clone_identity),
            "marker": marker_path.name,
        }

    if persist_phase("clone_created", clone_operation) is None:
        return _finalize_report(report, writer)

    def run_worker(invocation: WorkerInvocation) -> dict[str, object]:
        if marker_path is None:
            raise RehearsalRunError(
                "rehearsal_marker_missing",
                "Worker cannot start before the clone receipt is retained.",
            )
        return invoke_worker(
            python_executable=python_executable,
            database=clone_path,
            marker_path=marker_path,
            capability=capability,
            invocation=invocation,
        )

    def migration_operation() -> dict[str, object]:
        safety.assert_free_space(
            rehearsal_dir,
            min_free_bytes=min_free_bytes,
        )
        payload = run_worker(WorkerInvocation("migrate", "collector"))
        safety.assert_source_unchanged(expected_source)
        return payload

    if persist_phase("schema_migrated", migration_operation) is None:
        return _finalize_report(report, writer)

    def year_loop(
        action: str,
        runtime_mode: Literal["collector", "readonly"],
    ) -> list[dict[str, object]]:
        return [
            run_worker(WorkerInvocation(action, runtime_mode, year))
            for year in REHEARSAL_YEARS
        ]

    if persist_phase(
        "kam_dry_run_complete",
        lambda: {"years": year_loop("kam-dry-run", "collector")},
    ) is None:
        return _finalize_report(report, writer)

    snapshot_before: dict[str, object] = {}

    def first_rebuild_operation() -> dict[str, object]:
        nonlocal snapshot_before
        snapshot_before = run_worker(
            WorkerInvocation("semantic-snapshot", "readonly"),
        )
        _validate_semantic_snapshot(snapshot_before)
        safety.assert_free_space(
            rehearsal_dir,
            min_free_bytes=min_free_bytes,
        )
        years = year_loop("kam-rebuild", "collector")
        safety.assert_source_unchanged(expected_source)
        return {"snapshot_before": snapshot_before, "years": years}

    if persist_phase(
        "kam_rebuild_complete",
        first_rebuild_operation,
    ) is None:
        return _finalize_report(report, writer)

    snapshot_after_first: dict[str, object] = {}

    def first_procedure_operation() -> dict[str, object]:
        nonlocal snapshot_after_first
        safety.assert_free_space(
            rehearsal_dir,
            min_free_bytes=min_free_bytes,
        )
        years = year_loop("procedure-index", "collector")
        snapshot_after_first = run_worker(
            WorkerInvocation("semantic-snapshot", "readonly"),
        )
        _validate_semantic_snapshot(snapshot_after_first)
        return {"years": years, "snapshot_after": snapshot_after_first}

    if persist_phase(
        "procedure_reconcile_complete",
        first_procedure_operation,
    ) is None:
        return _finalize_report(report, writer)

    def idempotency_operation() -> dict[str, object]:
        safety.assert_free_space(
            rehearsal_dir,
            min_free_bytes=min_free_bytes,
        )
        rebuild = year_loop("kam-rebuild", "collector")
        safety.assert_source_unchanged(expected_source)
        safety.assert_free_space(
            rehearsal_dir,
            min_free_bytes=min_free_bytes,
        )
        procedures = year_loop("procedure-index", "collector")
        snapshot_after_second = run_worker(
            WorkerInvocation("semantic-snapshot", "readonly"),
        )
        _validate_semantic_snapshot(snapshot_after_second)
        if (
            snapshot_after_first.get("semantic_sha256")
            != snapshot_after_second.get("semantic_sha256")
            or _snapshot_integrity(snapshot_after_first)
            != _snapshot_integrity(snapshot_after_second)
        ):
            raise RehearsalRunError(
                "idempotency_mismatch",
                "Semantic snapshot changed during the second pass.",
            )
        return {
            "rebuild": rebuild,
            "procedures": procedures,
            "semantic_sha256": snapshot_after_second.get(
                "semantic_sha256",
            ),
            "integrity": _snapshot_integrity(snapshot_after_second),
        }

    if persist_phase(
        "idempotency_verified",
        idempotency_operation,
    ) is None:
        return _finalize_report(report, writer)

    mcp_payload: dict[str, object] = {}

    def mcp_operation() -> dict[str, object]:
        nonlocal mcp_payload
        mcp_payload = run_worker(
            WorkerInvocation("mcp-validate", "readonly"),
        )
        _validate_mcp_payload(mcp_payload)
        safety.assert_source_unchanged(expected_source)
        return mcp_payload

    if persist_phase("mcp_validation_complete", mcp_operation) is None:
        return _finalize_report(report, writer)

    if persist_phase(
        "live_immutability_verified",
        lambda: {
            "source": _identity_payload(
                safety.assert_source_unchanged(expected_source),
            ),
        },
    ) is None:
        return _finalize_report(report, writer)

    report["status"] = _classify_completed_mcp(mcp_payload)
    report["finished_at"] = _timestamp()
    report["live_sha256_unchanged"] = True
    return _finalize_report(report, writer)
