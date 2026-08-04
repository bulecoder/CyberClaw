from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from langchain_core.tools import BaseTool


class ToolSource(str, Enum):
    """Where a tool was registered from."""

    BUILTIN = "builtin"
    SKILL = "skill"
    CUSTOM = "custom"


class ToolRisk(str, Enum):
    """Coarse risk label used by future policy decisions."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Immutable capability metadata paired with one LangChain tool."""

    tool: BaseTool = field(compare=False, repr=False)
    source: ToolSource
    risk: ToolRisk = ToolRisk.MEDIUM
    read_only: bool = False
    concurrent_safe: bool = False
    timeout_seconds: float | None = None
    metadata: Mapping[str, Any] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if not isinstance(self.tool, BaseTool):
            raise TypeError("tool 必须是 LangChain BaseTool 实例")
        if not self.tool.name or not self.tool.name.strip():
            raise ValueError("工具名称不能为空")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须大于 0")

        object.__setattr__(self, "source", ToolSource(self.source))
        object.__setattr__(self, "risk", ToolRisk(self.risk))
        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(dict(self.metadata)),
        )

    @property
    def name(self) -> str:
        return self.tool.name

    @classmethod
    def from_tool(
        cls,
        tool: BaseTool,
        *,
        source: ToolSource,
        risk: ToolRisk = ToolRisk.MEDIUM,
        read_only: bool = False,
        concurrent_safe: bool = False,
        timeout_seconds: float | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "ToolSpec":
        return cls(
            tool=tool,
            source=source,
            risk=risk,
            read_only=read_only,
            concurrent_safe=concurrent_safe,
            timeout_seconds=timeout_seconds,
            metadata=metadata or {},
        )
