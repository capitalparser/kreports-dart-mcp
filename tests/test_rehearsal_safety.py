from __future__ import annotations

import copy
from dataclasses import dataclass, replace
import gc
from pathlib import Path
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import weakref

import pytest

from kreports.maintenance import rehearsal_safety
from kreports.maintenance.rehearsal_safety import (
    MIN_FREE_BYTES,
    RehearsalSafetyError,
    SourcePreflight,
    assert_free_space,
    assert_source_unchanged,
    create_apfs_clone,
    inspect_source_database,
    preflight_rehearsal,
)


@dataclass(frozen=True)
class ValidPaths:
    source: Path
    rehearsal_dir: Path
    repository_root: Path


def _write_database(path: Path, *, running_lease: bool = False) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE backfill_runs(id INTEGER, status TEXT)")
        if running_lease:
            connection.execute(
                "INSERT INTO backfill_runs(id, status) VALUES (1, 'running')"
            )


def _valid_paths(tmp_path: Path) -> ValidPaths:
    tmp_path.mkdir(parents=True, exist_ok=True)
    repository_root = tmp_path / "repository"
    source_dir = tmp_path / "source"
    rehearsal_dir = tmp_path / "rehearsal"
    repository_root.mkdir()
    source_dir.mkdir()
    rehearsal_dir.mkdir()
    source = source_dir / "production.db"
    _write_database(source)
    return ValidPaths(source, rehearsal_dir, repository_root)


def _arrange_source_case(tmp_path: Path, arrange: str) -> tuple[Path, Path, Path]:
    paths = _valid_paths(tmp_path)
    if arrange == "relative":
        return Path("production.db"), paths.rehearsal_dir, paths.repository_root
    if arrange == "symlink":
        symlink = paths.source.parent / "linked.db"
        symlink.symlink_to(paths.source)
        return symlink, paths.rehearsal_dir, paths.repository_root
    if arrange == "hardlink":
        hardlink = paths.source.parent / "linked.db"
        os.link(paths.source, hardlink)
        return hardlink, paths.rehearsal_dir, paths.repository_root
    if arrange in {"wal", "shm"}:
        (Path(f"{paths.source}-{arrange}")).write_bytes(b"sidecar")
        return paths.source, paths.rehearsal_dir, paths.repository_root
    if arrange == "directory":
        return paths.source.parent, paths.rehearsal_dir, paths.repository_root
    if arrange == "corrupt":
        paths.source.write_bytes(b"this is not a sqlite database")
        return paths.source, paths.rehearsal_dir, paths.repository_root
    if arrange == "running_lease":
        paths.source.unlink()
        _write_database(paths.source, running_lease=True)
        return paths.source, paths.rehearsal_dir, paths.repository_root
    raise AssertionError(f"unknown arrangement: {arrange}")


def _apfs_runner(commands: list[list[str]]):
    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[:3] == ["/usr/bin/stat", "-f", "%T"]:
            return subprocess.CompletedProcess(command, 0, stdout="apfs\n", stderr="")
        if command[0] == "/bin/cp":
            shutil.copy2(command[2], command[3])
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected subprocess command: {command}")

    return run


def _valid_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ValidPaths, SourcePreflight, list[list[str]]]:
    paths = _valid_paths(tmp_path)
    commands: list[list[str]] = []
    monkeypatch.setattr(rehearsal_safety.sys, "platform", "darwin")
    monkeypatch.setattr(rehearsal_safety.subprocess, "run", _apfs_runner(commands))
    monkeypatch.setattr(
        rehearsal_safety.shutil,
        "disk_usage",
        lambda _path: shutil._ntuple_diskusage(
            2 * MIN_FREE_BYTES,
            MIN_FREE_BYTES,
            MIN_FREE_BYTES,
        ),
    )
    preflight = preflight_rehearsal(
        paths.source,
        paths.rehearsal_dir,
        repository_root=paths.repository_root,
    )
    return paths, preflight, commands


