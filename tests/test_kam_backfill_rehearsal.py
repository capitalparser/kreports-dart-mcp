from __future__ import annotations

import hashlib
import hmac
import json
import sys
from datetime import UTC
from pathlib import Path
from types import SimpleNamespace

import pytest

_TEST_CAPABILITY = "ab" * 32
_EXPECTED_PROFESSIONAL_TOOLS = (
    "prepare_standard_audit_hours_inputs",
    "prepare_audit_materiality_inputs",
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


def _semantic_snapshot_fixture(
    **overrides: object,
) -> dict[str, object]:
    snapshot: dict[str, object] = {
        "ok": True,
        "kam_count": 10,
        "procedure_count": 8,
        "kam_quality_by_year": {"2025": {"usable": 10}},
        "procedure_quality_by_year": {"2025": {"usable": 8}},
        "duplicate_logical_identities": [],
        "integrity": {
            "orphan_procedure_count": 0,
            "cross_receipt_source_ordinal_link_count": 0,
            "usable_response_without_procedure_count": 0,
        },
        "audit_fee_observations": {
            "row_count": 1,
            "current_count": 1,
            "historical_count": 0,
        },
        "financial_compact_provenance": {
            "row_count": 1,
            "uncitable_count": 0,
        },
        "company_year_quality_freshness": {
            "row_count": 1,
            "blank_fingerprint_count": 0,
        },
        "semantic_sha256": "b" * 64,
    }
    snapshot.update(overrides)
    return snapshot


def _legacy_semantic_snapshot_fixture() -> dict[str, object]:
    snapshot = _semantic_snapshot_fixture()
    for key in (
        "audit_fee_observations",
        "financial_compact_provenance",
        "company_year_quality_freshness",
    ):
        snapshot.pop(key)
    return snapshot


def _mcp_row(
    tool: str,
    *,
    status: str = "usable",
    limitation_count: int = 0,
    **overrides: object,
) -> dict[str, object]:
    row: dict[str, object] = {
        "tool": tool,
        "status": status,
        "domain_verdict": None,
        "fact_count": 1,
        "evidence_count": 1,
        "pack_status": status,
        "table_ids": [],
        "source_count": 0,
        "resource_checked": False,
        "first_answer_paragraph": "bounded answer",
        "limitation_count": limitation_count,
    }
    row.update(overrides)
    return row


def _valid_mcp_payload(
    *,
    row_overrides: dict[str, dict[str, object]] | None = None,
) -> dict[str, object]:
    overrides = row_overrides or {}
    return {
        "ok": True,
        "tool_count": 18,
        "schema_error_closed": True,
        "all_boundary_parity": True,
        "matrix": [
            _mcp_row(tool, **overrides.get(tool, {}))
            for tool in _EXPECTED_PROFESSIONAL_TOOLS
        ],
    }


def test_rehearsal_fixture_is_closed_against_production_validator():
    from kreports.maintenance.kam_backfill_rehearsal import (
        _validate_mcp_payload,
    )

    _validate_mcp_payload(_valid_mcp_payload())


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
        repository_root=tmp_path,
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


def test_invoke_worker_rejects_oversized_stderr_without_buffering_it(
    tmp_path: Path,
) -> None:
    """Catch a child that can exhaust the parent through stderr alone."""
    from kreports.maintenance.kam_backfill_rehearsal import (
        RehearsalRunError,
        WorkerInvocation,
        invoke_worker,
    )

    _install_fake_worker(
        tmp_path,
        "import json\nimport sys\n"
        "print('x' * 2097152, file=sys.stderr)\n"
        "print(json.dumps({'ok': True}))",
    )

    with pytest.raises(RehearsalRunError) as caught:
        invoke_worker(
            python_executable=Path(sys.executable),
            database=tmp_path / "clone.db",
            marker_path=tmp_path / "kam-schema-backfill-rehearsal-marker.json",
            capability=_TEST_CAPABILITY,
            invocation=WorkerInvocation("migrate", "collector"),
            repository_root=tmp_path,
        )

    assert caught.value.code == "worker_output_too_large"


# Break caught: a package planted next to the retained clone wins Python's CWD
# import path before PYTHONPATH and executes instead of the approved worker.
def test_invoke_worker_runs_from_approved_repository_not_rehearsal_directory(
    tmp_path: Path,
) -> None:
    from kreports.maintenance.kam_backfill_rehearsal import (
        WorkerInvocation,
        invoke_worker,
    )

    rehearsal_dir = tmp_path / "rehearsal"
    approved_root = tmp_path / "approved-repository"
    database = rehearsal_dir / "clone.db"
    marker = rehearsal_dir / "kam-schema-backfill-rehearsal-marker.json"
    for root, origin in ((rehearsal_dir, "foreign"), (approved_root, "approved")):
        package = root / "kreports" / "maintenance"
        package.mkdir(parents=True)
        (package.parent / "__init__.py").write_text("", encoding="utf-8")
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "kam_rehearsal_worker.py").write_text(
            "import json\n"
            f"print(json.dumps({{'ok': True, 'origin': {origin!r}}}))\n",
            encoding="utf-8",
        )
    database.write_bytes(b"temporary rehearsal database")
    marker.write_text("{}", encoding="utf-8")

    payload = invoke_worker(
        python_executable=Path(sys.executable),
        database=database,
        marker_path=marker,
        capability=_TEST_CAPABILITY,
        invocation=WorkerInvocation("migrate", "collector"),
        repository_root=approved_root,
    )

    assert payload["origin"] == "approved"


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
            repository_root=tmp_path,
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
            repository_root=tmp_path,
        )

    assert caught.value.code == "worker_capability_disclosed"
    captured = capsys.readouterr()
    assert _TEST_CAPABILITY not in (
        str(caught.value) + captured.out + captured.err
    )


