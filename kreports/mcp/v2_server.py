"""Optional MCP SDK v2 server for the 2026-07-28 protocol revision.

The legacy ``kreports.mcp.server`` remains available in an MCP 1.x environment.
This module is a sidecar migration path: it advertises the same 34 tools with
native structured output, stateless HTTP, MRTR form requests, cache hints, and
application-only state/page metadata.
"""
from __future__ import annotations

import argparse
import asyncio
from importlib.metadata import PackageNotFoundError, version
import logging
import os
import secrets
from typing import Any


def _require_mcp_v2() -> None:
    try:
        installed = version("mcp")
    except PackageNotFoundError as exc:
        raise RuntimeError(
            "MCP SDK v2 is not installed. Use requirements-mcp-v2.txt."
        ) from exc
    try:
        major = int(installed.split(".", 1)[0])
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"Cannot interpret MCP SDK version {installed!r}"
        ) from exc
    if major < 2:
        raise RuntimeError(
            "kreports.mcp.v2_server requires MCP SDK >=2,<3. "
            "The default KReports runtime intentionally remains on mcp<2."
        )


_require_mcp_v2()

import mcp.server.stdio
from mcp.server import Server, ServerRequestContext
from mcp.server.request_state import (
    RequestStateBoundary,
    RequestStateSecurity,
)
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ElicitRequest,
    ElicitRequestFormParams,
    ElicitResult,
    GetPromptRequestParams,
    GetPromptResult,
    InputRequiredResult,
    ListPromptsResult,
    ListResourcesResult,
    ListResourceTemplatesResult,
    ListToolsResult,
    PaginatedRequestParams,
    Prompt,
    PromptArgument,
    ReadResourceRequestParams,
    ReadResourceResult,
    Resource,
    ResourceTemplate,
    TextContent,
    TextResourceContents,
    Tool,
)
from pydantic import ValidationError
from starlette.types import ASGIApp, Receive, Scope, Send

from kreports.conversation.contracts import InteractionRequest
from kreports.mcp.catalog import TOOL_CATALOG
from kreports.mcp.catalog_extensions import install_catalog_extensions
from kreports.mcp.contracts import (
    AnswerEnvelopeV1,
    build_answer_envelope,
)
from kreports.mcp.dispatch import legacy_result
from kreports.mcp.mcp_v2_runtime import (
    conversation_orchestrator,
    conversation_store,
    execution_coordinator,
    extract_page_rows,
    interactive_requested,
    page_answer,
    recent_turns,
    request_identity,
    save_preferences_requested,
    supplied_page_token,
    supplied_state_handle,
)
from kreports.mcp.prompts import (
    PromptRequestError,
    get_prompt,
    list_prompts,
)
from kreports.mcp.resources import (
    ResourceRequestError,
    list_resource_templates,
    list_resources,
    render_resource,
    resource_mime_type,
)
from kreports.mcp.schema_utils import legacy_compatible_schema


logger = logging.getLogger("kreports.mcp.v2")
_SERVER_VERSION = "0.2.0"
_MODERN_VERSION = "2026-07-28"
_PEER_INTERACTIVE_TOOLS = {
    "select_peer_group",
    "compare_to_industry_multi",
    "compare_peer_accounting_notes",
}


install_catalog_extensions()


def _answer_schema() -> dict[str, Any]:
    return AnswerEnvelopeV1.model_json_schema(
        mode="serialization",
        by_alias=True,
    )


def _tool_list() -> list[Tool]:
    output_schema = _answer_schema()
    return [
        Tool(
            name=name,
            description=spec.description,
            input_schema=legacy_compatible_schema(
                spec.input_model,
                name,
            ),
            output_schema=output_schema,
        )
        for name, spec in TOOL_CATALOG.items()
    ]


async def _list_tools(
    _ctx: ServerRequestContext,
    _params: PaginatedRequestParams | None,
) -> ListToolsResult:
    return ListToolsResult(
        tools=_tool_list(),
        ttl_ms=60_000,
        cache_scope="public",
    )


def _protocol_version(ctx: Any) -> str:
    direct = getattr(ctx, "protocol_version", None)
    if direct:
        return str(direct)
    session = getattr(ctx, "session", None)
    return str(getattr(session, "protocol_version", "") or "")


