"""The lightweight history report keeps source validation and final-choice gates."""

import copy
import time
import unittest

from backend.app.modules.explore.contracts import (
    has_final_choice, render_markdown, validate_model_report,
)
from backend.app.modules.explore.service import ExploreService
from backend.app.modules.workbench.errors import GatewayError
from backend.app.modules.workbench.graph_runtime import GiteeProvider


class HistoryContractTests(unittest.TestCase):
    def report(self):
        return {"title": "历史关联", "summary": "历史合作有助于理解当前分工。",
                "key_findings": ["合作分工仍以报道可确认的内容为限。"],
                "uncertainties": [], "limitations": [],
                "sections": [{"heading": "历史关系", "body": "合作关系曾发生变化。",
                              "source_refs": ["S1"]}], "recommendations": []}

    def test_lightweight_report_does_not_require_old_hypothesis_cases(self):
        data = validate_model_report(self.report(), "history", [{"id": "S1"}], set(),
                                     compact_history=True)
        self.assertEqual(data["conclusion"], self.report()["summary"])
        self.assertEqual(data["cases"], [])
        with self.assertRaises(GatewayError):
            validate_model_report(self.report(), "history", [{"id": "S1"}], set())

    def test_lightweight_report_still_rejects_unknown_source(self):
        with self.assertRaises(GatewayError) as caught:
            validate_model_report(self.report(), "history", [], set(), compact_history=True)
        self.assertEqual(caught.exception.code, "explore_invalid_source_ref")

    def test_only_history_lite_report_opens_final_choice(self):
        data = {**self.report(), "module": "history", "report_format": "history_lite@1",
                "question": "这段合作历史如何形成？", "sources": []}
        self.assertTrue(has_final_choice(data))
        self.assertFalse(has_final_choice({**data, "report_format": "research"}))
        markdown = render_markdown(data)
        self.assertIn("合作关系曾发生变化", markdown)
        self.assertNotIn("待验证判断", markdown)

    def test_short_history_provider_copy_preserves_selected_model(self):
        service = ExploreService.__new__(ExploreService)
        service.provider = GiteeProvider({"GITEE_AI_API_KEY": "test-only",
            "GITEE_AI_MODEL": "GLM-5.3", "GITEE_AI_MAX_TOKENS": 8192,
            "GITEE_AI_REASONING_EFFORT": "high"})
        original = copy.deepcopy(service.provider.request_options)
        provider = service._provider_for_run(time.monotonic() + 90,
            {"model": "GLM-5.3-Flash"}, "history_initial")
        self.assertEqual(provider.model, "GLM-5.3-Flash")
        self.assertEqual(provider.request_options["reasoning_effort"], "low")
        self.assertLessEqual(provider.request_options["max_tokens"], 600)
        self.assertLessEqual(provider.read_timeout, 20)
        self.assertEqual(service.provider.model, "GLM-5.3")
        self.assertEqual(service.provider.request_options, original)


if __name__ == "__main__":
    unittest.main()
