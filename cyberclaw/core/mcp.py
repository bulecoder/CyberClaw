from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import threading
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import mcp_types
from langchain_core.tools import StructuredTool, ToolException
from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client

from .config import WORKSPACE_DIR
from .environment import PROJECT_ROOT
from .tools.contracts import ToolRisk, ToolSource, ToolSpec


DEFAULT_MCP_CONFIG_PATH = Path(WORKSPACE_DIR) / "mcp_servers.json"
_ENV_REFERENCE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
_STOP = object()


class MCPConfigurationError(ValueError):
    """Raised when local MCP server configuration is unsafe or malformed."""


class MCPManagerError(RuntimeError):
    """Raised when the MCP manager cannot start or dispatch a request."""


@dataclass(frozen=True, slots=True)
class MCPServerConfig:
    name: str
    command: str
    args: tuple[str, ...] = ()
    cwd: Path | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not self.name or not self.command.strip():
            raise MCPConfigurationError("MCP server 名称和 command 不能为空")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise MCPConfigurationError("MCP timeout_seconds 必须大于 0")
        object.__setattr__(self, "args", tuple(self.args))
        object.__setattr__(self, "env", MappingProxyType(dict(self.env)))


@dataclass(slots=True)
class _MCPCall:
    server_name: str
    tool_name: str
    arguments: dict[str, Any]
    timeout_seconds: float
    future: Future[mcp_types.CallToolResult]


def _resolve_env(raw_env: object, server_name: str) -> dict[str, str]:
    if raw_env is None:
        return {}
    if not isinstance(raw_env, dict):
        raise MCPConfigurationError(f"MCP server '{server_name}' 的 env 必须是对象")

    resolved: dict[str, str] = {}
    for raw_key, raw_value in raw_env.items():
        if not isinstance(raw_key, str) or not isinstance(raw_value, str):
            raise MCPConfigurationError(
                f"MCP server '{server_name}' 的 env 键值必须是字符串"
            )
        match = _ENV_REFERENCE.fullmatch(raw_value)
        if match:
            variable = match.group(1)
            value = os.getenv(variable)
            if value is None:
                raise MCPConfigurationError(
                    f"MCP server '{server_name}' 缺少环境变量 {variable}"
                )
            resolved[raw_key] = value
        else:
            resolved[raw_key] = raw_value
    return resolved


def load_mcp_server_configs(
    path: str | Path | None = None,
) -> tuple[MCPServerConfig, ...]:
    """Load explicitly trusted stdio servers; a missing file disables MCP."""

    config_path = Path(
        path or os.getenv("CYBERCLAW_MCP_CONFIG", DEFAULT_MCP_CONFIG_PATH)
    )
    if not config_path.exists():
        return ()
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MCPConfigurationError(f"无法读取 MCP 配置：{config_path}") from exc

    servers = payload.get("servers") if isinstance(payload, dict) else None
    if not isinstance(servers, dict):
        raise MCPConfigurationError("MCP 配置必须包含 servers 对象")

    configs: list[MCPServerConfig] = []
    for name, raw in servers.items():
        if not isinstance(name, str) or not isinstance(raw, dict):
            raise MCPConfigurationError("MCP server 名称和配置必须是对象")
        enabled = raw.get("enabled", True)
        if not isinstance(enabled, bool):
            raise MCPConfigurationError(
                f"MCP server '{name}' 的 enabled 必须是布尔值"
            )
        if not enabled:
            continue
        if raw.get("trusted") is not True:
            raise MCPConfigurationError(
                f"MCP server '{name}' 必须显式设置 trusted=true"
            )
        command = raw.get("command")
        args = raw.get("args", [])
        if not isinstance(command, str) or not isinstance(args, list) or not all(
            isinstance(argument, str) for argument in args
        ):
            raise MCPConfigurationError(
                f"MCP server '{name}' 的 command/args 格式无效"
            )
        raw_cwd = raw.get("cwd", ".")
        if not isinstance(raw_cwd, str):
            raise MCPConfigurationError(f"MCP server '{name}' 的 cwd 必须是字符串")
        cwd = Path(raw_cwd)
        if not cwd.is_absolute():
            cwd = PROJECT_ROOT / cwd
        try:
            timeout_seconds = float(raw.get("timeout_seconds", 30.0))
        except (TypeError, ValueError) as exc:
            raise MCPConfigurationError(
                f"MCP server '{name}' 的 timeout_seconds 必须是数字"
            ) from exc
        configs.append(MCPServerConfig(
            name=name,
            command=command,
            args=tuple(args),
            cwd=cwd.resolve(),
            env=_resolve_env(raw.get("env"), name),
            timeout_seconds=timeout_seconds,
        ))
    return tuple(configs)


