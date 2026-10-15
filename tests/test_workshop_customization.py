"""Confirmed custom rules stay owner-bound and use the same frozen ledger."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from backend.app.modules.agent_platform.executor import SandboxExecutor
from backend.app.modules.strategy_workshop.custom_rules import compile_strategy, normalize_plan, plan_hash
from backend.app.modules.strategy_workshop.service import WorkshopService
from backend.app.modules.workbench.errors import GatewayError
from test_workshop_service import MarketFixture, matching_fixture


def request_key():
    return uuid4().hex


def sma_plan(fast=10, slow=30):
    left = {"type": "indicator", "name": "sma", "period": fast}
    right = {"type": "indicator", "name": "sma", "period": slow}
    return {"schema_version": 1, "name": "自定义10/30日均线", "description": "快线上穿慢线持有，反之退出",
            "features": [], "entry": {"type": "compare", "op": "gt", "left": left, "right": right},
            "exit": {"type": "compare", "op": "lte", "left": left, "right": right}}


def ready_reply():
    return {"status": "rules_ready", "plan": sma_plan(), "summary": "把20/60日改成10/30日，其余维持双均线逻辑。",
            "assumptions": ["其他规则沿用双均线模板"], "questions": [], "dataGaps": [],
            "skillVersion": "test-rewrite", "generationAttempts": 1, "repairFeedback": []}


class CustomMarketFixture(MarketFixture):
    def prepare(self, symbol, start, end, strategy_id, cash, custom_plan=None):
        self.custom_plan = custom_plan
        return super().prepare(symbol, start, end, strategy_id, cash)


class CustomizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cd80-custom-")
        self.service = WorkshopService(object(), {"DATA_DIR": self.temp.name, "STRATEGY_WORKSHOP_RUN_TIMEOUT": 30})
        self.service.market = CustomMarketFixture()
        self.service.matching.match = matching_fixture
        self.service.rewriting.prepare = lambda *args: ready_reply()
        self.owner = "custom-owner"
        self.session = self.service.create_session(self.owner, {"idea": "改成10/30日均线，其余不变", "idempotencyKey": request_key()})

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def error(self, code, callback, *args):
        with self.assertRaises(GatewayError) as raised:
            callback(*args)
        self.assertEqual(raised.exception.code, code)

    def body(self, session=None, **extra):
        session = session or self.session
        return {"matchRevision": session["matchRevision"], "selectionDigest": session["candidate"]["selectionDigest"],
                "idempotencyKey": request_key(), **extra}

    def customized(self):
        return self.service.customization(self.owner, self.session["id"], self.body())

    def confirm_body(self, doc):
        custom = doc["customization"]
        return {"customizationId": custom["id"], "rulesDigest": custom["rulesDigest"], "idempotencyKey": request_key()}

    @staticmethod
    def validation(plan, **kwargs):
        return {"passed": True, "sourceHash": hashlib.sha256(compile_strategy(plan).encode()).hexdigest(),
                "ruleHash": plan_hash(plan), "checks": ["fixture"]}

    def validated(self):
        doc = self.customized()
        with patch("backend.app.modules.strategy_workshop.custom_sandbox.validate_strategy", self.validation):
            return self.service.customization_confirm(self.owner, doc["id"], self.confirm_body(doc))

    def test_rewrite_idempotency_and_rule_confirmation_are_separate(self):
        calls = []
        self.service.rewriting.prepare = lambda *args: calls.append(args) or ready_reply()
        body = self.body()
        doc = self.service.customization(self.owner, self.session["id"], body)
        self.assertEqual(doc, self.service.customization(self.owner, self.session["id"], body))
        self.assertEqual(len(calls), 1)
        self.assertEqual(doc["customization"]["status"], "rules_ready")
        self.assertEqual(doc["candidate"]["strategyId"], "sma_trend")
        self.error("stale_selection", self.service.selection, self.owner, doc["id"], {**self.body(doc), "decision": "accept"})
        self.error("idempotency_conflict", self.service.customization, self.owner, doc["id"], {**body, "instructions": "different"})

    def test_clarifications_make_distinct_immutable_rule_versions(self):
        calls = []
        self.service.rewriting.prepare = lambda *args: calls.append(args) or ready_reply()
        first = self.service.customization(self.owner, self.session["id"], self.body(instructions="10/30日"))
        second = self.service.customization(self.owner, first["id"], self.body(first, instructions="其他不变"))
        self.assertNotEqual(first["customization"]["id"], second["customization"]["id"])
        self.assertEqual(calls[-1][-1], "10/30日\n\n其他不变")
        stored = self.service.repository.read(self.owner, "customization", first["customization"]["id"])
        self.assertEqual(stored["clarifications"], ["10/30日"])
        self.error("stale_customization", self.service.customization_confirm, self.owner, first["id"], self.confirm_body(first))

    def test_old_clarification_session_one_answer_reaches_rule_confirmation(self):
        calls = []
        def rewrite(*args, **kwargs):
            calls.append((args, kwargs))
            if len(calls) == 1:
                return {**ready_reply(), "status": "needs_clarification", "plan": None,
                        "summary": "建议短期10日、长期30日，退出规则沿用模板，请确认。",
                        "questions": ["是否采用10/30日并沿用模板？"]}
            return ready_reply()
        self.service.rewriting.prepare = rewrite
        question = self.customized()
        self.assertEqual(question["customization"]["alignmentQuestions"], ["是否采用10/30日并沿用模板？"])
        # A saved session from the earlier version has no alignment metadata.
        with self.service.repository.transaction() as db:
            saved = self.service.repository.get(db, self.owner, "session", question["id"])
            for field in ("alignmentCompleted", "alignmentQuestions", "alignmentIssues"):
                saved["customization"].pop(field, None)
            self.service.repository.put(db, self.owner, "session", saved)
        body = self.body(question, instructions="同意你上面的建议")
        ready = self.service.customization(self.owner, question["id"], body)
        self.assertEqual(ready, self.service.customization(self.owner, question["id"], body))
        self.assertEqual(len(calls), 2)
        context = calls[1][1]["alignment_context"]
        self.assertTrue(context["finalPass"])
        self.assertEqual(context["previousQuestions"], ["是否采用10/30日并沿用模板？"])
        self.assertEqual(context["previousSummary"], "建议短期10日、长期30日，退出规则沿用模板，请确认。")
        self.assertEqual(context["previousAssumptions"], ["其他规则沿用双均线模板"])
        self.assertEqual(context["latestAnswer"], "同意你上面的建议")
        self.assertEqual(ready["customization"]["status"], "rules_ready")
        self.assertTrue(ready["customization"]["alignmentCompleted"])
        self.assertEqual(ready["customization"]["questions"], [])
        with patch("backend.app.modules.strategy_workshop.custom_sandbox.validate_strategy", self.validation):
            confirmed = self.service.customization_confirm(self.owner, ready["id"], self.confirm_body(ready))
        self.assertEqual(confirmed["customization"]["status"], "validated")

    def test_answer_cannot_trigger_more_user_clarification_or_unverified_rules(self):
        calls = []
        def unresolved(*args, **kwargs):
            calls.append((args, kwargs))
            return {**ready_reply(), "status": "needs_clarification", "plan": None,
                    "summary": "退出条件仍未确定。", "questions": ["退出条件是什么？"]}
        self.service.rewriting.prepare = unresolved
        question = self.customized()
        self.error("alignment_answer_required", self.service.customization, self.owner, question["id"], self.body(question))
        self.assertEqual(len(calls), 1)
        answer = self.body(question, instructions="我仍未决定退出条件")
        stopped = self.service.customization(self.owner, question["id"], answer)
        custom = stopped["customization"]
        self.assertEqual(custom["status"], "alignment_incomplete")
        self.assertTrue(custom["alignmentCompleted"])
        self.assertEqual(custom["questions"], [])
        self.assertEqual(custom["alignmentIssues"], ["退出条件是什么？"])
        for field in ("rulePlan", "sourceCode", "sourceHash", "rulesDigest"):
            self.assertIsNone(custom[field])
        self.assertEqual(stopped, self.service.customization(self.owner, question["id"], answer))
        self.assertEqual(len(calls), 2)
        self.error("alignment_complete", self.service.customization, self.owner, question["id"],
                   self.body(stopped, instructions="再告诉我缺什么"))
        self.assertEqual(len(calls), 2)
        self.error("stale_customization", self.service.customization_confirm, self.owner, question["id"],
                   {"customizationId": custom["id"], "rulesDigest": "0" * 64, "idempotencyKey": request_key()})
        rematched = self.service.rematch(self.owner, question["id"], {"idea": "重新给出清楚的入场和退出条件",
            "expectedRevision": question["matchRevision"], "idempotencyKey": request_key()})
        self.assertIsNone(rematched["customization"])

    def test_completed_alignment_revision_never_starts_a_new_question_round(self):
        responses = [
            {**ready_reply(), "status": "needs_clarification", "plan": None,
             "summary": "请确认周期。", "questions": ["周期是多少？"]},
            ready_reply(),
            {**ready_reply(), "status": "needs_clarification", "plan": None,
             "summary": "修改后的退出条件未确定。", "questions": ["退出条件是什么？"]},
        ]
        calls = []
        def rewrite(*args, **kwargs):
            calls.append(kwargs)
            return responses.pop(0)
        self.service.rewriting.prepare = rewrite
        question = self.customized()
        ready = self.service.customization(self.owner, question["id"], self.body(question, instructions="周期10/30日，其他不变"))
        revised = self.service.customization(self.owner, ready["id"], self.body(ready, instructions="修改退出条件"))
        self.assertEqual(revised["customization"]["status"], "alignment_incomplete")
        self.assertTrue(calls[2]["alignment_context"]["finalPass"])

    def test_answer_requiring_new_external_data_stays_blocked(self):
        replies = [
            {**ready_reply(), "status": "needs_clarification", "plan": None,
             "summary": "需要明确买入信号。", "questions": ["买入信号依据什么？"]},
            {**ready_reply(), "status": "unsupported_data", "plan": None,
             "summary": "信号依赖新闻情绪历史数据。", "questions": [], "dataGaps": ["带日期的新闻情绪历史"]},
        ]
        self.service.rewriting.prepare = lambda *args, **kwargs: replies.pop(0)
        question = self.customized()
        blocked = self.service.customization(self.owner, question["id"],
            self.body(question, instructions="按新闻情绪买入，其他规则沿用模板"))
        self.assertEqual(blocked["customization"]["status"], "unsupported_data")
        self.assertTrue(blocked["customization"]["alignmentCompleted"])
        self.assertEqual(blocked["customization"]["dataGaps"], ["带日期的新闻情绪历史"])
        self.assertIsNone(blocked["customization"]["rulesDigest"])

    def test_bypassed_rewriter_cannot_label_unanswered_plan_ready(self):
        replies = [
            {**ready_reply(), "status": "needs_clarification", "plan": None,
             "summary": "请确定卖出条件。", "questions": ["卖出条件是什么？"]},
            {**ready_reply(), "questions": ["卖出条件仍未确定？"]},
        ]
        self.service.rewriting.prepare = lambda *args, **kwargs: replies.pop(0)
        question = self.customized()
        stopped = self.service.customization(self.owner, question["id"],
            self.body(question, instructions="还没有想好卖出条件"))
        custom = stopped["customization"]
        self.assertEqual(custom["status"], "alignment_incomplete")
        self.assertEqual(custom["alignmentIssues"], ["卖出条件仍未确定？"])
        self.assertIsNone(custom["rulePlan"])
        self.assertIsNone(custom["sourceCode"])

    def test_external_data_is_blocked_without_fabricated_price_plan(self):
        self.service.rewriting.prepare = lambda *args: {**ready_reply(), "status": "unsupported_data", "plan": None,
            "summary": "本平台暂不支持外源数据，如果你有数据，可以自行上传进行策略开发验证。",
            "dataGaps": ["微博热度"], "assumptions": []}
        doc = self.customized()
        custom = doc["customization"]
        self.assertEqual(custom["status"], "unsupported_data")
        self.assertIsNone(custom["sourceCode"])
        self.assertIsNone(custom["rulesDigest"])
        self.assertFalse(self.service.capabilities()["externalDataAvailable"])

    def test_validation_failure_cannot_select_or_backtest(self):
        doc = self.customized()
        with patch("backend.app.modules.strategy_workshop.custom_sandbox.validate_strategy", side_effect=ValueError("invalid")):
            failed = self.service.customization_confirm(self.owner, doc["id"], self.confirm_body(doc))
        self.assertEqual(failed["customization"]["status"], "validation_failed")
        self.error("stale_selection", self.service.selection, self.owner, failed["id"], {**self.body(failed), "decision": "accept"})

    def test_false_or_mismatched_validation_evidence_cannot_approve(self):
        for result in ({"passed": False}, {"passed": True, "sourceHash": "wrong", "ruleHash": "wrong"}):
            doc = self.customized()
            with patch("backend.app.modules.strategy_workshop.custom_sandbox.validate_strategy", return_value=result):
                failed = self.service.customization_confirm(self.owner, doc["id"], self.confirm_body(doc))
            self.assertEqual(failed["customization"]["status"], "validation_failed")
            self.assertEqual(failed["candidate"]["strategyId"], "sma_trend")

    def test_validated_candidate_requires_second_explicit_selection(self):
        doc = self.validated()
        self.assertEqual(doc["status"], "awaiting_custom_selection")
        self.assertEqual(doc["customization"]["status"], "validated")
        self.assertNotEqual(doc["candidate"]["selectionDigest"], self.session["candidate"]["selectionDigest"])
        accepted = self.service.selection(self.owner, doc["id"], {**self.body(doc), "decision": "accept"})
        self.assertEqual(accepted["status"], "configuring")
        frozen = self.service.repository.read(self.owner, "customization", doc["customization"]["id"])
        with self.service.repository.transaction() as db:
            self.error("immutable_record", self.service.repository.put, db, self.owner, "customization", frozen)

    def test_reset_preserves_template_and_old_custom_version(self):
        doc = self.validated()
        reset = self.service.customization_reset(self.owner, doc["id"], self.body(doc))
        self.assertEqual(reset["candidate"], self.session["candidate"])
        self.assertIsNone(reset["customization"])
        self.assertEqual(self.service.repository.read(self.owner, "customization", doc["customization"]["id"])["status"], "validated")

    def test_owner_and_rematch_invalidate_old_rule_confirmation(self):
        doc = self.customized()
        self.error("not_found", self.service.customization_confirm, "another-owner", doc["id"], self.confirm_body(doc))
        newer = self.service.rematch(self.owner, doc["id"], {"idea": "改成RSI", "expectedRevision": 1, "idempotencyKey": request_key()})
        self.assertIsNone(newer["customization"])
        self.error("stale_customization", self.service.customization_confirm, self.owner, doc["id"], self.confirm_body(doc))

    def test_compiler_change_requires_new_rewrite(self):
        doc = self.customized()
        with patch("backend.app.modules.strategy_workshop.service.custom_runtime_digest", return_value="changed"):
            self.error("custom_source_mismatch", self.service.customization_confirm, self.owner, doc["id"], self.confirm_body(doc))

    def test_run_freezes_clarifications_and_original_template_after_rematch(self):
        instructions = "使用10/30日均线，其他规则保持不变"
        doc = self.service.customization(self.owner, self.session["id"], self.body(instructions=instructions))
        with patch("backend.app.modules.strategy_workshop.custom_sandbox.validate_strategy", self.validation):
            confirmed = self.service.customization_confirm(self.owner, doc["id"], self.confirm_body(doc))
        self.service.selection(self.owner, doc["id"], {**self.body(confirmed), "decision": "accept"})
        preflight = self.service.preflight(self.owner, doc["id"], {"selectionRevision": 1, "symbol": "510300.SH",
            "startDate": "", "endDate": "", "initialCash": 100000, "idempotencyKey": request_key()})
        with patch.object(self.service.worker, "start"):
            run = self.service.create_run(self.owner, {"sessionId": doc["id"], "preflightId": preflight["id"],
                "configDigest": preflight["configDigest"], "mode": "diagnostic", "diagnosticAcknowledged": True,
                "idempotencyKey": request_key()})
        stored = self.service.repository.read(self.owner, "run", run["id"])
        archived = self.service.repository.load_snapshot(self.owner, stored["executionSnapshotId"])
        provenance = run["customizationProvenance"]
        self.assertEqual(provenance["originalIdea"], self.session["idea"])
        self.assertEqual(provenance["clarifications"], [instructions])
        self.assertEqual(provenance["baseStrategy"]["strategyId"], "sma_trend")
        self.assertEqual(provenance["baseStrategy"]["sourceHash"], self.session["candidate"]["sourceHash"])
        self.assertEqual(provenance["baseStrategy"]["version"], self.session["candidate"]["version"])
        self.service.matching.match = lambda text: {**matching_fixture(text), "strategyId": "rsi_reversion"}
        newer = self.service.rematch(self.owner, doc["id"], {"idea": "改成RSI策略", "expectedRevision": 1,
            "idempotencyKey": request_key()})
        self.assertEqual(newer["baseCandidate"]["strategyId"], "rsi_reversion")
        self.assertEqual(self.service.repository.load_snapshot(self.owner, stored["executionSnapshotId"]), archived)
        self.assertEqual(self.service.run(self.owner, run["id"])["customizationProvenance"], provenance)
        self.assertEqual(self.service.code(self.owner, run["id"])["provenance"], provenance)

    @unittest.skipUnless(SandboxExecutor().status()["available"], "Pinned WASI runtime unavailable")
    def test_real_wasi_validation_and_same_ledger_frozen_code(self):
        doc = self.customized()
        confirmed = self.service.customization_confirm(self.owner, doc["id"], self.confirm_body(doc))
        self.assertEqual(confirmed["customization"]["status"], "validated", confirmed["customization"]["validation"])
        self.assertIn("prefix_causality", confirmed["customization"]["validation"]["checks"])
        self.service.selection(self.owner, doc["id"], {**self.body(confirmed), "decision": "accept"})
        preflight = self.service.preflight(self.owner, doc["id"], {"selectionRevision": 1, "symbol": "510300.SH",
            "startDate": "", "endDate": "", "initialCash": 100000, "idempotencyKey": request_key()})
        self.assertEqual(self.service.market.custom_plan, normalize_plan(sma_plan()))
        run = self.service.create_run(self.owner, {"sessionId": doc["id"], "preflightId": preflight["id"],
            "configDigest": preflight["configDigest"], "mode": "diagnostic", "diagnosticAcknowledged": True,
            "idempotencyKey": request_key()})
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            result = self.service.run(self.owner, run["id"])
            if result["status"] in {"succeeded", "failed"}:
                break
            time.sleep(.05)
        self.assertEqual(result["status"], "succeeded", result)
        self.assertFalse(result["result"]["report_eligible"])
        self.assertEqual(result["result"]["custom_strategy"]["source_sha256"], confirmed["candidate"]["sourceHash"])
        self.assertEqual(result["result"]["custom_strategy"]["original_idea"], self.session["idea"])
        self.assertEqual(result["result"]["custom_strategy"]["base_strategy"]["sourceHash"], self.session["candidate"]["sourceHash"])
        code = self.service.code(self.owner, run["id"])
        self.assertEqual(code["sourceCode"], confirmed["candidate"]["sourceCode"])
        self.assertEqual(len(result["result"]["strategy_result"]["equity_curve"]), 150)


if __name__ == "__main__":
    unittest.main()
