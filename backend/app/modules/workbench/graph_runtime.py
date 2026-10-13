"""Durable LangGraph chat pipeline and bounded Gitee AI streaming client."""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import threading
from pathlib import Path
from typing import TypedDict
from urllib.parse import urlsplit

import requests
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from urllib3.exceptions import ReadTimeoutError

from .errors import GatewayError


os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
DEFAULT_BASE_URL = "https://ai.gitee.com/v1"
DEFAULT_MODEL = "GLM-5.3"


class ChatState(TypedDict, total=False):
    prompt: str
    history: list[dict[str, str]]
    query: str
    model: str
    messages: list[dict[str, str]]
    answer: str
    final_message: dict[str, str]


def validate_provider_url(value: str, testing: bool = False) -> str:
    """Pin production credentials to Gitee AI; permit loopback only in tests."""
    parsed = urlsplit(str(value).rstrip("/"))
    production = parsed.scheme == "https" and parsed.hostname == "ai.gitee.com" and parsed.port is None
    local_test = testing and parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"} and parsed.port is not None
    if not (production or local_test) or parsed.path != "/v1" or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("模型服务地址必须是固定的 Gitee AI HTTPS 地址。")
    return parsed.geturl().rstrip("/")


def _safe_http_error(status: int) -> GatewayError:
    if status in {401, 403}:
        return GatewayError("模型服务认证失败，请检查服务器上的密钥配置。", 502)
    if status == 429:
        return GatewayError("模型服务请求过于频繁，请稍后重试。", 503)
    if status in {400, 404, 422}:
        return GatewayError("模型请求被拒绝，请检查模型名称和输入。", 502)
    return GatewayError("模型服务暂时不可用，请稍后重试。", 502)


def _sse_events(response: requests.Response, stopped: threading.Event):
    """Parse OpenAI-compatible SSE without exposing raw upstream error text."""
    event = "message"
    data = []
    # One-byte reads release short tokens from a long-lived SSE connection.
    for raw in response.iter_lines(chunk_size=1):
        if stopped.is_set():
            raise GatewayError("已停止生成。", 409)
        line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
        if not line:
            if data:
                yield event, "\n".join(data)
            event, data = "message", []
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        yield event, "\n".join(data)


class AbortableResponse:
    """Wake a blocked requests stream without blocking the /stop request."""

    def __init__(self, response: requests.Response):
        self.response = response
        self._lock = threading.Lock()
        self._aborted = False

    def _socket(self):
        raw = getattr(self.response, "raw", None)
        connection = getattr(raw, "_connection", None)
        direct = getattr(connection, "sock", None)
        if direct is not None:
            return direct
        fp = getattr(raw, "_fp", None)
        buffered = getattr(fp, "fp", None)
        stream = getattr(buffered, "raw", None)
        return getattr(stream, "_sock", None)

    def abort(self):
        """Shutdown the socket first; perform potentially blocking close in a daemon."""
        with self._lock:
            if self._aborted:
                return
            self._aborted = True
        raw = getattr(self.response, "raw", None)
        try:
            # urllib3 retains the socket shutdown callback before http.client
            # transfers ownership; its public method also handles wrapped sockets.
            raw.shutdown()
        except (OSError, AttributeError, ValueError, RuntimeError):
            try:
                connection = self._socket()
                if connection is not None:
                    connection.shutdown(socket.SHUT_RDWR)
            except (OSError, AttributeError, ValueError):
                pass
        if os.name == "nt":
            # On Windows shutdown does not reliably wake a timeout-mode read.
            # http.client's makefile keeps _io_refs alive, so socket.close()
            # merely marks the Python wrapper closed. Detach invalidates that
            # wrapper before closing its native handle, preventing a later
            # SocketIO cleanup from closing a recycled handle.
            connection = self._socket()
            if connection is not None:
                try:
                    descriptor = connection.detach()
                    if descriptor != -1:
                        socket.close(descriptor)
                except (OSError, AttributeError, ValueError):
                    pass
        threading.Thread(target=self.response.close, name="model-response-close", daemon=True).start()


