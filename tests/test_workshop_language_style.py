"""Audience affects wording, while rules, approval and replay remain stable."""
import copy
import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from backend.app.modules.strategy_workshop.contracts import digest
from backend.app.modules.strategy_workshop.custom_rules import compile_strategy
from backend.app.modules.strategy_workshop.matching import MatchingService
from backend.app.modules.strategy_workshop.presentation import LANGUAGE_STYLE_VERSION
from backend.app.modules.strategy_workshop.rewriting import EXTERNAL_DATA_MESSAGE, StrategyRewriteSkill
from backend.app.modules.strategy_workshop.service import WorkshopService
from backend.app.modules.workbench.errors import GatewayError
from test_workshop_customization import ready_reply, request_key
from test_workshop_service import MarketFixture, matching_fixture


def model_workbench(key="test"):
    return SimpleNamespace(runtime=SimpleNamespace(provider=SimpleNamespace(key=key)))


class LanguageModelTests(unittest.TestCase):
    def test_both_audiences_keep_comparisons_precise_and_suggestions_supported(self):
        matcher = MatchingService(model_workbench(), {})
        skill = StrategyRewriteSkill(model_workbench(), {})
        prompts = []

        def reply(system, payload, *args, **kwargs):
            prompts.append(system)
            response = matching_fixture("高于就买、低于就卖") if "catalog" in payload else {
                k: v for k, v in ready_reply().items() if k in {"status", "plan", "summary", "assumptions", "questions", "dataGaps"}}
            return json.dumps(response, ensure_ascii=False)

        try:
            with patch("backend.app.modules.strategy_workshop.matching.Planner._call", side_effect=reply):
                for mode in ("beginner", "expert"):
                    matcher.match("高于就买、低于就卖", language_style=mode)
                    skill.prepare("10/30日，其他不变", {}, language_style=mode)
            self.assertEqual(len(prompts), 4)
            for system in prompts:
                self.assertIn("严格区分状态比较与穿越事件", system)
                self.assertIn("相等边界待确认", system)
                self.assertIn("不得建议“固定持有N日/持有若干天后卖出”", system)
                self.assertIn("不向用户罗列内部 SQLite、DSL、WASI、null", system)
            self.assertIn("用“现成方案”代替“模板”", prompts[0])
            self.assertIn("用“买多少”代替“仓位”", prompts[0])
        finally:
            matcher.close()
            skill.close()

    def test_matching_prompts_select_audience_without_changing_candidate_rules(self):
        service = MatchingService(model_workbench(), {})
        value = matching_fixture("10日平均价高于30日时买，否则卖")
        calls = []

        def reply(system, payload, *args, **kwargs):
            calls.append((system, copy.deepcopy(payload)))
            return json.dumps(value, ensure_ascii=False)

        try:
            with patch("backend.app.modules.strategy_workshop.matching.Planner._call", side_effect=reply):
                beginner = service.match("10日平均价高于30日时买，否则卖")
                expert = service.match("10日平均价高于30日时买，否则卖", language_style="expert")
            self.assertEqual(beginner, expert)
            self.assertEqual(calls[0][1]["catalog"], calls[1][1]["catalog"])
            self.assertIn("languageStyle=beginner", calls[0][0])
            self.assertIn("不假设用户懂任何金融术语", calls[0][0])
            self.assertIn("languageStyle=expert", calls[1][0])
            self.assertIn("准确的金融量化术语", calls[1][0])
            self.assertNotIn("这类面对用户说话的口语", calls[1][0])
            self.assertNotIn("先口语解释", calls[1][0])
        finally:
            service.close()

    def test_rewrite_prompt_mode_preserves_compiled_source_and_rule_numbers(self):
        skill = StrategyRewriteSkill(model_workbench(), {})
        value = {k: v for k, v in ready_reply().items() if k in {"status", "plan", "summary", "assumptions", "questions", "dataGaps"}}
        calls = []

        def reply(system, payload, *args, **kwargs):
            calls.append((system, copy.deepcopy(payload)))
            return json.dumps(value, ensure_ascii=False)

        try:
            with patch("backend.app.modules.strategy_workshop.rewriting.Planner._call", side_effect=reply):
                outputs = [skill.prepare("10/30日，其他不变", {}, language_style=mode) for mode in ("beginner", "expert")]
            self.assertEqual(compile_strategy(outputs[0]["plan"]), compile_strategy(outputs[1]["plan"]))
            self.assertEqual([item["languageStyle"] for item in outputs], ["beginner", "expert"])
            for (system, payload), mode in zip(calls, ("beginner", "expert")):
                self.assertEqual(payload["languageStyle"], mode)
                self.assertIn("languageStyle=" + mode, system)
                self.assertIn("比较运算符", system)
                self.assertIn("languageStyleVersion", outputs[0])
        finally:
            skill.close()

    def test_fallback_and_external_data_block_keep_semantics_in_both_styles(self):
        matcher = MatchingService(model_workbench(""), {})
        skill = StrategyRewriteSkill(model_workbench(), {})
        try:
            novice = matcher.match("上涨时买入")
            expert = matcher.match("上涨时买入", language_style="expert")
            for key in ("strategyId", "quality", "intentValidity", "fallbackReason"):
                self.assertEqual(novice[key], expert[key])
            self.assertIn("平均价格", novice["explanation"]["whyThisStrategy"])
            self.assertIn("SMA(20)", expert["explanation"]["whyThisStrategy"])
            self.assertNotEqual(novice["explanation"], expert["explanation"])
            response = {"status": "unsupported_data", "plan": None, "summary": "缺少微博热度", "assumptions": [], "questions": [], "dataGaps": ["微博热度历史"]}
            with patch.object(skill, "_call", return_value=json.dumps(response)):
                for mode in ("beginner", "expert"):
                    result = skill.prepare("按微博热度买卖", {}, language_style=mode)
                    self.assertEqual(result["message"], EXTERNAL_DATA_MESSAGE)
                    self.assertIsNone(result["plan"])
        finally:
            matcher.close()
            skill.close()


class LanguageServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cd80-style-")
        self.service = WorkshopService(object(), {"DATA_DIR": self.temp.name})
        self.service.market = MarketFixture()
        self.match_calls, self.rewrite_calls = [], []

        def match(text, language_style="beginner"):
            self.match_calls.append(language_style)
            return matching_fixture(text)

        def rewrite(*args, language_style="beginner"):
            self.rewrite_calls.append(language_style)
            return ready_reply()

        self.service.matching.match = match
        self.service.rewriting.prepare = rewrite
        self.owner = "style-owner"

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def session(self, **extra):
        return self.service.create_session(self.owner, {"idea": "10/30日，其他不变", "idempotencyKey": request_key(), **extra})

    def custom_body(self, session, **extra):
        return {"matchRevision": session["matchRevision"], "selectionDigest": session["candidate"]["selectionDigest"], "idempotencyKey": request_key(), **extra}

    def test_invalid_styles_rejected_before_generation_for_all_three_requests(self):
        session = self.session()
        self.match_calls.clear()
        for value in (None, True, 0, "", "professional", {}, []):
            calls = [
                lambda: self.session(languageStyle=value),
                lambda: self.service.rematch(self.owner, session["id"], {"idea": "重新匹配", "expectedRevision": 1, "idempotencyKey": request_key(), "languageStyle": value}),
                lambda: self.service.customization(self.owner, session["id"], self.custom_body(session, languageStyle=value)),
            ]
            for call in calls:
                with self.assertRaises(GatewayError) as error:
                    call()
                self.assertEqual(error.exception.status, 400)
                self.assertEqual(error.exception.field, "languageStyle")
        self.assertEqual(self.match_calls, [])
        self.assertEqual(self.rewrite_calls, [])

    def test_default_expert_and_rematch_history_retain_original_mode(self):
        first = self.session()
        before = self.service.conversation(self.owner, session_id=first["id"])["entries"]
        second = self.service.rematch(self.owner, first["id"], {"idea": "改20/60日", "expectedRevision": 1, "idempotencyKey": request_key(), "languageStyle": "expert"})
        after = self.service.conversation(self.owner, session_id=first["id"])["entries"]
        self.assertEqual(before, after[:len(before)])
        self.assertEqual([entry["languageStyle"] for entry in after if entry["role"] == "assistant"], ["beginner", "expert"])
        self.assertEqual(first["candidate"]["languageStyle"], "beginner")
        self.assertEqual(second["candidate"]["languageStyle"], "expert")
        self.assertEqual(second["candidate"]["languageStyleVersion"], LANGUAGE_STYLE_VERSION)
        self.assertEqual(self.match_calls, ["beginner", "expert"])
        self.assertEqual(self.service.session(self.owner, second["id"])["languageStyle"], "expert")

    def test_rewrite_versions_keep_mode_and_digest_is_bound_to_rules_not_style_field(self):
        session = self.session()
        expert = self.service.customization(self.owner, session["id"], self.custom_body(session, languageStyle="expert"))
        novice = self.service.customization(self.owner, session["id"], self.custom_body(expert))
        self.assertEqual(self.rewrite_calls, ["expert", "beginner"])
        self.assertEqual(expert["customization"]["rulePlan"], novice["customization"]["rulePlan"])
        self.assertEqual(expert["customization"]["sourceHash"], novice["customization"]["sourceHash"])
        frozen = self.service.repository.read(self.owner, "customization", expert["customization"]["id"])
        self.assertEqual(frozen["languageStyle"], "expert")
        self.assertEqual(frozen["rulesDigest"], digest({"customizationId": frozen["id"], "matchRevision": frozen["matchRevision"], "plan": frozen["rulePlan"], "summary": frozen["summary"], "assumptions": frozen["assumptions"], "sourceHash": frozen["sourceHash"]}))
        entries = self.service.conversation(self.owner, session_id=session["id"])["entries"]
        self.assertEqual([entry["languageStyle"] for entry in entries if entry["role"] == "assistant"], ["beginner", "expert", "beginner"])

    def test_style_metadata_does_not_change_fixed_candidate_digest_or_parameters(self):
        beginner = self.service._candidate("20/60日")
        expert = self.service._candidate("20/60日", "expert")
        for field in ("strategyId", "parameters", "sourceCode", "sourceHash", "catalogRevision", "selectionDigest"):
            self.assertEqual(beginner[field], expert[field])

    def test_style_only_metadata_keeps_approval_and_frozen_run_configuration(self):
        session = self.session(languageStyle="expert")
        self.service.selection(self.owner, session["id"], {**self.custom_body(session), "decision": "accept"})
        preflight = self.service.preflight(self.owner, session["id"], {"selectionRevision": 1, "symbol": "510300.SH", "startDate": "", "endDate": "", "initialCash": 100000, "idempotencyKey": request_key()})
        with self.service.repository.transaction() as db:
            current = self.service.repository.get(db, self.owner, "session", session["id"])
            current["languageStyle"] = "beginner"
            self.service.repository.put(db, self.owner, "session", current)
        self.assertEqual(self.service.session(self.owner, session["id"])["preflight"]["configDigest"], preflight["configDigest"])
        with patch.object(self.service.worker, "start"):
            run = self.service.create_run(self.owner, {"sessionId": session["id"], "preflightId": preflight["id"], "configDigest": preflight["configDigest"], "mode": "diagnostic", "diagnosticAcknowledged": True, "idempotencyKey": request_key()})
        self.assertEqual(run["configDigest"], preflight["configDigest"])
        self.assertEqual(run["candidate"]["languageStyle"], "expert")
        transcript = self.service.conversation(self.owner, run_id=run["id"])
        self.assertEqual(transcript["entries"][1]["languageStyle"], "expert")
        self.assertEqual(self.match_calls, ["expert"])


if __name__ == "__main__":
    unittest.main()
