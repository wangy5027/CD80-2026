APP_INTERFACE = {
    "name": "两数求和 Python 示例",
    "description": "完整单文件 APP：接收 a 与 b，返回它们的和。",
    "inputSchema": {
        "type": "object",
        "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
        "required": ["a", "b"],
        "additionalProperties": False,
    },
    "outputSchema": {
        "type": "object",
        "properties": {"sum": {"type": "number"}},
        "required": ["sum"],
    },
    "rules": ["返回 a 与 b 的和。"],
    "testCases": [{
        "label": "原任务用例：1 + 2",
        "input": {"a": 1, "b": 2},
        "fileBindings": {},
        "expectation": {"mode": "json_equals", "value": {"result": {"sum": 3}}},
    }],
}


def run(payload):
    return {"sum": payload["a"] + payload["b"]}
