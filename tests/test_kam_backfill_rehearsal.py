from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


_TEST_CAPABILITY = "ab" * 32


def _install_fake_worker(tmp_path: Path, body: str) -> None:
    package = tmp_path / "kreports" / "maintenance"
    package.mkdir(parents=True)
    (tmp_path / "kreports" / "__init__.py").write_text("", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "kam_rehearsal_worker.py").write_text(body, encoding="utf-8")
    (tmp_path / "kam-schema-backfill-rehearsal-marker.json").write_text(
        "{}",
        encoding="utf-8",
    )


def test_invoke_worker_uses_minimal_explicit_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kreports.maintenance.kam_backfill_rehearsal import (
        WorkerInvocation,
        invoke_worker,
    )

    _install_fake_worker(
        tmp_path,
        """
import json
import os
print(json.dumps({
    "ok": True,
        "capability_matches": os.environ.get(
            "KREPORTS_REHEARSAL_CAPABILITY"
        ) == "ab" * 32,
        "observed_env": {
            "DART_API_KEY": os.environ.get("DART_API_KEY"),
            "KREPORTS_RUNTIME_MODE": os.environ.get("KREPORTS_RUNTIME_MODE"),
            "DB_URL": os.environ.get("DB_URL"),
            "KREPORTS_REHEARSAL_MARKER": os.environ.get(
                "KREPORTS_REHEARSAL_MARKER"
            ),
        },
}))
""".strip(),
    )
    monkeypatch.setenv("DART_API_KEY", "must-not-propagate")
    monkeypatch.setenv("DB_URL", "sqlite:////wrong/live.db")
    payload = invoke_worker(
        python_executable=Path(sys.executable),
        database=tmp_path / "clone.db",
        marker_path=(
            tmp_path / "kam-schema-backfill-rehearsal-marker.json"
        ),
        capability=_TEST_CAPABILITY,
        invocation=WorkerInvocation("migrate", "collector"),
    )
    assert payload["capability_matches"] is True
    assert payload["observed_env"] == {
        "DART_API_KEY": "",
        "KREPORTS_RUNTIME_MODE": "collector",
        "DB_URL": f"sqlite:///{tmp_path / 'clone.db'}",
        "KREPORTS_REHEARSAL_MARKER": str(
            tmp_path / "kam-schema-backfill-rehearsal-marker.json",
        ),
    }
    assert _TEST_CAPABILITY not in json.dumps(payload, sort_keys=True)


@pytest.mark.parametrize(
    ("body", "expected_code"),
    [
        ("raise SystemExit(7)", "worker_exit_nonzero"),
        ("", "worker_output_empty"),
        (
            'print(\'{"ok": true}\')\nprint(\'{"ok": true}\')',
            "worker_output_multiple",
        ),
        (
            'import json\nprint(json.dumps({"ok": False, "error": "raw sql"}))',
            "worker_reported_failure",
        ),
        (
            'import json\nprint(json.dumps({"ok": True, "value": "x" * 2097152}))',
            "worker_output_too_large",
        ),
    ],
)
def test_invoke_worker_rejects_failed_or_malformed_child_output(
    tmp_path: Path,
    body: str,
    expected_code: str,
) -> None:
    from kreports.maintenance.kam_backfill_rehearsal import (
        RehearsalRunError,
        WorkerInvocation,
        invoke_worker,
    )

    _install_fake_worker(tmp_path, body)

    with pytest.raises(RehearsalRunError) as caught:
        invoke_worker(
            python_executable=Path(sys.executable),
            database=tmp_path / "clone.db",
            marker_path=(
                tmp_path / "kam-schema-backfill-rehearsal-marker.json"
            ),
            capability=_TEST_CAPABILITY,
            invocation=WorkerInvocation("migrate", "collector"),
        )

    assert caught.value.code == expected_code
    assert str(tmp_path / "clone.db") not in str(caught.value)
    assert _TEST_CAPABILITY not in str(caught.value)


