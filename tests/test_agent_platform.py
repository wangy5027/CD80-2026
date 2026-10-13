"""Platform lifecycle/ownership tests plus a real WASI integration path."""

import copy
import hashlib
import io
import json
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from backend.app import create_app
from backend.app.modules.agent_platform.contracts import digest, validate_agent_spec
from backend.app.modules.agent_platform.executor import SandboxExecutor
from backend.app.modules.agent_platform.planner import Planner
from backend.app.modules.agent_platform.repository import Repository


CSV_CODE = '''import csv
from decimal import Decimal
def run(payload):
    totals = {}
    with open(payload["filePath"], newline="", encoding="utf-8-sig") as stream:
        rows = csv.DictReader(stream)
        if payload["groupBy"] not in (rows.fieldnames or []) or payload["valueColumn"] not in (rows.fieldnames or []):
            raise ValueError("missing column")
        count = 0
        for row in rows:
            key = row[payload["groupBy"]]
            if not key or not row[payload["valueColumn"]]:
                raise ValueError("empty cell")
            value = Decimal(row[payload["valueColumn"]])
            if not value.is_finite():
                raise ValueError("nonfinite")
            totals[key] = totals.get(key, Decimal("0")) + value
            count += 1
    with open("/output/summary.csv", "w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream, lineterminator="\\n")
        writer.writerow([payload["groupBy"], "total_amount"])
        for key in sorted(totals):
            writer.writerow([key, str(totals[key])])
    return {"artifacts": ["summary.csv"], "rowCount": count, "totals": {k: str(v) for k,v in totals.items()}}
'''


def fixture_spec():
    return {"schemaVersion": "1", "name": "CSV 分组求和", "goal": "汇总用户指定的数据列", "prompt": "引用实际结果，不捏造数据。",
            "inputSchema": {"type": "object", "required": ["fileId", "groupBy", "valueColumn"],
                            "properties": {"fileId": {"type": "string"}, "groupBy": {"type": "string"}, "valueColumn": {"type": "string"}}},
            "outputSchema": {"type": "object", "required": ["csvArtifactId", "explanation"],
                             "properties": {"csvArtifactId": {"type": "string"}, "explanation": {"type": "string"}}},
            "tools": [{"id": "aggregate", "code": CSV_CODE, "entrypoint": "run", "inputSchema": {"type": "object"}, "outputSchema": {"type": "object"}, "permissions": {"network": False, "files": ["fileId"]}}],
            "skills": [{"id": "reporting", "content": "必须说明实际分组和数据来源。", "requiredTools": ["aggregate"]}],
            "pipeline": [{"id": "aggregate", "kind": "tool", "tool": "aggregate", "inputFrom": "run.input"},
                         {"id": "explain", "kind": "model", "purpose": "解释结果", "inputFrom": ["steps.aggregate.output"]}],
            "permissions": {"network": False}}


class FixtureProvider:
    model = "fixture"
    def __init__(self):
        self.calls = []
        self.spec = fixture_spec()
        self.decision = {"intent": "chat"}
    def tokens(self, messages, model, stopped, on_response):
        self.calls.append(copy.deepcopy(messages))
        system = messages[0]["content"]
        if "分类器" in system:
            answer = json.dumps(self.decision, ensure_ascii=False)
        elif "生成一个通用" in system:
            answer = json.dumps(self.spec, ensure_ascii=False)
        else:
            values = json.loads(messages[1]["content"])["stepOutputs"]
            answer = "实际汇总结果：" + json.dumps(values, ensure_ascii=False)
        for start in range(0, len(answer), 127):
            yield answer[start:start + 127]


class FakeExecutor:
    def __init__(self):
        self.calls = []
    def status(self):
        return {"available": True, "runtime": "test-double"}
    def execute(self, code, payload, files, stopped):
        self.calls.append((code, copy.deepcopy(payload), files))
        return {"result": {"artifacts": ["summary.csv"], "totals": {"A": "15", "B": "7"}}, "files": {"summary.csv": b"category,total_amount\nA,15\nB,7\n"}, "logs": ""}


class AgentPlatformTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cd80-platform-")
        self.app = create_app({"TESTING": True, "DATA_DIR": self.temp.name, "GITEE_AI_API_KEY": "test-only", "IMPORT_LEGACY_HISTORY": False})
        self.client = self.app.test_client()
        response = self.client.post("/desktop/api/register", json={"email": "agent@example.test", "password": "long-password"})
        self.owner = response.json["user"]["id"]
        self.platform = self.app.extensions["agent_platform"]
        # This suite injects its own catalog fixtures; preset wiring has its own tests.
        self.platform.presets.clear()
        self.provider = FixtureProvider()
        self.platform.planner = Planner(self.provider)
        self.executor = FakeExecutor()
        self.platform.executor = self.executor
        self.file = self.upload(b"category,amount\nA,10\nB,7\nA,5\n")

    def tearDown(self):
        self.app.extensions["workbench_service"].close()
        self.temp.cleanup()

    def upload(self, data):
        response = self.client.post("/desktop/api/files", data={"file": (io.BytesIO(data), "sales.csv")}, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 201)
        return response.json

    def wait(self, run_id, expected, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            doc = self.platform.repository.get(self.owner, run_id)
            if doc["status"] in expected:
                return doc
            if doc["status"] in {"failed", "cancelled", "interrupted"} and doc["status"] not in expected:
                self.fail(str(doc))
            time.sleep(.025)
        self.fail("run timeout: " + str(doc))

    def new_run(self, **changes):
        body = {"mode": "create", "goal": "生成按 category 对 amount 求和的应用", "input": {"fileId": self.file["id"], "groupBy": "category", "valueColumn": "amount"}, "fileIds": [self.file["id"]], "idempotencyKey": str(uuid4())}
        body.update(changes)
        response = self.client.post("/desktop/api/agent-runs", json=body)
        self.assertEqual(response.status_code, 202, response.json)
        return self.wait(response.json["id"], {"awaiting_confirmation", "awaiting_input", "succeeded", "awaiting_authorization"})

    def approve(self, doc, kind="execute", decision="approve"):
        item = next(a for a in doc["approvals"] if a["kind"] == kind and a["status"] == "pending")
        url = f"/desktop/api/agent-runs/{doc['id']}/approvals/{item['id']}"
        payload = {"decision": decision, "bindingDigest": item["bindingDigest"], "idempotencyKey": item["id"]}
        response = self.client.post(url, json=payload)
        self.assertEqual(response.status_code, 200, response.json)
        return url, payload

    def test_preview_no_tools_no_file_content_and_real_skill_context(self):
        doc = self.new_run()
        self.assertEqual(self.executor.calls, [])
        self.assertFalse(doc["executionPerformed"])
        self.assertEqual(doc["files"][0]["md5"], self.file["md5"])
        self.assertEqual(json.loads(self.provider.calls[0][1]["content"])["files"][0]["md5"], self.file["md5"])
        self.assertNotIn("A,10", json.dumps(self.provider.calls))
        self.assertEqual(doc["status"], "awaiting_confirmation")
        url, payload = self.approve(doc)
        done = self.wait(doc["id"], {"succeeded"})
        self.assertEqual(len(self.executor.calls), 1)
        self.assertIn("Skill reporting@1", self.provider.calls[-1][0]["content"])
        self.assertTrue(done["loadedSkills"])
        self.assertEqual(self.client.post(url, json=payload).status_code, 200)
        self.assertEqual(len(self.executor.calls), 1)
        artifact = done["artifacts"][0]
        download = self.client.get("/desktop/api/agent-artifacts/" + artifact["id"])
        self.assertEqual(download.data, b"category,total_amount\nA,15\nB,7\n")
        self.assertEqual(artifact["originalName"], "summary.csv")
        self.assertEqual(artifact["md5"], hashlib.md5(download.data, usedforsecurity=False).hexdigest())
        self.assertEqual(artifact["sha256"], hashlib.sha256(download.data).hexdigest())
        self.assertIn(artifact["name"], download.headers["Content-Disposition"])
        self.assertEqual(done["steps"][0]["output"]["artifacts"], ["summary.csv"])
        download.close()

    def test_artifact_names_use_beijing_milliseconds_and_unique_run_identity(self):
        instant = datetime(2026, 10, 13, 1, 2, 3, 456789, tzinfo=timezone(timedelta(hours=8)))
        data = b"category,total_amount\nA,15\nB,7\n"
        with patch("backend.app.modules.agent_platform.service.datetime") as clock:
            clock.now.return_value = instant
            first = self.platform._store_artifacts(self.owner, "run-a", "aggregate", {"summary.csv": data})[0]
            second = self.platform._store_artifacts(self.owner, "run-b", "aggregate", {"summary.csv": data})[0]
            clock.now.assert_called_with(timezone(timedelta(hours=8)))
        prefix = "20261013_010203_456_summary_" + hashlib.md5(data, usedforsecurity=False).hexdigest()[:8] + "_"
        for item in (first, second):
            self.assertEqual(item["name"], prefix + item["id"].replace("-", "")[:8] + ".csv")
            self.assertEqual(item["originalName"], "summary.csv")
            self.assertEqual(item["createdAt"], "2026-10-12T17:02:03.456Z")
            self.assertEqual((self.platform.root / item["id"]).read_bytes(), data)
        self.assertNotEqual(first["name"], second["name"])

    def test_legacy_artifact_without_original_name_or_md5_still_downloads(self):
        doc = self.new_run()
        self.approve(doc)
        done = self.wait(doc["id"], {"succeeded"})
        artifact_id = done["artifacts"][0]["id"]
        def legacy(current):
            item = current["artifacts"][0]
            item["name"] = "summary.csv"
            item.pop("originalName")
            item.pop("md5")
        self.platform.repository.mutate(self.owner, doc["id"], legacy)
        response = self.client.get("/desktop/api/agent-artifacts/" + artifact_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"category,total_amount\nA,15\nB,7\n")
        self.assertIn("summary.csv", response.headers["Content-Disposition"])
        response.close()

    def test_reject_confirmation_never_executes(self):
        doc = self.new_run()
        self.approve(doc, decision="reject")
        self.assertEqual(self.platform.repository.get(self.owner, doc["id"])["status"], "cancelled")
        self.assertFalse(self.executor.calls)

    def test_missing_input_is_explicit_and_supplied_input_gets_preview(self):
        doc = self.new_run(input={"fileId": self.file["id"]})
        self.assertEqual(doc["status"], "awaiting_input")
        response = self.client.post(f"/desktop/api/agent-runs/{doc['id']}/input", json={"input": {"groupBy": "category", "valueColumn": "amount"}})
        self.assertEqual(response.json["status"], "awaiting_confirmation")
        self.assertFalse(self.executor.calls)

    def test_changed_binding_and_input_are_not_approved(self):
        doc = self.new_run()
        item = doc["approvals"][0]
        response = self.client.post(f"/desktop/api/agent-runs/{doc['id']}/approvals/{item['id']}", json={"decision": "approve", "bindingDigest": "stale", "idempotencyKey": "a"})
        self.assertEqual(response.status_code, 409)
        path = self.app.extensions["workbench_service"].store.directory(self.owner) / "files" / self.file["id"]
        path.write_bytes(b"changed data")
        self.approve(doc)
        failed = self.wait(doc["id"], {"failed"})
        self.assertEqual(failed["error"]["code"], "input_changed")
        self.assertFalse(self.executor.calls)

    def test_save_is_explicit_and_reuse_does_not_regenerate(self):
        doc = self.new_run()
        self.approve(doc)
        self.wait(doc["id"], {"succeeded"})
        self.assertFalse(self.client.get("/desktop/api/agents").json["agents"])
        result = self.client.post("/desktop/api/agents", json={"runId": doc["id"], "idempotencyKey": "save-1"})
        self.assertEqual(result.status_code, 201)
        again = self.client.post("/desktop/api/agents", json={"runId": doc["id"], "idempotencyKey": "save-1"})
        self.assertEqual(result.json, again.json)
        self.assertEqual(len(self.client.get("/desktop/api/agents").json["agents"]), 1)
        new_file = self.upload(b"category,amount\nA,1\nC,4\nC,6\n")
        before = len(self.provider.calls)
        repeated = self.new_run(mode="invoke", agentRef=result.json["agentRef"], input={"fileId": new_file["id"], "groupBy": "category", "valueColumn": "amount"}, fileIds=[new_file["id"]])
        self.assertEqual(repeated["status"], "succeeded")
        self.assertEqual(len(self.provider.calls), before + 1)
        self.assertEqual(self.executor.calls[-1][2], {new_file["id"]: b"category,amount\nA,1\nC,4\nC,6\n"})

    def test_declined_save_can_be_requested_later_without_running_again(self):
        doc = self.new_run()
        self.approve(doc)
        self.wait(doc["id"], {"succeeded"})
        response = self.client.post(f"/desktop/api/agent-runs/{doc['id']}/feedback", json={"message": "结果正确"})
        self.approve(response.json, kind="save", decision="reject")
        self.assertFalse(self.platform.repository.list_agents(self.owner))
        saved = self.client.post("/desktop/api/agents", json={"runId": doc["id"], "idempotencyKey": "save-later"})
        self.assertEqual(saved.status_code, 201, saved.json)
        self.assertEqual(len(self.executor.calls), 1)

    def test_extra_information_requires_authorization_before_access(self):
        extra = self.upload(b"extra sensitive input")
        self.provider.spec["permissions"]["files"] = [extra["id"]]
        self.provider.spec["tools"][0]["permissions"]["files"].append(extra["id"])
        doc = self.new_run()
        self.approve(doc)
        waiting = self.wait(doc["id"], {"awaiting_authorization"})
        self.assertFalse(self.executor.calls)
        self.approve(waiting, kind="access")
        self.wait(doc["id"], {"succeeded"})
        self.assertIn(extra["id"], self.executor.calls[-1][2])

    def test_tool_without_file_permission_receives_no_ambient_input(self):
        self.provider.spec["tools"][0]["permissions"]["files"] = []
        doc = self.new_run()
        self.approve(doc)
        self.wait(doc["id"], {"succeeded"})
        _, payload, files = self.executor.calls[0]
        self.assertEqual(files, {})
        self.assertEqual(payload["filePaths"], {})
        self.assertNotIn("filePath", payload)

    def test_cross_account_all_objects_hidden(self):
        doc = self.new_run()
        self.approve(doc)
        doc = self.wait(doc["id"], {"succeeded"})
        other = self.app.test_client()
        other.post("/desktop/api/register", json={"email": "other@example.test", "password": "long-password"})
        self.assertEqual(other.get("/desktop/api/agent-runs/" + doc["id"]).status_code, 404)
        self.assertEqual(other.post("/desktop/api/agent-runs/" + doc["id"] + "/cancel").status_code, 404)
        self.assertEqual(other.get("/desktop/api/agent-artifacts/" + doc["artifacts"][0]["id"]).status_code, 404)
        self.assertEqual(other.post("/desktop/api/agents", json={"runId": doc["id"], "idempotencyKey": "steal"}).status_code, 404)

    def test_create_idempotency_conflict_and_events_are_read_only(self):
        doc = self.new_run(idempotencyKey="same")
        same = self.new_run(idempotencyKey="same")
        self.assertEqual(doc["id"], same["id"])
        response = self.client.post("/desktop/api/agent-runs", json={"mode": "create", "goal": "different", "input": {}, "fileIds": [], "idempotencyKey": "same"})
        self.assertEqual(response.status_code, 409)
        events = self.client.get(f"/desktop/api/agent-runs/{doc['id']}/events")
        self.assertIn(b"agent.plan_ready", events.data)
        events.close()
        self.assertFalse(self.executor.calls)

    def test_model_failure_and_cancellation_do_not_start_tools(self):
        self.provider.spec["pipeline"][0]["kind"] = "recursive"
        response = self.client.post("/desktop/api/agent-runs", json={"mode": "create", "goal": "bad", "input": {}, "fileIds": [], "idempotencyKey": "bad"})
        failed = self.wait(response.json["id"], {"failed"})
        self.assertTrue(failed["error"])
        self.assertFalse(self.executor.calls)
        self.provider.spec = fixture_spec()
        doc = self.new_run()
        self.client.post(f"/desktop/api/agent-runs/{doc['id']}/cancel")
        self.assertEqual(self.platform.repository.get(self.owner, doc["id"])["status"], "cancelled")
        self.assertFalse(self.executor.calls)

    def test_restart_retains_pending_confirmation_without_running(self):
        doc = self.new_run()
        restored = Repository(self.temp.name)
        self.assertEqual(restored.get(self.owner, doc["id"])["approvals"], doc["approvals"])
        self.assertFalse(self.executor.calls)

    def test_running_cancel_reaches_executor_and_prevents_explanation(self):
        entered, finished = threading.Event(), threading.Event()
        def blocking_execute(code, payload, files, stopped):
            entered.set()
            self.assertTrue(stopped.wait(5), "cancellation did not reach executor")
            finished.set()
            from backend.app.modules.agent_platform.executor import SandboxError
            raise SandboxError("cancelled", "cancelled")
        self.executor.execute = blocking_execute
        doc = self.new_run()
        self.approve(doc)
        self.assertTrue(entered.wait(3))
        start = time.monotonic()
        response = self.client.post(f"/desktop/api/agent-runs/{doc['id']}/cancel")
        self.assertEqual(response.status_code, 200)
        self.assertLess(time.monotonic() - start, 2)
        self.assertTrue(finished.is_set())
        done = self.wait(doc["id"], {"cancelled"})
        self.assertEqual(done["steps"][0]["status"], "cancelled")
        self.assertEqual(len(self.provider.calls), 1)

    def test_failed_explanation_keeps_completed_artifact_and_never_retries(self):
        from backend.app.modules.workbench.errors import GatewayError
        def fail_explanation(*args):
            raise GatewayError("synthetic model failure", 502)
        self.platform.planner.explain = fail_explanation
        doc = self.new_run()
        self.approve(doc)
        failed = self.wait(doc["id"], {"failed"})
        self.assertEqual(len(self.executor.calls), 1)
        self.assertEqual(failed["steps"][0]["status"], "succeeded")
        self.assertEqual(failed["steps"][1]["status"], "failed")
        self.assertEqual(len(failed["artifacts"]), 1)
        response = self.client.get("/desktop/api/agent-artifacts/" + failed["artifacts"][0]["id"])
        self.assertEqual(response.status_code, 200)
        response.close()
        self.assertEqual(self.client.post("/desktop/api/agents", json={"runId": doc["id"], "idempotencyKey": "failed-save"}).status_code, 409)

    def test_overall_deadline_interrupts_wait_and_is_failed_not_user_cancelled(self):
        from backend.app.modules.workbench.errors import GatewayError
        self.platform.config["AGENT_RUN_TIMEOUT_SECONDS"] = .1
        def blocked_explanation(spec, metadata, outputs, stopped, on_response):
            if not stopped.wait(2):
                raise AssertionError("run deadline did not interrupt explanation")
            raise GatewayError("transport stopped", 409)
        self.platform.planner.explain = blocked_explanation
        doc = self.new_run()
        self.approve(doc)
        failed = self.wait(doc["id"], {"failed"})
        self.assertEqual(failed["error"]["code"], "run_timeout")
        self.assertEqual(len(failed["artifacts"]), 1)

    def test_saved_preset_keeps_a_unique_dispatch_candidate(self):
        preset = fixture_spec()
        preset.update(id="preset-csv", version="1", origin="preset")
        self.platform.register_preset(preset)
        doc = self.new_run(mode="invoke", agentRef="preset-csv@1")
        result = self.client.post("/desktop/api/agents", json={"runId": doc["id"], "idempotencyKey": "save-preset"})
        self.assertEqual(result.status_code, 201)
        candidates = self.platform.list_agents(self.owner)
        self.assertEqual([a["agentRef"] for a in candidates], ["preset-csv@1"])
        self.assertTrue(candidates[0]["personal"])
        response = self.client.post("/desktop/api/agent-dispatch", json={"message": "解释概念", "fileIds": []})
        self.assertEqual(response.status_code, 200, response.json)

    def test_revision_creates_new_immutable_version_and_needs_confirmation(self):
        doc = self.new_run()
        revised = self.new_run(revisionOf=doc["id"], goal="增加说明要求")
        self.assertEqual(revised["status"], "awaiting_confirmation")
        self.assertEqual(revised["spec"]["id"], doc["spec"]["id"])
        self.assertNotEqual(revised["spec"]["version"], doc["spec"]["version"])
        self.assertEqual(self.platform.repository.get(self.owner, doc["id"])["spec"], doc["spec"])
        self.assertFalse(self.executor.calls)

    def test_approval_key_cannot_be_reused_for_different_run(self):
        first = self.new_run()
        second = self.new_run()
        self.approve(first)
        item = second["approvals"][0]
        response = self.client.post(f"/desktop/api/agent-runs/{second['id']}/approvals/{item['id']}", json={"decision": "approve", "bindingDigest": item["bindingDigest"], "idempotencyKey": first["approvals"][0]["id"]})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.platform.repository.get(self.owner, second["id"])["status"], "awaiting_confirmation")

    def test_dispatch_chat_clarify_and_create_use_same_task(self):
        response = self.client.post("/desktop/api/agent-dispatch", json={"message": "解释概念", "fileIds": []})
        self.assertEqual(response.json["intent"], "chat")
        task_id = response.json["taskId"]
        self.provider.decision = {"intent": "clarify", "question": "按哪一列分组？"}
        response = self.client.post("/desktop/api/agent-dispatch", json={"taskId": task_id, "message": "处理文件", "fileIds": [self.file["id"]]})
        self.assertEqual(response.json["intent"], "clarify")
        self.assertEqual(response.json["taskId"], task_id)
        self.provider.decision = {"intent": "create", "goal": "汇总", "input": {"groupBy": "category", "valueColumn": "amount", "fileId": self.file["id"]}}
        response = self.client.post("/desktop/api/agent-dispatch", json={"taskId": task_id, "message": "按category汇总amount", "fileIds": [self.file["id"]], "idempotencyKey": "dispatch"})
        doc = self.wait(response.json["run"]["id"], {"awaiting_confirmation"})
        self.assertEqual(doc["taskId"], task_id)

    def test_dispatch_replay_does_not_reclassify_or_duplicate_task(self):
        body = {"message": "解释概念", "fileIds": [], "idempotencyKey": "dispatch-once"}
        first = self.client.post("/desktop/api/agent-dispatch", json=body)
        second = self.client.post("/desktop/api/agent-dispatch", json=body)
        self.assertEqual(first.json, second.json)
        self.assertEqual(len(self.provider.calls), 1)
        conflict = self.client.post("/desktop/api/agent-dispatch", json={**body, "message": "different"})
        self.assertEqual(conflict.status_code, 409)

    def test_dispatch_application_keeps_project_and_checks_owner(self):
        project = self.client.post("/desktop/api/projects", json={"name": "CSV研究"}).json
        self.provider.decision = {"intent": "clarify", "question": "请说明分组列"}
        response = self.client.post("/desktop/api/agent-dispatch", json={"message": "研究数据", "projectId": project["id"], "idempotencyKey": "project-dispatch"})
        self.assertEqual(response.status_code, 200)
        task = self.client.get("/desktop/api/tasks/" + response.json["taskId"]).json
        self.assertEqual(task["projectId"], project["id"])
        bad = self.client.post("/desktop/api/agent-dispatch", json={"message": "研究数据", "projectId": "other-project", "idempotencyKey": "bad-project"})
        self.assertEqual(bad.status_code, 404)

    def test_dispatch_cancel_prevents_new_run(self):
        entered = threading.Event()
        def classify(message, files, candidates, history, stopped, on_response):
            entered.set()
            self.assertTrue(stopped.wait(3))
            return {"intent": "create", "goal": "must not create", "input": {}}
        self.platform.planner.classify = classify
        errors = []
        def call():
            try:
                self.platform.dispatch(self.owner, {"message": "new app", "idempotencyKey": "cancel-dispatch"})
            except Exception as error:
                errors.append(error)
        thread = threading.Thread(target=call)
        thread.start()
        self.assertTrue(entered.wait(3))
        response = self.client.post("/desktop/api/agent-dispatch/cancel-dispatch/cancel")
        self.assertEqual(response.status_code, 200)
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors[0].code, "cancelled")
        self.assertFalse(self.platform.repository.list_runs(self.owner))

    def test_extra_authorization_cannot_exceed_total_file_limit(self):
        file_ids = [self.file["id"]] + [self.upload(f"data-{index}".encode())["id"] for index in range(7)]
        extra = self.upload(b"ninth")["id"]
        self.provider.spec["permissions"]["files"] = [extra]
        doc = self.new_run(fileIds=file_ids)
        self.approve(doc)
        failed = self.wait(doc["id"], {"failed"})
        self.assertEqual(failed["error"]["code"], "too_many_files")
        self.assertFalse(self.executor.calls)

    def test_real_wasi_generated_tool_and_second_input(self):
        executor = SandboxExecutor({"AGENT_EXECUTION_TIMEOUT": 25})
        if not executor.status()["available"]:
            self.skipTest("verified WASI runtime not installed; separate acceptance must run after setup")
        self.platform.executor = executor
        doc = self.new_run()
        self.approve(doc)
        done = self.wait(doc["id"], {"succeeded"}, timeout=35)
        result = self.client.get("/desktop/api/agent-artifacts/" + done["artifacts"][0]["id"])
        self.assertEqual(result.data, b"category,total_amount\nA,15\nB,7\n")
        result.close()
        saved = self.client.post("/desktop/api/agents", json={"runId": doc["id"], "idempotencyKey": "real-save"}).json
        new_file = self.upload(b"category,amount\nA,1\nC,4\nC,6\n")
        doc = self.new_run(mode="invoke", agentRef=saved["agentRef"], input={"fileId": new_file["id"], "groupBy": "category", "valueColumn": "amount"}, fileIds=[new_file["id"]])
        result = self.client.get("/desktop/api/agent-artifacts/" + doc["artifacts"][0]["id"])
        self.assertEqual(result.data, b"category,total_amount\nA,1\nC,10\n")
        result.close()


if __name__ == "__main__":
    unittest.main()
