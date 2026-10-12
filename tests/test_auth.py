"""Local account and legacy-claim tests using disposable directories only."""

import sqlite3
import stat
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app import create_app  # noqa: E402
from backend.app.modules.workbench.auth import LocalAuth  # noqa: E402
from backend.app.modules.workbench.errors import GatewayError  # noqa: E402


class LocalAuthTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ficagent-auth-test-")
        self.root = Path(self.temporary.name) / "data"

    def tearDown(self):
        self.temporary.cleanup()

    def test_register_login_expiry_and_logout(self):
        auth = LocalAuth(self.root)
        alice, token = auth.register("Alice@Example.Invalid", "test-only-123", "Alice")
        self.assertEqual(alice["name"], "Alice")
        self.assertEqual(stat.S_IMODE(auth.database.stat().st_mode), 0o600)
        cookie = auth.cookie(token)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertEqual(auth.authenticate(cookie)["id"], alice["id"])
        self.assertEqual(auth.login("alice@example.invalid", "test-only-123")[0]["id"], alice["id"])
        database_bytes = auth.database.read_bytes()
        self.assertNotIn(b"test-only-123", database_bytes)
        self.assertNotIn(token.encode(), database_bytes)

        auth.logout(cookie)
        with self.assertRaises(GatewayError) as error:
            auth.authenticate(cookie)
        self.assertEqual(error.exception.status, 401)
        _, second_token = auth.login("alice@example.invalid", "test-only-123")
        with sqlite3.connect(auth.database) as connection:
            connection.execute("UPDATE sessions SET expires_at = 0")
        with self.assertRaises(GatewayError) as error:
            auth.authenticate(auth.cookie(second_token))
        self.assertEqual(error.exception.status, 401)
        self.assertIn("Max-Age=0", auth.clear_cookie())

    def test_duplicate_email_and_parallel_registration_are_atomic(self):
        auth = LocalAuth(self.root)

        def attempt():
            try:
                return auth.register("parallel@example.invalid", "test-only-123", "Parallel")[0]
            except GatewayError as error:
                return error

        with ThreadPoolExecutor(max_workers=2) as workers:
            outcomes = list(workers.map(lambda _item: attempt(), range(2)))
        self.assertEqual(sum(isinstance(item, dict) for item in outcomes), 1)
        self.assertEqual(sum(isinstance(item, GatewayError) and item.status == 409 for item in outcomes), 1)
        with sqlite3.connect(auth.database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)
        with self.assertRaises(GatewayError) as error:
            auth.login("missing@example.invalid", "test-only-123")
        self.assertEqual(error.exception.status, 401)

    def test_sessions_and_user_ids_are_separate(self):
        auth = LocalAuth(self.root)
        alice, alice_token = auth.register("alice@example.invalid", "test-only-123", "Alice")
        bob, bob_token = auth.register("bob@example.invalid", "test-only-456", "Bob")
        self.assertNotEqual(alice["id"], bob["id"])
        self.assertNotEqual(alice_token, bob_token)
        self.assertEqual(auth.authenticate(auth.cookie(alice_token))["id"], alice["id"])
        self.assertEqual(auth.authenticate(auth.cookie(bob_token))["id"], bob["id"])
        with self.assertRaises(GatewayError):
            auth.login("alice@example.invalid", "wrong-password")

    def _fake_legacy_state(self):
        legacy = self.root / "users" / "123456789"
        legacy.mkdir(parents=True)
        (legacy / "state.json").write_text('{"projects":[],"tasks":[{"id":"old-task"}],"schedules":[],"files":[],"bindings":{}}')
        return legacy

    def test_history_import_is_explicit_and_claimed_once(self):
        legacy = self._fake_legacy_state()
        disabled = LocalAuth(self.root, import_legacy=False)
        first, _ = disabled.register("first@example.invalid", "test-only-123", "First")
        self.assertFalse((self.root / "users" / first["id"]).exists())
        self.assertTrue(legacy.exists())

        with tempfile.TemporaryDirectory(prefix="ficagent-import-test-") as fresh:
            fresh_root = Path(fresh) / "data"
            fresh_legacy = fresh_root / "users" / "123456789"
            fresh_legacy.mkdir(parents=True)
            (fresh_legacy / "state.json").write_bytes((legacy / "state.json").read_bytes())
            enabled = LocalAuth(fresh_root, import_legacy=True)
            alice, _ = enabled.register("alice@example.invalid", "test-only-123", "Alice")
            copied = fresh_root / "users" / alice["id"] / "state.json"
            self.assertTrue(copied.exists())
            self.assertIn("old-task", copied.read_text())
            self.assertTrue(fresh_legacy.exists())
            bob, _ = enabled.register("bob@example.invalid", "test-only-123", "Bob")
            self.assertFalse((fresh_root / "users" / bob["id"]).exists())
            with sqlite3.connect(enabled.database) as connection:
                claims = connection.execute("SELECT source_id, user_id FROM legacy_claims").fetchall()
            self.assertEqual(claims, [("123456789", alice["id"])])

    def test_failed_legacy_copy_rolls_back_registration(self):
        legacy = self._fake_legacy_state()
        outside = Path(self.temporary.name) / "outside.txt"
        outside.write_text("test")
        (legacy / "outside-link").symlink_to(outside)
        auth = LocalAuth(self.root, import_legacy=True)
        with self.assertRaises(GatewayError) as error:
            auth.register("alice@example.invalid", "test-only-123", "Alice")
        self.assertEqual(error.exception.status, 409)
        with sqlite3.connect(auth.database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM legacy_claims").fetchone()[0], 0)

    def test_parallel_first_accounts_claim_history_for_only_one_user(self):
        self._fake_legacy_state()
        auth = LocalAuth(self.root, import_legacy=True)

        def attempt(index):
            return auth.register(f"user{index}@example.invalid", "test-only-123", f"User {index}")[0]

        with ThreadPoolExecutor(max_workers=2) as workers:
            users = list(workers.map(attempt, range(2)))
        imported = [user for user in users if (self.root / "users" / user["id"] / "state.json").exists()]
        self.assertEqual(len(imported), 1)
        with sqlite3.connect(auth.database) as connection:
            claims = connection.execute("SELECT source_id, user_id FROM legacy_claims").fetchall()
        self.assertEqual(claims, [("123456789", imported[0]["id"])])

    def test_http_register_runtime_and_logout(self):
        app = create_app({
            "TESTING": True,
            "DATA_DIR": str(self.root),
            "GITEE_AI_API_KEY": "",
            "IMPORT_LEGACY_HISTORY": False,
        })
        with app.test_client() as client:
            self.assertEqual(client.get("/desktop/api/runtime").status_code, 401)
            registered = client.post(
                "/desktop/api/register",
                json={"email": "alice@example.invalid", "password": "test-only-123", "name": "Alice"},
            )
            self.assertEqual(registered.status_code, 200)
            self.assertIn("HttpOnly", registered.headers["Set-Cookie"])
            self.assertEqual(client.get("/desktop/api/session").json["user"]["name"], "Alice")
            runtime = client.get("/desktop/api/runtime")
            self.assertEqual(runtime.status_code, 200)
            self.assertEqual(runtime.json["provider"], "Gitee AI")
            self.assertFalse(runtime.json["configured"])
            self.assertNotIn("key", runtime.json)
            self.assertEqual(client.post("/desktop/api/logout").status_code, 200)
            self.assertEqual(client.get("/desktop/api/runtime").status_code, 401)
        app.extensions["workbench_service"].close()


if __name__ == "__main__":
    unittest.main()
