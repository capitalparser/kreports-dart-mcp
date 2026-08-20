"""
kreports.mcp.server — KReports MCP stdio 서버.

Claude Desktop / Claude Code 등 MCP 클라이언트에 stdio로 연결된다.
"""
from __future__ import annotations

import asyncio
import json
import logging

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
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