# Break caught: accepting a relative path would make an untrusted source location
# look like a production database.
@pytest.mark.parametrize(
    ("arrange", "expected_code"),
    [
        ("relative", "source_not_absolute"),
        ("symlink", "source_is_symlink"),
        ("hardlink", "source_is_hardlink"),
        ("directory", "source_not_regular"),
        ("wal", "source_sidecar_present"),
        ("shm", "source_sidecar_present"),
        ("corrupt", "source_integrity_failed"),
        ("running_lease", "active_backfill_lease"),
    ],
)
def test_preflight_rejects_unsafe_source(
    tmp_path: Path,
    arrange: str,
    expected_code: str,
) -> None:
    source, rehearsal_dir, repository_root = _arrange_source_case(tmp_path, arrange)

    with pytest.raises(RehearsalSafetyError) as caught:
        preflight_rehearsal(
            source,
            rehearsal_dir,
            repository_root=repository_root,
        )

    assert caught.value.code == expected_code


# Break caught: a source that changes during inspection could be cloned without a
# stable digest or immutable metadata witness.
def test_inspect_source_returns_post_check_identity(tmp_path: Path) -> None:
    source, _, _ = _arrange_source_case(tmp_path, "running_lease")
    with sqlite3.connect(source) as connection:
        connection.execute("UPDATE backfill_runs SET status = 'finished'")

    identity = inspect_source_database(source)

    assert identity.path == source
    assert identity.size == source.stat().st_size
    assert identity.inode == source.stat().st_ino
    assert identity.device == source.stat().st_dev
    assert identity.mtime_ns == source.stat().st_mtime_ns
    assert len(identity.sha256) == 64


# Break caught: adding hidden authority fields to the public dataclass breaks
# the exact four-argument constructor promised to callers.
def test_source_preflight_preserves_four_field_constructor(tmp_path: Path) -> None:
    paths = _valid_paths(tmp_path)
    source = inspect_source_database(paths.source)

    approval = SourcePreflight(
        source,
        paths.rehearsal_dir,
        MIN_FREE_BYTES,
        "apfs",
    )

    assert approval.source == source
    assert approval.rehearsal_dir == paths.rehearsal_dir
    assert approval.free_bytes == MIN_FREE_BYTES
    assert approval.filesystem_type == "apfs"


# Break caught: writing a rehearsal clone into an operational or broad directory
# could overwrite source or repository state.
@pytest.mark.parametrize(
    "unsafe_kind",
    [
        "root",
        "home",
        "repository",
        "source_parent",
        "source_child",
        "repository_child",
        "symlink",
    ],
)
def test_preflight_rejects_unsafe_rehearsal_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsafe_kind: str,
) -> None:
    paths = _valid_paths(tmp_path)
    unsafe = {
        "root": Path("/"),
        "home": Path.home(),
        "repository": paths.repository_root,
        "source_parent": paths.source.parent,
        "source_child": paths.source.parent / "child",
        "repository_child": paths.repository_root / "child",
        "symlink": tmp_path / "rehearsal-link",
    }[unsafe_kind]
    if unsafe_kind.endswith("child"):
        unsafe.mkdir()
    if unsafe_kind == "symlink":
        unsafe.symlink_to(paths.rehearsal_dir, target_is_directory=True)
    commands: list[list[str]] = []
    monkeypatch.setattr(rehearsal_safety.sys, "platform", "darwin")
    monkeypatch.setattr(rehearsal_safety.subprocess, "run", _apfs_runner(commands))

    with pytest.raises(RehearsalSafetyError) as caught:
        preflight_rehearsal(
            paths.source,
            unsafe,
            repository_root=paths.repository_root,
        )

    assert caught.value.code == "unsafe_rehearsal_directory"


