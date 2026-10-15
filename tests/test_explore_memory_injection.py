"""Exercise the two Explore model paths that bypass structured model calls."""

from __future__ import annotations

import copy
import hashlib
import json
import threading
import unittest
from unittest.mock import patch

from backend.app.modules.explore.event_lite_runtime import EventLiteRuntime
from backend.app.modules.explore.memory_context import (
    MAX_MEMORY_CHARS, MEMORY_INSTRUCTION, inject_memory_payload, memory_payload,
    memory_trace,
)
from backend.app.modules.workbench.errors import GatewayError
from tests.test_explore_research_runtime import ScriptProvider, brief


def snapshot(content="A 的解释保留了待核实的资金变化。", *, version=1):
    return {"id": "memory-A", "chain_id": "chain-A", "parent_id": None,
            "run_id": "run-A", "phase": "initial_explanation", "version": version,
            "content": content,
            "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest()}


class CaptureTextProvider:
    model = "scripted-text"

    def __init__(self):
        self.messages = []

    def events(self, messages, _model, _stop, _on_response):
        self.messages.append(copy.deepcopy(messages))
        yield {"type": "token", "text": "本轮解释。"}
        yield {"type": "turn_end", "tool_calls": []}


class MemoryInjectionTests(unittest.TestCase):
    def test_payload_is_exact_bounded_text_and_metadata_is_content_free(self):
        original = snapshot()
        injected = inject_memory_payload({"question": "B"}, original)
        self.assertEqual(injected["research_memory"]["content"], original["content"])
        self.assertEqual(injected["research_memory"]["content_hash"], original["content_hash"])
        self.assertEqual(inject_memory_payload({"question": "A"}, None)["research_memory"], None)
        self.assertNotIn(original["content"], json.dumps(memory_trace(original), ensure_ascii=False))
        with self.assertRaises(ValueError):
            memory_payload({**original, "content": "被篡改的正文"})
        with self.assertRaises(ValueError):
            memory_payload(snapshot("太" * (MAX_MEMORY_CHARS + 1)))

    def test_event_first_text_request_sends_memory_body(self):
        memory = snapshot()
        provider = CaptureTextProvider()

        class Repository:
            calls = 0

            def validate_memory(self, owner, run_id, *, execution_id=None):
                self.calls += 1
                assert (owner, run_id, execution_id) == ("owner", "run-B", "execution-B")
                return memory

        class Service:
            repository = Repository()

            def _provider_for_deadline(self, _deadline):
                return provider

            @staticmethod
            def _check_stop(control):
                if control["stop"].is_set():
                    raise GatewayError("已停止", 409)

        runtime = EventLiteRuntime.__new__(EventLiteRuntime)
        runtime.service = Service()
        runtime.repo = runtime.service.repository
        runtime.owner = "owner"
        runtime.run = {"id": "run-B"}
        runtime.control = {"stop": threading.Event(), "response": None,
                           "execution_id": "execution-B"}
        runtime._initial_lock = threading.Lock()
        runtime._initial_tokens = []
        answer = runtime._plain_once([
            {"role": "system", "content": "回答用户。"},
            {"role": "user", "content": json.dumps({"question": "B"})},
        ])
        self.assertEqual(answer, "本轮解释。")
        self.assertEqual(runtime.repo.calls, 1)
        sent = provider.messages[0]
        self.assertIn(MEMORY_INSTRUCTION, sent[0]["content"])
        self.assertEqual(json.loads(sent[1]["content"])["research_memory"]["content"],
                         memory["content"])
        self.assertEqual(runtime.control["model_memory_trace"][0]["memory_hash"],
                         memory["content_hash"])
        self.assertNotIn(memory["content"], json.dumps(runtime.control["model_memory_trace"]))

    def test_research_loop_keeps_memory_on_each_model_turn(self):
        from tests.test_explore_research_runtime import ResearchRuntimeTests

        memory = snapshot("A 到 B 的研究进展与未解决问题。", version=2)
        fixture = ResearchRuntimeTests("test_each_product_module_loads_its_real_research_skill")
        fixture.setUp()
        self.addCleanup(fixture.tearDown)

        def turn(messages, _tools, *, done):
            task = json.loads(messages[1]["content"])["research_task"]
            return brief(task["subquestions"], done=done), []

        provider = ScriptProvider([
            lambda messages, tools: turn(messages, tools, done=False),
            lambda messages, tools: turn(messages, tools, done=True),
        ])
        runtime, service, _saved, control = fixture.make_runtime(
            provider, "根据前面的理解，继续核查 B 的证据。", module="history")
        with patch.object(service.repository, "validate_memory", return_value=memory, create=True) as validate:
            result = runtime.run_research()
        self.assertEqual(len(provider.turns), 2)
        self.assertGreaterEqual(validate.call_count, 3)  # preparation plus each provider call
        for turn_record in provider.turns:
            sent = turn_record["messages"]
            self.assertIn(MEMORY_INSTRUCTION, sent[0]["content"])
            self.assertEqual(json.loads(sent[1]["content"])["research_memory"]["content"],
                             memory["content"])
        self.assertEqual(len(result["retrieval"]["model_trace"]), 2)
        self.assertTrue(all(x["memory_hash"] == memory["content_hash"]
                            for x in result["retrieval"]["model_trace"]))
        self.assertNotIn(memory["content"], json.dumps(control["model_memory_trace"]))

    def test_research_refuses_version_change_before_next_provider_request(self):
        from tests.test_explore_research_runtime import ResearchRuntimeTests

        memory = snapshot("A 的总结。")
        changed = snapshot("A 的总结被替换。", version=2)
        fixture = ResearchRuntimeTests("test_each_product_module_loads_its_real_research_skill")
        fixture.setUp()
        self.addCleanup(fixture.tearDown)

        def first(messages, _tools):
            task = json.loads(messages[1]["content"])["research_task"]
            return brief(task["subquestions"], done=False), []

        provider = ScriptProvider([first])
        runtime, service, _saved, _control = fixture.make_runtime(
            provider, "继续核查 B 的证据。", module="history")
        with patch.object(service.repository, "validate_memory",
                          side_effect=[memory, memory, changed], create=True):
            with self.assertRaises(GatewayError) as failure:
                runtime.run_research()
        self.assertEqual(failure.exception.code, "explore_memory_changed")
        self.assertEqual(len(provider.turns), 1)


if __name__ == "__main__":
    unittest.main()
