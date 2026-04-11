"""
kreports.mcp.server — KReports MCP stdio 서버.

Claude Desktop / Claude Code 등 MCP 클라이언트에 stdio로 연결된다.
"""
from __future__ import annotations

import asyncio
import logging

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from kreports.mcp.tools import ALL_TOOLS, call_tool

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("kreports.mcp")

server = Server("kreports")


@server.list_tools()
async def list_tools() -> list[Tool]:
    """MCP 클라이언트에 사용 가능한 도구 목록을 알린다."""
    return ALL_TOOLS


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict) -> list[TextContent]:
    """도구 실행. 결과는 TextContent JSON으로 반환."""
    logger.info("call_tool: %s args=%s", name, arguments)
    result_json = call_tool(name, arguments)
    return [TextContent(type="text", text=result_json)]


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
