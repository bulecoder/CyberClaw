from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Annotated, Iterable, TypedDict

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    summary: str


class ContextPolicyError(ValueError):
    """Raised when context policy settings are invalid."""


@dataclass(frozen=True, slots=True)
class ContextPolicy:
    """Thresholds for the model-visible context view."""

    max_tokens: int = 64_000
    snip_ratio: float = 0.50
    summarize_ratio: float = 0.70
    collapse_ratio: float = 0.90
    max_turns_before_summary: int = 40
    keep_recent_turns: int = 10
    tool_output_chars: int = 1_500
    emergency_tool_output_chars: int = 500

    def __post_init__(self) -> None:
        if self.max_tokens <= 0:
            raise ContextPolicyError("max_tokens 必须大于 0")
        if not 0 < self.snip_ratio < self.summarize_ratio < self.collapse_ratio < 1:
            raise ContextPolicyError(
                "上下文阈值必须满足 0 < snip < summarize < collapse < 1"
            )
        if self.max_turns_before_summary <= 0 or self.keep_recent_turns <= 0:
            raise ContextPolicyError("回合阈值必须大于 0")
        if self.tool_output_chars < 100:
            raise ContextPolicyError("tool_output_chars 不能小于 100")
        if not 50 <= self.emergency_tool_output_chars <= self.tool_output_chars:
            raise ContextPolicyError(
                "emergency_tool_output_chars 必须在 50 到 tool_output_chars 之间"
            )

    @classmethod
    def from_env(cls) -> "ContextPolicy":
        raw_value = os.getenv("CYBERCLAW_CONTEXT_MAX_TOKENS")
        if raw_value is None:
            return cls()
        try:
            max_tokens = int(raw_value.strip())
        except (AttributeError, ValueError) as exc:
            raise ContextPolicyError(
                "CYBERCLAW_CONTEXT_MAX_TOKENS 必须是整数"
            ) from exc
        return cls(max_tokens=max_tokens)

    @property
    def snip_tokens(self) -> int:
        return int(self.max_tokens * self.snip_ratio)

    @property
    def summarize_tokens(self) -> int:
        return int(self.max_tokens * self.summarize_ratio)

    @property
    def collapse_tokens(self) -> int:
        return int(self.max_tokens * self.collapse_ratio)


@dataclass(frozen=True, slots=True)
class ContextPlan:
    """One immutable plan for the next model request."""

    visible_messages: tuple[BaseMessage, ...]
    discarded_messages: tuple[BaseMessage, ...]
    actions: tuple[str, ...]
    estimated_tokens_before: int
    estimated_tokens_after: int
    snipped_tool_messages: int = 0


def _serialized_content(value: object) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def estimate_message_tokens(messages: Iterable[BaseMessage]) -> int:
    """Conservatively approximate mixed Chinese/English message tokens."""

    characters = 0
    message_count = 0
    for message in messages:
        message_count += 1
        characters += len(_serialized_content(message.content))
        if isinstance(message, AIMessage) and message.tool_calls:
            characters += len(_serialized_content(message.tool_calls))
    return (characters + 2) // 3 + message_count * 4


def _split_turns(
    messages: Iterable[BaseMessage],
) -> tuple[SystemMessage | None, list[BaseMessage], list[list[BaseMessage]]]:
    first_system: SystemMessage | None = None
    prefix: list[BaseMessage] = []
    turns: list[list[BaseMessage]] = []
    current_turn: list[BaseMessage] | None = None

    for message in messages:
        if isinstance(message, SystemMessage):
            if first_system is None:
                first_system = message
            continue
        if isinstance(message, HumanMessage):
            if current_turn:
                turns.append(current_turn)
            current_turn = [message]
        elif current_turn is None:
            prefix.append(message)
        else:
            current_turn.append(message)

    if current_turn:
        turns.append(current_turn)
    return first_system, prefix, turns


def _flatten(turns: Iterable[Iterable[BaseMessage]]) -> list[BaseMessage]:
    return [message for turn in turns for message in turn]


def _clip_tool_content(content: str, max_chars: int) -> str:
    if len(content) <= max_chars:
        return content
    marker = f"\n...[工具结果已裁剪，原始长度 {len(content)} 字符]...\n"
    payload_chars = max(2, max_chars - len(marker))
    head_chars = payload_chars // 2
    tail_chars = payload_chars - head_chars
    return f"{content[:head_chars]}{marker}{content[-tail_chars:]}"


def _snip_tool_outputs(
    messages: Iterable[BaseMessage],
    *,
    max_chars: int,
    protected_message_ids: set[int],
) -> tuple[list[BaseMessage], int]:
    visible: list[BaseMessage] = []
    snipped = 0
    for message in messages:
        if (
            isinstance(message, ToolMessage)
            and id(message) not in protected_message_ids
            and isinstance(message.content, str)
            and len(message.content) > max_chars
        ):
            visible.append(
                message.model_copy(
                    update={
                        "content": _clip_tool_content(
                            message.content,
                            max_chars,
                        )
                    }
                )
            )
            snipped += 1
        else:
            visible.append(message)
    return visible, snipped


