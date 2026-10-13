"""Explore API tests use explicit fake news/model providers, never live services."""

import json
import re
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from backend.app import create_app
from backend.app.modules.explore.contracts import explicit_judgment, parse_json_object
from backend.app.modules.explore.repository import ExploreRepository, timestamp, uid
from backend.app.modules.workbench.builtin_tools import make_search_tool
from backend.app.modules.workbench.errors import GatewayError
from backend.app.modules.workbench.tool_registry import ToolRegistry


BASE = "/desktop/api/explore"


class FakeNews:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.empty = False
        self.unsafe_url = False
        self.include_historical = True

    def get_news(self, source="eastmoney", limit=100, offset=0, q=""):
        self.calls.append((source, limit, offset, q))
        if self.fail:
            raise GatewayError("测试新闻接口故障", 502)
        if self.empty or source == "cls":
            items = []
        else:
            now = datetime.now(timezone.utc)
            items = [
                {"id": "current", "source": source, "source_name": "东方财富财经快讯",
                 "title": "新能源政策影响光伏产业", "summary": "新能源政策近期发布，光伏产业可能受到影响。",
                 "published_at": (now - timedelta(days=2)).isoformat(),
                 "url": "javascript:alert(1)" if self.unsafe_url else "https://finance.eastmoney.com/a/123.html"},
                {"id": "historical", "source": source, "source_name": "东方财富财经快讯",
                 "title": "历史新能源政策影响光伏产业", "summary": "此前新能源政策与光伏产业的历史材料。",
                 "published_at": (now - timedelta(days=200)).isoformat(),
                 "url": "https://finance.eastmoney.com/a/456.html"},
            ]
            if not self.include_historical:
                items = items[:1]
        return {"items": items, "source_name": "东方财富财经快讯" if source == "eastmoney" else "财联社电报",
                "cache": {"stale": False, "fetched_at": timestamp()}}


class FakeSearch:
    """Explicit registered web-search replacement; unit tests never use network."""

    available = True

    def __init__(self):
        self.calls = []
        self.fail = False
        self.empty = False
        self.undated = False
        self.include_historical = True
        self.duplicate = False
        self.started = None
        self.release = None
        self.abortable = None

    def search(self, arguments, stopped, on_response):
        self.calls.append(dict(arguments))
        if self.abortable is not None:
            on_response(self.abortable)
        if self.started is not None:
            self.started.set()
            self.release.wait(timeout=5)
        if stopped.is_set():
            raise GatewayError("合成搜索已取消", 409)
        if self.fail:
            raise GatewayError("合成搜索故障", 502)
        now = datetime.now(timezone.utc)
        sources = [] if self.empty else [
            {"id": "web-current", "title": "新能源政策影响光伏产业的联网摘要",
             "snippet": "公开网页摘要：新能源政策及产业影响待核实。",
             "publishedAt": None if self.undated else (now - timedelta(days=1)).isoformat(),
             "url": "https://search.example.test/current"},
        ]
        if self.include_historical and not self.empty:
            sources.append({"id": "web-old", "title": "历史新能源政策与光伏产业案例",
                            "snippet": "公开网页摘要：过往政策案例，具体条件待核实。",
                            "publishedAt": None if self.undated else (now - timedelta(days=220)).isoformat(),
                            "url": "https://search.example.test/history"})
        if self.duplicate and sources:
            sources.append({**sources[0], "id": "duplicate"})
        return {"query": arguments["query"], "freshness": arguments["freshness"],
                "searchedAt": timestamp(), "sources": sources[:arguments["count"]]}


class FakeProvider:
    model = "explicit-test-double"

    def __init__(self):
        self.calls = []
        self.clarify_calls = []
        self.clarify_invalid = False
        self.meta_option_question = None
        self.force_ambiguous = False
        self.clarify_started = None
        self.clarify_release = None
        self.fail = None
        self.invalid = False
        self.inject_html = False
        self.malformed = False
        self.invalid_recommendation_type = False
        self.actors_override = None
        self.event_overview_override = None
        self.chunk_started = None
        self.chunk_release = None
        self.output_chars = 0
        self.started = None
        self.release = None
        self.abortable = None

    def json_tokens(self, messages, model, stopped, on_response):
        payload = json.loads(messages[1]["content"])
        if payload.get("task") == "clarify":
            self.clarify_calls.append(payload)
            if self.clarify_started is not None:
                self.clarify_started.set()
                self.clarify_release.wait(timeout=5)
                if stopped.is_set():
                    raise GatewayError("意图预判已停止", 409)
            if self.clarify_invalid:
                yield "{invalid"
                return
            question = payload["question"]
            ambiguous = (self.force_ambiguous or "[AMBIGUOUS_EXPLORE]" in question or
                         (payload["module"] == "history" and not explicit_judgment(question)) or
                         bool(re.search(r"^[\u4e00-\u9fffA-Za-z0-9]{2,20}发生了什么[？?]?$", question)) or
                         "有什么新闻" in question)
            material = next((item for item in payload["sources"] if item["kind"] != "user"), None)
            response = {"needs_clarification": ambiguous, "interpreted_question": question,
                        "summary": "已检索合成材料，具体内容须由用户核对。",
                        "question": "请明确要研究的具体事件或判断。" if ambiguous else "",
                        "options": ([{"id": "source_1", "label": material["title"],
                                     "question": self.meta_option_question or (
                                         "请历史验证：新能源政策是否利好光伏产业？" if payload["module"] == "history" else
                                         "请分析新能源政策影响光伏产业。"),
                                     "reason": "根据已得材料提出方向。", "source_refs": [material["id"]]}]
                                    if ambiguous and material else []),
                        "limitations": []}
            yield json.dumps(response, ensure_ascii=False)
            return
        self.calls.append(payload)
        if self.abortable is not None:
            on_response(self.abortable)
        if self.started is not None:
            self.started.set()
            self.release.wait(timeout=5)
        if self.fail:
            raise self.fail
        if self.malformed:
            yield "{broken-json"
            return
        sources = payload["sources"]
        source_id = next((item["id"] for item in sources if item["id"].startswith("S")),
                         sources[0]["id"] if sources else None)
        refs = [source_id] if source_id else []
        if self.invalid:
            refs = ["S999"]
        response = {
            "title": "新能源政策研究报告",
            "summary": "<img src=x onerror=alert(1)>" if self.inject_html else "基于本次材料的条件性分析。",
            "key_findings": ["政策影响取决于执行条件。"], "uncertainties": ["政策落地时点待确认。"],
            "sections": [{"heading": "证据与分析", "body": "需要核对后续公开信息。", "source_refs": refs}],
            "limitations": [],
            "recommendations": [{"question": "新能源政策将推动光伏产业吗？", "module": "relation", "reason": "核对路径"}],
        }
        if self.invalid_recommendation_type:
            response["recommendations"] = [{"question": "无法使用的推荐", "module": ["relation"], "reason": "坏类型"}]
        if payload["module"] == "event":
            response.update(event_overview=(self.event_overview_override if self.event_overview_override is not None
                                           else "政策事件概述"), background="政策背景待核对",
                            actors=self.actors_override if self.actors_override is not None else ["政策主体"],
                            drivers=["产业目标"], impact_paths=["政策到产业的传导依赖执行"],
                            timeline=[{"time": "未知", "event": "需要核实事件时间", "source_refs": refs}])
        elif payload["module"] == "relation":
            response.update(entities=["政策主体", "光伏产业"], relationships=[{
                "from": "政策主体", "to": "光伏产业", "relation_type": "政策影响", "direction": "前者影响后者",
                "mechanism": "执行规则改变产业预期", "conditions": "需实际落地", "source_refs": refs}])
        else:
            historical = payload["historical_source_ids"]
            response.update(judgment=payload["judgment"], conclusion="定性对照，未进行行情验证。",
                            cases=[{"event": "早期新能源政策", "similarities": "同属政策事件", "differences": "执行背景不同",
                                    "supporting_conditions": "政策实际执行", "failure_conditions": "政策未落地",
                                    "source_refs": [historical[0]]}] if historical else [])
        answer = json.dumps(response, ensure_ascii=False)
        self.output_chars = len(answer)
        if self.chunk_started is not None:
            yield answer[:25]
            self.chunk_started.set()
            self.chunk_release.wait(timeout=5)
            # Deliberately ignore cancellation here to exercise the service's
            # protection against an uncooperative upstream's late output.
            yield answer[25:]
        else:
            yield answer


