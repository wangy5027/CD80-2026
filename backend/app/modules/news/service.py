"""Per-app, per-source cache with single-flight refresh and bounded fallback."""

from datetime import datetime, timedelta
import math
import threading
import time

from ..workbench.errors import GatewayError
from .provider import FetchError, SHANGHAI, SOURCES, fetch_source


class NewsService:
    def __init__(self, config, fetcher=None, clock=None, now=None):
        self.fetcher = fetcher or fetch_source
        self.clock = clock or time.monotonic
        self.now = now or (lambda: datetime.now(SHANGHAI))
        self.ttl = float(config.get("NEWS_CACHE_TTL_SECONDS", 60))
        self.timeout = float(config.get("NEWS_FETCH_TIMEOUT_SECONDS", 15))
        self.max_stale = float(config.get("NEWS_MAX_STALE_SECONDS", 900))
        self.retry = float(config.get("NEWS_RETRY_SECONDS", 30))
        if (any(not math.isfinite(v) or v <= 0 for v in (self.ttl, self.timeout, self.max_stale, self.retry))
                or self.max_stale < self.ttl):
            raise ValueError("NEWS 配置必须为正数，NEWS_MAX_STALE_SECONDS 不能小于缓存有效期。")
        self._states = {source: {"lock": threading.Lock(), "items": None,
                                 "retry_at": 0, "error": None} for source in SOURCES}

    def get_sources(self):
        return {"provider": "akshare", "default_source": "eastmoney",
                "sources": [dict(value) for value in SOURCES.values()],
                "refresh": {"mode": "on_request", "ttl_seconds": self.ttl,
                            "timeout_seconds": self.timeout, "max_stale_seconds": self.max_stale,
                            "retry_seconds": self.retry}}

    def get_news(self, source="eastmoney", limit=20, offset=0, q=""):
        state = self._states[source]
        with state["lock"]:
            tick = self.clock()
            hit = True
            fresh = state["items"] is not None and tick - state["fetched_tick"] < self.ttl
            if not fresh and tick >= state["retry_at"]:
                try:
                    items = self.fetcher(source, self.timeout)
                    if not items:
                        raise FetchError("NEWS_EMPTY_RESPONSE", 502, "新闻来源暂未返回有效新闻，请稍后再试。")
                    state.update(items=items, fetched_tick=self.clock(), fetched_at=self.now().astimezone(SHANGHAI),
                                 retry_at=0, error=None)
                    hit = False
                except FetchError as error:
                    state.update(retry_at=self.clock() + self.retry, error=error)
            tick = self.clock()
            age = max(0, tick - state["fetched_tick"]) if state["items"] is not None else None
            if age is None or age > self.max_stale:
                error = state["error"]
                if error is None:
                    error = FetchError("NEWS_UPSTREAM_UNAVAILABLE", 502, "暂时无法获取新闻。")
                failure = GatewayError(str(error), error.status)
                failure.code = error.code
                raise failure
            stale = age >= self.ttl
            items = state["items"]
            search = q.casefold()
            matched = [item for item in items if not search or search in
                       (item["title"] + " " + item["summary"]).casefold()]
            error = state["error"]
            return {"provider": "akshare", "source": source, "source_name": SOURCES[source]["name"],
                    "items": [dict(item) for item in matched[offset:offset + limit]],
                    "pagination": {"limit": limit, "offset": offset, "total": len(matched),
                                   "has_more": offset + limit < len(matched)},
                    "latest_published_at": items[0]["published_at"],
                    "cache": {"hit": hit, "stale": stale,
                              "fetched_at": state["fetched_at"].isoformat(timespec="seconds"),
                              "expires_at": (state["fetched_at"] + timedelta(seconds=self.ttl)).isoformat(timespec="seconds"),
                              "age_seconds": round(age, 2), "ttl_seconds": self.ttl,
                              "refresh_retry_after_seconds": round(max(0, state["retry_at"] - tick), 2)},
                    "warning": {"code": error.code, "message": str(error)} if stale and error else None}
