"""Bound API request bodies before JSON parsing, including streamed bodies."""

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi.responses import JSONResponse


MAX_JSON_BODY_BYTES = 2 * 1024 * 1024


class JsonBodyLimitMiddleware:
    def __init__(self, app: Callable[..., Awaitable[Any]]) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive, send) -> None:
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH", "DELETE"}:
            await self.app(scope, receive, send)
            return

        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                try:
                    if int(value) > MAX_JSON_BODY_BYTES:
                        await self._reject(scope, receive, send)
                        return
                except ValueError:
                    pass  # Streaming still enforces the exact byte limit.

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            body.extend(message.get("body", b""))
            if len(body) > MAX_JSON_BODY_BYTES:
                await self._reject(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        buffered_body = bytes(body)
        replayed = False

        async def replay_receive():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": buffered_body, "more_body": False}
            return await receive()

        await self.app(scope, replay_receive, send)

    @staticmethod
    async def _reject(scope: dict[str, Any], receive, send) -> None:
        response = JSONResponse(
            status_code=413,
            content={"detail": "请求体超过 2 MiB 限制"},
        )
        await response(scope, receive, send)