class GiteeProvider:
    """Text-only OpenAI-compatible model transport with no redirect or secret echo."""

    def __init__(self, config):
        self.base = validate_provider_url(config.get("GITEE_AI_BASE_URL") or DEFAULT_BASE_URL, bool(config.get("TESTING")))
        self.model = str(config.get("GITEE_AI_MODEL") or DEFAULT_MODEL)
        self.key = str(config.get("GITEE_AI_API_KEY") or "")
        self.connect_timeout = float(config.get("GITEE_AI_CONNECT_TIMEOUT_SECONDS", 5))
        self.read_timeout = float(config.get("GITEE_AI_READ_TIMEOUT_SECONDS", config.get("CHAT_IDLE_TIMEOUT_SECONDS", 120)))
        self.request_options = {}
        if config.get("GITEE_AI_MAX_TOKENS"):
            self.request_options["max_tokens"] = int(config["GITEE_AI_MAX_TOKENS"])
        if config.get("GITEE_AI_REASONING_EFFORT"):
            self.request_options["reasoning_effort"] = str(config["GITEE_AI_REASONING_EFFORT"])

    def json_tokens(self, messages, model, stopped, on_response):
        yield from self.tokens(messages, model, stopped, on_response,
                               response_format={"type": "json_object"})

    def tokens(self, messages, model, stopped, on_response, *, response_format=None):
        if not self.key:
            raise GatewayError("模型服务尚未配置密钥，请在服务器 .env 中设置 GITEE_AI_API_KEY。", 503)
        if stopped.is_set():
            raise GatewayError("已停止生成。", 409)
        try:
            response = requests.post(
                self.base + "/chat/completions",
                headers={"Authorization": "Bearer " + self.key, "Accept": "text/event-stream", "Content-Type": "application/json"},
                json={"model": model, "messages": messages, "stream": True, **self.request_options,
                      **({"response_format": response_format} if response_format is not None else {})},
                stream=True,
                allow_redirects=False,
                timeout=(self.connect_timeout, self.read_timeout),
            )
        except requests.Timeout:
            raise GatewayError("模型连接或响应超时，请稍后重试。", 504) from None
        except requests.RequestException:
            raise GatewayError("模型服务连接失败，请稍后重试。", 502) from None

        abortable = AbortableResponse(response)
        try:
            on_response(abortable)
            if response.status_code != 200:
                raise _safe_http_error(response.status_code)
            if "text/event-stream" not in response.headers.get("Content-Type", ""):
                raise GatewayError("模型服务没有返回聊天流。", 502)
            finished = False
            for event, data in _sse_events(response, stopped):
                if stopped.is_set():
                    raise GatewayError("已停止生成。", 409)
                if data.strip() == "[DONE]":
                    finished = True
                    break
                if event == "error":
                    raise GatewayError("模型服务返回错误，请检查服务端模型配置。", 502)
                try:
                    chunk = json.loads(data)
                except (ValueError, TypeError):
                    raise GatewayError("模型服务返回无效的数据流。", 502) from None
                if isinstance(chunk, dict) and chunk.get("error"):
                    raise GatewayError("模型服务返回错误，请检查服务端模型配置。", 502)
                choices = chunk.get("choices") if isinstance(chunk, dict) else None
                if not isinstance(choices, list) or not choices:
                    continue
                choice = choices[0] if isinstance(choices[0], dict) else {}
                if choice.get("finish_reason") == "length":
                    raise GatewayError("模型输出达到令牌上限而被截断，请缩小需求或调整生成预算。", 502, "model_output_truncated")
                if choice.get("finish_reason") is not None:
                    finished = True
                delta = choice.get("delta") or {}
                content = delta.get("content") if isinstance(delta, dict) else None
                if isinstance(content, str) and content:
                    yield content
                elif isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and isinstance(item.get("text"), str) and item["text"]:
                            yield item["text"]
            if stopped.is_set():
                raise GatewayError("已停止生成。", 409)
            if not finished:
                raise GatewayError("模型连接提前结束，已保留部分回复。", 502)
        except requests.Timeout:
            raise GatewayError("模型长时间没有返回新内容，请稍后重试。", 504) from None
        except requests.ConnectionError as error:
            if stopped.is_set():
                raise GatewayError("已停止生成。", 409) from None
            if any(isinstance(item, ReadTimeoutError) for item in error.args):
                raise GatewayError("模型长时间没有返回新内容，请稍后重试。", 504) from None
            raise GatewayError("模型连接提前结束，已保留部分回复。", 502) from None
        except requests.RequestException:
            raise GatewayError("模型连接提前结束，已保留部分回复。", 502) from None
        finally:
            on_response(None)
            response.close()