@pytest.mark.parametrize("transform", ["upper", "mixed"])
def test_invoke_worker_rejects_equivalent_hex_capability_case(
    tmp_path: Path,
    transform: str,
) -> None:
    from kreports.maintenance.kam_backfill_rehearsal import (
        RehearsalRunError,
        WorkerInvocation,
        invoke_worker,
    )

    _install_fake_worker(
        tmp_path,
        f"""
import json
import os
capability = os.environ["KREPORTS_REHEARSAL_CAPABILITY"]
if {transform!r} == "upper":
    disclosed = capability.upper()
else:
    disclosed = "".join(
        character.upper() if index % 2 else character
        for index, character in enumerate(capability)
    )
print(json.dumps({{"ok": True, "disclosed": disclosed}}))
""".strip(),
    )

    with pytest.raises(RehearsalRunError) as caught:
        invoke_worker(
            python_executable=Path(sys.executable),
            database=tmp_path / "clone.db",
            marker_path=(
                tmp_path / "kam-schema-backfill-rehearsal-marker.json"
            ),
            capability=_TEST_CAPABILITY,
            invocation=WorkerInvocation("migrate", "collector"),
            repository_root=tmp_path,
        )

    assert caught.value.code == "worker_capability_disclosed"


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
        repository_root=tmp_path,
    )
    unscoped = invoke_worker(
        python_executable=Path(sys.executable),
        database=tmp_path / "clone.db",
        marker_path=(
            tmp_path / "kam-schema-backfill-rehearsal-marker.json"
        ),
        capability=_TEST_CAPABILITY,
        invocation=WorkerInvocation("migrate", "collector"),
        repository_root=tmp_path,
    )

    assert scoped["argv"] == ["kam-rebuild", "--year", "2023"]
    assert unscoped["argv"] == ["migrate"]


def test_worker_timeouts_keep_mcp_validation_bounded_at_thirty_minutes() -> None:
    """The full professional-tool matrix needs its own bounded window."""
    from kreports.maintenance.kam_backfill_rehearsal import (
        _WORKER_TIMEOUT_SECONDS,
    )

    assert _WORKER_TIMEOUT_SECONDS == {
        "migrate": 600,
        "kam-dry-run": 900,
        "kam-rebuild": 3600,
        "procedure-index": 3600,
        "audit-fee-observation-backfill": 900,
        "financial-compact-rebuild": 1800,
        "company-year-quality-rebuild": 1800,
        "semantic-snapshot": 600,
        "mcp-validate": 1800,
    }