def _tool_name(server_name: str, remote_name: str) -> str:
    def segment(value: str) -> str:
        normalized = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")
        if not normalized:
            raise MCPConfigurationError("MCP server/tool 名称无法转换为安全名称")
        return normalized

    name = f"mcp__{segment(server_name)}__{segment(remote_name)}"
    if len(name) <= 64:
        return name
    suffix = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
    return f"{name[:55]}_{suffix}"


def _tool_profile(tool: mcp_types.Tool) -> tuple[ToolRisk, bool]:
    annotations = tool.annotations
    if annotations is None:
        return ToolRisk.MEDIUM, False
    read_only = annotations.read_only_hint is True
    if annotations.destructive_hint is True or annotations.open_world_hint is True:
        return ToolRisk.HIGH, read_only
    if read_only:
        return ToolRisk.LOW, True
    return ToolRisk.MEDIUM, False


def _render_result(result: mcp_types.CallToolResult) -> str:
    if result.result_type == "input_required":
        raise ToolException("该 MCP 工具需要当前客户端尚未支持的额外输入")

    parts = [
        block.text
        for block in result.content
        if isinstance(block, mcp_types.TextContent)
    ]
    if parts:
        rendered = "\n".join(parts)
    elif result.structured_content is not None:
        rendered = json.dumps(
            result.structured_content,
            ensure_ascii=False,
            default=str,
        )
    else:
        content_types = ", ".join(block.type for block in result.content) or "empty"
        rendered = f"[MCP result content: {content_types}]"
    if result.is_error:
        raise ToolException(rendered or "MCP server 返回工具错误")
    return rendered