class GraphRuntime:
    """Prepare context, stream the model, and finalize output in a real StateGraph."""

    def __init__(self, config):
        self.provider = GiteeProvider(config)
        data_dir = Path(config["DATA_DIR"])
        data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        data_dir.chmod(0o700)
        database = data_dir / "graph-checkpoints.sqlite3"
        self._secure_sqlite_file(database, create=True)
        for suffix in ("-wal", "-shm", "-journal"):
            self._secure_sqlite_file(Path(str(database) + suffix), create=False)
        self.connection = sqlite3.connect(database, check_same_thread=False)
        self.checkpointer = SqliteSaver(self.connection)
        self._contexts = {}
        self._lock = threading.RLock()
        self._running_nodes = 0
        self._close_requested = False
        self._closed = False
        builder = StateGraph(ChatState)
        builder.add_node("prepare_context", self._prepare_context)
        builder.add_node("stream_model", self._stream_model)
        builder.add_node("finalize_message", self._finalize_message)
        builder.add_edge(START, "prepare_context")
        builder.add_edge("prepare_context", "stream_model")
        builder.add_edge("stream_model", "finalize_message")
        builder.add_edge("finalize_message", END)
        self.graph = builder.compile(checkpointer=self.checkpointer)

    @staticmethod
    def _secure_sqlite_file(path: Path, *, create: bool):
        """Apply POSIX private modes; Windows chmod does not replace account ACLs."""
        flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        if create:
            flags |= os.O_CREAT
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileNotFoundError:
            if create:
                raise
            return
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
            else:
                # Windows supports only the read-only flag, not POSIX owner modes.
                # Keep database writes available; access isolation relies on its ACL.
                os.chmod(path, 0o600)
        finally:
            os.close(descriptor)

    @staticmethod
    def thread_id(user_id, task_id):
        """Keep graph checkpoints isolated by both account and task."""
        return f"account:{user_id}:task:{task_id}"

    @staticmethod
    def _prepare_context(state: ChatState):
        messages = [{"role": "system", "content": state["prompt"]}]
        for item in state["history"]:
            if item.get("role") in {"user", "assistant"} and isinstance(item.get("content"), str) and item["content"]:
                messages.append({"role": item["role"], "content": item["content"]})
        messages.append({"role": "user", "content": state["query"]})
        return {"messages": messages}

    def _stream_model(self, state: ChatState, config):
        writer = get_stream_writer()
        with self._lock:
            context = self._contexts.get(config["configurable"]["thread_id"])
            if context is None:
                raise GatewayError("已停止生成。", 409)
            stopped, on_response = context
            self._running_nodes += 1
        try:
            answer = ""
            for part in self.provider.tokens(state["messages"], state["model"], stopped, on_response):
                answer += part
                writer({"type": "token", "text": part})
            if not answer.strip():
                raise GatewayError("模型没有返回文本回复。", 502)
            return {"answer": answer}
        finally:
            with self._lock:
                self._running_nodes -= 1
                self._close_if_idle()

    @staticmethod
    def _finalize_message(state: ChatState):
        return {"final_message": {"role": "assistant", "content": state["answer"]}}

    def stream(self, user_id, task_id, history, query, prompt, model, stopped, on_response):
        thread_id = self.thread_id(user_id, task_id)
        config = {"configurable": {"thread_id": thread_id}}
        inputs = {"prompt": prompt, "history": history, "query": query, "model": model, "messages": [], "answer": "", "final_message": {}}
        with self._lock:
            if self._close_requested:
                raise GatewayError("服务正在关闭，请稍后重试。", 503)
            self._contexts[thread_id] = (stopped, on_response)
        graph_stream = None
        try:
            graph_stream = self.graph.stream(inputs, config, stream_mode="custom")
            for item in graph_stream:
                if isinstance(item, dict) and item.get("type") == "token":
                    yield item
            snapshot = self.graph.get_state(config)
            message = snapshot.values.get("final_message") if snapshot else None
            if snapshot is None or snapshot.next or not message or not message.get("content"):
                raise GatewayError("模型输出未能完成，请稍后重试。", 502)
            yield {"type": "complete", "message": message}
        finally:
            try:
                if graph_stream is not None:
                    graph_stream.close()
            finally:
                with self._lock:
                    self._contexts.pop(thread_id, None)
                    self._close_if_idle()

    def forget(self, user_id, task_id):
        self.checkpointer.delete_thread(self.thread_id(user_id, task_id))

    def _close_if_idle(self):
        """Caller holds the lock; never close while a graph is writing."""
        if self._close_requested and not self._contexts and self._running_nodes == 0 and not self._closed:
            self.connection.close()
            self._closed = True

    def close(self):
        """Idempotently close SQLite now or after the last active graph exits."""
        with self._lock:
            self._close_requested = True
            self._close_if_idle()
