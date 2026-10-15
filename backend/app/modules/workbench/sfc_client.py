"""Small, credential-isolated client for the public read-only SFC endpoints.

No skill file, local runner, or model-supplied URL is read. A credential file
is read only when its path is explicitly configured by the website operator.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import requests

from .errors import GatewayError
from .transport import AbortableResponse


_HOSTS = {"terminal": "https://terminal.sfccn.com", "zxtop": "https://zxtop.sfccn.com"}
_PATHS = frozenset({
    "/terminal/rest/esSearch/searchkey/page",
    "/terminal/rest/article/recent",
    "/terminal/rest/article/flash",
    "/terminal/rest/theme/stock/articles",
    "/terminal/rest/company/notice",
    "/terminal/rest/company/notice/detail",
    "/terminal/sfc-search/api/v1/sprite/searchReport",
    "/terminal/quotes/api/quote",
    "/terminal/quotes/api/search_v2",
    "/terminal/quotes/api/dayk",
    "/terminal/quotes/api/weekk",
    "/terminal/quotes/api/monthk",
    "/terminal/zxt-web/mongo/query",
    "/terminal/zxt-web/investCalendar/list",
    "/terminal/rest/theme/recomend",
    "/terminal/rest/theme/list",
    "/terminal/rest/theme/detail",
    "/terminal/rest/theme/stocks/list",
    "/terminal/rest/theme/search",
    "/terminal/desk/quotes/api/newsharescal",
    "/console/newsflash/list",
})
_MAX_RESPONSE_BYTES = 2_000_000


class SFCClient:
    """One request at a time, with bounded response and cancellation support."""

    def __init__(self, config, *, transport=None):
        self._config = config
        self._transport = transport if transport is not None else requests
        try:
            timeout = float(config.get("SFC_REQUEST_TIMEOUT", 20))
        except (TypeError, ValueError):
            timeout = 20.0
        self._read_timeout = min(max(timeout, 3.0), 30.0) if math.isfinite(timeout) else 20.0

    def _token(self) -> str:
        token = self._config.get("SFC_TOKEN") or ""
        if isinstance(token, str) and token.strip():
            return token.strip()
        path = self._config.get("SFC_KEY_FILE") or ""
        if not isinstance(path, str) or not path.strip():
            return ""
        try:
            # Only a path explicitly supplied by the website operator is read.
            content = Path(path).expanduser().read_text(encoding="utf-8").strip()
            if content.startswith("{"):
                parsed = json.loads(content)
                return str(parsed.get("api_key") or parsed.get("token") or "").strip() if isinstance(parsed, dict) else ""
            return content
        except (OSError, UnicodeError, ValueError):
            return ""

    @property
    def available(self) -> bool:
        return bool(self._token())

    def request(self, method: str, path: str, context, *, host: str = "terminal", params=None, body=None,
                auth: str = "token") -> dict:
        if host not in _HOSTS or path not in _PATHS or (host == "zxtop") != path.startswith("/console/"):
            raise GatewayError("南财接口地址无效。", 400)
        if (method not in {"GET", "POST"} or auth not in {"token", "bearer", "none"}
                or (host == "zxtop") != (auth == "none")):
            raise GatewayError("南财接口请求方式无效。", 400)
        token = self._token()
        if not token:
            raise GatewayError("南财数据密钥尚未配置。", 503)
        if context.stopped.is_set():
            raise GatewayError("已停止生成。", 409)
        # Several SFC REST routes return HTTP 406 for Accept: application/json.
        headers = {}
        if host == "zxtop":
            # This documented newsflash endpoint identifies its API client this way.
            headers["User-Agent"] = "wxwork"
        if auth != "none":
            headers["Authorization" if auth == "bearer" else "token"] = (
                "Bearer " + token if auth == "bearer" else token
            )
        if body is not None:
            headers["Content-Type"] = "application/json"
        try:
            response = self._transport.request(
                method, _HOSTS[host] + path, headers=headers, params=params, json=body,
                timeout=(3, self._read_timeout), allow_redirects=False, stream=True,
            )
        except requests.Timeout:
            if context.stopped.is_set():
                raise GatewayError("已停止生成。", 409) from None
            raise GatewayError("南财数据请求超时。", 504) from None
        except requests.RequestException:
            if context.stopped.is_set():
                raise GatewayError("已停止生成。", 409) from None
            raise GatewayError("南财数据连接失败。", 502) from None
        abortable = AbortableResponse(response)
        try:
            context.on_response(abortable)
            if response.status_code in {401, 403}:
                raise GatewayError("南财数据认证失败。", 502)
            if response.status_code == 429:
                raise GatewayError("南财数据请求过于频繁。", 503)
            if response.status_code != 200:
                raise GatewayError("南财数据服务暂时不可用。", 502)
            size = 0
            chunks = []
            for chunk in response.iter_content(chunk_size=32768):
                if context.stopped.is_set():
                    raise GatewayError("已停止生成。", 409)
                size += len(chunk)
                if size > _MAX_RESPONSE_BYTES:
                    raise GatewayError("南财数据返回内容过大。", 502)
                chunks.append(chunk)
            try:
                result = json.loads(b"".join(chunks).decode("utf-8-sig"))
            except (ValueError, UnicodeError):
                raise GatewayError("南财数据格式无效。", 502) from None
            if isinstance(result, dict):
                code = result.get("code")
                status = result.get("status")
                if (code is not None and str(code) not in {"0", "200"}) or (
                    status is not None and str(status).lower() not in {"0", "200", "ok", "success"}
                ) or result.get("success") is False or result.get("error"):
                    raise GatewayError("南财数据服务返回业务错误。", 502)
            elif not isinstance(result, list):
                raise GatewayError("南财数据格式无效。", 502)
            if context.stopped.is_set():
                raise GatewayError("已停止生成。", 409)
            return result
        except requests.Timeout:
            if context.stopped.is_set():
                raise GatewayError("已停止生成。", 409) from None
            raise GatewayError("南财数据请求超时。", 504) from None
        except requests.RequestException:
            if context.stopped.is_set():
                raise GatewayError("已停止生成。", 409) from None
            raise GatewayError("南财数据连接失败。", 502) from None
        finally:
            try:
                context.on_response(None)
            finally:
                abortable.finish()
