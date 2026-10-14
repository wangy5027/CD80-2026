"""A narrow JSON compatibility layer for the event deep report only."""

from __future__ import annotations

import json
import threading
import unittest
from unittest.mock import Mock

from backend.app.modules.explore.event_report_json import parse_event_report_json
from backend.app.modules.explore.service import ExploreService
from backend.app.modules.workbench.errors import GatewayError


class EventReportJsonTests(unittest.TestCase):
    def assert_repair_trace(self, source: str, repairs: list[dict], *, maximum: int = 8):
        self.assertGreater(len(repairs), 0)
        self.assertLessEqual(len(repairs), maximum)
        for repair in repairs:
            self.assertIsInstance(repair, dict)
            self.assertIsInstance(repair.get("kind"), str)
            self.assertTrue(repair["kind"])
            self.assertIs(type(repair.get("position")), int)
            self.assertGreaterEqual(repair["position"], 0)
            self.assertLess(repair["position"], len(source))

    def assert_rejected(self, source: str):
        with self.assertRaises(GatewayError) as raised:
            parse_event_report_json(source)
        self.assertEqual(raised.exception.code, "explore_invalid_model_output")

    def test_valid_json_is_unchanged_and_needs_no_repair(self):
        report = {
            "summary": '所谓“防御”板块，原话包含英文引号："defensive"。',
            "sections": [{"body": "来源 S1 只支持这段事实，逗号，冒号：均属正文。",
                          "source_refs": ["S1"]}],
        }
        source = json.dumps(report, ensure_ascii=False)
        parsed, repairs = parse_event_report_json(source)
        self.assertEqual(parsed, report)
        self.assertEqual(repairs, [])

    def test_trailing_comma_repairs_only_json_structure(self):
        source = ('{"summary":"正文有中文逗号，冒号：和 S1",'
                  '"sections":[{"body":"事实不变","source_refs":["S1",],}],}')
        parsed, repairs = parse_event_report_json(source)
        self.assertEqual(parsed, {
            "summary": "正文有中文逗号，冒号：和 S1",
            "sections": [{"body": "事实不变", "source_refs": ["S1"]}],
        })
        self.assertEqual(len(repairs), 3)
        self.assert_repair_trace(source, repairs)

    def test_fullwidth_comma_and_colon_outside_strings_are_compatible(self):
        source = ('{"summary"："正文，标点：原样保留"，'
                  '"sections"：[ {"body":"报道文字","source_refs"：["S1"]} ]}')
        parsed, repairs = parse_event_report_json(source)
        self.assertEqual(parsed["summary"], "正文，标点：原样保留")
        self.assertEqual(parsed["sections"], [
            {"body": "报道文字", "source_refs": ["S1"]},
        ])
        self.assertEqual(len(repairs), 4)
        self.assert_repair_trace(source, repairs)

    def test_unescaped_inner_quotes_are_preserved_as_text(self):
        source = '{"summary":"称为"防御"板块","source_refs":["S1"]}'
        parsed, repairs = parse_event_report_json(source)
        self.assertEqual(parsed, {
            "summary": '称为"防御"板块', "source_refs": ["S1"],
        })
        self.assert_repair_trace(source, repairs)

    def test_unescaped_text_quotes_keep_chinese_punctuation_inside_body(self):
        source = ('{"summary":"称为"防御"板块，仍需核实：资金流向，'
                  '不能当成定论。","source_refs":["S1"]}')
        parsed, repairs = parse_event_report_json(source)
        self.assertEqual(parsed["summary"],
                         '称为"防御"板块，仍需核实：资金流向，不能当成定论。')
        self.assertEqual(parsed["source_refs"], ["S1"])
        self.assertEqual(len(repairs), 2)
        self.assert_repair_trace(source, repairs)

    def test_quotes_in_keys_arrays_module_and_source_refs_are_not_repaired(self):
        for source in (
            '{"sum"mary":"x"}',
            '{"items":["甲"乙"]}',
            '{"module":"ev"ent"}',
            '{"source_refs":["S"1"]}',
            '{"source_refs":"S"1"}',
            # An inner quote immediately followed by Chinese punctuation is
            # ambiguous with structure and is intentionally left strict.
            '{"summary":"称为"防御"，板块仍需核实"}',
        ):
            with self.subTest(source=source):
                self.assert_rejected(source)

    def test_adjacent_final_quote_requires_earlier_repair_in_same_value(self):
        source = '{"summary":"称为"防御"","title":"正常"}'
        parsed, repairs = parse_event_report_json(source)
        self.assertEqual(parsed, {"summary": '称为"防御"', "title": "正常"})
        self.assertEqual(len(repairs), 2)
        self.assert_repair_trace(source, repairs)
        for ambiguous in (
            '{"summary":"甲""}',
            '{"summary":"称为"防御","title":"甲""}',
        ):
            with self.subTest(source=ambiguous):
                self.assert_rejected(ambiguous)

    def test_missing_commas_must_not_merge_adjacent_values_or_fields(self):
        for source in (
            '{"items":["甲" "乙"]}',
            '{"summary":"上涨" "title":"情况"}',
            '{"summary":"上涨""title":"情况"}',
            '{"count":1 "summary":"情况"}',
        ):
            with self.subTest(source=source):
                self.assert_rejected(source)

    def test_more_than_eight_repairs_are_rejected(self):
        eight = "{" + "，".join(f'"k{i}":{i}' for i in range(9)) + "}"
        parsed, repairs = parse_event_report_json(eight)
        self.assertEqual(parsed, {f"k{i}": i for i in range(9)})
        self.assertEqual(len(repairs), 8)
        self.assert_repair_trace(eight, repairs)

        nine = "{" + "，".join(f'"k{i}":{i}' for i in range(10)) + "}"
        self.assert_rejected(nine)

    def test_structural_or_unsafe_outputs_remain_invalid(self):
        invalid = (
            '{"summary":"未闭合"',
            '{"summary":"one","summary":"two"}',
            '{"summary":NaN}',
            '{"summary":"A\\q"}',
            '{"summary":"A\x00B"}',
            '["不是对象"]',
            '{"summary":"' + "x" * 40000 + '"}',
        )
        for source in invalid:
            with self.subTest(source=source[:80]):
                self.assert_rejected(source)


class EventReportJsonStageIsolationTests(unittest.TestCase):
    def test_only_event_deep_lite_uses_compatibility_parser(self):
        malformed = '{"summary":"仍须核实",}'

        class FixtureProvider:
            model = "local-json-fixture"

            def __init__(self):
                self.calls = 0

            def json_tokens(self, messages, model, stopped, on_response):
                self.calls += 1
                yield malformed

        provider = FixtureProvider()
        service = ExploreService.__new__(ExploreService)
        service.provider = provider
        service.repository = Mock()

        def invoke(stage):
            control = {"stop": threading.Event(), "timed_out": threading.Event(),
                       "deadline": None}
            payload = {"task": "research_report", "stage": stage}
            return service._invoke_model_once(payload, "local fixture", control), control

        parsed, control = invoke("event_deep_lite")
        self.assertEqual(parsed, {"summary": "仍须核实"})
        self.assertEqual(provider.calls, 1)
        self.assertEqual(len(control["report_json_repairs"]), 1)

        for stage in ("event_initial", "relation_entities", "history_report"):
            with self.subTest(stage=stage):
                with self.assertRaises(GatewayError) as raised:
                    invoke(stage)
                self.assertEqual(raised.exception.code, "explore_invalid_model_output")
        self.assertEqual(provider.calls, 4)


if __name__ == "__main__":
    unittest.main()
