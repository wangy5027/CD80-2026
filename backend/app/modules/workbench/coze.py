"""Coze HTTP client and event-stream parsing for the workbench."""

from __future__ import annotations

import codecs
import json
import re
from collections.abc import Iterable, Iterator
from typing import Any

import requests


class GatewayError(Exception):
    """An upstream or workbench error with its public HTTP status and code."""

    def __init__(self, message: str, status: int = 502, code: Any = None):
        super().__init__(message)
        self.status = status
        self.code = code


def parse_coze_json(value: str | bytes) -> Any:
    """Decode Coze JSON without rounding integer model, bot, or space IDs."""
    return json.loads(value)


def _set_cookies(response: requests.Response) -> list[str]:
    """Return separate Set-Cookie fields so commas in Expires stay intact."""
    headers = getattr(getattr(response, "raw", None), "headers", None)
    if headers is not None:
        if hasattr(headers, "getlist"):
            values = headers.getlist("Set-Cookie")
            if values:
                return list(values)
        if hasattr(headers, "get_all"):
            values = headers.get_all("Set-Cookie")
            if values:
                return list(values)
    value = response.headers.get("Set-Cookie")
    return [value] if value else []


class CozeClient:
    """Call the local Coze API without sharing mutable HTTP session state."""

    def __init__(self, base_url: str = "http://127.0.0.1:8888"):
        self.base = base_url.rstrip("/")

    def request(
        self,
        route: str,
        body: dict[str, Any] | None = None,
        *,
        cookie: str = "",
        method: str = "POST",
        raw: bool = False,
        timeout: float | tuple[float, float | None] | None = 30,
    ) -> dict[str, Any] | requests.Response:
        """Send one Coze request and return parsed JSON or an open streamed response."""
        if not route.startswith("/") or route.startswith("//") or "\\" in route:
            raise GatewayError("Coze 请求地址不正确。", 400)
        method = method.upper()
        headers = {
            "Content-Type": "application/json",
            "Cookie": cookie,
            "Origin": self.base,
        }
        options: dict[str, Any] = {
            "headers": headers,
            "allow_redirects": False,
            "stream": raw,
            "timeout": timeout,
        }
        if method != "GET":
            options["json"] = body if body is not None else {}
        try:
            response = requests.request(method, self.base + route, **options)
        except requests.RequestException as exc:
            raise GatewayError(
                "Coze 服务暂时无法连接，请检查 COZE_BASE_URL 指向的服务是否正在运行。",
                503,
            ) from exc

        if raw and 200 <= response.status_code < 300:
            return response

        try:
            result = parse_coze_json(response.content)
        except (ValueError, UnicodeError) as exc:
            response.close()
            raise GatewayError(
                f"Coze 返回异常响应（HTTP {response.status_code}），请查看服务日志。", 502
            ) from exc

        status = response.status_code
        code = result.get("code") if isinstance(result, dict) else None
        success_code = code == "0" or (
            isinstance(code, (int, float)) and not isinstance(code, bool) and code == 0
        )
        if not 200 <= status < 300 or (code is not None and not success_code):
            if isinstance(result, dict):
                message = (
                    result.get("msg")
                    or result.get("message")
                    or result.get("error")
                    or f"Coze 请求失败（HTTP {status}）"
                )
            else:
                message = f"Coze 请求失败（HTTP {status}）"
            response.close()
            raise GatewayError(str(message), 401 if status == 401 else (400 if 200 <= status < 300 else status), code)

        cookies = _set_cookies(response)
        response.close()
        return {"result": result, "cookies": cookies, "response": response}

    def json(self, route: str, body: dict[str, Any] | None = None, cookie: str = "") -> Any:
        """Return the parsed JSON body for an authenticated Coze API call."""
        return self.request(route, body, cookie=cookie)["result"]

    def user(self, cookie: str) -> dict[str, str] | None:
        """Read the current Coze user from a session cookie, if present."""
        if not cookie or not re.search(r"(^|;\s*)session_key=", cookie):
            return None
        result = self.json("/api/passport/account/info/v2/", {}, cookie)
        data = result.get("data") or {}
        user_id = data.get("user_id_str") or data.get("user_id") or data.get("uid")
        if not user_id:
            raise GatewayError("登录状态已失效，请重新登录。", 401)
        email = data.get("email") or ""
        return {
            "id": str(user_id),
            "name": data.get("name") or data.get("user_unique_name") or email.split("@")[0] or "本地用户",
            "email": email,
            "avatar": data.get("avatar_url") or "",
        }

    def catalog(self, cookie: str) -> dict[str, Any]:
        """List available models and agents, preserving partial results on failure."""
        spaces = self.json("/api/playground_api/space/list", {}, cookie)
        available = (spaces.get("data") or {}).get("bot_space_list") or []
        space = next((item for item in available if item.get("space_type") == 1), None)
        if space is None and available:
            space = available[0]
        space_id = str(space.get("id")) if space and space.get("id") else ""

        warnings: list[str] = []
        try:
            body = {"model": True}
            if space_id:
                body["space_id"] = space_id
            model_result = self.json("/api/bot/get_type_list", body, cookie)
            model_list = (model_result.get("data") or {}).get("model_list") or []
        except Exception as exc:
            model_list = []
            warnings.append(str(exc))

        try:
            if space_id:
                agent_result = self.json(
                    "/api/intelligence_api/search/get_draft_intelligence_list",
                    {
                        "space_id": space_id,
                        "types": [1],
                        "size": 100,
                        "status": [1, 3, 4],
                        "order_by": 0,
                    },
                    cookie,
                )
                intelligence = (agent_result.get("data") or {}).get("intelligences") or []
            else:
                intelligence = []
        except Exception as exc:
            intelligence = []
            warnings.append(str(exc))

        models = []
        for model in model_list:
            if model.get("is_offline"):
                continue
            model_id = next((model[key] for key in ("model_type", "model_id", "id") if model.get(key) is not None), "")
            if str(model_id):
                models.append({"id": str(model_id), "name": model.get("name") or model.get("model_name") or str(model_id)})

        agents = []
        for item in intelligence:
            agent = item.get("basic_info") or item
            if not agent.get("id"):
                continue
            agents.append(
                {
                    "id": str(agent["id"]),
                    "name": agent.get("name"),
                    "description": agent.get("description") or "",
                    "avatar": agent.get("icon_url") or "",
                    "spaceId": str(agent.get("space_id") or space_id),
                }
            )
        return {"activeSpaceId": space_id, "models": models, "agents": agents, "warnings": warnings}


