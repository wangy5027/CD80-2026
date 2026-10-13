"""Test one existing account's financial permissions against disposable data.

Run from the desktop website directory::

    cd80-data-api/.venv/bin/python tests/account_financial_fixture.py

The production account and competition databases are opened read-only. The
website account uses a public fixture password in a temporary auth database;
the real password, its hash, and production sessions are never copied.
"""

from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import signal
import sqlite3
import tempfile
from urllib.parse import quote

from dotenv import dotenv_values
from werkzeug.serving import make_server

from financial_browser_fixture import (
    API_ROOT, PASSWORD, WEBSITE_ROOT, _make_website, _start_data_server,
    copy_sample_database,
)


ACCOUNT_EMAIL = "durantjialiang@gmail.com"


def _read_account(source_auth: Path, email: str) -> dict[str, str]:
    if not source_auth.is_file():
        raise FileNotFoundError(f"未找到网站账号数据库：{source_auth}")
    uri = "file:" + quote(str(source_auth.resolve()), safe="/") + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        row = connection.execute(
            "SELECT id,email,name FROM users WHERE email_key=?",
            (email.casefold(),),
        ).fetchone()
    if row is None:
        raise RuntimeError(f"网站账号数据库中没有该账号：{email}")
    identity = {key: row[key] for key in ("id", "email", "name")}
    if not identity["id"] or identity["email"].casefold() != email.casefold():
        raise RuntimeError("网站账号身份记录不完整")
    return identity


def _read_allowlist(env_file: Path) -> str:
    if not env_file.is_file():
        raise FileNotFoundError(f"未找到数据服务配置文件：{env_file}")
    # Parse only in memory. Never print or copy the service credentials.
    value = dotenv_values(env_file).get("CD80_DATA_ADMIN_USERS")
    return str(value or "")


def _install_account(website, temporary_root: Path, identity: dict[str, str],
                     allowlist: str) -> bool:
    """Create a throwaway login with the exact production id/email/name."""
    with website.test_client() as client:
        registration = client.post(
            "/desktop/api/register",
            json={"email": identity["email"], "name": identity["name"],
                  "password": PASSWORD},
        )
        if registration.status_code != 200:
            raise RuntimeError(f"临时账号创建失败：HTTP {registration.status_code}")
        old_id = registration.json["user"]["id"]
        logout = client.post("/desktop/api/logout")
        if logout.status_code != 200:
            raise RuntimeError("无法清除临时注册会话")

    temporary_auth = temporary_root / "accounts" / "auth.sqlite3"
    with closing(sqlite3.connect(temporary_auth)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        session_count = connection.execute(
            "SELECT COUNT(*) FROM sessions WHERE user_id=?", (old_id,)
        ).fetchone()[0]
        if session_count:
            raise RuntimeError("临时注册会话仍存在，不能替换账号 ID")
        cursor = connection.execute(
            "UPDATE users SET id=? WHERE id=?", (identity["id"], old_id)
        )
        if cursor.rowcount != 1:
            raise RuntimeError("临时账号 ID 替换失败")
        connection.commit()
        clone = connection.execute(
            "SELECT id,email,name FROM users WHERE email_key=?",
            (identity["email"].casefold(),),
        ).fetchone()
    if clone != (identity["id"], identity["email"], identity["name"]):
        raise RuntimeError("临时账号身份与原账号不一致")

    # Use the exact configured list; never add the target ID for the test.
    website.config["CD80_DATA_ADMIN_USERS"] = allowlist
    with website.test_client() as client:
        login = client.post(
            "/desktop/api/login",
            json={"email": identity["email"], "password": PASSWORD},
        )
        if login.status_code != 200:
            raise RuntimeError("临时账号无法登录")
        capability = client.get("/desktop/api/financial-data/capabilities")
        client.post("/desktop/api/logout")
    if capability.status_code != 200:
        raise RuntimeError("临时账号权限接口不可用")
    return capability.json.get("canWrite") is True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", type=Path,
                        default=API_ROOT / "data" / "cd80.sqlite3")
    parser.add_argument("--source-auth", type=Path,
                        default=WEBSITE_ROOT / "data" / "auth.sqlite3")
    parser.add_argument("--service-env", type=Path,
                        default=WEBSITE_ROOT / ".env")
    parser.add_argument("--rows-per-table", type=int, default=65)
    parser.add_argument("--api-port", type=int, default=48995)
    parser.add_argument("--website-port", type=int, default=48996)
    parser.add_argument("--frontend-dist", type=Path,
                        default=WEBSITE_ROOT / "frontend" / "dist")
    args = parser.parse_args()
    if args.api_port == args.website_port or not all(
        1 <= port <= 65535 for port in (args.api_port, args.website_port)
    ):
        parser.error("两个端口必须不同，且均在 1 到 65535 范围内")
    if not (args.frontend_dist / "index.html").is_file():
        parser.error("请先构建前端：npm --prefix frontend run build")

    identity = _read_account(args.source_auth, ACCOUNT_EMAIL)
    allowlist = _read_allowlist(args.service_env)

    def stop(_signal, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with tempfile.TemporaryDirectory(prefix="cd80-account-financial-") as temporary:
        root = Path(temporary)
        sample_db = root / "sample.sqlite3"
        if sample_db.resolve() == args.source_db.resolve():
            raise RuntimeError("测试数据库与正式数据库路径相同，已停止")
        counts = copy_sample_database(args.source_db, sample_db,
                                      args.rows_per_table)
        listener = api_server = api_thread = website_server = website = None
        try:
            listener, api_server, api_thread = _start_data_server(
                sample_db, args.api_port,
            )
            website = _make_website(root, sample_db, args.api_port,
                                    args.website_port, args.frontend_dist)
            can_write = _install_account(website, root, identity, allowlist)

            # _make_website has already registered its read-only stats route.
            # Extend that fixture-only response without adding routes after
            # Flask has processed registration/login requests.
            original_stats = website.view_functions["fixture_stats"]

            def account_fixture_stats():
                response = original_stats()
                details = response.get_json()
                details.update({
                    "accountEmail": identity["email"],
                    "identityMatchesSource": True,
                    "canWriteFromConfiguredAllowlist": can_write,
                })
                response.set_data(json.dumps(details, ensure_ascii=False))
                return response

            website.view_functions["fixture_stats"] = account_fixture_stats

            website_server = make_server(
                "127.0.0.1", args.website_port, website, threaded=True,
            )
            print(json.dumps({
                "ready": True,
                "url": f"http://127.0.0.1:{args.website_port}/desktop/",
                "stats": f"http://127.0.0.1:{args.website_port}/__financial_fixture__/stats",
                "identity": f"http://127.0.0.1:{args.website_port}/__financial_fixture__/stats",
                "apiHealth": f"http://127.0.0.1:{args.api_port}/health",
                "fixture": True,
                "temporaryData": True,
                "realModel": False,
                "tableCount": len(counts),
                "sampleRows": sum(counts.values()),
                "accountEmail": identity["email"],
                "identityMatchesSource": True,
                "canWriteFromConfiguredAllowlist": can_write,
                "password": PASSWORD,
            }, ensure_ascii=False), flush=True)
            website_server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            if website_server is not None:
                website_server.server_close()
            if website is not None:
                website.extensions["workbench_service"].close()
            if api_server is not None:
                api_server.should_exit = True
            if api_thread is not None:
                api_thread.join(timeout=10)
            if listener is not None:
                listener.close()


if __name__ == "__main__":
    main()
