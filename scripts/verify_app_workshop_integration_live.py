"""Bounded real-model acceptance for App Workshop in a disposable account/database.

Run explicitly with --live. All examples are synthetic; the configured model is
used only for planning and the saved fixed explanation node. No production user
history, market data, files, or credentials are copied into the temporary DATA_DIR.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DESTINATION = ROOT / "data/integration-acceptance/app-workshop"


def stamp():
    return datetime.now(timezone.utc).isoformat()


def source_spec(identifier, name, code, properties, output_properties):
    inputs = {"type": "object", "properties": {key: {"type": "number"} for key in properties},
              "required": list(properties), "additionalProperties": False}
    outputs = {"type": "object", "properties": {key: {"type": "number"} for key in output_properties},
               "required": list(output_properties), "additionalProperties": False}
    return {"schemaVersion": "1", "id": identifier, "version": "1", "origin": "generated",
            "name": name, "description": "根据本次输入执行固定的金额计算。", "goal": name,
            "prompt": "只根据实际计算数值解释，不增加任何估计。", "inputSchema": inputs,
            "outputSchema": {"type": "object", "properties": {"explanation": {"type": "string"}},
                             "required": ["explanation"], "additionalProperties": False},
            "tools": [{"id": "calculate", "version": "1", "description": name,
                       "code": code, "entrypoint": "run", "inputSchema": inputs,
                       "outputSchema": outputs, "permissions": {"network": False, "files": []}}],
            "skills": [{"id": "explain", "version": "1", "content": "严格按实际工具结果解释金额，不擅自增加金融结论。",
                        "requiredTools": ["calculate"]}],
            "pipeline": [{"id": "calculate", "kind": "tool", "tool": "calculate", "inputFrom": "run.input"},
                         {"id": "explain", "kind": "model", "purpose": "解释实际计算结果",
                          "inputFrom": ["steps.calculate.output"]}],
            "permissions": {"network": False, "files": []}}


def require_response(response, status, stage):
    payload = response.get_json(silent=True)
    if response.status_code != status:
        raise RuntimeError(f"{stage}: HTTP {response.status_code}: {str(payload)[:1000]}")
    return payload


def wait_for_runs(assets, owner, run_ids, deadline_seconds):
    deadline = time.monotonic() + deadline_seconds
    records = []
    for run_id in run_ids:
        while True:
            run = assets.run(owner, run_id)
            if run["status"] not in {"queued", "running", "cancelling"} and run_id not in assets.active:
                records.append(run)
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("运行超过验收时间上限：" + run_id)
            time.sleep(.25)
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--timeout", type=int, default=300, help="每一组验证和运行的总等待秒数")
    args = parser.parse_args()

    from dotenv import dotenv_values
    from backend.app import create_app
    from backend.app.modules.agent_platform.contracts import digest, validate_agent_spec
    from backend.app.modules.agent_platform.planner import GENERATE_PROMPT

    env = dotenv_values(ROOT / ".env")
    model_config = {key: os.environ.get(key) or env.get(key) for key in
                    ("GITEE_AI_API_KEY", "GITEE_AI_BASE_URL", "GITEE_AI_MODEL")}
    if not model_config["GITEE_AI_API_KEY"]:
        parser.error("未配置模型密钥，未发起任何模型调用。")
    DESTINATION.mkdir(parents=True, exist_ok=True)
    report_path = DESTINATION / ("live-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json")
    report = {"startedAt": stamp(), "passed": False, "realModel": True, "realWasi": True,
              "syntheticData": True, "productionWrites": False, "productionUserDataRead": False,
              "model": model_config["GITEE_AI_MODEL"], "stages": [], "sourceDefinitions": [],
              "budget": {"planningCalls": 3, "verificationCases": 2, "newRunCases": 1,
                         "timeoutSeconds": args.timeout}}

    def record(stage, **details):
        report["stages"].append({"name": stage, "at": stamp(), **details})
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"stage": stage, **{k: v for k, v in details.items() if k in
                          {"status", "runStatus", "verificationStatus", "saveExecutionCount"}}},
                         ensure_ascii=False), flush=True)

    workbench = None
    with tempfile.TemporaryDirectory(prefix="cd80-app-workshop-live-") as folder:
        try:
            app = create_app({**model_config, "TESTING": True, "DATA_DIR": folder,
                              "IMPORT_LEGACY_HISTORY": False, "CAPABILITY_SKIP_PUBLIC_SEED": True,
                              "AGENT_RUNTIME_DIR": str(ROOT / "data/agent-runtime"),
                              "AGENT_MODEL_MAX_TOKENS": 8192, "AGENT_MODEL_REASONING_EFFORT": "low",
                              "GITEE_AI_READ_TIMEOUT_SECONDS": 90, "SUB_AGENT_TIMEOUT": 180})
            workbench = app.extensions["workbench_service"]
            platform = app.extensions["agent_platform"]
            workshop = app.extensions["app_workshop"]
            assets = app.extensions["asset_apps"]
            client = app.test_client()
            owner = require_response(client.post("/desktop/api/register", json={
                "email": "app-workshop-live@example.invalid", "password": "isolated-synthetic-password"}),
                200, "register")["user"]["id"]
            record("temporary-account-created", status="passed")

            originals = [
                source_spec("tax-amount", "税后金额计算", "def run(payload):\n    return {\"taxedAmount\": round(payload[\"amount\"] * (1 + payload[\"taxRate\"]), 2)}\n",
                            ("amount", "taxRate"), ("taxedAmount",)),
                source_spec("discount-amount", "折扣金额计算", "def run(payload):\n    return {\"finalAmount\": round(payload[\"amount\"] * (1 - payload[\"discountRate\"]), 2)}\n",
                            ("amount", "discountRate"), ("finalAmount",)),
            ]
            for spec in originals:
                checked = validate_agent_spec(spec)
                platform.repository.put_agent(owner, checked)
                report["sourceDefinitions"].append({"agentRef": checked["id"] + "@1",
                                                    "specDigest": digest(checked),
                                                    "toolCodeSha256": hashlib.sha256(checked["tools"][0]["code"].encode()).hexdigest(),
                                                    "pipelineKinds": [item["kind"] for item in checked["pipeline"]]})
            record("original-sources-ready", status="passed")

            requirement = ("请把我账号已有的「税后金额计算」和「折扣金额计算」两个应用的实际方法，"
                           "组合成一个新的可复用应用。每次输入 amount、taxRate、discountRate 三个 number。"
                           "先算 taxedAmount = round(amount * (1 + taxRate), 2)，再算 "
                           "finalAmount = round(taxedAmount * (1 - discountRate), 2)。"
                           "请把两个来源的公式组合到一个固定 Python 工具中，一次完成先税后折；"
                           "该工具必须返回同时含 taxedAmount 和 finalAmount 的对象，"
                           "其 outputSchema 必须声明这两个 number 字段；只需一个 tool 步骤和最后的 model 解释步骤。"
                           "例如输入 amount=100、taxRate=0.1、discountRate=0.2 时应分别得到 110 和 88。"
                           "只用标准库固定代码和真实步骤，不访问网络或文件，不运行后才保存。")
            analyzed = require_response(client.post("/desktop/api/app-workshop/sessions", json={
                "requirement": requirement, "idempotencyKey": "live-analysis"}), 201, "analyze")
            record("analyzed", status=analyzed["status"], recommendation=analyzed.get("recommendation"),
                   analysis=analyzed.get("analysis"))
            original_call = platform.planner._call
            def capture_generation(system, payload, *call_args, **call_kwargs):
                answer = original_call(system, payload, *call_args, **call_kwargs)
                if system == GENERATE_PROMPT:
                    report["generationRaw"] = answer
                    record("generation-response-received", status="received", chars=len(answer))
                return answer
            with patch.object(platform.planner, "_call", side_effect=capture_generation):
                created = require_response(client.post(f"/desktop/api/app-workshop/sessions/{analyzed['id']}/create", json={
                    "revision": analyzed["revision"], "idempotencyKey": "live-create"}), 200, "create")
            private = workshop.store.get(owner, analyzed["id"])
            source_refs = [source["appKey"] for source in created["appPreview"]["sourceApps"]]
            if set(source_refs) != {"tax-amount@1", "discount-amount@1"}:
                raise RuntimeError("组合规划没有选中两个原始真实来源：" + str(source_refs))
            delivered_sources = {item["appKey"]: item for item in private["appPreview"]["sourceApps"]}
            if any("specDigest" not in delivered_sources[key] for key in source_refs):
                raise RuntimeError("组合来源缺少精确实现摘要。")
            definition = private["_definition"]
            if definition["format"] != "cd80.app/2" or len(definition["nodes"]) < 2:
                raise RuntimeError("生成的新版 App 没有完整工具及解释步骤。")
            report["generatedSpec"] = private["_spec"]
            report["generatedDefinition"] = definition
            record("created-from-exact-sources", status="passed", sourceRefs=source_refs,
                   generatedSpecDigest=digest(private["_spec"]), nodeKinds=[node["kind"] for node in definition["nodes"]])

            # A prepared App is a versioned record, never a trial execution.
            counter = {"calls": 0}
            def forbid(*_args, **_kwargs):
                counter["calls"] += 1
                raise AssertionError("保存阶段触发了模型或工具执行")
            with patch.object(platform.executor, "execute", side_effect=forbid), \
                 patch.object(platform.planner, "_call", side_effect=forbid), \
                 patch.object(workbench.runtime.provider, "events", side_effect=forbid), \
                 patch.object(assets, "_launch", side_effect=forbid):
                saved = require_response(client.post(f"/desktop/api/app-workshop/sessions/{analyzed['id']}/save", json={
                    "revision": created["revision"], "appDigest": created["appPreview"]["appDigest"],
                    "idempotencyKey": "live-save"}), 200, "save")
            detail = saved["detail"]
            if (counter["calls"] or detail["verification"]["status"] != "unverified" or
                    detail["enabledVersionId"] is not None or assets.runs(owner, saved["appId"])):
                raise RuntimeError("保存阶段执行计数或未验证状态不正确。")
            record("saved-zero-execution", status="passed", saveExecutionCount=counter["calls"],
                   appId=saved["appId"], versionId=saved["versionId"],
                   definitionDigest=detail["definitionDigest"], verification=detail["verification"])

            if "workshop-app:" + saved["appId"] not in {item["appKey"] for item in workshop._asset_sources(owner)}:
                raise RuntimeError("未验证新应用没有按原创作资格进入应用工坊组合来源。")
            if "workshop-app:" + saved["appId"] in {item["appKey"] for item in workshop._catalog(owner)}:
                raise RuntimeError("未验证新应用错误进入可直接运行推荐目录。")
            record("source-eligibility-distinct-from-enable", status="passed")

            last_tool = max(index for index, step in enumerate(private["_spec"]["pipeline"], 1)
                            if step["kind"] == "tool")
            tool = next(t for t in private["_spec"]["tools"] if t["id"] ==
                        next(step["tool"] for step in reversed(private["_spec"]["pipeline"]) if step["kind"] == "tool"))
            if not {"taxedAmount", "finalAmount"} <= set(tool["outputSchema"].get("properties", {})):
                raise RuntimeError("最后一个实际工具没有同时声明税后与折后结果。")
            cases = []
            for amount, tax_rate, discount_rate in ((100, .1, .2), (200, .05, .1)):
                taxed = round(amount * (1 + tax_rate), 2)
                final = round(taxed * (1 - discount_rate), 2)
                cases.append({"input": {"amount": amount, "taxRate": tax_rate, "discountRate": discount_rate},
                              "expectation": {"mode": "fields", "values": {
                                  f"step_{last_tool}.taxedAmount": taxed,
                                  f"step_{last_tool}.finalAmount": final}}})
            report["predeclaredCases"] = cases
            record("independent-expectations-fixed", status="passed")
            verifying = require_response(client.post(f"/desktop/api/asset-apps/{saved['appId']}/verify", json={
                "versionId": saved["versionId"], "cases": cases, "revision": detail["revision"]},
                headers={"Idempotency-Key": "live-verify"}), 202, "verify")
            records = wait_for_runs(assets, owner, verifying["verification"]["runIds"], args.timeout)
            verified = assets.detail(owner, saved["appId"])
            report["verificationRuns"] = records
            record("verified-two-distinct-cases", verificationStatus=verified["verification"]["status"],
                   runStatuses=[run["status"] for run in records], errors=[run.get("error") for run in records])
            if verified["verification"]["status"] != "passed":
                raise RuntimeError("两组独立预期未全部通过，实际验证记录已保留。")

            enabled = require_response(client.post(f"/desktop/api/asset-apps/{saved['appId']}/enable", json={
                "versionId": saved["versionId"], "enabled": True, "revision": verified["revision"]},
                headers={"Idempotency-Key": "live-enable"}), 200, "enable")
            if enabled["enabledVersionId"] != saved["versionId"]:
                raise RuntimeError("验证通过后未启用精确版本。")
            record("enabled-exact-version", status="passed")
            run = require_response(client.post(f"/desktop/api/asset-apps/{saved['appId']}/runs", json={
                "versionId": saved["versionId"], "input": {"amount": 50, "taxRate": .2, "discountRate": .25}},
                headers={"Idempotency-Key": "live-run"}), 202, "run")
            finished = wait_for_runs(assets, owner, [run["id"]], args.timeout)[0]
            report["enabledRun"] = finished
            actual = finished.get("result") or {}
            expected = {"taxedAmount": 60.0, "finalAmount": 45.0}
            observed = actual.get(f"step_{last_tool}", {})
            record("enabled-run", runStatus=finished["status"], expected=expected,
                   observed={key: observed.get(key) for key in expected}, error=finished.get("error"))
            if finished["status"] != "succeeded" or any(observed.get(key) != value for key, value in expected.items()):
                raise RuntimeError("启用后新输入实际运行结果与独立预期不一致。")

            revised = json.loads(json.dumps(enabled["definition"]))
            revised["name"] += " 第二版"
            new_version = assets.save(owner, {"definition": revised, "appId": saved["appId"],
                                              "revision": enabled["revision"]}, "live-platform-revision")
            remaining = workshop._asset_sources(owner)
            if any(item["appId"] == saved["appId"] for item in remaining):
                raise RuntimeError("平台改版后应用工坊仍使用旧 AgentSpec。")
            record("platform-revision-invalidates-source", status="passed",
                   newVersionId=new_version["versionId"])
            report["passed"] = True
            report["completedAt"] = stamp()
        except Exception as error:
            message = str(error)
            for key, value in model_config.items():
                if "KEY" in key and value:
                    message = message.replace(value, "[REDACTED]")
            report.update(error={"type": type(error).__name__, "message": message[:3000]},
                          completedAt=stamp())
            record("failed", status="failed", error=report["error"])
        finally:
            if workbench is not None:
                workbench.close()
            report["sourceSha256"] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
                ("backend/app/modules/app_workshop/service.py", "backend/app/modules/app_workshop/store.py",
                 "backend/app/modules/agent_platform/asset_legacy.py", "scripts/verify_app_workshop_integration_live.py")}
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "report": str(report_path),
                      "error": report.get("error")}, ensure_ascii=False), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