@pytest.mark.parametrize(
    "body",
    [
        """
import json
import os
print(json.dumps({
    "ok": True,
    "capability": os.environ["KREPORTS_REHEARSAL_CAPABILITY"],
}))
""".strip(),
        """
import json
import os
import sys
print(os.environ["KREPORTS_REHEARSAL_CAPABILITY"], file=sys.stderr)
print(json.dumps({"ok": True}))
""".strip(),
    ],
)
def test_invoke_worker_rejects_capability_in_child_output(
    tmp_path: Path,
    body: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from kreports.maintenance.kam_backfill_rehearsal import (
        RehearsalRunError,
        WorkerInvocation,
        invoke_worker,
    )

    _install_fake_worker(tmp_path, body)

    with pytest.raises(RehearsalRunError) as caught:
        invoke_worker(
            python_executable=Path(sys.executable),
            database=tmp_path / "clone.db",
            marker_path=(
                tmp_path / "kam-schema-backfill-rehearsal-marker.json"
            ),
            capability=_TEST_CAPABILITY,
            invocation=WorkerInvocation("migrate", "collector"),
        )

    assert caught.value.code == "worker_capability_disclosed"
    captured = capsys.readouterr()
    assert _TEST_CAPABILITY not in (
        str(caught.value) + captured.out + captured.err
    )


def test_invoke_worker_adds_year_only_for_year_scoped_actions(
    tmp_path: Path,
) -> None:
    from kreports.maintenance.kam_backfill_rehearsal import (
        WorkerInvocation,
        invoke_worker,
    )

    _install_fake_worker(
        tmp_path,
        """
import json
import sys
print(json.dumps({"ok": True, "argv": sys.argv[1:]}))
""".strip(),
    )

    scoped = invoke_worker(
        python_executable=Path(sys.executable),
        database=tmp_path / "clone.db",
        marker_path=(
            tmp_path / "kam-schema-backfill-rehearsal-marker.json"
        ),
        capability=_TEST_CAPABILITY,
        invocation=WorkerInvocation("kam-rebuild", "collector", 2023),
    )
    unscoped = invoke_worker(
        python_executable=Path(sys.executable),
        database=tmp_path / "clone.db",
        marker_path=(
            tmp_path / "kam-schema-backfill-rehearsal-marker.json"
        ),
        capability=_TEST_CAPABILITY,
        invocation=WorkerInvocation("migrate", "collector"),
    )

    assert scoped["argv"] == ["kam-rebuild", "--year", "2023"]
    assert unscoped["argv"] == ["migrate"]


def _install_phase_harness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail_action: str | None = None,
    fail_year: int | None = None,
    mcp_payload: dict[str, object] | None = None,
    snapshot_drift: bool = False,
    expected_capability: str | None = None,
) -> tuple[Path, Path, Path, list[tuple[object, ...]]]:
    from kreports.maintenance import kam_backfill_rehearsal as rehearsal

    source = tmp_path / "live.db"
    source.write_bytes(b"source-database")
    rehearsal_dir = tmp_path / "rehearsal"
    rehearsal_dir.mkdir()
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    calls: list[tuple[object, ...]] = []

    def identity(path: Path) -> SimpleNamespace:
        return SimpleNamespace(
            path=path,
            size=path.stat().st_size,
            inode=1,
            device=2,
            mtime_ns=3,
            sha256="a" * 64,
        )

    def preflight(
        source_db: Path,
        target_dir: Path,
        *,
        repository_root: Path,
        min_free_bytes: int,
    ) -> SimpleNamespace:
        calls.append(("preflight", source_db, target_dir, min_free_bytes))
        return SimpleNamespace(
            source=identity(source_db),
            rehearsal_dir=target_dir,
            repository_root=repository_root,
            free_bytes=20 * 1024**3,
            filesystem_type="apfs",
            enforced_min_free_bytes=min_free_bytes,
        )

    def assert_space(path: Path, *, min_free_bytes: int) -> int:
        calls.append(("free-space", min_free_bytes))
        return 20 * 1024**3

    def clone(preflight_result: SimpleNamespace) -> SimpleNamespace:
        clone_path = preflight_result.rehearsal_dir / "kreports-rehearsal.db"
        if clone_path.exists():
            raise SimpleSafetyError("target_exists", "clone exists")
        clone_path.write_bytes(b"clone-database")
        calls.append(("clone", clone_path.name))
        return identity(clone_path)

    def unchanged(expected: SimpleNamespace) -> SimpleNamespace:
        calls.append(("source-unchanged", expected.sha256))
        return expected

    class SimpleSafetyError(RuntimeError):
        def __init__(self, code: str, message: str) -> None:
            self.code = code
            super().__init__(message)

    safety = SimpleNamespace(
        MIN_FREE_BYTES=10 * 1024**3,
        RehearsalSafetyError=SimpleSafetyError,
        preflight_rehearsal=preflight,
        assert_free_space=assert_space,
        create_apfs_clone=clone,
        assert_source_unchanged=unchanged,
    )
    monkeypatch.setattr(rehearsal, "_load_safety", lambda: safety)

    snapshot_index = 0

    def worker(
        *,
        python_executable: Path,
        database: Path,
        marker_path: Path,
        capability: str,
        invocation: object,
    ) -> dict[str, object]:
        nonlocal snapshot_index
        calls.append((
            "worker",
            invocation.action,
            invocation.year,
            marker_path.name,
            capability == expected_capability
            if expected_capability is not None
            else len(capability) == 64,
        ))
        if invocation.action == fail_action and invocation.year == fail_year:
            raise rehearsal.RehearsalRunError(
                "worker_reported_failure",
                "bounded worker failure",
            )
        if invocation.action == "semantic-snapshot":
            snapshot_index += 1
            return {
                "ok": True,
                "semantic_sha256": (
                    "c" * 64
                    if snapshot_drift and snapshot_index == 3
                    else "b" * 64
                ),
                "counts": {"kam_items": 10, "audit_procedure_items": 8},
                "year_quality": {"2025": {"usable": 2}},
                "integrity": {
                    "duplicate_identity_count": 0,
                    "orphan_procedure_count": 0,
                },
                "snapshot_index": snapshot_index,
            }
        if invocation.action == "mcp-validate":
            return mcp_payload or {
                "ok": True,
                "tool_count": 17,
                "schema_error_closed": True,
                "all_boundary_parity": True,
                "matrix": [
                    {"tool": f"professional_tool_{index}", "status": "usable"}
                    for index in range(17)
                ],
            }
        return {"ok": True, "action": invocation.action, "year": invocation.year}

    monkeypatch.setattr(rehearsal, "invoke_worker", worker)
    return source, rehearsal_dir, repository_root, calls


