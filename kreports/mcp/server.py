"""
kreports.mcp.server — KReports MCP stdio 서버.

Claude Desktop / Claude Code 등 MCP 클라이언트에 stdio로 연결된다.
"""
from __future__ import annotations

import asyncio
import json
import logging
import signal
from typing import Any

from mcp.server import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from kreports.mcp.catalog_extensions import install_catalog_extensions
from kreports.mcp.dispatch import dispatch_tool, list_mcp_tools
from kreports.mcp.prompts import get_prompt, mcp_prompts
from kreports.mcp.resources import (
    mcp_resource_templates,
    mcp_resources,
    render_resource,
    resource_mime_type,
)
from kreports.runtime import runtime_mode

install_catalog_extensions()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("kreports.mcp")

server = Server("kreports")


@server.list_tools()
async def list_tools() -> list[Tool]:
    """MCP 클라이언트에 사용 가능한 도구 목록을 알린다."""
    return list_mcp_tools()


@server.list_resources()
async def handle_list_resources():
    return mcp_resources()


@server.list_resource_templates()
async def handle_list_resource_templates():
    return mcp_resource_templates()


@server.read_resource()
async def handle_read_resource(uri):
    return [
        ReadResourceContents(
            content=render_resource(uri),
            mime_type=resource_mime_type(uri),
        )
    ]


@server.list_prompts()
async def handle_list_prompts():
    return mcp_prompts()


@server.get_prompt()
async def handle_get_prompt(name: str, arguments: dict[str, str] | None):
    return get_prompt(name, arguments)


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict):
    """도구 실행. 결과는 structured content로 반환."""
    logger.info(
        "call_tool: %s runtime_mode=%s arg_keys=%s",
        name,
        runtime_mode(),
        sorted((arguments or {}).keys()),
    )
    envelope = dispatch_tool(name, arguments)
    result = envelope.model_dump(mode="json")
    result_json = json.dumps(result, ensure_ascii=False)
    logger.info("call_tool_done: %s bytes=%d", name, len(result_json))
    answer = result.get("answer")
    if isinstance(answer, str) and answer.strip():
        return [TextContent(type="text", text=answer)], result
    return result


async def run() -> None:
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
    finally:
        from kreports.db.engine import dispose_engine

        dispose_engine()


def _loop_signal_handle(
    loop: asyncio.AbstractEventLoop,
    candidate: signal.Signals,
) -> asyncio.Handle | None:
    """Return the registered asyncio Handle when the loop exposes it."""
    handlers = getattr(loop, "_signal_handlers", None)
    if not isinstance(handlers, dict):
        return None
    handle = handlers.get(candidate)
    return handle if isinstance(handle, asyncio.Handle) else None


def _restore_signal_handler(
    loop: asyncio.AbstractEventLoop,
    candidate: signal.Signals,
    previous_raw_handler: Any,
    previous_loop_handle: asyncio.Handle | None,
) -> None:
    loop.remove_signal_handler(candidate)
    if previous_loop_handle is None:
        signal.signal(candidate, previous_raw_handler)
        return

    callback = previous_loop_handle._callback
    args = previous_loop_handle._args
    loop.add_signal_handler(candidate, callback, *args)
    # add_signal_handler rebuilds a Handle with the current context. Reuse the
    # original Handle so its callback, arguments, and context remain exact.
    handlers = vars(loop)["_signal_handlers"]
    handlers[candidate] = previous_loop_handle


async def _run_with_signal_shutdown() -> None:
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    installed: list[
        tuple[signal.Signals, Any, asyncio.Handle | None]
    ] = []
    if task is None:
        raise RuntimeError("MCP signal wrapper requires a running task")
    for candidate in (signal.SIGINT, signal.SIGTERM):
        previous_raw_handler = signal.getsignal(candidate)
        previous_loop_handle = _loop_signal_handle(loop, candidate)
        try:
            loop.add_signal_handler(candidate, task.cancel)
        except (NotImplementedError, RuntimeError):
            continue
        installed.append(
            (
                candidate,
                previous_raw_handler,
                previous_loop_handle,
            )
        )
    try:
        await run()
    except asyncio.CancelledError:
        return
    finally:
        for (
            candidate,
            previous_raw_handler,
            previous_loop_handle,
        ) in reversed(installed):
            _restore_signal_handler(
                loop,
                candidate,
                previous_raw_handler,
                previous_loop_handle,
            )


def main() -> None:
    asyncio.run(_run_with_signal_shutdown())


if __name__ == "__main__":
    main()