# Break caught: placing source and clone on different devices invalidates the
# APFS clone boundary.
def test_preflight_rejects_different_filesystem(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _valid_paths(tmp_path)
    original_stat = Path.stat

    def stat_on_other_device(self: Path, *args: object, **kwargs: object) -> os.stat_result:
        result = original_stat(self, *args, **kwargs)
        if self == paths.rehearsal_dir:
            values = list(result)
            values[stat.ST_DEV] += 1
            return os.stat_result(values)
        return result

    commands: list[list[str]] = []
    monkeypatch.setattr(Path, "stat", stat_on_other_device)
    monkeypatch.setattr(rehearsal_safety.sys, "platform", "darwin")
    monkeypatch.setattr(rehearsal_safety.subprocess, "run", _apfs_runner(commands))

    with pytest.raises(RehearsalSafetyError) as caught:
        preflight_rehearsal(
            paths.source,
            paths.rehearsal_dir,
            repository_root=paths.repository_root,
        )

    assert caught.value.code == "different_filesystem"


# Break caught: allowing a non-APFS destination would silently downgrade the
# clone operation to a filesystem with different semantics.
def test_preflight_requires_apfs_filesystem(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _valid_paths(tmp_path)
    monkeypatch.setattr(rehearsal_safety.sys, "platform", "darwin")
    monkeypatch.setattr(
        rehearsal_safety.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, stdout="hfs\n", stderr=""
        ),
    )

    with pytest.raises(RehearsalSafetyError) as caught:
        preflight_rehearsal(
            paths.source,
            paths.rehearsal_dir,
            repository_root=paths.repository_root,
        )

    assert caught.value.code == "filesystem_not_apfs"


# Break caught: even an APFS volume is unsafe when the host cannot execute the
# Darwin-only clonefile contract.
def test_preflight_requires_darwin_for_apfs_clone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _valid_paths(tmp_path)
    commands: list[list[str]] = []
    monkeypatch.setattr(rehearsal_safety.sys, "platform", "linux")
    monkeypatch.setattr(rehearsal_safety.subprocess, "run", _apfs_runner(commands))

    with pytest.raises(RehearsalSafetyError) as caught:
        preflight_rehearsal(
            paths.source,
            paths.rehearsal_dir,
            repository_root=paths.repository_root,
        )

    assert caught.value.code == "filesystem_not_apfs"


# Break caught: accepting even one byte below the reserve allows the rehearsal
# to exhaust space while writing a clone.
def test_preflight_requires_exact_free_space_floor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _valid_paths(tmp_path)
    commands: list[list[str]] = []
    monkeypatch.setattr(rehearsal_safety.sys, "platform", "darwin")
    monkeypatch.setattr(rehearsal_safety.subprocess, "run", _apfs_runner(commands))
    monkeypatch.setattr(
        rehearsal_safety.shutil,
        "disk_usage",
        lambda _path: shutil._ntuple_diskusage(20, 15, MIN_FREE_BYTES - 1),
    )

    with pytest.raises(RehearsalSafetyError) as caught:
        preflight_rehearsal(
            paths.source,
            paths.rehearsal_dir,
            repository_root=paths.repository_root,
        )

    assert caught.value.code == "insufficient_free_space"


# Break caught: a caller-provided reserve below 10 GiB could bypass the global
# safety floor even when the current filesystem has ample free space.
def test_assert_free_space_rejects_below_global_floor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rehearsal_safety.shutil,
        "disk_usage",
        lambda _path: shutil._ntuple_diskusage(
            3 * MIN_FREE_BYTES,
            MIN_FREE_BYTES,
            2 * MIN_FREE_BYTES,
        ),
    )

    with pytest.raises(RehearsalSafetyError) as caught:
        assert_free_space(
            tmp_path,
            min_free_bytes=MIN_FREE_BYTES - 1,
        )

    assert caught.value.code == "insufficient_free_space"