class FakeAbortable:
    def __init__(self):
        self.aborts = 0

    def abort(self):
        self.aborts += 1


class ExploreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ficagent-explore-")
        self.app = create_app({"TESTING": True, "DATA_DIR": self.temp.name,
                               "AGENT_RUNTIME_DIR": str(Path(self.temp.name) / "runtime"),
                               "GITEE_AI_API_KEY": "test-only", "IMPORT_LEGACY_HISTORY": False})
        self.client = self.app.test_client()
        response = self.client.post("/desktop/api/register", json={"email": "a@example.test", "password": "long-password"})
        self.assertEqual(response.status_code, 200, response.json)
        self.owner = response.json["user"]["id"]
        self.service = self.app.extensions["explore_service"]
        self.news = FakeNews()
        self.search = FakeSearch()
        self.provider = FakeProvider()
        self.service.news_service = self.news
        self.service.search_tools = ToolRegistry((make_search_tool(self.search),))
        self.service.provider = self.provider

    def tearDown(self):
        self.app.extensions["workbench_service"].close()
        self.temp.cleanup()

    def session(self):
        response = self.client.post(BASE + "/sessions", json={"title": "政策研究"})
        self.assertEqual(response.status_code, 201, response.json)
        return response.json["session"]["id"]

    def submit(self, session_id, module="event", question="新能源政策影响光伏产业", **extra):
        body = {"module": module, "question": question, "idempotency_key": str(uuid4())}
        body.update(extra)
        response = self.client.post(BASE + f"/sessions/{session_id}/runs", json=body)
        self.assertEqual(response.status_code, 202, response.json)
        return response.json["run"], body

    def confirm_run(self, run, source_ids=None):
        ids = source_ids or [item["id"] for item in run["materials"]]
        response = self.client.post(BASE + f"/runs/{run['id']}/confirm", json={
            "idempotency_key": str(uuid4()), "revision": run["review"]["revision"], "source_ids": ids})
        self.assertEqual(response.status_code, 202, response.json)
        return response.json["run"]

    def legacy_review(self, session_id, module="event"):
        """A persisted pre-upgrade review remains confirmable after this rollout."""
        self.provider.force_ambiguous = True
        run, _ = self.submit(session_id, module, "新能源政策是否影响光伏产业？" if module == "history"
                             else "新能源政策影响光伏产业")
        waiting = self.wait(run["id"], {"awaiting_clarification"}, auto_review=False)
        self.provider.force_ambiguous = False
        review = {**waiting["review"], "needs_clarification": False,
                  "interpreted_question": "新能源政策是否影响光伏产业？" if module == "history"
                  else "新能源政策影响光伏产业", "question": ""}
        self.service.repository.transition(self.owner, run["id"], {"awaiting_clarification"},
                                           status="awaiting_review", stage="awaiting_review",
                                           fields={"review": review})
        return self.service.repository.get_run(self.owner, run["id"])

    def wait(self, run_id, expected={"succeeded", "partial", "failed", "cancelled"}, auto_review=True):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            response = self.client.get(BASE + "/runs/" + run_id)
            self.assertEqual(response.status_code, 200, response.json)
            run = response.json["run"]
            if run["status"] in expected:
                return run
            if auto_review and run["status"] == "awaiting_clarification":
                options = run["review"]["options"]
                answer = {"option_id": options[0]["id"]} if options else {
                    "question": "新能源政策是否利好光伏产业？" if run["module"] == "history" else
                                "请分析新能源政策影响光伏产业。"}
                clarified = self.client.post(BASE + f"/runs/{run_id}/clarify", json={
                    "idempotency_key": str(uuid4()), "revision": run["review"]["revision"], **answer})
                self.assertEqual(clarified.status_code, 202, clarified.json)
            elif auto_review and run["status"] == "awaiting_review":
                self.confirm_run(run)
            time.sleep(.02)
        self.fail("run did not complete")

    def test_three_modules_paths_repetition_branch_and_refresh_reads_only(self):
        session_id = self.session()
        root = None
        for path in ("eventrelationhistory", "eventeventevent", "relationrelationrelation"):
            parent = None
            for module in ("event" if path == "eventeventevent" else "relation" if path == "relationrelationrelation" else None,):
                pass
            modules = ({"eventrelationhistory": ["event", "relation", "history"],
                        "eventeventevent": ["event"] * 3,
                        "relationrelationrelation": ["relation"] * 3})[path]
            for module in modules:
                run, _ = self.submit(session_id, module,
                                     "新能源政策是否影响光伏产业" if module == "history" else "新能源政策影响光伏产业",
                                     source_report_id=parent)
                done = self.wait(run["id"])
                self.assertIn(done["status"], {"succeeded", "partial"}, done)
                self.assertIsNotNone(done["report"])
                if root is None:
                    root = done["report"]["id"]
                parent = done["report"]["id"]
        branch, _ = self.submit(session_id, "relation", "新能源政策影响光伏产业", source_report_id=root)
        branch = self.wait(branch["id"])
        self.assertEqual(branch["source_report_id"], root)
        self.assertEqual([item["report_id"] for item in branch["context_snapshot"]], [root])
        before = len(self.provider.calls)
        self.assertEqual(self.client.get(BASE + f"/sessions/{session_id}").status_code, 200)
        self.assertEqual(self.client.get(BASE + f"/runs/{branch['id']}").status_code, 200)
        self.assertEqual(len(self.provider.calls), before)
        detail = self.client.get(BASE + f"/sessions/{session_id}").json
        self.assertEqual(len(detail["runs"]), 10)
        self.assertEqual(detail["runs"][0]["report"]["id"], root)
        self.assertEqual(len(self.news.calls), before * 2)

    def test_history_awaiting_judgment_then_custom_question_runs(self):
        session_id = self.session()
        run, _ = self.submit(session_id, "history", "新能源政策影响光伏产业")
        review = self.wait(run["id"], {"awaiting_clarification"}, auto_review=False)
        self.assertTrue(review["materials"])
        self.assertTrue(self.news.calls)
        self.assertEqual(self.provider.calls, [])
        clarified = self.client.post(BASE + f"/runs/{run['id']}/clarify", json={
            "idempotency_key": str(uuid4()), "revision": review["review"]["revision"],
            "question": "新能源政策是否利好光伏产业？"})
        self.assertEqual(clarified.status_code, 202, clarified.json)
        done = self.wait(run["id"])
        self.assertEqual(done["report"]["judgment"], "新能源政策是否利好光伏产业？")
        self.assertEqual(done["report"]["mode"], "qualitative_no_market_validation")
        self.assertEqual(len(done["report"]["cases"]), 1)
        self.assertEqual(len(self.provider.calls), 1)

    def test_idempotency_conflict_retry_and_failure(self):
        session_id = self.session()
        self.provider.fail = GatewayError("模型超时", 504)
        run, body = self.submit(session_id)
        failure = self.wait(run["id"], {"failed"})
        self.assertIn("模型超时", failure["error"])
        same = self.client.post(BASE + f"/sessions/{session_id}/runs", json=body)
        self.assertEqual(same.json["run"]["id"], run["id"])
        self.assertEqual(len(self.provider.calls), 1)
        changed = self.client.post(BASE + f"/sessions/{session_id}/runs", json={**body, "module": "relation"})
        self.assertEqual(changed.status_code, 409)
        self.provider.fail = None
        retry = self.client.post(BASE + f"/runs/{run['id']}/retry", json={"idempotency_key": str(uuid4())})
        self.assertEqual(retry.status_code, 202, retry.json)
        second = self.wait(retry.json["run"]["id"])
        self.assertEqual(second["retry_of"], run["id"])
        self.assertEqual(failure["status"], "failed")
        self.assertNotEqual(second["id"], run["id"])

    def test_cancel_and_delete_prevent_late_success(self):
        session_id = self.session()
        self.provider.started = threading.Event()
        self.provider.release = threading.Event()
        run, _ = self.submit(session_id)
        self.assertTrue(self.provider.started.wait(3))
        control = self.service._active[(self.owner, run["id"])]
        files_before = {item["id"] for item in
                        self.client.get(BASE + f"/sessions/{session_id}/files").json["files"]}
        self.assertTrue(files_before)
        cancelled = self.client.post(BASE + f"/runs/{run['id']}/cancel")
        self.assertEqual(cancelled.json["run"]["status"], "cancelled")
        self.provider.release.set()
        control["thread"].join(timeout=3)
        self.assertEqual(self.wait(run["id"])["status"], "cancelled")
        self.assertIsNone(self.client.get(BASE + f"/runs/{run['id']}").json["run"]["report"])
        self.assertEqual({item["id"] for item in
                          self.client.get(BASE + f"/sessions/{session_id}/files").json["files"]}, files_before)
        self.assertFalse(self.service.repository.complete(self.owner, run["id"], {}, "succeeded"))
        self.provider.started = threading.Event()
        self.provider.release = threading.Event()
        run2, _ = self.submit(session_id)
        self.assertTrue(self.provider.started.wait(3))
        control2 = self.service._active[(self.owner, run2["id"])]
        self.assertEqual(self.client.delete(BASE + f"/sessions/{session_id}").status_code, 200)
        self.provider.release.set()
        control2["thread"].join(timeout=3)
        self.assertFalse(self.service.repository.complete(self.owner, run2["id"], {}, "succeeded"))
        self.assertEqual(self.client.get(BASE + f"/runs/{run2['id']}").status_code, 404)
        self.assertEqual(self.client.get(BASE + f"/sessions/{session_id}").status_code, 404)

    def test_account_isolation_all_resources_and_download(self):
        session_id = self.session()
        run, _ = self.submit(session_id)
        done = self.wait(run["id"])
        report_id = done["report"]["id"]
        other = self.app.test_client()
        self.assertEqual(other.post("/desktop/api/register", json={"email": "b@example.test", "password": "long-password"}).status_code, 200)
        for method, path, data in (
            ("get", f"/sessions/{session_id}", None), ("patch", f"/sessions/{session_id}", {"title": "抢占"}),
            ("delete", f"/sessions/{session_id}", None), ("post", f"/sessions/{session_id}/runs", {
                "module": "event", "question": "新能源政策", "idempotency_key": str(uuid4())}),
            ("get", f"/runs/{run['id']}", None), ("get", f"/runs/{run['id']}/events", None),
            ("post", f"/runs/{run['id']}/cancel", None),
            ("post", f"/runs/{run['id']}/retry", {"idempotency_key": str(uuid4())}),
            ("get", f"/reports/{report_id}", None), ("get", f"/reports/{report_id}/sources", None),
            ("get", f"/reports/{report_id}/markdown", None),
        ):
            response = getattr(other, method)(BASE + path, json=data) if data is not None else getattr(other, method)(BASE + path)
            self.assertEqual(response.status_code, 404, path)
        self.assertEqual(self.client.get(BASE + f"/reports/{report_id}").status_code, 200)

    def test_invalid_model_news_failure_empty_sources_and_safe_markdown(self):
        session_id = self.session()
        self.provider.invalid = True
        invalid, _ = self.submit(session_id)
        self.assertEqual(self.wait(invalid["id"], {"failed"})["status"], "failed")
        self.provider.invalid = False
        self.news.fail = True
        no_news, _ = self.submit(session_id)
        recovered = self.wait(no_news["id"])
        self.assertEqual(recovered["status"], "partial")
        self.assertTrue(any(item["kind"] == "web_search" for item in recovered["report"]["sources"]))
        self.search.fail = True
        both_down, _ = self.submit(session_id)
        no_external = self.wait(both_down["id"], {"partial"})
        self.assertEqual([item["id"] for item in no_external["materials"]], ["U1"])
        self.news.fail = False
        self.search.fail = False
        self.news.empty = True
        self.search.empty = True
        self.provider.inject_html = True
        empty, _ = self.submit(session_id)
        partial = self.wait(empty["id"], {"partial"})
        self.assertEqual(partial["report"]["sources"][-1]["id"], "U1")
        self.assertTrue(partial["report"]["limitations"])
        md = self.client.get(BASE + f"/reports/{partial['report']['id']}/markdown")
        self.assertEqual(md.status_code, 200)
        self.assertIn(b"attachment", md.headers["Content-Disposition"].encode())
        self.assertEqual(md.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("&lt;img", md.get_data(as_text=True))
        self.assertNotIn("<img", md.get_data(as_text=True))
        self.news.empty = False
        self.search.empty = False
        self.news.unsafe_url = True
        unsafe, _ = self.submit(session_id)
        done = self.wait(unsafe["id"])
        self.assertTrue(any(item["kind"] == "news" and item["url"] is None
                            for item in done["report"]["sources"]))
        link_only, _ = self.submit(session_id, question="https://unknown.example.com/a/1")
        self.assertEqual(self.wait(link_only["id"], {"awaiting_clarification"}, auto_review=False)["status"],
                         "awaiting_clarification")

    def test_restart_interrupts_active_run_without_model_call(self):
        session_id = self.session()
        repo = self.service.repository
        now = timestamp()
        record = {"id": uid(), "session_id": session_id, "module": "event", "question": "新能源政策影响光伏产业",
                  "source_report_id": None, "retry_of": None, "status": "queued", "stage": "queued",
                  "stage_history": [{"stage": "queued", "at": now}], "created_at": now, "updated_at": now,
                  "time_policy": {"as_of": now}, "context_snapshot": [], "judgment": None, "error": None}
        repo.create_run(self.owner, {"idempotency_key": str(uuid4()), "session_id": session_id}, record, 2)
        recovered = ExploreRepository(self.temp.name)
        self.assertEqual(recovered.get_run(self.owner, record["id"])["status"], "interrupted")
        self.assertEqual(len(self.provider.calls), 0)

    def test_deadline_persists_failure_and_aborts_late_model(self):
        session_id = self.session()
        self.provider.started = threading.Event()
        self.provider.release = threading.Event()
        self.provider.abortable = FakeAbortable()
        self.service.timeout = .08  # Test-only shortened deadline, production default is 180 s.
        run, _ = self.submit(session_id)
        self.assertTrue(self.provider.started.wait(3))
        control = self.service._active[(self.owner, run["id"])]
        failed = self.wait(run["id"], {"failed"})
        self.assertIn("时间上限", failed["error"])
        self.assertGreaterEqual(self.provider.abortable.aborts, 1)
        self.provider.release.set()
        control["thread"].join(timeout=3)
        self.assertIsNone(self.service.repository.get_run(self.owner, run["id"])["report"])
        self.assertFalse(self.service.repository.complete(self.owner, run["id"], {}, "succeeded"))

    def test_untrusted_request_types_time_policy_and_judgment_detection(self):
        session_id = self.session()
        for changes in ({"module": ["event"]}, {"module": {"bad": True}},
                        {"source_report_id": ["not-an-id"]}, {"source_report_id": []},
                        {"time_policy": {"mode": "historical"}},
                        {"as_of": "2020-01-01"}):
            body = {"module": "event", "question": "新能源政策影响光伏产业",
                    "idempotency_key": str(uuid4()), **changes}
            response = self.client.post(BASE + f"/sessions/{session_id}/runs", json=body)
            self.assertEqual(response.status_code, 400, (changes, response.json))
        run, _ = self.submit(session_id, "history", "请历史验证这条新能源政策新闻")
        self.assertEqual(self.wait(run["id"], {"awaiting_clarification"}, auto_review=False)["status"],
                         "awaiting_clarification")
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.client.post(BASE + f"/runs/{run['id']}/cancel").status_code, 200)
        self.assertEqual(parse_json_object('{"text":"first\nsecond"}')["text"], "first\nsecond")
        long_news = "新闻标题：某公司股价上涨\n新闻原文：" + "政策发布后仍待核实。" * 110
        long_run, _ = self.submit(session_id, "history", long_news)
        self.assertEqual(self.wait(long_run["id"], {"awaiting_clarification"}, auto_review=False)["status"],
                         "awaiting_clarification")
        self.assertEqual(self.provider.calls, [])

    def test_exact_trusted_url_excludes_unrelated_window_items(self):
        session_id = self.session()
        run, _ = self.submit(session_id, question="新能源政策影响光伏产业\n来源：https://finance.eastmoney.com/a/123.html")
        done = self.wait(run["id"])
        news_sources = [item for item in done["report"]["sources"] if item["kind"] == "news"]
        self.assertEqual(len(news_sources), 1)
        self.assertEqual(news_sources[0]["provider_id"], "current")

    def test_no_historical_material_and_optional_bad_recommendation(self):
        session_id = self.session()
        self.news.include_historical = False
        self.search.include_historical = False
        self.provider.invalid_recommendation_type = True
        run, _ = self.submit(session_id, "history", "新能源政策是否利好光伏产业？")
        done = self.wait(run["id"], {"partial"})
        self.assertEqual(done["report"]["cases"], [])
        self.assertEqual(done["report"]["recommendations"], [])
        self.assertTrue(any("历史材料" in item for item in done["report"]["limitations"]))
        self.provider.malformed = True
        malformed, _ = self.submit(session_id)
        self.assertEqual(self.wait(malformed["id"], {"failed"})["status"], "failed")

    def test_ancestor_limit_is_disclosed_and_source_metadata_preserved(self):
        session_id = self.session()
        parent = None
        for _ in range(6):
            run, _ = self.submit(session_id, source_report_id=parent)
            done = self.wait(run["id"])
            parent = done["report"]["id"]
        self.assertEqual(len(done["context_snapshot"]), 4)
        self.assertTrue(done["context_snapshot"][-1]["ancestors_truncated"])
        self.assertTrue(done["context_snapshot"][0]["sources"][0]["published_at"])
        self.assertTrue(any("祖先超过 4 层" in item for item in done["report"]["limitations"]))

    def test_web_search_runs_for_three_modules_and_records_real_query_scope(self):
        session_id = self.session()
        for question, module, freshness in (
            ("今日新能源政策影响光伏产业？", "event", "oneDay"),
            ("最近一个月新能源政策影响光伏产业？", "relation", "oneMonth"),
            ("新能源政策是否利好光伏产业？", "history", "noLimit"),
        ):
            before = len(self.search.calls)
            run, _ = self.submit(session_id, module, question)
            done = self.wait(run["id"])
            self.assertIn(done["status"], {"succeeded", "partial"})
            calls = self.search.calls[before:]
            self.assertEqual(len(calls), 1)
            self.assertTrue(all(call["freshness"] == freshness and call["count"] == 5 for call in calls))
            searches = done["report"]["retrieval"]["searches"]
            self.assertEqual(len(searches), len(calls))
            self.assertTrue(all(item["status"] == "complete" and item["count"] > 0 for item in searches))
            self.assertTrue(any(source["kind"] == "web_search" for source in done["report"]["sources"]))
            self.assertIn("联网搜索", self.client.get(BASE + f"/reports/{done['report']['id']}/markdown").get_data(as_text=True))
        # History no longer generates a fixed suffix query unrelated to a concrete gap.
        self.assertEqual(searches[0]["status"], "complete")
        urls = [source["url"] for source in done["report"]["sources"] if source["url"]]
        self.assertEqual(len(urls), len(set(urls)))

    def test_event_accepts_eleven_actors_seen_in_live_model_reply(self):
        # These 11 strings reproduce the actors list observed in the actual
        # saved model reply at data/explore-search-fix/live/event-model-output.txt.
        # The transport here remains an explicit test double, not a live call.
        self.provider.actors_override = [
            "美国贸易代表办公室及多国贸易部长", "粤芯半导体", "中芯国际、华虹、长鑫存储", "中微公司",
            "ASML", "三星电子", "SK海力士", "华为", "英飞凌",
            "台积电与英特尔（Terafab合作讨论）", "印度政府",
        ]
        session_id = self.session()
        run, _ = self.submit(session_id, question="半导体产业最近有什么新事件？")
        done = self.wait(run["id"])
        self.assertIn(done["status"], {"succeeded", "partial"})
        self.assertEqual(done["report"]["actors"], self.provider.actors_override)

    def test_report_v3_schema_and_observed_event_overview_object(self):
        self.assertIn("event_overview 字符串≤4000", self.service.prompts["event"])
        self.assertIn("relationships 数组，0-16 个对象", self.service.prompts["relation"])
        self.assertIn("cases 数组，0-5 个对象", self.service.prompts["history"])
        self.provider.event_overview_override = {
            "event_type": "市场行情事件", "occurred_at": "2026-10-13（美股前一交易日收盘）",
            "key_parties": "闪迪、SK海力士、美光"}
        session_id = self.session()
        run, _ = self.submit(session_id, question="存储芯片板块集中下跌发生了什么？")
        done = self.wait(run["id"])
        self.assertEqual(done["prompt_version"], "event@4")
        self.assertIn(done["status"], {"succeeded", "partial"})
        self.assertEqual(done["report"]["event_overview"],
                         "事件类型：市场行情事件；发生时间：2026-10-13（美股前一交易日收盘）；相关主体：闪迪、SK海力士、美光")
        self.assertIn("相关主体：闪迪", done["report"]["markdown"])
        for malformed in ({"event_type": "市场行情事件", "occurred_at": "未知", "key_parties": "甲", "extra": "不应接受"},
                          {"event_type": ["错误类型"], "occurred_at": "未知", "key_parties": "甲"}):
            with self.subTest(malformed=malformed):
                self.provider.event_overview_override = malformed
                bad, _ = self.submit(session_id, question="存储芯片板块下跌的情况")
                failed = self.wait(bad["id"])
                self.assertEqual(failed["status"], "failed")
                self.assertIsNone(failed["report"])

    def test_search_short_question_pasted_text_and_selected_report_context(self):
        session_id = self.session()
        first, _ = self.submit(session_id, question="光伏政策？")
        first = self.wait(first["id"])
        self.assertEqual(self.search.calls[-1]["query"], "光伏政策")
        article = "新闻原文：" + "冗长的合成新闻正文与重复段落。" * 150
        before = len(self.search.calls)
        second, _ = self.submit(session_id, question=article + "\n问题：新能源政策影响光伏产业吗？")
        deadline = time.monotonic() + 3
        while len(self.search.calls) == before and time.monotonic() < deadline:
            time.sleep(.01)
        initial_query = self.search.calls[before]["query"]
        self.wait(second["id"])
        self.assertEqual(initial_query, "新能源政策影响光伏产业吗")
        self.assertNotIn("冗长", initial_query)
        followup, _ = self.submit(session_id, question="这项政策可能通过哪些环节影响A股产业链？",
                                  source_report_id=first["report"]["id"])
        done = self.wait(followup["id"])
        self.assertTrue(self.search.calls[-1]["query"].startswith(first["report"]["title"]))
        self.assertEqual([item["report_id"] for item in done["context_snapshot"]], [first["report"]["id"]])

    def test_search_query_ignores_following_uncertainty_and_constraints(self):
        session_id = self.session()
        first, _ = self.submit(session_id, question="伊朗发生了什么？我还未确定具体事件，请先给我材料。")
        self.wait(first["id"], {"awaiting_clarification"}, auto_review=False)
        self.assertEqual(self.search.calls[-1]["query"], "伊朗发生了什么")
        second, _ = self.submit(session_id, "history", question=(
            "半导体产业是否会受到出口政策影响？不用最新新闻冒充历史证据。"))
        self.wait(second["id"])
        self.assertEqual(self.search.calls[-1]["query"], "半导体产业是否会受到出口政策影响")
        self.assertNotIn("不用", self.search.calls[-1]["query"])

    def test_legacy_clear_review_can_reword_question_and_waiting_cancel(self):
        session_id = self.session()
        ready = self.legacy_review(session_id)
        run = ready
        self.assertEqual(self.provider.calls, [])
        first_revision = ready["review"]["revision"]
        changed = self.client.post(BASE + f"/runs/{run['id']}/clarify", json={
            "idempotency_key": str(uuid4()), "revision": first_revision,
            "question": "新能源政策是否影响光伏产业？"})
        self.assertEqual(changed.status_code, 202, changed.json)
        second = self.wait(run["id"])
        self.assertEqual(second["review"]["revision"], first_revision + 1)
        self.assertEqual(len(self.provider.calls), 1)
        waiting = self.legacy_review(session_id)
        cancelled = self.client.post(BASE + f"/runs/{waiting['id']}/cancel")
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json["run"]["materials"], [])
        self.assertEqual(cancelled.json["run"]["review"], None)

    def test_search_failure_empty_and_undated_fallback_are_disclosed(self):
        session_id = self.session()
        self.search.fail = True
        failed_search, _ = self.submit(session_id)
        done = self.wait(failed_search["id"])
        self.assertEqual(done["status"], "partial")
        self.assertEqual(done["report"]["retrieval"]["searches"][0]["status"], "failed")
        self.assertTrue(any(item["kind"] == "news" for item in done["report"]["sources"]))
        self.search.fail = False
        self.search.empty = True
        empty_search, _ = self.submit(session_id)
        done = self.wait(empty_search["id"])
        self.assertEqual(done["report"]["retrieval"]["searches"][0]["status"], "empty")
        self.search.empty = False
        self.search.undated = True
        self.news.empty = True
        undated, _ = self.submit(session_id, "history", "新能源政策是否利好光伏产业？")
        done = self.wait(undated["id"])
        self.assertEqual(done["report"]["cases"], [])
        self.assertTrue(any(item["kind"] == "web_search" and item["published_at"] is None
                            for item in done["report"]["sources"]))
        self.assertTrue(any("没有可核实发布日期" in item for item in done["report"]["limitations"]))

    def test_web_source_quota_and_url_dedup(self):
        session_id = self.session()
        self.search.duplicate = True
        original = self.news.get_news

        def many_news(*args, **kwargs):
            page = original(*args, **kwargs)
            if kwargs.get("source") == "eastmoney":
                page["items"] = [{**page["items"][0], "id": f"n{index}",
                                  "url": f"https://finance.eastmoney.com/a/{index}.html"}
                                 for index in range(12)]
            return page

        self.news.get_news = many_news
        run, _ = self.submit(session_id)
        done = self.wait(run["id"])
        kinds = [item["kind"] for item in done["report"]["sources"]]
        self.assertGreaterEqual(kinds.count("web_search"), 2)
        self.assertGreaterEqual(kinds.count("news"), 2)
        urls = [item["url"] for item in done["report"]["sources"] if item["url"]]
        self.assertEqual(len(urls), len(set(urls)))
        self.assertLessEqual(len(done["report"]["sources"]), 8)

    def test_cancel_and_timeout_during_search_do_not_start_model(self):
        session_id = self.session()
        self.search.started = threading.Event()
        self.search.release = threading.Event()
        self.search.abortable = FakeAbortable()
        run, _ = self.submit(session_id)
        self.assertTrue(self.search.started.wait(3))
        control = self.service._active[(self.owner, run["id"])]
        self.assertEqual(self.client.post(BASE + f"/runs/{run['id']}/cancel").json["run"]["status"], "cancelled")
        self.search.release.set()
        control["thread"].join(timeout=3)
        self.assertEqual(self.wait(run["id"])["status"], "cancelled")
        self.assertEqual(self.provider.calls, [])
        self.assertGreaterEqual(self.search.abortable.aborts, 1)
        self.search.started = threading.Event()
        self.search.release = threading.Event()
        self.search.abortable = FakeAbortable()
        self.service.timeout = .08
        timed, _ = self.submit(session_id)
        self.assertTrue(self.search.started.wait(3))
        control = self.service._active[(self.owner, timed["id"])]
        self.assertEqual(self.wait(timed["id"], {"failed"})["status"], "failed")
        self.search.release.set()
        control["thread"].join(timeout=3)
        self.assertEqual(self.provider.calls, [])
        self.assertIsNone(self.service.repository.get_run(self.owner, timed["id"])["report"])
        self.assertGreaterEqual(self.search.abortable.aborts, 1)

    def test_materials_are_saved_before_intent_model_and_cancel_clears_them(self):
        session_id = self.session()
        self.provider.clarify_started = threading.Event()
        self.provider.clarify_release = threading.Event()
        run, _ = self.submit(session_id)
        self.assertTrue(self.provider.clarify_started.wait(3))
        checking = self.client.get(BASE + f"/runs/{run['id']}").json["run"]
        self.assertEqual(checking["stage"], "checking_intent")
        self.assertTrue(checking["materials"])
        self.assertTrue(checking["retrieval"]["searches"])
        self.assertEqual(self.provider.calls, [])
        control = self.service._active[(self.owner, run["id"])]
        self.assertEqual(self.client.post(BASE + f"/runs/{run['id']}/cancel").json["run"]["status"], "cancelled")
        self.provider.clarify_release.set()
        control["thread"].join(timeout=3)
        cancelled = self.client.get(BASE + f"/runs/{run['id']}").json["run"]
        self.assertEqual(cancelled["materials"], [])
        self.assertEqual(cancelled["retrieval"], {})
        self.assertEqual(self.provider.calls, [])

    def test_public_output_progress_is_saved_before_report_and_reads_do_not_execute(self):
        self.provider.chunk_started = threading.Event()
        self.provider.chunk_release = threading.Event()
        session_id = self.session()
        run, _ = self.submit(session_id)
        self.assertTrue(self.provider.chunk_started.wait(3))
        current = self.client.get(BASE + f"/runs/{run['id']}").json["run"]
        self.assertEqual(current["stage"], "analyzing")
        self.assertIsNone(current["report"])
        self.assertEqual(set(current["model_progress"]), {"task", "output_chars", "updated_at"})
        self.assertEqual(current["model_progress"]["task"], "report")
        self.assertEqual(current["model_progress"]["output_chars"], 25)
        self.assertTrue(current["materials"])
        self.assertTrue(current["review"]["interpreted_question"])
        before = len(self.provider.calls)
        self.client.get(BASE + f"/runs/{run['id']}")
        self.assertEqual(len(self.provider.calls), before)
        other = self.app.test_client()
        other.post("/desktop/api/register", json={"email": "progress@example.test", "password": "long-password"})
        self.assertEqual(other.get(BASE + f"/runs/{run['id']}").status_code, 404)
        self.provider.chunk_release.set()
        done = self.wait(run["id"])
        self.assertEqual(done["model_progress"]["output_chars"], self.provider.output_chars)
        self.assertIsNotNone(done["report"])

    def test_cancel_delete_and_stale_execution_reject_late_progress(self):
        for action in ("cancel", "delete", "replace_execution"):
            with self.subTest(action=action):
                self.provider.chunk_started = threading.Event()
                self.provider.chunk_release = threading.Event()
                session_id = self.session()
                run, _ = self.submit(session_id)
                self.assertTrue(self.provider.chunk_started.wait(3))
                control = self.service._active[(self.owner, run["id"])]
                before = self.service.repository.get_run(self.owner, run["id"])["model_progress"]
                if action == "cancel":
                    self.client.post(BASE + f"/runs/{run['id']}/cancel")
                elif action == "delete":
                    self.client.delete(BASE + f"/sessions/{session_id}")
                else:
                    self.service.repository.transition(self.owner, run["id"], {"running"},
                                                       fields={"execution_id": uid()})
                # Exercise the same callback even if an upstream races the stop.
                with self.assertRaises(GatewayError):
                    self.service._model_progress(self.owner, run["id"], control, "report")(999)
                self.provider.chunk_release.set()
                control["thread"].join(timeout=3)
                if action == "delete":
                    self.assertEqual(self.client.get(BASE + f"/runs/{run['id']}").status_code, 404)
                else:
                    saved = self.service.repository.get_run(self.owner, run["id"])
                    self.assertEqual(saved["model_progress"], before)
                    self.assertIsNone(saved["report"])
                    if action == "cancel":
                        self.assertEqual(saved["status"], "cancelled")
                    else:
                        self.client.post(BASE + f"/runs/{run['id']}/cancel")

    def test_all_modules_clear_questions_report_without_confirmation(self):
        for module, question in (("event", "新能源政策影响光伏产业"),
                                 ("relation", "新能源政策影响光伏产业的关系路径"),
                                 ("history", "新能源政策是否利好光伏产业？")):
            session_id = self.session()
            before = len(self.provider.calls)
            run, _ = self.submit(session_id, module, question)
            done = self.wait(run["id"])
            self.assertEqual(len(self.provider.calls), before + 1)
            self.assertTrue(done["materials"])
            self.assertTrue(done["retrieval"]["searches"])
            self.assertEqual(done["review"]["revision"], 1)
            clarify_payload = self.provider.clarify_calls[-1]
            self.assertEqual(clarify_payload["task"], "clarify")
            self.assertEqual([item["text_used"] for item in clarify_payload["sources"]],
                             [item["text_used"] for item in done["materials"]])
            chosen = next(item for item in done["materials"] if item["kind"] == "web_search")
            self.assertEqual(chosen["content_kind"], "search_snippet")
            self.assertTrue(chosen["url"])
            self.assertEqual([item["id"] for item in done["report"]["sources"]],
                             [item["id"] for item in done["materials"]])
            self.assertEqual(done["report"]["confirmed_sources"], done["report"]["sources"])
            self.assertEqual(self.provider.calls[-1]["task"], "report")
            self.assertEqual([item["text_used"] for item in self.provider.calls[-1]["sources"]],
                             [item["text_used"] for item in done["materials"]])

    def test_legacy_review_confirm_uses_only_selected_text(self):
        session_id = self.session()
        ready = self.legacy_review(session_id)
        chosen = next(item for item in ready["materials"] if item["kind"] == "web_search")
        self.assertEqual(self.provider.calls, [])
        body = {"idempotency_key": str(uuid4()), "revision": ready["review"]["revision"],
                "source_ids": [chosen["id"]]}
        self.assertEqual(self.client.post(BASE + f"/runs/{ready['id']}/confirm", json=body).status_code, 202)
        done = self.wait(ready["id"])
        self.assertEqual([item["id"] for item in done["report"]["sources"]], [chosen["id"]])
        self.assertEqual([item["id"] for item in self.provider.calls[-1]["sources"]], [chosen["id"]])
        before = len(self.provider.calls)
        self.assertEqual(self.client.post(BASE + f"/runs/{ready['id']}/confirm", json=body).status_code, 202)
        self.assertEqual(len(self.provider.calls), before)
        self.assertEqual(self.client.post(BASE + f"/runs/{ready['id']}/confirm", json={
            **body, "source_ids": ["U1"]}).status_code, 409)

    def test_three_ambiguous_options_each_directly_run_without_second_intent(self):
        session_id = self.session()
        self.provider.force_ambiguous = True
        for module in ("event", "relation", "history"):
            for index in range(3):
                question = "新能源政策影响光伏产业" if module != "history" else "新能源政策会怎样影响光伏产业"
                run, _ = self.submit(session_id, module, question)
                waiting = self.wait(run["id"], {"awaiting_clarification"}, auto_review=False)
                options = waiting["review"]["options"]
                self.assertEqual(len(options), 3)
                self.assertEqual(len({item["question"] for item in options}), 3)
                before_intent = len(self.provider.clarify_calls)
                before_search = len(self.search.calls)
                body = {"idempotency_key": str(uuid4()), "revision": waiting["review"]["revision"],
                        "option_id": options[index]["id"]}
                response = self.client.post(BASE + f"/runs/{run['id']}/clarify", json=body)
                self.assertEqual(response.status_code, 202, response.json)
                done = self.wait(run["id"])
                self.assertIn(done["status"], {"succeeded", "partial"})
                self.assertEqual(len(self.provider.clarify_calls), before_intent)
                self.assertGreater(len(self.search.calls), before_search)
                self.assertEqual(done["resolved_question"], options[index]["question"])
                self.assertEqual(done["report"]["resolved_question"], options[index]["question"])
                self.assertEqual(self.client.post(BASE + f"/runs/{run['id']}/clarify", json=body).status_code, 202)
                self.assertEqual(len(self.provider.clarify_calls), before_intent)

    def test_multiround_clarify_and_confirm_have_revision_cas_and_idempotency(self):
        session_id = self.session()
        self.provider.force_ambiguous = True
        run, _ = self.submit(session_id, question="新能源政策影响光伏产业")
        first = self.wait(run["id"], {"awaiting_clarification"}, auto_review=False)
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(first["review"]["revision"], 1)
        key = str(uuid4())
        body = {"idempotency_key": key, "revision": 1, "question": "新能源政策影响光伏产业的具体路径"}
        self.assertEqual(self.client.post(BASE + f"/runs/{run['id']}/clarify", json=body).status_code, 202)
        second = self.wait(run["id"], {"awaiting_clarification"}, auto_review=False)
        self.assertEqual(second["review"]["revision"], 2)
        before_search = len(self.search.calls)
        self.assertEqual(self.client.post(BASE + f"/runs/{run['id']}/clarify", json=body).status_code, 202)
        self.assertEqual(len(self.search.calls), before_search)
        self.assertEqual(self.client.post(BASE + f"/runs/{run['id']}/clarify", json={**body, "question": "另一问题"}).status_code, 409)
        self.assertEqual(self.client.post(BASE + f"/runs/{run['id']}/clarify", json={
            "idempotency_key": str(uuid4()), "revision": 1, "question": "旧页面问题"}).status_code, 409)
        self.provider.force_ambiguous = False
        self.assertEqual(self.client.post(BASE + f"/runs/{run['id']}/clarify", json={
            "idempotency_key": str(uuid4()), "revision": 2,
            "question": "新能源政策是否会影响光伏产业？"}).status_code, 202)
        done = self.wait(run["id"])
        self.assertEqual(done["review"]["revision"], 3)
        self.assertEqual(len(done["clarification_history"]), 2)
        self.assertEqual(done["report"]["resolved_question"], "新能源政策是否会影响光伏产业？")
        self.assertEqual(len(done["report"]["clarification_history"]), 2)
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(self.client.post(BASE + f"/runs/{run['id']}/confirm", json={
            "idempotency_key": str(uuid4()), "revision": 2, "source_ids": ["U1"]}).status_code, 409)

    def test_selected_direction_supersedes_initial_ambiguous_input(self):
        session_id = self.session()
        original = "半导体最近发生了什么？我还没有确定要研究哪一件事。"
        meta = "是否以近期全球半导体股集中下跌这一事件为研究对象？"
        self.provider.force_ambiguous = True
        self.provider.meta_option_question = meta
        run, _ = self.submit(session_id, question=original)
        first = self.wait(run["id"], {"awaiting_clarification"}, auto_review=False)
        self.assertEqual(first["intent_prompt_version"], "clarify@3")
        self.assertIn("请分析近期全球半导体股集中下跌", first["review"]["options"][0]["question"])
        # Old saved reviews may still contain the meta-question from clarify@1.
        old_review = {**first["review"], "options": [{**first["review"]["options"][0], "question": meta}]}
        self.service.repository.transition(self.owner, run["id"], {"awaiting_clarification"},
                                           fields={"review": old_review})
        self.provider.force_ambiguous = False
        before_intent = len(self.provider.clarify_calls)
        selected = self.client.post(BASE + f"/runs/{run['id']}/clarify", json={
            "idempotency_key": str(uuid4()), "revision": 1, "option_id": old_review["options"][0]["id"]})
        self.assertEqual(selected.status_code, 202, selected.json)
        done = self.wait(run["id"])
        self.assertEqual(len(self.provider.clarify_calls), before_intent)
        self.assertEqual(done["question"], original)
        self.assertTrue(done["resolved_question"].startswith("请分析近期全球半导体股集中下跌"))
        self.assertEqual(done["clarification_history"][-1]["question"], done["resolved_question"])
        current_material = next(item for item in done["materials"] if item["id"] == "U1")
        self.assertTrue(current_material["text_used"].startswith("本轮最新研究请求"))
        self.assertIn(done["resolved_question"], current_material["text_used"])
        self.assertIn("初次提供的原始输入或新闻材料", current_material["text_used"])
        self.assertIn(original, current_material["text_used"])
        self.assertEqual(next(item for item in self.provider.calls[-1]["sources"] if item["id"] == "U1")["text_used"],
                         current_material["text_used"])
        self.assertIn("过去状态不能覆盖", self.service.intent_prompt)
        self.assertEqual(done["report"]["question"], original)
        self.assertEqual(done["report"]["resolved_question"], done["resolved_question"])

    def test_unlabeled_pasted_news_remains_background_after_direction_selection(self):
        session_id = self.session()
        short = "测试甲公司宣布收购测试乙公司，交易金额尚待公告。"
        for article in (short, short * 8):
            with self.subTest(length=len(article)):
                original = "半导体最近发生了什么？我还没有确定要研究哪一件事。\n" + article
                self.provider.force_ambiguous = True
                run, _ = self.submit(session_id, question=original)
                waiting = self.wait(run["id"], {"awaiting_clarification"}, auto_review=False)
                self.provider.force_ambiguous = False
                response = self.client.post(BASE + f"/runs/{run['id']}/clarify", json={
                    "idempotency_key": str(uuid4()), "revision": waiting["review"]["revision"],
                    "question": "请分析测试甲公司拟收购测试乙公司的事件、主体和影响路径。"})
                self.assertEqual(response.status_code, 202, response.json)
                done = self.wait(run["id"])
                user_text = next(item for item in done["materials"] if item["id"] == "U1")["text_used"]
                self.assertTrue(user_text.startswith("本轮最新研究请求"))
                self.assertIn("初次提供的原始输入或新闻材料", user_text)
                self.assertIn(article[:100], user_text)
                self.assertEqual(done["question"], original)
                self.assertEqual(next(item for item in self.provider.calls[-1]["sources"]
                                      if item["id"] == "U1")["text_used"], user_text)

    def test_legacy_history_input_becomes_current_question_before_retrieval(self):
        session_id = self.session()
        run, _ = self.submit(session_id, module="history", question="新能源政策事件")
        waiting = self.wait(run["id"], {"awaiting_clarification"}, auto_review=False)
        self.service.repository.transition(self.owner, run["id"], {"awaiting_clarification"},
                                           status="awaiting_input", stage="awaiting_input",
                                           fields={"phase": "report", "resolved_question": None,
                                                   "review": None, "materials": []})
        old_execution = waiting["execution_id"]
        response = self.client.post(BASE + f"/runs/{run['id']}/input", json={
            "judgment": "新能源政策是否利好光伏产业？"})
        self.assertEqual(response.status_code, 202, response.json)
        done = self.wait(run["id"])
        self.assertNotEqual(done["execution_id"], old_execution)
        self.assertEqual(done["phase"], "report")
        self.assertEqual(done["question"], "新能源政策事件")
        self.assertIn("新能源政策是否利好光伏产业", done["resolved_question"])
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(self.provider.clarify_calls[-1]["question"], done["resolved_question"])

    def test_intent_protocol_failure_is_conservative_and_waiting_survives_restart(self):
        session_id = self.session()
        self.provider.clarify_invalid = True
        run, _ = self.submit(session_id, question="新能源政策影响光伏产业")
        waiting = self.wait(run["id"], {"awaiting_clarification"}, auto_review=False)
        self.assertIsNone(waiting["report"])
        self.assertEqual(self.provider.calls, [])
        self.assertTrue(waiting["review"]["limitations"])
        before_clarify = len(self.provider.clarify_calls)
        recovered = ExploreRepository(self.temp.name)
        self.assertEqual(recovered.get_run(self.owner, run["id"])["status"], "awaiting_clarification")
        self.assertEqual(len(self.provider.clarify_calls), before_clarify)
        old_execution = waiting["execution_id"]
        self.provider.clarify_invalid = False
        self.assertEqual(self.client.post(BASE + f"/runs/{run['id']}/clarify", json={
            "idempotency_key": str(uuid4()), "revision": 1,
            "question": "新能源政策是否影响光伏产业？"}).status_code, 202)
        self.assertIsNone(self.service.repository.transition(self.owner, run["id"], {"queued", "running"},
                                                              status="failed", execution_id=old_execution))
        done = self.wait(run["id"])
        self.assertEqual(done["review"]["revision"], 2)

    def test_owner_isolation_on_review_actions_and_generic_news_topic(self):
        session_id = self.session()
        original = self.news.get_news

        def iran_news(*args, **kwargs):
            page = original(*args, **kwargs)
            if kwargs.get("source") == "eastmoney":
                page["items"] = [{"id": "iran-real-shape", "source": "eastmoney", "source_name": "合成快讯",
                                  "title": "伊朗发生新外交事件", "summary": "伊朗外交消息仍待核实。",
                                  "published_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
                                  "url": "https://example.test/iran"}]
            return page

        self.news.get_news = iran_news
        run, _ = self.submit(session_id, question="伊朗发生了什么？")
        waiting = self.wait(run["id"], {"awaiting_clarification"}, auto_review=False)
        self.assertTrue(any(item["title"] == "伊朗发生新外交事件" for item in waiting["materials"]))
        self.assertEqual(self.provider.calls, [])
        other = self.app.test_client()
        self.assertEqual(other.post("/desktop/api/register", json={"email": "other@example.test", "password": "long-password"}).status_code, 200)
        for action, body in (("clarify", {"idempotency_key": str(uuid4()), "revision": 1, "question": "伊朗外交事件"}),
                             ("confirm", {"idempotency_key": str(uuid4()), "revision": 1, "source_ids": ["U1"]})):
            self.assertEqual(other.post(BASE + f"/runs/{run['id']}/{action}", json=body).status_code, 404)
        generic, _ = self.submit(session_id, question="最近有什么新闻？")
        generic = self.wait(generic["id"], {"awaiting_clarification"}, auto_review=False)
        self.assertTrue(any(item["kind"] == "news" for item in generic["materials"]))
        self.assertEqual(self.provider.calls, [])

    def test_specific_news_prefixed_question_auto_reports_not_clarification(self):
        session_id = self.session()
        question = "最新新闻显示测试甲公司收购测试乙公司，请解释该事件影响。"
        run, _ = self.submit(session_id, question=question)
        done = self.wait(run["id"])
        self.assertFalse(done["review"]["needs_clarification"])
        self.assertEqual(done["review"]["interpreted_question"], question)
        self.assertTrue(done["materials"])
        self.assertIn(done["status"], {"succeeded", "partial"})

    def test_file_pool_orders_by_instant_across_timezone_offsets(self):
        session_id = self.session()
        run, _ = self.submit(session_id)
        done = self.wait(run["id"])
        report_id = done["report"]["file_id"]
        material_id = done["materials"][0]["file_id"]
        with self.service.repository.transaction() as db:
            db.execute("UPDATE explore_files SET created_at=? WHERE session_id=?",
                       ("2026-10-12T00:00:00Z", session_id))
            db.execute("UPDATE explore_files SET created_at=? WHERE id=?",
                       ("2026-10-13T08:00:00+08:00", material_id))
            db.execute("UPDATE explore_files SET created_at=? WHERE id=?",
                       ("2026-10-13T00:01:00Z", report_id))
        ordered = self.client.get(BASE + f"/sessions/{session_id}/files").json["files"]
        self.assertEqual(ordered[0]["id"], report_id)
        self.assertEqual(ordered[1]["id"], material_id)

    def test_restart_between_clarify_and_new_material_save_keeps_prior_file_ids(self):
        session_id = self.session()
        self.provider.force_ambiguous = True
        run, _ = self.submit(session_id, question="新能源最近有什么新闻？")
        waiting = self.wait(run["id"], {"awaiting_clarification"}, auto_review=False)
        original_ids = {item["file_id"] for item in waiting["materials"]}
        self.search.started, self.search.release = threading.Event(), threading.Event()
        try:
            answer = self.client.post(BASE + f"/runs/{run['id']}/clarify", json={
                "idempotency_key": str(uuid4()), "revision": waiting["review"]["revision"],
                "option_id": waiting["review"]["options"][0]["id"]})
            self.assertEqual(answer.status_code, 202, answer.json)
            self.assertTrue(self.search.started.wait(3))
            control = self.service._active[(self.owner, run["id"])]
            during = self.client.get(BASE + f"/runs/{run['id']}").json["run"]
            self.assertNotEqual(during["execution_id"], waiting["execution_id"])
            self.assertEqual({item["file_id"] for item in during["materials"]}, original_ids)
            removed_id = next(iter(original_ids))
            self.assertEqual(self.client.delete(BASE + f"/sessions/{session_id}/files/{removed_id}").status_code, 200)
            visible_ids = original_ids - {removed_id}
            recovered = ExploreRepository(self.temp.name)
            interrupted = recovered.get_run(self.owner, run["id"])
            self.assertEqual(interrupted["status"], "interrupted")
            self.assertEqual({item["file_id"] for item in interrupted["materials"]}, original_ids)
            self.assertEqual({item["id"] for item in recovered.list_files(self.owner, session_id)}, visible_ids)
            self.search.release.set()
            control["thread"].join(timeout=3)
            again = ExploreRepository(self.temp.name)
            self.assertEqual({item["id"] for item in again.list_files(self.owner, session_id)}, visible_ids)
        finally:
            self.search.release.set()


if __name__ == "__main__":
    unittest.main()