def _install_phase_harness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail_action: str | None = None,
    fail_year: int | None = None,
    mcp_payload: dict[str, object] | None = None,
    snapshot_drift: bool = False,
    snapshot_payload: dict[str, object] | None = None,
    expected_capability: str | None = None,
    fail_preflight_code: str | None = None,
    source_change_check: int | None = None,
) -> tuple[Path, Path, Path, list[tuple[object, ...]]]:
    from kreports.maintenance import kam_backfill_rehearsal as rehearsal

    source = tmp_path / "live.db"
    source.write_bytes(b"source-database")
    rehearsal_dir = tmp_path / "rehearsal"
    rehearsal_dir.mkdir()
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    calls: list[tuple[object, ...]] = []
    source_check_count = 0

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
        if fail_preflight_code is not None:
            raise SimpleSafetyError(
                fail_preflight_code,
                "bounded preflight failure",
            )
        return SimpleNamespace(
            source=identity(source_db),
            rehearsal_dir=target_dir,
            free_bytes=20 * 1024**3,
            filesystem_type="apfs",
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
        nonlocal source_check_count
        source_check_count += 1
        calls.append(("source-unchanged", expected.sha256))
        if source_check_count == source_change_check:
            raise SimpleSafetyError(
                "source_changed",
                "source identity changed",
            )
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
        repository_root: Path | None = None,
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
            payload = dict(
                snapshot_payload
                if snapshot_payload is not None
                else _semantic_snapshot_fixture()
            )
            payload["semantic_sha256"] = (
                "c" * 64
                if snapshot_drift and snapshot_index == 3
                else payload.get("semantic_sha256")
            )
            return payload
        if invocation.action == "mcp-validate":
            return mcp_payload or _valid_mcp_payload()
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
    assert not {
        "audit-fee-observation-backfill",
        "financial-compact-rebuild",
        "company-year-quality-rebuild",
    } & {action for action, _year in worker_calls}
    assert sum(call[0] == "free-space" for call in calls) == 5
    assert sum(call[0] == "source-unchanged" for call in calls) == 8
    assert Path(report["report_path"]).exists()
    persisted = __import__("json").loads(
        Path(report["report_path"]).read_text(encoding="utf-8"),
    )
    assert persisted["phases"] == report["phases"]


def test_evidence_hardening_is_opt_in_and_preserves_exact_worker_order(
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
        include_db_evidence=True,
    )

    phase_names = [phase["name"] for phase in report["phases"]]
    procedure_index = phase_names.index("procedure_reconcile_complete")
    assert phase_names == [
        *PHASES[: procedure_index + 1],
        "audit_fee_observations_backfilled",
        "financial_compact_provenance_rebuilt",
        "quality_ledger_rebuilt",
        *PHASES[procedure_index + 1 :],
    ]

    worker_calls = [
        (call[1], call[2])
        for call in calls
        if call[0] == "worker"
    ]
    snapshot_positions = [
        index
        for index, (action, _year) in enumerate(worker_calls)
        if action == "semantic-snapshot"
    ]
    second_pass = worker_calls[
        snapshot_positions[1] + 1 : snapshot_positions[2] + 1
    ]
    expected_second_pass = [
        *[("kam-rebuild", year) for year in REHEARSAL_YEARS],
        *[("procedure-index", year) for year in REHEARSAL_YEARS],
        *[
            ("audit-fee-observation-backfill", year)
            for year in REHEARSAL_YEARS
        ],
        *[
            ("financial-compact-rebuild", year)
            for year in REHEARSAL_YEARS
        ],
        *[
            ("company-year-quality-rebuild", year)
            for year in REHEARSAL_YEARS
        ],
        ("semantic-snapshot", None),
    ]
    assert second_pass == expected_second_pass
    assert report["idempotency"]["integrity"][
        "audit_fee_observations"
    ]["current_count"] == 1


@pytest.mark.parametrize(
    ("failed_action", "expected_phase", "next_action"),
    [
        (
            "audit-fee-observation-backfill",
            "audit_fee_observations_backfilled",
            "financial-compact-rebuild",
        ),
        (
            "financial-compact-rebuild",
            "financial_compact_provenance_rebuilt",
            "company-year-quality-rebuild",
        ),
        (
            "company-year-quality-rebuild",
            "quality_ledger_rebuilt",
            "semantic-snapshot",
        ),
    ],
)
def test_each_evidence_phase_fails_closed_before_later_workers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_action: str,
    expected_phase: str,
    next_action: str,
) -> None:
    from kreports.maintenance.kam_backfill_rehearsal import (
        run_kam_schema_backfill_rehearsal,
    )

    source, rehearsal_dir, repository_root, calls = _install_phase_harness(
        tmp_path,
        monkeypatch,
        fail_action=failed_action,
        fail_year=2023,
    )
    report = run_kam_schema_backfill_rehearsal(
        source_db=source,
        rehearsal_dir=rehearsal_dir,
        repository_root=repository_root,
        python_executable=Path(sys.executable),
        min_free_bytes=10 * 1024**3,
        include_db_evidence=True,
    )
    failed_worker_index = next(
        index
        for index, call in enumerate(calls)
        if call[:3] == ("worker", failed_action, 2023)
    )

    assert report["status"] == "backfill_failed"
    assert report["last_phase"] == expected_phase
    assert report["phases"][-1]["status"] == "failed"
    assert calls[failed_worker_index - 2][0] == "source-unchanged"
    assert calls[failed_worker_index - 1][0] == "free-space"
    assert calls[-1][0] == "source-unchanged"
    assert all(
        call[0] != "worker"
        for call in calls[failed_worker_index + 1 :]
    )
    assert not any(
        call[:2] == ("worker", next_action)
        for call in calls[failed_worker_index + 1 :]
    )


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
        min_free_bytes=12 * 1024**3,
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


