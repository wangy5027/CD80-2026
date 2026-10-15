"""Validated data contracts. Generated source is never imported by this module."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re

from ..workbench.errors import GatewayError

MAX_STEPS = 8
MAX_SOURCE = 60000
TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}
IDENTIFIER = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,95}$")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def invalid(message):
    raise GatewayError(message, 400, "invalid_agent_contract")


def text(value, label, limit=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        invalid(f"{label}必须是非空文本，最长 {limit} 字符。")
    return value


def identifier(value, label="标识"):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        invalid(f"{label}格式不正确。")
    return value


def check_schema(schema, depth=0):
    if not isinstance(schema, dict) or depth > 8:
        invalid("参数 schema 格式不正确或层级过深。")
    allowed = {"type", "properties", "required", "additionalProperties", "items", "enum", "const", "description", "title", "default", "minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems"}
    if set(schema) - allowed:
        invalid("schema 含当前不支持的字段：" + ", ".join(sorted(set(schema) - allowed)))
    kind = schema.get("type")
    if kind is not None and (not isinstance(kind, str) or kind not in {"object", "string", "integer", "number", "boolean", "array", "null"}):
        invalid("schema type 不受支持。")
    if "properties" in schema:
        if not isinstance(schema["properties"], dict) or len(schema["properties"]) > 40:
            invalid("schema properties 不正确。")
        for name, child in schema["properties"].items():
            identifier(name, "参数名")
            check_schema(child, depth + 1)
    required = schema.get("required", [])
    if not isinstance(required, list) or any(not isinstance(x, str) for x in required) or not set(required) <= set(schema.get("properties", {})):
        invalid("schema required 必须引用已声明参数。")
    if "items" in schema:
        check_schema(schema["items"], depth + 1)
    if "additionalProperties" in schema and not isinstance(schema["additionalProperties"], bool):
        invalid("additionalProperties 只支持布尔值。")
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
        invalid("enum 必须为非空数组。")
    for key in ("minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems"):
        if key in schema and (type(schema[key]) not in (int, float) or not math.isfinite(schema[key])):
            invalid(f"schema {key} 不正确。")
    return schema


def missing_inputs(schema, value):
    if not isinstance(value, dict):
        return list(schema.get("required", []))
    return [name for name in schema.get("required", []) if name not in value or value[name] is None or value[name] == ""]


def validate_input(schema, value, path="input"):
    check_schema(schema)
    kind = schema.get("type")
    valid = {None: True, "object": isinstance(value, dict), "string": isinstance(value, str),
             "integer": type(value) is int, "number": type(value) in (int, float) and math.isfinite(value),
             "boolean": type(value) is bool, "array": isinstance(value, list), "null": value is None}
    if not valid.get(kind, False):
        invalid(f"{path} 类型应为 {kind}。")
    if "const" in schema and value != schema["const"]:
        invalid(f"{path} 不符合固定值。")
    if "enum" in schema and value not in schema["enum"]:
        invalid(f"{path} 不在允许范围内。")
    if isinstance(value, dict):
        missing = missing_inputs(schema, value)
        if missing:
            invalid("缺少必要参数：" + ", ".join(missing))
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False and set(value) - set(props):
            invalid(f"{path} 含未声明参数。")
        for name, child in props.items():
            if name in value:
                validate_input(child, value[name], path + "." + name)
    if isinstance(value, list):
        if len(value) > schema.get("maxItems", 100000) or len(value) < schema.get("minItems", 0):
            invalid(f"{path} 数组长度超限。")
        for item in value:
            if "items" in schema:
                validate_input(schema["items"], item, path + "[]")
    if isinstance(value, str) and not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 200000):
        invalid(f"{path} 文本长度不合要求。")
    if type(value) in (int, float) and not schema.get("minimum", -math.inf) <= value <= schema.get("maximum", math.inf):
        invalid(f"{path} 数值超限。")
    return copy.deepcopy(value)


def _permissions(value):
    if not isinstance(value, dict) or set(value) - {"network", "files"} or value.get("network", False) is not False:
        invalid("首版仅支持明确文件授权，工具不可联网。")
    files = value.get("files", [])
    if not isinstance(files, list) or len(files) > 8 or any(not isinstance(x, str) for x in files):
        invalid("文件权限声明格式不正确。")
    return {"network": False, "files": files}


def capability_reference(value, kind):
    fields = {"assetId", "versionId", "configurationId", "sourceIdentity", "contentDigest", "kind"}
    if not isinstance(value, dict) or set(value) != fields or value.get("kind") != kind:
        invalid("能力引用必须包含精确个人版本、配置和内容身份。")
    for key in fields - {"kind"}:
        text(value[key], "能力引用 " + key, 400)
    if not re.fullmatch(r"[a-f0-9]{64}", value["contentDigest"]):
        invalid("能力内容摘要格式不正确。")


def validate_agent_spec(value):
    if not isinstance(value, dict):
        invalid("agent 定义必须为 JSON 对象。")
    spec = copy.deepcopy(value)
    allowed = {"schemaVersion", "id", "version", "origin", "name", "description", "goal", "prompt", "inputSchema", "outputSchema", "tools", "skills", "pipeline", "permissions"}
    if set(spec) - allowed:
        invalid("agent 定义存在未知字段：" + ", ".join(sorted(set(spec) - allowed)))
    if spec.get("schemaVersion") != "1":
        invalid("不支持的 agent schemaVersion。")
    for key in ("name", "goal", "prompt"):
        text(spec.get(key), key, 12000 if key == "prompt" else 2000)
    spec.setdefault("description", spec["goal"])
    text(spec["description"], "description", 4000)
    if "id" in spec:
        identifier(spec["id"])
    spec.setdefault("version", "1")
    identifier(spec["version"], "版本")
    spec.setdefault("origin", "generated")
    if not isinstance(spec["origin"], str) or spec["origin"] not in {"generated", "preset"}:
        invalid("agent origin 无效。")
    for key in ("inputSchema", "outputSchema"):
        check_schema(spec.get(key))
    if spec["inputSchema"].get("type") != "object":
        invalid("agent 输入必须是对象。")
    spec["permissions"] = _permissions(spec.get("permissions", {}))
    tools = spec.get("tools")
    skills = spec.get("skills")
    if not isinstance(tools, list) or not 1 <= len(tools) <= 8 or not isinstance(skills, list) or not 0 <= len(skills) <= 8:
        invalid("agent 需要 1–8 个工具和 0–8 个可选 skill。")
    tool_ids = set()
    for tool in tools:
        if not isinstance(tool, dict) or set(tool) - {"id", "version", "code", "registeredTool", "capabilityRef", "entrypoint", "inputSchema", "outputSchema", "permissions", "description"}:
            invalid("tool 定义格式不正确。")
        identifier(tool.get("id"), "tool id")
        if tool["id"] in tool_ids:
            invalid("tool id 重复。")
        tool_ids.add(tool["id"])
        tool.setdefault("version", "1")
        identifier(tool["version"], "工具版本")
        if "capabilityRef" in tool:
            capability_reference(tool['capabilityRef'], 'tool')
            if "code" in tool or "registeredTool" in tool:
                invalid("个人能力引用不能同时包含代码或内置工具引用。")
        elif "registeredTool" in tool:
            identifier(tool["registeredTool"], "平台工具名称")
            if "code" in tool:
                invalid("平台工具引用不能同时包含代码。")
        else:
            text(tool.get("code"), "工具代码", MAX_SOURCE)
        if tool.get("entrypoint", "run") != "run":
            invalid("工具入口必须是 run(payload)。")
        tool["entrypoint"] = "run"
        tool["permissions"] = _permissions(tool.get("permissions", {}))
        if "registeredTool" in tool and tool["permissions"]["files"]:
            invalid("平台查询工具不接收文件授权或宿主路径。")
        for field in ("inputSchema", "outputSchema"):
            tool.setdefault(field, {"type": "object"})
            check_schema(tool[field])
    skill_ids = set()
    for skill in skills:
        if not isinstance(skill, dict) or set(skill) - {"id", "version", "content", "registeredSkill", "capabilityRef", "requiredTools", "description"}:
            invalid("skill 定义格式不正确。")
        identifier(skill.get("id"), "skill id")
        if skill["id"] in skill_ids:
            invalid("skill id 重复。")
        skill_ids.add(skill["id"])
        skill.setdefault("version", "1")
        identifier(skill["version"], "skill 版本")
        text(skill.get("content"), "skill 内容", 12000)
        if "capabilityRef" in skill:
            capability_reference(skill['capabilityRef'], 'skill')
            if "registeredSkill" in skill:
                invalid("个人Skill引用不能同时引用内置Skill。")
        if "registeredSkill" in skill:
            identifier(skill["registeredSkill"], "Skill 引用")
        required = skill.get("requiredTools", [])
        if not isinstance(required, list) or any(not isinstance(x, str) or x not in tool_ids for x in required):
            invalid("skill 引用了不存在的 tool。")
        skill["requiredTools"] = required
    pipeline = spec.get("pipeline")
    if not isinstance(pipeline, list) or not 1 <= len(pipeline) <= MAX_STEPS:
        invalid("pipeline 需要 1–8 个顺序步骤。")
    seen, model_count = set(), 0
    for step in pipeline:
        if not isinstance(step, dict) or set(step) - {"id", "kind", "tool", "purpose", "inputFrom", "name"}:
            invalid("步骤定义格式不正确。")
        identifier(step.get("id"), "步骤 id")
        if step["id"] in seen:
            invalid("步骤 id 重复。")
        if step.get("kind") == "tool":
            if not isinstance(step.get("tool"), str) or step.get("tool") not in tool_ids or model_count:
                invalid("工具步骤必须引用已定义 tool，并位于解释步骤之前。")
        elif step.get("kind") == "model":
            model_count += 1
            if model_count > 1 or step is not pipeline[-1]:
                invalid("首版只支持末尾一个模型解释步骤。")
            text(step.get("purpose"), "解释任务", 2000)
        else:
            invalid("只支持 tool/model 步骤，不支持递归、并行或任意代码节点。")
        refs = step.get("inputFrom", "run.input")
        refs = refs if isinstance(refs, list) else [refs]
        if not refs or len(refs) > 8:
            invalid("步骤输入引用无效。")
        for ref in refs:
            if not isinstance(ref, str):
                invalid("步骤输入引用必须为字符串。")
            if ref != "run.input":
                match = re.fullmatch(r"steps\.([A-Za-z0-9_-]+)\.output", ref)
                if not match or match.group(1) not in seen:
                    invalid("步骤只能引用原始输入或前面步骤的输出。")
        step["inputFrom"] = refs if isinstance(step.get("inputFrom"), list) else refs[0]
        seen.add(step["id"])
    if model_count != 1 or not any(step["kind"] == "tool" for step in pipeline):
        invalid("pipeline 必须包含工具执行与末尾的模型解释，以实际使用 prompt 和 skill。")
    if len(canonical(spec)) > 180000:
        invalid("agent 定义过大。")
    return spec
