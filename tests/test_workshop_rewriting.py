"""Rewrite skill gates, bounded repair, and explicit external-data non-support."""
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from backend.app.modules.strategy_workshop.rewriting import StrategyRewriteSkill, _validate
from backend.app.modules.workbench.errors import GatewayError


def plan():
    left = {"type": "indicator", "name": "sma", "period": 10}
    right = {"type": "indicator", "name": "sma", "period": 30}
    return {"schema_version": 1, "name": "10/30日均线", "description": "周期按用户要求修改，其余不变。", "features": [],
            "entry": {"type": "compare", "op": "gt", "left": left, "right": right},
            "exit": {"type": "compare", "op": "lte", "left": left, "right": right}}


def ready():
    return {"status": "rules_ready", "plan": plan(), "summary": "10日高于30日买入，否则退出。", "assumptions": [], "questions": [], "dataGaps": []}


class RewriteTests(unittest.TestCase):
    def skill(self, key="test"):
        return StrategyRewriteSkill(SimpleNamespace(runtime=SimpleNamespace(provider=SimpleNamespace(key=key))), {})

    def test_normalized_ready_preserves_user_periods(self):
        result = _validate(ready())
        self.assertEqual(result["plan"]["entry"]["left"]["period"], 10)
        self.assertEqual(result["plan"]["exit"]["right"]["period"], 30)

    def test_missing_conditions_require_question_not_fake_plan(self):
        value = {"status": "needs_clarification", "plan": None, "summary": "请明确退出条件", "assumptions": [], "questions": ["退出是否沿用模板？"], "dataGaps": []}
        self.assertEqual(_validate(value)["status"], "needs_clarification")
        value["questions"] = []
        with self.assertRaises(ValueError):
            _validate(value)

    def test_external_data_does_not_generate_plan(self):
        value = {"status": "unsupported_data", "plan": None, "summary": "微博热度未接入", "assumptions": [], "questions": [], "dataGaps": ["带日期的微博热度历史"]}
        with patch.object(StrategyRewriteSkill, "_call", return_value=json.dumps(value, ensure_ascii=False)):
            result = self.skill().prepare("微博不热时买入", {"strategyId": "rsi_reversion"})
        self.assertIsNone(result["plan"])
        self.assertIn("暂不支持外源数据", result["message"])
        value["plan"] = plan()
        with self.assertRaises(ValueError):
            _validate(value)

    def test_ready_cannot_hide_unresolved_questions(self):
        for key in ("questions", "dataGaps"):
            value = ready()
            value[key] = ["尚未确定"]
            with self.assertRaises(ValueError):
                _validate(value)

    def test_external_data_takes_priority_over_parameter_questions(self):
        value = {"status": "needs_clarification", "plan": None, "summary": "需要微博热度", "assumptions": [], "questions": ["热度阈值是多少？"], "dataGaps": ["微博热度历史"]}
        with self.assertRaises(ValueError):
            _validate(value)
        value["status"] = "unsupported_data"
        with self.assertRaises(ValueError):
            _validate(value)

    def test_repairs_invalid_rule_then_stops_after_success(self):
        bad = ready()
        bad["plan"]["entry"]["left"]["period"] = -1
        candidate = {"strategyId": "sma_trend", "parameters": {"fast_period": 20}}
        before = deepcopy(candidate)
        with patch.object(StrategyRewriteSkill, "_call", side_effect=[json.dumps(bad), json.dumps(ready())]) as model:
            result = self.skill().prepare("改10/30日，其余不变", candidate)
        self.assertEqual(result["generationAttempts"], 2)
        self.assertEqual(model.call_count, 2)
        self.assertTrue(result["repairFeedback"])
        self.assertEqual(candidate, before)

    def test_final_answer_repairs_another_question_within_three_model_attempts(self):
        unresolved = {"status": "needs_clarification", "plan": None, "summary": "仍需确认退出。",
                      "assumptions": [], "questions": ["是否沿用模板退出？"], "dataGaps": []}
        context = {"finalPass": True, "previousStatus": "needs_clarification",
                   "previousQuestions": ["买卖条件分别是什么？"],
                   "previousSummary": "请一次说明买卖条件。",
                   "previousAssumptions": ["单标的日线"],
                   "latestAnswer": "买入用10/30日均线，卖出沿用模板"}
        with patch.object(StrategyRewriteSkill, "_call", side_effect=[json.dumps(unresolved), json.dumps(ready())]) as model:
            result = self.skill().prepare("按均线交易", {"strategyId": "sma_trend"},
                                          "买入用10/30日均线，卖出沿用模板", alignment_context=context)
        self.assertEqual(result["status"], "rules_ready")
        self.assertEqual(result["generationAttempts"], 2)
        self.assertEqual(model.call_count, 2)
        self.assertEqual(model.call_args_list[0].args[0]["alignmentContext"], context)
        self.assertIn("validationFeedback", model.call_args_list[1].args[0])

    def test_final_answer_still_unresolved_stops_after_three_internal_attempts(self):
        unresolved = {"status": "needs_clarification", "plan": None, "summary": "退出条件仍不明确。",
                      "assumptions": [], "questions": ["退出条件是什么？"], "dataGaps": []}
        with patch.object(StrategyRewriteSkill, "_call", return_value=json.dumps(unresolved)) as model:
            result = self.skill().prepare("买入后卖出", {}, "仍未决定退出条件",
                                          alignment_context={"finalPass": True, "latestAnswer": "仍未决定退出条件"})
        self.assertEqual(result["status"], "needs_clarification")
        self.assertEqual(result["questions"], ["退出条件是什么？"])
        self.assertEqual(result["generationAttempts"], 3)
        self.assertEqual(model.call_count, 3)

    def test_initial_question_is_one_user_round_not_three_format_attempts(self):
        unresolved = {"status": "needs_clarification", "plan": None, "summary": "请确定退出条件。",
                      "assumptions": [], "questions": ["卖出条件是什么？"], "dataGaps": []}
        with patch.object(StrategyRewriteSkill, "_call", return_value=json.dumps(unresolved)) as model:
            result = self.skill().prepare("价格突破后买入", {})
        self.assertEqual(result["status"], "needs_clarification")
        self.assertEqual(result["generationAttempts"], 1)
        self.assertEqual(model.call_count, 1)

    def test_invalid_output_repair_is_bounded_to_three(self):
        with patch.object(StrategyRewriteSkill, "_call", return_value="bad") as model:
            with self.assertRaises(GatewayError) as caught:
                self.skill().prepare("改10/30日", {})
        self.assertEqual(caught.exception.code, "rewrite_validation_failed")
        self.assertEqual(model.call_count, 3)

    def test_final_answer_format_failure_is_not_another_user_question(self):
        with patch.object(StrategyRewriteSkill, "_call", return_value="bad") as model:
            with self.assertRaises(GatewayError) as caught:
                self.skill().prepare("改10/30日", {}, "确认按上一轮建议执行",
                                     alignment_context={"finalPass": True, "latestAnswer": "确认按上一轮建议执行"})
        self.assertEqual(caught.exception.code, "rewrite_finalization_failed")
        self.assertIn("重试本次回答", str(caught.exception))
        self.assertEqual(model.call_count, 3)

    def test_transport_failure_is_not_blindly_repaired(self):
        with patch.object(StrategyRewriteSkill, "_call", side_effect=GatewayError("unavailable", 503)) as model:
            with self.assertRaises(GatewayError):
                self.skill().prepare("改10/30日", {})
        self.assertEqual(model.call_count, 1)

    def test_no_provider_does_not_fallback_to_sma(self):
        with self.assertRaises(GatewayError) as caught:
            self.skill("").prepare("改10/30日", {})
        self.assertEqual(caught.exception.code, "rewrite_unavailable")

    def test_duplicate_keys_nonfinite_and_unknown_fields_rejected(self):
        values = ['{"status":"rules_ready","status":"unsupported_data"}', '{"value":NaN}']
        for value in values:
            with patch.object(StrategyRewriteSkill, "_call", return_value=value):
                with self.assertRaises(GatewayError):
                    self.skill().prepare("改10/30日", {})
        value = ready()
        value["python"] = "arbitrary"
        with self.assertRaises(ValueError):
            _validate(value)


if __name__ == "__main__":
    unittest.main()
