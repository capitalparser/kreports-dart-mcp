from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

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
