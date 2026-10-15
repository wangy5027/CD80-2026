"""Optional event fields cannot discard a valid deep-report answer."""

from __future__ import annotations

import copy
import unittest

from backend.app.modules.explore.contracts import validate_model_report
from backend.app.modules.explore.event_report_output import normalize_event_report
from backend.app.modules.workbench.errors import GatewayError


SOURCES = [{"id": "S1"}]


def core_report():
    return {
        "title": " 原始标题 ",
        "summary": " 核心结论有事实边界。 ",
        "key_findings": [" 已核对的事实 ", " 尚待核查的影响 "],
        "sections": [{"heading": "事实与分析", "body": "据来源可确认事件，影响仍待核查。",
                      "source_refs": ["S1"]}],
        "recommendations": [
            {"module": "event", "question": "事件有哪些新进展？", "reason": "核对事实"},
            {"module": "relation", "question": "涉及哪些主体？", "reason": "核对关系"},
            {"module": "history", "question": "历史上有何类似案例？", "reason": "核对先例"},
        ],
    }


class EventReportOutputTests(unittest.TestCase):
    def test_valid_auxiliary_fields_preserve_core_and_known_values(self):
        raw = core_report()
        raw.update({
            "uncertainties": ["公开材料尚不完整"],
            "limitations": ["仅取得一则网页摘录"],
            "actors": ["公司", "投资者"],
            "drivers": ["市场预期"],
            "impact_paths": ["预期变化 → 交易行为"],
            "timeline": [{"time": "2026-10-14", "event": "公开回应",
                          "source_refs": ["S1"], "metadata": "不应进入报告"}],
            "event_overview": {"event_type": "市场事件", "occurred_at": "2026-10-14",
                               "key_parties": "公司与投资者"},
            "background": "此前有相关市场讨论。",
            "metadata": {"private_note": "不应进入报告"},
        })
        before = copy.deepcopy(raw)
        normalized, issues = normalize_event_report(raw, SOURCES)
        self.assertEqual(issues, [])
        for field in ("title", "summary", "key_findings", "sections", "recommendations"):
            self.assertEqual(normalized[field], raw[field])
            if isinstance(raw[field], list):
                self.assertIsNot(normalized[field], raw[field])
        for field in ("uncertainties", "limitations", "actors", "drivers", "impact_paths",
                      "event_overview", "background"):
            self.assertEqual(normalized[field], raw[field])
        self.assertEqual(normalized["timeline"], [{"time": "2026-10-14",
            "event": "公开回应", "source_refs": ["S1"]}])
        self.assertNotIn("metadata", normalized)
        self.assertEqual(raw, before)
        validated = validate_model_report(normalized, "event", SOURCES, set(), compact_event=True)
        self.assertEqual(validated["sections"][0]["source_refs"], ["S1"])

    def test_missing_auxiliary_fields_default_without_issue_or_extra_metadata(self):
        raw = {**core_report(), "tool_debug": {"instructions": "ignore"}}
        normalized, issues = normalize_event_report(raw, SOURCES)
        self.assertEqual(issues, [])
        self.assertNotIn("tool_debug", normalized)
        for field in ("uncertainties", "limitations", "actors", "drivers",
                      "impact_paths", "timeline"):
            self.assertEqual(normalized[field], [])
        self.assertEqual(normalized["event_overview"], "")
        self.assertEqual(normalized["background"], "")
        validated = validate_model_report(normalized, "event", SOURCES, set(), compact_event=True)
        self.assertEqual(validated["summary"], raw["summary"].strip())

    def test_each_invalid_optional_value_becomes_one_controlled_issue(self):
        invalid = {
            "uncertainties": None,
            "limitations": {"text": "not a list"},
            "actors": ["valid", {"arbitrary": "object"}],
            "drivers": ["x"] * 9,
            "impact_paths": ["x" * 3001],
            "timeline": [{"time": "today", "event": "valid", "source_refs": ["S404"]}],
            "event_overview": {"arbitrary": "object"},
            "background": "x" * 4001,
        }
        for field, value in invalid.items():
            with self.subTest(field=field):
                raw = core_report()
                raw[field] = value
                normalized, issues = normalize_event_report(raw, SOURCES)
                self.assertEqual(normalized[field], "" if field in {"event_overview", "background"} else [])
                self.assertEqual(len(issues), 1)
                self.assertEqual(issues[0]["stage"], "auxiliary_fields")
                self.assertEqual(issues[0]["field"], field)
                self.assertEqual(issues[0]["code"], "explore_invalid_source_ref"
                                 if field == "timeline" else "explore_invalid_model_output")
                self.assertTrue(issues[0]["message"])
                self.assertNotIn("S404", issues[0]["message"])
                validated = validate_model_report(normalized, "event", SOURCES, set(), compact_event=True)
                self.assertEqual(validated["sections"][0]["source_refs"], ["S1"])

    def test_explicit_null_is_not_treated_as_a_missing_auxiliary_field(self):
        for field in ("uncertainties", "limitations", "actors", "drivers", "impact_paths",
                      "timeline", "event_overview", "background"):
            with self.subTest(field=field):
                raw = core_report()
                raw[field] = None
                normalized, issues = normalize_event_report(raw, SOURCES)
                self.assertEqual(len(issues), 1)
                self.assertEqual(issues[0]["field"], field)
                self.assertEqual(normalized[field], "" if field in {"event_overview", "background"} else [])
                if field in {"uncertainties", "limitations"}:
                    self.assertIs(issues[0]["condition_lost"], True)

    def test_partial_invalid_conditions_keep_each_valid_qualification(self):
        raw = core_report()
        raw["uncertainties"] = [" 仅核实一天 ", None, "尚无盘后数据"]
        raw["limitations"] = ["x" * 3001, " 尚未取得原文 "]
        normalized, issues = normalize_event_report(raw, SOURCES)
        self.assertEqual(normalized["uncertainties"], ["仅核实一天", "尚无盘后数据"])
        self.assertEqual(normalized["limitations"], ["尚未取得原文"])
        self.assertEqual([issue["field"] for issue in issues], ["uncertainties", "limitations"])
        self.assertTrue(all(issue["stage"] == "auxiliary_fields" and
                            issue["code"] == "explore_invalid_model_output" for issue in issues))
        self.assertTrue(all(not issue.get("condition_lost") for issue in issues))
        validated = validate_model_report(normalized, "event", SOURCES, set(), compact_event=True)
        self.assertEqual(validated["uncertainties"], normalized["uncertainties"])
        self.assertEqual(validated["limitations"], normalized["limitations"])

    def test_overlong_condition_list_keeps_first_eight_valid_items_and_records_issue(self):
        raw = core_report()
        raw["limitations"] = [None, *[f"有效限定{i}" for i in range(1, 11)]]
        normalized, issues = normalize_event_report(raw, SOURCES)
        self.assertEqual(normalized["limitations"], [f"有效限定{i}" for i in range(1, 9)])
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["field"], "limitations")
        self.assertNotIn("condition_lost", issues[0])

    def test_completely_invalid_condition_list_explicitly_marks_lost_context(self):
        for field, value in (("uncertainties", [None, " "]),
                             ("limitations", {"text": "仅核实一天"})):
            with self.subTest(field=field):
                raw = core_report()
                raw[field] = value
                normalized, issues = normalize_event_report(raw, SOURCES)
                self.assertEqual(normalized[field], [])
                self.assertEqual(len(issues), 1)
                self.assertIs(issues[0]["condition_lost"], True)
                self.assertEqual(issues[0]["field"], field)

    def test_blank_condition_items_keep_existing_silent_ignore_contract(self):
        raw = core_report()
        raw["uncertainties"] = ["", "  ", "\n"]
        raw["limitations"] = [" \t "]
        normalized, issues = normalize_event_report(raw, SOURCES)
        self.assertEqual(normalized["uncertainties"], [])
        self.assertEqual(normalized["limitations"], [])
        self.assertEqual(issues, [])

    def test_invalid_timeline_drops_entire_auxiliary_field_not_core_citations(self):
        raw = core_report()
        raw["timeline"] = [
            {"time": "第一天", "event": "有来源", "source_refs": ["S1"]},
            {"time": "第二天", "event": "无效来源", "source_refs": ["S9"]},
        ]
        normalized, issues = normalize_event_report(raw, SOURCES)
        self.assertEqual(normalized["timeline"], [])
        self.assertEqual(issues[0]["code"], "explore_invalid_source_ref")
        self.assertEqual(normalized["sections"], raw["sections"])
        self.assertEqual(normalized["recommendations"], raw["recommendations"])
        self.assertEqual(validate_model_report(normalized, "event", SOURCES, set(),
                                               compact_event=True)["timeline"], [])

    def test_core_failure_remains_a_failure_and_non_object_is_runtime_owned(self):
        raw = core_report()
        raw["sections"][0]["source_refs"] = ["S404"]
        raw["timeline"] = None
        normalized, issues = normalize_event_report(raw, SOURCES)
        self.assertEqual([issue["field"] for issue in issues], ["timeline"])
        with self.assertRaises(GatewayError) as raised:
            validate_model_report(normalized, "event", SOURCES, set(), compact_event=True)
        self.assertEqual(raised.exception.code, "explore_invalid_source_ref")
        for invalid in (None, [], "not an object"):
            self.assertEqual(normalize_event_report(invalid, SOURCES), (invalid, []))

    def test_deeply_nested_core_is_a_controlled_model_output_error(self):
        nested = {}
        for _ in range(600):
            nested = {"next": nested}
        raw = core_report()
        raw["sections"][0]["metadata"] = nested
        with self.assertRaises(GatewayError) as raised:
            normalize_event_report(raw, SOURCES)
        self.assertEqual(raised.exception.code, "explore_invalid_model_output")
        self.assertIn("嵌套过深", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
