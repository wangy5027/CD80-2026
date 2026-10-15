"""Keep Explore's diagnostics while using the platform's budgeted transport."""

from __future__ import annotations

import time

from ..workbench.errors import GatewayError
from ..workbench.graph_runtime import GiteeProvider


class ExploreGiteeProvider(GiteeProvider):
    """A per-Explore adapter; all HTTP requests still use the platform provider."""

    def __init__(self, config):
        super().__init__(config)
        self.on_diagnostic = None

    def events(self, messages, model, stopped, on_response, *, tools=None,
               tool_choice=None, response_format=None, on_trace=None):
        started = time.monotonic()
        observer = self.on_diagnostic
        request_bytes = None
        try:
            import json
            request_bytes = len(json.dumps(messages, ensure_ascii=False).encode("utf-8"))
        except (TypeError, ValueError, UnicodeError):
            pass
        detail = {
            "status": "request_waiting", "phase": "headers", "model": str(model),
            "request_bytes": request_bytes,
            "message_count": len(messages) if isinstance(messages, list) else None,
            "tool_names": [item["function"]["name"] for item in (tools or [])
                           if isinstance(item, dict) and isinstance(item.get("function"), dict)
                           and isinstance(item["function"].get("name"), str)],
            "max_tokens": self.request_options.get("max_tokens"),
            "reasoning_effort": self.request_options.get("reasoning_effort"),
            "timeout": {"connect_seconds": self.connect_timeout,
                        "read_seconds": self.read_timeout},
        }

        def notify(**fields):
            if callable(observer):
                try:
                    observer({**detail, **fields,
                              "elapsed_seconds": round(time.monotonic() - started, 3)})
                except Exception:
                    # A diagnostic write must not change an active model turn.
                    pass

        def trace(kind, metadata):
            if callable(on_trace):
                on_trace(kind, metadata)
            if kind != "transport" or not isinstance(metadata, dict):
                return
            # Do not persist messages, prompt text, tool arguments or token data.
            safe = {key: metadata[key] for key in ("httpStatus", "requestId",
                    "responseId", "finishReason") if key in metadata}
            notify(status="transport", phase="stream", **safe)

        notify()
        try:
            yield from super().events(messages, model, stopped, on_response,
                                      tools=tools, tool_choice=tool_choice,
                                      response_format=response_format, on_trace=trace)
        except GatewayError as error:
            code = error.code
            message = str(error)
            if not code and error.status == 504:
                code = "model_request_timeout"
            elif not code and error.status == 502 and "连接提前结束" in message:
                code = "model_stream_interrupted"
            elif not code and error.status == 502 and "连接失败" in message:
                code = "model_connection_failed"
            notify(status="error", phase="stream", error_code=code,
                   exception_type=type(error).__name__)
            if code != error.code:
                raise GatewayError(message, error.status, code) from None
            raise
        else:
            notify(status="completed", phase="stream")
