from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from langchain_core.runnables import RunnableConfig

from .contracts import ToolRisk, ToolSpec


class ToolPolicyBehavior(str, Enum):
    """Possible decisions made before a tool is allowed to run."""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class ToolArgumentsNormalizationError(ValueError):
    """Raised when model arguments cannot form a stable JSON identity."""


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    """Normalized identity of the exact tool call evaluated by policy."""

    call_id: str
    tool_name: str
    arguments: Mapping[str, Any]
    canonical_arguments: str
    fingerprint: str


@dataclass(frozen=True, slots=True)
class ToolPolicyDecision:
    """Deterministic policy outcome with a stable audit reason."""

    behavior: ToolPolicyBehavior
    rule_id: str
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "behavior", ToolPolicyBehavior(self.behavior))
        if not self.rule_id.strip():
            raise ValueError("rule_id 不能为空")
        if not self.reason.strip():
            raise ValueError("reason 不能为空")


def normalize_tool_invocation(
    *,
    call_id: str,
    tool_name: str,
    arguments: Mapping[str, Any],
) -> ToolInvocation:
    """Copy JSON arguments and bind their canonical form to the tool name."""

    try:
        canonical_arguments = json.dumps(
            arguments,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        normalized_arguments = json.loads(canonical_arguments)
    except (TypeError, ValueError) as exc:
        raise ToolArgumentsNormalizationError(
            "工具参数必须是可规范化的 JSON 对象"
        ) from exc

    if not isinstance(normalized_arguments, dict):
        raise ToolArgumentsNormalizationError("工具参数必须是 JSON 对象")

    identity = json.dumps(
        {"tool": tool_name, "arguments": normalized_arguments},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    fingerprint = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return ToolInvocation(
        call_id=call_id,
        tool_name=tool_name,
        arguments=MappingProxyType(normalized_arguments),
        canonical_arguments=canonical_arguments,
        fingerprint=fingerprint,
    )


class ToolPolicyEngine:
    """Small fixed-order policy: hard deny, approval rule, then allow."""

    def __init__(
        self,
        *,
        denied_tools: set[str] | frozenset[str] = frozenset(),
        approval_tools: set[str] | frozenset[str] = frozenset(),
        approval_risks: set[ToolRisk] | frozenset[ToolRisk] = frozenset(),
    ) -> None:
        self._denied_tools = frozenset(self._key(name) for name in denied_tools)
        self._approval_tools = frozenset(
            self._key(name) for name in approval_tools
        )
        self._approval_risks = frozenset(
            ToolRisk(risk) for risk in approval_risks
        )

    @staticmethod
    def _key(name: str) -> str:
        return name.strip().casefold()

    def decide(
        self,
        spec: ToolSpec,
        invocation: ToolInvocation,
        config: RunnableConfig,
    ) -> ToolPolicyDecision:
        """Return the first matching decision; deny always outranks ask."""

        del config  # Reserved for later session-scoped approval rules.
        key = self._key(spec.name)
        if key in self._denied_tools:
            return ToolPolicyDecision(
                behavior=ToolPolicyBehavior.DENY,
                rule_id="hard_deny.tool_name",
                reason=f"工具 '{spec.name}' 已被当前运行策略禁用",
            )
        if key in self._approval_tools:
            return ToolPolicyDecision(
                behavior=ToolPolicyBehavior.ASK,
                rule_id="approval.tool_name",
                reason=f"工具 '{spec.name}' 需要用户确认",
            )
        if spec.risk in self._approval_risks:
            return ToolPolicyDecision(
                behavior=ToolPolicyBehavior.ASK,
                rule_id=f"approval.risk.{spec.risk.value}",
                reason=f"{spec.risk.value} 风险工具需要用户确认",
            )
        return ToolPolicyDecision(
            behavior=ToolPolicyBehavior.ALLOW,
            rule_id="default.allow",
            reason="未命中拒绝或审批规则",
        )