def test_rehearsal_runs_exact_phases_and_ascending_year_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kreports.maintenance.kam_backfill_rehearsal import (
        PHASES,
        REHEARSAL_YEARS,
        run_kam_schema_backfill_rehearsal,
    )

    source, rehearsal_dir, repository_root, calls = _install_phase_harness(
        tmp_path,
        monkeypatch,
    )
    report = run_kam_schema_backfill_rehearsal(
        source_db=source,
        rehearsal_dir=rehearsal_dir,
        repository_root=repository_root,
        python_executable=Path(sys.executable),
        min_free_bytes=10 * 1024**3,
    )

    assert report["status"] == "complete"
    assert [phase["name"] for phase in report["phases"]] == list(PHASES)
    worker_calls = [
        (call[1], call[2])
        for call in calls
        if call[0] == "worker"
    ]
    assert [
        year for action, year in worker_calls if action == "kam-dry-run"
    ] == list(REHEARSAL_YEARS)
    assert [
        year for action, year in worker_calls if action == "kam-rebuild"
    ] == [*REHEARSAL_YEARS, *REHEARSAL_YEARS]
    assert [
        year for action, year in worker_calls if action == "procedure-index"
    ] == [*REHEARSAL_YEARS, *REHEARSAL_YEARS]
    assert sum(call[0] == "free-space" for call in calls) == 5
    assert sum(call[0] == "source-unchanged" for call in calls) == 6
    assert Path(report["report_path"]).exists()
    persisted = __import__("json").loads(
        Path(report["report_path"]).read_text(encoding="utf-8"),
    )
    assert persisted["phases"] == report["phases"]


