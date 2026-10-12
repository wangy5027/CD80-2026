"""HTTP routes for the local workbench and same-origin Coze bridge."""

import os
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import requests
from flask import Blueprint, Response, current_app, jsonify, redirect, request, send_from_directory, stream_with_context

from .coze import GatewayError
from .service import rewrite_cookie


workbench = Blueprint("workbench", __name__)
ALL_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "proxy-connection", "te", "trailer", "transfer-encoding", "upgrade"}


def connection_tokens(value):
    """Find headers nominated by a Connection field for one HTTP hop."""
    return {item.strip().lower() for item in (value or "").split(",") if item.strip()}


def service():
    """Resolve the application-scoped business service."""
    return current_app.extensions["workbench_service"]


def json_body():
    """Parse bounded JSON objects with the previous error contract."""
    request.max_content_length = 2 * 1024 * 1024
    data = request.get_data(cache=True)
    if len(data) > 2 * 1024 * 1024:
        raise GatewayError("文件过大，单次上传上限为 30 MB。", 413)
    try:
        import json
        value = json.loads(data) if data else {}
    except (UnicodeDecodeError, ValueError):
        raise GatewayError("请求格式不正确。", 400) from None
    if not isinstance(value, dict):
        raise GatewayError("请求格式不正确。", 400)
    return value


@workbench.route("/desktop/health", methods=["GET"])
def health():
    """Identify the standalone website's Flask process and health."""
    return jsonify({"ok": True, "app": "Ficagent", "backend": "flask", "instanceId": os.environ.get("FICAGENT_INSTANCE_ID", "")})


@workbench.route("/desktop/coze/studio", methods=["GET"])
@workbench.route("/desktop/coze/models", methods=["GET"])
def coze_redirect():
    """Open Coze itself; keep the browser's local host alias for its cookie."""
    base = urlsplit(service().coze.base)
    hostname = request.host.split(":", 1)[0] if base.hostname in {"127.0.0.1", "localhost"} else base.hostname
    authority = hostname + (f":{base.port}" if base.port is not None else "")
    models = request.path.endswith("models")
    destination = urlunsplit((base.scheme, authority, "/admin/" if models else "/", "", "model-management" if models else ""))
    return redirect(destination, code=302)


