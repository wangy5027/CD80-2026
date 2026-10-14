"""Persistence and revocation checks for branch-local Explore memory."""

import tempfile
import unittest
import json

from backend.app.modules.explore.memory import build_memory_state
from backend.app.modules.explore.repository import ExploreRepository, timestamp, uid
from backend.app.modules.workbench.errors import GatewayError


class ExploreMemoryRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="explore-memory-")
        self.addCleanup(self.temp.cleanup)
        self.repo = ExploreRepository(self.temp.name)
        self.owner = "researcher"
        self.session_id = self.repo.create_session(self.owner, "连续研究")["id"]

    def create_run(self, module, question, *, source_report_id=None, source_run_id=None,
                   retry_of=None, selected=()):
        now = timestamp()
        run = {"id": uid(), "session_id": self.session_id, "module": module,
               "question": question, "source_report_id": source_report_id,
               "source_run_id": source_run_id, "retry_of": retry_of,
               "status": "queued", "stage": "queued", "phase": "initial_explanation" if module == "event" else "prepare",
               "stage_history": [{"stage": "queued", "at": now}],
               "created_at": now, "updated_at": now, "execution_id": uid(),
               "materials": [], "retrieval": {}, "time_policy": {"as_of": now}}
        request = {"idempotency_key": uid(), "module": module, "question": question,
                   "source_report_id": source_report_id, "source_run_id": source_run_id,
                   "retry_of": retry_of}
        saved, created = self.repo.create_run(self.owner, request, run, 8,
            {"mode": "manual", "file_ids": list(selected)})
        self.assertTrue(created)
        return saved

    def complete(self, run, summary, *, sources=(), final_choice=False,
                 next_module=None, next_question=None, sections=(), cases=()):
        report = {"id": uid(), "module": run["module"], "title": run["question"],
                  "summary": summary, "key_findings": [summary],
                  "uncertainties": ["仍需进一步核实。"], "limitations": [],
                  "sources": list(sources), "markdown": "# " + summary,
                  "created_at": timestamp(), "as_of": timestamp(), "recommendations": [],
                  "sections": list(sections), "cases": list(cases)}
        if final_choice:
            report["report_format"] = "event_lite@2" if run["module"] == "event" else "relation_lite@2"
            report["recommendations"] = [{"module": next_module, "question": next_question}]
        self.assertTrue(self.repo.complete(self.owner, run["id"], report, "succeeded",
                                           execution_id=run["execution_id"]))
        return self.repo.get_run(self.owner, run["id"])["report"]

    def initial(self, run, answer="A独有初答：需要核查资金和预期。"):
        saved = self.repo.save_event_initial(self.owner, run["id"], answer,
                                             execution_id=run["execution_id"])
        self.assertIsNotNone(saved)
        return self.repo.get_run(self.owner, run["id"])

    def set_running(self, run, phase):
        with self.repo.transaction() as db:
            current = self.repo._run(db, self.owner, run["id"])
            current.update(status="queued", stage="queued", phase=phase, execution_id=uid(),
                           updated_at=timestamp())
            self.repo._save_run(db, self.owner, current)
            return current

    def test_event_relation_history_inherit_exact_versions_and_preserve_old_answers(self):
        a = self.create_run("event", "A：指数发生了什么？")
        a_initial = self.initial(a)
        first = a_initial["memory_output"]
        self.assertIsNone(a_initial["memory_input"])
        self.assertEqual(first["version"], 1)
        self.assertIn("A独有初答", first["content"])
        self.assertEqual(self.repo.validate_memory(self.owner, a["id"])["id"], first["id"])

        a_deep = self.set_running(a, "event_deep_report")
        report_a = self.complete(a_deep, "A正式报告：波动需要区分已知和推断。")
        a_final = self.repo.get_run(self.owner, a["id"])["memory_output"]
        self.assertEqual((a_final["version"], a_final["parent_id"]), (2, first["id"]))
        self.assertIn("A独有初答", a_final["content"])
        self.assertEqual(report_a["memory_input"]["id"], first["id"])
        self.assertEqual(report_a["memory_output"]["id"], a_final["id"])

        b = self.create_run("relation", "B：它关联哪些主体？", source_report_id=report_a["id"])
        self.assertEqual(b["memory_input"]["id"], a_final["id"])
        self.assertEqual(b["context_selection"]["file_ids"], [])
        self.assertIn("A独有初答", self.repo.validate_memory(self.owner, b["id"])["content"])
        report_b = self.complete(b, "B独有发现：主体之间存在条件关系。",
            sections=[{"heading": "关键机制", "body": "B章节独有机制：传导取决于流动性条件。", "source_refs": []}])
        b_final = self.repo.get_run(self.owner, b["id"])["memory_output"]
        self.assertEqual(b_final["version"], 3)
        self.assertIn("A独有初答", b_final["content"])
        self.assertIn("B独有发现", b_final["content"])
        self.assertIn("B章节独有机制", b_final["content"])

        c = self.create_run("history", "C：历史上类似情形如何？", source_report_id=report_b["id"])
        self.assertEqual(c["memory_input"]["id"], b_final["id"])
        self.assertIn("A独有初答", self.repo.validate_memory(self.owner, c["id"])["content"])
        self.assertIn("B章节独有机制", self.repo.validate_memory(self.owner, c["id"])["content"])
        self.complete(c, "C独有结论：历史表现随条件而异。",
            cases=[{"event": "案例一", "similarities": "相似条件：资金收紧",
                    "differences": "差异条件：政策不同", "supporting_conditions": "支持条件：低流动性",
                    "failure_conditions": "失效条件：政策转向", "source_refs": ["S1"]}])
        lineage = self.repo.list_memories(self.owner, self.session_id, c["id"])
        self.assertEqual([item["version"] for item in lineage], [1, 2, 3, 4])
        self.assertEqual(lineage[0]["content"], first["content"])
        self.assertEqual(self.repo.get_memory(self.owner, self.session_id, first["id"])["content"], first["content"])
        self.assertEqual(len(self.repo.list_memories(self.owner, self.session_id)), 4)
        self.assertLessEqual(len(lineage[-1]["content"]), 10_000)
        self.assertIn("支持条件：低流动性", lineage[-1]["content"])

    def test_direct_first_explanation_continuation_and_retry_parent(self):
        a = self.initial(self.create_run("event", "第一步问题"), "第一步独有文字")
        b = self.create_run("relation", "接着研究", source_run_id=a["id"])
        self.assertEqual(b["memory_input"]["id"], a["memory_output"]["id"])
        self.complete(b, "B的发现")
        a_deep = self.set_running(a, "event_deep_report")
        self.complete(a_deep, "A后来形成的正式报告")
        retry = self.create_run("relation", "接着研究", source_run_id=a["id"], retry_of=b["id"])
        self.assertEqual(retry["memory_input"]["id"], b["memory_input"]["id"])
        self.assertNotEqual(retry["memory_input"]["id"],
                            self.repo.get_run(self.owner, b["id"])["memory_output"]["id"])

    def test_deleted_or_changed_dependency_blocks_memory_without_leaking_other_owner(self):
        a = self.initial(self.create_run("event", "第一步问题"))
        b = self.create_run("relation", "第二步问题", source_run_id=a["id"])
        with self.assertRaises(GatewayError) as denied:
            self.repo.get_memory("another-owner", self.session_id, a["memory_output"]["id"])
        self.assertEqual(denied.exception.status, 404)
        answer_file = a["initial_file_ids"]["answer"]
        self.repo.delete_file(self.owner, self.session_id, answer_file)
        with self.assertRaises(GatewayError) as removed:
            self.repo.validate_memory(self.owner, b["id"], b["execution_id"])
        self.assertEqual(removed.exception.code, "explore_memory_dependency_missing")

        with self.repo.transaction() as db:
            blocked = self.repo._run(db, self.owner, b["id"])
            blocked["status"] = "cancelled"
            self.repo._save_run(db, self.owner, blocked)
        other = self.initial(self.create_run("event", "另一条独立研究"))
        file_id = other["initial_file_ids"]["answer"]
        with self.repo.transaction() as db:
            db.execute("UPDATE explore_files SET content=? WHERE id=?", ("被更改的正文", file_id))
        with self.assertRaises(GatewayError) as changed:
            self.repo.validate_memory(self.owner, other["id"])
        self.assertEqual(changed.exception.code, "explore_memory_dependency_changed")

    def test_session_deletion_cascades_memory_history(self):
        self.initial(self.create_run("event", "A"))
        self.assertEqual(len(self.repo.list_memories(self.owner, self.session_id)), 1)
        self.repo.delete_session(self.owner, self.session_id)
        with self.repo.transaction() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM research_memories").fetchone()[0], 0)

    def test_final_choice_report_allows_only_its_own_retry_lineage(self):
        a = self.initial(self.create_run("event", "A"))
        a_deep = self.set_running(a, "event_deep_report")
        report_a = self.complete(a_deep, "A报告", final_choice=True,
                                 next_module="relation", next_question="B推荐问题")
        b = self.create_run("relation", "B推荐问题", source_report_id=report_a["id"])
        self.complete(b, "B报告")
        retry = self.create_run("relation", "B推荐问题", source_report_id=report_a["id"],
                                retry_of=b["id"])
        self.assertEqual(retry["memory_input"]["id"], b["memory_input"]["id"])
        self.assertEqual(self.repo.get_run(self.owner, a["id"])["continuation_run_id"], b["id"])
        with self.assertRaises(GatewayError) as invalid:
            self.create_run("relation", "伪造的问题", source_report_id=report_a["id"],
                            retry_of=b["id"])
        self.assertEqual(invalid.exception.code, "explore_invalid_retry")

    def test_legacy_source_is_backfilled_only_when_used(self):
        a = self.initial(self.create_run("event", "旧事件"), "旧事件第一步发现")
        a_deep = self.set_running(a, "event_deep_report")
        report_a = self.complete(a_deep, "旧事件正式报告")
        with self.repo.transaction() as db:
            old = self.repo._run(db, self.owner, a["id"])
            old.pop("memory_input", None)
            old.pop("memory_output", None)
            self.repo._save_run(db, self.owner, old)
            db.execute("DELETE FROM research_memories WHERE run_id=?", (a["id"],))
            saved_report = db.execute("SELECT doc FROM reports WHERE id=?", (report_a["id"],)).fetchone()
            report_doc = json.loads(saved_report["doc"])
            report_doc.pop("memory_input", None)
            report_doc.pop("memory_output", None)
            db.execute("UPDATE reports SET doc=? WHERE id=?", (json.dumps(report_doc), report_a["id"]))
        b = self.create_run("relation", "旧事件之后的关联", source_report_id=report_a["id"])
        lineage = self.repo.list_memories(self.owner, self.session_id, b["id"])
        self.assertEqual([item["version"] for item in lineage], [1, 2])
        self.assertIn("旧事件第一步发现", b["memory_input"]["content"])

    def test_direct_run_link_cannot_bypass_formal_report_recommendations(self):
        a = self.initial(self.create_run("event", "A"))
        a_deep = self.set_running(a, "event_deep_report")
        report_a = self.complete(a_deep, "A报告", final_choice=True,
                                 next_module="relation", next_question="指定问题")
        with self.assertRaises(GatewayError) as bypass:
            self.create_run("relation", "自选问题", source_run_id=a["id"])
        self.assertEqual(bypass.exception.code, "explore_memory_source_report_required")
        with self.assertRaises(GatewayError) as wrong:
            self.create_run("relation", "自选问题", source_report_id=report_a["id"],
                            source_run_id=a["id"])
        self.assertEqual(wrong.exception.code, "explore_invalid_recommendation")
        valid = self.create_run("relation", "指定问题", source_report_id=report_a["id"],
                                source_run_id=a["id"])
        self.assertEqual(valid["memory_input"]["id"], self.repo.get_run(self.owner, a["id"])["memory_output"]["id"])

    def test_direct_run_link_requires_saved_event_first_explanation(self):
        pending = self.create_run("event", "尚未回答")
        with self.assertRaises(GatewayError) as unfinished:
            self.create_run("relation", "继续", source_run_id=pending["id"])
        self.assertEqual(unfinished.exception.code, "explore_memory_source_pending")

    def test_frozen_state_and_history_content_are_checked(self):
        a = self.initial(self.create_run("event", "A"))
        b = self.create_run("relation", "B", source_run_id=a["id"])
        with self.repo.transaction() as db:
            changed = self.repo._run(db, self.owner, b["id"])
            changed["memory_input"]["state"]["goal"] = "被改写的目标"
            self.repo._save_run(db, self.owner, changed)
        with self.assertRaises(GatewayError) as corrupted:
            self.repo.validate_memory(self.owner, b["id"])
        self.assertEqual(corrupted.exception.code, "explore_memory_changed")
        with self.repo.transaction() as db:
            db.execute("UPDATE research_memories SET content=? WHERE id=?",
                       ("被改写的历史正文", a["memory_output"]["id"]))
        with self.assertRaises(GatewayError) as history:
            self.repo.get_memory(self.owner, self.session_id, a["memory_output"]["id"])
        self.assertEqual(history.exception.code, "explore_memory_changed")

    def test_long_chain_marks_compressed_stages_and_retains_recent_digest(self):
        parent = None
        for index in range(12):
            run = {"id": str(index), "module": "relation", "question": f"问题{index}",
                   "updated_at": timestamp(), "time_policy": {"as_of": timestamp()}}
            built = build_memory_state(parent, run, initial_answer=f"第{index}阶段独有结论")
            parent = built
        state = parent["state"]
        self.assertEqual(state["compacted_count"], 7)
        self.assertEqual(len(state["stages"]), 5)
        self.assertIn("问题6：第6阶段独有结论", parent["content"])
        self.assertIn("已有 7 个更早阶段压缩归档", parent["content"])
        self.assertIn("完整内容见历史记忆版本", parent["content"])
        self.assertLessEqual(len(parent["content"]), 10_000)


if __name__ == "__main__":
    unittest.main()