class MCPClientManager:
    """Own stdio clients on one background task and expose sync LangChain tools."""

    def __init__(self, configs: tuple[MCPServerConfig, ...] = ()) -> None:
        self._configs = configs
        self._ready = threading.Event()
        self._state_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[_MCPCall | object] | None = None
        self._clients: dict[str, Client] = {}
        self._discovered: list[tuple[MCPServerConfig, mcp_types.Tool]] = []
        self._errors: dict[str, str] = {}
        self._startup_error: BaseException | None = None
        self._closed = False

    @property
    def errors(self) -> Mapping[str, str]:
        return MappingProxyType(dict(self._errors))

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    async def _serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        try:
            async with AsyncExitStack() as stack:
                for config in self._configs:
                    try:
                        parameters = StdioServerParameters(
                            command=config.command,
                            args=list(config.args),
                            cwd=config.cwd,
                            env=dict(config.env),
                        )
                        async with asyncio.timeout(config.timeout_seconds):
                            client = await stack.enter_async_context(Client(
                                stdio_client(parameters),
                                read_timeout_seconds=config.timeout_seconds,
                            ))
                            tools = await client.list_tools()
                        self._clients[config.name] = client
                        self._discovered.extend(
                            (config, tool) for tool in tools.tools
                        )
                    except Exception as exc:
                        self._errors[config.name] = type(exc).__name__
                self._ready.set()

                while True:
                    request = await self._queue.get()
                    if request is _STOP:
                        return
                    assert isinstance(request, _MCPCall)
                    if not request.future.set_running_or_notify_cancel():
                        continue
                    try:
                        client = self._clients[request.server_name]
                        async with asyncio.timeout(request.timeout_seconds):
                            result = await client.call_tool(
                                request.tool_name,
                                request.arguments,
                                read_timeout_seconds=request.timeout_seconds,
                            )
                    except Exception as exc:
                        request.future.set_exception(exc)
                    else:
                        request.future.set_result(result)
        finally:
            self._ready.set()

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._serve())
        except BaseException as exc:
            self._startup_error = exc
            self._ready.set()

    def start(self) -> tuple[ToolSpec, ...]:
        with self._state_lock:
            if self._closed:
                raise MCPManagerError("MCP manager 已关闭")
            if self._thread is not None:
                raise MCPManagerError("MCP manager 不能重复启动")
            if not self._configs:
                return ()
            self._thread = threading.Thread(
                target=self._thread_main,
                name="cyberclaw-mcp",
                daemon=True,
            )
            self._thread.start()

        startup_timeout = sum(config.timeout_seconds for config in self._configs) + 2
        if not self._ready.wait(startup_timeout):
            raise MCPManagerError("MCP server 启动超时")
        if self._startup_error is not None:
            raise MCPManagerError("MCP manager 启动失败") from self._startup_error
        try:
            return tuple(
                self._build_spec(config, tool)
                for config, tool in self._discovered
            )
        except Exception as exc:
            self.close()
            raise MCPManagerError("MCP 工具描述转换失败") from exc

    def _build_spec(
        self,
        config: MCPServerConfig,
        remote_tool: mcp_types.Tool,
    ) -> ToolSpec:
        local_name = _tool_name(config.name, remote_tool.name)

        def invoke_remote(**arguments: Any) -> str:
            result = self.call_tool(
                config.name,
                remote_tool.name,
                arguments,
                timeout_seconds=config.timeout_seconds,
            )
            return _render_result(result)

        tool = StructuredTool(
            name=local_name,
            description=remote_tool.description or (
                f"MCP tool {remote_tool.name} from server {config.name}"
            ),
            args_schema=dict(remote_tool.input_schema),
            func=invoke_remote,
            handle_tool_error=True,
        )
        risk, read_only = _tool_profile(remote_tool)
        return ToolSpec.from_tool(
            tool,
            source=ToolSource.MCP,
            risk=risk,
            read_only=read_only,
            concurrent_safe=False,
            timeout_seconds=config.timeout_seconds,
            metadata={
                "mcp_server": config.name,
                "mcp_tool": remote_tool.name,
                "transport": "stdio",
            },
        )

    def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> mcp_types.CallToolResult:
        if self._loop is None or self._queue is None or not self.running:
            raise MCPManagerError("MCP manager 未运行")
        future: Future[mcp_types.CallToolResult] = Future()
        request = _MCPCall(
            server_name=server_name,
            tool_name=tool_name,
            arguments=arguments,
            timeout_seconds=timeout_seconds,
            future=future,
        )
        self._loop.call_soon_threadsafe(self._queue.put_nowait, request)
        try:
            return future.result(timeout=timeout_seconds + 2)
        except FutureTimeoutError as exc:
            future.cancel()
            raise TimeoutError(f"MCP tool '{tool_name}' 执行超时") from exc

    def close(self, timeout: float = 15.0) -> bool:
        with self._state_lock:
            if self._closed:
                return not self.running
            self._closed = True
            thread = self._thread
            loop = self._loop
            request_queue = self._queue
        if thread is None:
            return True
        if loop is not None and request_queue is not None and thread.is_alive():
            loop.call_soon_threadsafe(request_queue.put_nowait, _STOP)
        thread.join(timeout=max(0.0, timeout))
        return not thread.is_alive()
