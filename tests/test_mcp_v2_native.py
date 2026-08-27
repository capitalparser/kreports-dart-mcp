from __future__ import annotations

import asyncio
from importlib.metadata import PackageNotFoundError, version

import pytest


def _mcp_major() -> int:
    try:
        return int(version("mcp").split(".", 1)[0])
    except (PackageNotFoundError, TypeError, ValueError):
        return 0


if _mcp_major() < 2:
    pytest.skip(
        "native MCP v2 tests require requirements-mcp-v2.txt",
        allow_module_level=True,
    )

from mcp import Client
from mcp.types import ElicitRequestFormParams, ElicitResult


def _run(coro):
    return asyncio.run(coro)


def _fixture_result(arguments: dict) -> dict:
    peers = [
        {
            "corp_code": f"{index:08d}",
            "corp_name": f"회사 {index}",
            "stock_code": f"{index:06d}",
            "market": "KOSPI",
            "induty_code": "26410",
            "total_assets": index * 100_000_000,
            "revenue": index * 80_000_000,
            "include_reasons": ["same_ksic_prefix"],
        }
        for index in range(1, 8)
    ]
    return {
        "subject": {
            "corp_code": "00126380",
            "corp_name": "삼성전자",
            "stock_code": "005930",
            "market": "KOSPI",
        },
        "selection_policy": {
            "requested_year": arguments.get("year"),
            "resolved_year": arguments.get("year") or 2024,
            "fs_div_used": arguments.get("fs_strategy") or "CFS",
            "criteria_applied": arguments.get("peer_criteria") or {},
        },
        "peer_count": len(peers),
        "returned_peer_count": len(peers),
        "statistical_member_count": len(peers),
        "confidence": "medium",
        "peers": peers,
        "answer": (
            "2024년 연결재무제표 기준으로 업종과 규모가 유사한 "
            "상장사 7개를 선정했습니다."
        ),
        "data_quality": {
            "status": "usable",
            "dataset_version": "fixture-v1",
            "schema_version": "fixture-v1",
            "covered_years": [2024],
            "limitations": [],
        },
    }


def test_v2_server_discovers_34_structured_tools_and_extension():
    from kreports.mcp.v2_server import server

    async def scenario():
        async with Client(server) as client:
            listing = await client.list_tools()
            return client.protocol_version, client.server_capabilities, listing

    protocol_version, capabilities, listing = _run(scenario())

    assert protocol_version == "2026-07-28"
    assert len(listing.tools) == 34
    assert all(tool.output_schema for tool in listing.tools)
    assert (
        capabilities.extensions["io.kreports/conversation"]["inputRequired"]
        is True
    )


def test_v2_mrtr_poll_applies_choices_and_returns_structured_output(monkeypatch):
    import kreports.mcp.v2_server as v2_server

    calls: list[dict] = []

    def fake_legacy_result(name: str, arguments: dict) -> dict:
        assert name == "select_peer_group"
        calls.append(dict(arguments))
        return _fixture_result(arguments)

    monkeypatch.setattr(v2_server, "legacy_result", fake_legacy_result)

    async def elicitation_callback(_context, params):
        assert isinstance(params, ElicitRequestFormParams)
        assert "fs_basis" in params.requested_schema["properties"]
        return ElicitResult(
            action="accept",
            content={
                "fs_basis": "CFS",
                "industry_scope": "detailed",
                "size_basis": "revenue",
                "selection_mode": "ranked",
                "require_note_data": True,
            },
        )

    async def scenario():
        await v2_server.execution_coordinator.clear()
        async with Client(
            v2_server.server,
            elicitation_callback=elicitation_callback,
        ) as client:
            return await client.call_tool(
                "select_peer_group",
                {"company": "005930", "year": 2024},
                meta={
                    "io.kreports/context": {
                        "userId": "user-1",
                        "conversationId": "chat-1",
                        "clientId": "web-chatbot",
                        "interactive": True,
                    }
                },
            )

    result = _run(scenario())
    wire = result.model_dump(mode="json", by_alias=True)

    assert len(calls) == 1
    assert calls[0]["fs_strategy"] == "CFS"
    assert calls[0]["peer_criteria"]["mode"] == "ranked"
    assert calls[0]["peer_criteria"]["required_features"] == ["notes"]
    assert result.structured_content["data_quality"]["status"] == "usable"
    conversation = wire["_meta"]["io.kreports/conversation"]
    assert conversation["stateHandle"]
    assert conversation["resultRef"]
    assert conversation["page"]["pagination"]["returned"] == 5
    assert conversation["pageToken"]
    assert conversation["contextSnapshot"]["active_task"]


def test_next_five_page_does_not_recompute_domain_result(monkeypatch):
    import kreports.mcp.v2_server as v2_server

    calls = 0

    def fake_legacy_result(_name: str, arguments: dict) -> dict:
        nonlocal calls
        calls += 1
        return _fixture_result(arguments)

    monkeypatch.setattr(v2_server, "legacy_result", fake_legacy_result)

    async def scenario():
        await v2_server.execution_coordinator.clear()
        meta = {
            "io.kreports/context": {
                "userId": "user-page",
                "conversationId": "chat-page",
                "clientId": "web-chatbot",
                "interactive": False,
            }
        }
        async with Client(v2_server.server) as client:
            first = await client.call_tool(
                "select_peer_group",
                {
                    "company": "005930",
                    "year": 2024,
                    "fs_strategy": "CFS",
                    "peer_criteria": {
                        "mode": "adaptive",
                        "industry_basis": "ksic",
                    },
                },
                meta=meta,
            )
            first_wire = first.model_dump(mode="json", by_alias=True)
            conversation = first_wire["_meta"]["io.kreports/conversation"]
            second_meta = {
                "io.kreports/context": {
                    **meta["io.kreports/context"],
                    "stateHandle": conversation["stateHandle"],
                    "pageToken": conversation["pageToken"],
                }
            }
            second = await client.call_tool(
                "select_peer_group",
                {"company": "005930"},
                meta=second_meta,
            )
            return first, second

    _first, second = _run(scenario())
    second_wire = second.model_dump(mode="json", by_alias=True)

    assert calls == 1
    page = second_wire["_meta"]["io.kreports/conversation"]["page"]
    assert page["pagination"]["offset"] == 5
    assert page["pagination"]["returned"] == 2
    assert "6~7번째" in second.content[0].text


def test_same_tool_arguments_hit_server_cache(monkeypatch):
    import kreports.mcp.v2_server as v2_server

    calls = 0

    def fake_legacy_result(_name: str, arguments: dict) -> dict:
        nonlocal calls
        calls += 1
        return _fixture_result(arguments)

    monkeypatch.setattr(v2_server, "legacy_result", fake_legacy_result)

    async def scenario():
        await v2_server.execution_coordinator.clear()
        args = {
            "company": "005930",
            "year": 2024,
            "fs_strategy": "CFS",
            "peer_criteria": {
                "mode": "adaptive",
                "industry_basis": "ksic",
            },
        }
        async with Client(v2_server.server) as client:
            first = await client.call_tool("select_peer_group", args)
            second = await client.call_tool("select_peer_group", args)
            return first, second

    first, second = _run(scenario())
    first_meta = first.model_dump(mode="json", by_alias=True)["_meta"]
    second_meta = second.model_dump(mode="json", by_alias=True)["_meta"]

    assert calls == 1
    assert first_meta["io.kreports/performance"]["cacheHit"] is False
    assert second_meta["io.kreports/performance"]["cacheHit"] is True
