"""Focused contract tests for the migrated Coze client and state store."""

from __future__ import annotations

import concurrent.futures
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests
from urllib3._collections import HTTPHeaderDict

from backend.app.modules.workbench.coze import CozeClient, GatewayError, parse_coze_json, parse_sse
from backend.app.modules.workbench.store import StateStore


def response(status: int, value: bytes, cookies: list[str] | None = None) -> requests.Response:
    """Build an in-memory HTTP response without making a network request."""
    result = requests.Response()
    result.status_code = status
    result._content = value
    headers = HTTPHeaderDict()
    for cookie in cookies or []:
        headers.add("Set-Cookie", cookie)
    result.raw = type("Raw", (), {"headers": headers, "close": lambda self: None})()
    return result


class CozeClientTests(unittest.TestCase):
    def test_large_ids_and_separate_cookies(self):
        """Long model IDs survive JSON decoding and login cookies stay separate."""
        large_id = 9223372036854775807
        payload = json.dumps({"code": 0, "data": {"model_id": large_id}}).encode()
        upstream = response(200, payload, ["session_key=abc; Path=/", "other=x; Expires=Wed, 21 Oct 2026 07:28:00 GMT"])
        with patch("backend.app.modules.workbench.coze.requests.request", return_value=upstream) as request:
            result = CozeClient("http://127.0.0.1:8888/").request("/api/test", {"x": 1}, cookie="inbound=y")
        self.assertEqual(result["result"]["data"]["model_id"], large_id)
        self.assertEqual(result["cookies"], ["session_key=abc; Path=/", "other=x; Expires=Wed, 21 Oct 2026 07:28:00 GMT"])
        self.assertEqual(request.call_args.args, ("POST", "http://127.0.0.1:8888/api/test"))
        self.assertEqual(request.call_args.kwargs["headers"]["Cookie"], "inbound=y")
        self.assertFalse(request.call_args.kwargs["allow_redirects"])

    def test_stream_and_error_status(self):
        """A successful stream stays open; upstream error codes become GatewayError."""
        stream = response(200, b"event: done\n\n")
        with patch("backend.app.modules.workbench.coze.requests.request", return_value=stream):
            self.assertIs(CozeClient().request("/api/conversation/chat", raw=True), stream)
        rejected = response(200, b'{"code":4001,"msg":"model unavailable"}')
        with patch("backend.app.modules.workbench.coze.requests.request", return_value=rejected):
            with self.assertRaises(GatewayError) as caught:
                CozeClient().request("/api/test")
        self.assertEqual((caught.exception.status, caught.exception.code), (400, 4001))
        unauthorized = response(401, b'{"message":"login required"}')
        with patch("backend.app.modules.workbench.coze.requests.request", return_value=unauthorized):
            with self.assertRaises(GatewayError) as caught:
                CozeClient().request("/api/test")
        self.assertEqual(caught.exception.status, 401)

    def test_user_and_catalog_keep_long_ids(self):
        """User, model, and agent identifiers use exact decimal strings."""
        large_id = 9223372036854775807
        client = CozeClient()
        answers = {
            "/api/passport/account/info/v2/": {"data": {"user_id": large_id, "email": "a@example.com"}},
            "/api/playground_api/space/list": {"data": {"bot_space_list": [{"id": large_id, "space_type": 1}]}},
            "/api/bot/get_type_list": {"data": {"model_list": [{"model_type": large_id, "name": "Model"}]}},
            "/api/intelligence_api/search/get_draft_intelligence_list": {"data": {"intelligences": [{"basic_info": {"id": large_id, "name": "Agent"}}]}},
        }
        client.json = lambda route, body=None, cookie="": answers[route]
        self.assertIsNone(client.user(""))
        self.assertEqual(client.user("session_key=x")["id"], str(large_id))
        catalog = client.catalog("session_key=x")
        self.assertEqual(catalog["activeSpaceId"], str(large_id))
        self.assertEqual(catalog["models"][0]["id"], str(large_id))
        self.assertEqual(catalog["agents"][0]["id"], str(large_id))

    def test_sse_utf8_crlf_and_final_event(self):
        """A split UTF-8 character or CRLF delimiter does not corrupt SSE data."""
        source = "event: token\r\ndata: 金融\r\ndata: 完成\r\n\r\nevent: done\ndata: {}"
        chunks = (source.encode("utf-8")[i:i + 1] for i in range(len(source.encode("utf-8"))))
        self.assertEqual(
            list(parse_sse(chunks)),
            [{"event": "token", "data": "金融\n完成"}, {"event": "done", "data": "{}"}],
        )
        self.assertEqual(parse_coze_json('{"id":9223372036854775807}')["id"], 9223372036854775807)


class StateStoreTests(unittest.TestCase):
    def test_legacy_file_recovery_and_copy_isolation(self):
        """Existing JSON loads at its old path and a stale run becomes stopped."""
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "users" / "123"
            directory.mkdir(parents=True)
            original = {"projects": [], "tasks": [{"id": "run", "status": "running", "messages": []}], "schedules": [], "files": []}
            (directory / "state.json").write_text(json.dumps(original), encoding="utf-8")
            store = StateStore(root)
            state = store.get("123")
            self.assertEqual(state["tasks"][0]["status"], "stopped")
            self.assertEqual(json.loads((directory / "state.json").read_text())["tasks"][0]["status"], "stopped")
            self.assertEqual(state["bindings"], {})
            self.assertTrue((directory / "files").is_dir())
            state["tasks"].clear()
            self.assertEqual(len(store.get("123")["tasks"]), 1)
            self.assertEqual(store.get("other")["tasks"], [])

    def test_concurrent_updates_and_failed_write(self):
        """Concurrent callbacks retain every update and a failed rename keeps prior data."""
        with tempfile.TemporaryDirectory() as root:
            store = StateStore(root)

            def add_project(index):
                return store.update("123", lambda state: state["projects"].append(index))

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(add_project, range(40)))
            self.assertEqual(sorted(store.get("123")["projects"]), list(range(40)))
            with patch("backend.app.modules.workbench.store.os.replace", side_effect=OSError("disk failure")):
                with self.assertRaises(OSError):
                    add_project(41)
            self.assertEqual(len(store.get("123")["projects"]), 40)
            self.assertEqual(len(list(store.directory("123").glob("*.tmp"))), 0)
            reloaded = StateStore(root)
            self.assertEqual(len(reloaded.get("123")["projects"]), 40)


if __name__ == "__main__":
    unittest.main()
