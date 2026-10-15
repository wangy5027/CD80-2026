"""Real LangGraph + HTTP model-stream tests; never call the configured provider."""

import json
import os
import stat
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from backend.app.modules.workbench.errors import GatewayError
from backend.app.modules.workbench.graph_runtime import GraphRuntime, validate_provider_url


class MockModel(BaseHTTPRequestHandler):
    calls = []
    requests_lock = threading.Lock()
    hold_release = threading.Event()

    def log_message(self, _format, *_args):
        pass

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(size))
        with type(self).requests_lock:
            type(self).calls.append({"path": self.path, "body": request, "authorization": self.headers.get("Authorization")})
        if request["messages"][-1]["content"] == "redirect":
            self.send_response(302)
            self.send_header("Location", "http://example.invalid/steal")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        if request["messages"][-1]["content"] == "hold-open":
            self.wfile.write(b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n')
            self.wfile.flush()
            type(self).hold_release.wait(timeout=5)
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return
        if request["messages"][-1]["content"] == "error":
            self.wfile.write(b'event: error\ndata: {"error":{"message":"secret-key-must-not-appear"}}\n\n')
            self.wfile.flush()
            return
        for text in ["你好", "，世界"]:
            self.wfile.write(("data: " + json.dumps({"choices": [{"delta": {"content": text}}]}, ensure_ascii=False) + "\n\n").encode())
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


class GraphRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), MockModel)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ficagent-graph-test-")
        self.config = {
            "DATA_DIR": self.temp.name,
            "TESTING": True,
            "GITEE_AI_BASE_URL": f"http://127.0.0.1:{self.server.server_port}/v1",
            "GITEE_AI_API_KEY": "test-only-key",
            "GITEE_AI_MODEL": "mock-model",
            "GITEE_AI_CONNECT_TIMEOUT_SECONDS": 1,
            "GITEE_AI_READ_TIMEOUT_SECONDS": 2,
        }
        with MockModel.requests_lock:
            MockModel.calls.clear()
        MockModel.hold_release.clear()

    def tearDown(self):
        self.temp.cleanup()

    def collect(self, runtime, user, task, history, query):
        return list(runtime.stream(user, task, history, query, "system role", "mock-model", threading.Event(), lambda _response: None))

    def test_graph_stream_checkpoint_restart_and_authoritative_history(self):
        first = GraphRuntime(self.config)
        events = self.collect(first, "u1", "t1", [], "first")
        self.assertEqual("".join(item["text"] for item in events if item["type"] == "token"), "你好，世界")
        self.assertEqual(events[-1], {"type": "complete", "message": {"role": "assistant", "content": "你好，世界"}})
        self.assertTrue((Path(self.temp.name) / "graph-checkpoints.sqlite3").is_file())
        checkpoint = first.graph.get_state({"configurable": {"thread_id": first.thread_id("u1", "t1")}})
        self.assertEqual(checkpoint.values["final_message"]["content"], "你好，世界")
        self.assertEqual(checkpoint.next, ())
        first.close()
        first.close()

        restarted = GraphRuntime(self.config)
        changed_history = [{"role": "user", "content": "edited old question"}, {"role": "assistant", "content": "edited old answer"}]
        self.collect(restarted, "u1", "t1", changed_history, "second")
        with MockModel.requests_lock:
            sent = MockModel.calls[-1]
        self.assertEqual(sent["path"], "/v1/chat/completions")
        self.assertEqual(sent["authorization"], "Bearer test-only-key")
        self.assertEqual(sent["body"]["messages"], [
            {"role": "system", "content": "system role"},
            *changed_history,
            {"role": "user", "content": "second"},
        ])
        self.assertNotEqual(first.thread_id("u1", "t1"), first.thread_id("u2", "t1"))
        restarted.forget("u1", "t1")
        self.assertIsNone(restarted.checkpointer.get_tuple({"configurable": {"thread_id": restarted.thread_id("u1", "t1")}}))
        restarted.close()

    def test_reject_redirect_and_upstream_error_without_echoing_secret(self):
        runtime = GraphRuntime(self.config)
        for query in ["redirect", "error"]:
            with self.assertRaises(GatewayError) as caught:
                self.collect(runtime, "u1", query, [], query)
            self.assertNotIn("test-only-key", str(caught.exception))
            self.assertNotIn("secret-key-must-not-appear", str(caught.exception))
        with MockModel.requests_lock:
            self.assertEqual([item["path"] for item in MockModel.calls], ["/v1/chat/completions", "/v1/chat/completions"])
        runtime.close()

    def test_first_short_token_arrives_before_nonchunked_stream_finishes(self):
        runtime = GraphRuntime(self.config)
        arrived = threading.Event()
        tokens = []
        errors = []

        def consume():
            try:
                for item in runtime.stream("u1", "hold", [], "hold-open", "system role", "mock-model", threading.Event(), lambda _response: None):
                    if item["type"] == "token":
                        tokens.append(item["text"])
                        arrived.set()
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=consume, daemon=True)
        thread.start()
        try:
            self.assertTrue(arrived.wait(timeout=2), "Short SSE token was buffered until upstream finished")
            self.assertEqual(tokens, ["first"])
            self.assertFalse(MockModel.hold_release.is_set())
            runtime.close()  # Defers SQLite close until the graph finishes.
            self.assertFalse(runtime._closed)
        finally:
            MockModel.hold_release.set()
            thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(runtime._closed)
        runtime.close()

    def test_provider_host_is_pinned_outside_tests(self):
        self.assertEqual(validate_provider_url("https://ai.gitee.com/v1"), "https://ai.gitee.com/v1")
        for value in ["https://evil.example/v1", "http://ai.gitee.com/v1", "https://ai.gitee.com/v1/else", f"http://127.0.0.1:{self.server.server_port}/v1"]:
            with self.assertRaises(ValueError):
                validate_provider_url(value)
        self.assertEqual(validate_provider_url(self.config["GITEE_AI_BASE_URL"], testing=True), self.config["GITEE_AI_BASE_URL"])

    def test_existing_checkpoint_and_sidecars_are_private_before_writes(self):
        data_dir = Path(self.temp.name)
        database = data_dir / "graph-checkpoints.sqlite3"
        data_dir.chmod(0o755)
        database.write_bytes(b"")
        sidecars = [Path(str(database) + suffix) for suffix in ("-wal", "-shm", "-journal")]
        for item in [database, *sidecars]:
            if item != database:
                item.write_bytes(b"")
            item.chmod(0o644)

        runtime = GraphRuntime(self.config)
        if os.name != "nt":  # Windows chmod does not express POSIX account permissions.
            self.assertEqual(stat.S_IMODE(data_dir.stat().st_mode), 0o700)
            for item in [database, *sidecars]:
                self.assertEqual(stat.S_IMODE(item.stat().st_mode), 0o600, str(item))

        # The placeholders have served their migration check; SQLite creates real WAL files.
        for item in sidecars:
            item.unlink()
        runtime.connection.execute("PRAGMA journal_mode=WAL")
        events = self.collect(runtime, "private-user", "private-task", [], "checkpoint write")
        self.assertEqual(events[-1]["type"], "complete")
        self.assertTrue(database.stat().st_size > 0)
        self.assertTrue(sidecars[0].exists(), "WAL sidecar was not created during checkpoint write")
        self.assertTrue(sidecars[1].exists(), "SHM sidecar was not created during checkpoint write")
        for item in sidecars:
            if item.exists() and os.name != "nt":
                self.assertEqual(stat.S_IMODE(item.stat().st_mode), 0o600, str(item))
        runtime.close()


if __name__ == "__main__":
    unittest.main()