def test_rehearsal_creates_bound_marker_after_clone_before_workers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from kreports.maintenance import kam_backfill_rehearsal as rehearsal

    capability = "cd" * 32
    token_hex_calls: list[int] = []

    def token_hex(byte_count: int) -> str:
        token_hex_calls.append(byte_count)
        return capability

    monkeypatch.setattr(rehearsal.secrets, "token_hex", token_hex)
    source, rehearsal_dir, repository_root, calls = _install_phase_harness(
        tmp_path,
        monkeypatch,
        expected_capability=capability,
    )
    report = rehearsal.run_kam_schema_backfill_rehearsal(
        source_db=source,
        rehearsal_dir=rehearsal_dir,
        repository_root=repository_root,
        python_executable=Path(sys.executable),
        min_free_bytes=10 * 1024**3,
    )

    marker_path = Path(report["marker_path"])
    marker = __import__("json").loads(
        marker_path.read_text(encoding="utf-8"),
    )
    assert marker_path.is_absolute()
    assert marker_path.is_file()
    assert not marker_path.is_symlink()
    assert set(marker) == {
        "schema_version",
        "run_id",
        "database_path",
        "database_inode",
        "database_device",
        "source_sha256",
        "clone_initial_sha256",
        "source_path",
        "source_inode",
        "source_device",
        "repository_root",
        "rehearsal_dir",
        "filesystem_type",
        "min_free_bytes",
        "hmac_sha256",
    }
    assert marker["schema_version"] == (
        "kam-schema-backfill-rehearsal-marker.v1"
    )
    assert marker["database_path"] == str(
        (rehearsal_dir / "kreports-rehearsal.db").resolve(),
    )
    assert marker["database_inode"] == 1
    assert marker["database_device"] == 2
    assert marker["source_sha256"] == "a" * 64
    assert marker["clone_initial_sha256"] == "a" * 64
    assert marker["source_path"] == str(source.resolve())
    assert marker["source_inode"] == 1
    assert marker["source_device"] == 2
    assert marker["repository_root"] == str(repository_root.resolve())
    assert marker["rehearsal_dir"] == str(rehearsal_dir.resolve())
    assert marker["filesystem_type"] == "apfs"
    assert marker["min_free_bytes"] == 10 * 1024**3
    signature = marker.pop("hmac_sha256")
    canonical = json.dumps(
        marker,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    expected_signature = hmac.new(
        bytes.fromhex(capability),
        canonical,
        hashlib.sha256,
    ).hexdigest()
    assert hmac.compare_digest(signature, expected_signature)
    assert token_hex_calls == [32]
    worker_calls = [call for call in calls if call[0] == "worker"]
    assert worker_calls
    assert all(call[3:] == (
        "kam-schema-backfill-rehearsal-marker.json",
        True,
    ) for call in worker_calls)
    captured = capsys.readouterr()
    persisted_text = (
        marker_path.read_text(encoding="utf-8")
        + Path(report["report_path"]).read_text(encoding="utf-8")
        + Path(report["report_path"]).with_suffix(".md").read_text(
            encoding="utf-8",
        )
        + json.dumps(report, sort_keys=True)
        + captured.out
        + captured.err
    )
    assert capability not in persisted_text
    assert calls.index(("clone", "kreports-rehearsal.db")) < next(
        index
        for index, call in enumerate(calls)
        if call[0] == "worker"
    )


def test_rehearsal_stops_before_next_year_after_backfill_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kreports.maintenance.kam_backfill_rehearsal import (
        run_kam_schema_backfill_rehearsal,
    )

    source, rehearsal_dir, repository_root, calls = _install_phase_harness(
        tmp_path,
        monkeypatch,
        fail_action="kam-rebuild",
        fail_year=2023,
    )
    report = run_kam_schema_backfill_rehearsal(
        source_db=source,
        rehearsal_dir=rehearsal_dir,
        repository_root=repository_root,
        python_executable=Path(sys.executable),
        min_free_bytes=10 * 1024**3,
    )
    invocations = {
        (call[1], call[2])
        for call in calls
        if call[0] == "worker"
    }

    assert report["status"] == "backfill_failed"
    assert report["last_phase"] == "kam_rebuild_complete"
    assert ("kam-rebuild", 2024) not in invocations
    assert ("procedure-index", 2021) not in invocations
    assert Path(report["report_path"]).exists()


def test_rehearsal_fails_closed_when_second_pass_digest_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kreports.maintenance.kam_backfill_rehearsal import (
        run_kam_schema_backfill_rehearsal,
    )

    source, rehearsal_dir, repository_root, _ = _install_phase_harness(
        tmp_path,
        monkeypatch,
        snapshot_drift=True,
    )
    report = run_kam_schema_backfill_rehearsal(
        source_db=source,
        rehearsal_dir=rehearsal_dir,
        repository_root=repository_root,
        python_executable=Path(sys.executable),
        min_free_bytes=10 * 1024**3,
    )

    assert report["status"] == "backfill_failed"
    assert report["last_phase"] == "idempotency_verified"
    assert report["phases"][-1]["status"] == "failed"


def test_rehearsal_rejects_existing_clone_and_exact_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import datetime, timezone

    from kreports.maintenance import kam_backfill_rehearsal as rehearsal

    source, rehearsal_dir, repository_root, _ = _install_phase_harness(
        tmp_path,
        monkeypatch,
    )
    (rehearsal_dir / "kreports-rehearsal.db").write_bytes(b"existing")
    report = rehearsal.run_kam_schema_backfill_rehearsal(
        source_db=source,
        rehearsal_dir=rehearsal_dir,
        repository_root=repository_root,
        python_executable=Path(sys.executable),
        min_free_bytes=10 * 1024**3,
    )
    assert report["status"] == "preflight_blocked"

    other_dir = tmp_path / "other-rehearsal"
    other_dir.mkdir()
    fixed = datetime(2026, 7, 29, 1, 2, 3, tzinfo=timezone.utc)
    monkeypatch.setattr(rehearsal, "_utc_now", lambda: fixed)
    existing = (
        other_dir / "kam-schema-backfill-rehearsal-20260729T010203Z.json"
    )
    existing.write_text("{}", encoding="utf-8")
    with pytest.raises(rehearsal.RehearsalRunError) as caught:
        rehearsal.run_kam_schema_backfill_rehearsal(
            source_db=source,
            rehearsal_dir=other_dir,
            repository_root=repository_root,
            python_executable=Path(sys.executable),
            min_free_bytes=10 * 1024**3,
        )
    assert caught.value.code == "report_exists"


def test_rehearsal_rejects_free_space_floor_override(
    tmp_path: Path,
) -> None:
    from kreports.maintenance.kam_backfill_rehearsal import (
        RehearsalRunError,
        run_kam_schema_backfill_rehearsal,
    )

    source = tmp_path / "live.db"
    source.write_bytes(b"source")
    rehearsal_dir = tmp_path / "rehearsal"
    rehearsal_dir.mkdir()
    repository_root = tmp_path / "repository"
    repository_root.mkdir()

    with pytest.raises(RehearsalRunError) as caught:
        run_kam_schema_backfill_rehearsal(
            source_db=source,
            rehearsal_dir=rehearsal_dir,
            repository_root=repository_root,
            python_executable=Path(sys.executable),
            min_free_bytes=1,
        )

    assert caught.value.code == "min_free_bytes_below_floor"
    assert not list(rehearsal_dir.iterdir())


def _terminal_report_fixture(status: str) -> dict[str, object]:
    return {
        "schema_version": "kam-schema-backfill-rehearsal.v1",
        "status": status,
        "last_phase": "live_immutability_verified",
        "started_at": "2026-07-29T01:00:00Z",
        "finished_at": "2026-07-29T02:00:00Z",
        "source": {
            "size": 100,
            "allocated_size": 128,
            "inode": 1,
            "device": 2,
            "mtime_ns": 3,
            "sha256": "a" * 64,
        },
        "clone": {
            "size": 90,
            "allocated_size": 128,
            "inode": 4,
            "device": 2,
            "mtime_ns": 5,
            "sha256": "b" * 64,
        },
        "clone_path": "/private/operator/rehearsal/kreports-rehearsal.db",
        "report_path": (
            "/private/operator/rehearsal/"
            "kam-schema-backfill-rehearsal-20260729T010000Z.json"
        ),
        "live_sha256_unchanged": status != "live_digest_changed",
        "phases": [{
            "name": "mcp_validation_complete",
            "status": "complete",
            "started_at": "2026-07-29T01:30:00Z",
            "finished_at": "2026-07-29T01:31:00Z",
            "evidence": {
                "DART_API_KEY": "must-not-render",
                "sql_error": "OperationalError: SELECT secret",
                "receipts": [f"2025{index:010d}" for index in range(30)],
            },
        }],
    }


@pytest.mark.parametrize(
    "status",
    [
        "preflight_blocked",
        "migration_failed",
        "backfill_failed",
        "data_quality_limited",
        "mcp_schema_closed",
        "live_digest_changed",
        "complete",
    ],
)
def test_markdown_names_terminal_status_and_live_digest_result(
    status: str,
) -> None:
    from kreports.maintenance.kam_backfill_rehearsal import (
        render_rehearsal_markdown,
    )

    markdown = render_rehearsal_markdown(
        _terminal_report_fixture(status),
    )

    assert f"Final status: `{status}`" in markdown
    assert "Live SHA-256 unchanged:" in markdown
    assert "kreports-rehearsal.db" in markdown
    assert "Source allocated bytes: `128`" in markdown
    assert "Clone allocated bytes: `128`" in markdown


def test_markdown_redacts_paths_secrets_raw_errors_and_receipt_arrays() -> None:
    from kreports.maintenance.kam_backfill_rehearsal import (
        render_rehearsal_markdown,
    )

    markdown = render_rehearsal_markdown(
        _terminal_report_fixture("backfill_failed"),
    )

    for forbidden in (
        "/private/operator",
        "must-not-render",
        "OperationalError",
        "SELECT secret",
        "20250000000000",
    ):
        assert forbidden not in markdown
    assert "Retained clone cleanup is an explicit operator action." in markdown


@pytest.mark.parametrize(
    ("matrix", "expected_status"),
    [
        (
            [{
                "tool": "get_kam_lifecycle",
                "status": "limited",
                "limitation_count": 1,
            }],
            "data_quality_limited",
        ),
        (
            [{
                "tool": "get_kam_lifecycle",
                "status": "limited",
                "limitation_count": 0,
            }],
            "mcp_schema_closed",
        ),
    ],
)
def test_terminal_status_classifies_mcp_evidence_limitations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    matrix: list[dict[str, object]],
    expected_status: str,
) -> None:
    from kreports.maintenance.kam_backfill_rehearsal import (
        run_kam_schema_backfill_rehearsal,
    )

    source, rehearsal_dir, repository_root, _ = _install_phase_harness(
        tmp_path,
        monkeypatch,
        mcp_payload={
            "ok": True,
            "tool_count": 17,
            "schema_error_closed": True,
            "all_boundary_parity": True,
            "matrix": [
                *matrix,
                *[
                    {
                        "tool": f"professional_tool_{index}",
                        "status": "usable",
                        "limitation_count": 0,
                    }
                    for index in range(16)
                ],
            ],
        },
    )
    report = run_kam_schema_backfill_rehearsal(
        source_db=source,
        rehearsal_dir=rehearsal_dir,
        repository_root=repository_root,
        python_executable=Path(sys.executable),
        min_free_bytes=10 * 1024**3,
    )

    assert report["status"] == expected_status
    assert Path(report["markdown_report_path"]).exists()


