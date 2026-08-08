from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from ..logger import JSONLEventLogger
from .contracts import ToolResult, ToolSpec
from .policy import ToolInvocation, ToolPolicyDecision


@dataclass(frozen=True, slots=True)
class ToolHookContext:
    """Immutable view exposed around one normalized tool attempt."""

    thread_id: str
    spec: ToolSpec
    invocation: ToolInvocation
    policy_decision: ToolPolicyDecision


@dataclass(frozen=True, slots=True)
class ToolResultHookContext:
    tool: ToolHookContext
    result: ToolResult


@dataclass(frozen=True, slots=True)
class ToolHookFailure:
    hook: str
    phase: str
    error_type: str

    def as_dict(self) -> dict[str, str]:
        return {
            "hook": self.hook,
            "phase": self.phase,
            "error_type": self.error_type,
        }


class ToolLifecycleHook(Protocol):
    """Observational hook; it cannot replace policy or tool results."""

    def before_tool(self, context: ToolHookContext) -> None: ...

    def after_tool(self, context: ToolResultHookContext) -> None: ...


class ToolHookPipeline:
    """Run typed hooks in registration order and isolate hook failures."""

    def __init__(self, hooks: Iterable[ToolLifecycleHook] = ()) -> None:
        self._hooks = tuple(hooks)

    @property
    def hooks(self) -> tuple[ToolLifecycleHook, ...]:
        return self._hooks

    def _run(self, phase: str, context: object) -> tuple[ToolHookFailure, ...]:
        failures: list[ToolHookFailure] = []
        for hook in self._hooks:
            try:
                getattr(hook, phase)(context)
            except Exception as exc:
                failures.append(ToolHookFailure(
                    hook=type(hook).__name__,
                    phase=phase,
                    error_type=type(exc).__name__,
                ))
        return tuple(failures)

    def before_tool(
        self,
        context: ToolHookContext,
    ) -> tuple[ToolHookFailure, ...]:
        return self._run("before_tool", context)

    def after_tool(
        self,
        context: ToolResultHookContext,
    ) -> tuple[ToolHookFailure, ...]:
        return self._run("after_tool", context)


class AuditToolHook:
    """Write bounded tool lifecycle metadata through the existing logger."""

    def __init__(self, logger: JSONLEventLogger) -> None:
        self._logger = logger

    def before_tool(self, context: ToolHookContext) -> None:
        accepted = self._logger.log_event(
            thread_id=context.thread_id,
            event="tool_call",
            tool=context.spec.name,
            args=dict(context.invocation.arguments),
            source=context.spec.source.value,
            risk=context.spec.risk.value,
            invocation_fingerprint=context.invocation.fingerprint,
            policy=context.policy_decision.behavior.value,
            policy_rule=context.policy_decision.rule_id,
        )
        if not accepted:
            raise RuntimeError("工具调用审计事件未被日志队列接收")

    def after_tool(self, context: ToolResultHookContext) -> None:
        metadata = context.result.metadata
        approval = metadata.get("approval", {})
        accepted = self._logger.log_event(
            thread_id=context.tool.thread_id,
            event="tool_result",
            tool=context.result.tool_name,
            status=context.result.status.value,
            error_type=context.result.error_type,
            result_chars=len(str(context.result.content)),
            duration_ms=metadata.get("duration_ms"),
            policy_rule=context.tool.policy_decision.rule_id,
            approval_request_id=approval.get("request_id"),
            approval_grant_id=approval.get("grant_id"),
        )
        if not accepted:
            raise RuntimeError("工具结果审计事件未被日志队列接收")