def build_context_plan(
    messages: Iterable[BaseMessage],
    policy: ContextPolicy | None = None,
) -> ContextPlan:
    """Build a layered context view without mutating checkpoint messages."""

    active_policy = policy or ContextPolicy()
    materialized = list(messages)
    before_tokens = estimate_message_tokens(materialized)
    first_system, prefix, turns = _split_turns(materialized)

    if not turns:
        visible = ([first_system] if first_system else []) + prefix
        return ContextPlan(
            visible_messages=tuple(visible),
            discarded_messages=(),
            actions=(),
            estimated_tokens_before=before_tokens,
            estimated_tokens_after=estimate_message_tokens(visible),
        )

    protected_ids = {id(message) for message in turns[-1]}
    full_visible = ([first_system] if first_system else []) + prefix + _flatten(turns)
    light_visible = full_visible
    if before_tokens >= active_policy.snip_tokens:
        light_visible, _ = _snip_tool_outputs(
            full_visible,
            max_chars=active_policy.tool_output_chars,
            protected_message_ids=protected_ids,
        )

    summarize_needed = (
        len(turns) >= active_policy.max_turns_before_summary
        or estimate_message_tokens(light_visible) >= active_policy.summarize_tokens
    )
    kept_start = 0
    if summarize_needed and len(turns) > active_policy.keep_recent_turns:
        kept_start = len(turns) - active_policy.keep_recent_turns

    initial_kept_start = kept_start

    def visible_for(start: int) -> tuple[list[BaseMessage], int]:
        raw_visible = (
            ([first_system] if first_system else [])
            + prefix
            + _flatten(turns[start:])
        )
        if estimate_message_tokens(raw_visible) < active_policy.snip_tokens:
            return raw_visible, 0
        return _snip_tool_outputs(
            raw_visible,
            max_chars=active_policy.tool_output_chars,
            protected_message_ids=protected_ids,
        )

    visible, snipped = visible_for(kept_start)
    while (
        estimate_message_tokens(visible) >= active_policy.collapse_tokens
        and kept_start < len(turns) - 1
    ):
        kept_start += 1
        visible, snipped = visible_for(kept_start)

    actions: list[str] = []
    if kept_start > 0:
        actions.append("summarize")
    if kept_start > initial_kept_start:
        actions.append("hard_collapse")
    if snipped:
        actions.insert(0, "tool_snip")

    after_tokens = estimate_message_tokens(visible)
    if after_tokens >= active_policy.collapse_tokens:
        visible, emergency_snipped = _snip_tool_outputs(
            visible,
            max_chars=active_policy.emergency_tool_output_chars,
            protected_message_ids=set(),
        )
        if emergency_snipped:
            actions.append("emergency_tool_snip")
            snipped = max(snipped, emergency_snipped)
            after_tokens = estimate_message_tokens(visible)
    if after_tokens >= active_policy.collapse_tokens:
        actions.append("context_overflow")

    return ContextPlan(
        visible_messages=tuple(visible),
        discarded_messages=tuple(_flatten(turns[:kept_start])),
        actions=tuple(actions),
        estimated_tokens_before=before_tokens,
        estimated_tokens_after=after_tokens,
        snipped_tool_messages=snipped,
    )


def render_summary_input(
    messages: Iterable[BaseMessage],
    max_chars: int = 20_000,
) -> str:
    """Render a bounded summary prompt payload from discarded messages."""

    parts: list[str] = []
    used_chars = 0
    for message in messages:
        content = _serialized_content(message.content)
        if isinstance(message, ToolMessage):
            content = _clip_tool_content(content, 2_000)
        part = f"{message.type}: {content}"
        remaining = max_chars - used_chars
        if remaining <= 0:
            break
        parts.append(part[:remaining])
        used_chars += len(parts[-1]) + 1
    return "\n".join(parts)


def trim_context_messages(
    messages: list[BaseMessage],
    trigger_turns: int = 8,
    keep_turns: int = 4,
) -> tuple[list[BaseMessage], list[BaseMessage]]:
    """Compatibility wrapper for the original turn-count trimming API."""

    first_system, _, turns = _split_turns(messages)
    if not turns or len(turns) < trigger_turns:
        non_system = [
            message
            for message in messages
            if not isinstance(message, SystemMessage)
        ]
        return ([first_system] if first_system else []) + non_system, []

    kept = turns[-keep_turns:]
    discarded = turns[:-keep_turns]
    return (
        ([first_system] if first_system else []) + _flatten(kept),
        _flatten(discarded),
    )
