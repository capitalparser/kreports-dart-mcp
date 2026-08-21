from __future__ import annotations

import time

import pytest


def identity(user: str = "user-1", conversation: str = "chat-1"):
    from kreports.conversation.contracts import ConversationIdentity

    return ConversationIdentity(
        user_key=user,
        conversation_key=conversation,
        client_key="web-chatbot",
    )


def test_interaction_schema_is_deterministic_and_ui_renderable():
    from kreports.conversation.orchestrator import PeerConversationOrchestrator

    request = PeerConversationOrchestrator.peer_choice_request()
    schema = request.requested_schema()

    assert request.interaction_id == "peer_preferences"
    assert schema["additionalProperties"] is False
    assert schema["properties"]["fs_basis"]["enum"] == ["CFS", "OFS", "AUTO"]
    assert schema["properties"]["size_basis"]["default"] == "revenue"
    assert "require_note_data" not in schema["required"]


def test_state_handle_is_bound_to_user_and_conversation_and_rejects_tampering():
    from kreports.conversation.store import (
        InMemoryConversationStore,
        StateAccessError,
        StateHandleError,
    )

    store = InMemoryConversationStore(signing_key=b"k" * 32)
    handle, state = store.create_state(identity())

    assert store.get_state(handle, identity()).state_id == state.state_id
    with pytest.raises(StateAccessError):
        store.get_state(handle, identity(user="user-2"))
    with pytest.raises(StateAccessError):
        store.get_state(handle, identity(conversation="chat-2"))
    with pytest.raises(StateHandleError):
        store.get_state(handle[:-1] + ("A" if handle[-1] != "A" else "B"), identity())


def test_peer_choice_applies_only_after_user_accepts_and_can_be_saved():
    from kreports.conversation.orchestrator import PeerConversationOrchestrator
    from kreports.conversation.store import InMemoryConversationStore

    store = InMemoryConversationStore(signing_key=b"s" * 32)
    orchestrator = PeerConversationOrchestrator(store)
    prepared = orchestrator.prepare(
        tool_name="select_peer_group",
        arguments={"company": "005930", "year": 2024},
        identity=identity(),
        interactive=True,
    )

    assert prepared.arguments is None
    assert prepared.interaction is not None

    arguments, state = orchestrator.apply_choices(
        content={
            "fs_basis": "CFS",
            "industry_scope": "detailed",
            "size_basis": "revenue",
            "selection_mode": "ranked",
            "require_note_data": True,
        },
        original_arguments={"company": "005930", "year": 2024},
        identity=identity(),
        state_handle=prepared.state_handle,
        save_as_preference=True,
    )

    assert arguments["fs_strategy"] == "CFS"
    assert arguments["peer_criteria"]["mode"] == "ranked"
    assert arguments["peer_criteria"]["size_metric"] == "revenue"
    assert arguments["peer_criteria"]["required_features"] == ["notes"]
    assert state.saved_preferences is not None


def test_saved_preferences_remove_repeated_poll_for_same_conversation():
    from kreports.conversation.orchestrator import PeerConversationOrchestrator
    from kreports.conversation.store import InMemoryConversationStore

    store = InMemoryConversationStore(signing_key=b"p" * 32)
    orchestrator = PeerConversationOrchestrator(store)
    first = orchestrator.prepare(
        tool_name="select_peer_group",
        arguments={"company": "005930"},
        identity=identity(),
        interactive=True,
    )
    orchestrator.apply_choices(
        content={
            "fs_basis": "OFS",
            "industry_scope": "broad",
            "size_basis": "none",
            "selection_mode": "adaptive",
            "require_note_data": False,
        },
        original_arguments={"company": "005930"},
        identity=identity(),
        state_handle=first.state_handle,
        save_as_preference=True,
    )

    second = orchestrator.prepare(
        tool_name="compare_to_industry_multi",
        arguments={"company": "005930"},
        identity=identity(),
        state_handle=first.state_handle,
        interactive=True,
    )

    assert second.interaction is None
    assert second.arguments["fs_strategy"] == "OFS"
    assert second.arguments["peer_criteria"]["industry_basis"] == "sector_group"


