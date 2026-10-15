"""The second event turn keeps a grounded report when optional model output fails."""

from __future__ import annotations

import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from backend.app import create_app
from backend.app.modules.explore.contracts import parse_json_object
from backend.app.modules.explore.repository import timestamp
from backend.app.modules.workbench.errors import GatewayError
from backend.app.modules.workbench.tool_registry import ToolRegistry, ToolResult, ToolSpec


class ContractProvider:
    model = "event-report-contract-fixture"

    def __init__(self):
        self.initial_calls = 0
        self.report_calls = 0
        self.report_prompts = []
        self.report_payloads = []
        self.mutate_report = None
        self.raw_report = None
        self.raw_transform = None

    def events(self, messages, model, stopped, on_response, *, tools=None, **_options):
        assert tools is None
        self.initial_calls += 1
        yield {"type": "token", "text": "本周行情应结合指数、成交和资金观察，具体变化以同期公开报道为准。"}
        yield {"type": "turn_end", "tool_calls": [], "finish_reason": "stop"}

    def json_tokens(self, messages, model, stopped, on_response):
        payload = json.loads(messages[1]["content"])
        assert payload["stage"] == "event_deep_lite"
        self.report_calls += 1
        self.report_prompts.append(messages[0]["content"])
        self.report_payloads.append(payload)
        if self.raw_report is not None:
            yield self.raw_report
            return
        refs = [item["id"] for item in payload["sources"]]
        report = {
            "title": "模型给出的本周行情分析",
            "summary": "有效正文：本轮公开报道显示市场有波动，具体走势应结合指数和成交数据核实。",
            "key_findings": ["有效发现：报道只支持已经观察到的市场变化。"],
            "sections": [{"heading": "有效章节", "body": "有效分析：不能把预测当成已发生事实。",
                          "source_refs": refs[:1]}],
            "recommendations": [
                {"module": "event", "question": "本周市场变化还有哪些事实要核实？", "reason": "核实事件"},
                {"module": "relation", "question": "成交资金怎样影响本轮波动？", "reason": "梳理关系"},
                {"module": "history", "question": "以往类似波动有哪些不同？", "reason": "历史对照"},
            ],
        }
        if self.mutate_report is not None:
            self.mutate_report(report)
        raw = json.dumps(report, ensure_ascii=False)
        yield self.raw_transform(raw) if self.raw_transform is not None else raw


class OneNewsTool:
    def __init__(self):
        self.search_calls = 0
        self.read_calls = 0

    def search(self):
        def handle(arguments, context):
            self.search_calls += 1
            return ToolResult(content={"searchedAt": timestamp()}, sources=[{
                "id": "weekly-news", "title": "A股本周指数与成交观察",
                "url": "https://example.org/weekly-market", "publishedAt": timestamp(),
                "snippet": "本周 A 股指数和市场成交出现变化，报道分析资金与板块表现。",
            }])
        return ToolSpec("web_search", "fixture search", {"type": "object"}, handle, max_calls=2)

    def reader(self, _dns_mode):
        def handle(arguments, context):
            self.read_calls += 1
            return ToolResult(content={"status": "ok", "text": (
                "本周 A 股指数和市场成交出现变化，投资者关注资金与板块表现。"),
                "final_url": arguments["url"], "publisher": "测试新闻"})
        return ToolSpec("fetch_public_page", "fixture page", {"type": "object"}, handle, max_calls=5)


class EventReportContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="event-report-contract-")
        root = Path(self.temp.name)
        self.app = create_app({"TESTING": True, "DATA_DIR": str(root / "data"),
            "AGENT_RUNTIME_DIR": str(root / "runtime"), "GITEE_AI_API_KEY": "test-only",
            "IMPORT_LEGACY_HISTORY": False, "EXPLORE_EVENT_LITE_ENABLED": True,
            "EXPLORE_RUN_TIMEOUT_SECONDS": 30})
        self.client = self.app.test_client()
        registered = self.client.post("/desktop/api/register", json={
            "email": f"event-contract-{uuid4().hex}@example.test", "password": "long-password"})
        self.assertEqual(registered.status_code, 200, registered.json)
        self.owner = registered.json["user"]["id"]
        self.service = self.app.extensions["explore_service"]
        self.provider = ContractProvider()
        self.news = OneNewsTool()
        self.service.provider = self.provider
        self.service.search_tools = ToolRegistry((self.news.search(),))
        created = self.client.post("/desktop/api/explore/sessions", json={"title": "报告契约"})
        self.assertEqual(created.status_code, 201, created.json)
        self.session_id = created.json["session"]["id"]

    def tearDown(self):
        self.service.close()
        self.app.extensions["workbench_service"].close()
        self.temp.cleanup()

    def _wait(self, run_id, statuses):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            response = self.client.get(f"/desktop/api/explore/runs/{run_id}")
            self.assertEqual(response.status_code, 200, response.json)
            run = response.json["run"]
            if run["status"] in statuses:
                return run
            time.sleep(.01)
        self.fail(f"run {run_id} did not reach {statuses}")

    def _produce_report(self):
        initial_before = self.provider.initial_calls
        report_before = self.provider.report_calls
        created = self.client.post(f"/desktop/api/explore/sessions/{self.session_id}/runs", json={
            "module": "event", "question": "A股这周的行情怎么样？",
            "idempotency_key": str(uuid4())})
        self.assertEqual(created.status_code, 202, created.json)
        run_id = created.json["run"]["id"]
        first = self._wait(run_id, {"awaiting_feedback", "failed"})
        self.assertEqual(first["status"], "awaiting_feedback", first.get("error"))
        self.assertEqual(self.provider.initial_calls, initial_before + 1)
        with patch("backend.app.modules.explore.event_lite_runtime.build_public_reader_tool",
                   self.news.reader):
            feedback = self.client.post(f"/desktop/api/explore/runs/{run_id}/feedback", json={
                "choice": "explain_more", "idempotency_key": str(uuid4())})
            self.assertEqual(feedback.status_code, 202, feedback.json)
            finished = self._wait(run_id, {"succeeded", "partial", "failed"})
        self.assertIn(finished["status"], {"succeeded", "partial"}, finished.get("error"))
        self.assertEqual(self.provider.report_calls, report_before + 1)
        return finished

    def _assert_specific_error(self, report, expected_code=None):
        errors = report["retrieval"].get("report_errors")
        self.assertIsInstance(errors, list)
        self.assertTrue(errors, report["retrieval"])
        for error in errors:
            self.assertIsInstance(error.get("stage"), str)
            self.assertTrue(error["stage"])
            self.assertIsInstance(error.get("code"), str)
            self.assertTrue(error["code"])
            self.assertIsInstance(error.get("message"), str)
            self.assertTrue(error["message"])
        if expected_code:
            self.assertIn(expected_code, [item["code"] for item in errors])
        return errors

    def test_second_prompt_contains_only_second_step_skill_and_valid_report_is_saved(self):
        run = self._produce_report()
        self.assertEqual(run["status"], "succeeded", run.get("error"))
        prompt = self.provider.report_prompts[-1]
        self.assertIn("## 第二步：", prompt)
        self.assertIn("## 第二步之后：", prompt)
        self.assertNotIn("## 第一步：", prompt)
        self.assertNotIn("## 用户反馈：", prompt)
        self.assertNotIn("只输出解释正文", prompt)
        self.assertIn("只输出 JSON 对象", prompt)
        self.assertIn("source_refs", prompt)
        self.assertIn("recommendations", prompt)
        self.assertEqual(run["report"]["summary"],
                         "有效正文：本轮公开报道显示市场有波动，具体走势应结合指数和成交数据核实。")
        self.assertEqual(run["report"]["sections"][0]["source_refs"], ["S1"])
        self.assertEqual([item["module"] for item in run["report"]["recommendations"]],
                         ["event", "relation", "history"])
        self.assertEqual(len(self.service.repository.list_files(self.owner, self.session_id)), 4)
        self.assertEqual(self.news.search_calls, 2)
        self.assertEqual(self.news.read_calls, 1)

    def test_compatible_json_saves_body_recommendations_pool_and_memory_without_retry(self):
        summary = '有效正文：市场被称为"防御"板块，具体走势，仍需核实。'
        self.provider.mutate_report = lambda report: report.update(summary=summary)

        def make_compatible(raw):
            # The fixture emits the same prose, but with two unescaped quotes
            # in a narrative value and a trailing comma after the last field.
            malformed = raw.replace(r'\"防御\"', '"防御"')
            self.assertNotEqual(malformed, raw)
            return malformed[:-1] + ",}"

        self.provider.raw_transform = make_compatible
        run = self._produce_report()
        report = run["report"]
        self.assertEqual(run["status"], "succeeded", run.get("error"))
        self.assertEqual(report["summary"], summary)
        self.assertEqual(report["sections"][0]["source_refs"], ["S1"])
        self.assertEqual([row["module"] for row in report["recommendations"]],
                         ["event", "relation", "history"])
        self.assertEqual(self.provider.report_calls, 1)
        self.assertEqual(run.get("model_retries") or [], [])

        repairs = report["retrieval"].get("report_json_repairs")
        self.assertIsInstance(repairs, list)
        self.assertEqual(len(repairs), 3)
        for repair in repairs:
            self.assertIn(repair["kind"], {"text_quote_escape", "trailing_comma"})
            self.assertIs(type(repair["position"]), int)
            self.assertIs(type(repair["line"]), int)
            self.assertIs(type(repair["column"]), int)
            self.assertEqual(repair["coordinate_system"], "normalized_json_before_repair")
        self.assertNotIn("有效正文", json.dumps(repairs, ensure_ascii=False))
        self.assertNotIn("防御", json.dumps(repairs, ensure_ascii=False))
        self.assertNotIn("S1", json.dumps(repairs, ensure_ascii=False))
        self.assertNotIn("report_errors", report["retrieval"])

        pool = self.service.repository.list_files(self.owner, self.session_id)
        self.assertEqual(len(pool), 4)
        report_files = [self.service.repository.get_file(self.owner, self.session_id, row["id"])
                        for row in pool if row["kind"] == "report"]
        self.assertTrue(any(summary in item["content"] for item in report_files))
        self.assertEqual(report["memory_input"]["state"]["stages"][-1]["phase"],
                         "initial_explanation")
        final_stage = report["memory_output"]["state"]["stages"][-1]
        self.assertEqual(final_stage["phase"], "report")
        self.assertEqual(final_stage["summary"], summary)
        self.assertEqual(final_stage["sections"][0]["source_refs"], ["S1"])

    def test_invalid_optional_fields_do_not_discard_grounded_core(self):
        invalid = {
            "event_overview": {"summary": "unexpected object"},
            "background": None,
            "actors": None,
            "drivers": {"unexpected": "object"},
            "impact_paths": "wrong shape",
            "timeline": [None],
            "uncertainties": None,
            "limitations": {"unexpected": "object"},
        }
        for name, value in invalid.items():
            with self.subTest(optional_field=name):
                self.provider.mutate_report = lambda report, key=name, bad=copy.deepcopy(value): report.update({key: bad})
                run = self._produce_report()
                report = run["report"]
                self.assertEqual(report["summary"],
                                 "有效正文：本轮公开报道显示市场有波动，具体走势应结合指数和成交数据核实。")
                self.assertEqual(report["sections"][0]["heading"], "有效章节")
                self.assertEqual(report["sections"][0]["source_refs"], ["S1"])
                self.assertEqual([row["module"] for row in report["recommendations"]],
                                 ["event", "relation", "history"])
                self.assertNotEqual(report.get("report_variant"), "generation_fallback")
                errors = self._assert_specific_error(report)
                self.assertTrue(any(item.get("stage") == "auxiliary_fields" and
                                    item.get("field") == name for item in errors), errors)

    def test_invalid_json_reports_parse_error_without_storing_raw_output(self):
        marker = "PRIVATE_RAW_MODEL_TEXT_MUST_NOT_PERSIST"
        self.provider.raw_report = '{"title":"' + marker + '"'
        run = self._produce_report()
        self.assertEqual(run["status"], "partial")
        errors = self._assert_specific_error(run["report"], "explore_invalid_model_output")
        parse_errors = [item for item in errors if item["stage"] == "json_parse"]
        self.assertEqual(len(parse_errors), 1, errors)
        self.assertEqual(parse_errors[0]["reason"], "syntax")
        self.assertEqual(parse_errors[0]["decoder_message"], "Expecting ',' delimiter")
        self.assertEqual(parse_errors[0]["response_chars"], len(self.provider.raw_report))
        self.assertIs(type(parse_errors[0]["position"]), int)
        self.assertNotIn(marker, json.dumps(parse_errors, ensure_ascii=False))
        self.assertNotIn(marker, json.dumps(run, ensure_ascii=False))
        for file_row in self.service.repository.list_files(self.owner, self.session_id):
            full = self.service.repository.get_file(self.owner, self.session_id, file_row["id"])
            self.assertNotIn(marker, full["content"])

    def test_missing_core_is_rejected_and_records_specific_reason(self):
        self.provider.mutate_report = lambda report: report.update(title="")
        run = self._produce_report()
        self.assertEqual(run["status"], "partial")
        self.assertNotEqual(run["report"]["summary"],
                            "有效正文：本轮公开报道显示市场有波动，具体走势应结合指数和成交数据核实。")
        errors = self._assert_specific_error(run["report"], "explore_invalid_model_output")
        self.assertTrue(any("标题" in item["message"] for item in errors))

    def test_invalid_source_ref_is_rejected_and_records_specific_reason(self):
        self.provider.mutate_report = lambda report: report["sections"][0].update(source_refs=["S999"])
        run = self._produce_report()
        self.assertEqual(run["status"], "partial")
        self.assertNotEqual(run["report"]["sections"][0]["heading"], "有效章节")
        self.assertNotIn("S999", run["report"]["markdown"])
        errors = self._assert_specific_error(run["report"], "explore_invalid_source_ref")
        self.assertTrue(any(item["stage"] == "source_validation" and
                            "来源" in item["message"] for item in errors))

    def test_missing_source_ref_still_downgrades_at_source_validation(self):
        self.provider.mutate_report = lambda report: report["sections"][0].pop("source_refs")
        run = self._produce_report()
        self.assertEqual(run["status"], "partial")
        self.assertNotEqual(run["report"]["sections"][0]["heading"], "有效章节")
        errors = self._assert_specific_error(run["report"], "explore_invalid_source_ref")
        self.assertTrue(any(item["stage"] == "source_validation" for item in errors), errors)

    def test_invalid_timeline_source_is_not_displayed_as_evidence(self):
        self.provider.mutate_report = lambda report: report.update(timeline=[{
            "time": "本周", "event": "未经证实的时间线事实", "source_refs": ["S999"]}])
        run = self._produce_report()
        report = run["report"]
        self.assertEqual(report["summary"],
                         "有效正文：本轮公开报道显示市场有波动，具体走势应结合指数和成交数据核实。")
        self.assertEqual(report["timeline"], [])
        self.assertNotIn("未经证实的时间线事实", report["markdown"])
        self.assertTrue(any(item.get("stage") == "auxiliary_fields" and
                            item.get("field") == "timeline" and
                            item.get("code") == "explore_invalid_source_ref"
                            for item in self._assert_specific_error(report)))

    def test_bad_recommendations_keep_valid_body_but_only_offer_ok(self):
        self.provider.mutate_report = lambda report: report.update(recommendations=report["recommendations"][:2])
        run = self._produce_report()
        self.assertEqual(run["status"], "partial")
        self.assertEqual(run["report"]["summary"],
                         "有效正文：本轮公开报道显示市场有波动，具体走势应结合指数和成交数据核实。")
        self.assertEqual(run["report"]["sections"][0]["heading"], "有效章节")
        self.assertEqual(run["report"]["recommendations"], [])
        self.assertEqual(run["next_step_state"], "pending")
        self._assert_specific_error(run["report"], "explore_invalid_recommendations")
        self.assertEqual(len(self.service.repository.list_files(self.owner, self.session_id)), 4)
        finished = self.client.post(f"/desktop/api/explore/runs/{run['id']}/finish", json={
            "idempotency_key": str(uuid4())})
        self.assertEqual(finished.status_code, 200, finished.json)
        self.assertEqual(finished.json["run"]["next_step_state"], "finished")

    def test_nul_in_recommendation_question_or_reason_keeps_report_body(self):
        for field in ("question", "reason"):
            with self.subTest(field=field):
                def add_nul(report, key=field):
                    report["recommendations"][0][key] += "\x00hidden"

                self.provider.mutate_report = add_nul
                run = self._produce_report()
                report = run["report"]
                self.assertEqual(run["status"], "partial")
                self.assertEqual(report["summary"],
                                 "有效正文：本轮公开报道显示市场有波动，具体走势应结合指数和成交数据核实。")
                self.assertEqual(report["sections"][0]["heading"], "有效章节")
                self.assertEqual(report["recommendations"], [])
                self.assertEqual(run["next_step_state"], "pending")
                self._assert_specific_error(report, "explore_invalid_recommendations")
                self.assertNotIn("hidden", report["markdown"])