def _supports_input_required(ctx: Any) -> bool:
    value = _protocol_version(ctx)
    return bool(value and value >= _MODERN_VERSION)


def _interaction_elicit_request(
    interaction: InteractionRequest,
) -> ElicitRequest:
    return ElicitRequest(
        params=ElicitRequestFormParams(
            message=interaction.message,
            requested_schema=interaction.requested_schema(),
        )
    )


def _accepted_elicitation(
    params: CallToolRequestParams,
    interaction_id: str,
) -> dict[str, Any] | None:
    value = (params.input_responses or {}).get(interaction_id)
    if not isinstance(value, ElicitResult):
        return None
    if str(value.action) != "accept" or value.content is None:
        return {}
    return dict(value.content)


def _cancelled_envelope(tool_name: str) -> AnswerEnvelopeV1:
    answer = "선택을 취소했습니다. 필요한 경우 비교 기준을 다시 선택해 주세요."
    return build_answer_envelope(
        tool_name,
        {
            "answer": answer,
            "verdict": "limited",
            "data_quality": {
                "status": "limited",
                "dataset_version": "conversation",
                "schema_version": "kreports.interaction.v1",
                "limitations": [
                    "사용자가 비교 기준 선택을 취소했습니다."
                ],
            },
            "next_checks": [
                "비교 기준을 다시 선택해 분석을 시작할 수 있습니다."
            ],
        },
    )


def _legacy_choice_envelope(
    tool_name: str,
    interaction: InteractionRequest,
) -> AnswerEnvelopeV1:
    options = []
    for field in interaction.fields:
        if field.options:
            options.append(
                f"{field.label}: "
                + ", ".join(option.label for option in field.options)
            )
        else:
            options.append(f"{field.label}: 예 / 아니오")
    answer = (
        f"{interaction.message}\n\n"
        + "\n".join(f"- {line}" for line in options)
        + "\n\n선택 내용을 자연어로 알려주시면 같은 분석을 이어서 수행합니다."
    )
    return build_answer_envelope(
        tool_name,
        {
            "answer": answer,
            "verdict": "limited",
            "data_quality": {
                "status": "limited",
                "dataset_version": "conversation",
                "schema_version": "kreports.interaction.v1",
                "limitations": [
                    "현재 클라이언트는 선택형 화면을 지원하지 않아 "
                    "텍스트 선택지를 제공합니다."
                ],
            },
        },
    )


def _page_envelope(
    tool_name: str,
    page: dict[str, Any],
) -> AnswerEnvelopeV1:
    return build_answer_envelope(
        tool_name,
        {
            "answer": page_answer(page),
            "verdict": "usable",
            "data_quality": {
                "status": "usable",
                "dataset_version": "stored-result",
                "schema_version": "kreports.page.v1",
                "limitations": [],
            },
        },
    )


def _result_meta(
    *,
    state_handle: str | None,
    task_id: str | None,
    result_ref: str | None,
    page_token: str | None,
    page: dict[str, Any] | None,
    context_snapshot: dict[str, Any] | None,
    cache_hit: bool | None = None,
    shared_execution: bool | None = None,
    duration_ms: float | None = None,
) -> dict[str, Any]:
    return {
        "io.kreports/conversation": {
            "stateHandle": state_handle,
            "taskId": task_id,
            "resultRef": result_ref,
            "pageToken": page_token,
            "page": page,
            "contextSnapshot": context_snapshot,
        },
        "io.kreports/performance": {
            "cacheHit": cache_hit,
            "sharedExecution": shared_execution,
            "durationMs": duration_ms,
        },
    }


def _call_result(
    envelope: AnswerEnvelopeV1,
    *,
    meta: dict[str, Any] | None = None,
    is_error: bool = False,
) -> CallToolResult:
    payload = envelope.model_dump(
        mode="json",
        by_alias=True,
        exclude_none=False,
    )
    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=envelope.answer or envelope.verdict,
            )
        ],
        structured_content=payload,
        is_error=is_error,
        _meta=meta or {},
    )