def test_rehearsal_rejects_unclosed_mcp_schema_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kreports.maintenance.kam_backfill_rehearsal import (
        run_kam_schema_backfill_rehearsal,
    )

    source, rehearsal_dir, repository_root, _ = _install_phase_harness(
        tmp_path,
        monkeypatch,
        mcp_payload={
            "ok": True,
            "tool_count": 17,
            "schema_error_closed": False,
            "all_boundary_parity": True,
            "matrix": [],
        },
    )
    report = run_kam_schema_backfill_rehearsal(
        source_db=source,
        rehearsal_dir=rehearsal_dir,
        repository_root=repository_root,
        python_executable=Path(sys.executable),
        min_free_bytes=10 * 1024**3,
    )

    assert report["status"] == "backfill_failed"
    assert report["last_phase"] == "mcp_validation_complete"
    assert report["phases"][-1]["status"] == "failed"


@pytest.mark.parametrize(
    "status",
    ["complete", "mcp_schema_closed", "data_quality_limited"],
)
def test_cli_prints_retained_artifacts_for_successful_outcomes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    from typer.testing import CliRunner

    from kreports.cli.main import app
    from kreports.maintenance import kam_backfill_rehearsal as rehearsal

    source = tmp_path / "live.db"
    source.write_bytes(b"source")
    rehearsal_dir = tmp_path / "rehearsal"
    rehearsal_dir.mkdir()
    clone = rehearsal_dir / "kreports-rehearsal.db"
    clone.write_bytes(b"clone")
    json_report = rehearsal_dir / "result.json"
    json_report.write_text("{}", encoding="utf-8")
    markdown_report = rehearsal_dir / "result.md"
    markdown_report.write_text("# result", encoding="utf-8")
    monkeypatch.setattr(
        rehearsal,
        "run_kam_schema_backfill_rehearsal",
        lambda **_: {
            "status": status,
            "report_path": str(json_report),
            "markdown_report_path": str(markdown_report),
            "clone_path": str(clone),
            "live_sha256_unchanged": True,
        },
    )

    result = CliRunner().invoke(
        app,
        [
            "rehearse-kam-schema-backfill",
            "--source-db",
            str(source),
            "--rehearsal-dir",
            str(rehearsal_dir),
            "--python-executable",
            sys.executable,
        ],
    )

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        f"status={status}",
        f"json_report={json_report}",
        f"markdown_report={markdown_report}",
        f"clone={clone}",
        "clone_retained=true",
        "live_sha256_unchanged=true",
    ]


