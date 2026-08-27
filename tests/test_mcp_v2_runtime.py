from __future__ import annotations

import asyncio


def _run(coro):
    return asyncio.run(coro)


def test_vendor_context_identity_and_recent_turns_are_bounded():
    from kreports.mcp.mcp_v2_runtime import (
        recent_turns,
        request_identity,
        supplied_page_token,
        supplied_state_handle,
    )

    class Context:
        meta = {
            "io.kreports/context": {
                "userId": "user-1",
                "conversationId": "chat-1",
                "clientId": "web",
                "stateHandle": "state-token",
                "pageToken": "page-token",
                "recentTurns": [
                    {"role": "user", "content": " x " * 1_000}
                    for _ in range(20)
                ],
            }
        }

    identity = request_identity(Context())
    turns = recent_turns(Context())

    assert identity.user_key == "user-1"
    assert identity.conversation_key == "chat-1"
    assert supplied_state_handle(Context()) == "state-token"
    assert supplied_page_token(Context()) == "page-token"
    assert len(turns) == 8
    assert all(len(turn["content"]) <= 700 for turn in turns)


def test_execution_coordinator_caches_same_dataset_and_arguments():
    from kreports.mcp.mcp_v2_runtime import ToolExecutionCoordinator

    async def scenario():
        coordinator = ToolExecutionCoordinator(ttl_seconds=60)
        calls = 0

        async def runner():
            nonlocal calls
            calls += 1
            return {"value": calls}

        first, first_evidence = await coordinator.execute(
            tool_name="select_peer_group",
            arguments={"company": "005930"},
            runner=runner,
        )
        second, second_evidence = await coordinator.execute(
            tool_name="select_peer_group",
            arguments={"company": "005930"},
            runner=runner,
        )
        return calls, first, second, first_evidence, second_evidence

    calls, first, second, first_evidence, second_evidence = _run(scenario())

    assert calls == 1
    assert first == second == {"value": 1}
    assert first_evidence.cache_hit is False
    assert second_evidence.cache_hit is True


def test_execution_coordinator_single_flights_concurrent_requests():
    from kreports.mcp.mcp_v2_runtime import ToolExecutionCoordinator

    async def scenario():
        coordinator = ToolExecutionCoordinator(ttl_seconds=60)
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def runner():
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return {"value": 7}

        first = asyncio.create_task(coordinator.execute(
            tool_name="compare_to_industry_multi",
            arguments={"company": "005930", "year": 2024},
            runner=runner,
        ))
        await started.wait()
        second = asyncio.create_task(coordinator.execute(
            tool_name="compare_to_industry_multi",
            arguments={"company": "005930", "year": 2024},
            runner=runner,
        ))
        await asyncio.sleep(0)
        release.set()
        first_result, second_result = await asyncio.gather(first, second)
        return calls, first_result, second_result

    calls, first, second = _run(scenario())

    assert calls == 1
    assert first[0] == second[0] == {"value": 7}
    assert {first[1].shared_execution, second[1].shared_execution} == {False, True}


def test_non_cacheable_external_fetch_runs_each_time():
    from kreports.mcp.mcp_v2_runtime import ToolExecutionCoordinator

    async def scenario():
        coordinator = ToolExecutionCoordinator(ttl_seconds=60)
        calls = 0

        async def runner():
            nonlocal calls
            calls += 1
            return {"value": calls}

        first = await coordinator.execute(
            tool_name="fetch_disclosure_on_demand",
            arguments={"rcept_no": "20250101000001"},
            runner=runner,
        )
        second = await coordinator.execute(
            tool_name="fetch_disclosure_on_demand",
            arguments={"rcept_no": "20250101000001"},
            runner=runner,
        )
        return calls, first, second

    calls, first, second = _run(scenario())

    assert calls == 2
    assert first[0] == {"value": 1}
    assert second[0] == {"value": 2}
    assert first[1].cache_hit is False
    assert second[1].cache_hit is False


def test_distinct_arguments_do_not_share_cache_entries():
    from kreports.mcp.mcp_v2_runtime import ToolExecutionCoordinator

    async def scenario():
        coordinator = ToolExecutionCoordinator(ttl_seconds=60)
        calls = 0

        async def runner():
            nonlocal calls
            calls += 1
            return {"value": calls}

        await coordinator.execute(
            tool_name="select_peer_group",
            arguments={"company": "005930", "year": 2024},
            runner=runner,
        )
        await coordinator.execute(
            tool_name="select_peer_group",
            arguments={"company": "005930", "year": 2023},
            runner=runner,
        )
        return calls

    assert _run(scenario()) == 2


def test_extract_page_rows_returns_company_level_rows_only():
    from kreports.mcp.mcp_v2_runtime import extract_page_rows

    peer_rows = extract_page_rows(
        "select_peer_group",
        {
            "peers": [
                {"corp_code": "1", "corp_name": "A"},
                {"corp_code": "2", "corp_name": "B"},
            ]
        },
    )
    note_search_rows = extract_page_rows(
        "search_dataset",
        {
            "query": {"dataset": "accounting_note_chapters"},
            "companies": [
                {
                    "corp_code": "1",
                    "corp_name": "A",
                    "records": [{"rcept_no": "20250101000001"}],
                }
            ],
        },
    )
    note_comparison_rows = extract_page_rows(
        "compare_peer_accounting_notes",
        {
            "subject": {"corp_code": "subject"},
            "topics": [
                {
                    "topic": "leases",
                    "rows": [
                        {"company": {"corp_code": "subject"}},
                        {
                            "company": {
                                "corp_code": "peer",
                                "corp_name": "Peer",
                            },
                            "availability": "available",
                            "rcept_no": "20250101000002",
                        },
                    ],
                }
            ],
        },
    )

    assert [row["corp_name"] for row in peer_rows] == ["A", "B"]
    assert note_search_rows[0]["company"] == "A"
    assert note_comparison_rows[0]["company"] == "Peer"
    assert len(note_comparison_rows[0]["topics"]) == 1


def test_page_answer_is_plain_korean_and_does_not_expose_token_details():
    from kreports.mcp.mcp_v2_runtime import page_answer

    text = page_answer({
        "pagination": {
            "offset": 5,
            "returned": 5,
            "total": 12,
        }
    })

    assert text == "전체 12개 회사 중 6~10번째 회사를 보여드립니다."
    assert "offset" not in text
    assert "token" not in text.lower()
