import asyncio
import os
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage


STOP_TASK = object()


class RuntimeLimitConfigError(ValueError):
    """Raised when an Agent runtime-limit setting is invalid."""


@dataclass(frozen=True, slots=True)
class AgentRunLimits:
    """Hard limits applied independently to each user turn."""

    max_model_calls: int = 20
    max_tool_calls: int = 50
    recursion_limit: int = 50

    def __post_init__(self) -> None:
        if self.max_model_calls <= 0:
            raise RuntimeLimitConfigError("max_model_calls 必须大于 0")
        if self.max_tool_calls < 0:
            raise RuntimeLimitConfigError("max_tool_calls 不能小于 0")

        minimum_recursion_limit = self.max_model_calls * 2 + 1
        if self.recursion_limit < minimum_recursion_limit:
            raise RuntimeLimitConfigError(
                "recursion_limit 至少应为 max_model_calls * 2 + 1 "
                f"（当前至少需要 {minimum_recursion_limit}）"
            )

    @classmethod
    def from_env(cls) -> "AgentRunLimits":
        """Load optional limits at runtime, after the project .env is loaded."""

        defaults = cls()
        return cls(
            max_model_calls=_read_int_env(
                "CYBERCLAW_MAX_MODEL_CALLS",
                defaults.max_model_calls,
            ),
            max_tool_calls=_read_int_env(
                "CYBERCLAW_MAX_TOOL_CALLS",
                defaults.max_tool_calls,
            ),
            recursion_limit=_read_int_env(
                "CYBERCLAW_RECURSION_LIMIT",
                defaults.recursion_limit,
            ),
        )


def _read_int_env(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return int(raw_value.strip())
    except (AttributeError, ValueError) as exc:
        raise RuntimeLimitConfigError(f"{name} 必须是整数") from exc


def current_turn_messages(messages: Iterable[BaseMessage]) -> list[BaseMessage]:
    """Return messages from the latest HumanMessage onward."""

    materialized = list(messages)
    for index in range(len(materialized) - 1, -1, -1):
        if isinstance(materialized[index], HumanMessage):
            return materialized[index:]
    return materialized


def count_current_turn_model_calls(messages: Iterable[BaseMessage]) -> int:
    """Count persisted main-loop model responses in the current user turn."""

    return sum(
        isinstance(message, AIMessage)
        for message in current_turn_messages(messages)
    )


def count_current_turn_tool_calls(messages: Iterable[BaseMessage]) -> int:
    """Count answered tool calls in the current user turn."""

    return sum(
        isinstance(message, ToolMessage)
        for message in current_turn_messages(messages)
    )


def _drain_pending(queue: asyncio.Queue[Any]) -> None:
    """Discard queued items while keeping Queue.join() accounting balanced."""
    while True:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        else:
            queue.task_done()


async def shutdown_task_queue(
    queue: asyncio.Queue[Any],
    consumer: asyncio.Task[Any],
    producers: Iterable[asyncio.Task[Any]] = (),
    timeout: float = 10.0,
) -> bool:
    """Stop producers, drain accepted work, then stop the single consumer."""
    if timeout <= 0:
        raise ValueError("timeout 必须大于 0")

    producer_tasks = tuple(producers)
    for task in producer_tasks:
        task.cancel()
    if producer_tasks:
        await asyncio.gather(*producer_tasks, return_exceptions=True)

    if consumer.done():
        _drain_pending(queue)
        await asyncio.gather(consumer, return_exceptions=True)
        return False

    try:
        await asyncio.wait_for(queue.put(STOP_TASK), timeout=timeout)
        await asyncio.wait_for(queue.join(), timeout=timeout)
    except asyncio.TimeoutError:
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)
        _drain_pending(queue)
        return False

    result = (await asyncio.gather(consumer, return_exceptions=True))[0]
    return not isinstance(result, BaseException)
