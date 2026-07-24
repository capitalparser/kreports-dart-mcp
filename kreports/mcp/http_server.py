"""Streamable HTTP transport for the KReports MCP server.

This exposes the same low-level MCP server used by the stdio launcher, but over
HTTP so remote MCP clients such as Claude custom connectors can reach it through
a public HTTPS tunnel or deployed ASGI server.
"""
from __future__ import annotations

import argparse
import logging
import os
from collections.abc import Iterable
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp.server import StreamableHTTPASGIApp
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send
from kreports.mcp.server import server
from kreports.mcp.tools import ALL_TOOLS
from kreports.quality.release_gate import evaluate_release_gate

logger = logging.getLogger("kreports.mcp.http")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_PATH = "/mcp"


class BearerTokenMiddleware:
    """Small ASGI wrapper for static bearer-token protection."""

    def __init__(self, app: ASGIApp, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin1").lower(): value.decode("latin1")
            for key, value in scope.get("headers", [])
        }
        expected = f"Bearer {self.token}"
        if headers.get("authorization") != expected:
            response = PlainTextResponse(
                "Unauthorized",
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


def _split_csv(values: str | Iterable[str] | None) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        raw_values = values.split(",")
    else:
        raw_values = []
        for value in values:
            raw_values.extend(str(value).split(","))
    return [value.strip() for value in raw_values if value and value.strip()]


def _security_settings(
    allowed_hosts: str | Iterable[str] | None = None,
    allowed_origins: str | Iterable[str] | None = None,
) -> TransportSecuritySettings | None:
    hosts = _split_csv(allowed_hosts)
    origins = _split_csv(allowed_origins)
    if not hosts and not origins:
        return None
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )


async def _health(_: Request) -> JSONResponse:
    return JSONResponse(
        {
            "ok": True,
            "name": "kreports",
            "transport": "streamable-http",
            "tool_count": len(ALL_TOOLS),
            "tools": [tool.name for tool in ALL_TOOLS],
        }
    )


async def _ready(_: Request) -> JSONResponse:
    report = evaluate_release_gate("public_runtime")
    return JSONResponse(report, status_code=503 if report["required_failures"] else 200)


def create_app(
    *,
    path: str = DEFAULT_PATH,
    token: str | None = None,
    stateless: bool = False,
    json_response: bool = False,
    allowed_hosts: str | Iterable[str] | None = None,
    allowed_origins: str | Iterable[str] | None = None,
) -> Starlette:
    """Create a Starlette app exposing the existing MCP server over HTTP."""
    if not path.startswith("/"):
        raise ValueError("path must start with '/'")

    session_manager = StreamableHTTPSessionManager(
        app=server,
        json_response=json_response,
        stateless=stateless,
        security_settings=_security_settings(allowed_hosts, allowed_origins),
    )

    mcp_app: ASGIApp = StreamableHTTPASGIApp(session_manager)
    if token:
        mcp_app = BearerTokenMiddleware(mcp_app, token)

    async def root(_: Request) -> JSONResponse:
        return JSONResponse(
            {
                "name": "kreports",
                "mcp_path": path,
                "health_path": "/healthz",
                "ready_path": "/readyz",
                "note": "Connect an MCP client to the configured mcp_path.",
            }
        )

    @asynccontextmanager
    async def lifespan(_: Starlette):
        async with session_manager.run():
            yield

    return Starlette(
        routes=[
            Route("/", endpoint=root, methods=["GET"]),
            Route("/healthz", endpoint=_health, methods=["GET"]),
            Route("/readyz", endpoint=_ready, methods=["GET"]),
            Route(path, endpoint=mcp_app),
        ],
        lifespan=lifespan,
    )


def _effective_token(token: str | None) -> str | None:
    return token or os.environ.get("KREPORTS_MCP_TOKEN")


def run_http(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    path: str = DEFAULT_PATH,
    token: str | None = None,
    allow_unauthenticated: bool = False,
    stateless: bool = False,
    json_response: bool = False,
    allowed_hosts: str | Iterable[str] | None = None,
    allowed_origins: str | Iterable[str] | None = None,
    log_level: str = "info",
) -> None:
    """Run the remote MCP HTTP server with uvicorn."""
    import uvicorn

    auth_token = _effective_token(token)
    if not auth_token and not allow_unauthenticated:
        raise RuntimeError(
            "KREPORTS_MCP_TOKEN is required for remote MCP. "
            "Use --token, set KREPORTS_MCP_TOKEN, or pass --allow-unauthenticated "
            "only for short-lived local/tunnel testing."
        )

    app = create_app(
        path=path,
        token=auth_token,
        stateless=stateless,
        json_response=json_response,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )
    uvicorn.run(app, host=host, port=port, log_level=log_level)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run KReports MCP over Streamable HTTP.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--path", default=DEFAULT_PATH)
    parser.add_argument("--token", default=None, help="Bearer token. Defaults to KREPORTS_MCP_TOKEN.")
    parser.add_argument("--allow-unauthenticated", action="store_true")
    parser.add_argument("--stateless", action="store_true")
    parser.add_argument("--json-response", action="store_true")
    parser.add_argument("--allowed-hosts", default=None, help="Comma-separated Host allowlist.")
    parser.add_argument("--allowed-origins", default=None, help="Comma-separated Origin allowlist.")
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args(argv)
    run_http(
        host=args.host,
        port=args.port,
        path=args.path,
        token=args.token,
        allow_unauthenticated=args.allow_unauthenticated,
        stateless=args.stateless,
        json_response=args.json_response,
        allowed_hosts=args.allowed_hosts,
        allowed_origins=args.allowed_origins,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
