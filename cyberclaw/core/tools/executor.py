from __future__ import annotations

import json
import time
from collections.abc import Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from pydantic import ValidationError
from pydantic.v1 import ValidationError as ValidationErrorV1

from ..runtime import count_current_turn_tool_calls
from .approval import ApprovalGrant, ApprovalRequest, ApprovalStore
from .contracts import ToolMessageContent, ToolResult, ToolResultStatus
from .policy import (
    ToolArgumentsNormalizationError,
    ToolInvocation,
    ToolPolicyBehavior,
    ToolPolicyDecision,
    ToolPolicyEngine,
    normalize_tool_invocation,
)
from .registry import ToolRegistry


_MAX_PARALLEL_TOOLS = 4


class ToolProtocolError(RuntimeError):
    """Raised when the graph routes an invalid message sequence to tools."""


def find_pending_tool_calls(
    messages: Iterable[BaseMessage],
) -> list[Mapping[str, Any]]:
    """Find unanswered calls on the terminal assistant tool-call message."""

    materialized = list(messages)
    terminal_ai_index: int | None = None
    for index in range(len(materialized) - 1, -1, -1):
        message = materialized[index]
        if isinstance(message, ToolMessage):
            continue
        if isinstance(message, AIMessage) and message.tool_calls:
            terminal_ai_index = index
        break

    if terminal_ai_index is None:
        return []

    answered_ids = {
        message.tool_call_id
        for message in materialized[terminal_ai_index + 1:]
        if isinstance(message, ToolMessage)
    }
    return [
        call
        for call in materialized[terminal_ai_index].tool_calls
        if str(call.get("id", "")).strip() not in answered_ids
    ]


def build_interrupted_tool_messages(
    messages: Iterable[BaseMessage],
) -> list[ToolMessage]:
    """Build protocol-valid placeholders for a cancelled Agent run."""

    placeholders: list[ToolMessage] = []
    for call in find_pending_tool_calls(messages):
        call_id = str(call.get("id", "")).strip()
        tool_name = str(call.get("name", "")).strip()
        if not call_id or not tool_name:
            raise ToolProtocolError(
                "待恢复的工具调用必须同时包含 id 和 name"
            )
        placeholders.append(
            ToolResult(
                tool_call_id=call_id,
                tool_name=tool_name,
                status=ToolResultStatus.INTERRUPTED,
                content=(
                    "该工具调用因本次 Agent 运行被取消而未完成。"
                    "如果仍需执行，请在新的用户任务中重新发起。"
                ),
                error_type="ToolExecutionInterrupted",
                metadata={"reason": "run_cancelled"},
            ).to_tool_message()
        )
    return placeholders


def _normalize_content(value: Any) -> ToolMessageContent:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return value
    if value is None:
        return ""
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def _validation_summary(exc: ValidationError | ValidationErrorV1) -> str:
    try:
        errors = exc.errors(include_url=False, include_input=False)
    except TypeError:
        errors = exc.errors()

    details: list[str] = []
    for error in errors[:5]:
        location = ".".join(str(part) for part in error.get("loc", ()))
        message = str(error.get("msg", "参数不合法"))
        details.append(f"{location or '<root>'}: {message}")
    return "; ".join(details) or "参数不符合工具 Schema"