# Break caught: preflight must not mint an approval with a caller-weakened
# reserve even when the filesystem currently has exactly 10 GiB free.
def test_preflight_rejects_below_global_floor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _valid_paths(tmp_path)
    commands: list[list[str]] = []
    monkeypatch.setattr(rehearsal_safety.sys, "platform", "darwin")
    monkeypatch.setattr(rehearsal_safety.subprocess, "run", _apfs_runner(commands))
    monkeypatch.setattr(
        rehearsal_safety.shutil,
        "disk_usage",
        lambda _path: shutil._ntuple_diskusage(
            2 * MIN_FREE_BYTES,
            MIN_FREE_BYTES,
            MIN_FREE_BYTES,
        ),
    )

    with pytest.raises(RehearsalSafetyError) as caught:
        preflight_rehearsal(
            paths.source,
            paths.rehearsal_dir,
            repository_root=paths.repository_root,
            min_free_bytes=MIN_FREE_BYTES - 1,
        )
    assert caught.value.code == "insufficient_free_space"


# Break caught: using `<` instead of `<=` at the reserve boundary must preserve
# an exact 10 GiB approval and its enforcement provenance.
def test_preflight_accepts_exact_global_floor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _valid_paths(tmp_path)
    commands: list[list[str]] = []
    monkeypatch.setattr(rehearsal_safety.sys, "platform", "darwin")
    monkeypatch.setattr(rehearsal_safety.subprocess, "run", _apfs_runner(commands))
    monkeypatch.setattr(
        rehearsal_safety.shutil,
        "disk_usage",
        lambda _path: shutil._ntuple_diskusage(
            2 * MIN_FREE_BYTES,
            MIN_FREE_BYTES,
            MIN_FREE_BYTES,
        ),
    )

    approved = preflight_rehearsal(
        paths.source,
        paths.rehearsal_dir,
        repository_root=paths.repository_root,
        min_free_bytes=MIN_FREE_BYTES,
    )
    assert approved.free_bytes == MIN_FREE_BYTES


# Break caught: a pre-existing destination could be overwritten by the clone.
def test_preflight_rejects_existing_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _valid_paths(tmp_path)
    (paths.rehearsal_dir / "kreports-rehearsal.db").write_bytes(b"keep")
    commands: list[list[str]] = []
    monkeypatch.setattr(rehearsal_safety.sys, "platform", "darwin")
    monkeypatch.setattr(rehearsal_safety.subprocess, "run", _apfs_runner(commands))
    monkeypatch.setattr(
        rehearsal_safety.shutil,
        "disk_usage",
        lambda _path: shutil._ntuple_diskusage(
            2 * MIN_FREE_BYTES,
            MIN_FREE_BYTES,
            MIN_FREE_BYTES,
        ),
    )

    with pytest.raises(RehearsalSafetyError) as caught:
        preflight_rehearsal(
            paths.source,
            paths.rehearsal_dir,
            repository_root=paths.repository_root,
        )

    assert caught.value.code == "target_exists"


# Break caught: a clone command without -c could copy or mutate data with no
# APFS clone guarantee.
def test_create_apfs_clone_uses_clone_only_and_returns_file_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, preflight, commands = _valid_preflight(tmp_path, monkeypatch)

    clone = create_apfs_clone(preflight)

    assert clone.path == paths.rehearsal_dir / "kreports-rehearsal.db"
    assert clone.sha256 == preflight.source.sha256
    assert clone.inode != preflight.source.inode
    assert clone.path.stat().st_nlink == 1
    clone_commands = [command for command in commands if command[0] == "/bin/cp"]
    assert len(clone_commands) == 1
    assert clone_commands[0][:3] == ["/bin/cp", "-c", str(paths.source)]
    staged_target = Path(clone_commands[0][3])
    assert staged_target.parent.parent == paths.rehearsal_dir
    assert staged_target.parent.name.startswith(".kreports-clone-")


