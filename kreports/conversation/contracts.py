"""Strict contracts for choices, task state, and compact model context."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _strict_config() -> ConfigDict:
    return ConfigDict(extra="forbid", strict=True, validate_assignment=True)


class ChoiceOption(BaseModel):
    model_config = _strict_config()

    value: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=300)


class ChoiceField(BaseModel):
    model_config = _strict_config()

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    label: str = Field(min_length=1, max_length=120)
    kind: Literal["single_select", "multi_select", "boolean", "confirmation"]
    options: list[ChoiceOption] = Field(default_factory=list, max_length=20)
    required: bool = True
    default: str | list[str] | bool | None = None

    @model_validator(mode="after")
    def _coherent_options(self) -> "ChoiceField":
        selectable = self.kind in {"single_select", "multi_select"}
        if selectable and len(self.options) < 2:
            raise ValueError("select fields require at least two options")
        if not selectable and self.options:
            raise ValueError("boolean/confirmation fields cannot declare options")
        values = [item.value for item in self.options]
        if len(values) != len(set(values)):
            raise ValueError("choice option values must be unique")
        if self.default is not None and selectable:
            allowed = set(values)
            selected = self.default if isinstance(self.default, list) else [self.default]
            if not set(selected) <= allowed:
                raise ValueError("choice default must be one of the declared options")
        return self


class InteractionRequest(BaseModel):
    """Application-neutral form request.

    MCP v2 maps this to ``InputRequiredResult``. Older or custom clients can
    render the same object as chips, radio buttons, a modal, or numbered text.
    """

    model_config = _strict_config()

    schema_version: Literal["kreports.interaction.v1"] = "kreports.interaction.v1"
    interaction_id: str = Field(pattern=r"^[a-z][a-z0-9_.:-]{0,119}$")
    title: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=600)
    fields: list[ChoiceField] = Field(min_length=1, max_length=8)
    allow_cancel: bool = True

    @model_validator(mode="after")
    def _unique_fields(self) -> "InteractionRequest":
        keys = [item.key for item in self.fields]
        if len(keys) != len(set(keys)):
            raise ValueError("interaction field keys must be unique")
        return self

    def requested_schema(self) -> dict[str, Any]:
        """Return the bounded JSON Schema used by MCP elicitation/MRTR."""
        properties: dict[str, Any] = {}
        required: list[str] = []
        for field in self.fields:
            if field.required:
                required.append(field.key)
            if field.kind == "single_select":
                properties[field.key] = {
                    "type": "string",
                    "title": field.label,
                    "enum": [option.value for option in field.options],
                    "oneOf": [
                        {
                            "const": option.value,
                            "title": option.label,
                            **(
                                {"description": option.description}
                                if option.description
                                else {}
                            ),
                        }
                        for option in field.options
                    ],
                }
                if isinstance(field.default, str):
                    properties[field.key]["default"] = field.default
            elif field.kind == "multi_select":
                properties[field.key] = {
                    "type": "array",
                    "title": field.label,
                    "items": {
                        "type": "string",
                        "enum": [option.value for option in field.options],
                    },
                    "uniqueItems": True,
                    "maxItems": len(field.options),
                }
                if isinstance(field.default, list):
                    properties[field.key]["default"] = field.default
            else:
                properties[field.key] = {
                    "type": "boolean",
                    "title": field.label,
                }
                if isinstance(field.default, bool):
                    properties[field.key]["default"] = field.default
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }


class ConversationIdentity(BaseModel):
    """Identity supplied by the trusted host, never by model-generated args."""

    model_config = _strict_config()

    user_key: str = Field(min_length=1, max_length=200)
    conversation_key: str = Field(min_length=1, max_length=200)
    client_key: str = Field(default="unknown-client", min_length=1, max_length=160)


class PeerSelectionPreferences(BaseModel):
    model_config = _strict_config()

    fs_basis: Literal["AUTO", "CFS", "OFS"] = "CFS"
    industry_scope: Literal["detailed", "broad"] = "detailed"
    size_basis: Literal["revenue", "total_assets", "none"] = "revenue"
    selection_mode: Literal["strict", "adaptive", "ranked"] = "adaptive"
    require_note_data: bool = False

    def to_tool_arguments(self) -> dict[str, Any]:
        criteria: dict[str, Any] = {
            "mode": self.selection_mode,
            "industry_basis": (
                "ksic" if self.industry_scope == "detailed" else "sector_group"
            ),
        }
        if self.industry_scope == "detailed":
            criteria.update(prefix_len=3, fallback_prefix_len=2)
        if self.size_basis != "none":
            criteria.update(
                size_metric=self.size_basis,
                size_log10_tolerance=1.0,
            )
        if self.require_note_data:
            criteria.update(required_features=["notes"], minimum_coverage=1.0)
        return {
            "fs_strategy": self.fs_basis,
            "peer_criteria": criteria,
        }


class TaskState(BaseModel):
    model_config = _strict_config()

    task_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{8,80}$")
    kind: Literal[
        "peer_selection",
        "peer_benchmark",
        "note_search",
        "note_comparison",
        "other",
    ]
    subject: dict[str, Any] = Field(default_factory=dict)
    criteria: dict[str, Any] = Field(default_factory=dict)
    result_refs: dict[str, str] = Field(default_factory=dict)
    current_page: int = Field(default=1, ge=1, le=100_000)
    status: Literal["active", "paused", "completed", "cancelled"] = "active"
    version: int = Field(default=1, ge=1)
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @field_validator("subject", "criteria", "result_refs")
    @classmethod
    def _bounded_mapping(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > 64:
            raise ValueError("task mapping exceeds bounds")
        if len(str(value)) > 20_000:
            raise ValueError("task mapping exceeds serialized bounds")
        return value


class ConversationState(BaseModel):
    model_config = _strict_config()

    state_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{16,120}$")
    user_key: str = Field(min_length=1, max_length=200)
    conversation_key: str = Field(min_length=1, max_length=200)
    active_task_id: str | None = None
    tasks: dict[str, TaskState] = Field(default_factory=dict, max_length=20)
    saved_preferences: PeerSelectionPreferences | None = None
    revision: int = Field(default=1, ge=1)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @model_validator(mode="after")
    def _active_task_exists(self) -> "ConversationState":
        if self.active_task_id is not None and self.active_task_id not in self.tasks:
            raise ValueError("active_task_id must reference a stored task")
        return self


class ContextSnapshot(BaseModel):
    """Bounded state supplied to the host model instead of the full chat."""

    model_config = _strict_config()

    schema_version: Literal["kreports.context.v1"] = "kreports.context.v1"
    active_task: dict[str, Any] | None = None
    other_tasks: list[dict[str, Any]] = Field(default_factory=list, max_length=10)
    recent_turns: list[dict[str, str]] = Field(default_factory=list, max_length=8)
    result_refs: dict[str, str] = Field(default_factory=dict, max_length=20)
    omitted_payloads: list[str] = Field(default_factory=list, max_length=20)
    summary_text: str = Field(default="", max_length=8_000)