@workbench.route("/desktop/api/<path:endpoint>", methods=ALL_METHODS)
def api(endpoint):
    """Preserve the existing /desktop/api paths and response shapes."""
    key = "/" + endpoint
    method = request.method
    backend = service()
    cookie = request.headers.get("Cookie", "")
    if key in {"/login", "/register"}:
        if method != "POST":
            raise GatewayError("请求方法不受支持。", 405)
        payload, cookies = backend.login(endpoint, json_body())
        response = jsonify(payload)
        for item in cookies:
            response.headers.add("Set-Cookie", item)
        response.headers["Cache-Control"] = "no-store"
        return response
    if key == "/session":
        return jsonify(backend.session(cookie))
    if key == "/logout" and method == "POST":
        payload = backend.logout(cookie)
        response = jsonify(payload)
        response.headers.add("Set-Cookie", "session_key=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0")
        return response

    auth = backend.authenticate(cookie)
    user_id = auth["user"]["id"]
    if key == "/chat" and method == "POST":
        generator, abandon = backend.start_chat(auth, json_body())
        response = Response(stream_with_context(generator), mimetype="text/event-stream")
        response.headers.update({"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
        response.call_on_close(abandon)
        return response
    if key == "/state" and method == "GET":
        return jsonify(backend.state(user_id))
    if key == "/stop" and method == "POST":
        return jsonify(backend.stop(user_id, json_body().get("taskId")))
    if key == "/projects" and method == "POST":
        return jsonify(backend.create_project(user_id, json_body())), 201
    project = re.fullmatch(r"/projects/([A-Za-z0-9_-]+)", key)
    if project and method == "DELETE":
        return jsonify(backend.delete_project(user_id, project.group(1)))
    if key == "/schedules" and method == "POST":
        return jsonify(backend.create_schedule(user_id, json_body())), 201
    schedule = re.fullmatch(r"/schedules/([A-Za-z0-9_-]+)", key)
    if schedule and method in {"PATCH", "DELETE"}:
        return jsonify(backend.change_schedule(user_id, schedule.group(1), method, json_body() if method == "PATCH" else {}))
    if key == "/files" and method == "POST":
        if request.content_length and request.content_length > 35 * 1024 * 1024:
            raise GatewayError("文件过大，单次上传上限为 30 MB。", 413)
        if not request.mimetype.startswith("multipart/"):
            raise GatewayError("文件上传格式不正确。", 400)
        return jsonify(backend.upload_file(user_id, request.files.get("file"), str(request.form.get("projectId") or ""))), 201
    file_match = re.fullmatch(r"/files/([A-Za-z0-9_-]+)", key)
    if file_match and method == "DELETE":
        return jsonify(backend.delete_file(user_id, file_match.group(1)))
    if file_match and method == "GET":
        stream, metadata, lock = backend.open_file(user_id, file_match.group(1))
        released = False

        def release():
            nonlocal released
            if not released:
                released = True
                stream.close()
                lock.release()

        def body():
            try:
                while chunk := stream.read(65536):
                    yield chunk
            finally:
                release()

        response = Response(stream_with_context(body()))
        response.headers.update(backend.file_headers(metadata, "download" in request.args))
        response.headers["Content-Length"] = str(metadata["size"])
        response.call_on_close(release)
        return response
    task = re.fullmatch(r"/tasks/([A-Za-z0-9_-]+)", key)
    if task and method == "GET":
        return jsonify(backend.get_task(user_id, task.group(1)))
    if task and method == "DELETE":
        return jsonify(backend.delete_task(auth, task.group(1)))
    raise GatewayError("接口不存在。", 404)


@workbench.route("/desktop", methods=["GET"])
@workbench.route("/desktop/", methods=["GET"])
@workbench.route("/desktop/<path:asset>", methods=["GET"])
def frontend(asset=""):
    """Serve only the Vite build output under the historic workbench URL."""
    if asset and not (asset.startswith("assets/") or re.fullmatch(r"[\w.-]+\.(?:js|css|svg|png)", asset)):
        return proxy_coze()
    filename = asset or "index.html"
    dist = Path(current_app.config["FRONTEND_DIST"]).resolve()
    target = (dist / filename).resolve()
    if dist not in target.parents:
        raise GatewayError("文件不存在。", 404)
    if not target.is_file():
        raise GatewayError("网站界面文件不存在，请先构建前端。", 404)
    response = send_from_directory(dist, filename, conditional=False)
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@workbench.route("/", defaults={"other": ""}, methods=ALL_METHODS)
@workbench.route("/<path:other>", methods=ALL_METHODS)
def proxy_coze(other=""):
    """Forward remaining same-origin paths to the configured local Coze service."""
    if request.path == "/" and request.method == "GET":
        return redirect("/desktop/", code=302)
    backend = service()
    target = backend.coze.base + request.full_path.rstrip("?")
    incoming_hop = HOP_HEADERS | connection_tokens(request.headers.get("Connection"))
    headers = {key: value for key, value in request.headers.items() if key.lower() not in incoming_hop | {"host", "origin", "content-length"}}
    headers["Origin"] = backend.coze.base
    try:
        upstream = requests.request(request.method, target, headers=headers, data=request.get_data(), allow_redirects=False, timeout=(5, 120), stream=True)
    except requests.Timeout:
        raise GatewayError("本地 Coze 响应超时。", 504) from None
    except requests.RequestException:
        raise GatewayError("本地 Coze 服务暂时无法连接。", 503) from None

    def body():
        try:
            yield from upstream.raw.stream(65536, decode_content=False)
        finally:
            upstream.close()

    response = Response(stream_with_context(body()), status=upstream.status_code)
    outgoing_hop = HOP_HEADERS | connection_tokens(upstream.headers.get("Connection"))
    for key, value in upstream.headers.items():
        if key.lower() not in outgoing_hop | {"set-cookie"}:
            response.headers[key] = value
    getlist = getattr(upstream.raw.headers, "getlist", None)
    cookies = getlist("Set-Cookie") if getlist else ([upstream.headers["Set-Cookie"]] if "Set-Cookie" in upstream.headers else [])
    for cookie in cookies:
        response.headers.add("Set-Cookie", rewrite_cookie(cookie))
    response.call_on_close(upstream.close)
    return response
