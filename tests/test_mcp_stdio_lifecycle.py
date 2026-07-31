from __future__ import annotations

import asyncio
import hashlib
import os
import signal
import sqlite3
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text


def _patch_stdio_server(monkeypatch, server_module, server_body):
    read_stream = object()
    write_stream = object()

    @asynccontextmanager
    async def fake_stdio_server():
        yield read_stream, write_stream

    monkeypatch.setattr(server_module, "stdio_server", fake_stdio_server)
    monkeypatch.setattr(server_module.server, "run", server_body)
    return read_stream, write_stream


def test_run_disposes_engine_once_after_normal_eof(monkeypatch):
    import kreports.db.engine as engine_module
    import kreports.mcp.server as server_module

    calls: list[str] = []

    async def server_body(*_args):
        calls.append("server")

    read_stream, write_stream = _patch_stdio_server(
        monkeypatch,
        server_module,
        server_body,
    )
    monkeypatch.setattr(
        engine_module,
        "dispose_engine",
        lambda: calls.append("dispose"),
        raising=False,
    )

    asyncio.run(server_module.run())

    assert calls == ["server", "dispose"]
    assert read_stream is not write_stream


def test_run_disposes_engine_once_and_propagates_cancellation(monkeypatch):
    import kreports.db.engine as engine_module
    import kreports.mcp.server as server_module

    calls: list[str] = []

    async def server_body(*_args):
        calls.append("server")
        raise asyncio.CancelledError

    _patch_stdio_server(monkeypatch, server_module, server_body)
    monkeypatch.setattr(
        engine_module,
        "dispose_engine",
        lambda: calls.append("dispose"),
        raising=False,
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(server_module.run())

    assert calls == ["server", "dispose"]


def test_run_disposes_engine_once_and_preserves_ordinary_exception(
    monkeypatch,
):
    import kreports.db.engine as engine_module
    import kreports.mcp.server as server_module

    calls: list[str] = []
    expected = RuntimeError("stdio server failed")

    async def server_body(*_args):
        calls.append("server")
        raise expected

    _patch_stdio_server(monkeypatch, server_module, server_body)
    monkeypatch.setattr(
        engine_module,
        "dispose_engine",
        lambda: calls.append("dispose"),
        raising=False,
    )

    with pytest.raises(RuntimeError) as captured:
        asyncio.run(server_module.run())

    assert captured.value is expected
    assert calls == ["server", "dispose"]


def test_dispose_engine_is_idempotent_on_temporary_database(
    monkeypatch,
    tmp_path,
):
    import kreports.db.engine as engine_module

    database_path = tmp_path / "dispose-idempotency.db"
    temporary_engine = create_engine(f"sqlite:///{database_path}")
    monkeypatch.setattr(engine_module, "engine", temporary_engine)
    with temporary_engine.connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar_one() == 1

    engine_module.dispose_engine()
    engine_module.dispose_engine()

    with temporary_engine.connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar_one() == 1


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX signals")
@pytest.mark.parametrize("outcome", ["success", "cancelled", "error"])
def test_signal_wrapper_restores_exact_previous_handlers(
    monkeypatch,
    outcome,
):
    import kreports.mcp.server as server_module

    expected_error = RuntimeError("stdio failed")

    async def fake_run():
        if outcome == "cancelled":
            raise asyncio.CancelledError
        if outcome == "error":
            raise expected_error

    monkeypatch.setattr(server_module, "run", fake_run)

    def previous_loop_callback(marker):
        assert marker == "previous-loop-handler"

    def previous_raw_handler(_signum, _frame):
        return None

    async def exercise_wrapper():
        loop = asyncio.get_running_loop()
        original_sigint = signal.getsignal(signal.SIGINT)
        original_sigterm = signal.getsignal(signal.SIGTERM)
        try:
            loop.add_signal_handler(
                signal.SIGINT,
                previous_loop_callback,
                "previous-loop-handler",
            )
            signal.signal(signal.SIGTERM, previous_raw_handler)

            if outcome == "error":
                with pytest.raises(RuntimeError) as captured:
                    await server_module._run_with_signal_shutdown()
                assert captured.value is expected_error
            else:
                await server_module._run_with_signal_shutdown()

            restored = loop._signal_handlers[signal.SIGINT]
            assert restored._callback is previous_loop_callback
            assert restored._args == ("previous-loop-handler",)
            assert signal.getsignal(signal.SIGTERM) is previous_raw_handler
        finally:
            loop.remove_signal_handler(signal.SIGINT)
            loop.remove_signal_handler(signal.SIGTERM)
            signal.signal(signal.SIGINT, original_sigint)
            signal.signal(signal.SIGTERM, original_sigterm)

    asyncio.run(exercise_wrapper())


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sqlite_file_state(database_path: Path) -> dict[str, str | None]:
    """Capture the main database and every SQLite sidecar without creating one."""
    paths = {
        "main": database_path,
        "wal": Path(f"{database_path}-wal"),
        "shm": Path(f"{database_path}-shm"),
    }
    return {
        name: _sha256(path) if path.exists() else None
        for name, path in paths.items()
    }


def _wait_for_path(path: Path, process: subprocess.Popen, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        if process.poll() is not None:
            stderr = process.stderr.read() if process.stderr is not None else ""
            raise AssertionError(
                f"probe exited before marker: {process.returncode}: {stderr}"
            )
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for marker: {path}")


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX signals")
def test_sigterm_disposes_read_handle_without_mutating_database(tmp_path):
    database_path = tmp_path / "signal-lifecycle.db"
    open_marker = tmp_path / "connection-open.marker"
    disposed_marker = tmp_path / "engine-disposed.marker"
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == (
            "wal",
        )
        connection.execute(
            "CREATE TABLE lifecycle_probe (value INTEGER NOT NULL)"
        )
        connection.execute("INSERT INTO lifecycle_probe VALUES (1)")
        connection.commit()
        assert connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone() == (
            0,
            0,
            0,
        )
    initial_file_state = _sqlite_file_state(database_path)

    probe = r"""
import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path


def forbidden_os_unlink(*_args, **_kwargs):
    raise AssertionError("application os.unlink is forbidden")


def forbidden_path_unlink(*_args, **_kwargs):
    raise AssertionError("application Path.unlink is forbidden")


os.unlink = forbidden_os_unlink
Path.unlink = forbidden_path_unlink

import kreports.db.engine as engine_module
import kreports.mcp.server as server_module

original_dispose = engine_module.dispose_engine


def marked_dispose():
    original_dispose()
    Path(os.environ["DISPOSED_MARKER"]).write_text("disposed")


@asynccontextmanager
async def fake_stdio():
    yield object(), object()


async def fake_server_run(*_args):
    with engine_module.engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT COUNT(*) FROM lifecycle_probe"
        ).scalar_one() == 1
        Path(os.environ["OPEN_MARKER"]).write_text("open")
        await asyncio.Event().wait()


engine_module.dispose_engine = marked_dispose
server_module.stdio_server = fake_stdio
server_module.server.run = fake_server_run
server_module.main()
"""
    environment = os.environ.copy()
    environment.update(
        {
            "DB_URL": f"sqlite:///{database_path}",
            "DART_API_KEY": "",
            "KREPORTS_RUNTIME_MODE": "readonly",
            "OPEN_MARKER": str(open_marker),
            "DISPOSED_MARKER": str(disposed_marker),
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        }
    )
    process = subprocess.Popen(
        [sys.executable, "-c", probe],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_path(open_marker, process, timeout=5)
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=5) == 0
        assert disposed_marker.read_text() == "disposed"
        assert _sqlite_file_state(database_path) == initial_file_state
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_readonly_engine_rejects_committed_uncheckpointed_wal_without_touching_files(
    tmp_path,
):
    """Catch a readonly engine returning stale WAL data or mutating its sidecars."""
    database_path = tmp_path / "uncheckpointed-wal.db"
    outcome_marker = tmp_path / "readonly-outcome.marker"
    writer = sqlite3.connect(database_path)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE lifecycle_probe (value INTEGER NOT NULL)")
        writer.execute("INSERT INTO lifecycle_probe VALUES (1)")
        writer.commit()
        assert Path(f"{database_path}-wal").stat().st_size > 0
        initial_file_state = _sqlite_file_state(database_path)

        probe = r"""
import os
from pathlib import Path

import kreports.db.engine as engine_module

try:
    with engine_module.engine.connect() as connection:
        connection.exec_driver_sql("SELECT COUNT(*) FROM lifecycle_probe").scalar_one()
except Exception as error:
    Path(os.environ["OUTCOME_MARKER"]).write_text(str(error))
else:
    Path(os.environ["OUTCOME_MARKER"]).write_text("unexpected_read")
"""
        environment = os.environ.copy()
        environment.update(
            {
                "DB_URL": f"sqlite:///{database_path}",
                "DART_API_KEY": "",
                "KREPORTS_RUNTIME_MODE": "readonly",
                "OUTCOME_MARKER": str(outcome_marker),
                "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
            }
        )
        result = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
        assert result.returncode == 0, result.stderr
        assert outcome_marker.read_text() == "runtime_db_unavailable:uncheckpointed_wal"
        assert _sqlite_file_state(database_path) == initial_file_state
    finally:
        writer.close()


def test_readonly_engine_rejects_hot_rollback_journal_without_touching_files(
    tmp_path,
):
    """Catch immutable readonly startup ignoring a recovery-required journal."""
    database_path = tmp_path / "hot-rollback-journal.db"
    outcome_marker = tmp_path / "rollback-journal-outcome.marker"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE lifecycle_probe (value INTEGER NOT NULL)")
        connection.execute("INSERT INTO lifecycle_probe VALUES (1)")
        connection.commit()
    Path(f"{database_path}-journal").write_bytes(b"hot rollback journal")
    initial_file_state = {
        **_sqlite_file_state(database_path),
        "journal": _sha256(Path(f"{database_path}-journal")),
    }

    probe = r"""
import os
from pathlib import Path

import kreports.db.engine as engine_module

try:
    with engine_module.engine.connect() as connection:
        connection.exec_driver_sql("SELECT COUNT(*) FROM lifecycle_probe").scalar_one()
except Exception as error:
    Path(os.environ["OUTCOME_MARKER"]).write_text(str(error))
else:
    Path(os.environ["OUTCOME_MARKER"]).write_text("unexpected_read")
"""
    environment = os.environ.copy()
    environment.update(
        {
            "DB_URL": f"sqlite:///{database_path}",
            "DART_API_KEY": "",
            "KREPORTS_RUNTIME_MODE": "readonly",
            "OUTCOME_MARKER": str(outcome_marker),
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        }
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr
    assert outcome_marker.read_text() == "runtime_db_unavailable:hot_rollback_journal"
    assert {
        **_sqlite_file_state(database_path),
        "journal": _sha256(Path(f"{database_path}-journal")),
    } == initial_file_state


def test_readonly_sqlite_file_uri_normalizes_percent_encoded_special_path(
    tmp_path,
    monkeypatch,
):
    """Catch a file: URI that loses a space, hash, or query character on reopen."""
    from kreports.db.engine import _readonly_sqlite_database_path

    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")
    database_path = tmp_path / "snapshot % # question?.db"
    encoded_file_uri = database_path.as_uri().removeprefix("file:")
    configured_url = (
        f"sqlite:///file:{encoded_file_uri}?mode=rw&uri=true"
    )

    assert _readonly_sqlite_database_path(configured_url) == database_path.resolve()


def test_readonly_sqlite_uri_rejects_query_parameters_it_cannot_preserve(
    tmp_path,
    monkeypatch,
):
    """Catch readonly startup silently dropping a configured SQLite URI option."""
    from kreports.db.engine import (
        ReadonlySQLiteConfigurationError,
        _readonly_sqlite_database_path,
    )

    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")
    database_path = tmp_path / "configured.db"
    configured_url = (
        f"sqlite:///file:{database_path.as_uri().removeprefix('file:')}"
        "?mode=rw&cache=shared&uri=true"
    )

    with pytest.raises(ReadonlySQLiteConfigurationError):
        _readonly_sqlite_database_path(configured_url)


def test_readonly_engine_overrides_configured_file_uri_write_mode(tmp_path):
    """Catch a configured SQLite `mode=rw` URI bypassing readonly enforcement."""
    database_path = tmp_path / "configured % # writable?.db"
    outcome_marker = tmp_path / "readonly-uri-outcome.marker"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE lifecycle_probe (value INTEGER NOT NULL)")
        connection.execute("INSERT INTO lifecycle_probe VALUES (1)")
        connection.commit()
    initial_file_state = _sqlite_file_state(database_path)
    configured_url = (
        f"sqlite:///file:{database_path.as_uri().removeprefix('file:')}"
        "?mode=rw&uri=true"
    )
    probe = r"""
import os
from pathlib import Path

import kreports.db.engine as engine_module

with engine_module.engine.connect() as connection:
    count = connection.exec_driver_sql("SELECT COUNT(*) FROM lifecycle_probe").scalar_one()
    try:
        connection.exec_driver_sql("INSERT INTO lifecycle_probe VALUES (2)")
    except Exception as error:
        Path(os.environ["OUTCOME_MARKER"]).write_text(f"{count}|{error}")
    else:
        Path(os.environ["OUTCOME_MARKER"]).write_text("unexpected_write")
"""
    environment = os.environ.copy()
    environment.update(
        {
            "DB_URL": configured_url,
            "DART_API_KEY": "",
            "KREPORTS_RUNTIME_MODE": "readonly",
            "OUTCOME_MARKER": str(outcome_marker),
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        }
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr
    assert outcome_marker.read_text().startswith("1|")
    assert "readonly" in outcome_marker.read_text().lower()
    assert _sqlite_file_state(database_path) == initial_file_state
