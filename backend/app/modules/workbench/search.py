"""Bounded Gitee AI web search for the workbench's web_search tool."""

from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import re
import threading
from datetime import datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import urldefrag, urlsplit
from zoneinfo import ZoneInfo

import requests

from .errors import GatewayError
from .transport import AbortableResponse


_BASE = "https://ai.gitee.com/v1"
_TZ = ZoneInfo("Asia/Shanghai")
_FRESHNESS = {"oneDay": timedelta(days=1), "oneWeek": timedelta(days=7), "oneMonth": timedelta(days=30), "noLimit": None}
_MAX_RESPONSE_BYTES = 1024 * 1024
_MAX_RESULTS = 5
_MAX_CANDIDATES = 30
_HTML_TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9:_-]*(?:\s+[^<>]*?)?\s*/?>")
_DASH_PLACEHOLDER = re.compile(r"[-‐‑‒–—―−﹣－]+")
_BLOCK_TAGS = {"article", "blockquote", "div", "h1", "h2", "h3", "h4", "h5", "h6",
               "li", "ol", "p", "section", "ul"}


class _SourceTextParser(HTMLParser):
    """Keep provider search metadata readable without retaining page markup."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.ignored = 0
        self.row = None
        self.cell = None
        self.row_count = 0

    def _append(self, value):
        if self.cell is not None:
            self.cell.append(value)
        elif self.row is not None:
            if value.strip():
                self.row.append(value)
        else:
            self.parts.append(value)

    def _finish_cell(self):
        if self.cell is not None:
            if self.row is None:
                self.row = []
            self.row.append(re.sub(r"\s+", " ", "".join(self.cell)).strip())
            self.cell = None

    def _finish_row(self):
        self._finish_cell()
        if self.row is not None:
            cells = [cell for cell in self.row if cell]
            # Search snippets often contain whole table rows of dash-only
            # placeholders. Keep mixed rows and real zero/negative values.
            if cells and not all(_DASH_PLACEHOLDER.fullmatch(cell.replace(" ", "")) for cell in cells):
                self.parts.append(" ; " if self.row_count else " ")
                self.parts.append(" | ".join(cells))
                self.row_count += 1
            self.row = None

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.ignored += 1
            return
        if self.ignored:
            return
        if tag == "table":
            self._append(" ")
            self.row_count = 0
        elif tag == "tr":
            self._finish_row()
            self.row = []
        elif tag in {"td", "th"}:
            self._finish_cell()
            if self.row is None:
                self.row = []
            self.cell = []
        elif tag == "br" or tag in _BLOCK_TAGS:
            self._append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.ignored = max(0, self.ignored - 1)
            return
        if self.ignored:
            return
        if tag in {"td", "th"}:
            self._finish_cell()
        elif tag == "tr":
            self._finish_row()
        elif tag == "table":
            self._finish_row()
            self.parts.append(" ")
            self.row_count = 0
        elif tag in _BLOCK_TAGS:
            self._append(" ")

    def handle_data(self, data):
        if not self.ignored:
            self._append(data)


def _provider_plain_text(value):
    if not isinstance(value, str):
        return ""
    decoded = value
    # Search providers sometimes return HTML escaped once or twice as text.
    for _ in range(3):
        unescaped = html.unescape(decoded)
        if unescaped == decoded:
            break
        decoded = unescaped
    decoded = re.sub(r"[\u200b-\u200d\ufeff]", "", decoded)
    if _HTML_TAG.search(decoded):
        parser = _SourceTextParser()
        parser.feed(decoded)
        parser.close()
        parser._finish_row()
        decoded = "".join(parser.parts)
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]+", " ", decoded)).strip()


def _provider_base(config):
    value = str(config.get("GITEE_AI_BASE_URL") or _BASE).rstrip("/")
    if value == _BASE:
        return value
    if config.get("TESTING"):
        parts = urlsplit(value)
        if (
            parts.scheme == "http"
            and parts.hostname in {"localhost", "127.0.0.1"}
            and parts.port is not None
            and parts.path == "/v1"
            and not (parts.username or parts.password or parts.query or parts.fragment)
        ):
            return value
    raise ValueError("搜索服务地址必须是固定的 Gitee AI HTTPS 地址。")


def _safe_source_url(value):
    if not isinstance(value, str) or len(value) > 2048 or not value or value != value.strip():
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    try:
        parts = urlsplit(value)
        host = parts.hostname
        # Accessing .port also rejects malformed values such as :abc or :99999.
        port = parts.port
        if parts.scheme not in {"http", "https"} or not host or parts.username or parts.password or port == 0:
            return None
    except ValueError:
        return None
    host = host.rstrip(".").lower()
    if not host or host in {"localhost", "local", "internal"} or host.endswith((".localhost", ".local", ".internal", ".test", ".invalid", ".onion")) or "%" in host:
        return None
    try:
        if not ipaddress.ip_address(host).is_global:
            return None
    except ValueError:
        if "." not in host:
            return None
    return value


def _published_at(value):
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_TZ)
    return parsed.astimezone(_TZ)


def _short_text(value, length):
    return _provider_plain_text(value)[:length]


class GiteeSearch:
    """Execute a single search with strict freshness and safe source metadata."""

    def __init__(self, config):
        self.base = _provider_base(config)
        self.key = str(config.get("GITEE_AI_API_KEY") or "")
        self.connect_timeout = min(max(float(config.get("GITEE_AI_SEARCH_CONNECT_TIMEOUT_SECONDS", 3)), 0.5), 10)
        self.read_timeout = min(max(float(config.get("GITEE_AI_SEARCH_READ_TIMEOUT_SECONDS", 8)), 1), 20)

    @property
    def available(self):
        return bool(self.key)

    def search(self, arguments: dict, stopped: threading.Event, on_response):
        if not isinstance(arguments, dict):
            raise GatewayError("搜索参数无效。", 400)
        query = arguments.get("query")
        freshness = arguments.get("freshness", "noLimit")
        count = arguments.get("count", _MAX_RESULTS)
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 200:
            raise GatewayError("搜索词应为 1 至 200 个字符。", 400)
        query = query.strip()
        if not isinstance(freshness, str) or freshness not in _FRESHNESS:
            raise GatewayError("搜索时间范围无效。", 400)
        if type(count) is not int or count < 1:
            raise GatewayError("搜索结果数量无效。", 400)
        count = min(count, _MAX_RESULTS)
        if not self.available:
            raise GatewayError("联网搜索尚未配置密钥，请检查服务器配置。", 503)
        if stopped.is_set():
            raise GatewayError("已停止生成。", 409)

        searched_at = datetime.now(_TZ)
        try:
            response = requests.post(
                self.base + "/web-search",
                headers={"Authorization": "Bearer " + self.key, "Accept": "application/json", "Content-Type": "application/json"},
                json={"query": query, "count": count, "summary": True, "freshness": freshness},
                stream=True,
                allow_redirects=False,
                timeout=(self.connect_timeout, self.read_timeout),
            )
        except requests.Timeout:
            raise GatewayError("联网搜索超时，请稍后重试。", 504) from None
        except requests.RequestException:
            raise GatewayError("联网搜索连接失败，请稍后重试。", 502) from None

        abortable = AbortableResponse(response)
        try:
            on_response(abortable)
            if stopped.is_set():
                raise GatewayError("已停止生成。", 409)
            if response.status_code in {401, 403}:
                raise GatewayError("联网搜索认证失败，请检查服务器密钥。", 502)
            if response.status_code == 429:
                raise GatewayError("联网搜索请求过于频繁，请稍后重试。", 503)
            if response.status_code != 200:
                raise GatewayError("联网搜索暂时不可用，请稍后重试。", 502)
            if "application/json" not in response.headers.get("Content-Type", "").lower():
                raise GatewayError("联网搜索返回的数据格式无效。", 502)
            raw = bytearray()
            for chunk in response.iter_content(chunk_size=4096):
                if stopped.is_set():
                    raise GatewayError("已停止生成。", 409)
                raw.extend(chunk)
                if len(raw) > _MAX_RESPONSE_BYTES:
                    raise GatewayError("联网搜索返回内容过大。", 502)
            if stopped.is_set():
                raise GatewayError("已停止生成。", 409)
            try:
                body = json.loads(raw)
            except (ValueError, UnicodeError):
                raise GatewayError("联网搜索返回的数据格式无效。", 502) from None
            if not isinstance(body, dict) or body.get("code") != 200:
                raise GatewayError("联网搜索暂时不可用，请稍后重试。", 502)
            data = body.get("data")
            pages = data.get("webPages") if isinstance(data, dict) else None
            values = pages.get("value") if isinstance(pages, dict) else None
            if not isinstance(values, list):
                raise GatewayError("联网搜索返回的数据格式无效。", 502)

            sources, seen = [], set()
            excluded_reasons = {
                "candidate_limit": max(0, len(values) - _MAX_CANDIDATES),
                "invalid_row": 0,
                "invalid_source": 0,
                "duplicate": 0,
                "future_published_at": 0,
                "missing_published_at": 0,
                "outside_freshness": 0,
                "result_limit": 0,
            }
            excluded = excluded_reasons["candidate_limit"]
            for page in values[:_MAX_CANDIDATES]:
                if not isinstance(page, dict):
                    excluded += 1
                    excluded_reasons["invalid_row"] += 1
                    continue
                url = _safe_source_url(page.get("url"))
                title = _short_text(page.get("name"), 240)
                published = _published_at(page.get("datePublished"))
                identity_url = urldefrag(url).url if url else None
                if not url or not title:
                    excluded += 1
                    excluded_reasons["invalid_source"] += 1
                    continue
                if identity_url in seen:
                    excluded += 1
                    excluded_reasons["duplicate"] += 1
                    continue
                if published is not None and published > searched_at + timedelta(minutes=5):
                    excluded += 1
                    excluded_reasons["future_published_at"] += 1
                    continue
                if freshness != "noLimit":
                    if published is None:
                        excluded += 1
                        excluded_reasons["missing_published_at"] += 1
                        continue
                    if published < searched_at - _FRESHNESS[freshness]:
                        excluded += 1
                        excluded_reasons["outside_freshness"] += 1
                        continue
                if len(sources) >= count:
                    excluded += 1
                    excluded_reasons["result_limit"] += 1
                    continue
                seen.add(identity_url)
                sources.append({
                    "id": hashlib.sha256(identity_url.encode("utf-8")).hexdigest()[:16],
                    "title": title,
                    "url": url,
                    "publishedAt": published.isoformat() if published else None,
                    "snippet": _short_text(page.get("summary"), 1000) or _short_text(page.get("snippet"), 1000),
                })
            result = {
                "query": query,
                "freshness": freshness,
                "searchedAt": searched_at.isoformat(),
                "sources": sources,
                "excludedCount": excluded,
                "rawCount": len(values),
                "excludedReasons": excluded_reasons,
            }
            if not sources:
                result["warning"] = "没有找到符合所选时间范围且可核对来源的结果。"
            return result
        except requests.Timeout:
            if stopped.is_set():
                raise GatewayError("已停止生成。", 409) from None
            raise GatewayError("联网搜索超时，请稍后重试。", 504) from None
        except requests.RequestException:
            if stopped.is_set():
                raise GatewayError("已停止生成。", 409) from None
            raise GatewayError("联网搜索连接中断，请稍后重试。", 502) from None
        finally:
            try:
                on_response(None)
            finally:
                abortable.finish()