@pytest.mark.parametrize(
    ("source_change_check", "expected_phase"),
    [
        (1, "clone_created"),
        (2, "schema_migrated"),
    ],
)
def test_source_change_always_classifies_live_digest_changed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_change_check: int,
    expected_phase: str,
) -> None:
    from kreports.maintenance.kam_backfill_rehearsal import (
        run_kam_schema_backfill_rehearsal,
    )

    source, rehearsal_dir, repository_root, _ = _install_phase_harness(
        tmp_path,
        monkeypatch,
        source_change_check=source_change_check,
    )
    report = run_kam_schema_backfill_rehearsal(
        source_db=source,
        rehearsal_dir=rehearsal_dir,
        repository_root=repository_root,
        python_executable=Path(sys.executable),
    )

    assert report["status"] == "live_digest_changed"
    assert report["last_phase"] == expected_phase


@pytest.mark.parametrize(
    (
        "source_change_check",
        "expected_phase",
        "expected_action_counts",
    ),
    [
        pytest.param(
            4,
            "procedure_reconcile_complete",
            {
                "kam-rebuild": 5,
                "procedure-index": 5,
                "semantic-snapshot": 1,
                "mcp-validate": 0,
            },
            id="first-procedure-loop",
        ),
        pytest.param(
            6,
            "idempotency_verified",
            {
                "kam-rebuild": 10,
                "procedure-index": 10,
                "semantic-snapshot": 2,
                "mcp-validate": 0,
            },
            id="second-procedure-loop",
        ),
    ],
)
def test_source_change_after_procedure_loop_stops_before_next_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_change_check: int,
    expected_phase: str,
    expected_action_counts: dict[str, int],
) -> None:
    """Catch either procedure loop advancing before source revalidation."""
    from kreports.maintenance.kam_backfill_rehearsal import (
        run_kam_schema_backfill_rehearsal,
    )

    source, rehearsal_dir, repository_root, calls = _install_phase_harness(
        tmp_path,
        monkeypatch,
        source_change_check=source_change_check,
    )
    report = run_kam_schema_backfill_rehearsal(
        source_db=source,
        rehearsal_dir=rehearsal_dir,
        repository_root=repository_root,
        python_executable=Path(sys.executable),
    )
    worker_actions = [
        call[1]
        for call in calls
        if call[0] == "worker"
    ]
    failed_check_index = max(
        index
        for index, call in enumerate(calls)
        if call[0] == "source-unchanged"
    )

    assert report["status"] == "live_digest_changed"
    assert report["last_phase"] == expected_phase
    assert report["phases"][-1]["status"] == "failed"
    assert {
        action: worker_actions.count(action)
        for action in expected_action_counts
    } == expected_action_counts
    assert all(
        call[0] != "worker"
        for call in calls[failed_check_index + 1:]
    )


