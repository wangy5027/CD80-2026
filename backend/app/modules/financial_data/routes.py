"""Fixed-route, local-only bridge to the 成都八零 SQLite API."""

from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

import requests
from flask import Blueprint, Response, current_app, jsonify, request

from ..workbench.errors import GatewayError


financial_data = Blueprint("financial_data", __name__)
_TABLE = re.compile(r"cd80_[a-z0-9_]+\Z")
_MAX_BODY_BYTES = 2 * 1024 * 1024
_TIMEOUT = (3, 30)


def _account():
    service = current_app.extensions["workbench_service"]
    from ..capability_library.data_connections import request_user
    return request_user(service)


def _can_write(user):
    configured = str(current_app.config.get("CD80_ADMIN_API_KEY") or "").strip()
    names = {
        part.strip()
        for part in str(current_app.config.get("CD80_DATA_ADMIN_USERS") or "").split(",")
        if part.strip()
    }
    # Existing accounts have an id, email and display name, but no username.
    # Do not use a non-unique display name as an administrator identifier.
    identifiers = {str(user.get("id") or "")}
    if user.get("username"):
        identifiers.add(str(user["username"]))
    return bool(configured and names.intersection(identifiers))


def _local_origin():
    configured = str(current_app.config.get("CD80_DATA_API_URL") or "").strip()
    try:
        parsed = urlsplit(configured)
        port = parsed.port
    except ValueError:
        port = None
        parsed = None
    if (
        parsed is None
        or parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or port is None
        or port < 1
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise GatewayError("金融数据服务地址必须是本机 HTTP 地址。", 503)
    # Rebuild the origin so userinfo, URL path and percent escapes cannot pass.
    # Pin the friendly localhost alias to a literal loopback address so local
    # host resolution cannot move a request carrying a server key elsewhere.
    host = "[::1]" if parsed.hostname == "::1" else "127.0.0.1"
    return f"http://{host}:{port}"


def _table_name(table):
    if not _TABLE.fullmatch(table):
        raise GatewayError("数据表不存在。", 404)
    return table


def _json_payload():
    request.max_content_length = _MAX_BODY_BYTES
    raw = request.get_data(cache=False)
    if len(raw) > _MAX_BODY_BYTES:
        raise GatewayError("请求数据过大。", 413)
    if not request.is_json:
        raise GatewayError("请提交 JSON 请求体。", 400)
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, ValueError):
        raise GatewayError("请求 JSON 格式不正确。", 400) from None
    if not isinstance(payload, dict):
        raise GatewayError("请求 JSON 必须是对象。", 400)
    return payload


def _upstream_error(upstream):
    try:
        body = upstream.json()
    except (ValueError, requests.exceptions.RequestException):
        body = {}
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict) and isinstance(detail.get('code'), str):
        raise GatewayError(str(detail.get('message', '数据请求失败。'))[:300], upstream.status_code, detail['code'])
    if isinstance(detail, list):
        detail = next((item.get("msg") for item in detail if isinstance(item, dict) and item.get("msg")), None)
    if not isinstance(detail, str) or not detail.strip():
        detail = body.get("error") if isinstance(body, dict) else None
    if not isinstance(detail, str) or not detail.strip():
        detail = f"金融数据服务请求失败（{upstream.status_code}）。"
    status = upstream.status_code if 400 <= upstream.status_code <= 599 else 502
    raise GatewayError(detail.strip()[:300], status)


def _forward(path, *, write=False, body=False):
    user = _account()
    if write and not _can_write(user):
        raise GatewayError("当前账号没有金融数据写入权限。", 403)
    origin = _local_origin()  # Validate before attaching any server-side secret.
    from ..capability_library.read_budget import current_scope
    scope = str(user['id']) + ':' + str(current_scope() or 'interactive')
    headers = {"Accept": "application/json", "X-CD80-Scope": scope}
    if write:
        headers["X-API-Key"] = str(current_app.config["CD80_ADMIN_API_KEY"])
    else:
        key = str(current_app.config.get("CD80_READ_API_KEY") or
                  current_app.config.get("CD80_ADMIN_API_KEY") or "")
        if key:
            headers["X-API-Key"] = key
    kwargs = {
        "headers": headers,
        "params": list(request.args.items(multi=True)),
        "timeout": _TIMEOUT,
        "allow_redirects": False,
    }
    if body:
        kwargs["json"] = _json_payload()
    try:
        with requests.Session() as session:
            session.trust_env = False
            upstream = session.request(request.method, origin + "/api/v1" + path, **kwargs)
    except requests.exceptions.RequestException:
        raise GatewayError("无法连接本机金融数据服务，请确认数据服务已启动。", 502) from None
    if 300 <= upstream.status_code < 400:
        raise GatewayError("金融数据服务返回了不受支持的跳转。", 502)
    if upstream.status_code >= 400:
        _upstream_error(upstream)
    if upstream.status_code == 204:
        return Response(status=204)
    if "application/json" not in upstream.headers.get("Content-Type", "").lower():
        raise GatewayError("金融数据服务响应格式不正确。", 502)
    # Return the upstream success document byte-for-byte; this preserves the
    # data/pagination contract for callers without forwarding upstream headers.
    return Response(upstream.content, status=upstream.status_code, mimetype="application/json")


@financial_data.route("/desktop/api/financial-data/capabilities", methods=["GET"])
def capabilities():
    user = _account()
    return jsonify({"canRead": True, "canWrite": _can_write(user)})


@financial_data.route("/desktop/api/financial-data/catalog", methods=["GET"])
def catalog():
    """Discover only the official tables present in the configured local database."""
    return _forward("/catalog")


@financial_data.route("/desktop/api/financial-data/tables", methods=["GET"])
def tables():
    return _forward("/tables")


@financial_data.route("/desktop/api/financial-data/tables/<table>/schema", methods=["GET"])
def schema(table):
    return _forward(f"/tables/{_table_name(table)}/schema")


@financial_data.route("/desktop/api/financial-data/tables/<table>/rows", methods=["GET", "POST"])
def rows(table):
    table = _table_name(table)
    return _forward(f"/tables/{table}/rows", write=request.method == "POST", body=request.method == "POST")


@financial_data.route("/desktop/api/financial-data/tables/<table>/query", methods=["POST"])
def query(table):
    return _forward(f"/tables/{_table_name(table)}/query", body=True)


@financial_data.route("/desktop/api/financial-data/tables/<table>/rows/batch", methods=["POST"])
def batch(table):
    return _forward(f"/tables/{_table_name(table)}/rows/batch", write=True, body=True)


@financial_data.post('/desktop/api/financial-data/tables/<table>/scan')
def scan(table):
    return _forward(f'/tables/{_table_name(table)}/scan', body=True)


@financial_data.post('/desktop/api/financial-data/tables/<table>/aggregate')
def aggregate(table):
    return _forward(f'/tables/{_table_name(table)}/aggregate', body=True)


@financial_data.route("/desktop/api/financial-data/tables/<table>/rows/<int:row_id>", methods=["GET", "PATCH", "DELETE"])
def row(table, row_id):
    table = _table_name(table)
    if row_id < 1:
        raise GatewayError("记录不存在。", 404)
    return _forward(
        f"/tables/{table}/rows/{row_id}",
        write=request.method in {"PATCH", "DELETE"},
        body=request.method == "PATCH",
    )
