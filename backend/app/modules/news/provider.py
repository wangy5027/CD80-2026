"""Normalize public news and fetch it in a bounded, separate Python process."""

from datetime import datetime, timedelta, timezone
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit


SHANGHAI = timezone(timedelta(hours=8))
SOURCES = {
    "eastmoney": {"id": "eastmoney", "name": "东方财富财经快讯",
                  "interface": "stock_info_global_em", "has_article_url": True},
    "cls": {"id": "cls", "name": "财联社电报",
            "interface": "stock_info_global_cls", "has_article_url": False},
}


class FetchError(Exception):
    def __init__(self, code, status, message):
        super().__init__(message)
        self.code = code
        self.status = status


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        elif tag in {"br", "p", "div", "li"}:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        elif tag in {"p", "div", "li"}:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _text(value, limit):
    if value is None or str(value).strip().lower() in {"nan", "nat", "none", "<na>"}:
        return ""
    parser = _PlainText()
    parser.feed(str(value)[:40000])
    parser.close()
    return re.sub(r"\s+", " ", "".join(parser.parts)).strip()[:limit]


def _url(value):
    if not isinstance(value, str) or len(value) > 2048 or re.search(r"[\s\x00-\x1f\\]", value):
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme in {"http", "https"} and parsed.hostname
                and not parsed.username and not parsed.password):
            _ = parsed.port
            return value
    except ValueError:
        pass
    return None


def normalize_records(source, records):
    """Return a deduplicated, newest-first window; never invent missing dates."""
    items = {}
    for row in records[:200]:
        if not isinstance(row, dict):
            continue
        raw_time = str(row.get("发布时间", ""))
        if source == "cls":
            raw_time = str(row.get("发布日期", ""))[:10] + " " + raw_time
        if not re.match(r"^\d{4}-\d{2}-\d{2}[T ]", raw_time):
            continue
        try:
            published = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
            if published.tzinfo is None:
                published = published.replace(tzinfo=SHANGHAI)
            published_at = published.astimezone(SHANGHAI).isoformat(timespec="seconds")
        except (ValueError, OverflowError):
            continue
        title = _text(row.get("标题"), 300)
        summary = _text(row.get("摘要") if source == "eastmoney" else row.get("内容"), 4000)
        generated = not bool(title)
        if not title and not summary:
            continue
        title = title or summary[:80]
        url = _url(row.get("链接")) if source == "eastmoney" else None
        identity = f"{source}|{url or (published_at + '|' + title + '|' + summary)}"
        item_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
        item = {"id": item_id, "source": source, "source_name": SOURCES[source]["name"],
                "title": title, "summary": summary, "published_at": published_at,
                "url": url, "title_generated": generated}
        if item_id not in items or published_at > items[item_id]["published_at"]:
            items[item_id] = item
    return sorted(items.values(), key=lambda item: item["published_at"], reverse=True)


def fetch_source(source, timeout):
    if source not in SOURCES:
        raise FetchError("NEWS_INVALID_SOURCE", 400, "不支持的新闻来源。")
    # This public-data worker does not need model keys, account state or .env.
    allowed = {"PATH", "SYSTEMROOT", "SystemRoot", "WINDIR", "TEMP", "TMP", "TMPDIR",
               "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
               "CURL_CA_BUNDLE", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
               "http_proxy", "https_proxy", "all_proxy", "no_proxy"}
    env = {key: value for key, value in os.environ.items() if key in allowed}
    env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1", TZ="Asia/Shanghai")
    try:
        result = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("worker.py")), source],
            capture_output=True, text=True, encoding="utf-8", timeout=timeout,
            env=env, cwd=str(Path(__file__).parent),
        )
    except subprocess.TimeoutExpired:
        raise FetchError("NEWS_UPSTREAM_TIMEOUT", 504, "新闻来源响应超时，请稍后再试。") from None
    except OSError:
        raise FetchError("NEWS_WORKER_UNAVAILABLE", 503, "新闻采集进程暂时不可用。") from None
    try:
        payload = json.loads(result.stdout)
    except (ValueError, TypeError):
        payload = None
    if isinstance(payload, dict) and payload.get("error") == "dependency_missing":
        raise FetchError("NEWS_DEPENDENCY_MISSING", 503, "新闻依赖未安装，请按网站文档安装 Python 依赖后重启。")
    if result.returncode or not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise FetchError("NEWS_UPSTREAM_UNAVAILABLE", 502, "暂时无法获取该来源的新闻，请稍后再试。")
    if not payload["items"]:
        raise FetchError("NEWS_EMPTY_RESPONSE", 502, "新闻来源暂未返回有效新闻，请稍后再试。")
    return payload["items"]
