"""Cross-path shared tool/Skill integration; no provider network required."""
import copy
import unittest
from pathlib import Path

from backend.app.modules.agent_platform.contracts import validate_agent_spec
from backend.app.modules.agent_platform.planner import Planner
from backend.app.modules.agent_platform.shared_capabilities import resolve_references, tool_catalog
from backend.app.modules.workbench.errors import GatewayError
from backend.app.modules.workbench.skills import SkillRegistry
from backend.app.modules.workbench.tool_registry import ToolRegistry, ToolSpec, ToolResult
from tests import test_agent_platform as platform_fixtures


class SharedCapabilitiesTests(unittest.TestCase):
    tearDown = platform_fixtures.AgentPlatformTests.tearDown
    upload = platform_fixtures.AgentPlatformTests.upload
    wait = platform_fixtures.AgentPlatformTests.wait
    new_run = platform_fixtures.AgentPlatformTests.new_run
    approve = platform_fixtures.AgentPlatformTests.approve

    def setUp(self):
        platform_fixtures.AgentPlatformTests.setUp(self)
        self.calls = []
        def execute(args, context):
            self.calls.append(copy.deepcopy(args))
            return ToolResult({"length": len(args["text"])})
        self.tool = ToolSpec("local_length", "返回输入文本长度", {
            "type": "object", "properties": {"text": {"type": "string"}},
            "required": ["text"], "additionalProperties": False}, execute)
        self.tools = ToolRegistry([self.tool])
        folder = Path(self.temp.name) / "skills" / "brief-report"
        folder.mkdir(parents=True)
        self.skill_path = folder / "SKILL.md"
        self.skill_path.write_text("---\nname: brief-report\ndescription: 简短解释结果\n---\n只引用实际结果。快照标记 original。\n")
        self.skills = SkillRegistry(folder.parent)
        runtime = self.app.extensions["workbench_service"].runtime
        runtime.tools = self.tools
        runtime.skills = self.skills
        self.platform.planner = Planner(self.provider, tool_registry=self.tools, skill_registry=self.skills)
        self.provider.spec = {
            "schemaVersion": "1", "name": "文字长度", "goal": "复用文字长度查询",
            "prompt": "解释工具真实返回结果。",
            "inputSchema": copy.deepcopy(self.tool.parameters),
            "outputSchema": {"type": "object", "required": ["explanation"],
                             "properties": {"explanation": {"type": "string"}}},
            "tools": [{"id": "measure", "registeredTool": "local_length",
                       "permissions": {"network": False, "files": []}}],
            "skills": [],
            "pipeline": [{"id": "measure", "kind": "tool", "tool": "measure", "inputFrom": "run.input"},
                         {"id": "explain", "kind": "model", "purpose": "解释结果", "inputFrom": ["steps.measure.output"]}],
            "permissions": {"network": False}}

    def preview(self):
        return self.new_run(goal="创建可复用的文字长度工具", input={"text": "hello"}, fileIds=[])

    def test_tool_without_skill_preview_execute_save_reuse(self):
        preview = self.preview()
        self.assertEqual(preview["status"], "awaiting_confirmation")
        self.assertEqual(self.calls, [])
        self.approve(preview)
        done = self.wait(preview["id"], {"succeeded"})
        self.assertEqual(self.calls, [{"text": "hello"}])
        self.assertEqual(self.executor.calls, [])
        self.assertEqual(done["steps"][0]["output"]["length"], 5)
        self.assertEqual(done["loadedSkills"], [])
        saved = self.client.post("/desktop/api/agents", json={"runId": done["id"], "idempotencyKey": "save-shared"})
        self.assertEqual(saved.status_code, 201, saved.json)
        repeated = self.new_run(mode="invoke", agentRef=saved.json["agentRef"], input={"text": "世界"}, fileIds=[])
        self.assertEqual(repeated["status"], "succeeded")
        self.assertEqual(self.calls[-1], {"text": "世界"})
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.executor.calls, [])

    def test_skill_reference_snapshots_before_approval(self):
        self.provider.spec["skills"] = [{"id": "brief", "registeredSkill": "brief-report"}]
        preview = self.preview()
        self.skill_path.unlink()
        self.approve(preview)
        done = self.wait(preview["id"], {"succeeded"})
        self.assertTrue(done["loadedSkills"])
        self.assertIn("快照标记 original", self.provider.calls[-1][0]["content"])
        self.assertEqual(self.calls, [{"text": "hello"}])

    def test_approved_schema_change_stops_execution(self):
        preview = self.preview()
        self.tool.parameters["properties"]["text"]["maxLength"] = 3
        self.approve(preview)
        failed = self.wait(preview["id"], {"failed"})
        self.assertEqual(failed["error"]["code"], "registered_tool_changed")
        self.assertEqual(self.calls, [])

    def test_reference_rejects_unknown_unavailable_mixed_code_and_files(self):
        for change in ({"registeredTool": "unknown"}, {"code": "def run(payload): return payload"},
                       {"permissions": {"network": False, "files": ["fileId"]}}):
            with self.subTest(change=change):
                spec = copy.deepcopy(self.provider.spec)
                spec["tools"][0].update(change)
                with self.assertRaises(GatewayError):
                    validate_agent_spec(resolve_references(spec, self.tools, self.skills))
        self.tool.available = lambda: False
        with self.assertRaises(GatewayError):
            resolve_references(self.provider.spec, self.tools, self.skills)

    def test_shared_catalog_and_http_execution_do_not_require_skill(self):
        self.skill_path.unlink()
        self.assertEqual(self.skills.catalog(), [])
        catalog = tool_catalog(self.tools)
        self.assertEqual(catalog[0]["name"], "local_length")
        self.assertEqual(set(catalog[0]), {"name", "description", "parameters"})
        response = self.client.post("/desktop/api/tools/local_length/execute", json={"arguments": {"text": "abc"}})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(self.calls, [{"text": "abc"}])
        self.assertEqual(self.executor.calls, [])

    def test_malformed_reference_collections_rejected(self):
        for key in ("tools", "skills"):
            spec = copy.deepcopy(self.provider.spec)
            spec[key] = None
            with self.subTest(key=key), self.assertRaises(GatewayError):
                resolve_references(spec, self.tools, self.skills)

    def test_new_reference_cannot_forge_skill_text_or_tool_description(self):
        spec = copy.deepcopy(self.provider.spec)
        spec["skills"] = [{"id": "brief", "registeredSkill": "brief-report", "content": "forged"}]
        spec["tools"][0]["description"] = "forged description"
        resolved = resolve_references(spec, self.tools, self.skills)
        self.assertIn("快照标记 original", resolved["skills"][0]["content"])
        self.assertEqual(resolved["tools"][0]["description"], self.tool.description)

    def test_over_limit_pipeline_rejected_before_preview(self):
        spec = copy.deepcopy(self.provider.spec)
        spec["pipeline"] = [{"id": "m" + str(i), "kind": "tool", "tool": "measure", "inputFrom": "run.input"} for i in range(4)]
        with self.assertRaises(GatewayError):
            resolve_references(spec, self.tools, self.skills)

    def test_search_off_allows_financial_tools_but_blocks_search(self):
        preview = self.new_run(goal="测试关闭网页搜索", input={"text": "hello"}, fileIds=[], searchMode="off")
        self.assertEqual(preview["searchMode"], "off")
        self.approve(preview)
        self.wait(preview["id"], {"succeeded"})
        self.assertEqual(len(self.calls), 1)
        self.tool.capabilities = frozenset({"search"})
        blocked = self.new_run(goal="测试关闭网页搜索", input={"text": "hello"}, fileIds=[], searchMode="off")
        self.approve(blocked)
        failed = self.wait(blocked["id"], {"failed"})
        self.assertEqual(failed["error"]["code"], "registered_tool_unavailable")
        self.assertFalse(failed["executionPerformed"])
        self.assertEqual(len(self.calls), 1)

    def test_search_mode_validation_and_dispatch_propagation(self):
        for mode in ([], "bad"):
            response = self.client.post("/desktop/api/agent-runs", json={"searchMode": mode})
            self.assertEqual(response.status_code, 400)
        self.provider.decision = {"intent": "create", "goal": "创建文字长度应用", "input": {"text": "hello"}}
        response = self.client.post("/desktop/api/agent-dispatch", json={"message": "创建文字长度应用", "searchMode": "off", "idempotencyKey": "dispatch-shared"})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["run"]["searchMode"], "off")