def _event_from_block(block: str) -> dict[str, str]:
    """Decode one SSE block, combining multiple data lines per the protocol."""
    event = "message"
    data = []
    for line in block.split("\n"):
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            value = line[5:]
            data.append(value[1:] if value.startswith(" ") else value)
    return {"event": event, "data": "\n".join(data)}


def parse_sse(chunks: Iterable[bytes | str]) -> Iterator[dict[str, str]]:
    """Parse SSE across arbitrary UTF-8 chunks and LF or CRLF boundaries."""
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    buffer = ""
    pending_cr = ""
    for chunk in chunks:
        piece = chunk if isinstance(chunk, str) else decoder.decode(chunk)
        text = pending_cr + piece
        pending_cr = "\r" if text.endswith("\r") else ""
        if pending_cr:
            text = text[:-1]
        buffer += text.replace("\r\n", "\n").replace("\r", "\n")
        while "\n\n" in buffer:
            block, buffer = buffer.split("\n\n", 1)
            if block.strip():
                yield _event_from_block(block)

    buffer += (pending_cr + decoder.decode(b"", final=True)).replace("\r\n", "\n").replace("\r", "\n")
    while "\n\n" in buffer:
        block, buffer = buffer.split("\n\n", 1)
        if block.strip():
            yield _event_from_block(block)
    if buffer.strip():
        yield _event_from_block(buffer)
