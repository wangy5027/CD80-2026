"""Evidence continuation is scoped to one active Explore run and its frozen files."""

import hashlib
import tempfile
import threading
import unittest

from backend.app.modules.explore.evidence import EvidenceSession
from backend.app.modules.explore.repository import ExploreRepository, encoded, file_id_for, timestamp, uid
from backend.app.modules.workbench.errors import GatewayError
from backend.app.modules.workbench.tool_registry import ToolContext, ToolRegistry


class ExploreEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = ExploreRepository(self.temp.name)
        self.owner = "owner-a"
        self.session_id = self.repo.create_session(self.owner, "研究")["id"]

    def insert_file(self, name, content, *, kind="material", origin="web_search"):
        file_id = file_id_for("evidence-test:" + uid())
        with self.repo.transaction() as db:
            self.repo._insert_file(
                db, file_id=file_id, owner=self.owner, session_id=self.session_id,
                origin_key="evidence-test:" + file_id, kind=kind, origin=origin,
                name=name, content=content,
                metadata={"context_text": content[:1000], "content_kind": "search_snippet",
                          "url": "https://example.org/item", "published_at": "2026-10-01"},
                created_at=timestamp())
        return file_id

    def create_run(self, selected=(), *, owner=None, session_id=None):
        owner = owner or self.owner
        session_id = session_id or self.session_id
        now = timestamp()
        run = {"id": uid(), "session_id": session_id, "module": "event",
               "question": "核查公开资料", "status": "queued", "stage": "queued",
               "stage_history": [{"stage": "queued", "at": now}],
               "created_at": now, "updated_at": now,
               "execution_id": uid(), "materials": [], "retrieval": {},
               "source_report_id": None, "time_policy": {"as_of": now}}
        override = {"mode": "manual", "file_ids": list(selected)}
        self.repo.create_run(owner, {"idempotency_key": uid()}, run, 8, override)
        return run, EvidenceSession(self.repo, owner, run)

    def assert_rejected(self, action, status):
        with self.assertRaises(GatewayError) as caught:
            action()
        self.assertEqual(caught.exception.status, status)

    def test_selected_file_read_has_stable_hash_range_and_no_unselected_access(self):
        selected = self.insert_file("已选择.md", "ABCDEFGHIJKL")
        unselected = self.insert_file("未选择.md", "hidden")
        run, evidence = self.create_run([selected])
        catalog = evidence.selected_catalog()
        self.assertEqual(len(catalog), 1)
        self.assertEqual(catalog[0]["file_id"], selected)
        opaque = catalog[0]["evidence_id"]
        self.assertNotEqual(opaque, selected)
        first = evidence.read({"evidence_id": opaque, "offset": 0, "limit": 5}).content
        self.assertEqual(first["text"], "ABCDE")
        self.assertTrue(first["has_more"])
        self.assertEqual(first["next_offset"], 5)
        self.repo.rename_file(self.owner, self.session_id, selected, "改名后.md")
        second = evidence.read({"evidence_id": opaque, "offset": 5, "limit": 20}).content
        self.assertEqual(second["text"], "FGHIJKL")
        self.assertEqual(second["title"], "已选择.md")
        self.assertEqual(evidence.selected_catalog()[0]["title"], "已选择.md")
        self.assertFalse(second["has_more"])
        self.assertIsNone(second["next_offset"])
        self.assertEqual(first["content_hash"], second["content_hash"])
        self.assertEqual(first["content_hash"], hashlib.sha256(b"ABCDEFGHIJKL").hexdigest())
        self.assertEqual(len(evidence.sources()), 1)
        self.assertEqual(evidence.sources()[0]["file_id"], selected)
        self.assertEqual(first["requested_evidence_id"], opaque)
        self.assertEqual(first["source"]["evidence_id"], first["evidence_id"])
        self.assertNotEqual(first["evidence_id"], opaque)
        self.assert_rejected(lambda: evidence.read({"evidence_id": unselected}), 404)
        self.assert_rejected(lambda: evidence.read({"evidence_id": "../../secret"}), 404)
        self.assert_rejected(lambda: evidence.read({"evidence_id": opaque, "limit": 6001}), 400)

    def test_tool_source_is_archived_with_full_text_and_duplicate_hash_cluster(self):
        run, evidence = self.create_run()
        body = "公开文件原文。" * 1500
        first = evidence.add_source({"kind": "public_page", "title": "公告原文",
                                     "url": "https://example.org/a", "evidence_text": body,
                                     "text_used": body[:800], "content_kind": "html_fulltext",
                                     "http_status": 200, "final_url": "https://example.org/a",
                                     "publisher": "公开机构", "target_found": True,
                                     "target_paragraph_index": 1,
                                     "headings": [{"index": 1, "text": "第二节", "anchor": "part-2"}],
                                     "paragraphs": [{"index": 1, "start": 0, "end": 7000,
                                                     "heading": "第一节", "anchor": "part-1"},
                                                    {"index": 2, "start": 7000,
                                                     "end": len(body), "heading": "第二节"}]})
        second = evidence.add_source({"kind": "public_page", "title": "转载",
                                      "url": "https://example.org/b", "evidence_text": body,
                                      "content_kind": "html_fulltext",
                                      "file_id": "unselected-foreign-file"})
        self.assertEqual((first["id"], second["id"]), ("S1", "S2"))
        self.assertNotEqual(first["evidence_id"], second["evidence_id"])
        self.assertEqual(first["content_hash"], second["content_hash"])
        self.assertEqual(first["origin_cluster"], second["origin_cluster"])
        self.assertNotEqual(second["file_id"], "unselected-foreign-file")
        chunk = evidence.read({"evidence_id": first["evidence_id"], "limit": 6000}).content
        first_text = chunk["text"]
        self.assertTrue(chunk["has_more"])
        self.assertEqual(chunk["next_offset"], len(chunk["text"]))
        self.assertLessEqual(len(encoded(chunk)), 7900)
        self.assertEqual(chunk["source"]["evidence_id"], first["evidence_id"])
        self.assertEqual(chunk["http_status"], 200)
        self.assertEqual(chunk["publisher"], "公开机构")
        self.assertEqual(chunk["target_paragraph_index"], 1)
        self.assertEqual(chunk["paragraph_count"], 2)
        self.assertEqual(chunk["locations"][0]["anchor"], "part-1")
        rest = []
        while chunk["has_more"]:
            chunk = evidence.read({"evidence_id": first["evidence_id"],
                                   "offset": chunk["next_offset"], "limit": 6000}).content
            rest.append(chunk["text"])
        self.assertEqual(first_text + "".join(rest), body)
        archived = self.repo.get_file(self.owner, self.session_id, first["file_id"])
        self.assertIn(body, archived["content"])
        self.assertEqual(archived["origin"], "public_page")
        self.assertEqual(archived["kind"], "material")
        self.assertEqual(len(evidence.sources()), 2)
        self.assertEqual(evidence.sources()[0]["text_used"], body[:800])
        self.assertNotIn("evidence_text", evidence.sources()[0])
        self.repo.delete_file(self.owner, self.session_id, first["file_id"])
        self.assert_rejected(lambda: evidence.read({"evidence_id": first["evidence_id"]}), 404)
        self.assertEqual([item["id"] for item in evidence.sources()], ["S2"])

    def test_initial_retrieval_sources_get_evidence_ids_in_same_transaction(self):
        run, evidence = self.create_run()
        source = {"id": "S1", "kind": "web_search", "title": "公开摘要",
                  "source": "web_search", "source_name": "搜索",
                  "text_used": "可核查摘录", "content_kind": "search_snippet",
                  "url": "https://example.org/item"}
        saved = self.repo.save_materials(self.owner, run["id"], [source], {},
                                         execution_id=run["execution_id"])
        self.assertEqual(saved["materials"][0]["evidence_id"], source["evidence_id"])
        self.assertEqual(evidence.sources()[0]["id"], "S1")
        actual = evidence.read({"evidence_id": source["evidence_id"]}).content
        self.assertEqual(actual["text"], "可核查摘录")
        self.assertEqual(actual["source"]["content_kind"], "search_snippet")
        self.assertEqual(self.repo.get_file(self.owner, self.session_id,
                                            source["file_id"])["origin"], "web_search")

    def test_selected_prior_material_keeps_original_source_cluster(self):
        first_run, first_evidence = self.create_run()
        original = first_evidence.add_source({"kind": "public_page", "title": "公开原文",
                                              "text": "同一来源的可核查段落", "content_kind": "html_fulltext"})
        self.repo.transition(self.owner, first_run["id"], {"queued"}, status="succeeded")
        _, followup = self.create_run([original["file_id"]])
        selected = followup.selected_catalog()[0]
        result = followup.read({"evidence_id": selected["evidence_id"]}).content
        self.assertEqual(result["text"], "同一来源的可核查段落")
        self.assertEqual(result["content_hash"], original["content_hash"])
        self.assertEqual(result["origin_cluster"], original["origin_cluster"])
        self.assertEqual(result["source"]["origin_cluster"], original["origin_cluster"])

    def test_read_evidence_runs_through_existing_tool_registry(self):
        _, evidence = self.create_run()
        source = evidence.add_source({"kind": "public_page", "title": "正文",
                                      "text": "可核实的原文", "content_kind": "html_fulltext"})
        registry = ToolRegistry([evidence.tool_spec()])
        context = ToolContext(threading.Event(), lambda _response: None, lambda _event: None)
        result = registry.execute("read_evidence", {"evidence_id": source["evidence_id"],
                                                    "offset": 2, "limit": 4}, context)
        self.assertEqual(result.content["text"], "实的原文")
        self.assertEqual(result.content["source_id"], "S1")
        self.assert_rejected(lambda: registry.execute("read_evidence", {
            "evidence_id": source["evidence_id"]}, context, call_count=24), 429)

    def test_only_confirmed_material_survives_new_execution(self):
        run, first_execution = self.create_run()
        source_a = {"id": "S1", "kind": "web_search", "title": "已确认",
                    "text_used": "第一条材料", "content_kind": "search_snippet"}
        source_b = {"id": "S2", "kind": "web_search", "title": "未确认",
                    "text_used": "第二条材料", "content_kind": "search_snippet"}
        self.repo.save_materials(self.owner, run["id"], [source_a, source_b], {},
                                 execution_id=run["execution_id"])
        self.assertEqual(first_execution.selected_catalog(), [])
        self.repo.transition(self.owner, run["id"], {"running"}, status="awaiting_review",
                             fields={"phase": "prepare", "review": {"revision": 1}})

        def confirm(saved):
            saved.update(status="queued", stage="queued", phase="report", execution_id=uid(),
                         confirmed_sources=[dict(source_a)])

        confirmed, created = self.repo.apply_review_action(
            self.owner, run["id"], "confirm", uid(), {"source_ids": ["S1"]}, confirm, 8)
        self.assertTrue(created)
        new_execution = EvidenceSession(self.repo, self.owner, confirmed)
        catalog = new_execution.selected_catalog()
        self.assertEqual([item["file_id"] for item in catalog], [source_a["file_id"]])
        self.assert_rejected(lambda: first_execution.read({"evidence_id": source_a["evidence_id"]}), 409)
        self.assert_rejected(lambda: new_execution.read({"evidence_id": source_a["evidence_id"]}), 404)
        self.assert_rejected(lambda: new_execution.read({"evidence_id": source_b["evidence_id"]}), 404)
        read = new_execution.read({"evidence_id": catalog[0]["evidence_id"]}).content
        self.assertEqual(read["text"], "第一条材料")
        self.assertEqual(read["source_id"], "S3")
        self.assertEqual([item["id"] for item in new_execution.sources()], ["S3"])
        self.repo.delete_file(self.owner, self.session_id, source_a["file_id"])
        self.assertEqual(new_execution.selected_catalog(), [])
        self.assert_rejected(lambda: new_execution.read({"evidence_id": catalog[0]["evidence_id"]}), 404)

    def test_changing_phase_does_not_revive_unconfirmed_material(self):
        run, evidence = self.create_run()
        source = {"id": "S1", "kind": "web_search", "title": "待确认",
                  "text_used": "不可自动放行", "content_kind": "search_snippet"}
        self.repo.save_materials(self.owner, run["id"], [source], {},
                                 execution_id=run["execution_id"])
        self.repo.transition(self.owner, run["id"], {"running"},
                             fields={"phase": "report", "execution_id": uid(),
                                     "confirmed_sources": []})
        current = EvidenceSession(self.repo, self.owner, self.repo.get_run(self.owner, run["id"]))
        self.assertEqual(current.selected_catalog(), [])
        self.assertEqual(current.sources(), [])
        self.assert_rejected(lambda: current.read({"evidence_id": source["evidence_id"]}), 404)

    def test_owner_run_execution_cancel_and_delete_are_checked_each_read(self):
        selected = self.insert_file("来源.md", "source body")
        run, evidence = self.create_run([selected])
        opaque = evidence.selected_catalog()[0]["evidence_id"]
        other_owner = EvidenceSession(self.repo, "owner-b", run)
        self.assert_rejected(lambda: other_owner.read({"evidence_id": opaque}), 404)
        other_session = self.repo.create_session(self.owner, "另一个分支")["id"]
        _, other_run = self.create_run(session_id=other_session)
        self.assert_rejected(lambda: other_run.read({"evidence_id": opaque}), 404)
        wrong_execution = EvidenceSession(self.repo, self.owner, run, execution_id=uid())
        self.assert_rejected(lambda: wrong_execution.read({"evidence_id": opaque}), 409)
        self.repo.delete_file(self.owner, self.session_id, selected)
        self.assertEqual(evidence.selected_catalog(), [])
        self.assert_rejected(lambda: evidence.read({"evidence_id": opaque}), 404)
        self.assertEqual(self.repo.get_run(self.owner, run["id"])["context_files"][0]["file_id"], selected)
        self.repo.transition(self.owner, run["id"], {"queued"}, status="cancelled")
        self.assert_rejected(lambda: evidence.read({"evidence_id": opaque}), 409)

    def test_report_selection_is_background_and_never_promoted_as_fact(self):
        report_id = self.insert_file("旧报告.md", "AI 推论，未经核实", kind="report", origin="report")
        _, evidence = self.create_run([report_id])
        item = evidence.selected_catalog()[0]
        self.assertEqual(item["role"], "background")
        result = evidence.read({"evidence_id": item["evidence_id"]}).content
        self.assertEqual(result["role"], "background")
        self.assertNotIn("source_id", result)
        self.assertEqual(evidence.sources(), [])


if __name__ == "__main__":
    unittest.main()
