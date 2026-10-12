"""Workbench business operations and chat orchestration."""

import hashlib
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import requests
from urllib3.exceptions import ReadTimeoutError

from .coze import CozeClient, GatewayError, parse_coze_json, parse_sse
from .store import StateStore


BUILTINS = [
    {"id": "assistant", "name": "Ficagent 助手", "description": "写作、分析与日常工作", "color": "#ef92aa", "prompt": "你是Ficagent的通用助手。用简洁清晰的中文帮助用户处理写作、资料分析和工作问题。只根据实际提供的工具和资料回答；没有执行过的动作不得声称已经完成。"},
    {"id": "coder", "name": "编程专家", "description": "代码、调试与技术方案", "color": "#c6dcc9", "prompt": "你是Ficagent的编程助手。帮助用户理解、编写和调试代码，提供可运行的方案。未实际运行的代码须明确说明。不要声称已操作本机文件或终端；只有真实工具返回才能确认执行结果。"},
    {"id": "director", "name": "视频导演", "description": "分镜、脚本与创意策划", "color": "#cab2e8", "prompt": "你是Ficagent的视频策划助手。帮助用户编写视频脚本、分镜、镜头说明和生成提示词。没有真实视频生成工具时，只提供策划文本，不声称已生成或导出视频。"},
]
TEXT_EXT = {".txt", ".md", ".json", ".csv", ".tsv", ".log", ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".html", ".css", ".yaml", ".yml", ".xml", ".sql", ".sh", ".c", ".cpp", ".java", ".rs"}
INLINE_EXT = {".html", ".css", ".js", ".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".txt", ".md", ".json", ".csv", ".pdf"}
MIME = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp", ".txt": "text/plain; charset=utf-8", ".md": "text/plain; charset=utf-8", ".json": "application/json; charset=utf-8", ".csv": "text/plain; charset=utf-8", ".pdf": "application/pdf"}


def now_iso():
    """Return a UTC timestamp compatible with the existing JSON state."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def clean_name(value):
    """Normalize a user-visible short name."""
    return str(value or "").strip()[:120]


def public_task(task):
    """Remove private Coze identifiers before returning a task to the UI."""
    return {key: value for key, value in task.items() if key not in {"botId", "conversationId", "botSpaceId", "configuredModelId"}}


def rewrite_cookie(cookie):
    """Scope an upstream session cookie to this local HTTP origin."""
    cookie = re.sub(r";\s*Domain=[^;]+", "", cookie, flags=re.I)
    cookie = re.sub(r";\s*SameSite=[^;]+", "", cookie, flags=re.I)
    return cookie + "; SameSite=Lax"


class WorkbenchService:
    def __init__(self, config):
        self.coze = CozeClient(config["COZE_BASE_URL"])
        self.store = StateStore(config["DATA_DIR"])
        self.chat_idle_timeout = float(config.get("CHAT_IDLE_TIMEOUT_SECONDS", 120))
        self.sessions = {}
        self.active = {}
        self._lock = threading.RLock()
        self._file_locks = {}

    def close(self):
        """Stop ongoing generation and close upstream response streams."""
        with self._lock:
            active = list(self.active.values())
        for run in active:
            run["event"].set()
            if run.get("response") is not None:
                run["response"].close()

    def _check_cancel(self, run):
        if run["event"].is_set():
            raise GatewayError("已停止生成。", 409)

    def _file_lock(self, user_id, file_id):
        key = (str(user_id), str(file_id))
        with self._lock:
            return self._file_locks.setdefault(key, threading.RLock())

    def authenticate(self, cookie, refresh=False):
        """Resolve the upstream user, with a short per-cookie cache."""
        key = hashlib.sha256(cookie.encode()).hexdigest()
        with self._lock:
            cached = self.sessions.get(key)
        if not refresh and cached and cached["until"] > time.monotonic():
            return {**cached, "cookie": cookie, "key": key}
        user = self.coze.user(cookie)
        if not user:
            raise GatewayError("请先登录本地 Coze 账号。", 401)
        item = {"user": user, "until": time.monotonic() + 10}
        with self._lock:
            self.sessions[key] = item
        return {**item, "cookie": cookie, "key": key}

    def catalog(self, auth):
        """Combine available upstream agents with the workbench built-ins."""
        result = self.coze.catalog(auth["cookie"])
        state = self.store.get(auth["user"]["id"])
        internal = {task.get("botId") for task in state["tasks"] if task.get("botId")}
        builtins = [{**{key: value for key, value in agent.items() if key != "prompt"}, "builtin": True} for agent in BUILTINS]
        return {**result, "agents": builtins + [agent for agent in result["agents"] if agent["id"] not in internal]}

    def login(self, kind, body):
        """Authenticate or register against Coze and return rewritten cookies."""
        email = str(body.get("email") or "").strip()
        password = str(body.get("password") or "")
        if not email or not password:
            raise GatewayError("请输入邮箱和密码。", 400)
        route = "/api/passport/web/email/login/" if kind == "login" else "/api/passport/web/email/register/v2/"
        result = self.coze.request(route, {"email": email, "password": password})
        cookies = result["cookies"]
        warning = None
        if kind == "register" and clean_name(body.get("name")):
            cookie = "; ".join(item.split(";", 1)[0] for item in cookies)
            try:
                self.coze.json("/api/user/update_profile", {"name": clean_name(body.get("name"))}, cookie)
            except GatewayError:
                warning = "账号已创建，昵称可稍后在 Coze 工作台修改。"
        return {"ok": True, "user": result["result"].get("data"), **({"warning": warning} if warning else {})}, [rewrite_cookie(item) for item in cookies]

    def session(self, cookie):
        """Return a safe unauthenticated result when Coze cannot validate login."""
        try:
            auth = self.authenticate(cookie, refresh=True)
            try:
                resources = self.catalog(auth)
            except GatewayError as error:
                if error.status == 401:
                    raise
                resources = {"models": [], "agents": [{key: value for key, value in item.items() if key != "prompt"} for item in BUILTINS], "activeSpaceId": "", "warnings": [str(error)]}
            return {"authenticated": True, "user": auth["user"], **resources}
        except GatewayError as error:
            return {"authenticated": False, "backendUnavailable": error.status >= 500, **({"error": str(error)} if error.status >= 500 else {})}

    def logout(self, cookie):
        """Clear local session immediately, even when upstream logout fails."""
        key = hashlib.sha256(cookie.encode()).hexdigest()
        with self._lock:
            self.sessions.pop(key, None)
        warning = None
        try:
            if cookie:
                self.coze.request("/api/passport/web/logout/", {}, cookie=cookie, method="GET")
        except GatewayError:
            warning = "已清除本机登录状态；Coze 暂不可用，服务端会话可能尚未撤销。"
        return {"ok": True, **({"warning": warning} if warning else {})}

    def state(self, user_id):
        """Return all current account state with private bindings hidden."""
        state = self.store.get(user_id)
        state.pop("bindings", None)
        state["tasks"] = sorted((public_task(task) for task in state["tasks"]), key=lambda task: task.get("updatedAt", ""), reverse=True)
        return state

    def stop(self, user_id, task_id):
        """Cancel only a task active for the authenticated account."""
        with self._lock:
            run = self.active.get(task_id)
        if run and run["userId"] == user_id:
            run["event"].set()
            if run.get("response") is not None:
                run["response"].close()
        return {"ok": True}

    def create_project(self, user_id, body):
        name = clean_name(body.get("name"))
        if not name:
            raise GatewayError("请输入项目名称。", 400)
        project = {"id": str(uuid4()), "name": name, "createdAt": now_iso()}
        self.store.update(user_id, lambda state: state["projects"].insert(0, project))
        return project

    def delete_project(self, user_id, project_id):
        def change(state):
            state["projects"] = [item for item in state["projects"] if item["id"] != project_id]
            for task in state["tasks"]:
                if task.get("projectId") == project_id:
                    task["projectId"] = ""
            for item in state["files"]:
                if item.get("projectId") == project_id:
                    item["projectId"] = ""
        self.store.update(user_id, change)
        return {"ok": True}

    def create_schedule(self, user_id, body):
        title = clean_name(body.get("title"))
        try:
            at = datetime.fromisoformat(str(body.get("at") or "").replace("Z", "+00:00"))
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
            at = at.astimezone(timezone.utc)
        except ValueError:
            at = None
        if not title or at is None:
            raise GatewayError("请填写日程标题和有效时间。", 400)
        schedule = {"id": str(uuid4()), "title": title, "at": at.isoformat(timespec="milliseconds").replace("+00:00", "Z"), "done": False}
        self.store.update(user_id, lambda state: state["schedules"].insert(0, schedule))
        return schedule

    def change_schedule(self, user_id, schedule_id, method, body):
        def change(state):
            item = next((value for value in state["schedules"] if value["id"] == schedule_id), None)
            if item is None:
                raise GatewayError("日程不存在。", 404)
            if method == "DELETE":
                state["schedules"] = [value for value in state["schedules"] if value["id"] != schedule_id]
            else:
                item["done"] = bool(body.get("done"))
        self.store.update(user_id, change)
        return {"ok": True}

    def upload_file(self, user_id, upload, project_id):
        """Save one uploaded file under the authenticated account."""
        if upload is None:
            raise GatewayError("请选择文件。", 400)
        state = self.store.get(user_id)
        if project_id and not any(item["id"] == project_id for item in state["projects"]):
            raise GatewayError("项目不存在。", 404)
        data = upload.read(30 * 1024 * 1024 + 1)
        if len(data) > 30 * 1024 * 1024:
            raise GatewayError("单个文件不得超过 30 MB。", 413)
        file_id = str(uuid4())
        name = Path(upload.filename or "").name.replace("\r", "").replace("\n", "").replace("\x00", "")[:180]
        metadata = {"id": file_id, "name": name, "size": len(data), "type": upload.content_type or "application/octet-stream", "projectId": project_id, "createdAt": now_iso()}
        target = self.store.directory(user_id) / "files" / file_id
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
            self.store.update(user_id, lambda current: current["files"].insert(0, metadata))
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return metadata

    def open_file(self, user_id, file_id):
        """Open a validated file and hold its lock until the HTTP body finishes."""
        lock = self._file_lock(user_id, file_id)
        lock.acquire()
        try:
            metadata = next((item for item in self.store.get(user_id)["files"] if item["id"] == file_id), None)
            if metadata is None:
                raise GatewayError("文件不存在。", 404)
            try:
                stream = open(self.store.directory(user_id) / "files" / file_id, "rb")
            except FileNotFoundError:
                raise GatewayError("文件内容不存在，请重新上传。", 404) from None
            if not os.path.isfile(stream.name):
                stream.close()
                raise GatewayError("文件内容不可读取。", 404)
            return stream, metadata, lock
        except BaseException:
            lock.release()
            raise

    def delete_file(self, user_id, file_id):
        with self._file_lock(user_id, file_id):
            state = self.store.get(user_id)
            if not any(item["id"] == file_id for item in state["files"]):
                raise GatewayError("文件不存在。", 404)
            target = self.store.directory(user_id) / "files" / file_id
            try:
                target.unlink(missing_ok=True)
            except OSError:
                raise GatewayError("删除文件失败，记录已保留，请检查文件权限后重试。", 500) from None
            self.store.update(user_id, lambda current: current.__setitem__("files", [item for item in current["files"] if item["id"] != file_id]))
        return {"ok": True}

    def file_headers(self, metadata, download=False):
        """Return the existing safe content type and disposition contract."""
        extension = Path(metadata["name"]).suffix.lower()
        content_type = MIME.get(extension, "application/octet-stream")
        disposition = "attachment" if download or extension not in INLINE_EXT else "inline"
        return {"Content-Type": content_type, "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(metadata['name'])}", "Content-Security-Policy": "sandbox; default-src 'none'; img-src data:", "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"}

    def get_task(self, user_id, task_id):
        task = next((item for item in self.store.get(user_id)["tasks"] if item["id"] == task_id), None)
        if task is None:
            raise GatewayError("任务不存在。", 404)
        return public_task(task)

    def delete_task(self, auth, task_id):
        user_id = auth["user"]["id"]
        task = next((item for item in self.store.get(user_id)["tasks"] if item["id"] == task_id), None)
        if task is None:
            raise GatewayError("任务不存在。", 404)
        with self._lock:
            if task_id in self.active:
                raise GatewayError("该任务正在处理，请稍后重试。", 409)
            self.active[task_id] = {"event": threading.Event(), "userId": user_id, "deleting": True}
        try:
            task = next((item for item in self.store.get(user_id)["tasks"] if item["id"] == task_id), None)
            if task is None:
                raise GatewayError("任务不存在。", 404)
            if task.get("botId") and task.get("botSpaceId"):
                self.coze.json("/api/draftbot/delete", {"space_id": task["botSpaceId"], "bot_id": task["botId"]}, auth["cookie"])
            self.store.update(user_id, lambda state: state.__setitem__("tasks", [item for item in state["tasks"] if item["id"] != task_id]))
            return {"ok": True}
        finally:
            with self._lock:
                self.active.pop(task_id, None)

    def _prepare_bot(self, task, auth, model_id, available, run):
        """Create or update the dedicated Coze bot for one task."""
        self._check_cancel(run)
        if task.get("botId") and task.get("configuredModelId") == model_id:
            return task["botId"]
        builtin = next((item for item in BUILTINS if item["id"] == task["agentId"]), None)
        space_id = available["activeSpaceId"]
        if not space_id:
            raise GatewayError("还没有可用空间，请先在 Coze 工作台创建空间。", 400)
        bot_id = task.get("botId")
        if not bot_id and builtin:
            icons = self.coze.json("/api/developer/get_icon", {"icon_type": 1}, auth["cookie"])
            self._check_cancel(run)
            icon_uri = next((item.get("uri") for item in icons.get("data", {}).get("icon_list", []) if isinstance(item.get("uri"), str) and item["uri"].strip()), None)
            if not icon_uri:
                raise GatewayError("Coze 未返回可用的智能体头像，请检查 Coze 的默认资源配置后重试。", 502)
            created = self.coze.json("/api/draftbot/create", {"space_id": space_id, "name": f"Ficagent 任务 · {task['title']}"[:60], "description": builtin["description"], "icon_uri": icon_uri}, auth["cookie"])
            bot_id = str(created.get("data", {}).get("bot_id") or "")
        elif not bot_id:
            source = next((item for item in available["agents"] if item["id"] == task["agentId"] and not item.get("builtin")), None)
            if source is None:
                raise GatewayError("源智能体已不可用，请选择其他智能体。", 404)
            duplicated = self.coze.json("/api/draftbot/duplicate", {"space_id": space_id, "bot_id": source["id"]}, auth["cookie"])
            bot_id = str(duplicated.get("data", {}).get("bot_id") or "")
        if not bot_id:
            raise GatewayError("Coze 没有返回智能体 ID，请检查模型配置。", 502)

        def save_bot(state):
            current = next((item for item in state["tasks"] if item["id"] == task["id"]), None)
            if current:
                current["botId"] = bot_id
                current["botSpaceId"] = space_id
        self.store.update(auth["user"]["id"], save_bot)
        task["botId"] = bot_id
        self._check_cancel(run)
        draft = self.coze.json("/api/playground_api/draftbot/get_draft_bot_info", {"bot_id": bot_id}, auth["cookie"])
        self._check_cancel(run)
        previous_model = draft.get("data", {}).get("bot_info", {}).get("model_info") or {}
        bot_info = {"bot_id": bot_id, "name": f"Ficagent 任务 · {task['title']}"[:60]}
        if builtin:
            bot_info["prompt_info"] = {"prompt": builtin["prompt"]}
        if model_id:
            bot_info["model_info"] = {**previous_model, "model_id": str(model_id)}
        self.coze.json("/api/playground_api/draftbot/update_draft_bot_info", {"bot_info": bot_info}, auth["cookie"])
        self.store.update(auth["user"]["id"], lambda state: next((item.__setitem__("configuredModelId", model_id) for item in state["tasks"] if item["id"] == task["id"]), None))
        self._check_cancel(run)
        return bot_id

    def start_chat(self, auth, payload):
        """Validate chat before HTTP 200 and return the SSE generator."""
        if payload.get("taskId") and (not isinstance(payload["taskId"], str) or not re.fullmatch(r"[A-Za-z0-9_-]+", payload["taskId"])):
            raise GatewayError("任务 ID 不正确。", 400)
        task_id = payload.get("taskId") or str(uuid4())
        run = {"event": threading.Event(), "userId": auth["user"]["id"], "response": None, "started": False}
        with self._lock:
            if task_id in self.active:
                raise GatewayError("该任务正在处理，请稍后重试。", 409)
            self.active[task_id] = run
        try:
            message = str(payload.get("message") or "").strip()
            if not message:
                raise GatewayError("请输入消息。", 400)
            if len(message) > 100000:
                raise GatewayError("消息过长，请缩短后重试。", 400)
            available = self.catalog(auth)
            self._check_cancel(run)
            if not available["models"]:
                raise GatewayError("还没有配置可用模型。请打开设置中的“模型管理”，添加模型服务和 API Key。", 409)
            state = self.store.get(auth["user"]["id"])
            task = next((item for item in state["tasks"] if item["id"] == payload.get("taskId")), None) if payload.get("taskId") else None
            if payload.get("taskId") and task is None:
                raise GatewayError("找不到这个任务。", 404)
            agent_id = task.get("agentId") if task else str(payload.get("agentId") or "assistant")
            if not any(item["id"] == agent_id for item in available["agents"]) and not (task and task.get("botId")):
                raise GatewayError("该智能体不可用，请重新选择。", 400)
            model_id = str(payload.get("modelId") or (task.get("modelId") if task else "") or available["models"][0]["id"])
            if not any(item["id"] == model_id for item in available["models"]):
                raise GatewayError("所选模型已不可用，请刷新模型列表。", 400)
            project_id = (task.get("projectId") if task else None) or payload.get("projectId") or ""
            if project_id and not any(item["id"] == project_id for item in state["projects"]):
                raise GatewayError("项目不存在。", 404)
            query = message
            attachments = []
            file_ids = payload.get("fileIds") if isinstance(payload.get("fileIds"), list) else []
            for file_id in file_ids[:8]:
                self._check_cancel(run)
                metadata = next((item for item in state["files"] if item["id"] == file_id), None)
                if metadata is None:
                    raise GatewayError("附件不存在或无权访问。", 404)
                extension = Path(metadata["name"]).suffix.lower()
                if extension not in TEXT_EXT:
                    raise GatewayError(f"“{metadata['name']}”已保存在文件中。当前聊天可读取文本、代码和 CSV；图片、PDF、视频解析需在 Coze 工作台配置对应能力。", 400)
                if metadata["size"] > 180000:
                    raise GatewayError(f"文本附件“{metadata['name']}”超过 180 KB，请缩小后重试。", 400)
                with self._file_lock(auth["user"]["id"], file_id):
                    if not any(item["id"] == file_id for item in self.store.get(auth["user"]["id"])["files"]):
                        raise GatewayError("附件不存在或无权访问。", 404)
                    try:
                        content = (self.store.directory(auth["user"]["id"]) / "files" / file_id).read_text(encoding="utf-8", errors="replace")
                    except FileNotFoundError:
                        raise GatewayError("附件内容不存在，请重新上传。", 404) from None
                query += f"\n\n<用户提供的参考文件 name={json.dumps(metadata['name'], ensure_ascii=False)}>\n{content}\n</用户提供的参考文件>"
                attachments.append({"id": file_id, "name": metadata["name"]})
            if len(query) > 250000:
                raise GatewayError("附件总文本过长，请减少文件数量。", 400)
            self._check_cancel(run)
            created_task = task is None
            run["createdTask"] = created_task
            if created_task:
                task = {"id": task_id, "title": message[:32], "agentId": agent_id, "modelId": model_id, "projectId": project_id, "createdAt": now_iso(), "updatedAt": now_iso(), "messages": [], "status": "idle"}
                self.store.update(auth["user"]["id"], lambda current: current["tasks"].insert(0, task))
            if run["event"].is_set() and created_task:
                self.store.update(auth["user"]["id"], lambda current: current.__setitem__("tasks", [item for item in current["tasks"] if item["id"] != task_id]))
            self._check_cancel(run)
            context = {"auth": auth, "run": run, "task": task, "message": message, "query": query, "attachments": attachments, "available": available, "modelId": model_id}
            return self._stream_chat(context), lambda: self.abandon_chat(task_id, run)
        except Exception as error:
            self.abandon_chat(task_id, run)
            if run["event"].is_set():
                raise GatewayError("已停止生成。", 409) from error
            raise

    def abandon_chat(self, task_id, run):
        """Release an unstarted response closed before its SSE iterator runs."""
        with self._lock:
            if self.active.get(task_id) is not run or run["started"]:
                return
            self.active.pop(task_id, None)
        if run.get("createdTask"):
            self.store.update(run["userId"], lambda state: state.__setitem__("tasks", [item for item in state["tasks"] if item["id"] != task_id or item.get("messages") or item.get("status") != "idle"]))

    def _stream_chat(self, context):
        """Forward model tokens while persisting a complete or partial task."""
        auth = context["auth"]
        run = context["run"]
        task = context["task"]
        user_id = auth["user"]["id"]
        message = context["message"]
        attachments = context["attachments"]
        answer = ""
        failure = ""
        persisted_input = False
        saw_done = False
        disconnected = False
        upstream = None
        last_event = time.monotonic()

        def emit(name, data):
            return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        try:
            with self._lock:
                run["started"] = True
            yield emit("meta", {"taskId": task["id"], "title": task["title"]})
            bot_id = self._prepare_bot(task, auth, context["modelId"], context["available"], run)
            self._check_cancel(run)

            def save_input(state):
                current = next((item for item in state["tasks"] if item["id"] == task["id"]), None)
                if current:
                    current["messages"].append({"role": "user", "content": message, "attachments": attachments, "at": now_iso()})
                    current.update({"status": "running", "error": "", "updatedAt": now_iso(), "modelId": context["modelId"]})
            self.store.update(user_id, save_input)
            persisted_input = True
            self._check_cancel(run)
            upstream = self.coze.request("/api/conversation/chat", {"bot_id": bot_id, "conversation_id": task.get("conversationId") or "0", "query": context["query"], "scene": 4, "draft_mode": True, "content_type": "text"}, cookie=auth["cookie"], raw=True, timeout=(5, self.chat_idle_timeout))
            run["response"] = upstream
            if "text/event-stream" not in upstream.headers.get("Content-Type", ""):
                raise GatewayError("Coze 未返回聊天流，请检查智能体和模型配置。", 502)
            for event in parse_sse(upstream.iter_content(chunk_size=1)):
                self._check_cancel(run)
                if event["data"] or event["event"] in {"done", "error"}:
                    last_event = time.monotonic()
                elif time.monotonic() - last_event > self.chat_idle_timeout:
                    raise TimeoutError("模型长时间没有返回新内容，请重试或检查模型服务。")
                if event["event"] == "error":
                    try:
                        detail = parse_coze_json(event["data"])
                    except ValueError:
                        detail = {}
                    raise GatewayError(detail.get("msg") or detail.get("message") or "模型调用失败，请检查 API Key、余额与模型名称。", 400)
                if event["event"] == "done":
                    saw_done = True
                    break
                if not event["data"]:
                    continue
                try:
                    chunk = parse_coze_json(event["data"])
                except ValueError:
                    continue
                conversation_id = chunk.get("conversation_id")
                if conversation_id and str(conversation_id) != task.get("conversationId"):
                    task["conversationId"] = str(conversation_id)
                    self.store.update(user_id, lambda state: next((item.__setitem__("conversationId", task["conversationId"]) for item in state["tasks"] if item["id"] == task["id"]), None))
                part = chunk.get("message") or {}
                if part.get("type") == "answer" and part.get("role") != "user" and chunk.get("is_finish") and str(part.get("content") or "").startswith("Something error:"):
                    raise GatewayError(str(part["content"])[len("Something error:"):].strip() or "模型运行失败，请重试。", 502)
                if part.get("type") == "answer" and part.get("content") and part.get("role") != "user" and not chunk.get("is_finish"):
                    answer += str(part["content"])
                    yield emit("token", {"text": part["content"]})
            if not saw_done:
                raise GatewayError("模型连接提前结束，已保留部分回复，请检查后重试。", 502)
            if not answer.strip():
                raise GatewayError("模型没有返回文本回复。请在 Coze 工作台检查智能体运行结果。", 400)
        except GeneratorExit:
            disconnected = True
            run["event"].set()
            raise
        except (requests.Timeout, TimeoutError):
            failure = "模型长时间没有返回新内容，请重试或检查模型服务。"
            yield emit("error", {"message": failure})
        except requests.ConnectionError as error:
            if run["event"].is_set():
                failure = "已停止生成。"
            elif any(isinstance(item, ReadTimeoutError) for item in error.args):
                failure = "模型长时间没有返回新内容，请重试或检查模型服务。"
            else:
                failure = "模型连接提前结束，已保留部分回复，请检查后重试。"
            yield emit("error", {"message": failure})
        except Exception as error:
            failure = "已停止生成。" if run["event"].is_set() else str(error)
            yield emit("error", {"message": failure})
        finally:
            if upstream is not None:
                upstream.close()
            if disconnected:
                failure = "已停止生成。"

            def finish(state):
                current = next((item for item in state["tasks"] if item["id"] == task["id"]), None)
                if current is None:
                    return
                if not persisted_input:
                    current["messages"].append({"role": "user", "content": message, "attachments": attachments, "at": now_iso()})
                if answer:
                    current["messages"].append({"role": "assistant", "content": answer, "at": now_iso(), "partial": bool(failure)})
                current["error"] = failure
                current["status"] = "stopped" if run["event"].is_set() and failure == "已停止生成。" else ("error" if failure else "done")
                current["updatedAt"] = now_iso()
                if task.get("conversationId"):
                    current["conversationId"] = task["conversationId"]
            self.store.update(user_id, finish)
            with self._lock:
                if self.active.get(task["id"]) is run:
                    self.active.pop(task["id"], None)
        if not disconnected:
            current = next((item for item in self.store.get(user_id)["tasks"] if item["id"] == task["id"]), None)
            yield emit("done", {"task": public_task(current) if current else None})