async def _call_tool(
    ctx: ServerRequestContext,
    params: CallToolRequestParams,
) -> CallToolResult | InputRequiredResult:
    name = str(params.name)
    if name not in TOOL_CATALOG:
        envelope = build_answer_envelope(
            name,
            {
                "error": "지원하지 않는 기능입니다.",
                "answer": "지원하지 않는 기능입니다.",
            },
        )
        return _call_result(envelope, is_error=True)

    identity = request_identity(ctx)
    arguments = dict(params.arguments or {})

    page_token = supplied_page_token(ctx)
    if page_token:
        try:
            page = conversation_store.get_page(
                page_token,
                identity,
            )
        except Exception:
            envelope = build_answer_envelope(
                name,
                {
                    "error": "페이지 정보가 만료되었거나 유효하지 않습니다.",
                    "answer": (
                        "페이지 정보가 만료되었습니다. "
                        "같은 조건으로 결과를 다시 조회해 주세요."
                    ),
                },
            )
            return _call_result(envelope, is_error=True)
        envelope = _page_envelope(name, page)
        return _call_result(
            envelope,
            meta=_result_meta(
                state_handle=supplied_state_handle(ctx),
                task_id=None,
                result_ref=page["result_id"],
                page_token=page["pagination"].get(
                    "next_page_token"
                ),
                page=page,
                context_snapshot=None,
            ),
        )

    state_handle = supplied_state_handle(ctx)
    task_id: str | None = None

    accepted = _accepted_elicitation(
        params,
        "peer_preferences",
    )
    if accepted is not None:
        request_state = getattr(params, "request_state", None)
        state_handle = str(request_state or state_handle or "")
        if not state_handle:
            envelope = build_answer_envelope(
                name,
                {
                    "error": "선택 상태가 만료되었습니다.",
                    "answer": (
                        "선택 상태가 만료되었습니다. "
                        "비교 기준을 다시 선택해 주세요."
                    ),
                },
            )
            return _call_result(envelope, is_error=True)
        if not accepted:
            return _call_result(_cancelled_envelope(name))
        try:
            arguments, state = conversation_orchestrator.apply_choices(
                content=accepted,
                original_arguments=arguments,
                identity=identity,
                state_handle=state_handle,
                save_as_preference=save_preferences_requested(ctx),
            )
            task_id = state.active_task_id
        except (ValidationError, ValueError, PermissionError) as exc:
            envelope = build_answer_envelope(
                name,
                {
                    "error": "선택한 비교 기준을 적용할 수 없습니다.",
                    "answer": (
                        "선택한 비교 기준을 적용할 수 없습니다. "
                        "항목을 다시 선택해 주세요."
                    ),
                    "limitations": [type(exc).__name__],
                },
            )
            return _call_result(envelope, is_error=True)
    elif name in _PEER_INTERACTIVE_TOOLS:
        prepared = conversation_orchestrator.prepare(
            tool_name=name,
            arguments=arguments,
            identity=identity,
            state_handle=state_handle,
            interactive=interactive_requested(ctx),
        )
        state_handle = prepared.state_handle
        task_id = prepared.task_id
        if prepared.interaction is not None:
            if _supports_input_required(ctx):
                return InputRequiredResult(
                    input_requests={
                        prepared.interaction.interaction_id:
                        _interaction_elicit_request(
                            prepared.interaction
                        )
                    },
                    request_state=state_handle,
                )
            return _call_result(
                _legacy_choice_envelope(
                    name,
                    prepared.interaction,
                ),
                meta=_result_meta(
                    state_handle=state_handle,
                    task_id=task_id,
                    result_ref=None,
                    page_token=None,
                    page=None,
                    context_snapshot=None,
                ),
            )
        arguments = prepared.arguments or arguments
    elif state_handle:
        try:
            state = conversation_store.get_state(
                state_handle,
                identity,
            )
            task_id = state.active_task_id
        except Exception:
            state_handle = None

    async def run_domain() -> dict[str, Any]:
        return await asyncio.to_thread(
            legacy_result,
            name,
            arguments,
        )

    result, evidence = await execution_coordinator.execute(
        tool_name=name,
        arguments=arguments,
        runner=run_domain,
    )
    envelope = build_answer_envelope(name, result)
    page_rows = extract_page_rows(name, result)
    result_ref: str | None = None
    next_page_token: str | None = None
    page: dict[str, Any] | None = None
    context_payload: dict[str, Any] | None = None

    if state_handle:
        try:
            state = conversation_store.get_state(
                state_handle,
                identity,
            )
            if task_id is None:
                task_id = state.active_task_id
            if page_rows:
                result_ref, first_token = (
                    conversation_store.store_result(
                        identity=identity,
                        rows=page_rows,
                        metadata={
                            "tool": name,
                            "subject": result.get("subject"),
                            "query": result.get("query"),
                        },
                        page_size=5,
                    )
                )
                if first_token:
                    page = conversation_store.get_page(
                        first_token,
                        identity,
                    )
                    next_page_token = (
                        page["pagination"].get(
                            "next_page_token"
                        )
                    )
            if task_id and result_ref:
                state = conversation_orchestrator.record_result(
                    state,
                    task_id=task_id,
                    result_ref=result_ref,
                    result_kind=name,
                )
                state = conversation_store.save_state(
                    state_handle,
                    identity,
                    state,
                )
            snapshot = conversation_orchestrator.compact_context(
                state,
                recent_turns=recent_turns(ctx),
            )
            context_payload = snapshot.model_dump(
                mode="json",
                exclude_none=True,
            )
        except Exception as exc:
            logger.warning(
                "conversation state enrichment failed: %s",
                type(exc).__name__,
            )

    return _call_result(
        envelope,
        meta=_result_meta(
            state_handle=state_handle,
            task_id=task_id,
            result_ref=result_ref,
            page_token=next_page_token,
            page=page,
            context_snapshot=context_payload,
            cache_hit=evidence.cache_hit,
            shared_execution=evidence.shared_execution,
            duration_ms=evidence.duration_ms,
        ),
        is_error=envelope.data_quality.status == "error",
    )