class ToolExecutorNode:
    """Execute safe call groups concurrently while preserving call order."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        max_tool_calls: int | None = None,
        policy: ToolPolicyEngine | None = None,
        approval_store: ApprovalStore | None = None,
    ) -> None:
        if max_tool_calls is not None and max_tool_calls < 0:
            raise ValueError("max_tool_calls 不能小于 0")
        self._registry = registry.snapshot()
        self._max_tool_calls = max_tool_calls
        self._policy = policy or ToolPolicyEngine()
        self._approval_store = approval_store

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    @property
    def max_tool_calls(self) -> int | None:
        return self._max_tool_calls

    @property
    def policy(self) -> ToolPolicyEngine:
        return self._policy

    @property
    def approval_store(self) -> ApprovalStore | None:
        return self._approval_store

    def _metadata(
        self,
        tool_name: str,
        duration_ms: float,
        *,
        invocation: ToolInvocation | None = None,
        decision: ToolPolicyDecision | None = None,
        approval: ApprovalRequest | ApprovalGrant | None = None,
    ) -> dict[str, Any]:
        spec = self._registry.get(tool_name)
        if spec is None:
            return {"duration_ms": round(duration_ms, 3)}
        metadata = {
            "source": spec.source.value,
            "risk": spec.risk.value,
            "read_only": spec.read_only,
            "concurrent_safe": spec.concurrent_safe,
            "duration_ms": round(duration_ms, 3),
        }
        if invocation is not None:
            metadata["invocation_fingerprint"] = invocation.fingerprint
        if decision is not None:
            metadata["policy"] = {
                "behavior": decision.behavior.value,
                "rule_id": decision.rule_id,
            }
        if isinstance(approval, ApprovalRequest):
            metadata["approval"] = {
                "request_id": approval.request_id,
                "tool_name": approval.tool_name,
                "arguments": approval.canonical_arguments,
                "ttl_seconds": self._approval_store.ttl_seconds,
            }
        elif isinstance(approval, ApprovalGrant):
            metadata["approval"] = {
                "grant_id": approval.grant_id,
                "consumed": True,
            }
        return metadata

    def execute_call(
        self,
        call: Mapping[str, Any],
        config: RunnableConfig,
    ) -> ToolResult:
        call_id = str(call.get("id", "")).strip()
        tool_name = str(call.get("name", "")).strip()
        if not call_id:
            raise ToolProtocolError("模型产生的工具调用缺少 id")
        if not tool_name:
            raise ToolProtocolError(f"工具调用 {call_id} 缺少 name")

        spec = self._registry.get(tool_name)
        if spec is None:
            available = ", ".join(sorted(self._registry.names)) or "无"
            return ToolResult(
                tool_call_id=call_id,
                tool_name=tool_name,
                status=ToolResultStatus.TOOL_NOT_FOUND,
                content=(
                    f"未注册工具 '{tool_name}'。当前可用工具：{available}。"
                ),
                error_type="ToolNotFound",
                metadata={"available_tool_count": len(self._registry.names)},
            )

        raw_args = call.get("args", {})
        if not isinstance(raw_args, dict):
            return ToolResult(
                tool_call_id=call_id,
                tool_name=tool_name,
                status=ToolResultStatus.INVALID_ARGUMENTS,
                content="工具参数必须是 JSON 对象，请根据工具 Schema 修正参数。",
                error_type="InvalidArgumentShape",
                metadata=self._metadata(tool_name, 0.0),
            )

        try:
            invocation = normalize_tool_invocation(
                call_id=call_id,
                tool_name=spec.name,
                arguments=raw_args,
            )
        except ToolArgumentsNormalizationError:
            return ToolResult(
                tool_call_id=call_id,
                tool_name=tool_name,
                status=ToolResultStatus.INVALID_ARGUMENTS,
                content=(
                    "工具参数必须是可规范化的 JSON 对象，"
                    "请根据工具 Schema 修正参数。"
                ),
                error_type="ToolArgumentsNormalizationError",
                metadata=self._metadata(tool_name, 0.0),
            )

        decision = self._policy.decide(spec, invocation, config)
        grant: ApprovalGrant | None = None
        if decision.behavior is not ToolPolicyBehavior.ALLOW:
            requires_approval = decision.behavior is ToolPolicyBehavior.ASK
            thread_id = str(
                config.get("configurable", {}).get(
                    "thread_id",
                    "system_default",
                )
            )
            if requires_approval and self._approval_store is not None:
                grant = self._approval_store.consume(
                    thread_id=thread_id,
                    invocation_fingerprint=invocation.fingerprint,
                )
            if grant is not None:
                decision = ToolPolicyDecision(
                    behavior=ToolPolicyBehavior.ALLOW,
                    rule_id="approval.once",
                    reason="用户已批准完全相同的工具调用",
                )
            else:
                request = None
                if requires_approval and self._approval_store is not None:
                    request = self._approval_store.request(
                        thread_id=thread_id,
                        tool_name=spec.name,
                        canonical_arguments=invocation.canonical_arguments,
                        invocation_fingerprint=invocation.fingerprint,
                        reason=decision.reason,
                    )
                request_hint = (
                    f"审批编号：{request.request_id}。"
                    "请用户输入 /approve <编号>，然后重试完全相同的调用。"
                    if request is not None
                    else "当前运行没有可用的审批存储。"
                )
                return ToolResult(
                    tool_call_id=call_id,
                    tool_name=spec.name,
                    status=ToolResultStatus.PERMISSION_DENIED,
                    content=(
                        f"需要用户确认：{decision.reason}。{request_hint}"
                        if requires_approval
                        else f"策略拒绝：{decision.reason}。"
                    ),
                    error_type=(
                        "ToolApprovalRequired"
                        if requires_approval
                        else "ToolPolicyDenied"
                    ),
                    metadata=self._metadata(
                        tool_name,
                        0.0,
                        invocation=invocation,
                        decision=decision,
                        approval=request,
                    ),
                )

        args = dict(invocation.arguments)

        started = time.perf_counter()
        try:
            response = spec.tool.invoke(
                {
                    "name": spec.name,
                    "args": args,
                    "id": call_id,
                    "type": "tool_call",
                },
                config=config,
            )
            duration_ms = (time.perf_counter() - started) * 1000
            metadata = self._metadata(
                tool_name,
                duration_ms,
                invocation=invocation,
                decision=decision,
                approval=grant,
            )

            if isinstance(response, ToolMessage):
                content = response.content
                if response.artifact is not None:
                    metadata["tool_artifact"] = response.artifact
                if response.status == "error":
                    return ToolResult(
                        tool_call_id=call_id,
                        tool_name=tool_name,
                        status=ToolResultStatus.EXECUTION_ERROR,
                        content=content,
                        error_type="ToolReportedError",
                        metadata=metadata,
                    )
            else:
                content = _normalize_content(response)

            metadata["output_chars"] = len(str(content))
            return ToolResult(
                tool_call_id=call_id,
                tool_name=tool_name,
                status=ToolResultStatus.SUCCESS,
                content=content,
                metadata=metadata,
            )
        except (ValidationError, ValidationErrorV1) as exc:
            duration_ms = (time.perf_counter() - started) * 1000
            return ToolResult(
                tool_call_id=call_id,
                tool_name=tool_name,
                status=ToolResultStatus.INVALID_ARGUMENTS,
                content=(
                    "工具参数校验失败，请根据工具 Schema 修正参数："
                    f"{_validation_summary(exc)}"
                ),
                error_type=type(exc).__name__,
                metadata=self._metadata(
                    tool_name,
                    duration_ms,
                    invocation=invocation,
                    decision=decision,
                    approval=grant,
                ),
            )
        except PermissionError as exc:
            duration_ms = (time.perf_counter() - started) * 1000
            return ToolResult(
                tool_call_id=call_id,
                tool_name=tool_name,
                status=ToolResultStatus.PERMISSION_DENIED,
                content="权限拒绝：该操作不符合当前工具或工作区策略。",
                error_type=type(exc).__name__,
                metadata=self._metadata(
                    tool_name,
                    duration_ms,
                    invocation=invocation,
                    decision=decision,
                    approval=grant,
                ),
            )
        except TimeoutError:
            duration_ms = (time.perf_counter() - started) * 1000
            return ToolResult(
                tool_call_id=call_id,
                tool_name=tool_name,
                status=ToolResultStatus.TIMEOUT,
                content=f"工具 '{tool_name}' 执行超时。",
                error_type="TimeoutError",
                metadata=self._metadata(
                    tool_name,
                    duration_ms,
                    invocation=invocation,
                    decision=decision,
                    approval=grant,
                ),
            )
        except Exception as exc:
            duration_ms = (time.perf_counter() - started) * 1000
            return ToolResult(
                tool_call_id=call_id,
                tool_name=tool_name,
                status=ToolResultStatus.EXECUTION_ERROR,
                content=f"工具 '{tool_name}' 执行失败（{type(exc).__name__}）。",
                error_type=type(exc).__name__,
                metadata=self._metadata(
                    tool_name,
                    duration_ms,
                    invocation=invocation,
                    decision=decision,
                    approval=grant,
                ),
            )

    def __call__(
        self,
        state: Mapping[str, Any],
        config: RunnableConfig,
    ) -> dict[str, list[ToolMessage]]:
        messages = state.get("messages", [])
        if not messages or not isinstance(messages[-1], AIMessage):
            raise ToolProtocolError("tools 节点必须接收以 AIMessage 结尾的状态")

        tool_calls = messages[-1].tool_calls
        if not tool_calls:
            raise ToolProtocolError("tools 节点收到的 AIMessage 不包含 tool_calls")

        consumed_calls = count_current_turn_tool_calls(messages)
        results: list[ToolResult | None] = [None] * len(tool_calls)

        def within_budget(index: int) -> bool:
            return (
                self._max_tool_calls is None
                or consumed_calls + index < self._max_tool_calls
            )

        def budget_result(index: int, call: Mapping[str, Any]) -> ToolResult:
            call_id = str(call.get("id", "")).strip()
            tool_name = str(call.get("name", "")).strip()
            if not call_id or not tool_name:
                raise ToolProtocolError(
                    "超出预算的工具调用仍必须包含 id 和 name"
                )
            return ToolResult(
                tool_call_id=call_id,
                tool_name=tool_name,
                status=ToolResultStatus.BUDGET_EXCEEDED,
                content=(
                    "本次任务已达到工具调用上限，未执行该工具。"
                    "请基于已有结果作答，或让用户缩小任务范围后重试。"
                ),
                error_type="ToolCallBudgetExceeded",
                metadata={
                    "max_tool_calls": self._max_tool_calls,
                    "consumed_tool_calls": consumed_calls + index,
                },
            )

        index = 0
        while index < len(tool_calls):
            call = tool_calls[index]
            if not within_budget(index):
                results[index] = budget_result(index, call)
                index += 1
                continue

            spec = self._registry.get(str(call.get("name", "")))
            if spec is None or not spec.concurrent_safe:
                results[index] = self.execute_call(call, config)
                index += 1
                continue

            group_end = index + 1
            while group_end < len(tool_calls) and within_budget(group_end):
                next_call = tool_calls[group_end]
                next_spec = self._registry.get(
                    str(next_call.get("name", ""))
                )
                if next_spec is None or not next_spec.concurrent_safe:
                    break
                group_end += 1

            group = tool_calls[index:group_end]
            if len(group) == 1:
                results[index] = self.execute_call(group[0], config)
            else:
                worker_count = min(len(group), _MAX_PARALLEL_TOOLS)
                with ThreadPoolExecutor(
                    max_workers=worker_count,
                    thread_name_prefix="cyberclaw-tool",
                ) as pool:
                    futures = [
                        pool.submit(
                            copy_context().run,
                            self.execute_call,
                            grouped_call,
                            config,
                        )
                        for grouped_call in group
                    ]
                    for offset, future in enumerate(futures):
                        results[index + offset] = future.result()
            index = group_end

        if any(result is None for result in results):
            raise RuntimeError("工具执行计划存在未完成的结果槽位")
        return {
            "messages": [
                result.to_tool_message()
                for result in results
                if result is not None
            ]
        }
