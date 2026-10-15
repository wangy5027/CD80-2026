"""Built-in CSV application acceptance through real WASI and the public runner."""
from __future__ import annotations

import io
import json
import tempfile
import time
import unittest

from backend.app import create_app
from backend.app.modules.agent_platform.contracts import validate_agent_spec, validate_input
from backend.app.modules.agent_platform.executor import SandboxError, SandboxExecutor
from backend.app.modules.agent_platform.planner import Planner
from backend.app.modules.agent_platform.presets import CSV_SUMMARY_REF, csv_summary_spec, register_builtin_agents
from backend.app.modules.workbench.errors import GatewayError


class SummaryProvider:
    """Deterministic model text; calculation must still happen inside actual WASI."""
    model = "preset-test"

    def __init__(self):
        self.calls = []

    def tokens(self, messages, model, stopped, on_response):
        self.calls.append(messages)
        payload = json.loads(messages[1]["content"])
        if "分类器" in messages[0]["content"]:
            answer = json.dumps({"intent": "invoke", "agentRef": CSV_SUMMARY_REF,
                                 "input": {"fileId": payload["files"][0]["id"], "groupBy": "category", "valueColumn": "amount"}})
        else:
            result = payload["stepOutputs"]["aggregate"]
            answer = f"实际结果：{result['rowCount']} 行，{result['groupCount']} 组，总计 {result['total']}。"
        yield answer


class PresetDefinitionTests(unittest.TestCase):
    def test_fresh_valid_immutable_definition_and_registration(self):
        first = validate_agent_spec(csv_summary_spec())
        self.assertEqual(first["id"] + "@" + first["version"], CSV_SUMMARY_REF)
        first["tools"][0]["code"] = "modified by caller"
        self.assertNotEqual(first, csv_summary_spec())
        class Catalog:
            def __init__(self):
                self.definitions = []
            def register_preset(self, spec):
                self.definitions.append(validate_agent_spec(spec))
        catalog = Catalog()
        self.assertEqual(register_builtin_agents(catalog), [CSV_SUMMARY_REF])
        self.assertEqual(len(catalog.definitions), 1)
        self.assertEqual(catalog.definitions[0]["tools"][0]["permissions"], {"files": ["fileId"], "network": False})

    def test_parameters_are_explicit_and_only_sum_supported(self):
        schema = csv_summary_spec()["inputSchema"]
        values = {"fileId": "input-file", "groupBy": "category", "valueColumn": "amount"}
        validate_input(schema, values)
        validate_input(schema, values | {"aggregation": "sum"})
        for invalid in (values | {"aggregation": "mean"}, {"fileId": "input-file"}, values | {"filePath": "/host/private"}):
            with self.subTest(invalid=invalid), self.assertRaises(GatewayError):
                validate_input(schema, invalid)


class PresetWasiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.executor = SandboxExecutor({"AGENT_EXECUTION_TIMEOUT": 25})
        if not cls.executor.status()["available"]:
            raise unittest.SkipTest("install the verified WASI runtime before preset acceptance")

    def run_csv(self, data):
        spec = csv_summary_spec()
        payload = {"fileId": "fixture", "groupBy": "category", "valueColumn": "amount", "filePath": "/input/fixture"}
        result = self.executor.execute(spec["tools"][0]["code"], payload, {"fixture": data})
        validate_input(spec["tools"][0]["outputSchema"], result["result"])
        return result

    def test_fixed_inputs_produce_exact_sorted_csv(self):
        first = self.run_csv(b"category,amount\nA,10\nB,7\nA,5\n")
        self.assertEqual(first["files"]["summary.csv"], b"category,total_amount\nA,15\nB,7\n")
        self.assertEqual(first["result"]["total"], "22")
        self.assertEqual(first["result"]["rowCount"], 3)
        second = self.run_csv(b"category,amount\nC,4\nA,1\nC,6\n")
        self.assertEqual(second["files"]["summary.csv"], b"category,total_amount\nA,1\nC,10\n")
        self.assertEqual(second["result"]["total"], "11")

    def test_decimal_utf8_bom_and_quoted_cells(self):
        result = self.run_csv('\ufeffcategory,amount\r\n"甲,乙",0.1\r\n"甲,乙",0.2\r\n丙,-0.05\r\n'.encode("utf-8"))
        self.assertEqual(result["result"]["total"], "0.25")
        self.assertIn('"甲,乙",0.3\n', result["files"]["summary.csv"].decode("utf-8"))
        high_precision = self.run_csv(b"category,amount\nA,123456789012345678901234567890.1\nA,0.2\n")
        self.assertEqual(high_precision["result"]["total"], "123456789012345678901234567890.3")

    def test_invalid_data_fails_explicitly_without_artifacts(self):
        cases = [
            (b"category,wrong\nA,10\n", "缺少指定列"),
            (b"category,amount,amount\nA,10,10\n", "重复列名"),
            (b"category,amount\nA\n", "列数不一致"),
            (b"category,amount\nA,1,extra\n", "列数不一致"),
            (b"category,amount\n,1\n", "分组值为空"),
            (b"category,amount\nA,\n", "数值为空"),
            (b"category,amount\nA,NaN\n", "有效有限数字"),
            (b"category,amount\nA,Infinity\n", "有效有限数字"),
            (b"category,amount\nA,not-a-number\n", "有效有限数字"),
            (b"category,amount\nA,1e999999\n", "超出数值范围"),
            (b"category,amount\n", "没有数据行"),
            (b"", "空文件"),
        ]
        for data, expected in cases:
            with self.subTest(data=data), self.assertRaises(SandboxError) as raised:
                self.run_csv(data)
            self.assertEqual(raised.exception.code, "tool_failed")
            self.assertIn(expected, raised.exception.logs)

    def test_many_groups_export_full_csv_with_bounded_model_summary(self):
        data = "category,amount\n" + "".join(f"G{index:03d},1\n" for index in range(100))
        result = self.run_csv(data.encode())
        self.assertEqual(len(result["files"]["summary.csv"].splitlines()), 101)
        self.assertEqual(len(result["result"]["preview"]), 20)
        self.assertTrue(result["result"]["previewTruncated"])
        self.assertEqual(result["result"]["groupCount"], 100)
        self.assertEqual(result["result"]["total"], "100")
        self.assertLess(len(json.dumps(result["result"])), 4000)

    def test_dispatch_runner_save_and_reuse_with_real_wasi(self):
        with tempfile.TemporaryDirectory(prefix="cd80-preset-") as temporary:
            app = create_app({"TESTING": True, "DATA_DIR": temporary, "GITEE_AI_API_KEY": "test-only", "IMPORT_LEGACY_HISTORY": False})
            platform = app.extensions["agent_platform"]
            try:
                register_builtin_agents(platform)
                provider = SummaryProvider()
                platform.planner = Planner(provider)
                platform.executor = self.executor
                client = app.test_client()
                registered = client.post("/desktop/api/register", json={"email": "preset@example.test", "password": "long-password"})
                owner = registered.json["user"]["id"]

                def upload(data):
                    response = client.post("/desktop/api/files", data={"file": (io.BytesIO(data), "sales.csv")}, content_type="multipart/form-data")
                    self.assertEqual(response.status_code, 201)
                    return response.json["id"]

                def wait(run_id):
                    deadline = time.monotonic() + 35
                    while time.monotonic() < deadline:
                        run = platform.repository.get(owner, run_id)
                        if run["status"] in {"succeeded", "failed", "cancelled"}:
                            self.assertEqual(run["status"], "succeeded", run.get("error"))
                            return run
                        time.sleep(.025)
                    self.fail("preset run did not complete")

                first_file = upload(b"category,amount\nA,10\nB,7\nA,5\n")
                response = client.post("/desktop/api/agent-dispatch", json={"message": "按 category 分组，对 amount 求和", "fileIds": [first_file], "idempotencyKey": "preset-dispatch"})
                self.assertEqual(response.status_code, 200, response.json)
                self.assertEqual(response.json["intent"], "invoke")
                run = wait(response.json["run"]["id"])
                self.assertEqual(run["executionBasis"], "direct_request")
                self.assertFalse(any(a["kind"] == "execute" for a in run["approvals"]))
                self.assertEqual(run["loadedSkills"][0]["id"], "csv-summary-report")
                self.assertIn("Skill csv-summary-report@1", provider.calls[-1][0]["content"])
                self.assertEqual(run["output"]["stepOutputs"]["aggregate"]["total"], "22")
                first_download = client.get("/desktop/api/agent-artifacts/" + run["output"]["csvArtifactId"])
                self.assertEqual(first_download.data, b"category,total_amount\nA,15\nB,7\n")
                first_download.close()
                saved = client.post("/desktop/api/agents", json={"runId": run["id"], "idempotencyKey": "preset-save"})
                self.assertEqual(saved.status_code, 201, saved.json)
                self.assertEqual(saved.json["agentRef"], CSV_SUMMARY_REF)
                self.assertNotIn(first_file, json.dumps(saved.json["spec"]))
                catalog = client.get("/desktop/api/agents").json["agents"]
                self.assertEqual(sum(item["agentRef"] == CSV_SUMMARY_REF for item in catalog), 2)
                self.assertEqual(len(next(item for item in catalog if not item["personal"] and item["agentRef"]==CSV_SUMMARY_REF)["personalCopies"]),1)
                second_file = upload(b"category,amount\nA,1\nC,4\nC,6\n")
                count = len(provider.calls)
                invoked = client.post("/desktop/api/agent-runs", json={"mode": "invoke", "agentRef": CSV_SUMMARY_REF,
                    "input": {"fileId": second_file, "groupBy": "category", "valueColumn": "amount", "aggregation": "sum"},
                    "fileIds": [second_file], "idempotencyKey": "preset-reuse"})
                self.assertEqual(invoked.status_code, 202, invoked.json)
                second = wait(invoked.json["id"])
                self.assertEqual(len(provider.calls), count + 1)
                self.assertEqual(second["output"]["stepOutputs"]["aggregate"]["total"], "11")
                self.assertEqual(second["files"][0]["id"], second_file)
                download = client.get("/desktop/api/agent-artifacts/" + second["output"]["csvArtifactId"])
                self.assertEqual(download.data, b"category,total_amount\nA,1\nC,10\n")
                download.close()
            finally:
                app.extensions["workbench_service"].close()


if __name__ == "__main__":
    unittest.main()
