"""Small, provider-neutral registry for executable model tools.

Skills are instructions.  Only handlers registered here can perform work.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from threading import Event
from typing import Any, Callable, Iterable

from .errors import GatewayError


_TOOL_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_MAX_RESULT_BYTES = 512 * 1024


def _available() -> bool:
    return True


@dataclass
class ToolContext:
    stopped: Event
    on_response: Callable[[Any], None]
    emit: Callable[[dict], None]
    query: str = ""
    required: bool = False


@dataclass
class ToolResult:
    content: dict
    sources: list[dict] | None = None


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict
    handler: Callable[[dict, ToolContext], ToolResult]
    capabilities: frozenset[str] = field(default_factory=frozenset)
    available: Callable[[], bool] = _available
    max_calls: int = 3
    fallback_arguments: Callable[[str], dict] | None = None


class ToolRegistry:
    """Advertise and invoke explicitly registered tools within capability limits."""

    def __init__(self, tools: Iterable[ToolSpec] = ()):
        self._tools: dict[str, ToolSpec] = {}
        for tool in tools:
            self.register(tool)

    def register(self, spec: ToolSpec) -> None:
        if not isinstance(spec, ToolSpec):
            raise ValueError("工具配置无效。")
        if not isinstance(spec.name, str) or not _TOOL_NAME.fullmatch(spec.name) or spec.name == "load_skill":
            raise ValueError("工具名称无效或已保留。")
        if spec.name in self._tools:
            raise ValueError("工具名称重复。")
        if (
            not isinstance(spec.description, str)
            or not spec.description.strip()
            or not isinstance(spec.parameters, dict)
            or not callable(spec.handler)
            or not callable(spec.available)
            or type(spec.max_calls) is not int
            or spec.max_calls < 1
            or (spec.fallback_arguments is not None and not callable(spec.fallback_arguments))
            or not isinstance(spec.capabilities, frozenset)
            or any(not isinstance(capability, str) or not capability for capability in spec.capabilities)
        ):
            raise ValueError("工具配置无效。")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name) if isinstance(name, str) else None

    def catalog(self, *, include_parameters: bool = False) -> list[dict]:
        """Expose public tool metadata without handlers or credentials."""
        result = []
        for spec in self._tools.values():
            item = {"name": spec.name, "description": spec.description,
                    "available": self._available(spec),
                    "capabilities": sorted(spec.capabilities)}
            if include_parameters:
                item["parameters"] = copy.deepcopy(spec.parameters)
            result.append(item)
        return result

    @staticmethod
    def _blocked(spec: ToolSpec, blocked_capabilities: Iterable[str]) -> bool:
        blocked = {blocked_capabilities} if isinstance(blocked_capabilities, str) else set(blocked_capabilities)
        return not spec.capabilities.isdisjoint(blocked)

    @staticmethod
    def _available(spec: ToolSpec) -> bool:
        try:
            return bool(spec.available())
        except Exception:
            # Provider status exceptions must not leak through a tool catalogue.
            return False

    def definitions(self, blocked_capabilities: Iterable[str] = ()) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": copy.deepcopy(spec.parameters),
                },
            }
            for spec in self._tools.values()
            if not self._blocked(spec, blocked_capabilities) and self._available(spec)
        ]

    def first_available(self, capability: str) -> ToolSpec | None:
        for spec in self._tools.values():
            if capability in spec.capabilities and self._available(spec):
                return spec
        return None

    def has_capability(self, capability: str) -> bool:
        return self.first_available(capability) is not None

    def is_available(self, name: str, blocked_capabilities: Iterable[str] = ()) -> bool:
        spec = self.get(name)
        return bool(spec and not self._blocked(spec, blocked_capabilities) and self._available(spec))

    def execute(
        self,
        name: str,
        arguments: dict,
        context: ToolContext,
        *,
        blocked_capabilities: Iterable[str] = (),
        call_count: int = 0,
    ) -> ToolResult:
        spec = self.get(name)
        if spec is None:
            raise GatewayError("该工具未注册。", 400)
        if self._blocked(spec, blocked_capabilities):
            raise GatewayError("该工具在本轮不可用。", 403)
        if not self._available(spec):
            raise GatewayError("工具暂时不可用。", 503)
        if not isinstance(arguments, dict):
            raise GatewayError("工具参数必须是 JSON 对象。", 400)
        if type(call_count) is not int or call_count < 0:
            raise GatewayError("工具调用次数无效。", 400)
        if call_count >= spec.max_calls:
            raise GatewayError("本轮工具调用次数已达到上限。", 429)
        if context.stopped.is_set():
            raise GatewayError("已停止生成。", 409)
        try:
            result = spec.handler(arguments, context)
        except GatewayError:
            raise
        except Exception:
            raise GatewayError("工具执行失败，请稍后重试。", 502) from None
        if context.stopped.is_set():
            raise GatewayError("已停止生成。", 409)
        if not isinstance(result, ToolResult) or not isinstance(result.content, dict) or (result.sources is not None and not isinstance(result.sources, list)):
            raise GatewayError("工具返回的数据格式无效。", 502)
        if result.sources is not None and any(not isinstance(source, dict) for source in result.sources):
            raise GatewayError("工具返回的数据格式无效。", 502)
        try:
            encoded = json.dumps({"content": result.content, "sources": result.sources},
                                 ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError, UnicodeError):
            raise GatewayError("工具返回的数据格式无效。", 502) from None
        if len(encoded) > _MAX_RESULT_BYTES:
            raise GatewayError("工具返回的数据过大，请缩小查询范围。", 502)
        return result
