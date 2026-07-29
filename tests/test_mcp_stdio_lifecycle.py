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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    initial_sha256 = _sha256(database_path)

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
        assert _sha256(database_path) == initial_sha256
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
