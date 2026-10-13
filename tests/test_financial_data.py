"""SQLite bridge tests: disposable local accounts and a mocked local upstream."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from backend.app import create_app


ROOT = "/desktop/api/financial-data"
ROWS = ROOT + "/tables/cd80_daily/rows"


def upstream_response(status=200, body=None, content_type="application/json"):
    response = requests.Response()
    response.status_code = status
    response.headers["Content-Type"] = content_type
    response._content = json.dumps(body if body is not None else {"data": []}).encode("utf-8")
    return response


class FinancialDataBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cd80-financial-bridge-")
        self.app = create_app({
            "TESTING": True,
            "DATA_DIR": str(Path(self.temp.name) / "accounts"),
            "AGENT_RUNTIME_DIR": str(Path(self.temp.name) / "runtime"),
            "GITEE_AI_API_KEY": "",
            "IMPORT_LEGACY_HISTORY": False,
            "CD80_DATA_API_URL": "http://127.0.0.1:8897",
            "CD80_ADMIN_API_KEY": "test-admin-secret",
            "CD80_READ_API_KEY": "test-read-secret",
            "CD80_DATA_ADMIN_USERS": "",
        })
        self.client = self.app.test_client()
        self.calls = []
        self.reply = upstream_response(body={"data": [{"_row_id": 1, "code": "000001"}]})
        self.failure = None
        self.patcher = patch.object(
            requests.Session, "request", autospec=True, side_effect=self.fake_request
        )
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.app.extensions["workbench_service"].close()
        self.temp.cleanup()

    def fake_request(self, session, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, "kwargs": kwargs,
                           "trust_env": session.trust_env})
        if self.failure:
            raise self.failure
        return self.reply

    def login(self, name="Test User"):
        registered = self.client.post(
            "/desktop/api/register",
            json={"email": "finance@example.invalid", "password": "test-only-123", "name": name},
        )
        self.assertEqual(registered.status_code, 200, registered.json)
        return registered.json["user"]

    def test_login_required_for_capabilities_and_every_read_kind(self):
        for method, path, kwargs in [
            ("get", ROOT + "/capabilities", {}),
            ("get", ROOT + "/tables", {}),
            ("get", ROOT + "/tables/cd80_daily/schema", {}),
            ("get", ROWS, {}),
            ("get", ROWS + "/1", {}),
            ("post", ROOT + "/tables/cd80_daily/query", {"json": {"limit": 1}}),
        ]:
            result = getattr(self.client, method)(path, **kwargs)
            self.assertEqual(result.status_code, 401, (method, path, result.json))
            self.assertIn("error", result.json)
        self.assertEqual(self.calls, [])

    def test_read_proxy_preserves_shape_and_sends_only_server_read_key(self):
        self.login()
        self.assertEqual(self.client.get(ROOT + "/capabilities").json,
                         {"canRead": True, "canWrite": False})
        self.reply = upstream_response(body={
            "data": [{"_row_id": 1, "code": "000001"}],
            "pagination": {"limit": 1, "has_more": False, "total": None},
        })
        result = self.client.get(
            ROWS + "?limit=1&filters=%5B%7B%22field%22%3A%22code%22%7D%5D",
            headers={"X-API-Key": "browser-must-not-control-key"},
        )
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(result.json, json.loads(self.reply.content))
        call = self.calls[-1]
        self.assertEqual(call["method"], "GET")
        self.assertEqual(call["url"], "http://127.0.0.1:8897/api/v1/tables/cd80_daily/rows")
        self.assertEqual(call["kwargs"]["params"],
                         [("limit", "1"), ("filters", '[{"field":"code"}]')])
        self.assertEqual(call["kwargs"]["headers"]["X-API-Key"], "test-read-secret")
        self.assertNotIn("Cookie", call["kwargs"]["headers"])
        self.assertNotIn("Authorization", call["kwargs"]["headers"])
        self.assertFalse(call["trust_env"])
        self.assertFalse(call["kwargs"]["allow_redirects"])
        self.assertTrue(all(value > 0 for value in call["kwargs"]["timeout"]))

        queried = self.client.post(
            ROOT + "/tables/cd80_daily/query", json={"limit": 1}
        )
        self.assertEqual(queried.status_code, 200)
        self.assertEqual(self.calls[-1]["method"], "POST")
        self.assertEqual(self.calls[-1]["url"],
                         "http://127.0.0.1:8897/api/v1/tables/cd80_daily/query")
        self.assertEqual(self.calls[-1]["kwargs"]["json"], {"limit": 1})
        self.assertEqual(self.calls[-1]["kwargs"]["headers"]["X-API-Key"],
                         "test-read-secret")
        for path, target in [
            (ROOT + "/tables", "/api/v1/tables"),
            (ROOT + "/tables/cd80_daily/schema", "/api/v1/tables/cd80_daily/schema"),
            (ROWS + "/1", "/api/v1/tables/cd80_daily/rows/1"),
        ]:
            self.assertEqual(self.client.get(path).status_code, 200)
            self.assertEqual(self.calls[-1]["url"], "http://127.0.0.1:8897" + target)

    def test_write_requires_allowlist_and_never_uses_browser_key(self):
        user = self.login(name="Same display name")
        denied = self.client.post(
            ROWS, json={"data": {"code": "000001"}},
            headers={"X-API-Key": "browser-key"},
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(self.calls, [])
        # The existing account has no username; the display name must not grant admin.
        self.app.config["CD80_DATA_ADMIN_USERS"] = user["name"]
        self.assertFalse(self.client.get(ROOT + "/capabilities").json["canWrite"])
        self.app.config["CD80_DATA_ADMIN_USERS"] = user["id"]
        self.assertTrue(self.client.get(ROOT + "/capabilities").json["canWrite"])

        cases = [
            ("post", ROWS, {"data": {"code": "000002"}}, 201,
             "http://127.0.0.1:8897/api/v1/tables/cd80_daily/rows"),
            ("post", ROWS + "/batch", {"rows": [{"code": "000002"}]}, 201,
             "http://127.0.0.1:8897/api/v1/tables/cd80_daily/rows/batch"),
            ("patch", ROWS + "/1", {"data": {"code": "000003"}}, 200,
             "http://127.0.0.1:8897/api/v1/tables/cd80_daily/rows/1"),
            ("delete", ROWS + "/1", None, 204,
             "http://127.0.0.1:8897/api/v1/tables/cd80_daily/rows/1"),
        ]
        for method, path, body, status, target in cases:
            self.reply = upstream_response(status=status, body={"data": {"_row_id": 1}})
            kwargs = {"headers": {"X-API-Key": "browser-key"}}
            if body is not None:
                kwargs["json"] = body
            result = getattr(self.client, method)(path, **kwargs)
            self.assertEqual(result.status_code, status, (path, result.json))
            call = self.calls[-1]
            self.assertEqual(call["url"], target)
            self.assertEqual(call["kwargs"]["headers"]["X-API-Key"], "test-admin-secret")
            self.assertNotIn("Cookie", call["kwargs"]["headers"])
            if body is not None:
                self.assertEqual(call["kwargs"]["json"], body)
        self.assertEqual(result.get_data(), b"")

    def test_no_admin_key_means_no_write_even_if_user_is_allowlisted(self):
        user = self.login()
        self.app.config["CD80_DATA_ADMIN_USERS"] = user["id"]
        self.app.config["CD80_ADMIN_API_KEY"] = ""
        self.assertFalse(self.client.get(ROOT + "/capabilities").json["canWrite"])
        result = self.client.patch(ROWS + "/1", json={"data": {"code": "x"}})
        self.assertEqual(result.status_code, 403)
        self.assertEqual(self.calls, [])

    def test_admin_key_can_read_when_optional_read_key_is_unset(self):
        self.login()
        self.app.config["CD80_READ_API_KEY"] = ""
        result = self.client.get(ROOT + "/tables")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.calls[-1]["kwargs"]["headers"]["X-API-Key"],
                         "test-admin-secret")

    def test_localhost_alias_is_pinned_to_loopback_address(self):
        self.login()
        self.app.config["CD80_DATA_API_URL"] = "http://localhost:8897"
        self.assertEqual(self.client.get(ROOT + "/tables").status_code, 200)
        self.assertEqual(self.calls[-1]["url"], "http://127.0.0.1:8897/api/v1/tables")

    def test_upstream_errors_redirects_and_timeouts_are_safe(self):
        self.login()
        self.reply = upstream_response(422, {"detail": [{"msg": "Invalid limit"}]})
        invalid = self.client.get(ROWS)
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(invalid.json, {"error": "Invalid limit"})
        self.reply = upstream_response(307, {"detail": "redirect"})
        self.reply.headers["Location"] = "https://external.example/steal"
        redirect = self.client.get(ROWS)
        self.assertEqual(redirect.status_code, 502)
        self.assertEqual(len(self.calls), 2)  # No follow-up request to Location.
        self.failure = requests.exceptions.Timeout("test timeout")
        timeout = self.client.get(ROWS)
        self.assertEqual(timeout.status_code, 502)
        self.assertIn("error", timeout.json)

    def test_unknown_paths_traversal_and_nonlocal_origin_never_call_upstream(self):
        user = self.login()
        for path in [
            ROOT + "/tables/_datasets/rows",
            ROOT + "/tables/cd80_daily;drop/rows",
            ROOT + "/tables/cd80_daily/%2e%2e/rows",
            ROOT + "/tables/cd80_daily/rows/1/extra",
            ROOT + "/arbitrary-url/https://external.example",
        ]:
            result = self.client.get(path)
            self.assertIn(result.status_code, {400, 404}, (path, result.status_code))
        self.assertEqual(self.calls, [])
        self.app.config["CD80_DATA_API_URL"] = "http://example.com:8897"
        bad_target = self.client.get(ROOT + "/tables")
        self.assertEqual(bad_target.status_code, 503)
        self.assertEqual(self.calls, [])
        self.app.config["CD80_DATA_ADMIN_USERS"] = user["id"]
        self.assertEqual(self.client.post(ROWS, json={"data": {"code": "x"}}).status_code, 503)
        self.assertEqual(self.calls, [])
        self.app.config["CD80_DATA_API_URL"] = "http://127.0.0.1:8897/redirect"
        self.assertEqual(self.client.get(ROOT + "/tables").status_code, 503)
        self.assertEqual(self.calls, [])

    def test_request_body_limit_and_bad_json_stop_before_upstream(self):
        user = self.login()
        self.app.config["CD80_DATA_ADMIN_USERS"] = user["id"]
        too_large = self.client.post(ROWS, json={"data": {"code": "x" * (2 * 1024 * 1024)}})
        self.assertEqual(too_large.status_code, 413)
        malformed = self.client.post(ROWS, data=b"{not json", content_type="application/json")
        self.assertEqual(malformed.status_code, 400)
        self.assertEqual(self.calls, [])

    def test_existing_local_origin_boundary_still_applies(self):
        self.login()
        external_origin = self.client.get(ROWS, headers={"Origin": "http://evil.example"})
        self.assertEqual(external_origin.status_code, 403)
        external_host = self.client.get(ROWS, headers={"Host": "evil.example"})
        self.assertEqual(external_host.status_code, 403)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