@pytest.mark.parametrize(
    "snapshot",
    [
        pytest.param({"ok": True}, id="missing-all-evidence"),
        pytest.param(
            _semantic_snapshot_fixture(semantic_sha256="B" * 64),
            id="non-lowercase-digest",
        ),
        pytest.param(
            _semantic_snapshot_fixture(
                kam_count=0,
                procedure_count=0,
                kam_quality_by_year={},
                procedure_quality_by_year={},
            ),
            id="empty-count-and-quality-structures",
        ),
        pytest.param(
            _semantic_snapshot_fixture(integrity={
                "orphan_procedure_count": 0,
                "cross_receipt_source_ordinal_link_count": 0,
            }),
            id="missing-integrity-counter",
        ),
        pytest.param(
            _semantic_snapshot_fixture(integrity={
                "orphan_procedure_count": -1,
                "cross_receipt_source_ordinal_link_count": 0,
                "usable_response_without_procedure_count": 0,
            }),
            id="negative-integrity-counter",
        ),
        pytest.param(
            _semantic_snapshot_fixture(integrity={
                "orphan_procedure_count": 1,
                "cross_receipt_source_ordinal_link_count": 0,
                "usable_response_without_procedure_count": 0,
            }),
            id="positive-integrity-blocker",
        ),
        pytest.param(
            _semantic_snapshot_fixture(
                duplicate_logical_identities=[{
                    "rcept_no": "20250000000000",
                    "source_type": "audit_report",
                    "ordinal": 1,
                    "count": 2,
                }],
            ),
            id="duplicate-logical-identity-blocker",
        ),
    ],
)
def test_rehearsal_rejects_malformed_or_adverse_semantic_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    snapshot: dict[str, object],
) -> None:
    from kreports.maintenance.kam_backfill_rehearsal import (
        run_kam_schema_backfill_rehearsal,
    )

    source, rehearsal_dir, repository_root, _ = _install_phase_harness(
        tmp_path,
        monkeypatch,
        snapshot_payload=snapshot,
    )
    report = run_kam_schema_backfill_rehearsal(
        source_db=source,
        rehearsal_dir=rehearsal_dir,
        repository_root=repository_root,
        python_executable=Path(sys.executable),
    )

    assert report["status"] == "backfill_failed"
    assert report["last_phase"] == (
        "procedure_reconcile_complete"
        if snapshot.get("kam_count") == 0
        else "kam_rebuild_complete"
    )
    assert report["phases"][-1]["status"] == "failed"


@pytest.mark.parametrize(
    ("section", "value", "expected_code"),
    [
        (
            "audit_fee_observations",
            None,
            "semantic_snapshot_invalid",
        ),
        (
            "financial_compact_provenance",
            None,
            "semantic_snapshot_invalid",
        ),
        (
            "company_year_quality_freshness",
            None,
            "semantic_snapshot_invalid",
        ),
        (
            "audit_fee_observations",
            [],
            "semantic_snapshot_invalid",
        ),
        (
            "audit_fee_observations",
            {
                "row_count": 1,
                "current_count": 1,
                "historical_count": 0,
                "unexpected": 0,
            },
            "semantic_snapshot_invalid",
        ),
        (
            "audit_fee_observations",
            {
                "row_count": 1,
                "current_count": -1,
                "historical_count": 2,
            },
            "semantic_snapshot_invalid",
        ),
        (
            "audit_fee_observations",
            {
                "row_count": 1,
                "current_count": True,
                "historical_count": 0,
            },
            "semantic_snapshot_invalid",
        ),
        (
            "audit_fee_observations",
            {
                "row_count": 2,
                "current_count": 1,
                "historical_count": 0,
            },
            "semantic_snapshot_invalid",
        ),
        (
            "financial_compact_provenance",
            {
                "row_count": 1,
            },
            "semantic_snapshot_invalid",
        ),
        (
            "financial_compact_provenance",
            {
                "row_count": 1,
                "uncitable_count": 2,
            },
            "semantic_snapshot_invalid",
        ),
        (
            "financial_compact_provenance",
            {
                "row_count": False,
                "uncitable_count": 0,
            },
            "semantic_snapshot_invalid",
        ),
        (
            "company_year_quality_freshness",
            {
                "row_count": 1,
                "blank_fingerprint_count": 0,
                "unexpected": 0,
            },
            "semantic_snapshot_invalid",
        ),
        (
            "company_year_quality_freshness",
            {
                "row_count": 1,
                "blank_fingerprint_count": 2,
            },
            "semantic_snapshot_invalid",
        ),
        (
            "company_year_quality_freshness",
            {
                "row_count": 1,
                "blank_fingerprint_count": 1,
            },
            "semantic_snapshot_blocked",
        ),
    ],
)
def test_opt_in_snapshot_rejects_missing_malformed_or_inconsistent_aggregates(
    section: str,
    value: object,
    expected_code: str,
) -> None:
    from kreports.maintenance.kam_backfill_rehearsal import (
        RehearsalRunError,
        _validate_semantic_snapshot,
    )

    snapshot = _semantic_snapshot_fixture()
    if value is None:
        snapshot.pop(section)
    else:
        snapshot[section] = value

    with pytest.raises(RehearsalRunError) as caught:
        _validate_semantic_snapshot(
            snapshot,
            require_db_evidence=True,
        )

    assert caught.value.code == expected_code


