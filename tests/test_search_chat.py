"""Search/skill SSE persistence and account boundaries at the HTTP layer."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app import create_app
from backend.app.modules.workbench.errors import GatewayError
from backend.app.modules.workbench.skills import SkillRegistry
from backend.app.modules.workbench.tool_registry import ToolRegistry


SOURCE = {"id": "source-test", "title": "新闻来源", "url": "https://example.com/news", "publishedAt": "2026-10-12T10:00:00+08:00", "snippet": "仅作测试的新闻摘要。"}


class SearchChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        skill_dir = Path(self.temp.name) / "skills" / "news-search"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: news-search\ndescription: 测试搜索指令。\n---\n搜索真实来源。\n", encoding="utf-8")
        self.config = {"TESTING": True, "DATA_DIR": self.temp.name, "SKILLS_DIR": str(skill_dir.parent),
                       "GITEE_AI_API_KEY": "fixture-secret", "IMPORT_LEGACY_HISTORY": False}
        self.app = create_app(self.config)
        self.backend = self.app.extensions["workbench_service"]
        self.client = self.app.test_client()
        response = self.client.post("/desktop/api/register", json={"email": "alice@example.invalid", "password": "test-password"})
        self.assertEqual(response.status_code, 200)

    def tearDown(self):
        self.backend.close()
        self.temp.cleanup()

    def stream(self, *_args, **kwargs):
        self.mode = kwargs["search_mode"]
        yield {"type": "skill_loaded", "name": "news-search"}
        yield {"type": "search_status", "status": "searching", "query": "测试新闻"}
        yield {"type": "tool_status", "name": "web_search", "status": "running"}
        yield {"type": "search_sources", "sources": [SOURCE]}
        yield {"type": "search_sources", "sources": [SOURCE]}
        yield {"type": "tool_status", "name": "web_search", "status": "complete"}
        yield {"type": "tool_status", "name": "web_search", "status": "complete"}
        yield {"type": "token", "text": "有来源的回答。"}
        yield {"type": "complete", "message": {"role": "assistant", "content": "有来源的回答。", "sources": [SOURCE],
                                              "skillsUsed": ["news-search"], "toolsUsed": ["web_search"]}}

    def test_sse_sources_skill_and_mode_survive_restart(self):
        with patch.object(self.backend.runtime, "stream", self.stream):
            response = self.client.post("/desktop/api/chat", json={"message": "搜索新闻", "searchMode": "on"}, buffered=True)
        self.assertEqual(self.mode, "on")
        body = response.get_data(as_text=True)
        for event in ["skill_loaded", "search_status", "search_sources", "tool_status", "token", "done"]:
            self.assertIn(f"event: {event}", body)
        self.assertNotIn("fixture-secret", body)
        task = self.client.get("/desktop/api/state").json["tasks"][0]
        self.assertEqual(task["messages"][0]["searchMode"], "on")
        self.assertEqual(task["messages"][-1]["sources"], [SOURCE])
        self.assertEqual(task["messages"][-1]["skillsUsed"], ["news-search"])
        self.assertEqual(task["messages"][-1]["toolsUsed"], ["web_search"])
        self.backend.close()
        self.app = create_app(self.config)
        self.backend = self.app.extensions["workbench_service"]
        self.client = self.app.test_client()
        self.client.post("/desktop/api/login", json={"email": "alice@example.invalid", "password": "test-password"})
        restored = self.client.get(f"/desktop/api/tasks/{task['id']}").json
        self.assertEqual(restored["messages"][-1]["sources"], [SOURCE])
        self.assertEqual(restored["messages"][-1]["toolsUsed"], ["web_search"])
        history = self.backend._history_for_model("unused", restored, {"files": []})
        self.assertIn(SOURCE["url"], history[-1]["content"])
        self.assertNotIn(SOURCE["snippet"], history[-1]["content"])
        bob = self.app.test_client()
        bob.post("/desktop/api/register", json={"email": "bob@example.invalid", "password": "test-password"})
        self.assertEqual(bob.get(f"/desktop/api/tasks/{task['id']}").status_code, 404)

    def test_sources_persist_when_generation_fails_before_any_text(self):
        def failed(*_args, **_kwargs):
            yield {"type": "skill_loaded", "name": "news-search"}
            yield {"type": "search_sources", "sources": [SOURCE]}
            raise GatewayError("模型暂时不可用。", 502)
        with patch.object(self.backend.runtime, "stream", failed):
            response = self.client.post("/desktop/api/chat", json={"message": "搜索"}, buffered=True)
        self.assertIn("event: error", response.get_data(as_text=True))
        task = self.client.get("/desktop/api/state").json["tasks"][0]
        self.assertEqual(task["status"], "error")
        self.assertEqual(task["messages"][-1]["content"], "")
        self.assertEqual(task["messages"][-1]["sources"], [SOURCE])
        self.assertTrue(task["messages"][-1]["partial"])
        self.assertEqual(self.backend.active, {})

    def test_invalid_search_mode_does_not_create_task_or_lock(self):
        for mode in [True, None, "sometimes", [], {}]:
            response = self.client.post("/desktop/api/chat", json={"message": "搜索", "searchMode": mode})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get("/desktop/api/state").json["tasks"], [])
        self.assertEqual(self.backend.active, {})

    def test_runtime_catalog_is_safe_and_search_is_disabled_without_key(self):
        status = self.client.get("/desktop/api/runtime")
        self.assertTrue(status.json["searchAvailable"])
        self.assertIn("news-search", [skill["name"] for skill in status.json["skills"]])
        self.assertNotIn("instructions", status.get_data(as_text=True))
        self.assertNotIn("fixture-secret", status.get_data(as_text=True))
        with patch.object(self.backend.runtime, "tools", ToolRegistry()):
            self.assertFalse(self.client.get("/desktop/api/runtime").json["searchAvailable"])

    def test_search_availability_does_not_require_any_skill(self):
        with tempfile.TemporaryDirectory() as empty:
            with patch.object(self.backend.runtime, "skills", SkillRegistry(empty)):
                status = self.client.get("/desktop/api/runtime").json
        self.assertEqual(status["skills"], [])
        self.assertTrue(status["searchAvailable"])


if __name__ == "__main__":
    unittest.main()