def test_cli_safety_failure_exits_two_and_prints_existing_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typer.testing import CliRunner

    from kreports.cli.main import app
    from kreports.maintenance import kam_backfill_rehearsal as rehearsal

    source = tmp_path / "live.db"
    source.write_bytes(b"source")
    rehearsal_dir = tmp_path / "rehearsal"
    rehearsal_dir.mkdir()
    json_report = rehearsal_dir / "blocked.json"
    json_report.write_text("{}", encoding="utf-8")
    markdown_report = rehearsal_dir / "blocked.md"
    markdown_report.write_text("# blocked", encoding="utf-8")
    monkeypatch.setattr(
        rehearsal,
        "run_kam_schema_backfill_rehearsal",
        lambda **_: {
            "status": "preflight_blocked",
            "report_path": str(json_report),
            "markdown_report_path": str(markdown_report),
            "clone_path": None,
            "live_sha256_unchanged": False,
        },
    )

    result = CliRunner().invoke(
        app,
        [
            "rehearse-kam-schema-backfill",
            "--source-db",
            str(source),
            "--rehearsal-dir",
            str(rehearsal_dir),
        ],
    )

    assert result.exit_code == 2
    assert f"json_report={json_report}" in result.stdout
    assert "status=preflight_blocked" in result.stdout


@pytest.mark.parametrize(
    ("source_value", "directory_value"),
    [
        ("relative.db", "relative-dir"),
        ("relative.db", "/absolute/missing-directory"),
    ],
)
def test_cli_rejects_non_absolute_or_missing_operator_paths(
    source_value: str,
    directory_value: str,
) -> None:
    from typer.testing import CliRunner

    from kreports.cli.main import app

    result = CliRunner().invoke(
        app,
        [
            "rehearse-kam-schema-backfill",
            "--source-db",
            source_value,
            "--rehearsal-dir",
            directory_value,
        ],
    )

    assert result.exit_code == 2