def test_result_pages_are_five_rows_and_do_not_recompute():
    from kreports.conversation.store import InMemoryConversationStore

    store = InMemoryConversationStore(signing_key=b"r" * 32)
    rows = [{"company": f"Company {index}"} for index in range(12)]
    _result_id, token = store.store_result(
        identity=identity(),
        rows=rows,
        metadata={"population": 12},
        page_size=5,
    )
    assert token is not None

    page1 = store.get_page(token, identity())
    page2 = store.get_page(page1["pagination"]["next_page_token"], identity())
    page3 = store.get_page(page2["pagination"]["next_page_token"], identity())

    assert [len(page["rows"]) for page in (page1, page2, page3)] == [5, 5, 2]
    assert page1["metadata"]["population"] == 12
    assert page3["pagination"]["has_more"] is False
    assert page2["pagination"]["previous_page_token"] is not None


def test_page_token_is_owner_bound():
    from kreports.conversation.store import (
        InMemoryConversationStore,
        StateAccessError,
    )

    store = InMemoryConversationStore(signing_key=b"q" * 32)
    _, token = store.store_result(
        identity=identity(),
        rows=[{"company": "A"}],
    )
    with pytest.raises(StateAccessError):
        store.get_page(token, identity(user="other-user"))


def test_context_snapshot_excludes_heavy_payloads_and_keeps_active_task():
    from kreports.conversation.orchestrator import PeerConversationOrchestrator
    from kreports.conversation.store import InMemoryConversationStore

    store = InMemoryConversationStore(signing_key=b"c" * 32)
    orchestrator = PeerConversationOrchestrator(store)
    prepared = orchestrator.prepare(
        tool_name="select_peer_group",
        arguments={"company": "005930", "year": 2024},
        identity=identity(),
        interactive=False,
    )

    snapshot = orchestrator.compact_context(
        prepared.state,
        recent_turns=[
            {"role": "user", "content": "삼성전자 동종기업을 보여줘"},
            {"role": "assistant", "content": "첫 5개 회사를 표시했습니다."},
        ],
    )

    assert snapshot.active_task["subject"]["company"] == "005930"
    assert snapshot.recent_turns[-1]["role"] == "assistant"
    assert "주석 원문" in snapshot.omitted_payloads
    assert len(snapshot.summary_text) < 8_000


def test_peer_defining_change_invalidates_dependent_result_refs():
    from kreports.conversation.orchestrator import PeerConversationOrchestrator
    from kreports.conversation.store import InMemoryConversationStore

    store = InMemoryConversationStore(signing_key=b"i" * 32)
    orchestrator = PeerConversationOrchestrator(store)
    prepared = orchestrator.prepare(
        tool_name="select_peer_group",
        arguments={"company": "005930"},
        identity=identity(),
        interactive=False,
    )
    task_id = prepared.task_id
    state = orchestrator.record_result(
        prepared.state,
        task_id=task_id,
        result_ref="result-1",
        result_kind="peer_group",
    )
    assert state.tasks[task_id].result_refs

    changed = orchestrator.invalidate_for_criteria_change(
        state,
        task_id=task_id,
        changed_fields={"fs_strategy"},
    )
    assert changed.tasks[task_id].result_refs == {}


def test_expired_handle_fails_closed():
    from kreports.conversation.store import (
        InMemoryConversationStore,
        StateExpiredError,
    )

    store = InMemoryConversationStore(
        signing_key=b"e" * 32,
        state_ttl_seconds=60,
    )
    handle, _ = store.create_state(identity())
    expired = store._encode_token(
        kind="state",
        identifier=store._decode_token(
            handle,
            expected_kind="state",
            identity=identity(),
        )["id"],
        identity=identity(),
        expires_at=int(time.time()) - 1,
    )
    with pytest.raises(StateExpiredError):
        store.get_state(expired, identity())