def test_opt_in_snapshot_accepts_consistent_zero_observation_revision04_case(
) -> None:
    from kreports.maintenance.kam_backfill_rehearsal import (
        _validate_semantic_snapshot,
    )

    snapshot = _semantic_snapshot_fixture(
        audit_fee_observations={
            "row_count": 0,
            "current_count": 0,
            "historical_count": 0,
        },
        financial_compact_provenance={
            "row_count": 1,
            "uncitable_count": 0,
        },
        company_year_quality_freshness={
            "row_count": 5,
            "blank_fingerprint_count": 0,
        },
    )

    _validate_semantic_snapshot(snapshot, require_db_evidence=True)


def test_db_evidence_strictness_is_opt_in_and_legacy_integrity_shape_survives(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kreports.maintenance.kam_backfill_rehearsal import (
        run_kam_schema_backfill_rehearsal,
    )

    legacy_snapshot = _legacy_semantic_snapshot_fixture()
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    source, rehearsal_dir, repository_root, _ = _install_phase_harness(
        legacy_root,
        monkeypatch,
        snapshot_payload=legacy_snapshot,
    )
    legacy_report = run_kam_schema_backfill_rehearsal(
        source_db=source,
        rehearsal_dir=rehearsal_dir,
        repository_root=repository_root,
        python_executable=Path(sys.executable),
    )
    assert legacy_report["status"] == "complete"
    assert set(legacy_report["idempotency"]["integrity"]) == {
        "kam_count",
        "procedure_count",
        "kam_quality_by_year",
        "procedure_quality_by_year",
        "duplicate_logical_identities",
        "integrity",
    }

    strict_root = tmp_path / "strict"
    strict_root.mkdir()
    source, rehearsal_dir, repository_root, _ = _install_phase_harness(
        strict_root,
        monkeypatch,
        snapshot_payload=legacy_snapshot,
    )
    strict_report = run_kam_schema_backfill_rehearsal(
        source_db=source,
        rehearsal_dir=rehearsal_dir,
        repository_root=repository_root,
        python_executable=Path(sys.executable),
        include_db_evidence=True,
    )
    assert strict_report["status"] == "backfill_failed"
    assert strict_report["last_phase"] == "quality_ledger_rebuilt"
    assert strict_report["phases"][-1]["evidence"]["error_code"] == (
        "semantic_snapshot_invalid"
    )


def test_rehearsal_rejects_existing_clone_and_exact_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import datetime

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
    fixed = datetime(2026, 7, 29, 1, 2, 3, tzinfo=UTC)
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


def test_preflight_rejection_writes_nothing_and_cli_prints_empty_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typer.testing import CliRunner

    from kreports.cli.main import app
    from kreports.maintenance import kam_backfill_rehearsal as rehearsal

    source, rehearsal_dir, repository_root, _ = _install_phase_harness(
        tmp_path,
        monkeypatch,
        fail_preflight_code="unsafe_rehearsal_directory",
    )
    report = rehearsal.run_kam_schema_backfill_rehearsal(
        source_db=source,
        rehearsal_dir=rehearsal_dir,
        repository_root=repository_root,
        python_executable=Path(sys.executable),
    )

    assert report["status"] == "preflight_blocked"
    assert report["report_path"] == ""
    assert report["markdown_report_path"] == ""
    assert not list(rehearsal_dir.iterdir())

    monkeypatch.setattr(
        rehearsal,
        "run_kam_schema_backfill_rehearsal",
        lambda **_: report,
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
    assert result.stdout.splitlines() == [
        "status=preflight_blocked",
        "json_report=",
        "markdown_report=",
        "clone=",
        "clone_retained=false",
        "live_sha256_unchanged=not_verified",
    ]


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


def test_markdown_marks_unchecked_live_digest_as_not_verified() -> None:
    from kreports.maintenance.kam_backfill_rehearsal import (
        render_rehearsal_markdown,
    )

    report = _terminal_report_fixture("backfill_failed")
    report["live_sha256_unchanged"] = None

    markdown = render_rehearsal_markdown(report)

    assert "Live SHA-256 unchanged: `not_verified`" in markdown


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
    ("limitation_count", "expected_status"),
    [
        (1, "data_quality_limited"),
        (0, "backfill_failed"),
    ],
)
def test_terminal_status_classifies_mcp_evidence_limitations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    limitation_count: int,
    expected_status: str,
) -> None:
    from kreports.maintenance.kam_backfill_rehearsal import (
        run_kam_schema_backfill_rehearsal,
    )

    source, rehearsal_dir, repository_root, _ = _install_phase_harness(
        tmp_path,
        monkeypatch,
        mcp_payload=_valid_mcp_payload(row_overrides={
            "get_kam_lifecycle": {
                "status": "limited",
                "pack_status": "limited",
                "limitation_count": limitation_count,
            },
        }),
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
            "tool_count": 18,
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
    "malformation",
    [
        "empty-rows",
        "duplicate-tools",
        "unexpected-tool",
        "invalid-status",
        "invalid-status-type",
        "pack-status-mismatch",
        "invalid-pack-status-type",
        "invalid-resource-flag",
        "limited-without-limitation",
        "missing-without-limitation",
        "missing-row-field",
    ],
)
def test_rehearsal_rejects_malformed_mcp_matrix_before_classification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    malformation: str,
) -> None:
    from kreports.maintenance.kam_backfill_rehearsal import (
        run_kam_schema_backfill_rehearsal,
    )

    payload = _valid_mcp_payload()
    matrix = payload["matrix"]
    assert isinstance(matrix, list)
    if malformation == "empty-rows":
        payload["matrix"] = [{} for _ in range(18)]
    elif malformation == "duplicate-tools":
        payload["matrix"] = [dict(matrix[0]) for _ in range(18)]
    elif malformation == "unexpected-tool":
        matrix[0]["tool"] = "not_a_professional_tool"
    elif malformation == "invalid-status":
        matrix[0]["status"] = "unknown"
    elif malformation == "invalid-status-type":
        matrix[0]["status"] = {"not": "canonical"}
    elif malformation == "pack-status-mismatch":
        matrix[0]["pack_status"] = "missing"
    elif malformation == "invalid-pack-status-type":
        matrix[0]["pack_status"] = {"not": "canonical"}
    elif malformation == "invalid-resource-flag":
        matrix[0]["resource_checked"] = "false"
    elif malformation == "limited-without-limitation":
        matrix[0].update({
            "status": "limited",
            "pack_status": "limited",
            "limitation_count": 0,
        })
    elif malformation == "missing-without-limitation":
        matrix[0].update({
            "status": "missing",
            "pack_status": "missing",
            "limitation_count": 0,
        })
    else:
        matrix[0].pop("first_answer_paragraph")

    source, rehearsal_dir, repository_root, _ = _install_phase_harness(
        tmp_path,
        monkeypatch,
        mcp_payload=payload,
    )
    report = run_kam_schema_backfill_rehearsal(
        source_db=source,
        rehearsal_dir=rehearsal_dir,
        repository_root=repository_root,
        python_executable=Path(sys.executable),
    )

    assert report["status"] == "backfill_failed"
    assert report["last_phase"] == "mcp_validation_complete"
    assert report["phases"][-1]["status"] == "failed"
    assert report["phases"][-1]["evidence"]["error_code"] == (
        "mcp_schema_not_closed"
    )


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


