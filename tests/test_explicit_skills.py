"""Explicit Skill selection is loaded by the server before chat streaming."""

import json
import tempfile
import unittest
from pathlib import Path

from backend.app import create_app
from backend.app.modules.workbench.errors import GatewayError


class ExplicitSkillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.skills_dir = root / "skills"
        for name, body in (("news-search", "仅引用查到的真实新闻。"),
                           ("finance", "核对财务数据的日期。")):
            folder = self.skills_dir / name
            folder.mkdir(parents=True)
            (folder / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: 测试 {name}。\n---\n{body}\n", encoding="utf-8"
            )
        self.app = create_app({"TESTING": True, "DATA_DIR": str(root / "data"),
                               "SKILLS_DIR": str(self.skills_dir), "GITEE_AI_API_KEY": "fixture-only",
                               "IMPORT_LEGACY_HISTORY": False})
        self.backend = self.app.extensions["workbench_service"]
        self.client = self.app.test_client()
        response = self.client.post("/desktop/api/register", json={
            "email": "skills@example.invalid", "password": "test-password"})
        self.assertEqual(response.status_code, 200)
        self.provider_requests = []

        def provider_events(messages, _model, _stopped, _on_response, *, tools, tool_choice):
            self.provider_requests.append(json.loads(json.dumps(messages, ensure_ascii=False)))
            query = next(item["content"] for item in reversed(messages) if item["role"] == "user")
            if query == "重复加载" and not any(item["role"] == "tool" for item in messages):
                yield {"type": "turn_end", "tool_calls": [{"id": "same-skill", "type": "function",
                        "function": {"name": "load_skill", "arguments": '{"name":"news-search"}'}}]}
            elif query == "失败" and not any(item["role"] == "tool" for item in messages):
                raise GatewayError("模型暂不可用。", 502)
            else:
                yield {"type": "token", "text": "已回答。"}
                yield {"type": "turn_end", "tool_calls": []}

        self.backend.runtime.provider.events = provider_events

    def tearDown(self):
        self.backend.close()
        self.temp.cleanup()

    def test_selected_skill_content_reaches_model_and_does_not_leak_next_turn(self):
        first = self.client.post("/desktop/api/chat", json={
            "message": "第一次", "skillNames": ["news-search", "finance"]}, buffered=True)
        self.assertEqual(first.status_code, 200)
        body = first.get_data(as_text=True)
        self.assertEqual(body.count("event: skill_loaded"), 2)
        self.assertIn("仅引用查到的真实新闻。", self.provider_requests[0][0]["content"])
        self.assertIn("核对财务数据的日期。", self.provider_requests[0][0]["content"])
        task = self.client.get("/desktop/api/state").json["tasks"][0]
        self.assertEqual(task["messages"][0]["skillNames"], ["news-search", "finance"])
        self.assertEqual(task["messages"][1]["skillsUsed"], ["news-search", "finance"])
        self.assertNotIn("仅引用查到的真实新闻。", body)

        second = self.client.post("/desktop/api/chat", json={
            "taskId": task["id"], "message": "第二次"}, buffered=True)
        self.assertEqual(second.status_code, 200)
        self.assertNotIn("仅引用查到的真实新闻。", self.provider_requests[-1][0]["content"])
        task = self.client.get(f"/desktop/api/tasks/{task['id']}").json
        self.assertEqual(task["messages"][2]["skillNames"], [])
        self.assertEqual(task["messages"][3]["skillsUsed"], [])

    def test_duplicate_selection_and_model_load_are_counted_once(self):
        response = self.client.post("/desktop/api/chat", json={
            "message": "重复加载", "skillNames": ["news-search", "news-search"]}, buffered=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(as_text=True).count("event: skill_loaded"), 1)
        task = self.client.get("/desktop/api/state").json["tasks"][0]
        self.assertEqual(task["messages"][0]["skillNames"], ["news-search"])
        self.assertEqual(task["messages"][1]["skillsUsed"], ["news-search"])
        self.assertEqual(len(self.provider_requests), 2)
        self.assertEqual(self.provider_requests[1][-1]["role"], "tool")
        self.assertTrue(json.loads(self.provider_requests[1][-1]["content"])["alreadyLoaded"])
        self.assertEqual(self.provider_requests[1][0]["content"].count("本轮用户选用的 Skill news-search"), 1)

    def test_bad_skills_fail_before_sse_and_do_not_create_task(self):
        for value in ("news-search", None, {}, [4], ["missing"], ["../other"],
                      ["news-search", "finance", "news-search", "finance"]):
            with self.subTest(value=value):
                response = self.client.post("/desktop/api/chat", json={
                    "message": "问题", "skillNames": value})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.client.get("/desktop/api/state").json["tasks"], [])
                self.assertEqual(self.backend.active, {})
        self.assertEqual(self.provider_requests, [])

    def test_selected_skill_snapshot_survives_edit_after_request_validation(self):
        response = self.client.post("/desktop/api/chat", json={
            "message": "快照", "skillNames": ["news-search"]}, buffered=False)
        self.assertEqual(response.status_code, 200)
        (self.skills_dir / "news-search" / "SKILL.md").write_text(
            "---\nname: news-search\ndescription: 已修改。\n---\n第二版正文。\n", encoding="utf-8")
        response.get_data(as_text=True)
        self.assertIn("仅引用查到的真实新闻。", self.provider_requests[0][0]["content"])
        self.assertNotIn("第二版正文。", self.provider_requests[0][0]["content"])

    def test_selected_skill_survives_stream_failure_with_usage_record(self):
        response = self.client.post("/desktop/api/chat", json={
            "message": "失败", "skillNames": ["news-search"]}, buffered=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("event: error", response.get_data(as_text=True))
        task = self.client.get("/desktop/api/state").json["tasks"][0]
        self.assertEqual(task["status"], "error")
        self.assertEqual(task["messages"][0]["skillNames"], ["news-search"])
        self.assertEqual(task["messages"][1]["skillsUsed"], ["news-search"])
        self.assertTrue(task["messages"][1]["partial"])


if __name__ == "__main__":
    unittest.main()
