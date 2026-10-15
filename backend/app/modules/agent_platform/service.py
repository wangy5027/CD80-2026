"""Fixed main-agent lifecycle and bounded, sequential sub-agent execution."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
from functools import wraps
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from ..workbench.errors import GatewayError
from ..workbench.graph_runtime import GiteeProvider
from .contracts import TERMINAL, canonical, digest, identifier, missing_inputs, text, validate_agent_spec, validate_input
from .planner import Planner
from .repository import Repository, now, definition_digest
from .shared_capabilities import resolve_references
from .library_capabilities import catalog as component_catalog, validate_dependencies


def uid():
    return str(uuid4())


def conversation_entry(method):
    @wraps(method)
    def guarded(self, owner, body):
        with self.workbench._lock:
            coordinator = getattr(self.workbench, 'sub_agents', None)
            if coordinator:
                coordinator.guard_legacy(owner, body.get('taskId'))
            return method(self, owner, body)
    return guarded


class AgentPlatform:
    def __init__(self, workbench, config):
        self.workbench = workbench
        self.config = config
        self.repository = Repository(config["DATA_DIR"])
        self.planner = Planner(GiteeProvider({**config,
            "GITEE_AI_MAX_TOKENS": config.get("AGENT_MODEL_MAX_TOKENS", 8192),
            "GITEE_AI_REASONING_EFFORT": config.get("AGENT_MODEL_REASONING_EFFORT", "low")}),
            tool_registry=workbench.runtime.tools, skill_registry=workbench.runtime.skills)
        from .executor import SandboxExecutor
        self.executor = SandboxExecutor(config)
        self.root = Path(config["DATA_DIR"]) / "agent-artifacts"
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.active = {}
        self.dispatch_active = {}
        self.closed = False
        self.presets = {}

    def register_preset(self, spec):
        spec = validate_agent_spec(resolve_references(spec, self.workbench.runtime.tools, self.workbench.runtime.skills))
        spec["origin"] = "preset"
        self.presets[spec["id"] + "@" + spec["version"]] = spec

    def list_agents(self, owner, catalog=False):
        personal = self.repository.list_agents(owner)
        metadata = self.repository.application_metadata(owner)
        personal_refs={p["id"]+"@"+p["version"] for p in personal}
        return [{"agentRef": ref, "spec": spec, "personal": False, "personalCopies":[{'agentRef':p['id']+'@'+p['version'],'name':p['name']} for p in personal if definition_digest(p)==definition_digest(spec)]} for ref, spec in self.presets.items() if catalog or ref not in personal_refs] + [
            {"agentRef": spec["id"] + "@" + spec["version"], "spec": spec, "personal": True,
             **metadata.get(spec["id"] + "@" + spec["version"], {})} for spec in personal]

    def components(self, owner):
        return component_catalog(getattr(self.workbench, 'capabilities', None), owner)

    def export_application(self, owner, agent_ref):
        from .packages import export_application
        return export_application(self, owner, agent_ref)

    def import_application(self, owner, body, *, source=None):
        from .packages import import_application
        return import_application(self, owner, body, source=source)

    def fork_application(self, owner, body):
        from .packages import fork_application
        return fork_application(self, owner, body)

    def agent(self, owner, reference):
        try:
            return self.repository.agent(owner, reference)
        except GatewayError as error:
            if error.status != 404 or reference not in self.presets:
                raise
        return copy.deepcopy(self.presets[reference])

    def status(self):
        return {"executor": self.executor.status(), "maxSteps": 8, "maxConcurrentRuns": 2, "execution": "sequential", "previewExecutesTools": False}

    def _metadata(self, owner, file_ids, *, fingerprint=True):
        if not isinstance(file_ids, list) or len(file_ids) > 8 or any(not isinstance(x, str) for x in file_ids):
            raise GatewayError("最多选择 8 个输入文件。", 400)
        files = {item["id"]: item for item in self.workbench.store.get(owner)["files"]}
        result = []
        for file_id in dict.fromkeys(file_ids):
            identifier(file_id, "文件 ID")
            if file_id not in files:
                raise GatewayError("输入文件不存在或无权访问。", 404)
            item = files[file_id]
            # New uploads carry a digest; legacy files need only a content fingerprint,
            # never parsing, computation or submission of their content to the planner.
            sha, md5 = item.get("sha256"), item.get("md5")
            if fingerprint and (not sha or not md5):
                stream, stored, lock = self.workbench.open_file(owner, file_id)
                try:
                    sha, md5 = stored.get("sha256"), stored.get("md5")
                finally:
                    stream.close()
                    lock.release()
            result.append({key: item[key] for key in ("id", "name", "size", "type") if key in item} | {"sha256": sha, "md5": md5})
        return result

    def _load_files(self, owner, metadata):
        files = {}
        for item in metadata:
            stream, _, lock = self.workbench.open_file(owner, item["id"])
            try:
                data = stream.read(30 * 1024 * 1024 + 1)
            finally:
                stream.close()
                lock.release()
            if hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise GatewayError("输入文件内容已改变，请重新预览并确认。", 409, "input_changed")
            files[item["id"]] = data
        return files

    @staticmethod
    def _binding(doc):
        binding = {"spec": doc["spec"], "input": doc["input"], "files": doc["files"], "allowedFileIds": doc["allowedFileIds"]}
        # Older pending runs retain their original approval digest.
        if "searchMode" in doc:
            binding["searchMode"] = doc["searchMode"]
        return digest(binding)

    @staticmethod
    def _check_file_total(existing, added):
        if len(set(existing) | set(added)) > 8:
            raise GatewayError("一次运行累计最多授权 8 个文件，请缩小输入范围。", 400, "too_many_files")

    @staticmethod
    def _approval(kind, binding, **extra):
        return {"id": uid(), "kind": kind, "status": "pending", "bindingDigest": binding, "createdAt": now(), **extra}

    def _task(self, owner, task_id, message, file_ids, project_id=None):
        state = self.workbench.store.get(owner)
        if project_id and not any(item["id"] == project_id for item in state["projects"]):
            raise GatewayError("项目不存在或无权访问。", 404)
        task = next((item for item in state["tasks"] if item["id"] == task_id), None) if task_id else None
        if task_id and task is None:
            raise GatewayError("任务不存在。", 404)
        if task is None:
            task = {"id": uid(), "title": message[:32], "agentId": "assistant", "modelId": self.workbench.model_id, "projectId": project_id or "", "createdAt": now(), "updatedAt": now(), "messages": [], "status": "idle", "platform": True}
            self.workbench.store.update(owner, lambda state: state["tasks"].insert(0, task))
        return task

    def _message(self, owner, task_id, role, content, **extra):
        def change(state):
            task = next((item for item in state["tasks"] if item["id"] == task_id), None)
            if task:
                task["messages"].append({"role": role, "content": content, "at": now(), **extra})
                task["updatedAt"] = now()
                task["platform"] = True
        self.workbench.store.update(owner, change)

    @staticmethod
    def _search_mode(body):
        mode = body.get("searchMode", "auto")
        if not isinstance(mode, str) or mode not in {"auto", "on", "off"}:
            raise GatewayError("网页搜索模式无效。", 400)
        return mode

    @conversation_entry
    def dispatch(self, owner, body):
        self._search_mode(body)
        key = text(body.get("idempotencyKey") or uid(), "请求标识", 150)
        request = {k: body.get(k) for k in ("message", "taskId", "projectId", "fileIds", "searchMode")}
        with self.lock:
            old = self.repository.request_result(owner, "dispatch", key, request)
            if old is not None:
                if old.get("run"):
                    old["run"] = self.repository.get(owner, old["run"]["id"])
                return old
            if self.closed:
                raise GatewayError("服务正在关闭。", 503)
            if (owner, key) in self.dispatch_active or len(self.dispatch_active) >= 2:
                raise GatewayError("正在识别需求，请等待当前请求完成。", 409)
            control = {"stop": threading.Event(), "response": None}
            self.dispatch_active[(owner, key)] = control
        try:
            result = self._dispatch(owner, {**body, "idempotencyKey": key}, control)
            with self.lock:
                stopped = control["stop"].is_set()
                if not stopped:
                    self.repository.record_request(owner, "dispatch", key, request, result)
            if stopped:
                if result.get("run"):
                    self.cancel(owner, result["run"]["id"])
                raise GatewayError("已停止识别需求。", 409, "cancelled")
            return result
        finally:
            with self.lock:
                self.dispatch_active.pop((owner, key), None)

    def cancel_dispatch(self, owner, key):
        with self.lock:
            control = self.dispatch_active.get((owner, key))
            if control:
                control["stop"].set()
                if control["response"]:
                    control["response"].abort()
                return {"status": "cancelling"}
            result = self.repository.request_result(owner, "dispatch", key)
        if result and result.get("run"):
            return self.cancel(owner, result["run"]["id"])
        return {"status": "cancelled"}

    def _dispatch(self, owner, body, control):
        """Classify using metadata only; normal chat returns to the existing stream."""
        message = text(body.get("message"), "需求", 10000)
        file_ids = body.get("fileIds", [])
        task = self._task(owner, body.get("taskId"), message, file_ids, body.get("projectId"))
        pending = task.get("pendingAgentInput") or {}
        if not file_ids and pending:
            file_ids = pending.get("fileIds", [])
        metadata = self._metadata(owner, file_ids)
        history = [{"role": m["role"], "content": m.get("content", "")[:5000]} for m in task.get("messages", [])[-12:] if m.get("role") in {"user", "assistant"}]
        if pending.get("input"):
            history.append({"role": "assistant", "content": "上轮已明确的应用参数（本轮可修改）：" + canonical(pending["input"])})
        decision = self.planner.classify(message, metadata, self.list_agents(owner), history, control["stop"], self._on_response(control))
        if control["stop"].is_set():
            raise GatewayError("已停止识别需求。", 409, "cancelled")
        def remember(state):
            current = next(x for x in state["tasks"] if x["id"] == task["id"])
            current["pendingAgentInput"] = {"input": decision.get("input", {}), "fileIds": file_ids} if decision["intent"] == "clarify" else None
        self.workbench.store.update(owner, remember)
        if decision["intent"] == "chat":
            return {"intent": "chat", "taskId": task["id"]}
        self._message(owner, task["id"], "user", message, attachments=[{"id": f["id"], "name": f["name"]} for f in metadata])
        if decision["intent"] == "clarify":
            self._message(owner, task["id"], "assistant", decision["question"])
            return {**decision, "taskId": task["id"]}
        payload = {"mode": decision["intent"], "goal": decision.get("goal") or message,
                   "agentRef": decision.get("agentRef"), "input": decision.get("input", {}),
                   "fileIds": file_ids, "taskId": task["id"], "searchMode": self._search_mode(body),
                   "idempotencyKey": body.get("idempotencyKey") or uid()}
        if len(file_ids) == 1:
            payload["input"].setdefault("fileId", file_ids[0])
        with self.lock:
            if control["stop"].is_set():
                raise GatewayError("已停止识别需求。", 409, "cancelled")
            run = self.create(owner, payload)
        return {"intent": decision["intent"], "taskId": task["id"], "run": run}

    @conversation_entry
    def create(self, owner, body):
        with self.lock:
            if self.closed:
                raise GatewayError("服务正在关闭。", 503)
        search_mode = self._search_mode(body)
        mode = body.get("mode", "create")
        if mode not in {"create", "invoke"}:
            raise GatewayError("运行模式必须为 create 或 invoke。", 400)
        key = text(body.get("idempotencyKey"), "请求标识", 150)
        inputs = body.get("input", {})
        if not isinstance(inputs, dict) or len(canonical(inputs)) > 40000 or any(name in inputs for name in ("filePath", "filePaths", "outputDir")):
            raise GatewayError("输入参数格式不正确，不能指定宿主或沙箱路径。", 400)
        file_ids = body.get("fileIds", [])
        metadata = self._metadata(owner, file_ids)
        if body.get("taskId"):
            self.workbench.get_task(owner, body["taskId"])
        baseline = None
        if body.get('baseAgentRef') and body.get('revisionOf'):
            raise GatewayError('请选择已有应用或已有运行作为改编来源。', 400)
        if body.get('baseAgentRef'):
            if mode != 'create':
                raise GatewayError('改编应用必须重新生成并预览。', 400)
            baseline = {'spec': self.agent(owner, str(body['baseAgentRef']))}
        if body.get("revisionOf"):
            baseline = self.repository.get(owner, body["revisionOf"])
            if not baseline.get("spec") or baseline["status"] not in TERMINAL | {"awaiting_confirmation", "awaiting_input"}:
                raise GatewayError("请先停止当前运行再修改流程。", 409)
            if mode != "create":
                raise GatewayError("修改流程必须重新生成并预览。", 400)
        spec = self.agent(owner, str(body.get("agentRef") or "")) if mode == "invoke" else None
        if spec:
            validate_input(spec["inputSchema"], inputs)
            validate_dependencies(spec, getattr(self.workbench, 'capabilities', None), owner)
        goal = text(body.get("goal") or (spec or {}).get("goal"), "任务目标", 10000)
        doc = {"schemaVersion": "1", "id": uid(), "taskId": body.get("taskId", ""), "mode": mode, "goal": goal,
               "spec": spec, "input": inputs, "searchMode": search_mode, "files": metadata, "allowedFileIds": list(dict.fromkeys(file_ids)),
               "status": "queued" if spec else "generating", "steps": [], "artifacts": [], "approvals": [],
               "executionPerformed": False, "output": None, "error": None, "feedback": [],
               "executionBasis": "direct_request" if spec else None,
               "revisionOf": body.get("revisionOf"), "baselineSpec": baseline["spec"] if baseline else None,
               "baseAgentRef": body.get('baseAgentRef'), "capabilityRefs": body.get('capabilityRefs', [])}
        request = {k: body.get(k) for k in ("mode", "goal", "agentRef", "input", "fileIds", "taskId", "revisionOf", "searchMode", "baseAgentRef", "capabilityRefs")}
        doc, fresh = self.repository.create(owner, request, doc, key)
        if fresh:
            self._launch(owner, doc["id"], self._execute if spec else self._generate)
        return self.repository.get(owner, doc["id"])

    def _launch(self, owner, run_id, target):
        with self.lock:
            if self.closed:
                raise GatewayError("服务正在关闭。", 503)
            if (owner, run_id) in self.active:
                return
            if len(self.active) >= 2:
                self._fail(owner, run_id, GatewayError("已有任务正在运行，请稍后重新发起。", 409, "capacity"))
                return
            control = {"stop": threading.Event(), "response": None}
            self.active[(owner, run_id)] = control
            def work():
                try:
                    target(owner, run_id, control)
                except Exception as error:
                    self._fail(owner, run_id, error)
                finally:
                    if control.get("timer"):
                        control["timer"].cancel()
                    if control["stop"].is_set():
                        def final_cancel(doc):
                            if doc["status"] not in TERMINAL:
                                doc["status"] = "cancelled"
                                doc["completedAt"] = now()
                                return [("run.cancelled", {})]
                            return []
                        self.repository.mutate(owner, run_id, final_cancel)
                    with self.lock:
                        self.active.pop((owner, run_id), None)
                        if not self.closed and not control["stop"].is_set() and self.repository.get(owner, run_id)["status"] == "queued":
                            # An approval can arrive before the planner/authorization
                            # worker has returned. Hand off only after its slot is released.
                            self._launch(owner, run_id, self._execute)
            thread = threading.Thread(target=work, daemon=True, name="agent-" + run_id[:8])
            control["thread"] = thread
            thread.start()

    @staticmethod
    def _on_response(control):
        def receive(response):
            control["response"] = response
            if response is not None and control["stop"].is_set():
                response.abort()
        return receive

    def _generate(self, owner, run_id, control):
        doc = self.repository.get(owner, run_id)
        metadata = {"input": doc["input"], "files": doc["files"]}
        selected = doc.get('capabilityRefs') or []
        if not isinstance(selected, list):
            raise GatewayError('所选能力必须是列表。', 400)
        selected = copy.deepcopy(selected)
        if doc.get("baselineSpec"):
            metadata["baselineSpec"] = doc["baselineSpec"]
            for item in doc['baselineSpec']['tools'] + doc['baselineSpec']['skills']:
                if 'capabilityRef' in item and not any(r.get('assetId') == item['capabilityRef']['assetId'] for r in selected if isinstance(r, dict)):
                    selected.append({key: item['capabilityRef'][key] for key in ('assetId', 'versionId')})
        components = [c for c in component_catalog(getattr(self.workbench, 'capabilities', None), owner, selected, snapshots=True) if c['available']]
        if not selected:
            # Automatic discovery is bounded; an explicit selection is never
            # silently dropped to fit a prompt budget.
            bounded, used = [], 0
            for component in components:
                size = len(canonical(component))
                if len(bounded) >= 24 or used + size > 60000:
                    continue
                bounded.append(component)
                used += size
            components = bounded
        metadata['components'] = components
        spec = self.planner.generate(doc["goal"], metadata, control["stop"], self._on_response(control))
        spec = validate_agent_spec(resolve_references(spec, self.workbench.runtime.tools, self.workbench.runtime.skills))
        validate_dependencies(spec, getattr(self.workbench, 'capabilities', None), owner)
        spec["id"], spec["version"], spec["origin"] = uid(), "1", "generated"
        if doc.get("baselineSpec") and not doc.get('baseAgentRef') and doc["baselineSpec"].get("origin") != "preset":
            spec["id"] = doc["baselineSpec"]["id"]
            spec["version"] = "v-" + uid()[:8]
        missing = missing_inputs(spec["inputSchema"], doc["input"])
        if not missing:
            validate_input(spec["inputSchema"], doc["input"])
        def ready(current):
            if current["status"] != "generating" or control["stop"].is_set():
                return []
            current["spec"] = spec
            current["status"] = "awaiting_input" if missing else "awaiting_confirmation"
            current["missingInputs"] = missing
            current["bindingDigest"] = self._binding(current)
            if missing:
                return [("run.input_required", {"missingInputs": missing})]
            approval = self._approval("execute", current["bindingDigest"], reason="请确认此流程后开始实际运行。")
            current["approvals"].append(approval)
            return [("agent.plan_ready", {"validationScope": "structure_only", "spec": spec}), ("approval.required", {"approval": approval})]
        doc = self.repository.mutate(owner, run_id, ready)
        if doc["status"] in {"awaiting_input", "awaiting_confirmation"}:
            self._message(owner, doc["taskId"], "assistant", "请补充必要参数：" + "、".join(missing) if missing else "已生成应用流程。当前仅展示设计，尚未执行；请查看流程并确认。", agentRunId=run_id)

    def supply_input(self, owner, run_id, body):
        values = body.get("input")
        if not isinstance(values, dict):
            raise GatewayError("请提供参数对象。", 400)
        if any(name in values for name in ("filePath", "filePaths", "outputDir")):
            raise GatewayError("不能指定执行路径。", 400)
        extra_files = self._metadata(owner, body.get("fileIds", []))
        def update(doc):
            if doc["status"] != "awaiting_input":
                raise GatewayError("当前运行不在等待参数。", 409)
            merged = doc["input"] | values
            validate_input(doc["spec"]["inputSchema"], merged)
            doc["input"], doc["missingInputs"] = merged, []
            known = {f["id"] for f in doc["files"]}
            self._check_file_total(known, [f["id"] for f in extra_files])
            doc["files"].extend(f for f in extra_files if f["id"] not in known)
            doc["allowedFileIds"] = list(dict.fromkeys(doc["allowedFileIds"] + [f["id"] for f in extra_files]))
            doc["status"] = "awaiting_confirmation"
            doc["bindingDigest"] = self._binding(doc)
            approval = self._approval("execute", doc["bindingDigest"], reason="参数已补全，请确认流程。")
            doc["approvals"].append(approval)
            return [("agent.plan_ready", {"validationScope": "structure_only", "spec": doc["spec"]}), ("approval.required", {"approval": approval})]
        return self.repository.mutate(owner, run_id, update)

    def approve(self, owner, run_id, approval_id, body):
        decision = body.get("decision")
        if decision not in {"approve", "reject"}:
            raise GatewayError("确认决定必须为 approve 或 reject。", 400)
        text(body.get("idempotencyKey"), "确认请求标识", 150)
        start = False
        def change(doc):
            nonlocal start
            approval = next((x for x in doc["approvals"] if x["id"] == approval_id), None)
            if not approval or body.get("bindingDigest") != approval["bindingDigest"]:
                raise GatewayError("确认版本不匹配。", 409)
            desired = "approved" if decision == "approve" else "rejected"
            if approval["status"] != "pending":
                if approval["status"] == desired:
                    return []
                raise GatewayError("该确认已处理。", 409)
            if approval["kind"] == "save":
                if decision == "approve":
                    raise GatewayError("请使用保存个人应用接口。", 400)
                approval.update(status="rejected", decidedAt=now())
                return [("agent.save_declined", {})]
            expected = "awaiting_confirmation" if approval["kind"] == "execute" else "awaiting_authorization"
            if doc["status"] != expected:
                raise GatewayError("运行状态已变化。", 409)
            if approval["kind"] == "execute" and self._binding(doc) != approval["bindingDigest"]:
                raise GatewayError("方案或输入已变化，请重新确认。", 409)
            approval.update(status=desired, decidedAt=now())
            if decision == "reject":
                doc["status"] = "cancelled"
                return [("run.cancelled", {"reason": "用户拒绝"})]
            if approval["kind"] == "access":
                # This read/fingerprint is now covered by the user's explicit access decision.
                self._check_file_total(doc["allowedFileIds"], approval["fileIds"])
                granted_files = self._metadata(owner, approval["fileIds"])
                doc["allowedFileIds"] = list(dict.fromkeys(doc["allowedFileIds"] + approval["fileIds"]))
                known = {f["id"] for f in doc["files"]}
                doc["files"].extend(f for f in granted_files if f["id"] not in known)
            else:
                doc["executionBasis"] = "preview_approval"
            doc["status"] = "queued"
            start = True
            return [("approval.decided", {"approvalId": approval_id, "decision": decision})]
        doc = self.repository.mutate_request(owner, run_id, "approval", body["idempotencyKey"],
            {"runId": run_id, "approvalId": approval_id, "decision": decision, "bindingDigest": body.get("bindingDigest")}, change)
        if start:
            self._launch(owner, run_id, self._execute)
        return self.repository.get(owner, run_id)

    @staticmethod
    def _resource_ids(spec, inputs, *, include_primary=True):
        names = list(spec["permissions"].get("files", []))
        for tool in spec["tools"]:
            names.extend(tool["permissions"].get("files", []))
        if include_primary and inputs.get("fileId"):
            names.append("fileId")
        found = []
        for name in names:
            value = inputs.get(name, name)
            if not isinstance(value, str):
                raise GatewayError("文件参数必须为文件 ID。", 400)
            identifier(value, "文件 ID")
            if value not in found:
                found.append(value)
        return found

    def _authorize_resources(self, owner, run_id, doc):
        needed = [x for x in self._resource_ids(doc["spec"], doc["input"]) if x not in doc["allowedFileIds"]]
        if not needed:
            return True
        self._check_file_total(doc["allowedFileIds"], needed)
        metadata = self._metadata(owner, needed, fingerprint=False)
        binding = digest({"runId": run_id, "spec": doc["spec"], "input": doc["input"], "extraFiles": metadata})
        approval = self._approval("access", binding, fileIds=needed, files=metadata, reason="该应用需要访问以下未包含在本次输入授权中的文件。")
        def wait(current):
            if current["status"] != "queued":
                return []
            current["status"] = "awaiting_authorization"
            current["approvals"].append(approval)
            return [("approval.required", {"approval": approval})]
        self.repository.mutate(owner, run_id, wait)
        return False

    def _execute(self, owner, run_id, control):
        doc = self.repository.get(owner, run_id)
        if doc["status"] != "queued" or control["stop"].is_set():
            return
        validate_input(doc["spec"]["inputSchema"], doc["input"])
        context_spec = validate_dependencies(doc['spec'], getattr(self.workbench, 'capabilities', None), owner, with_context=True)
        from ..workbench.retired_capabilities import check_spec
        check_spec(doc['spec'])
        if not self._authorize_resources(owner, run_id, doc):
            return
        def expire():
            control["timedOut"] = True
            control["stop"].set()
            if control["response"]:
                control["response"].abort()
        control["timer"] = threading.Timer(float(self.config.get("AGENT_RUN_TIMEOUT_SECONDS", 180)), expire)
        control["timer"].daemon = True
        control["timer"].start()
        doc = self.repository.get(owner, run_id)
        files = self._load_files(owner, doc["files"])
        def begin(current):
            if current["status"] != "queued":
                raise GatewayError("运行已经处理。", 409)
            current["status"] = "running"
            current["startedAt"] = now()
            current["loadedSkills"] = [{"id": s["id"], "version": s["version"], "sha256": hashlib.sha256(s["content"].encode()).hexdigest(),
                **({'capabilityRef': s['capabilityRef'], 'resources': [{key: value for key, value in resource.items() if key != 'content'}
                    for resource in s.get('resources', [])]} if 'capabilityRef' in s else {})} for s in context_spec['skills']]
            return [("run.started", {})]
        doc = self.repository.mutate(owner, run_id, begin)
        outputs, explanation, registered_counts = {}, "", {}
        for step in doc["spec"]["pipeline"]:
            if control["stop"].is_set():
                raise GatewayError("用户已停止运行。", 409, "cancelled")
            if time.monotonic() - control.setdefault("began", time.monotonic()) > 180:
                raise GatewayError("本次运行超过 180 秒限制。", 504, "run_timeout")
            def start_step(current):
                if current["status"] != "running":
                    raise GatewayError("运行已停止。", 409, "cancelled")
                current["steps"].append({"id": step["id"], "kind": step["kind"], "status": "running", "startedAt": now()})
                return [("step.started", {"stepId": step["id"]})]
            self.repository.mutate(owner, run_id, start_step)
            if step["kind"] == "tool":
                tool = next(x for x in doc["spec"]["tools"] if x["id"] == step["tool"])
                refs = step["inputFrom"]
                refs = refs if isinstance(refs, list) else [refs]
                values = [copy.deepcopy(doc["input"] if ref == "run.input" else outputs[ref.split(".")[1]]) for ref in refs]
                payload = values[0] if len(values) == 1 else {"inputs": values}
                if not isinstance(payload, dict):
                    raise GatewayError("工具输入必须为 JSON 对象。", 400)
                validate_input(tool["inputSchema"], payload)
                if 'capabilityRef' in tool:
                    result, artifacts, detail = self._execute_library_tool(owner, run_id, doc, tool, payload, files, control, step)
                    outputs[step['id']] = result['result']
                elif "registeredTool" in tool:
                    from ..workbench.tool_registry import ToolContext
                    name = tool["registeredTool"]
                    registry = self.workbench.runtime.tools
                    entry = registry.get(name)
                    if entry is None or canonical(entry.parameters) != canonical(tool["inputSchema"]):
                        raise GatewayError("平台工具接口已变化，请重新生成并确认流程。", 409, "registered_tool_changed")
                    blocked = {"search"} if doc.get("searchMode") == "off" else set()
                    if not registry.is_available(name, blocked):
                        raise GatewayError("该平台工具在本轮不可用，请检查网页搜索模式或工具配置。", 403, "registered_tool_unavailable")
                    # No model-generated code, file bytes, host paths or credentials
                    # cross this boundary. Only the approved JSON arguments do.
                    self.repository.mutate(owner, run_id, lambda current: current.update(executionPerformed=True))
                    count = registered_counts.get(name, 0)
                    registered_counts[name] = count + 1
                    outcome = registry.execute(name, payload, ToolContext(
                        control["stop"], self._on_response(control), lambda _event: None,
                        query=doc["goal"]), call_count=count, blocked_capabilities=blocked)
                    result = {"result": {**outcome.content}, "files": {}, "logs": ""}
                    if outcome.sources is not None:
                        result["result"]["sources"] = outcome.sources
                    validate_input(tool["outputSchema"], result["result"])
                    outputs[step["id"]] = result["result"]
                    artifacts = []
                    detail = {"toolId": tool["id"], "toolVersion": tool["version"],
                              "registeredTool": name, "output": result["result"], "logs": ""}
                else:
                    result, artifacts, detail = self._execute_python_tool(owner, run_id, doc, tool, payload, files, control, step)
                    outputs[step["id"]] = result["result"]
            else:
                context_spec = validate_dependencies(doc['spec'], getattr(self.workbench, 'capabilities', None), owner, with_context=True)
                explanation = self.planner.explain(context_spec, {"input": doc["input"], "files": doc["files"]}, outputs, control["stop"], self._on_response(control))
                outputs[step["id"]] = {"explanation": explanation}
                artifacts, detail = [], {"output": outputs[step["id"]], "skillIds": [s["id"] for s in doc["spec"]["skills"]]}
            if control["stop"].is_set():
                raise GatewayError("用户已停止运行。", 409, "cancelled")
            def finish_step(current):
                if current["status"] != "running":
                    raise GatewayError("运行已停止。", 409, "cancelled")
                current["executionPerformed"] = True
                current["artifacts"].extend(artifacts)
                current["steps"][-1].update(status="succeeded", completedAt=now(), **detail)
                return [("step.completed", {"stepId": step["id"], "artifacts": artifacts})]
            self.repository.mutate(owner, run_id, finish_step)
        def complete(current):
            if current["status"] != "running":
                raise GatewayError("运行已停止。", 409, "cancelled")
            csv = next((a["id"] for a in current["artifacts"] if a["name"].lower().endswith(".csv")), None)
            output = {"explanation": explanation, "stepOutputs": outputs, "artifactIds": [a["id"] for a in current["artifacts"]]}
            if csv:
                output["csvArtifactId"] = csv
            validate_input(current["spec"]["outputSchema"], output)
            current.update(status="succeeded", output=output, completedAt=now(), verificationScope="this_input_only")
            return [("run.completed", {"output": output})]
        doc = self.repository.mutate(owner, run_id, complete)
        self._message(owner, doc["taskId"], "assistant", explanation + "\n\n可以反馈需要修改的地方，或选择保存为个人应用。", agentRunId=run_id)

    def _execute_library_tool(self, owner, run_id, doc, tool, payload, files, control, step):
        from .library_capabilities import admitted
        library = self.workbench.capabilities
        admitted(library, owner, tool['capabilityRef'], 'tool')
        permitted = self._resource_ids({'permissions': tool['permissions'], 'tools': []}, doc['input'], include_primary=False)
        # Pass the already fingerprinted bytes, not a second read of mutable files.
        names = {f['id']: f['name'] for f in doc['files']}
        tool_files = {key + Path(names[key]).suffix: files[key] for key in permitted if key in files}
        self.repository.mutate(owner, run_id, lambda current: current.update(executionPerformed=True))
        invocation = library.invoke(owner, {**tool['capabilityRef'], 'input': payload, 'fileIds': [],
            'taskId': doc.get('taskId'), '_sourceSkillRefs': [
                {key: skill['capabilityRef'][key] for key in ('assetId', 'versionId')}
                for skill in doc['spec']['skills'] if 'capabilityRef' in skill and tool['id'] in skill.get('requiredTools', [])
            ]}, stopped=control['stop'], task_files=tool_files)
        content = invocation['result']['content']
        validate_input(tool['outputSchema'], content)
        outputs = {item['name']: library.store.blob(owner, item['id']) for item in invocation.get('artifacts', [])}
        artifacts = self._store_artifacts(owner, run_id, step['id'], outputs)
        return {'result': content}, artifacts, {'toolId': tool['id'], 'toolVersion': tool['version'],
            'capabilityRef': tool['capabilityRef'], 'invocationId': invocation['invocationId'], 'output': content, 'logs': ''}

    def _execute_python_tool(self, owner, run_id, doc, tool, payload, files, control, step):
        permitted = self._resource_ids({"permissions": tool["permissions"], "tools": []}, doc["input"], include_primary=False)
        tool_files = {file_id: files[file_id] for file_id in permitted if file_id in files}
        payload["filePaths"] = {file_id: "/input/" + file_id for file_id in tool_files}
        if doc["input"].get("fileId") in tool_files:
            payload["filePath"] = payload["filePaths"][doc["input"]["fileId"]]
        payload["outputDir"] = "/output"
        self.repository.mutate(owner, run_id, lambda current: current.update(executionPerformed=True))
        result = self.executor.execute(tool["code"], payload, tool_files, control["stop"])
        validate_input(tool["outputSchema"], result["result"])
        artifacts = self._store_artifacts(owner, run_id, step["id"], result.get("files", {}))
        declared = result["result"].get("artifacts", []) if isinstance(result["result"], dict) else []
        if declared and (not isinstance(declared, list) or any(name not in result.get("files", {}) for name in declared)):
            raise GatewayError("工具声明的输出文件不存在。", 502, "missing_artifact")
        detail = {"toolId": tool["id"], "toolVersion": tool["version"], "codeSha256": hashlib.sha256(tool["code"].encode()).hexdigest(), "output": result["result"], "logs": result.get("logs", "")[:8000]}
        return result, artifacts, detail

    def _store_artifacts(self, owner, run_id, step_id, files):
        result = []
        if not isinstance(files, dict) or len(files) > 16:
            raise GatewayError("输出文件数量超限。", 502)
        for name, data in files.items():
            if not isinstance(name, str) or Path(name).name != name or name in {".", ".."} or any(c in name for c in "/\\:\x00") or len(name) > 150:
                raise GatewayError("输出文件名不正确。", 502)
            if not isinstance(data, bytes) or len(data) > 2 * 1024 * 1024:
                raise GatewayError("输出文件过大。", 502)
            artifact_id = uid()
            md5 = hashlib.md5(data, usedforsecurity=False).hexdigest()
            instant = datetime.now(timezone(timedelta(hours=8)))
            stamp = instant.strftime("%Y%m%d_%H%M%S_") + f"{instant.microsecond // 1000:03d}"
            original = Path(name)
            # Keep the tool protocol's original name, and disambiguate exports
            # even when identical tools finish within the same millisecond.
            display_name = f"{stamp}_{original.stem}_{md5[:8]}_{artifact_id.replace('-', '')[:8]}{original.suffix}"
            target = self.root / artifact_id
            target.write_bytes(data)
            result.append({"id": artifact_id, "stepId": step_id, "name": display_name, "originalName": name, "size": len(data), "md5": md5, "sha256": hashlib.sha256(data).hexdigest(), "mediaType": "text/csv" if name.lower().endswith(".csv") else "application/octet-stream", "createdAt": instant.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")})
        return result

    def _fail(self, owner, run_id, error):
        from .executor import SandboxError
        with self.lock:
            control = self.active.get((owner, run_id))
            stopped = control is not None and control["stop"].is_set()
        def fail(current):
            if current["status"] in TERMINAL:
                return []
            timed_out = bool(control and control.get("timedOut"))
            code = "run_timeout" if timed_out else "cancelled" if stopped else (getattr(error, "code", None) or "execution_error")
            message = "本次运行超过总时间限制。" if timed_out else "用户已停止运行。" if stopped else (str(error) if isinstance(error, (GatewayError, SandboxError)) else "运行失败，请检查流程或工具代码。")
            current["status"] = "cancelled" if code == "cancelled" else "failed"
            current["error"] = {"code": code, "message": message}
            if current["steps"] and current["steps"][-1]["status"] == "running":
                current["steps"][-1].update(status=current["status"], error=message)
                if isinstance(error, SandboxError):
                    current["steps"][-1]["logs"] = error.logs[-8000:]
                current["error"]["stepId"] = current["steps"][-1]["id"]
            current["completedAt"] = now()
            return [("run.cancelled" if code == "cancelled" else "run.failed", {"error": current["error"]})]
        doc = self.repository.mutate(owner, run_id, fail)
        self._message(owner, doc["taskId"], "assistant", (doc.get("error") or {}).get("message", "运行已结束。"), agentRunId=run_id)

    def cancel(self, owner, run_id):
        with self.lock:
            control = self.active.get((owner, run_id))
            if control:
                control["stop"].set()
                if control["response"]:
                    control["response"].abort()
        def cancel_doc(doc):
            if doc["status"] in TERMINAL:
                return []
            doc["status"] = "cancelling" if control else "cancelled"
            doc["error"] = {"code": "cancelled", "message": "用户已取消。"}
            for approval in doc["approvals"]:
                if approval["status"] == "pending":
                    approval["status"] = "rejected"
            for step in doc["steps"]:
                if step["status"] == "running":
                    step["status"] = "cancelled"
            return [("run.cancelling" if control else "run.cancelled", {})]
        doc = self.repository.mutate(owner, run_id, cancel_doc)
        if control:
            control["thread"].join(timeout=2)
        return self.repository.get(owner, run_id)

    def feedback(self, owner, run_id, body):
        content = text(body.get("message"), "反馈", 5000)
        def update(doc):
            if doc["status"] not in TERMINAL:
                raise GatewayError("请等待运行结束再反馈。", 409)
            doc["feedback"].append({"message": content, "at": now()})
            if doc["status"] == "succeeded" and not any(a["kind"] == "save" for a in doc["approvals"]):
                approval = self._approval("save", digest(doc["spec"]), reason="是否将本次成功使用的定义保存为个人应用？")
                doc["approvals"].append(approval)
                return [("approval.required", {"approval": approval})]
            return [("feedback.received", {})]
        return self.repository.mutate(owner, run_id, update)

    def save(self, owner, body):
        run_id = text(body.get("runId"), "运行 ID", 100)
        def ensure_approval(doc):
            if doc["status"] != "succeeded":
                raise GatewayError("仅能保存成功运行的应用。", 409)
            if not any(a["kind"] == "save" and a["status"] in {"pending", "approved"} for a in doc["approvals"]):
                approval = self._approval("save", digest(doc["spec"]), reason="用户明确选择接受结果并保存应用。")
                doc["approvals"].append(approval)
                doc["feedback"].append({"message": "接受当前结果并保存此应用。", "at": now()})
                return [("approval.required", {"approval": approval})]
            return []
        doc = self.repository.mutate(owner, run_id, ensure_approval)
        approval = next(a for a in reversed(doc["approvals"]) if a["kind"] == "save" and a["status"] in {"pending", "approved"})
        from .asset_legacy import migrate_pipeline
        def save_modern(db, spec):
            modern = self.workbench.asset_apps
            definition = migrate_pipeline(modern, owner, spec)
            return modern._save_in_transaction(db, owner, {'definition': definition},
                provenance={'kind': 'legacy_run_acceptance', 'runId': run_id,
                            'agentRef': spec['id'] + '@' + spec['version']})
        return self.repository.save(owner, run_id, body.get("approvalId") or approval["id"],
                                    body.get("bindingDigest") or approval["bindingDigest"],
                                    text(body.get("idempotencyKey"), "请求标识", 150), save_modern=save_modern)

    def artifact(self, owner, artifact_id):
        identifier(artifact_id)
        item = self.repository.artifact(owner, artifact_id)
        if item:
            target = self.root / artifact_id
            if target.is_file():
                return target, item
        raise GatewayError("产物不存在或无权访问。", 404)

    def close(self):
        with self.lock:
            self.closed = True
            controls = list(self.active.items())
            dispatch_controls = list(self.dispatch_active.values())
        for control in dispatch_controls:
            control["stop"].set()
            if control["response"]:
                control["response"].abort()
        for (owner, run_id), control in controls:
            control["stop"].set()
            if control["response"]:
                control["response"].abort()
        for _, control in controls:
            control["thread"].join(timeout=3)
