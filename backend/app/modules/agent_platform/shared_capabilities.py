"""Resolve explicit platform references before a plan is shown for approval.

Generated Python never receives credentials or a host execution capability.
Skill text and tool parameter schemas are frozen into the proposed definition.
"""
from __future__ import annotations

import copy

from ..workbench.errors import GatewayError


def resolve_references(value, tools=None, skills=None):
    if not isinstance(value, dict):
        raise GatewayError("应用定义必须是 JSON 对象。", 400)
    spec = copy.deepcopy(value)
    for key in ("tools", "skills"):
        if key in spec and not isinstance(spec[key], list):
            raise GatewayError("工具和 Skill 列表必须是数组。", 400)
    for tool in spec.get("tools", []):
        if not isinstance(tool, dict) or "registeredTool" not in tool:
            continue
        name = tool["registeredTool"]
        entry = tools.get(name) if tools is not None else None
        if entry is None or not tools.is_available(name):
            raise GatewayError("引用的平台工具不存在或尚未配置。", 400, "registered_tool_unavailable")
        if "code" in tool:
            raise GatewayError("平台工具引用不能同时包含生成代码。", 400)
        tool["inputSchema"] = copy.deepcopy(entry.parameters)
        tool.setdefault("outputSchema", {"type": "object"})
        tool["description"] = entry.description
    for skill in spec.get("skills", []):
        if not isinstance(skill, dict) or "registeredSkill" not in skill:
            continue
        # New previews always load the real registry text. Saved definitions
        # execute their stored snapshot without calling this resolver again.
        try:
            loaded = skills.load(skill["registeredSkill"]) if skills is not None else None
        except (KeyError, ValueError, OSError, TypeError):
            loaded = None
        if not loaded:
            raise GatewayError("引用的 Skill 不存在或不可读取。", 400, "registered_skill_unavailable")
        skill["content"] = loaded["instructions"]
    by_id = {tool.get("id"): tool for tool in spec.get("tools", [])
             if isinstance(tool, dict) and isinstance(tool.get("id"), str)}
    counts = {}
    pipeline = spec.get("pipeline", [])
    if isinstance(pipeline, list):
        for step in pipeline:
            if not isinstance(step, dict) or not isinstance(step.get("tool"), str):
                continue
            name = by_id.get(step["tool"], {}).get("registeredTool")
            if isinstance(name, str):
                counts[name] = counts.get(name, 0) + 1
                entry = tools.get(name) if tools is not None else None
                if entry and counts[name] > entry.max_calls:
                    raise GatewayError("流程超过该平台工具的单次调用上限，请减少查询步骤。", 400)
    return spec


def tool_catalog(registry, *, schemas=True):
    if registry is None:
        return []
    result = []
    for definition in registry.definitions():
        function = definition["function"]
        item = {"name": function["name"], "description": function["description"]}
        if schemas:
            item["parameters"] = function["parameters"]
        result.append(item)
    return result
