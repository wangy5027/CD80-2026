"""Authenticated public tool and Skill endpoints without external network calls."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app import create_app
from backend.app.modules.workbench.tool_registry import ToolRegistry, ToolResult, ToolSpec


class ToolApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ficagent-tool-api-")
        root = Path(self.temp.name)
        skill = root / "skills" / "market-guide"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: market-guide\ndescription: 测试市场数据使用说明。\n---\n引用真实查询结果。\n",
            encoding="utf-8",
        )
        self.app = create_app({"TESTING": True, "DATA_DIR": str(root / "data"),
                               "SKILLS_DIR": str(root / "skills"), "GITEE_AI_API_KEY": ""})
        self.backend = self.app.extensions["workbench_service"]
        self.called = []

        def echo(arguments, _context):
            self.called.append(arguments)
            return ToolResult({"echo": arguments["text"]})

        self.backend.runtime.tools = ToolRegistry((
            ToolSpec("market_echo", "回显测试市场参数。",
                     {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
                     echo, capabilities=frozenset({"financial_data"}), max_calls=1),
        ))
        self.client = self.app.test_client()

    def tearDown(self):
        self.backend.close()
        self.temp.cleanup()

    def test_auth_catalog_load_execute_and_unknown_tool(self):
        for path in ("/desktop/api/tools", "/desktop/api/skills", "/desktop/api/skills/market-guide"):
            self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.post("/desktop/api/tools/market_echo/execute", json={"arguments": {"text": "x"}}).status_code, 401)

        registered = self.client.post("/desktop/api/register", json={"email": "api@example.invalid", "password": "test-password"})
        self.assertEqual(registered.status_code, 200)
        runtime = self.client.get("/desktop/api/runtime").json
        self.assertEqual(runtime["skills"], [{"name": "market-guide", "description": "测试市场数据使用说明。"}])
        self.assertFalse(runtime["searchAvailable"])
        self.assertEqual(runtime["tools"], [{"name": "market_echo", "description": "回显测试市场参数。",
                                             "available": True, "capabilities": ["financial_data"]}])
        self.assertNotIn("instructions", str(runtime))
        tools = self.client.get("/desktop/api/tools").json["tools"]
        self.assertEqual(tools[0]["parameters"]["required"], ["text"])
        self.assertNotIn("handler", str(tools))
        self.assertEqual(self.client.get("/desktop/api/skills").json["skills"][0]["name"], "market-guide")
        loaded = self.client.get("/desktop/api/skills/market-guide")
        self.assertEqual(loaded.status_code, 200)
        self.assertIn("引用真实查询结果", loaded.json["instructions"])
        self.assertEqual(loaded.headers["Cache-Control"], "no-store")

        executed = self.client.post("/desktop/api/tools/market_echo/execute", json={"arguments": {"text": "独立调用"}})
        self.assertEqual(executed.status_code, 200, executed.json)
        self.assertEqual(executed.json, {"name": "market_echo", "result": {"echo": "独立调用"}, "sources": []})
        self.assertEqual(self.called, [{"text": "独立调用"}])
        self.assertEqual(self.client.post("/desktop/api/tools/market_echo/execute", json={"arguments": []}).status_code, 400)
        self.assertEqual(self.client.post("/desktop/api/tools/not_registered/execute", json={"arguments": {}}).status_code, 400)
        self.assertEqual(self.client.get("/desktop/api/skills/missing").status_code, 404)
        self.assertEqual(self.client.get("/desktop/api/skills/../market-guide").status_code, 404)


if __name__ == "__main__":
    unittest.main()
