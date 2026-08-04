from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from pydantic import ValidationError
from pydantic.v1 import ValidationError as ValidationErrorV1

from .contracts import ToolMessageContent, ToolResult, ToolResultStatus
from .registry import ToolRegistry


class ToolProtocolError(RuntimeError):
    """Raised when the graph routes an invalid message sequence to tools."""


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
    """Execute registered tool calls serially and return paired ToolMessages."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry.snapshot()

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    def _metadata(self, tool_name: str, duration_ms: float) -> dict[str, Any]:
        spec = self._registry.get(tool_name)
        if spec is None:
            return {"duration_ms": round(duration_ms, 3)}
        return {
            "source": spec.source.value,
            "risk": spec.risk.value,
            "read_only": spec.read_only,
            "concurrent_safe": spec.concurrent_safe,
            "duration_ms": round(duration_ms, 3),
        }

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

        args = call.get("args", {})
        if not isinstance(args, dict):
            return ToolResult(
                tool_call_id=call_id,
                tool_name=tool_name,
                status=ToolResultStatus.INVALID_ARGUMENTS,
                content="工具参数必须是 JSON 对象，请根据工具 Schema 修正参数。",
                error_type="InvalidArgumentShape",
                metadata=self._metadata(tool_name, 0.0),
            )

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
            metadata = self._metadata(tool_name, duration_ms)

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
                metadata=self._metadata(tool_name, duration_ms),
            )
        except PermissionError as exc:
            duration_ms = (time.perf_counter() - started) * 1000
            return ToolResult(
                tool_call_id=call_id,
                tool_name=tool_name,
                status=ToolResultStatus.PERMISSION_DENIED,
                content="权限拒绝：该操作不符合当前工具或工作区策略。",
                error_type=type(exc).__name__,
                metadata=self._metadata(tool_name, duration_ms),
            )
        except TimeoutError:
            duration_ms = (time.perf_counter() - started) * 1000
            return ToolResult(
                tool_call_id=call_id,
                tool_name=tool_name,
                status=ToolResultStatus.TIMEOUT,
                content=f"工具 '{tool_name}' 执行超时。",
                error_type="TimeoutError",
                metadata=self._metadata(tool_name, duration_ms),
            )
        except Exception as exc:
            duration_ms = (time.perf_counter() - started) * 1000
            return ToolResult(
                tool_call_id=call_id,
                tool_name=tool_name,
                status=ToolResultStatus.EXECUTION_ERROR,
                content=f"工具 '{tool_name}' 执行失败（{type(exc).__name__}）。",
                error_type=type(exc).__name__,
                metadata=self._metadata(tool_name, duration_ms),
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

        results = [
            self.execute_call(call, config).to_tool_message()
            for call in tool_calls
        ]
        return {"messages": results}
