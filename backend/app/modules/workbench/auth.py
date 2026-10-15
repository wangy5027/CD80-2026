"""Local accounts and browser sessions for the standalone website."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import shutil
import sqlite3
import time
from contextlib import closing
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from uuid import uuid4

from .errors import GatewayError


COOKIE_NAME = "ficagent_session"
SESSION_LIFETIME = 7 * 24 * 60 * 60
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class LocalAuth:
    """Own credentials and sessions without consulting the model provider."""

    def __init__(self, root: str | Path, import_legacy: bool = False):
        self.root = Path(root).resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.database = self.root / "auth.sqlite3"
        self.import_legacy = bool(import_legacy)
        try:
            descriptor = os.open(self.database, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.database, timeout=10)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self):
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL,
                    email_key TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    password_salt BLOB NOT NULL,
                    password_hash BLOB NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    expires_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at);
                CREATE TABLE IF NOT EXISTS legacy_claims (
                    source_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL UNIQUE REFERENCES users(id),
                    claimed_at INTEGER NOT NULL
                );
                """
            )
            connection.commit()

    @staticmethod
    def _email(value):
        email = str(value or "").strip()
        if len(email) > 254 or not EMAIL_PATTERN.fullmatch(email):
            raise GatewayError("请输入有效邮箱。", 400)
        return email, email.casefold()

    @staticmethod
    def _password(value):
        password = str(value or "")
        if len(password) < 6 or len(password.encode("utf-8")) > 1024:
            raise GatewayError("密码长度应为 6 至 1024 字节。", 400)
        return password

    @staticmethod
    def _derive(password, salt):
        return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)

    @staticmethod
    def _public_user(row):
        return {"id": row["id"], "email": row["email"], "name": row["name"]}

    @staticmethod
    def _issue_session(connection, user_id):
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        connection.execute(
            "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
            (digest, user_id, int(time.time()) + SESSION_LIFETIME),
        )
        return token

    def _legacy_source(self, connection):
        if not self.import_legacy:
            return None
        if connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] != 1:
            return None
        if connection.execute("SELECT COUNT(*) FROM legacy_claims").fetchone()[0]:
            return None
        users = self.root / "users"
        if not users.is_dir():
            return None
        candidates = [
            item for item in users.iterdir()
            if item.name.isascii() and item.name.isdigit() and item.is_dir() and not item.is_symlink()
            and (item / "state.json").is_file() and not (item / "state.json").is_symlink()
        ]
        return candidates[0] if len(candidates) == 1 else None

    def _copy_legacy_once(self, connection, user_id):
        source = self._legacy_source(connection)
        if source is None:
            return None
        for parent, directories, files in os.walk(source, followlinks=False):
            if any((Path(parent) / name).is_symlink() for name in directories + files):
                raise GatewayError("历史目录包含符号链接，需要人工检查后迁移。", 409)
        destination = self.root / "users" / user_id
        if destination.exists():
            raise GatewayError("历史记录迁移目标已存在。", 409)
        try:
            shutil.copytree(source, destination)
            for parent, directories, files in os.walk(destination):
                os.chmod(parent, 0o700)
                for filename in files:
                    os.chmod(Path(parent) / filename, 0o600)
            connection.execute(
                "INSERT INTO legacy_claims (source_id, user_id, claimed_at) VALUES (?, ?, ?)",
                (source.name, user_id, int(time.time())),
            )
        except Exception:
            shutil.rmtree(destination, ignore_errors=True)
            raise
        return destination

    def register(self, email, password, name):
        email, email_key = self._email(email)
        password = self._password(password)
        display_name = str(name or "").strip()[:50] or email.split("@", 1)[0][:50]
        salt = secrets.token_bytes(16)
        digest = self._derive(password, salt)
        user_id = uuid4().hex
        copied_legacy = None
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO users (id, email, email_key, name, password_salt, password_hash, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (user_id, email, email_key, display_name, salt, digest, int(time.time())),
                )
                copied_legacy = self._copy_legacy_once(connection, user_id)
                token = self._issue_session(connection, user_id)
                connection.commit()
            except sqlite3.IntegrityError:
                connection.rollback()
                if copied_legacy is not None:
                    shutil.rmtree(copied_legacy, ignore_errors=True)
                raise GatewayError("该邮箱已注册。", 409) from None
            except Exception:
                connection.rollback()
                if copied_legacy is not None:
                    shutil.rmtree(copied_legacy, ignore_errors=True)
                raise
        return {"id": user_id, "email": email, "name": display_name}, token

    def login(self, email, password):
        email, email_key = self._email(email)
        password = self._password(password)
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM users WHERE email_key = ?", (email_key,)).fetchone()
            salt = row["password_salt"] if row is not None else b"\0" * 16
            expected = row["password_hash"] if row is not None else b"\0" * 32
            actual = self._derive(password, salt)
            if row is None or not hmac.compare_digest(actual, expected):
                raise GatewayError("邮箱或密码不正确。", 401)
            token = self._issue_session(connection, row["id"])
            connection.commit()
            return self._public_user(row), token

    @staticmethod
    def _token(cookie_header):
        cookies = SimpleCookie()
        try:
            cookies.load(cookie_header or "")
        except CookieError:
            return None
        item = cookies.get(COOKIE_NAME)
        token = item.value if item is not None else ""
        return token if 20 <= len(token) <= 256 and re.fullmatch(r"[A-Za-z0-9_-]+", token) else None

    def get_user(self, user_id):
        """Resolve server-owned connector identity; no new session is issued."""
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT id,email,name FROM users WHERE id=?", (user_id,)).fetchone()
        if row is None:
            raise GatewayError("账号不可用。", 401)
        return self._public_user(row)

    def authenticate(self, cookie_header):
        token = self._token(cookie_header)
        if token is None:
            raise GatewayError("请先登录本地账号。", 401)
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT users.id, users.email, users.name FROM sessions "
                "JOIN users ON users.id = sessions.user_id "
                "WHERE sessions.token_hash = ? AND sessions.expires_at > ?",
                (digest, int(time.time())),
            ).fetchone()
        if row is None:
            raise GatewayError("登录已过期，请重新登录。", 401)
        return self._public_user(row)

    def logout(self, cookie_header):
        token = self._token(cookie_header)
        if token is None:
            return
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        with closing(self._connect()) as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (digest,))
            connection.commit()

    @staticmethod
    def cookie(token):
        return f"{COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_LIFETIME}"

    @staticmethod
    def clear_cookie():
        return f"{COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