def test_cli_prints_not_verified_when_live_digest_was_not_checked(
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
    monkeypatch.setattr(
        rehearsal,
        "run_kam_schema_backfill_rehearsal",
        lambda **_: {
            "status": "backfill_failed",
            "report_path": "",
            "markdown_report_path": "",
            "clone_path": "",
            "live_sha256_unchanged": None,
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

    assert result.exit_code == 2
    assert result.stdout.splitlines()[-1] == (
        "live_sha256_unchanged=not_verified"
    )


def test_db_evidence_cli_resolves_paths_and_opts_into_evidence_phases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typer.testing import CliRunner

    from kreports.cli import main as cli_main
    from kreports.maintenance import kam_backfill_rehearsal as rehearsal

    source = tmp_path / "source.db"
    source.write_bytes(b"source")
    rehearsal_dir = tmp_path / "rehearsal"
    rehearsal_dir.mkdir()
    captured: dict[str, object] = {}

    def fake_rehearsal(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {
            "status": "complete",
            "report_path": "",
            "markdown_report_path": "",
            "clone_path": "",
            "live_sha256_unchanged": True,
        }

    monkeypatch.setattr(
        rehearsal,
        "run_kam_schema_backfill_rehearsal",
        fake_rehearsal,
    )
    result = CliRunner().invoke(
        cli_main.app,
        [
            "rehearse-db-evidence-hardening",
            "--source-db",
            str(source),
            "--rehearsal-dir",
            str(rehearsal_dir),
            "--python-executable",
            sys.executable,
        ],
    )

    assert result.exit_code == 0
    assert captured == {
        "source_db": source.resolve(),
        "rehearsal_dir": rehearsal_dir.resolve(),
        "repository_root": Path(cli_main.__file__).resolve().parents[2],
        "python_executable": Path(sys.executable).absolute(),
        "include_db_evidence": True,
    }


def test_cli_preserves_virtualenv_python_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typer.testing import CliRunner

    from kreports.cli import main as cli_main
    from kreports.maintenance import kam_backfill_rehearsal as rehearsal

    source = tmp_path / "source.db"
    source.write_bytes(b"source")
    rehearsal_dir = tmp_path / "rehearsal"
    rehearsal_dir.mkdir()
    virtualenv_python = tmp_path / "venv-python"
    virtualenv_python.symlink_to(Path(sys.executable))
    captured: dict[str, object] = {}

    def fake_rehearsal(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {
            "status": "complete",
            "report_path": "",
            "markdown_report_path": "",
            "clone_path": "",
            "live_sha256_unchanged": True,
        }

    monkeypatch.setattr(
        rehearsal,
        "run_kam_schema_backfill_rehearsal",
        fake_rehearsal,
    )

    result = CliRunner().invoke(
        cli_main.app,
        [
            "rehearse-kam-schema-backfill",
            "--source-db",
            str(source),
            "--rehearsal-dir",
            str(rehearsal_dir),
            "--python-executable",
            str(virtualenv_python),
        ],
    )

    assert result.exit_code == 0
    assert captured["python_executable"] == virtualenv_python.absolute()


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


def test_cli_reports_exact_retained_clone_when_marker_creation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typer.testing import CliRunner

    from kreports.cli.main import app
    from kreports.maintenance import kam_backfill_rehearsal as rehearsal

    source, rehearsal_dir, _, _ = _install_phase_harness(
        tmp_path,
        monkeypatch,
    )

    def fail_marker(**_: object) -> Path:
        raise rehearsal.RehearsalRunError(
            "rehearsal_marker_invalid",
            "bounded marker failure",
        )

    monkeypatch.setattr(rehearsal, "_create_rehearsal_marker", fail_marker)
    clone = (rehearsal_dir / "kreports-rehearsal.db").resolve()
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
    output = result.stdout.splitlines()
    assert output[0] == "status=preflight_blocked"
    assert output[1].startswith("json_report=")
    assert Path(output[1].partition("=")[2]).is_file()
    assert output[2].startswith("markdown_report=")
    assert Path(output[2].partition("=")[2]).is_file()
    assert output[3:] == [
        f"clone={clone}",
        "clone_retained=true",
        "live_sha256_unchanged=not_verified",
    ]


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