class EventReportJsonParseTests(unittest.TestCase):
    def _diagnostic(self, raw):
        with self.assertRaises(GatewayError) as caught:
            parse_json_object(raw)
        error = caught.exception
        self.assertEqual(error.code, "explore_invalid_model_output")
        detail = error.diagnostic
        self.assertEqual(detail["stage"], "json_parse")
        self.assertNotIn(raw, json.dumps(detail, ensure_ascii=False))
        self.assertNotIn(raw, str(error))
        return detail

    def test_syntax_error_has_coordinates_without_response_text(self):
        detail = self._diagnostic('{"title":"PRIVATE_PARSE_MARKER"')
        self.assertEqual(detail["reason"], "syntax")
        self.assertIsInstance(detail["position"], int)
        self.assertGreaterEqual(detail["position"], 0)
        self.assertEqual(detail["line"], 1)
        self.assertIsInstance(detail["column"], int)
        self.assertGreater(detail["column"], 0)
        self.assertEqual(detail["coordinate_system"], "normalized_json")

    def test_duplicate_nonfinite_and_nonobject_have_distinct_categories(self):
        cases = {
            '{"title":1,"title":2}': "duplicate_key",
            '{"score":NaN}': "non_finite_number",
            '["not an object"]': "not_object",
        }
        for raw, reason in cases.items():
            with self.subTest(reason=reason):
                detail = self._diagnostic(raw)
                self.assertEqual(detail["reason"], reason)
                self.assertNotIn("position", detail)

    def test_raw_lf_inside_json_string_remains_accepted(self):
        self.assertEqual(parse_json_object('{"summary":"第一行\n第二行"}'),
                         {"summary": "第一行\n第二行"})


if __name__ == "__main__":
    unittest.main()
