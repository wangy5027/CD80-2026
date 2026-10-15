"""Workbench business operations and chat orchestration."""

import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from .auth import LocalAuth
from .data_files import MAX_DATA_BYTES, build_data_info, data_context
from .errors import GatewayError
from .graph_runtime import GraphRuntime
from .store import StateStore
from .tool_registry import ToolContext


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
    """Hide legacy provider identifiers while keeping old tasks readable."""
    return {key: value for key, value in task.items() if key not in {"botId", "conversationId", "botSpaceId", "configuredModelId"}}


class WorkbenchService:
    def __init__(self, config):
        self.store = StateStore(config["DATA_DIR"])
        self.auth = LocalAuth(config["DATA_DIR"], import_legacy=config.get("IMPORT_LEGACY_HISTORY", False))
        self.runtime = GraphRuntime(config)
        self.model_id = self.runtime.provider.model
        self.active = {}
        self._lock = threading.RLock()
        self._file_locks = {}
        self.platform = None
        self.capabilities = None
        self.sub_agents = None

    def close(self):
        """Stop ongoing generation and close provider response streams."""
        for name in ('explore', 'workshop', 'app_workshop'):
            component = getattr(self, name, None)
            if component is not None:
                component.close()
        if getattr(self, 'authoring', None) is not None:
            self.authoring.close()
        if getattr(self, 'asset_apps', None) is not None:
            self.asset_apps.close()
        if self.sub_agents is not None:
            self.sub_agents.close()
        if self.platform is not None:
            self.platform.close()
        with self._lock:
            active = list(self.active.values())
        for run in active:
            run["event"].set()
            if run.get("response") is not None:
                try:
                    run["response"].abort()
                except Exception:
                    pass
        self.runtime.close()
        if self.capabilities is not None:
            self.capabilities.close()

    def _check_cancel(self, run):
        if run["event"].is_set():
            raise GatewayError("已停止生成。", 409)

    def _file_lock(self, user_id, file_id):
        key = (str(user_id), str(file_id))
        with self._lock:
            return self._file_locks.setdefault(key, threading.RLock())

    def authenticate(self, cookie, refresh=False):
        """Resolve the local account for this request."""
        return {"user": self.auth.authenticate(cookie), "cookie": cookie}

    def catalog(self, auth=None):
        """Advertise only the configured provider model and three text roles."""
        builtins = [{**{key: value for key, value in agent.items() if key != "prompt"}, "builtin": True} for agent in BUILTINS]
        return {"models": [{"id": self.model_id, "name": self.model_id}], "agents": builtins, "activeSpaceId": "", "warnings": []}

    def model_status(self):
        """Expose configuration presence without a secret, URL, or false health claim."""
        configured = bool(self.runtime.provider.key)
        skills = self.runtime.skills.catalog()
        return {"provider": "Gitee AI", "model": self.model_id, "configured": configured,
                "subAgentAvailable": self.sub_agents is not None,
                "message": "模型密钥已配置；实际可用性将在对话时验证。" if configured else "请在网站 .env 中配置 GITEE_AI_API_KEY。",
                "searchAvailable": self.runtime.tools.has_capability("search"),
                "skills": skills, "skillWarnings": list(self.runtime.skills.warnings),
                "tools": self.runtime.tools.catalog()}

    def tools_catalog(self):
        return {"tools": self.runtime.tools.catalog(include_parameters=True)}

    def execute_tool(self, name, body):
        """Execute one explicitly registered tool call for an authenticated request."""
        arguments = body.get("arguments")
        if not isinstance(arguments, dict):
            raise GatewayError("工具参数必须是 JSON 对象。", 400)
        query = arguments.get("query")
        result = self.runtime.tools.execute(
            name, arguments,
            ToolContext(threading.Event(), lambda _response: None, lambda _event: None,
                        query=query if isinstance(query, str) else ""),
            call_count=0,
        )
        return {"name": name, "result": result.content, "sources": result.sources or []}

    def skills_catalog(self):
        return {"skills": self.runtime.skills.catalog(), "warnings": list(self.runtime.skills.warnings)}

    def load_skill(self, name):
        try:
            return self.runtime.skills.load(name)
        except ValueError:
            raise GatewayError("Skill 不存在或无法加载。", 404) from None

    def login(self, kind, body):
        """Authenticate or register locally and return the local session cookie."""
        email = str(body.get("email") or "").strip()
        password = str(body.get("password") or "")
        if not email or not password:
            raise GatewayError("请输入邮箱和密码。", 400)
        if kind == "register":
            user, token = self.auth.register(email, password, clean_name(body.get("name")))
        elif kind == "login":
            user, token = self.auth.login(email, password)
        else:
            raise GatewayError("请求方法不受支持。", 405)
        return {"ok": True, "user": user}, [self.auth.cookie(token)]

    def session(self, cookie):
        """Return the local account and server-side model catalog."""
        try:
            auth = self.authenticate(cookie)
            return {"authenticated": True, "user": auth["user"], **self.catalog(auth)}
        except GatewayError as error:
            return {"authenticated": False, "backendUnavailable": False}

    def logout(self, cookie):
        """Revoke only this local session."""
        self.auth.logout(cookie)
        return {"ok": True}

    def state(self, user_id):
        """Return all current account state with private bindings hidden."""
        # Older uploads predate content fingerprints. Fill them from the stored
        # bytes once, while preserving any existing SHA256 approval bindings.
        for item in self.store.get(user_id)["files"]:
            if item.get("md5") and item.get("sha256"):
                continue
            try:
                stream, _, lock = self.open_file(user_id, item["id"])
            except GatewayError as error:
                if error.status == 404:
                    continue  # Keep a legacy missing-file record visible.
                raise
            try:
                stream.close()
            finally:
                lock.release()
        state = self.store.get(user_id)
        state.pop("bindings", None)
        state["tasks"] = sorted((public_task(task) for task in state["tasks"]), key=lambda task: task.get("updatedAt", ""), reverse=True)
        return state

    def stop(self, user_id, task_id):
        """Cancel only a task active for the authenticated account."""
        with self._lock:
            run = self.active.get((user_id, task_id))
        if run:
            run["event"].set()
            if run.get("response") is not None:
                try:
                    run["response"].abort()
                except Exception:
                    pass
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

    def upload_file(self, user_id, upload, project_id, purpose=""):
        """Reuse identical uploads only within the same account/project/name."""
        if upload is None:
            raise GatewayError("请选择文件。", 400)
        if purpose not in {"", "data"}:
            raise GatewayError("文件用途无效。", 400)
        state = self.store.get(user_id)
        if project_id and not any(item["id"] == project_id for item in state["projects"]):
            raise GatewayError("项目不存在。", 404)
        limit = MAX_DATA_BYTES if purpose == "data" else 30 * 1024 * 1024
        data = upload.read(limit + 1)
        if len(data) > limit:
            raise GatewayError(f"单个{'数据' if purpose == 'data' else ''}文件不得超过 {limit // (1024 * 1024)} MB。", 413)
        original_name = Path(upload.filename or "").name.replace("\r", "").replace("\n", "").replace("\x00", "")
        name = original_name[:180]
        data_info = build_data_info(original_name, data) if purpose == "data" else None
        md5 = hashlib.md5(data, usedforsecurity=False).hexdigest()
        sha256 = hashlib.sha256(data).hexdigest()
        # Serialize discovery and insertion so concurrent identical requests do
        # not both create a file. This reserved lock key cannot be a file UUID.
        with self._file_lock(user_id, "_uploads"):
            state = self.store.get(user_id)
            for item in state["files"]:
                if item.get("projectId", "") != project_id or item.get("originalName", item["name"]) != original_name or item["size"] != len(data):
                    continue
                try:
                    stream, existing, lock = self.open_file(user_id, item["id"])
                except GatewayError as error:
                    if error.status == 404:
                        continue
                    raise
                try:
                    if existing.get("md5") == md5 and existing.get("sha256") == sha256:
                        # Metadata can outlive an externally changed file. Never
                        # reuse an ID unless its current bytes also match.
                        if stream.read(len(data) + 1) == data:
                            if data_info is not None and (existing.get("source") != "data_management" or existing.get("dataInfo") != data_info):
                                def promote(current):
                                    matched = next(value for value in current["files"] if value["id"] == existing["id"])
                                    matched.update(source="data_management", dataInfo=data_info)
                                self.store.update(user_id, promote)
                                return {**existing, "source": "data_management", "dataInfo": data_info}
                            return existing
                finally:
                    stream.close()
                    lock.release()
            file_id = str(uuid4())
            metadata = {"id": file_id, "name": name, "originalName": original_name, "size": len(data), "md5": md5, "sha256": sha256, "type": upload.content_type or "application/octet-stream", "projectId": project_id, "createdAt": now_iso()}
            if data_info is not None:
                metadata.update(source="data_management", dataInfo=data_info)
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

    def _backfill_file_hashes(self, user_id, metadata, stream):
        """Persist missing digests without rewriting a known SHA256 fingerprint."""
        missing = [key for key in ("md5", "sha256") if not metadata.get(key)]
        if not missing:
            return metadata
        md5, sha256 = hashlib.md5(usedforsecurity=False), hashlib.sha256()
        for chunk in iter(lambda: stream.read(65536), b""):
            md5.update(chunk)
            sha256.update(chunk)
        stream.seek(0)
        hashes = {"md5": md5.hexdigest(), "sha256": sha256.hexdigest()}
        updates = {key: hashes[key] for key in missing}
        def change(state):
            item = next(value for value in state["files"] if value["id"] == metadata["id"])
            item.update(updates)
        self.store.update(user_id, change)
        return {**metadata, **updates}

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
            try:
                metadata = self._backfill_file_hashes(user_id, metadata, stream)
            except BaseException:
                stream.close()
                raise
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
        if not any(item["id"] == task_id for item in self.store.get(user_id)["tasks"]):
            raise GatewayError("任务不存在。", 404)
        active_key = (user_id, task_id)
        with self._lock:
            if self.sub_agents is not None:
                self.sub_agents.guard_legacy(user_id, task_id)
            if active_key in self.active:
                raise GatewayError("该任务正在处理，请稍后重试。", 409)
            self.active[active_key] = {"event": threading.Event(), "userId": user_id, "deleting": True}
        try:
            if not any(item["id"] == task_id for item in self.store.get(user_id)["tasks"]):
                raise GatewayError("任务不存在。", 404)
            self.store.update(user_id, lambda state: state.__setitem__("tasks", [item for item in state["tasks"] if item["id"] != task_id]))
            self.runtime.forget(user_id, task_id)
            return {"ok": True}
        finally:
            with self._lock:
                self.active.pop(active_key, None)

    def _history_for_model(self, user_id, task, state):
        """Replay saved task messages, restoring existing text attachments when present."""
        files = {item["id"]: item for item in state["files"]}
        history = []
        for item in task.get("messages", []):
            role = item.get("role")
            content = item.get("content")
            if role not in {"user", "assistant"} or not isinstance(content, str) or not content:
                continue
            if role == "user":
                for attachment in item.get("attachments") or []:
                    file_id = attachment.get("id") if isinstance(attachment, dict) else ""
                    metadata = files.get(file_id)
                    if metadata is None:
                        continue
                    if metadata.get("source") == "data_management":
                        try:
                            stream, current, lock = self.open_file(user_id, file_id)
                        except GatewayError as error:
                            if error.status == 404:
                                continue
                            raise
                        try:
                            text = data_context(current, stream.read(MAX_DATA_BYTES + 1))
                        except GatewayError:
                            continue
                        finally:
                            stream.close()
                            lock.release()
                        content += f"\n\n<用户提供的参考文件 name={json.dumps(current['name'], ensure_ascii=False)}>\n{text}\n</用户提供的参考文件>"
                    elif Path(metadata["name"]).suffix.lower() in TEXT_EXT and metadata["size"] <= 180000:
                        with self._file_lock(user_id, file_id):
                            target = self.store.directory(user_id) / "files" / file_id
                            try:
                                text = target.read_text(encoding="utf-8", errors="replace")
                            except FileNotFoundError:
                                continue
                        content += f"\n\n<用户提供的参考文件 name={json.dumps(metadata['name'], ensure_ascii=False)}>\n{text}\n</用户提供的参考文件>"
            elif role == "assistant":
                references = []
                for source in item.get("sources") or []:
                    if isinstance(source, dict) and isinstance(source.get("url"), str):
                        references.append(f"{source.get('id', '')} {source.get('title', '')}: {source['url']}")
                if references:
                    content += "\n\n已核对来源：\n" + "\n".join(references[:20])
            history.append({"role": role, "content": content})
        return history

    def start_chat(self, auth, payload):
        """Validate a task before HTTP 200, then run the persisted graph as SSE."""
        if payload.get("taskId") and (not isinstance(payload["taskId"], str) or not re.fullmatch(r"[A-Za-z0-9_-]+", payload["taskId"])):
            raise GatewayError("任务 ID 不正确。", 400)
        task_id = payload.get("taskId") or str(uuid4())
        run = {"event": threading.Event(), "userId": auth["user"]["id"], "response": None, "started": False}
        active_key = (auth["user"]["id"], task_id)
        with self._lock:
            if self.sub_agents is not None:
                self.sub_agents.guard_legacy(auth['user']['id'], task_id)
            if active_key in self.active:
                raise GatewayError("该任务正在处理，请稍后重试。", 409)
            self.active[active_key] = run
        try:
            message = str(payload.get("message") or "").strip()
            if not message:
                raise GatewayError("请输入消息。", 400)
            if len(message) > 100000:
                raise GatewayError("消息过长，请缩短后重试。", 400)
            search_mode = payload.get("searchMode", "auto")
            if not isinstance(search_mode, str) or search_mode not in {"auto", "on", "off"}:
                raise GatewayError("联网搜索模式无效。", 400)
            requested_skills = payload.get("skillNames", [])
            if not isinstance(requested_skills, list) or len(requested_skills) > 3:
                raise GatewayError("每次最多选择 3 个 Skill。", 400)
            selected_skills = []
            selected_names = set()
            for name in requested_skills:
                if not isinstance(name, str):
                    raise GatewayError("Skill 名称无效。", 400)
                if name in selected_names:
                    continue
                try:
                    skill = self.runtime.skills.load(name)
                except ValueError:
                    raise GatewayError("所选 Skill 不存在或无法加载。", 400) from None
                selected_skills.append(skill)
                selected_names.add(name)
            if not self.runtime.provider.key:
                raise GatewayError("模型服务尚未配置密钥，请在服务器 .env 中设置 GITEE_AI_API_KEY。", 503)
            self._check_cancel(run)
            user_id = auth["user"]["id"]
            state = self.store.get(user_id)
            task = next((item for item in state["tasks"] if item["id"] == payload.get("taskId")), None) if payload.get("taskId") else None
            if payload.get("taskId") and task is None:
                raise GatewayError("找不到这个任务。", 404)
            capability_refs = payload.get("capabilityRefs", [])
            selected_capabilities, capability_locks = [], {}
            if capability_refs:
                selected_capabilities, capability_locks = self.capabilities.prepare_chat(
                    user_id, task, capability_refs, payload.get("replaceCapabilityLocks") is True, payload.get("taskRevision"))
            elif not isinstance(capability_refs, list):
                raise GatewayError("能力引用格式无效。", 400)
            available = self.catalog(auth)
            requested_agent = str(payload.get("agentId") or "assistant")
            agent_id = task.get("agentId") if task else requested_agent
            if not any(item["id"] == agent_id for item in available["agents"]):
                if task:
                    agent_id = "assistant"  # Former provider roles remain readable as text history.
                else:
                    raise GatewayError("该智能体不可用，请重新选择。", 400)
            selected_model = payload.get("modelId")
            if selected_model and str(selected_model) != self.model_id:
                raise GatewayError("所选模型已不可用，请刷新模型列表。", 400)
            model_id = self.model_id  # A former provider model is never reused.
            project_id = payload.get("projectId") if payload.get("projectId") is not None else (task.get("projectId") if task else "")
            project_id = project_id or ""
            if project_id and not any(item["id"] == project_id for item in state["projects"]):
                if task and payload.get("projectId") is None:
                    project_id = ""
                else:
                    raise GatewayError("项目不存在。", 404)
            query = message
            attachments = []
            file_ids = payload.get("fileIds") if isinstance(payload.get("fileIds"), list) else []
            for file_id in file_ids[:8]:
                self._check_cancel(run)
                metadata = next((item for item in state["files"] if item["id"] == file_id), None)
                if metadata is None:
                    raise GatewayError("附件不存在或无权访问。", 404)
                if metadata.get("source") == "data_management":
                    stream, current, lock = self.open_file(user_id, file_id)
                    try:
                        content = data_context(current, stream.read(MAX_DATA_BYTES + 1))
                    finally:
                        stream.close()
                        lock.release()
                else:
                    extension = Path(metadata["name"]).suffix.lower()
                    if extension not in TEXT_EXT:
                        raise GatewayError(f"“{metadata['name']}”已保存在文件中。当前聊天可读取文本、代码和 CSV；图片、PDF、视频解析尚未配置。", 400)
                    if metadata["size"] > 180000:
                        raise GatewayError(f"文本附件“{metadata['name']}”超过 180 KB，请缩小后重试。", 400)
                    with self._file_lock(user_id, file_id):
                        if not any(item["id"] == file_id for item in self.store.get(user_id)["files"]):
                            raise GatewayError("附件不存在或无权访问。", 404)
                        try:
                            content = (self.store.directory(user_id) / "files" / file_id).read_text(encoding="utf-8", errors="replace")
                        except FileNotFoundError:
                            raise GatewayError("附件内容不存在，请重新上传。", 404) from None
                query += f"\n\n<用户提供的参考文件 name={json.dumps(metadata['name'], ensure_ascii=False)}>\n{content}\n</用户提供的参考文件>"
                attachments.append({"id": file_id, "name": metadata["name"]})
            if len(query) > 250000:
                raise GatewayError("附件总文本过长，请减少文件数量。", 400)
            history = self._history_for_model(user_id, task, state) if task else []
            prompt = next(item["prompt"] for item in BUILTINS if item["id"] == agent_id)
            self._check_cancel(run)
            created_task = task is None
            run["createdTask"] = created_task
            if created_task:
                task = {"id": task_id, "title": message[:32], "agentId": agent_id, "modelId": model_id, "projectId": project_id, "createdAt": now_iso(), "updatedAt": now_iso(), "messages": [], "status": "idle"}
                self.store.update(user_id, lambda current: current["tasks"].insert(0, task))
            if run["event"].is_set() and created_task:
                self.store.update(user_id, lambda current: current.__setitem__("tasks", [item for item in current["tasks"] if item["id"] != task_id]))
            self._check_cancel(run)
            context = {"auth": auth, "run": run, "task": task, "message": message, "query": query, "attachments": attachments, "history": history, "prompt": prompt, "modelId": model_id, "agentId": agent_id, "projectId": project_id, "searchMode": search_mode, "selectedSkills": selected_skills}
            context.update(selectedCapabilities=selected_capabilities, capabilityLocks=capability_locks, fileIds=file_ids)
            return self._stream_chat(context), lambda: self.abandon_chat(task_id, run)
        except Exception as error:
            self.abandon_chat(task_id, run)
            if run["event"].is_set():
                raise GatewayError("已停止生成。", 409) from error
            raise

    def abandon_chat(self, task_id, run):
        """Release an unstarted response closed before its SSE iterator runs."""
        active_key = (run["userId"], task_id)
        with self._lock:
            if self.active.get(active_key) is not run or run["started"]:
                return
            self.active.pop(active_key, None)
        if run.get("createdTask"):
            self.store.update(run["userId"], lambda state: state.__setitem__("tasks", [item for item in state["tasks"] if item["id"] != task_id or item.get("messages") or item.get("status") != "idle"]))

    def _stream_chat(self, context):
        """Run the graph, emit tokens, and persist complete or partial replies."""
        auth, run, task = context["auth"], context["run"], context["task"]
        user_id = auth["user"]["id"]
        message, attachments = context["message"], context["attachments"]
        answer = ""
        sources = []
        skills_used = []
        tools_used = []
        capability_usages = []
        failure = ""
        persisted_input = False
        completed = False
        disconnected = False
        graph_stream = None

        def emit(name, data):
            return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        def on_response(response):
            run["response"] = response
            if response is not None and run["event"].is_set():
                response.abort()

        try:
            with self._lock:
                run["started"] = True
            yield emit("meta", {"taskId": task["id"], "title": task["title"]})

            def save_input(state):
                current = next((item for item in state["tasks"] if item["id"] == task["id"]), None)
                if current:
                    current["messages"].append({"role": "user", "content": message, "attachments": attachments,
                                                "searchMode": context["searchMode"],
                                                "skillNames": [skill["name"] for skill in context["selectedSkills"]],
                                                "at": now_iso()})
                    current.update({"status": "running", "error": "", "updatedAt": now_iso(), "modelId": context["modelId"], "agentId": context["agentId"], "projectId": context["projectId"]})
                    if context.get("selectedCapabilities"):
                        current["capabilityLocks"] = context["capabilityLocks"]
                        current["capabilityRevision"] = current.get("capabilityRevision", 0) + 1
                        current["messages"][-1]["capabilityRefs"] = [{"assetId":v["assetId"],"versionId":v["versionId"]} for v in context["selectedCapabilities"]]
                    for legacy_key in ("botId", "conversationId", "botSpaceId", "configuredModelId"):
                        current.pop(legacy_key, None)
            self.store.update(user_id, save_input)
            persisted_input = True
            self._check_cancel(run)
            graph_options = {"search_mode": context["searchMode"]}
            if context["selectedSkills"]:
                graph_options["selected_skills"] = context["selectedSkills"]
            from ..capability_library.chat_bridge import ChatCapabilities
            graph_options['capability_session'] = ChatCapabilities(self.capabilities, user_id, task['id'], context['selectedCapabilities'], context['fileIds'])
            graph_stream = self.runtime.stream(user_id, task["id"], context["history"], context["query"], context["prompt"], context["modelId"], run["event"], on_response, **graph_options)
            for item in graph_stream:
                self._check_cancel(run)
                if item["type"] == "token":
                    answer += item["text"]
                    yield emit("token", {"text": item["text"]})
                elif item["type"] == "skill_loaded":
                    if item["name"] not in skills_used:
                        skills_used.append(item["name"])
                    if item.get("assetId"):
                        capability_usages.append(item)
                    yield emit("skill_loaded", {key:value for key,value in item.items() if key != "type"})
                elif item["type"] in {"capability_selected", "tool_started", "tool_completed", "tool_failed", "capability_blocked"}:
                    capability_usages.append(item)
                    yield emit(item["type"], {key:value for key,value in item.items() if key != "type"})
                elif item["type"] == "search_status":
                    yield emit("search_status", {key: value for key, value in item.items() if key != "type"})
                elif item["type"] == "search_sources":
                    existing = {source.get("url") for source in sources}
                    for source in item.get("sources", []):
                        if isinstance(source, dict) and source.get("url") not in existing:
                            sources.append(source)
                            existing.add(source.get("url"))
                    yield emit("search_sources", {"sources": item.get("sources", [])})
                elif item["type"] == "tool_status":
                    name = item.get("name")
                    status = item.get("status")
                    if status == "complete" and isinstance(name, str) and name not in tools_used:
                        tools_used.append(name)
                    yield emit("tool_status", {"name": name, "status": status})
                elif item["type"] == "complete":
                    answer = item["message"]["content"]
                    sources = item["message"].get("sources", sources)
                    skills_used = item["message"].get("skillsUsed", skills_used)
                    tools_used = item["message"].get("toolsUsed", tools_used)
                    completed = True
            if not completed:
                raise GatewayError("模型输出未能完成，请稍后重试。", 502)
        except GeneratorExit:
            disconnected = True
            run["event"].set()
            raise
        except GatewayError as error:
            failure = "已停止生成。" if run["event"].is_set() else str(error)
            yield emit("error", {"message": failure})
        except Exception:
            failure = "已停止生成。" if run["event"].is_set() else "模型运行失败，请稍后重试。"
            yield emit("error", {"message": failure})
        finally:
            if disconnected:
                run["event"].set()
                failure = "已停止生成。"
            if run.get("response") is not None:
                try:
                    run["response"].abort()
                except Exception:
                    pass
            if graph_stream is not None:
                try:
                    graph_stream.close()
                except Exception:
                    pass

            def finish(state):
                current = next((item for item in state["tasks"] if item["id"] == task["id"]), None)
                if current is None:
                    return
                if not persisted_input:
                    current["messages"].append({"role": "user", "content": message, "attachments": attachments,
                                                "searchMode": context["searchMode"],
                                                "skillNames": [skill["name"] for skill in context["selectedSkills"]],
                                                "at": now_iso()})
                if answer or sources or skills_used or tools_used or capability_usages:
                    current["messages"].append({"role": "assistant", "content": answer, "at": now_iso(), "partial": bool(failure),
                                                "sources": sources, "skillsUsed": skills_used,
                                                "toolsUsed": tools_used, "capabilityUsages": capability_usages})
                current["error"] = failure
                current["status"] = "stopped" if run["event"].is_set() and failure == "已停止生成。" else ("error" if failure else "done")
                current["updatedAt"] = now_iso()
            try:
                self.store.update(user_id, finish)
            finally:
                with self._lock:
                    active_key = (user_id, task["id"])
                    if self.active.get(active_key) is run:
                        self.active.pop(active_key, None)
        if not disconnected:
            current = next((item for item in self.store.get(user_id)["tasks"] if item["id"] == task["id"]), None)
            yield emit("done", {"task": public_task(current) if current else None})