# Break caught: creating the final name between preflight and installation must
# never let this process overwrite or unlink the competing file.
def test_create_apfs_clone_preserves_target_won_by_racer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, preflight, _ = _valid_preflight(tmp_path, monkeypatch)
    final_target = paths.rehearsal_dir / "kreports-rehearsal.db"
    real_link = os.link
    staging_modes: list[int] = []

    def race_before_link(staged: Path, target: Path) -> None:
        staging_modes.append(stat.S_IMODE(Path(staged).parent.stat().st_mode))
        Path(target).write_bytes(b"competing process owns this target")
        real_link(staged, target)

    monkeypatch.setattr(rehearsal_safety.os, "link", race_before_link)

    with pytest.raises(RehearsalSafetyError) as caught:
        create_apfs_clone(preflight)

    assert caught.value.code == "target_exists"
    assert final_target.read_bytes() == b"competing process owns this target"
    assert staging_modes == [0o700]
    assert not list(paths.rehearsal_dir.glob(".kreports-clone-*"))


# Break caught: a genuine approval must still enforce fresh free-space evidence
# immediately before cloning instead of trusting its cached observation.
def test_create_apfs_clone_rechecks_approved_preflight_free_space(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, approved, commands = _valid_preflight(tmp_path, monkeypatch)
    monkeypatch.setattr(
        rehearsal_safety.shutil,
        "disk_usage",
        lambda _path: shutil._ntuple_diskusage(
            2 * MIN_FREE_BYTES,
            MIN_FREE_BYTES + 1,
            MIN_FREE_BYTES - 1,
        ),
    )

    with pytest.raises(RehearsalSafetyError) as caught:
        create_apfs_clone(approved)

    assert caught.value.code == "insufficient_free_space"
    assert not [command for command in commands if command[0] == "/bin/cp"]


# Break caught: reconstructing an equal four-field value must not inherit the
# process-local authority of the exact object returned by preflight.
def test_create_apfs_clone_rejects_reconstructed_equal_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, approved, commands = _valid_preflight(tmp_path, monkeypatch)
    reconstructed = SourcePreflight(
        approved.source,
        approved.rehearsal_dir,
        approved.free_bytes,
        approved.filesystem_type,
    )
    assert reconstructed == approved
    assert reconstructed is not approved

    with pytest.raises(RehearsalSafetyError) as caught:
        create_apfs_clone(reconstructed)

    assert caught.value.code == "unsafe_rehearsal_directory"
    assert not [command for command in commands if command[0] == "/bin/cp"]


# Break caught: dataclass and shallow-copy duplication must not copy the exact
# object identity that carries process-local approval authority.
@pytest.mark.parametrize(
    "duplicate",
    [
        pytest.param(copy.copy, id="copy"),
        pytest.param(lambda value: replace(value), id="dataclasses-replace"),
    ],
)
def test_create_apfs_clone_rejects_copied_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    duplicate,
) -> None:
    _, approved, commands = _valid_preflight(tmp_path, monkeypatch)
    copied = duplicate(approved)
    assert copied == approved
    assert copied is not approved

    with pytest.raises(RehearsalSafetyError) as caught:
        create_apfs_clone(copied)

    assert caught.value.code == "unsafe_rehearsal_directory"
    assert not [command for command in commands if command[0] == "/bin/cp"]


# Break caught: replacing the approved rehearsal path with a protected source
# directory must be rejected instead of trusting a forgeable dataclass.
def test_create_apfs_clone_rejects_forged_unsafe_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, preflight, commands = _valid_preflight(tmp_path, monkeypatch)
    forged = replace(preflight, rehearsal_dir=paths.source.parent)

    with pytest.raises(RehearsalSafetyError) as caught:
        create_apfs_clone(forged)

    assert caught.value.code == "unsafe_rehearsal_directory"
    assert not [command for command in commands if command[0] == "/bin/cp"]


# Break caught: swapping in the real repository root must not turn an unrelated
# reconstructed value into authority to create files there.
def test_create_apfs_clone_rejects_repository_root_forgery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, approved, commands = _valid_preflight(tmp_path, monkeypatch)
    forged = replace(approved, rehearsal_dir=paths.repository_root)

    with pytest.raises(RehearsalSafetyError) as caught:
        create_apfs_clone(forged)

    assert caught.value.code == "unsafe_rehearsal_directory"
    assert not [command for command in commands if command[0] == "/bin/cp"]


