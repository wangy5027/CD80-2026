"""Flask route contracts against a real local HTTP mock of Coze."""

import io
import json
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests
from werkzeug.serving import make_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app import create_app  # noqa: E402


class FakeCoze(BaseHTTPRequestHandler):
    bots = {}
    next_bot = 1000

    def log_message(self, _format, *_args):
        pass

    def answer(self, value, status=200, headers=None):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, item in (headers or {}).items():
            self.send_header(key, item)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/passport/web/logout/":
            return self.answer({"code": 0})
        if self.path == "/upstream-test":
            self.send_response(201)
            self.send_header("Connection", "X-Hop")
            self.send_header("X-Hop", "should-not-forward")
            self.send_header("Set-Cookie", "session_key=remote; Domain=127.0.0.1; Path=/; HttpOnly")
            self.end_headers()
            self.wfile.write(b"upstream ok")
            return
        self.answer({"code": 404, "msg": self.path}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        data = json.loads(self.rfile.read(length) or b"{}")
        cookie = self.headers.get("Cookie", "")
        user = "bob" if "session_key=bob" in cookie else "alice"
        if self.path in {"/api/passport/web/email/login/", "/api/passport/web/email/register/v2/"}:
            user = "bob" if data["email"].startswith("bob") else "alice"
            return self.answer({"code": 0, "data": {"user_id_str": "2" if user == "bob" else "1"}}, headers={"Set-Cookie": f"session_key={user}; Domain=127.0.0.1; Path=/; HttpOnly"})
        if self.path == "/api/passport/account/info/v2/":
            return self.answer({"code": 0, "data": {"user_id_str": "2" if user == "bob" else "1", "name": user, "email": f"{user}@example.invalid"}})
        if self.path == "/api/playground_api/space/list":
            return self.answer({"code": 0, "data": {"bot_space_list": [{"id": "123", "space_type": 1}]}})
        if self.path == "/api/bot/get_type_list":
            return self.answer({"code": 0, "data": {"model_list": [{"model_type": 100001, "name": "测试模型"}]}})
        if self.path == "/api/intelligence_api/search/get_draft_intelligence_list":
            return self.answer({"code": 0, "data": {"intelligences": []}})
        if self.path == "/api/developer/get_icon":
            return self.answer({"code": 0, "data": {"icon_list": [{"uri": "test/icon.png"}]}})
        if self.path == "/api/draftbot/create":
            if not data.get("icon_uri"):
                return self.answer({"code": 400, "msg": "missing icon"})
            type(self).next_bot += 1
            bot_id = str(type(self).next_bot)
            type(self).bots[bot_id] = {"bot_id": bot_id, "model_info": {"model_id": "100001", "temperature": 0.7}}
            return self.answer({"code": 0, "data": {"bot_id": bot_id}})
        if self.path == "/api/playground_api/draftbot/get_draft_bot_info":
            return self.answer({"code": 0, "data": {"bot_info": type(self).bots[data["bot_id"]]}})
        if self.path == "/api/playground_api/draftbot/update_draft_bot_info":
            return self.answer({"code": 0})
        if self.path == "/api/draftbot/delete":
            type(self).bots.pop(data["bot_id"], None)
            return self.answer({"code": 0})
        if self.path == "/api/conversation/chat":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            if data.get("query") == "timeout test":
                self.wfile.write(b'event: message\ndata: {"message":{"role":"assistant","type":"answer","content":"partial"}}\n\n')
                self.wfile.flush()
                time.sleep(0.3)
                return
            if data.get("query") == "truncated test":
                self.wfile.write(b'event: message\ndata: {"message":{"role":"assistant","type":"answer","content":"partial"}}\n\n')
                self.wfile.flush()
                return
            self.wfile.write(b'event: message\ndata: {"conversation_id":"222","message":{"role":"assistant","type":"answer","content":"test answer"}}\n\n')
            self.wfile.write(b"event: done\n\n")
            self.wfile.flush()
            return
        self.answer({"code": 404, "msg": self.path}, 404)


class RouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mock = ThreadingHTTPServer(("127.0.0.1", 0), FakeCoze)
        cls.thread = threading.Thread(target=cls.mock.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.mock.shutdown()
        cls.mock.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ficagent-flask-test-")
        root = Path(self.temp.name)
        dist = root / "dist"
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text("<!doctype html><title>test workbench</title>")
        (dist / "assets" / "app.js").write_text("window.testWorkbench = true")
        self.app = create_app({"TESTING": True, "DATA_DIR": str(root / "data"), "FRONTEND_DIST": str(dist), "COZE_BASE_URL": f"http://127.0.0.1:{self.mock.server_port}", "DESKTOP_PORT": 8890})
        self.client = self.app.test_client()
        self.client.set_cookie("session_key", "alice")

    def tearDown(self):
        self.app.extensions["workbench_service"].close()
        self.temp.cleanup()

    def test_login_session_origin_and_actual_http_health(self):
        login = self.client.post("/desktop/api/login", json={"email": "alice@example.invalid", "password": "test-only"})
        self.assertEqual(login.status_code, 200)
        self.assertIn("HttpOnly", login.headers.get("Set-Cookie"))
        self.assertNotIn("Domain=", login.headers.get("Set-Cookie"))
        session = self.client.get("/desktop/api/session").json
        self.assertTrue(session["authenticated"])
        self.assertEqual(session["models"][0]["id"], "100001")
        self.assertEqual(session["agents"][0]["id"], "assistant")
        self.assertEqual(self.client.post("/desktop/api/projects", json={"name": "blocked"}, headers={"Origin": "https://invalid.example"}).status_code, 403)
        self.assertEqual(self.client.get("/desktop/health", headers={"Host": "evil.example"}).status_code, 403)

        server = make_server("127.0.0.1", 0, self.app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            response = requests.get(f"http://127.0.0.1:{server.server_port}/desktop/health", timeout=5)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["backend"], "flask")
        finally:
            server.shutdown()
            thread.join(timeout=2)

    def test_account_isolation_crud_file_and_static_page(self):
        project = self.client.post("/desktop/api/projects", json={"name": "验收项目"}).json
        self.assertEqual(self.client.get("/desktop/api/state").json["projects"][0]["id"], project["id"])
        schedule = self.client.post("/desktop/api/schedules", json={"title": "测试日程", "at": "2026-10-10T18:00:00+08:00"}).json
        self.assertEqual(self.client.patch(f"/desktop/api/schedules/{schedule['id']}", json={"done": True}).status_code, 200)
        self.assertTrue(self.client.get("/desktop/api/state").json["schedules"][0]["done"])
        uploaded = self.client.post("/desktop/api/files", data={"file": (io.BytesIO("合同测试正文".encode()), "测试.txt"), "projectId": project["id"]}, content_type="multipart/form-data")
        self.assertEqual(uploaded.status_code, 201)
        file_id = uploaded.json["id"]
        downloaded = self.client.get(f"/desktop/api/files/{file_id}")
        self.assertEqual(downloaded.data.decode(), "合同测试正文")
        downloaded.close()
        bob = self.app.test_client()
        bob.set_cookie("session_key", "bob")
        self.assertEqual(bob.get("/desktop/api/state").json["projects"], [])
        self.assertEqual(bob.get(f"/desktop/api/files/{file_id}").status_code, 404)
        page = self.client.get("/desktop/")
        self.assertEqual(page.status_code, 200)
        page.close()
        asset = self.client.get("/desktop/assets/app.js")
        self.assertIn(b"testWorkbench", asset.data)
        asset.close()
        self.assertEqual(self.client.delete(f"/desktop/api/files/{file_id}").status_code, 200)
        self.assertEqual(self.client.delete(f"/desktop/api/schedules/{schedule['id']}").status_code, 200)
        self.assertEqual(self.client.delete(f"/desktop/api/projects/{project['id']}").status_code, 200)

    def test_chat_stream_persistence_and_stop(self):
        response = self.client.post("/desktop/api/chat", json={"message": "hello", "agentId": "assistant"}, buffered=True)
        self.assertEqual(response.status_code, 200)
        body = response.data.decode()
        self.assertIn("event: meta", body)
        self.assertIn("event: token", body)
        self.assertIn("test answer", body)
        self.assertIn("event: done", body)
        task = self.client.get("/desktop/api/state").json["tasks"][0]
        self.assertEqual(task["status"], "done")
        self.assertEqual([item["role"] for item in task["messages"]], ["user", "assistant"])
        self.assertNotIn("botId", task)
        self.assertEqual(self.client.delete(f"/desktop/api/tasks/{task['id']}").status_code, 200)

        pending = self.client.post("/desktop/api/chat", json={"message": "stop me"}, buffered=False)
        first = next(pending.response).decode()
        task_id = json.loads(first.split("data: ", 1)[1])["taskId"]
        bob = self.app.test_client()
        bob.set_cookie("session_key", "bob")
        self.assertEqual(bob.delete(f"/desktop/api/tasks/{task_id}").status_code, 404)
        self.assertEqual(self.client.post("/desktop/api/stop", json={"taskId": task_id}).status_code, 200)
        rest = b"".join(pending.response).decode()
        pending.close()
        self.assertIn("已停止生成", rest)
        self.assertEqual(self.client.get(f"/desktop/api/tasks/{task_id}").json["status"], "stopped")

    def test_proxy_cookie_and_logout_failure_independent_of_account(self):
        response = self.client.get("/upstream-test")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data, b"upstream ok")
        self.assertNotIn("Domain=", response.headers.get("Set-Cookie"))
        self.assertIn("SameSite=Lax", response.headers.get("Set-Cookie"))
        self.assertNotIn("X-Hop", response.headers)
        logout = self.client.post("/desktop/api/logout")
        self.assertTrue(logout.json["ok"])
        self.assertIn("Max-Age=0", logout.headers.get("Set-Cookie"))

    def test_website_root_and_coze_destinations(self):
        home = self.client.get("/", headers={"Host": "localhost:8890"})
        self.assertEqual((home.status_code, home.headers["Location"]), (302, "/desktop/"))
        page = self.client.get(home.headers["Location"], headers={"Host": "localhost:8890"})
        self.assertIn(b"test workbench", page.data)
        page.close()

        studio = self.client.get("/desktop/coze/studio", headers={"Host": "localhost:8890"})
        models = self.client.get("/desktop/coze/models", headers={"Host": "127.0.0.1:8890"})
        coze_port = self.mock.server_port
        self.assertEqual(studio.headers["Location"], f"http://localhost:{coze_port}/")
        self.assertEqual(models.headers["Location"], f"http://127.0.0.1:{coze_port}/admin/#model-management")
        self.assertNotEqual(studio.headers["Location"], "/")

    def test_closing_unstarted_chat_releases_reservation(self):
        with self.app.test_request_context("/desktop/api/chat", method="POST", json={"message": "never read"}, headers={"Cookie": "session_key=alice"}):
            response = self.app.full_dispatch_request()
            self.assertEqual(response.status_code, 200)
            response.close()
        backend = self.app.extensions["workbench_service"]
        self.assertEqual(backend.active, {})
        self.assertEqual(backend.store.get("1")["tasks"], [])

    def test_oversized_json_is_rejected_before_parsing(self):
        oversized = b'{"message":"' + b'x' * (2 * 1024 * 1024) + b'"}'
        response = self.client.post("/desktop/api/login", data=oversized, content_type="application/json")
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json, {"error": "文件过大，单次上传上限为 30 MB。"})
        normal = self.client.post("/desktop/api/login", json={"email": "alice@example.invalid", "password": "test-only"})
        self.assertEqual(normal.status_code, 200)

    def test_partial_chat_is_saved_on_timeout_and_early_eof(self):
        backend = self.app.extensions["workbench_service"]
        backend.chat_idle_timeout = 0.1
        for prompt, expected in [("timeout test", "模型长时间没有返回新内容"), ("truncated test", "模型连接提前结束")]:
            response = self.client.post("/desktop/api/chat", json={"message": prompt}, buffered=True)
            self.assertEqual(response.status_code, 200)
            text = response.data.decode()
            self.assertIn("event: token", text)
            self.assertIn("event: error", text)
            self.assertIn(expected, text)
            task = self.client.get("/desktop/api/state").json["tasks"][0]
            self.assertEqual(task["status"], "error")
            self.assertEqual(task["messages"][-1]["content"], "partial")
            self.assertTrue(task["messages"][-1]["partial"])


if __name__ == "__main__":
    unittest.main()
