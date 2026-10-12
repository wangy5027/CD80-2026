"""Workbench chat contract with a local OpenAI-style provider and local accounts."""

import io
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from backend.app import create_app


class ChatProvider(BaseHTTPRequestHandler):
    calls = []
    lock = threading.Lock()
    release_slow = threading.Event()

    def log_message(self, _format, *_args):
        pass

    def frame(self, value):
        if isinstance(value, str):
            value = value.encode("utf-8")
        self.wfile.write(f"{len(value):X}\r\n".encode() + value + b"\r\n")
        self.wfile.flush()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        with type(self).lock:
            type(self).calls.append(body)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        query = body["messages"][-1]["content"]
        try:
            token = "first" if query == "slow" else "mock answer"
            self.frame("data: " + json.dumps({"choices": [{"delta": {"content": token}}]}) + "\n\n")
            if query == "slow":
                type(self).release_slow.wait(timeout=8)
            if query == "provider-error":
                self.frame('event: error\ndata: {"error":{"message":"test-only-key leaked"}}\n\n')
            else:
                self.frame("data: [DONE]\n\n")
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


class ChatContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.provider = ThreadingHTTPServer(("127.0.0.1", 0), ChatProvider)
        cls.thread = threading.Thread(target=cls.provider.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.provider.shutdown()
        cls.provider.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ficagent-chat-test-")
        self.config = {
            "TESTING": True,
            "DATA_DIR": self.temp.name,
            "GITEE_AI_BASE_URL": f"http://127.0.0.1:{self.provider.server_port}/v1",
            "GITEE_AI_MODEL": "mock-model",
            "GITEE_AI_API_KEY": "test-only-key",
            "DESKTOP_PORT": 8893,
        }
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        registered = self.client.post("/desktop/api/register", json={"email": "alice@example.invalid", "password": "test-only", "name": "Alice"})
        self.assertEqual(registered.status_code, 200, registered.json)
        self.user_id = registered.json["user"]["id"]
        with ChatProvider.lock:
            ChatProvider.calls.clear()
        ChatProvider.release_slow.clear()

    def tearDown(self):
        ChatProvider.release_slow.set()
        self.app.extensions["workbench_service"].close()
        self.temp.cleanup()

    def task(self, task_id):
        response = self.client.get(f"/desktop/api/tasks/{task_id}")
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def test_chat_replays_local_history_after_restart_and_accepts_legacy_task(self):
        response = self.client.post("/desktop/api/chat", json={"message": "first", "agentId": "assistant"}, buffered=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("event: token", response.data.decode())
        self.assertIn("event: done", response.data.decode())
        first_task = self.client.get("/desktop/api/state").json["tasks"][0]
        self.assertEqual(first_task["status"], "done")
        self.assertEqual([item["role"] for item in first_task["messages"]], ["user", "assistant"])

        self.app.extensions["workbench_service"].close()
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        logged_in = self.client.post("/desktop/api/login", json={"email": "alice@example.invalid", "password": "test-only"})
        self.assertEqual(logged_in.status_code, 200)
        resumed = self.client.post("/desktop/api/chat", json={"taskId": first_task["id"], "message": "second", "modelId": "mock-model"}, buffered=True)
        self.assertEqual(resumed.status_code, 200)
        self.assertIn("event: done", resumed.data.decode())
        with ChatProvider.lock:
            replay = ChatProvider.calls[-1]["messages"]
        self.assertEqual([item["role"] for item in replay], ["system", "user", "assistant", "user"])
        self.assertEqual([item["content"] for item in replay[1:]], ["first", "mock answer", "second"])

        legacy = {"id": "old-task", "title": "旧任务", "agentId": "old-provider-role", "modelId": "old-model", "projectId": "", "messages": [{"role": "user", "content": "old question"}, {"role": "assistant", "content": "old answer"}], "status": "done", "botId": "old-bot", "conversationId": "old-conversation"}
        self.app.extensions["workbench_service"].store.update(self.user_id, lambda state: state["tasks"].append(legacy))
        continuation = self.client.post("/desktop/api/chat", json={"taskId": "old-task", "message": "continue", "modelId": "mock-model"}, buffered=True)
        self.assertEqual(continuation.status_code, 200)
        self.assertIn("event: done", continuation.data.decode())
        task = self.task("old-task")
        self.assertEqual((task["agentId"], task["modelId"]), ("assistant", "mock-model"))
        self.assertNotIn("botId", task)
        with ChatProvider.lock:
            old_replay = ChatProvider.calls[-1]["messages"]
        self.assertEqual([item["content"] for item in old_replay[1:]], ["old question", "old answer", "continue"])

    def test_attachment_and_account_isolation(self):
        uploaded = self.client.post("/desktop/api/files", data={"file": (io.BytesIO("附件正文".encode()), "memo.txt")}, content_type="multipart/form-data")
        self.assertEqual(uploaded.status_code, 201)
        file_id = uploaded.json["id"]
        chat = self.client.post("/desktop/api/chat", json={"message": "review", "fileIds": [file_id]}, buffered=True)
        self.assertEqual(chat.status_code, 200)
        with ChatProvider.lock:
            query = ChatProvider.calls[-1]["messages"][-1]["content"]
        self.assertIn("附件正文", query)
        task = self.client.get("/desktop/api/state").json["tasks"][0]
        self.assertEqual(task["messages"][0]["attachments"][0]["id"], file_id)
        bob = self.app.test_client()
        self.assertEqual(bob.post("/desktop/api/register", json={"email": "bob@example.invalid", "password": "test-only"}).status_code, 200)
        self.assertEqual(bob.get("/desktop/api/state").json["tasks"], [])
        self.assertEqual(bob.get(f"/desktop/api/tasks/{task['id']}").status_code, 404)
        self.assertEqual(bob.post("/desktop/api/chat", json={"message": "steal", "fileIds": [file_id]}).status_code, 404)
        pdf = self.client.post("/desktop/api/files", data={"file": (io.BytesIO(b"%PDF fake"), "memo.pdf")}, content_type="multipart/form-data")
        self.assertEqual(pdf.status_code, 201)
        self.assertEqual(self.client.post("/desktop/api/chat", json={"message": "read pdf", "fileIds": [pdf.json["id"]]}).status_code, 400)

    def test_provider_error_is_sanitized_and_partial_reply_is_saved(self):
        response = self.client.post("/desktop/api/chat", json={"message": "provider-error"}, buffered=True)
        body = response.data.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn("event: token", body)
        self.assertIn("event: error", body)
        self.assertIn("event: done", body)
        self.assertNotIn("test-only-key", body)
        task = self.client.get("/desktop/api/state").json["tasks"][0]
        self.assertEqual(task["status"], "error")
        self.assertEqual(task["messages"][-1]["content"], "mock answer")
        self.assertTrue(task["messages"][-1]["partial"])

    def test_stop_releases_lock_and_saves_partial(self):
        pending = self.client.post("/desktop/api/chat", json={"message": "slow"}, buffered=False)
        meta = next(pending.response).decode()
        task_id = json.loads(meta.split("data: ", 1)[1])["taskId"]
        token = next(pending.response).decode()
        self.assertIn("first", token)
        started = time.monotonic()
        stopped = self.client.post("/desktop/api/stop", json={"taskId": task_id})
        stop_seconds = time.monotonic() - started
        self.assertEqual(stopped.status_code, 200)
        self.assertLess(stop_seconds, 1.0, f"/stop blocked for {stop_seconds:.2f}s")
        started = time.monotonic()
        rest = b"".join(pending.response).decode()
        finish_seconds = time.monotonic() - started
        pending.close()
        self.assertLess(finish_seconds, 1.5, f"Cancelled stream held its lock for {finish_seconds:.2f}s")
        self.assertFalse(ChatProvider.release_slow.is_set(), "Upstream was artificially released")
        self.assertIn("已停止生成", rest)
        self.assertEqual(self.task(task_id)["status"], "stopped")
        self.assertEqual(self.app.extensions["workbench_service"].active, {})
        continued = self.client.post("/desktop/api/chat", json={"taskId": task_id, "message": "after stop"}, buffered=True)
        self.assertEqual(continued.status_code, 200)
        self.assertIn("event: done", continued.data.decode())
        self.assertEqual(self.task(task_id)["status"], "done")

    def test_same_legacy_task_id_in_two_accounts_can_run_independently(self):
        backend = self.app.extensions["workbench_service"]
        bob = self.app.test_client()
        registered = bob.post("/desktop/api/register", json={"email": "bob@example.invalid", "password": "test-only"})
        bob_id = registered.json["user"]["id"]
        old_task = {"id": "shared-old-id", "title": "旧任务", "agentId": "assistant", "modelId": "mock-model", "projectId": "", "messages": [], "status": "idle"}
        backend.store.update(self.user_id, lambda state: state["tasks"].append(old_task.copy()))
        backend.store.update(bob_id, lambda state: state["tasks"].append(old_task.copy()))
        alice_stream = self.client.post("/desktop/api/chat", json={"taskId": "shared-old-id", "message": "slow"}, buffered=False)
        next(alice_stream.response)  # meta
        self.assertIn("first", next(alice_stream.response).decode())
        try:
            bob_chat = bob.post("/desktop/api/chat", json={"taskId": "shared-old-id", "message": "bob's independent turn"}, buffered=True)
            self.assertEqual(bob_chat.status_code, 200)
            self.assertIn("event: done", bob_chat.data.decode())
            self.assertEqual(bob.get("/desktop/api/tasks/shared-old-id").json["status"], "done")
        finally:
            timer = threading.Timer(0.1, ChatProvider.release_slow.set)
            timer.start()
            self.client.post("/desktop/api/stop", json={"taskId": "shared-old-id"})
            b"".join(alice_stream.response)
            alice_stream.close()
            timer.join(timeout=2)
        self.assertEqual(self.task("shared-old-id")["status"], "stopped")
        self.assertEqual(backend.active, {})

    def test_shutdown_aborts_stalled_stream_without_waiting_for_upstream(self):
        pending = self.client.post("/desktop/api/chat", json={"message": "slow"}, buffered=False)
        meta = next(pending.response).decode()
        task_id = json.loads(meta.split("data: ", 1)[1])["taskId"]
        self.assertIn("first", next(pending.response).decode())
        old_backend = self.app.extensions["workbench_service"]
        started = time.monotonic()
        old_backend.close()
        close_seconds = time.monotonic() - started
        self.assertLess(close_seconds, 1.0, f"Shutdown blocked for {close_seconds:.2f}s")
        started = time.monotonic()
        rest = b"".join(pending.response).decode()
        pending.close()
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertFalse(ChatProvider.release_slow.is_set())
        self.assertIn("已停止生成", rest)
        self.assertEqual(old_backend.active, {})
        self.assertTrue(old_backend.runtime._closed)

        self.app = create_app(self.config)
        self.client = self.app.test_client()
        self.assertEqual(self.client.post("/desktop/api/login", json={"email": "alice@example.invalid", "password": "test-only"}).status_code, 200)
        self.assertEqual(self.task(task_id)["status"], "stopped")
        continued = self.client.post("/desktop/api/chat", json={"taskId": task_id, "message": "after shutdown"}, buffered=True)
        self.assertEqual(continued.status_code, 200)
        self.assertIn("event: done", continued.data.decode())


if __name__ == "__main__":
    unittest.main()
