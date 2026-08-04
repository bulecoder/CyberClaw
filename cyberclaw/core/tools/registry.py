from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Mapping

from langchain_core.tools import BaseTool

from .contracts import ToolRisk, ToolSource, ToolSpec


class ToolRegistrationError(ValueError):
    """Raised when a tool cannot be added to a registry safely."""


class ToolRegistry:
    """Instance-scoped tool capabilities with immutable runtime snapshots."""

    def __init__(
        self,
        specs: Iterable[ToolSpec] = (),
        *,
        frozen: bool = False,
    ) -> None:
        self._specs_by_name: dict[str, ToolSpec] = {}
        self._frozen = False
        for spec in specs:
            self.register(spec)
        self._frozen = frozen

    @staticmethod
    def _key(name: str) -> str:
        return name.strip().casefold()

    @property
    def frozen(self) -> bool:
        return self._frozen

    @property
    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(self._specs_by_name.values())

    @property
    def tools(self) -> tuple[BaseTool, ...]:
        return tuple(spec.tool for spec in self._specs_by_name.values())

    @property
    def names(self) -> frozenset[str]:
        return frozenset(spec.name for spec in self._specs_by_name.values())

    def get(self, name: str) -> ToolSpec | None:
        return self._specs_by_name.get(self._key(name))

    def register(self, spec: ToolSpec) -> None:
        if self._frozen:
            raise ToolRegistrationError("ToolRegistry 快照已冻结，不能继续注册工具")
        if not isinstance(spec, ToolSpec):
            raise TypeError("spec 必须是 ToolSpec")

        key = self._key(spec.name)
        existing = self._specs_by_name.get(key)
        if existing is not None:
            raise ToolRegistrationError(
                f"工具名称冲突：'{spec.name}' 与已注册工具 "
                f"'{existing.name}' 重名"
            )
        self._specs_by_name[key] = spec

    def register_tool(
        self,
        tool: BaseTool,
        *,
        source: ToolSource,
        risk: ToolRisk = ToolRisk.MEDIUM,
        read_only: bool = False,
        concurrent_safe: bool = False,
        timeout_seconds: float | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> ToolSpec:
        spec = ToolSpec.from_tool(
            tool,
            source=source,
            risk=risk,
            read_only=read_only,
            concurrent_safe=concurrent_safe,
            timeout_seconds=timeout_seconds,
            metadata=metadata,
        )
        self.register(spec)
        return spec

    def freeze(self) -> "ToolRegistry":
        self._frozen = True
        return self

    def snapshot(self) -> "ToolRegistry":
        """Return an independent frozen registry for one Agent instance."""
        return ToolRegistry(self.specs, frozen=True)