# Break caught: retaining a dead weakref registry entry permits a later object
# that reuses the same integer id to acquire stale approval authority.
def test_preflight_approval_registry_removes_dead_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, approved, _ = _valid_preflight(tmp_path, monkeypatch)
    approval_id = id(approved)
    approved_reference = weakref.ref(approved)
    registry = getattr(rehearsal_safety, "_PREFLIGHT_APPROVALS", {})
    assert approval_id in registry

    del approved
    gc.collect()

    assert approved_reference() is None
    assert approval_id not in registry


# Break caught: a source sidecar that appears after approval must stop cloning
# before `/bin/cp -c` is invoked.
def test_create_apfs_clone_rechecks_sidecars_before_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, preflight, commands = _valid_preflight(tmp_path, monkeypatch)
    Path(f"{paths.source}-wal").write_bytes(b"late writer")

    with pytest.raises(RehearsalSafetyError) as caught:
        create_apfs_clone(preflight)

    assert caught.value.code == "source_sidecar_present"
    assert not [command for command in commands if command[0] == "/bin/cp"]


# Break caught: source writes between preflight and clone can make the clone
# differ from the approved immutable witness.
def test_create_apfs_clone_rejects_changed_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, preflight, commands = _valid_preflight(tmp_path, monkeypatch)
    with sqlite3.connect(paths.source) as connection:
        connection.execute("CREATE TABLE changed_after_preflight(value TEXT)")

    with pytest.raises(RehearsalSafetyError) as caught:
        create_apfs_clone(preflight)

    assert caught.value.code == "source_changed"
    assert not [command for command in commands if command[0] == "/bin/cp"]


# Break caught: accepting a failed clone command would permit fallback behavior
# or report a destination that was never safely created.
def test_create_apfs_clone_maps_cp_failure_to_unsupported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, preflight, _ = _valid_preflight(tmp_path, monkeypatch)

    def rejected_clone(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[0] == "/bin/cp":
            raise subprocess.CalledProcessError(1, command, stderr="unsupported")
        return subprocess.CompletedProcess(command, 0, stdout="apfs\n", stderr="")

    monkeypatch.setattr(rehearsal_safety.subprocess, "run", rejected_clone)

    with pytest.raises(RehearsalSafetyError) as caught:
        create_apfs_clone(preflight)

    assert caught.value.code == "clonefile_unsupported"


# Break caught: a later source change or sidecar means the originally approved
# production identity is no longer safe to rely on.
def test_assert_source_unchanged_rejects_metadata_and_sidecar_changes(
    tmp_path: Path,
) -> None:
    paths = _valid_paths(tmp_path)
    expected = inspect_source_database(paths.source)
    with sqlite3.connect(paths.source) as connection:
        connection.execute("CREATE TABLE changed(value TEXT)")

    with pytest.raises(RehearsalSafetyError) as metadata_change:
        assert_source_unchanged(expected)
    assert metadata_change.value.code == "source_changed"

    paths = _valid_paths(tmp_path / "sidecar")
    expected = inspect_source_database(paths.source)
    Path(f"{paths.source}-wal").write_bytes(b"new write")
    with pytest.raises(RehearsalSafetyError) as sidecar_change:
        assert_source_unchanged(expected)
    assert sidecar_change.value.code == "source_changed"


@pytest.mark.skipif(sys.platform != "darwin", reason="requires Darwin APFS clonefile")
def test_create_apfs_clone_with_real_cp_on_apfs(tmp_path: Path) -> None:
    paths = _valid_paths(tmp_path)
    filesystem_type = subprocess.run(
        ["/usr/bin/stat", "-f", "%T", str(paths.rehearsal_dir)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip().lower()
    if filesystem_type != "apfs":
        pytest.skip("temporary directory is not APFS")

    preflight = preflight_rehearsal(
        paths.source,
        paths.rehearsal_dir,
        repository_root=paths.repository_root,
    )
    clone = create_apfs_clone(preflight)

    assert clone.sha256 == preflight.source.sha256
    assert clone.inode != preflight.source.inode