async def _list_resources(
    _ctx: ServerRequestContext,
    _params: PaginatedRequestParams | None,
) -> ListResourcesResult:
    return ListResourcesResult(
        resources=[
            Resource(
                uri=item.uri,
                name=item.name,
                description=item.description,
                mime_type=item.mime_type,
            )
            for item in list_resources()
        ],
        ttl_ms=60_000,
        cache_scope="private",
    )


async def _list_resource_templates(
    _ctx: ServerRequestContext,
    _params: PaginatedRequestParams | None,
) -> ListResourceTemplatesResult:
    return ListResourceTemplatesResult(
        resource_templates=[
            ResourceTemplate(
                uri_template=item.uri_template,
                name=item.name,
                description=item.description,
                mime_type=item.mime_type,
            )
            for item in list_resource_templates()
        ],
        ttl_ms=60_000,
        cache_scope="private",
    )


async def _read_resource(
    _ctx: ServerRequestContext,
    params: ReadResourceRequestParams,
) -> ReadResourceResult:
    try:
        text = await asyncio.to_thread(
            render_resource,
            params.uri,
        )
        mime_type = resource_mime_type(params.uri)
    except ResourceRequestError:
        text = "요청한 자료를 찾을 수 없습니다."
        mime_type = "text/plain"
    return ReadResourceResult(
        contents=[
            TextResourceContents(
                uri=params.uri,
                text=text,
                mime_type=mime_type,
            )
        ],
        ttl_ms=5_000,
        cache_scope="private",
    )


async def _list_prompts(
    _ctx: ServerRequestContext,
    _params: PaginatedRequestParams | None,
) -> ListPromptsResult:
    return ListPromptsResult(
        prompts=[
            Prompt(
                name=item.name,
                description=item.description,
                arguments=[
                    PromptArgument(
                        name="company",
                        description=(
                            "회사명, 종목코드 또는 DART 회사코드"
                        ),
                        required=True,
                    ),
                    PromptArgument(
                        name="year",
                        description="사업연도",
                        required=True,
                    ),
                ],
            )
            for item in list_prompts()
        ],
        ttl_ms=60_000,
        cache_scope="public",
    )


async def _get_prompt(
    _ctx: ServerRequestContext,
    params: GetPromptRequestParams,
) -> GetPromptResult:
    try:
        return get_prompt(
            params.name,
            params.arguments,
        )
    except PromptRequestError as exc:
        raise ValueError(
            "요청한 안내 템플릿을 만들 수 없습니다."
        ) from exc


