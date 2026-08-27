"""Deterministic peer-choice planning and compact context construction."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import secrets
from typing import Any, Iterable

from kreports.conversation.contracts import (
    ChoiceField,
    ChoiceOption,
    ContextSnapshot,
    ConversationIdentity,
    ConversationState,
    InteractionRequest,
    PeerSelectionPreferences,
    TaskState,
)
from kreports.conversation.store import InMemoryConversationStore


_PEER_TOOLS = {
    "select_peer_group": "peer_selection",
    "compare_to_industry_multi": "peer_benchmark",
    "compare_peer_accounting_notes": "note_comparison",
}


@dataclass(frozen=True)
class PreparedPeerRequest:
    arguments: dict[str, Any] | None
    interaction: InteractionRequest | None
    state_handle: str
    state: ConversationState
    task_id: str


class PeerConversationOrchestrator:
    """Keep choice/state logic outside domain computations."""

    def __init__(
        self,
        store: InMemoryConversationStore,
    ) -> None:
        self.store = store

    @staticmethod
    def peer_choice_request() -> InteractionRequest:
        return InteractionRequest(
            interaction_id="peer_preferences",
            title="비교 기준 선택",
            message=(
                "동종기업 선정 결과에 영향을 주는 기준입니다. "
                "선택하지 않으면 연결재무제표·세부업종·매출 규모를 우선합니다."
            ),
            fields=[
                ChoiceField(
                    key="fs_basis",
                    label="재무제표 기준",
                    kind="single_select",
                    default="CFS",
                    options=[
                        ChoiceOption(value="CFS", label="연결재무제표"),
                        ChoiceOption(value="OFS", label="별도재무제표"),
                        ChoiceOption(
                            value="AUTO",
                            label="확보된 자료 우선",
                            description="연결 자료가 있으면 연결을 우선합니다.",
                        ),
                    ],
                ),
                ChoiceField(
                    key="industry_scope",
                    label="비교 업종 범위",
                    kind="single_select",
                    default="detailed",
                    options=[
                        ChoiceOption(
                            value="detailed",
                            label="세부업종 중심",
                            description="유사성이 높은 회사를 우선합니다.",
                        ),
                        ChoiceOption(
                            value="broad",
                            label="넓은 산업군",
                            description="비교기업 수를 늘려 폭넓게 봅니다.",
                        ),
                    ],
                ),
                ChoiceField(
                    key="size_basis",
                    label="회사 규모 기준",
                    kind="single_select",
                    default="revenue",
                    options=[
                        ChoiceOption(value="revenue", label="매출"),
                        ChoiceOption(value="total_assets", label="총자산"),
                        ChoiceOption(value="none", label="규모 제한 없음"),
                    ],
                ),
                ChoiceField(
                    key="selection_mode",
                    label="선정 방식",
                    kind="single_select",
                    default="adaptive",
                    options=[
                        ChoiceOption(
                            value="adaptive",
                            label="비교기업이 부족하면 범위 확대",
                        ),
                        ChoiceOption(
                            value="strict",
                            label="요청 기준을 엄격하게 적용",
                        ),
                        ChoiceOption(
                            value="ranked",
                            label="유사한 순서대로 선정",
                        ),
                    ],
                ),
                ChoiceField(
                    key="require_note_data",
                    label="주석자료가 있는 회사만 포함",
                    kind="boolean",
                    required=False,
                    default=False,
                ),
            ],
        )

    @staticmethod
    def _has_explicit_peer_choice(arguments: dict[str, Any]) -> bool:
        return bool(
            arguments.get("peer_criteria")
            or arguments.get("criteria")
            or str(arguments.get("fs_strategy") or "").upper()
            in {"CFS", "OFS"}
        )

    @staticmethod
    def _new_task(
        tool_name: str,
        arguments: dict[str, Any],
    ) -> TaskState:
        task_id = secrets.token_urlsafe(12)
        return TaskState(
            task_id=task_id,
            kind=_PEER_TOOLS.get(tool_name, "other"),
            subject={"company": arguments.get("company")},
            criteria={
                key: arguments.get(key)
                for key in (
                    "year",
                    "fs_strategy",
                    "peer_criteria",
                    "criteria",
                    "metrics",
                    "years_back",
                    "topics",
                )
                if arguments.get(key) is not None
            },
        )

    def prepare(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        identity: ConversationIdentity,
        state_handle: str | None = None,
        interactive: bool = False,
    ) -> PreparedPeerRequest:
        if state_handle:
            state = self.store.get_state(state_handle, identity)
            handle = state_handle
        else:
            handle, state = self.store.create_state(identity)

        task = self._new_task(tool_name, arguments)
        state.tasks[task.task_id] = task
        state.active_task_id = task.task_id
        state = self.store.save_state(handle, identity, state)

        preferences = state.saved_preferences
        if self._has_explicit_peer_choice(arguments):
            return PreparedPeerRequest(
                arguments=dict(arguments),
                interaction=None,
                state_handle=handle,
                state=state,
                task_id=task.task_id,
            )
        if preferences is not None:
            applied = {
                **arguments,
                **preferences.to_tool_arguments(),
            }
            return PreparedPeerRequest(
                arguments=applied,
                interaction=None,
                state_handle=handle,
                state=state,
                task_id=task.task_id,
            )
        if interactive:
            return PreparedPeerRequest(
                arguments=None,
                interaction=self.peer_choice_request(),
                state_handle=handle,
                state=state,
                task_id=task.task_id,
            )

        defaults = PeerSelectionPreferences()
        applied = {
            **arguments,
            **defaults.to_tool_arguments(),
        }
        return PreparedPeerRequest(
            arguments=applied,
            interaction=None,
            state_handle=handle,
            state=state,
            task_id=task.task_id,
        )

    def apply_choices(
        self,
        *,
        content: dict[str, Any],
        original_arguments: dict[str, Any],
        identity: ConversationIdentity,
        state_handle: str,
        save_as_preference: bool = False,
    ) -> tuple[dict[str, Any], ConversationState]:
        preferences = PeerSelectionPreferences.model_validate(content)
        state = self.store.get_state(state_handle, identity)
        if save_as_preference:
            state.saved_preferences = preferences
        if state.active_task_id:
            active = state.tasks[state.active_task_id]
            active.criteria.update(preferences.to_tool_arguments())
            active.version += 1
            active.updated_at = datetime.now(timezone.utc)
        state = self.store.save_state(state_handle, identity, state)
        return {
            **original_arguments,
            **preferences.to_tool_arguments(),
        }, state

    @staticmethod
    def record_result(
        state: ConversationState,
        *,
        task_id: str,
        result_ref: str,
        result_kind: str,
    ) -> ConversationState:
        updated = state.model_copy(deep=True)
        task = updated.tasks[task_id]
        task.result_refs[result_kind] = result_ref
        task.current_page = 1
        task.updated_at = datetime.now(timezone.utc)
        task.version += 1
        updated.active_task_id = task_id
        return updated

    @staticmethod
    def invalidate_for_criteria_change(
        state: ConversationState,
        *,
        task_id: str,
        changed_fields: Iterable[str],
    ) -> ConversationState:
        changed = set(changed_fields)
        peer_defining = {
            "company",
            "year",
            "fs_strategy",
            "peer_criteria",
            "criteria",
            "included_corp_codes",
            "excluded_corp_codes",
        }
        updated = state.model_copy(deep=True)
        task = updated.tasks[task_id]
        if changed & peer_defining:
            task.result_refs.clear()
            task.current_page = 1
            task.version += 1
            task.updated_at = datetime.now(timezone.utc)
        return updated

    @staticmethod
    def compact_context(
        state: ConversationState,
        *,
        recent_turns: list[dict[str, str]] | None = None,
        max_chars: int = 6_000,
    ) -> ContextSnapshot:
        active = (
            state.tasks.get(state.active_task_id)
            if state.active_task_id
            else None
        )
        active_payload = (
            {
                "task_id": active.task_id,
                "kind": active.kind,
                "subject": active.subject,
                "criteria": active.criteria,
                "current_page": active.current_page,
                "status": active.status,
            }
            if active
            else None
        )
        other_tasks = [
            {
                "task_id": task.task_id,
                "kind": task.kind,
                "subject": task.subject,
                "status": task.status,
            }
            for task_id, task in state.tasks.items()
            if task_id != state.active_task_id
        ][:10]
        turns = []
        for raw in (recent_turns or [])[-8:]:
            role = str(raw.get("role") or "user")[:20]
            content = " ".join(str(raw.get("content") or "").split())[:700]
            if content:
                turns.append({"role": role, "content": content})
        result_refs = dict(active.result_refs) if active else {}
        lines = []
        if active_payload:
            subject = active_payload["subject"].get("company") or "대상 미확정"
            lines.append(
                f"현재 작업: {active_payload['kind']} / 대상: {subject} / "
                f"페이지: {active_payload['current_page']}"
            )
            if active_payload["criteria"]:
                lines.append(
                    "적용 기준: "
                    + ", ".join(
                        f"{key}={value}"
                        for key, value in active_payload["criteria"].items()
                    )
                )
        if other_tasks:
            lines.append(f"대기 중인 다른 작업: {len(other_tasks)}개")
        summary = "\n".join(lines)[:max_chars]
        return ContextSnapshot(
            active_task=active_payload,
            other_tasks=other_tasks,
            recent_turns=turns,
            result_refs=result_refs,
            omitted_payloads=[
                "전체 회사 목록",
                "전체 재무지표 행",
                "주석 원문",
                "공시 원문",
                "제외회사 전체 목록",
            ],
            summary_text=summary,
        )