server = Server(
    "KReports",
    version=_SERVER_VERSION,
    title="KReports",
    description=(
        "한국 DART 공시 기반 재무·감사·주석 분석 서버"
    ),
    instructions=(
        "사용자의 질문에 직접 답하고, 비교회사 목록은 5개씩 보여주며, "
        "중요 판단 전 원 공시 링크와 자료 범위를 확인한다."
    ),
    on_list_tools=_list_tools,
    on_call_tool=_call_tool,
    on_list_resources=_list_resources,
    on_list_resource_templates=_list_resource_templates,
    on_read_resource=_read_resource,
    on_list_prompts=_list_prompts,
    on_get_prompt=_get_prompt,
)
server.extensions["io.kreports/conversation"] = {
    "version": "1.0",
    "inputRequired": True,
    "explicitStateHandle": True,
    "fiveCompanyPages": True,
    "contextSnapshot": "kreports.context.v1",
}


def _request_state_key() -> bytes:
    raw = os.environ.get("KREPORTS_MCP_REQUEST_STATE_KEY")
    if raw is None:
        logger.warning(
            "KREPORTS_MCP_REQUEST_STATE_KEY is not set; "
            "MRTR requestState survives only this process lifetime."
        )
        return secrets.token_bytes(32)
    value = raw.encode("utf-8")
    if len(value) < 32:
        raise RuntimeError(
            "KREPORTS_MCP_REQUEST_STATE_KEY must be at least "
            "32 UTF-8 bytes"
        )
    return value


server.middleware.append(
    RequestStateBoundary(
        RequestStateSecurity(
            keys=[_request_state_key()],
        ),
        default_audience=server.name,
    )
)


class BearerTokenMiddleware:
    def __init__(self, app: ASGIApp, token: str):
        self.app = app
        self.token = token

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {
            key.decode("latin1").lower():
            value.decode("latin1")
            for key, value in scope.get("headers", [])
        }
        if headers.get("authorization") != (
            f"Bearer {self.token}"
        ):
            from starlette.responses import PlainTextResponse

            response = PlainTextResponse(
                "Unauthorized",
                status_code=401,
                headers={
                    "WWW-Authenticate": "Bearer"
                },
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _split_csv(value: str | None) -> list[str]:
    return [
        item.strip()
        for item in str(value or "").split(",")
        if item.strip()
    ]


def create_http_app(
    *,
    host: str = "127.0.0.1",
    path: str = "/mcp",
    token: str | None = None,
) -> ASGIApp:
    hosts = _split_csv(
        os.environ.get("KREPORTS_MCP_ALLOWED_HOSTS")
    )
    origins = _split_csv(
        os.environ.get("KREPORTS_MCP_ALLOWED_ORIGINS")
    )
    transport_security = (
        TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=hosts,
            allowed_origins=origins,
        )
        if hosts or origins
        else None
    )
    app: ASGIApp = server.streamable_http_app(
        streamable_http_path=path,
        json_response=True,
        stateless_http=True,
        transport_security=transport_security,
        host=host,
    )
    auth_token = token or os.environ.get(
        "KREPORTS_MCP_TOKEN"
    )
    if auth_token:
        app = BearerTokenMiddleware(
            app,
            auth_token,
        )
    elif host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError(
            "KREPORTS_MCP_TOKEN is required when binding MCP v2 "
            "outside localhost"
        )
    return app


async def run_stdio() -> None:
    async with mcp.server.stdio.stdio_server() as (
        read_stream,
        write_stream,
    ):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def run_http(
    *,
    host: str,
    port: int,
    path: str,
    token: str | None,
) -> None:
    import uvicorn

    uvicorn.run(
        create_http_app(
            host=host,
            path=path,
            token=token,
        ),
        host=host,
        port=port,
        log_level=os.environ.get(
            "KREPORTS_LOG_LEVEL",
            "info",
        ).lower(),
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run the optional KReports MCP SDK v2 adapter."
        )
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "http"),
        default="stdio",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8766,
    )
    parser.add_argument(
        "--path",
        default="/mcp",
    )
    parser.add_argument(
        "--token",
        default=None,
    )
    args = parser.parse_args(argv)
    if args.transport == "stdio":
        asyncio.run(run_stdio())
    else:
        run_http(
            host=args.host,
            port=args.port,
            path=args.path,
            token=args.token,
        )


if __name__ == "__main__":
    main()
