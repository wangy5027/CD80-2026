"""Transactional, account-scoped Explore sessions, runs, reports and stages."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

from ..workbench.errors import GatewayError
from .contracts import MODULES, has_final_choice, markdown_text, safe_url
from .memory import build_memory_state


ACTIVE = {"queued", "running"}
TERMINAL = {"succeeded", "partial", "failed", "cancelled", "interrupted"}
MAX_CONTEXT_FILES = 8
CONTEXT_EXCERPT_CHARS = 1000
MAX_EVIDENCE_CHARS = 400_000
MAX_RUN_EVIDENCE_CHARS = 1_200_000
MAX_EVIDENCE_ENTRIES = 64
MAX_EVIDENCE_READ_CHARS = 6000
EVENT_PHASE_LABELS = {"initial_explanation": "第一步", "event_deep_report": "第二步"}


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def uid():
    return str(uuid4())


def file_id_for(origin_key):
    return str(uuid5(NAMESPACE_URL, "ficagent-explore:" + origin_key))


def material_document(source, run):
    kind = {"web_search": "网页搜索摘要（非全文）", "news": "快讯摘录（非全文）",
            "user": "用户输入（未经独立核实）", "public_page": "公开网页原文",
            "public_pdf": "公开 PDF 原文"}.get(source.get("kind"), "工具取得的研究材料")
    lines = ["# " + markdown_text(source.get("title")), "", "材料类型：" + kind,
             "来源：" + markdown_text(source.get("source_name") or source.get("source")),
             "发布时间：" + markdown_text(source.get("published_at") or "未知"),
             "获取时间：" + markdown_text(source.get("retrieved_at") or "未知")]
    if source.get("phase") in EVENT_PHASE_LABELS:
        lines.append("所属阶段：理解事件" + EVENT_PHASE_LABELS[source["phase"]])
        lines.append("来源编号：" + markdown_text(source.get("id")))
    if source.get("url"):
        lines.append("原始链接：" + markdown_text(source["url"]))
    lines += ["", "## 本次实际可用文本", "",
              markdown_text(source.get("evidence_text") or source.get("text_used") or "")]
    if source.get("kind") == "user" and run.get("question") != source.get("text_used"):
        lines += ["", "## 完整初始用户输入（未经独立核实）", "", markdown_text(run.get("question"))]
    return "\n".join(lines) + "\n"


class ExploreRepository:
    def __init__(self, data_dir):
        root = Path(data_dir)
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = root / "explore.sqlite3"
        self.lock = threading.RLock()
        with self.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS explore_sessions_owner ON sessions(owner,updated_at);
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, session_id TEXT NOT NULL,
                    status TEXT NOT NULL, doc TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE);
                CREATE INDEX IF NOT EXISTS explore_runs_session ON runs(owner,session_id,created_at);
                CREATE TABLE IF NOT EXISTS reports (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, session_id TEXT NOT NULL,
                    run_id TEXT NOT NULL UNIQUE, doc TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE,
                    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE);
                CREATE TABLE IF NOT EXISTS requests (
                    owner TEXT NOT NULL, key TEXT NOT NULL, digest TEXT NOT NULL,
                    run_id TEXT NOT NULL, PRIMARY KEY(owner,key),
                    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE);
                CREATE TABLE IF NOT EXISTS review_actions (
                    owner TEXT NOT NULL, key TEXT NOT NULL, digest TEXT NOT NULL,
                    run_id TEXT NOT NULL, PRIMARY KEY(owner,key),
                    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE);
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL,
                    run_id TEXT NOT NULL, stage TEXT NOT NULL, at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE);
                CREATE INDEX IF NOT EXISTS explore_events_run ON events(owner,run_id,seq);
                CREATE TABLE IF NOT EXISTS explore_files (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, session_id TEXT NOT NULL,
                    origin_key TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, origin TEXT NOT NULL,
                    name TEXT NOT NULL, content TEXT NOT NULL, metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE);
                CREATE INDEX IF NOT EXISTS explore_files_session ON explore_files(owner,session_id,deleted_at,created_at);
                CREATE TABLE IF NOT EXISTS context_preferences (
                    owner TEXT NOT NULL, session_id TEXT NOT NULL, module TEXT NOT NULL,
                    mode TEXT NOT NULL, file_ids TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(owner,session_id,module),
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE);
                CREATE TABLE IF NOT EXISTS explore_evidence (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, session_id TEXT NOT NULL,
                    run_id TEXT NOT NULL, execution_id TEXT, source_id TEXT,
                    file_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT,
                    content_hash TEXT NOT NULL, origin_cluster TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE,
                    FOREIGN KEY(file_id) REFERENCES explore_files(id) ON DELETE CASCADE);
                CREATE INDEX IF NOT EXISTS explore_evidence_run
                    ON explore_evidence(owner,run_id,execution_id,role);
                CREATE UNIQUE INDEX IF NOT EXISTS explore_evidence_source
                    ON explore_evidence(run_id,execution_id,source_id)
                    WHERE source_id IS NOT NULL;
                CREATE UNIQUE INDEX IF NOT EXISTS explore_evidence_selected
                    ON explore_evidence(run_id,file_id) WHERE role='selected';
                CREATE TABLE IF NOT EXISTS research_memories (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, session_id TEXT NOT NULL,
                    chain_id TEXT NOT NULL, run_id TEXT NOT NULL, parent_id TEXT,
                    version INTEGER NOT NULL, phase TEXT NOT NULL, execution_id TEXT NOT NULL,
                    content TEXT NOT NULL, content_hash TEXT NOT NULL,
                    dependency_file_ids TEXT NOT NULL, dependency_file_hashes TEXT NOT NULL,
                    state TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(run_id,phase,execution_id),
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE,
                    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE);
                CREATE INDEX IF NOT EXISTS research_memories_session
                    ON research_memories(owner,session_id,created_at,id);
                CREATE INDEX IF NOT EXISTS research_memories_chain
                    ON research_memories(owner,chain_id,version);
            """)
            if not any(row["name"] == "metadata" for row in db.execute("PRAGMA table_info(explore_evidence)")):
                db.execute("ALTER TABLE explore_evidence ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}' ")
            if not any(row["name"] == "closed_at" for row in db.execute("PRAGMA table_info(sessions)")):
                db.execute("ALTER TABLE sessions ADD COLUMN closed_at TEXT")
            self._migrate_files(db)
        self.recover()

    @contextmanager
    def transaction(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=15)
            db.row_factory = sqlite3.Row
            try:
                db.execute("PRAGMA foreign_keys=ON")
                db.execute("BEGIN IMMEDIATE")
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    @staticmethod
    def _session(db, owner, session_id, *, require_open=False):
        row = db.execute("SELECT * FROM sessions WHERE id=? AND owner=?", (session_id, owner)).fetchone()
        if row is None:
            raise GatewayError("探索不存在或无权访问。", 404)
        if require_open and row["closed_at"]:
            raise GatewayError("本次探索会话已结束，请新建会话继续研究。", 409, "explore_session_closed")
        return {key: row[key] for key in ("id", "title", "created_at", "updated_at", "closed_at")}

    @staticmethod
    def _run(db, owner, run_id):
        row = db.execute("SELECT doc FROM runs WHERE id=? AND owner=?", (run_id, owner)).fetchone()
        if row is None:
            raise GatewayError("分析记录不存在或无权访问。", 404)
        run = json.loads(row["doc"])
        report = db.execute("SELECT doc FROM reports WHERE run_id=? AND owner=?", (run_id, owner)).fetchone()
        run["report"] = json.loads(report["doc"]) if report else None
        return run

    @staticmethod
    def _event(db, owner, run, stage):
        db.execute("INSERT INTO events(owner,run_id,stage,at) VALUES(?,?,?,?)",
                   (owner, run["id"], stage, run["updated_at"]))

    @staticmethod
    def _save_run(db, owner, run):
        # `report` is a joined view, never part of the run document.
        stored = {key: value for key, value in run.items() if key != "report"}
        db.execute("UPDATE runs SET status=?,doc=?,updated_at=? WHERE id=? AND owner=?",
                   (stored["status"], encoded(stored), stored["updated_at"], stored["id"], owner))

    @staticmethod
    def _file_meta(row):
        meta = json.loads(row["metadata"])
        meta.pop("context_text", None)
        return {"id": row["id"], "session_id": row["session_id"], "name": row["name"],
                "kind": row["kind"], "origin": row["origin"], "created_at": row["created_at"],
                "updated_at": row["updated_at"], "size_chars": len(row["content"]), **meta}

    @staticmethod
    def _file(db, owner, session_id, file_id):
        row = db.execute("SELECT * FROM explore_files WHERE id=? AND owner=? AND session_id=? AND deleted_at IS NULL",
                         (file_id, owner, session_id)).fetchone()
        if row is None:
            raise GatewayError("研究文件不存在或无权访问。", 404)
        return row

    @staticmethod
    def _memory_snapshot(row):
        if hashlib.sha256(row["content"].encode("utf-8")).hexdigest() != row["content_hash"]:
            raise GatewayError("研究记忆内容已变化，请重新开始本轮。", 409, "explore_memory_changed")
        return {"id": row["id"], "parent_id": row["parent_id"], "chain_id": row["chain_id"],
                "version": row["version"], "content": row["content"],
                "content_hash": row["content_hash"], "created_at": row["created_at"],
                "run_id": row["run_id"], "phase": row["phase"],
                "execution_id": row["execution_id"],
                "dependency_file_ids": json.loads(row["dependency_file_ids"]),
                "dependency_file_hashes": json.loads(row["dependency_file_hashes"]),
                "state": json.loads(row["state"])}

    @classmethod
    def _validate_memory_db(cls, db, owner, session_id, snapshot):
        """Resolve a frozen snapshot and check its exact dependent pool files."""
        if snapshot is None:
            return None
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("id"), str):
            raise GatewayError("研究记忆不可用，请重新选择研究起点。", 409, "explore_memory_unavailable")
        row = db.execute("SELECT * FROM research_memories WHERE id=? AND owner=? AND session_id=?",
                         (snapshot["id"], owner, session_id)).fetchone()
        if row is None:
            raise GatewayError("研究记忆不可用，请重新选择研究起点。", 409, "explore_memory_unavailable")
        saved = cls._memory_snapshot(row)
        digest = hashlib.sha256(saved["content"].encode("utf-8")).hexdigest()
        if digest != saved["content_hash"] or any(
            snapshot.get(key) != saved[key]
            for key in ("id", "parent_id", "chain_id", "version", "content", "content_hash",
                        "run_id", "phase", "execution_id", "created_at", "state",
                        "dependency_file_ids", "dependency_file_hashes")
        ):
            raise GatewayError("研究记忆内容已变化，请重新开始本轮。", 409, "explore_memory_changed")
        for file_id in saved["dependency_file_ids"]:
            file_row = db.execute("SELECT content,deleted_at FROM explore_files "
                                  "WHERE id=? AND owner=? AND session_id=?",
                                  (file_id, owner, session_id)).fetchone()
            if file_row is None or file_row["deleted_at"]:
                raise GatewayError("研究记忆依赖的文件已移除，请重新选择研究起点。", 409,
                                   "explore_memory_dependency_missing")
            if hashlib.sha256(file_row["content"].encode("utf-8")).hexdigest() != saved["dependency_file_hashes"].get(file_id):
                raise GatewayError("研究记忆依赖的文件已变化，请重新开始本轮。", 409,
                                   "explore_memory_dependency_changed")
        return saved

    @classmethod
    def _memory_dependencies_db(cls, db, owner, run, parent, *, report=None, initial=False):
        ids = set((parent or {}).get("dependency_file_ids") or [])
        ids.update(item.get("file_id") for item in run.get("context_files") or [] if isinstance(item, dict))
        if initial:
            ids.update((run.get("initial_file_ids") or {}).values())
            sources = run.get("initial_sources") or []
        else:
            ids.update(item for item in (run.get("input_file_id"), (report or {}).get("file_id")) if item)
            sources = (report or {}).get("sources") or (report or {}).get("confirmed_sources") or []
        ids.update(item.get("file_id") for item in sources if isinstance(item, dict))
        hashes = {}
        for file_id in sorted(item for item in ids if isinstance(item, str) and item):
            row = db.execute("SELECT content,deleted_at FROM explore_files "
                             "WHERE id=? AND owner=? AND session_id=?",
                             (file_id, owner, run["session_id"])).fetchone()
            if row is None or row["deleted_at"]:
                raise GatewayError("研究记忆依赖的文件已移除，请重新选择研究起点。", 409,
                                   "explore_memory_dependency_missing")
            hashes[file_id] = hashlib.sha256(row["content"].encode("utf-8")).hexdigest()
        return sorted(hashes), hashes

    @classmethod
    def _publish_memory_db(cls, db, owner, run, *, report=None, initial_answer=None):
        """Write one immutable version and advance this run's head in one transaction."""
        phase = "initial_explanation" if initial_answer is not None else "report"
        execution_id = run.get("execution_id") or "legacy"
        existing = db.execute("SELECT * FROM research_memories WHERE run_id=? AND phase=? AND execution_id=?",
                              (run["id"], phase, execution_id)).fetchone()
        if existing is not None:
            snapshot = cls._memory_snapshot(existing)
            run["memory_output"] = snapshot
            return snapshot
        parent = cls._validate_memory_db(db, owner, run["session_id"],
                                         run.get("memory_output") or run.get("memory_input"))
        built = build_memory_state(parent, run, report=report, initial_answer=initial_answer)
        dependency_ids, dependency_hashes = cls._memory_dependencies_db(
            db, owner, run, parent, report=report, initial=initial_answer is not None)
        now = timestamp()
        snapshot = {"id": uid(), "parent_id": parent["id"] if parent else None,
                    "chain_id": parent["chain_id"] if parent else run["id"],
                    "version": parent["version"] + 1 if parent else 1,
                    "content": built["content"],
                    "content_hash": hashlib.sha256(built["content"].encode("utf-8")).hexdigest(),
                    "created_at": now, "run_id": run["id"], "phase": phase,
                    "execution_id": execution_id,
                    "dependency_file_ids": dependency_ids,
                    "dependency_file_hashes": dependency_hashes, "state": built["state"]}
        db.execute("INSERT INTO research_memories VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (snapshot["id"], owner, run["session_id"], snapshot["chain_id"], run["id"],
                    snapshot["parent_id"], snapshot["version"], phase, execution_id,
                    snapshot["content"], snapshot["content_hash"], encoded(dependency_ids),
                    encoded(dependency_hashes), encoded(snapshot["state"]), now))
        run["memory_output"] = snapshot
        return snapshot

    @classmethod
    def _ensure_source_memory_db(cls, db, owner, source_run, seen=None):
        """Backfill only the requested old lineage, never scan other users' runs."""
        seen = set(seen or ())
        if source_run["id"] in seen or len(seen) >= 24:
            raise GatewayError("研究来源链无法继续，请重新选择起点。", 409, "explore_memory_source_cycle")
        seen.add(source_run["id"])
        if source_run.get("memory_input") is None and source_run.get("memory_output") is None:
            parent_run_id = None
            if source_run.get("source_report_id"):
                row = db.execute("SELECT run_id FROM reports WHERE id=? AND owner=? AND session_id=?",
                                 (source_run["source_report_id"], owner, source_run["session_id"])).fetchone()
                parent_run_id = row["run_id"] if row else None
            elif source_run.get("source_run_id"):
                parent_run_id = source_run["source_run_id"]
            if parent_run_id:
                predecessor = cls._run(db, owner, parent_run_id)
                source_run["memory_input"] = cls._ensure_source_memory_db(db, owner, predecessor, seen)
                cls._save_run(db, owner, source_run)
        output = source_run.get("memory_output")
        if source_run.get("initial_answer") and not output:
            output = cls._publish_memory_db(db, owner, source_run,
                                            initial_answer=source_run["initial_answer"])
            cls._save_run(db, owner, source_run)
        report = source_run.get("report")
        if report and (not output or output.get("phase") != "report"):
            report = dict(report)
            if not report.get("file_id"):
                cls._insert_report_file(db, owner, source_run, report)
            report["memory_input"] = source_run.get("memory_output") or source_run.get("memory_input")
            output = cls._publish_memory_db(db, owner, source_run, report=report)
            report["memory_output"] = output
            db.execute("UPDATE reports SET doc=? WHERE id=? AND owner=?",
                       (encoded(report), report["id"], owner))
            cls._save_run(db, owner, source_run)
        return output

    @staticmethod
    def _file_name(prefix, title, date):
        clean = re.sub(r"[\\/\x00-\x1f<>:\"|?*]", " ", str(title or "未命名"))
        clean = re.sub(r"\s+", " ", clean).strip(" .")[:70] or "未命名"
        return f"{prefix}-{str(date or '未知日期')[:10]}-{clean}.md"

    @staticmethod
    def _insert_file(db, *, file_id, owner, session_id, origin_key, kind, origin, name, content, metadata, created_at):
        db.execute("INSERT OR IGNORE INTO explore_files VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL)",
                   (file_id, owner, session_id, origin_key, kind, origin, name, content,
                    encoded(metadata), created_at, created_at))

    @classmethod
    def _insert_material_file(cls, db, owner, run, source, *, preserved_file_id=None):
        origin_key = (f"material:preserved:{preserved_file_id}" if preserved_file_id else
                      f"material:{run['id']}:{run.get('execution_id') or 'legacy'}:{source['id']}")
        file_id = preserved_file_id or file_id_for(origin_key)
        source["file_id"] = file_id
        raw_kind = source.get("kind")
        origin = (raw_kind if isinstance(raw_kind, str) and
                  re.fullmatch(r"[a-z][a-z0-9_]{0,63}", raw_kind) else "tool_result")
        prefix = {"web_search": "网页摘要", "news": "快讯摘录", "user": "用户输入",
                  "public_page": "网页原文", "public_pdf": "PDF原文"}.get(origin, "研究材料")
        phase = source.get("phase")
        if phase in EVENT_PHASE_LABELS:
            prefix = EVENT_PHASE_LABELS[phase] + prefix
        metadata = {"source_run_id": run["id"], "source_report_id": None, "source_id": source["id"],
                    "url": source.get("url"), "content_kind": source.get("content_kind"),
                    "evidence_content_hash": source.get("content_hash"),
                    "origin_cluster": source.get("origin_cluster"),
                    "published_at": source.get("published_at"), "retrieved_at": source.get("retrieved_at"),
                    "context_text": "\n".join(filter(None, [
                        ("用户输入，未经独立核实" if origin == "user" else
                         "来源材料摘录，非独立核实的全文" if origin in {"web_search", "news"} else
                         "工具取得的研究材料，需核对来源与完整性"),
                        "标题：" + str(source.get("title") or ""),
                        "链接：" + str(source["url"]) if source.get("url") else "",
                        "发布时间：" + str(source["published_at"]) if source.get("published_at") else "",
                        "实际可用文本：" + str(source.get("text_used") or "")]))}
        for key in ("phase", "status", "http_status", "final_url", "canonical_url", "publisher",
                    "target_section", "target_found", "target_paragraph_index",
                    "truncated", "has_more", "locations", "headings"):
            if key in source:
                metadata[key] = source[key]
        cls._insert_file(db, file_id=file_id, owner=owner, session_id=run["session_id"],
                         origin_key=origin_key, kind="material", origin=origin,
                         name=cls._file_name(prefix, source.get("title"), source.get("published_at") or run["created_at"]),
                         content=material_document(source, run), metadata=metadata,
                         created_at=source.get("retrieved_at") or run["created_at"])
        return file_id

    @staticmethod
    def _evidence_text(source):
        for key in ("evidence_text", "body_text", "text", "text_used", "summary"):
            value = source.get(key)
            if isinstance(value, str) and value:
                return value
        return ""

    @classmethod
    def _store_source_evidence_db(cls, db, owner, run, source, *, content=None, role="source",
                                  metadata=None, cluster_override=None):
        """Archive one run-owned source and its exact readable text atomically."""
        source_id = source.get("id")
        if not isinstance(source_id, str) or not re.fullmatch(r"(?:S[1-9][0-9]*|U[1-9][0-9]*)", source_id):
            raise GatewayError("研究来源编号无效。", 400)
        existing = db.execute(
            "SELECT id,file_id,content_hash,origin_cluster FROM explore_evidence "
            "WHERE owner=? AND run_id=? AND execution_id=? AND source_id=?",
            (owner, run["id"], run.get("execution_id"), source_id)).fetchone()
        if existing:
            supplied_full_text = content if content is not None else source.get("evidence_text")
            if (isinstance(supplied_full_text, str) and
                    hashlib.sha256(supplied_full_text.encode("utf-8")).hexdigest() != existing["content_hash"]):
                raise GatewayError("同一来源编号的证据正文已发生变化。", 409)
            source.update(evidence_id=existing["id"], file_id=existing["file_id"],
                          content_hash=existing["content_hash"], origin_cluster=existing["origin_cluster"])
            return source
        content = cls._evidence_text(source) if content is None else content
        if not isinstance(content, str) or not content or len(content) > MAX_EVIDENCE_CHARS:
            raise GatewayError("研究资料正文为空或超过单份上限。", 400)
        metadata_text = encoded(metadata or {})
        if len(metadata_text) > 100_000:
            raise GatewayError("研究资料定位元数据超过单份上限。", 400)
        usage = db.execute(
            "SELECT count(*),coalesce(sum(length(content)+length(metadata)),0) FROM explore_evidence "
            "WHERE owner=? AND run_id=? AND role IN ('source','selected_read')",
            (owner, run["id"])).fetchone()
        if usage[0] >= MAX_EVIDENCE_ENTRIES or usage[1] + len(content) + len(metadata_text) > MAX_RUN_EVIDENCE_CHARS:
            raise GatewayError("本轮研究资料已达到存储上限。", 429)
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        source["content_hash"] = digest
        source["origin_cluster"] = (cluster_override if isinstance(cluster_override, str) and
                                    re.fullmatch(r"sha256:[0-9a-f]{64}", cluster_override) else "sha256:" + digest)
        source["evidence_id"] = uid()
        if not source.get("file_id"):
            archived = {**source, "evidence_text": content}
            cls._insert_material_file(db, owner, run, archived)
            source["file_id"] = archived["file_id"]
        else:
            cls._file(db, owner, run["session_id"], source["file_id"])
        db.execute(
            "INSERT INTO explore_evidence(id,owner,session_id,run_id,execution_id,source_id,"
            "file_id,role,content,content_hash,origin_cluster,metadata,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (source["evidence_id"], owner, run["session_id"], run["id"],
             run.get("execution_id"), source_id, source["file_id"], role, content,
             digest, source["origin_cluster"], metadata_text, timestamp()))
        return source

    @staticmethod
    def _next_source_id(run):
        numbers = [int(item["id"][1:]) for item in run.get("materials") or []
                   if isinstance(item.get("id"), str) and re.fullmatch(r"S[1-9][0-9]*", item["id"])]
        return f"S{max(numbers, default=0) + 1}"

    @classmethod
    def _authorized_selected_db(cls, db, owner, run):
        """Frozen context plus the exact materials the user confirmed for report phase."""
        selected = list(run.get("context_files") or [])
        if run.get("phase") != "report":
            return selected
        original = {item.get("id"): item for item in run.get("materials") or []
                    if isinstance(item, dict) and isinstance(item.get("id"), str)}
        seen = {item.get("file_id") for item in selected}
        for confirmed in run.get("confirmed_sources") or []:
            if not isinstance(confirmed, dict):
                continue
            material = original.get(confirmed.get("id"))
            file_id = confirmed.get("file_id")
            if (not material or not isinstance(file_id, str) or not file_id or
                    material.get("file_id") != file_id or file_id in seen):
                continue
            if any(key in material and confirmed.get(key) != material[key]
                   for key in ("evidence_id", "content_hash", "origin_cluster", "kind")):
                continue
            evidence_id = material.get("evidence_id")
            if evidence_id:
                match = db.execute(
                    "SELECT 1 FROM explore_evidence WHERE id=? AND owner=? AND session_id=? "
                    "AND run_id=? AND source_id=? AND file_id=? AND role IN ('source','selected_read')",
                    (evidence_id, owner, run["session_id"], run["id"], material["id"], file_id)).fetchone()
                if match is None:
                    continue
            file_row = db.execute(
                "SELECT name,metadata FROM explore_files WHERE id=? AND owner=? AND session_id=? "
                "AND deleted_at IS NULL", (file_id, owner, run["session_id"])).fetchone()
            if file_row is None:
                continue
            if not evidence_id:
                file_meta = json.loads(file_row["metadata"])
                if (file_meta.get("source_run_id") != run["id"] or
                        file_meta.get("source_id") != material["id"]):
                    continue
            selected.append({"file_id": file_id, "name": file_row["name"],
                             "reason": "用户已确认用于本轮报告的材料"})
            seen.add(file_id)
        return selected

    @classmethod
    def _selected_evidence_db(cls, db, owner, run):
        for snapshot in cls._authorized_selected_db(db, owner, run):
            file_id = snapshot.get("file_id")
            if not isinstance(file_id, str):
                continue
            row = db.execute(
                "SELECT content,metadata FROM explore_files WHERE id=? AND owner=? AND session_id=? "
                "AND deleted_at IS NULL", (file_id, owner, run["session_id"])).fetchone()
            if row is None:
                continue
            file_digest = hashlib.sha256(row["content"].encode("utf-8")).hexdigest()
            original = db.execute(
                "SELECT id,content,content_hash,origin_cluster FROM explore_evidence "
                "WHERE owner=? AND session_id=? AND file_id=? AND role IN ('source','selected_read') "
                "ORDER BY CASE role WHEN 'source' THEN 0 ELSE 1 END, created_at LIMIT 1",
                (owner, run["session_id"], file_id)).fetchone()
            original = original if original and isinstance(original["content"], str) else None
            digest = original["content_hash"] if original else file_digest
            inherited_cluster = (original["origin_cluster"] if original else
                                 json.loads(row["metadata"]).get("origin_cluster"))
            cluster = (inherited_cluster if isinstance(inherited_cluster, str) and
                       re.fullmatch(r"sha256:[0-9a-f]{64}", inherited_cluster) else "sha256:" + digest)
            selected_meta = {"file_content_hash": file_digest}
            if original:
                selected_meta["origin_evidence_id"] = original["id"]
            db.execute(
                "INSERT OR IGNORE INTO explore_evidence(id,owner,session_id,run_id,"
                "execution_id,source_id,file_id,role,content,content_hash,origin_cluster,metadata,created_at) "
                "VALUES(?,?,?,?,NULL,NULL,?,'selected',NULL,?,?,?,?)",
                (uid(), owner, run["session_id"], run["id"], file_id, digest,
                 cluster, encoded(selected_meta), run["created_at"]))

    @staticmethod
    def _selected_payload_db(db, owner, session_id, selected_row, file_content):
        metadata = json.loads(selected_row["metadata"])
        file_digest = hashlib.sha256(file_content.encode("utf-8")).hexdigest()
        if metadata.get("file_content_hash", selected_row["content_hash"]) != file_digest:
            raise GatewayError("证据内容已变化，不能继续读取。", 409)
        origin_id = metadata.get("origin_evidence_id")
        if not origin_id:
            return file_content, {}
        original = db.execute(
            "SELECT content,metadata FROM explore_evidence WHERE id=? AND owner=? "
            "AND session_id=? AND file_id=? AND role IN ('source','selected_read')",
            (origin_id, owner, session_id, selected_row["file_id"])).fetchone()
        if original is None or not isinstance(original["content"], str):
            raise GatewayError("原始证据已不可用。", 404)
        return original["content"], json.loads(original["metadata"])

    @classmethod
    def _insert_report_file(cls, db, owner, run, report):
        origin_key = f"report:{report['id']}"
        file_id = file_id_for(origin_key)
        report["file_id"] = file_id
        metadata = {"source_run_id": run["id"], "source_report_id": report["id"], "source_id": None,
                    "url": None, "content_kind": "ai_report", "published_at": None,
                    "retrieved_at": report.get("created_at"),
                    "context_text": "\n".join([
                        "AI生成的研究推论，未在本次独立核实",
                        "报告标题：" + str(report.get("title") or ""),
                        "研究时点：" + str(report.get("as_of") or ""),
                        "概述：" + str(report.get("summary") or ""),
                        "核心发现：" + "；".join(str(item) for item in (report.get("key_findings") or [])[:4]),
                        "不确定性：" + "；".join(str(item) for item in (report.get("uncertainties") or [])[:3])])}
        cls._insert_file(db, file_id=file_id, owner=owner, session_id=run["session_id"],
                         origin_key=origin_key, kind="report", origin="report",
                         name=cls._file_name({"event": "理解事件报告", "relation": "发现关联报告",
                                              "history": "历史验证报告"}.get(run["module"], "研究报告"),
                                             report.get("title"), report.get("created_at")),
                         content=report["markdown"], metadata=metadata,
                         created_at=report.get("created_at") or run["updated_at"])
        return file_id

    def _migrate_files(self, db):
        # Startup-only, idempotent backfill. Tombstones stay in place, so a
        # file removed by the user cannot reappear after a restart.
        rows = db.execute("SELECT owner,doc FROM runs WHERE status!='cancelled'").fetchall()
        for row in rows:
            run = json.loads(row["doc"])
            changed = False
            for source in run.get("materials") or []:
                before = source.get("file_id")
                if isinstance(before, str):
                    existing = db.execute("SELECT 1 FROM explore_files WHERE id=? AND owner=? AND session_id=?",
                                          (before, row["owner"], run["session_id"])).fetchone()
                    if existing:
                        # The material can belong to an earlier execution of
                        # this same run. Keep both its identity and tombstone.
                        continue
                self._insert_material_file(db, row["owner"], run, source,
                                           preserved_file_id=before if isinstance(before, str) else None)
                changed |= source["file_id"] != before
            if changed:
                self._save_run(db, row["owner"], run)
        reports = db.execute("SELECT owner,doc,run_id FROM reports").fetchall()
        for row in reports:
            report = json.loads(row["doc"])
            run_row = db.execute("SELECT doc FROM runs WHERE id=? AND owner=?",
                                 (row["run_id"], row["owner"])).fetchone()
            if run_row is None:
                continue
            run = json.loads(run_row["doc"])
            by_source = {item["id"]: item.get("file_id") for item in run.get("materials") or []}
            for key in ("sources", "confirmed_sources"):
                for source in report.get(key) or []:
                    if by_source.get(source.get("id")):
                        source["file_id"] = by_source[source["id"]]
            before = report.get("file_id")
            self._insert_report_file(db, row["owner"], run, report)
            if report["file_id"] != before or not before:
                db.execute("UPDATE reports SET doc=? WHERE id=? AND owner=?",
                           (encoded(report), report["id"], row["owner"]))

    def list_files(self, owner, session_id):
        with self.transaction() as db:
            self._session(db, owner, session_id)
            rows = db.execute("SELECT * FROM explore_files WHERE owner=? AND session_id=? AND deleted_at IS NULL "
                              "ORDER BY julianday(created_at) DESC,created_at DESC,id DESC",
                              (owner, session_id)).fetchall()
            return [self._file_meta(row) for row in rows]

    def get_file(self, owner, session_id, file_id):
        with self.transaction() as db:
            self._session(db, owner, session_id)
            row = self._file(db, owner, session_id, file_id)
            return {**self._file_meta(row), "content": row["content"]}

    def rename_file(self, owner, session_id, file_id, name):
        with self.transaction() as db:
            self._session(db, owner, session_id, require_open=True)
            self._file(db, owner, session_id, file_id)
            db.execute("UPDATE explore_files SET name=?,updated_at=? WHERE id=? AND owner=? AND session_id=?",
                       (name, timestamp(), file_id, owner, session_id))
            return self._file_meta(self._file(db, owner, session_id, file_id))

    def delete_file(self, owner, session_id, file_id):
        with self.transaction() as db:
            self._session(db, owner, session_id, require_open=True)
            self._file(db, owner, session_id, file_id)
            db.execute("UPDATE explore_files SET deleted_at=?,updated_at=? WHERE id=? AND owner=? AND session_id=?",
                       (timestamp(), timestamp(), file_id, owner, session_id))
            for row in db.execute("SELECT module,file_ids FROM context_preferences WHERE owner=? AND session_id=?",
                                  (owner, session_id)).fetchall():
                ids = [item for item in json.loads(row["file_ids"]) if item != file_id]
                db.execute("UPDATE context_preferences SET file_ids=?,updated_at=? WHERE owner=? AND session_id=? AND module=?",
                           (encoded(ids), timestamp(), owner, session_id, row["module"]))

    def create_session(self, owner, title):
        now = timestamp()
        session = {"id": uid(), "title": title, "created_at": now, "updated_at": now, "closed_at": None}
        with self.transaction() as db:
            db.execute("INSERT INTO sessions(id,owner,title,created_at,updated_at) VALUES(?,?,?,?,?)",
                       (session["id"], owner, title, now, now))
        return session

    def list_sessions(self, owner):
        with self.transaction() as db:
            return [{key: row[key] for key in ("id", "title", "created_at", "updated_at", "closed_at")}
                    for row in db.execute("SELECT * FROM sessions WHERE owner=? ORDER BY updated_at DESC LIMIT 200", (owner,))]

    def get_session(self, owner, session_id):
        with self.transaction() as db:
            session = self._session(db, owner, session_id)
            rows = db.execute("SELECT id FROM runs WHERE owner=? AND session_id=? ORDER BY created_at,id",
                              (owner, session_id)).fetchall()
            return session, [self._run(db, owner, row["id"]) for row in rows]

    def rename_session(self, owner, session_id, title):
        with self.transaction() as db:
            self._session(db, owner, session_id, require_open=True)
            db.execute("UPDATE sessions SET title=?,updated_at=? WHERE id=? AND owner=?",
                       (title, timestamp(), session_id, owner))
            return self._session(db, owner, session_id)

    def delete_session(self, owner, session_id):
        with self.transaction() as db:
            self._session(db, owner, session_id, require_open=True)
            db.execute("DELETE FROM sessions WHERE id=? AND owner=?", (session_id, owner))

    def create_run(self, owner, request, run, max_concurrent, selection_override=None):
        request_hash = hashlib.sha256(encoded(request).encode()).hexdigest()
        key = request["idempotency_key"]
        with self.transaction() as db:
            old = db.execute("SELECT digest,run_id FROM requests WHERE owner=? AND key=?", (owner, key)).fetchone()
            if old:
                if old["digest"] != request_hash:
                    raise GatewayError("同一提交标识不能用于不同分析。", 409, "idempotency_conflict")
                return self._run(db, owner, old["run_id"]), False
            self._session(db, owner, run["session_id"], require_open=True)
            previous = None
            if run.get("retry_of"):
                previous = self._run(db, owner, run["retry_of"])
                if previous["session_id"] != run["session_id"]:
                    raise GatewayError("重试记录不属于当前探索。", 404)
                if (previous.get("source_report_id") != run.get("source_report_id") or
                        previous.get("source_run_id") != run.get("source_run_id") or
                        previous.get("module") != run.get("module") or
                        previous.get("question") != run.get("question") or
                        previous.get("status") not in TERMINAL):
                    raise GatewayError("重试内容与原研究不一致。", 409, "explore_invalid_retry")
            continuation = None
            if run.get("source_report_id"):
                source = db.execute("SELECT doc,run_id FROM reports WHERE id=? AND owner=? AND session_id=?",
                                    (run["source_report_id"], owner, run["session_id"])).fetchone()
                if source is None:
                    raise GatewayError("来源报告不存在或无权访问。", 404)
                source_report = json.loads(source["doc"])
                if has_final_choice(source_report):
                    if not any(item.get("module") == run["module"] and item.get("question") == run["question"]
                               for item in source_report.get("recommendations") or []):
                        raise GatewayError("请选择本报告生成的进一步研究选项。", 400, "explore_invalid_recommendation")
                    source_owner_run = self._run(db, owner, source["run_id"])
                    if previous is None:
                        if source_owner_run.get("next_step_state") != "pending":
                            raise GatewayError("该报告已选择后续操作，请查看对应的新流程。", 409,
                                               "explore_stale_next_step")
                        continuation = source_owner_run
                    else:
                        # The parent report was consumed by the first child.
                        # Walk only this same recommendation's retry lineage.
                        root_id = source_owner_run.get("continuation_run_id")
                        cursor, visited = previous, set()
                        while cursor and cursor["id"] not in visited:
                            if cursor["id"] == root_id:
                                break
                            visited.add(cursor["id"])
                            ancestor_id = cursor.get("retry_of")
                            cursor = self._run(db, owner, ancestor_id) if ancestor_id else None
                        if (source_owner_run.get("next_step_state") != "continued" or
                                cursor is None or cursor["id"] != root_id):
                            raise GatewayError("该报告已选择其他后续操作。", 409,
                                               "explore_stale_next_step")
            source_run = None
            if run.get("source_report_id"):
                source_run = self._run(db, owner, source["run_id"])
            if run.get("source_run_id"):
                direct = self._run(db, owner, run["source_run_id"])
                if direct["session_id"] != run["session_id"]:
                    raise GatewayError("来源研究不存在或无权访问。", 404)
                if source_run is not None and direct["id"] != source_run["id"]:
                    raise GatewayError("来源研究与来源报告不一致。", 400, "explore_memory_source_mismatch")
                if source_run is None:
                    # A direct run link is only for the event's saved first
                    # explanation, before a formal report exists. Reports must
                    # use source_report_id and its recommendation gate. The
                    # one exception is a retry of a child that already froze
                    # this exact event-first-answer snapshot before the
                    # report appeared.
                    retry_parent = (previous or {}).get("memory_input") or {}
                    retry_of_first_answer = (previous is not None and
                                             retry_parent.get("run_id") == direct["id"] and
                                             retry_parent.get("phase") == "initial_explanation")
                    if direct.get("report") is not None and not retry_of_first_answer:
                        raise GatewayError("该研究已有正式报告，请从报告的后续问题继续。", 400,
                                           "explore_memory_source_report_required")
                    valid_first_answer = (direct.get("module") == "event" and
                                          isinstance(direct.get("initial_answer"), str) and
                                          bool(direct["initial_answer"].strip()) and
                                          (retry_of_first_answer or direct.get("status") == "awaiting_feedback" or
                                           (direct.get("status") == "succeeded" and
                                            direct.get("feedback_choice") == "understood")))
                    if not valid_first_answer:
                        raise GatewayError("来源研究尚未形成可继续的第一步解释。", 409,
                                           "explore_memory_source_pending")
                    output = direct.get("memory_output")
                    if not retry_of_first_answer and output is not None and output.get("phase") != "initial_explanation":
                        raise GatewayError("来源研究记忆阶段不匹配。", 409,
                                           "explore_memory_source_invalid")
                source_run = direct
            if "model" not in run:
                # Follow only an owner-validated parent. A retry keeps its
                # original model choice, including the legacy absent choice.
                model_parent = previous if previous is not None else source_run
                if model_parent is not None and "model" in model_parent:
                    run["model"] = model_parent["model"]
            if previous is not None:
                # A retry reuses the exact parent state from the original run.
                parent = previous.get("memory_input")
            elif source_run is not None:
                parent = self._ensure_source_memory_db(db, owner, source_run)
                if parent is None:
                    raise GatewayError("来源研究尚未生成可用记忆，请等待回答完成。", 409,
                                       "explore_memory_source_pending")
            else:
                parent = None
            run["memory_input"] = self._validate_memory_db(db, owner, run["session_id"], parent)
            run["memory_output"] = None
            run["memory_chain_id"] = (run["memory_input"] or {}).get("chain_id") or run["id"]
            if run["status"] in ACTIVE:
                self._check_capacity(db, run["session_id"], max_concurrent)
            selection, snapshots, limitations, ancestry = self._context_plan_db(
                db, owner, run["session_id"], run["module"], run.get("source_report_id"), selection_override)
            if selection_override is not None:
                self._set_context_db(db, owner, run["session_id"], run["module"],
                                     selection_override["mode"], selection_override["file_ids"])
            for item in ancestry:
                item["summary"] = item["summary"][:900]
                item["key_findings"] = [text[:300] for text in item["key_findings"][:3]]
                item["uncertainties"] = [text[:300] for text in item["uncertainties"][:3]]
                item["limitations"] = [text[:300] for text in item["limitations"][:3]]
                item["sections"] = [{**section, "body": str(section.get("body") or "")[:300]}
                                    for section in item["sections"][:2]]
            run["context_snapshot"] = ancestry
            run["context_selection"] = selection
            run["context_files"] = snapshots
            run["context_limitations"] = limitations
            db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?)",
                       (run["id"], owner, run["session_id"], run["status"], encoded(run),
                        run["created_at"], run["updated_at"]))
            self._selected_evidence_db(db, owner, run)
            if continuation is not None:
                continuation.update(next_step_state="continued", continuation_run_id=run["id"],
                                    updated_at=run["created_at"])
                self._save_run(db, owner, continuation)
                self._event(db, owner, continuation, "continued")
            db.execute("INSERT INTO requests VALUES(?,?,?,?)", (owner, key, request_hash, run["id"]))
            self._event(db, owner, run, run["stage"])
            db.execute("UPDATE sessions SET updated_at=? WHERE id=? AND owner=?",
                       (run["updated_at"], run["session_id"], owner))
            return {**run, "report": None}, True

    @staticmethod
    def _check_capacity(db, session_id, max_concurrent):
        active = db.execute("SELECT count(*) FROM runs WHERE status IN ('queued','running')").fetchone()[0]
        if active >= max_concurrent:
            raise GatewayError("当前分析任务已达到并发上限，请稍后再试。", 429, "explore_busy")
        same = db.execute("SELECT count(*) FROM runs WHERE session_id=? AND status IN ('queued','running')",
                          (session_id,)).fetchone()[0]
        if same:
            raise GatewayError("当前探索已有正在进行的分析，请完成或取消后再提交。", 409, "session_busy")

    def get_run(self, owner, run_id):
        with self.transaction() as db:
            return self._run(db, owner, run_id)

    def validate_memory(self, owner, run_id, execution_id=None):
        """Return the current phase's exact model input after live checks."""
        with self.transaction() as db:
            run = self._run(db, owner, run_id)
            self._session(db, owner, run["session_id"])
            if execution_id is not None and run.get("execution_id") != execution_id:
                raise GatewayError("本轮研究状态已变化，请刷新后重试。", 409,
                                   "explore_stale_memory_execution")
            # The same event run has a second model phase after publishing its
            # first explanation. Its frozen memory_input remains the parent.
            effective = run.get("memory_output") or run.get("memory_input")
            return self._validate_memory_db(db, owner, run["session_id"], effective)

    def list_memories(self, owner, session_id, run_id=None):
        """List all versions, or one run's exact parent chain, oldest first."""
        with self.transaction() as db:
            self._session(db, owner, session_id)
            if run_id is None:
                rows = db.execute("SELECT * FROM research_memories WHERE owner=? AND session_id=? "
                                  "ORDER BY created_at,id", (owner, session_id)).fetchall()
                return [self._memory_snapshot(row) for row in rows]
            run = self._run(db, owner, run_id)
            if run["session_id"] != session_id:
                raise GatewayError("研究记录不存在或无权访问。", 404)
            head = run.get("memory_output") or run.get("memory_input")
            result, seen = [], set()
            current = (head or {}).get("id")
            while current:
                if current in seen:
                    raise GatewayError("研究记忆版本链无效。", 409, "explore_memory_source_cycle")
                seen.add(current)
                row = db.execute("SELECT * FROM research_memories WHERE id=? AND owner=? AND session_id=?",
                                 (current, owner, session_id)).fetchone()
                if row is None:
                    raise GatewayError("研究记忆版本不可用。", 409, "explore_memory_unavailable")
                item = self._memory_snapshot(row)
                result.append(item)
                current = item["parent_id"]
            return list(reversed(result))

    def get_memory(self, owner, session_id, memory_id):
        with self.transaction() as db:
            self._session(db, owner, session_id)
            row = db.execute("SELECT * FROM research_memories WHERE id=? AND owner=? AND session_id=?",
                             (memory_id, owner, session_id)).fetchone()
            if row is None:
                raise GatewayError("研究记忆版本不存在或无权访问。", 404)
            return self._memory_snapshot(row)

    def get_report(self, owner, report_id):
        with self.transaction() as db:
            row = db.execute("SELECT doc FROM reports WHERE id=? AND owner=?", (report_id, owner)).fetchone()
            if row is None:
                raise GatewayError("报告不存在或无权访问。", 404)
            return json.loads(row["doc"])

    def events(self, owner, run_id, after=0):
        with self.transaction() as db:
            self._run(db, owner, run_id)
            rows = db.execute("SELECT seq,stage,at FROM events WHERE owner=? AND run_id=? AND seq>? ORDER BY seq LIMIT 100",
                              (owner, run_id, after)).fetchall()
            return [{"event_id": row["seq"], "stage": row["stage"], "at": row["at"]} for row in rows]

    @classmethod
    def _ancestor_context_db(cls, db, owner, source_report_id, max_ancestors=4):
        if not source_report_id:
            return []
        result, current, visited = [], source_report_id, set()
        while current and len(result) < max_ancestors:
            if current in visited:
                raise GatewayError("报告分支出现循环引用。", 409)
            visited.add(current)
            row = db.execute("SELECT doc,run_id FROM reports WHERE id=? AND owner=?", (current, owner)).fetchone()
            if row is None:
                raise GatewayError("来源报告不存在或无权访问。", 404)
            report = json.loads(row["doc"])
            run = cls._run(db, owner, row["run_id"])
            result.append({"report_id": current, "title": report.get("title", ""),
                           "question": run["question"], "module": run["module"],
                           "resolved_question": run.get("resolved_question"),
                           "summary": report["summary"], "key_findings": report["key_findings"],
                           "uncertainties": report["uncertainties"],
                           "limitations": report["limitations"], "source_report_id": run.get("source_report_id"),
                           "as_of": report.get("as_of"), "inheritance_label": "AI研究推论，未在本次运行独立核实",
                           "sections": [{"heading": item.get("heading"), "body": item.get("body"),
                                         "source_refs": item.get("source_refs", [])}
                                        for item in report.get("sections", [])[:4]],
                           "sources": [{key: source.get(key) for key in
                                        ("id", "title", "source", "source_name", "published_at", "retrieved_at", "url")}
                                       for source in report.get("sources", [])[:4]]})
            current = run.get("source_report_id")
        if current and result:
            result[-1]["ancestors_truncated"] = True
        return result

    def ancestor_context(self, owner, source_report_id, max_ancestors=4):
        with self.transaction() as db:
            return self._ancestor_context_db(db, owner, source_report_id, max_ancestors)

    @staticmethod
    def _contexts_db(db, owner, session_id):
        contexts = {module: {"module": module, "mode": "auto", "file_ids": []} for module in MODULES}
        for row in db.execute("SELECT module,mode,file_ids FROM context_preferences WHERE owner=? AND session_id=?",
                              (owner, session_id)):
            if row["module"] in contexts:
                contexts[row["module"]] = {"module": row["module"], "mode": row["mode"],
                                           "file_ids": json.loads(row["file_ids"])}
        return contexts

    def get_contexts(self, owner, session_id):
        with self.transaction() as db:
            self._session(db, owner, session_id)
            return self._contexts_db(db, owner, session_id)

    @classmethod
    def _validate_selection_db(cls, db, owner, session_id, mode, file_ids):
        if not isinstance(mode, str) or mode not in {"auto", "manual"} or not isinstance(file_ids, list) or len(file_ids) > MAX_CONTEXT_FILES or \
                any(not isinstance(item, str) for item in file_ids) or len(file_ids) != len(set(file_ids)):
            raise GatewayError("研究文件选择不正确。", 400)
        if mode == "auto" and file_ids:
            raise GatewayError("自动上下文不应指定文件。", 400)
        if mode == "manual":
            for file_id in file_ids:
                cls._file(db, owner, session_id, file_id)

    @classmethod
    def _set_context_db(cls, db, owner, session_id, module, mode, file_ids):
        if module not in MODULES:
            raise GatewayError("研究模块不正确。", 400)
        cls._validate_selection_db(db, owner, session_id, mode, file_ids)
        db.execute("INSERT INTO context_preferences VALUES(?,?,?,?,?,?) "
                   "ON CONFLICT(owner,session_id,module) DO UPDATE SET "
                   "mode=excluded.mode,file_ids=excluded.file_ids,updated_at=excluded.updated_at",
                   (owner, session_id, module, mode, encoded(file_ids), timestamp()))
        return {"module": module, "mode": mode, "file_ids": file_ids}

    def set_context(self, owner, session_id, module, mode, file_ids):
        with self.transaction() as db:
            self._session(db, owner, session_id, require_open=True)
            return self._set_context_db(db, owner, session_id, module, mode, file_ids)

    @classmethod
    def _context_plan_db(cls, db, owner, session_id, module, source_report_id, override=None):
        contexts = cls._contexts_db(db, owner, session_id)
        setting = override or contexts[module]
        mode, requested_ids = setting["mode"], setting["file_ids"]
        cls._validate_selection_db(db, owner, session_id, mode, requested_ids)
        ancestry = cls._ancestor_context_db(db, owner, source_report_id) if mode == "auto" else []
        reasons, candidate_ids = {}, []
        if mode == "manual":
            for file_id in requested_ids:
                reasons[file_id] = "用户明确选择的同会话研究文件"
                candidate_ids.append(file_id)
        else:
            # Reserve report slots first, then take only materials belonging to
            # those exact reports. Sibling branches never enter this list.
            for item in ancestry:
                file_id = file_id_for("report:" + item["report_id"])
                candidate_ids.append(file_id)
                reasons[file_id] = "选中来源报告的 AI 推论" if item is ancestry[0] else "必要祖先报告的 AI 推论"
            for item in ancestry:
                report_row = db.execute("SELECT doc FROM reports WHERE id=? AND owner=? AND session_id=?",
                                        (item["report_id"], owner, session_id)).fetchone()
                if not report_row:
                    continue
                report = json.loads(report_row["doc"])
                for source in report.get("sources") or []:
                    file_id = source.get("file_id")
                    if file_id:
                        candidate_ids.append(file_id)
                        reasons[file_id] = "来源报告确认使用的材料；网页/快讯仅为摘录"
        seen, selected_rows = set(), []
        for file_id in candidate_ids:
            if file_id in seen:
                continue
            seen.add(file_id)
            row = db.execute("SELECT * FROM explore_files WHERE id=? AND owner=? AND session_id=? AND deleted_at IS NULL",
                             (file_id, owner, session_id)).fetchone()
            if row is not None:
                selected_rows.append((row, reasons[file_id]))
        omitted = max(0, len(selected_rows) - MAX_CONTEXT_FILES)
        selected_rows = selected_rows[:MAX_CONTEXT_FILES]
        snapshots = []
        for row, reason in selected_rows:
            content = row["content"]
            meta = cls._file_meta(row)
            context_text = json.loads(row["metadata"]).get("context_text") or content
            excerpt = context_text[:CONTEXT_EXCERPT_CHARS]
            snapshots.append({"file_id": row["id"], "name": row["name"], "kind": row["kind"],
                              "origin": row["origin"], "source_run_id": meta.get("source_run_id"),
                              "source_report_id": meta.get("source_report_id"), "url": meta.get("url"),
                              "content_kind": meta.get("content_kind"),
                              "content_excerpt": excerpt,
                              "original_chars": len(content),
                              "used_chars": len(excerpt),
                              "truncated": len(content) > len(excerpt) or len(context_text) > CONTEXT_EXCERPT_CHARS,
                              "extraction_rule": ("报告优先保留概述、核心发现和不确定性；仅作 AI 推论"
                                                  if row["kind"] == "report" else
                                                  "材料优先保留实际可用摘录与来源元数据"),
                              "reason": reason,
                              "evidence_role": ("AI 推论，未在本次独立核实" if row["kind"] == "report"
                                                else "来源摘录或用户输入，需按来源类型核对")})
        limitations = []
        if omitted:
            limitations.append(f"上下文文件超过 {MAX_CONTEXT_FILES} 份，本次未纳入其余 {omitted} 份。")
        truncated = sum(item["truncated"] for item in snapshots)
        if truncated:
            limitations.append(f"{truncated} 份上下文文件正文按每份 {CONTEXT_EXCERPT_CHARS} 字截取，完整文本仍保存在文件池。")
        if ancestry and ancestry[-1].get("ancestors_truncated"):
            limitations.append("来源报告祖先超过 4 层，本次未纳入更早分支。")
        actual_ids = [item["file_id"] for item in snapshots]
        selection = {"module": module, "mode": mode, "file_ids": actual_ids}
        return selection, snapshots, limitations, ancestry

    def preview_context(self, owner, session_id, module, source_report_id=None):
        with self.transaction() as db:
            self._session(db, owner, session_id)
            if module not in MODULES:
                raise GatewayError("研究模块不正确。", 400)
            if source_report_id:
                row = db.execute("SELECT 1 FROM reports WHERE id=? AND owner=? AND session_id=?",
                                 (source_report_id, owner, session_id)).fetchone()
                if row is None:
                    raise GatewayError("来源报告不存在或无权访问。", 404)
            setting = self._contexts_db(db, owner, session_id)[module]
            selection, snapshots, limitations, _ = self._context_plan_db(db, owner, session_id, module,
                                                                          source_report_id)
            return {**setting, "preview_file_ids": selection["file_ids"],
                    "preview_files": [{key: item[key] for key in
                                       ("file_id", "name", "kind", "origin", "reason", "truncated")}
                                      for item in snapshots], "preview_limitations": limitations}

    def transition(self, owner, run_id, allowed, *, status=None, stage=None, error=None, judgment=None,
                   max_concurrent=None, execution_id=None, fields=None):
        with self.transaction() as db:
            try:
                run = self._run(db, owner, run_id)
            except GatewayError as exc:
                if exc.status == 404:
                    return None
                raise
            if run["status"] not in allowed:
                return None
            if execution_id is not None and run.get("execution_id") != execution_id:
                return None
            if max_concurrent is not None and status in ACTIVE and run["status"] not in ACTIVE:
                self._check_capacity(db, run["session_id"], max_concurrent)
            run["updated_at"] = timestamp()
            if status:
                run["status"] = status
            if stage and stage != run["stage"]:
                run["stage"] = stage
                run["stage_history"].append({"stage": stage, "at": run["updated_at"]})
                self._event(db, owner, run, stage)
            if error is not None:
                run["error"] = error
            if judgment is not None:
                run["judgment"] = judgment
            if fields:
                run.update(fields)
            self._save_run(db, owner, run)
            return run

    def save_event_initial(self, owner, run_id, answer, *, execution_id, fields=None):
        """Publish the first explanation and its two pool files atomically."""
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 8000:
            raise GatewayError("首轮解释内容无效。", 502, "explore_invalid_model_output")
        with self.transaction() as db:
            try:
                run = self._run(db, owner, run_id)
            except GatewayError as exc:
                if exc.status == 404:
                    return None
                raise
            session = self._session(db, owner, run["session_id"])
            if session["closed_at"] or run.get("execution_id") != execution_id or run["module"] != "event":
                return None
            if run["status"] == "awaiting_feedback" and run.get("initial_answer") == answer:
                return run
            if run["status"] not in ACTIVE or run.get("phase") != "initial_explanation":
                return None
            saved_fields = dict(fields or {})
            private_search = bool(saved_fields.pop("_initial_private_search", False))
            if private_search:
                # First-step search only informs this execution's answer. Do
                # not publish its source snapshot, trace, or links into a run.
                saved_fields["initial_sources"] = []
                saved_fields["initial_retrieval"] = {}
            initial_sources = saved_fields.get("initial_sources") or []
            initial_search_attempted = bool(saved_fields.get("initial_search_attempted"))
            answer_mode = saved_fields.get("initial_answer_mode") or "model_complete"
            answer_sources = initial_sources if answer_mode != "knowledge_fallback" else []
            if answer_mode in {"model_partial", "source_excerpt"}:
                cited = set(re.findall(r"\[(S\d+)\]", answer))
                answer_sources = [source for source in initial_sources if source.get("id") in cited]
            # A removed source must not be resurrected by a late model answer.
            required_files = {source["file_id"] for source in initial_sources if source.get("file_id")}
            if required_files:
                readable = {row[0] for row in db.execute(
                    "SELECT id FROM explore_files WHERE owner=? AND session_id=? AND deleted_at IS NULL",
                    (owner, run["session_id"]))}
                if not required_files <= readable:
                    return None
            now = timestamp()
            files = {}
            for slot, kind, origin, prefix, content_kind, text in (
                ("input", "material", "user", "理解事件原始输入", "user_input", run["question"]),
                ("answer", "report", "report", "理解事件第一步解释", "ai_report", answer),
            ):
                origin_key = f"event-initial:{run_id}:{execution_id}:{slot}"
                file_id = file_id_for(origin_key)
                if slot == "input":
                    label = "用户输入，未经独立核实"
                elif answer_mode == "knowledge_fallback":
                    label = "一般知识说明，由系统整理，不是本次模型生成的结论或原始新闻证据"
                    content_kind = "knowledge_note"
                elif answer_mode == "source_excerpt":
                    label = ("临时联网摘要中的可用内容，由系统整理，不是模型分析结论"
                             if private_search else "本轮搜索摘要摘录，由系统整理，未读取全文，不是模型分析结论")
                    content_kind = "source_excerpt"
                elif answer_mode == "model_partial":
                    label = "AI已生成内容中的完整句子，属于简要分析，不是原始新闻证据"
                elif initial_search_attempted:
                    label = ("AI生成的简明解释，不是原始新闻证据" if private_search else
                             "AI根据第一步联网搜索摘要生成的解释，未读取新闻全文，不是原始新闻证据"
                             if initial_sources else "AI基于已有知识生成的解释，不是原始新闻证据")
                else:
                    label = "AI已有知识解释，未联网核实，不是新闻证据"
                source_notes = ""
                if slot == "answer" and answer_sources:
                    lines = ["", "## 第一步联网来源（搜索摘要，非全文）", ""]
                    for source in answer_sources:
                        lines.append("- " + markdown_text(source.get("id")) + "：" + markdown_text(source.get("title")))
                        if safe_url(source.get("url")):
                            lines.append("  原始链接：" + source["url"])
                    source_notes = "\n".join(lines) + "\n"
                self._insert_file(db, file_id=file_id, owner=owner, session_id=run["session_id"],
                    origin_key=origin_key, kind=kind, origin=origin,
                    name=self._file_name(prefix, run["question"], now),
                    content=f"# {prefix}\n\n{label}\n\n{text}\n" + source_notes,
                    metadata={"source_run_id": run_id, "source_report_id": None, "source_id": None,
                              "phase": "initial_explanation", "content_kind": content_kind,
                              "initial_search_attempted": initial_search_attempted,
                              "initial_answer_mode": answer_mode if slot == "answer" else None,
                              "source_ids": [source["id"] for source in answer_sources] if slot == "answer" else [],
                              "source_file_ids": sorted(source["file_id"] for source in answer_sources if source.get("file_id")) if slot == "answer" else [],
                              "retrieved_at": now, "published_at": None, "url": None,
                              "context_text": label + "\n" + text}, created_at=now)
                files[slot] = file_id
            run.update(saved_fields)
            run.update(initial_answer=answer, initial_file_ids=files, status="awaiting_feedback",
                       stage="awaiting_feedback", model_progress=None, updated_at=now)
            run["stage_history"].append({"stage": "awaiting_feedback", "at": now})
            self._publish_memory_db(db, owner, run, initial_answer=answer)
            self._save_run(db, owner, run)
            self._event(db, owner, run, "awaiting_feedback")
            db.execute("UPDATE sessions SET updated_at=? WHERE id=? AND owner=?", (now, run["session_id"], owner))
            return run

    def save_history_initial(self, owner, run_id, answer, *, execution_id, fields=None):
        """Publish the history reply before search, without pausing for feedback."""
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 8000:
            raise GatewayError("首轮解释内容无效。", 502, "explore_invalid_model_output")
        with self.transaction() as db:
            try:
                run = self._run(db, owner, run_id)
            except GatewayError as exc:
                if exc.status == 404:
                    return None
                raise
            session = self._session(db, owner, run["session_id"])
            if (session["closed_at"] or run.get("execution_id") != execution_id
                    or run.get("workflow_version") != "history-lite@1"
                    or run["module"] != "history" or run["status"] not in ACTIVE):
                return None
            if run.get("phase") != "history_initial":
                return run if run.get("initial_answer") == answer else None
            saved_fields = dict(fields or {})
            answer_mode = saved_fields.get("initial_answer_mode") or "model_complete"
            now, files = timestamp(), {}
            for slot, kind, origin, prefix, content_kind, text in (
                ("input", "material", "user", "历史验证原始输入", "user_input", run["question"]),
                ("answer", "report", "report", "历史验证第一步解释", "ai_report", answer),
            ):
                origin_key = f"history-initial:{run_id}:{execution_id}:{slot}"
                file_id = file_id_for(origin_key)
                if slot == "input":
                    label = "用户输入，未经独立核实"
                elif answer_mode == "system_fallback":
                    label = "系统整理的检索目的说明，不是本次模型生成的结论或原始新闻证据"
                    content_kind = "knowledge_note"
                elif answer_mode == "model_partial":
                    label = "AI已生成内容中的完整句子，属于简要分析，不是原始新闻证据"
                else:
                    label = "AI根据用户问题与已有分析解释检索目的，未经独立核实，不是新闻证据"
                self._insert_file(db, file_id=file_id, owner=owner, session_id=run["session_id"],
                    origin_key=origin_key, kind=kind, origin=origin,
                    name=self._file_name(prefix, run["question"], now),
                    content=f"# {prefix}\n\n{label}\n\n{text}\n",
                    metadata={"source_run_id": run_id, "source_report_id": None, "source_id": None,
                              "phase": "history_initial", "content_kind": content_kind,
                              "initial_answer_mode": answer_mode if slot == "answer" else None,
                              "retrieved_at": now, "published_at": None, "url": None,
                              "context_text": label + "\n" + text}, created_at=now)
                files[slot] = file_id
            run.update(saved_fields)
            run.update(initial_answer=answer, initial_file_ids=files, input_file_id=files["input"],
                       status="running", stage="history_initial_ready", phase="history_report",
                       model_progress=None, updated_at=now)
            run["stage_history"].append({"stage": "history_initial_ready", "at": now})
            # Memory validation also guards deleted input files in this transaction.
            self._publish_memory_db(db, owner, run, initial_answer=answer)
            self._save_run(db, owner, run)
            self._event(db, owner, run, "history_initial_ready")
            db.execute("UPDATE sessions SET updated_at=? WHERE id=? AND owner=?", (now, run["session_id"], owner))
            return run

    def finish_event(self, owner, run_id, key):
        """Consume the final OK once and close the session without erasing files."""
        digest = hashlib.sha256(encoded({"run_id": run_id, "action": "event_finish"}).encode()).hexdigest()
        with self.transaction() as db:
            previous = db.execute("SELECT digest,run_id FROM review_actions WHERE owner=? AND key=?",
                                  (owner, key)).fetchone()
            if previous:
                if previous["digest"] != digest:
                    raise GatewayError("同一提交标识不能用于不同确认操作。", 409, "idempotency_conflict")
                saved = self._run(db, owner, previous["run_id"])
                return saved, self._session(db, owner, saved["session_id"])
            run = self._run(db, owner, run_id)
            self._session(db, owner, run["session_id"], require_open=True)
            report = run.get("report") or {}
            if (run["status"] not in {"succeeded", "partial"}
                    or not has_final_choice(report) or run.get("next_step_state") != "pending"):
                raise GatewayError("本轮尚未等待结束确认，或已经选择后续操作。", 409, "explore_stale_next_step")
            now = timestamp()
            db.execute("UPDATE sessions SET closed_at=?,updated_at=? WHERE id=? AND owner=?",
                       (now, now, run["session_id"], owner))
            rows = db.execute("SELECT id FROM runs WHERE owner=? AND session_id=?", (owner, run["session_id"])).fetchall()
            for row in rows:
                item = self._run(db, owner, row["id"])
                if item["status"] in ACTIVE | {"awaiting_feedback", "awaiting_input", "awaiting_review", "awaiting_clarification"}:
                    item.update(status="cancelled", stage="cancelled", error="会话已结束。", model_progress=None)
                    item["stage_history"].append({"stage": "cancelled", "at": now})
                item.update(session_closed_at=now, updated_at=now)
                if item.get("next_step_state") == "pending" or item["id"] == run_id:
                    item.update(next_step_state="finished", finished_at=now)
                self._save_run(db, owner, item)
                self._event(db, owner, item, "session_closed")
            db.execute("INSERT INTO review_actions VALUES(?,?,?,?)", (owner, key, digest, run_id))
            return self._run(db, owner, run_id), self._session(db, owner, run["session_id"])

    def save_materials(self, owner, run_id, materials, retrieval, *, execution_id, stage="checking_intent"):
        """CAS stage update and file-pool insert in one transaction."""
        with self.transaction() as db:
            try:
                run = self._run(db, owner, run_id)
            except GatewayError as exc:
                if exc.status == 404:
                    return None
                raise
            if run["status"] not in ACTIVE or run.get("execution_id") != execution_id:
                return None
            for source in materials:
                self._store_source_evidence_db(db, owner, run, source)
            run["materials"] = materials
            run["retrieval"] = retrieval
            run["status"] = "running"
            run["updated_at"] = timestamp()
            if run["stage"] != stage:
                run["stage"] = stage
                run["stage_history"].append({"stage": stage, "at": run["updated_at"]})
                self._event(db, owner, run, stage)
            self._save_run(db, owner, run)
            return run

    @staticmethod
    def _active_evidence_run(db, owner, run_id, execution_id):
        run = ExploreRepository._run(db, owner, run_id)
        if (not isinstance(execution_id, str) or not execution_id or
                run["status"] not in ACTIVE or run.get("execution_id") != execution_id):
            raise GatewayError("本次研究已停止，证据不可继续读取。", 409, "explore_cancelled")
        return run

    def register_evidence(self, owner, run_id, source, *, execution_id):
        """Persist one tool-derived source, its full readable text and file-pool copy."""
        if not isinstance(source, dict):
            raise GatewayError("研究资料格式无效。", 400)
        content = self._evidence_text(source)
        if not content or len(content) > MAX_EVIDENCE_CHARS:
            raise GatewayError("研究资料正文为空或超过单份上限。", 400)
        with self.transaction() as db:
            run = self._active_evidence_run(db, owner, run_id, execution_id)
            source_id = self._next_source_id(run)
            raw_kind = source.get("kind")
            kind = (raw_kind if isinstance(raw_kind, str) and
                    re.fullmatch(r"[a-z][a-z0-9_]{0,63}", raw_kind) else "tool_result")
            if kind in {"user", "report"}:
                kind = "tool_result"
            title = str(source.get("title") or "未命名研究资料")[:300]
            summary = str(source.get("summary") or source.get("text_used") or content[:2400])[:2400]
            material = {
                "id": source_id, "kind": kind, "title": title, "summary": summary,
                "source": str(source.get("source") or kind)[:100],
                "source_name": str(source.get("source_name") or source.get("source") or kind)[:100],
                "published_at": str(source["published_at"])[:100] if source.get("published_at") else None,
                "retrieved_at": str(source.get("retrieved_at") or timestamp())[:100],
                "url": safe_url(source.get("url")),
                "provider_id": str(source.get("provider_id") or "")[:100],
                "content_kind": str(source.get("content_kind") or "tool_excerpt")[:64],
                "text_used": str(source.get("text_used") or summary)[:2400],
            }
            for key, maximum in (("canonical_url", 2048), ("final_url", 2048),
                                 ("publisher", 200), ("target_section", 300),
                                 ("status", 50), ("content_type", 100),
                                 ("extraction_method", 100)):
                if isinstance(source.get(key), str):
                    value = safe_url(source[key]) if key.endswith("_url") else source[key][:maximum]
                    if value is not None:
                        material[key] = value
            if type(source.get("http_status")) is int and 100 <= source["http_status"] <= 599:
                material["http_status"] = source["http_status"]
            if type(source.get("target_paragraph_index")) is int and source["target_paragraph_index"] >= 0:
                material["target_paragraph_index"] = source["target_paragraph_index"]
            for key in ("target_found", "truncated", "has_more"):
                if type(source.get(key)) is bool:
                    material[key] = source[key]
            locations = source.get("locations")
            if isinstance(locations, list):
                material["locations"] = [item for item in locations[:20]
                                         if isinstance(item, dict) and len(encoded(item)) <= 1000]
            headings = source.get("headings")
            if isinstance(headings, list):
                material["headings"] = [item for item in headings[:50]
                                        if isinstance(item, dict) and len(encoded(item)) <= 500]
            detail = {key: material[key] for key in
                      ("canonical_url", "final_url", "publisher", "target_section", "status",
                       "content_type", "extraction_method", "http_status", "target_found",
                       "target_paragraph_index", "truncated", "has_more", "locations",
                       "headings") if key in material}
            paragraphs = source.get("paragraphs")
            if isinstance(paragraphs, list):
                positions = []
                cursor = 0
                for index, paragraph in enumerate(paragraphs[:500]):
                    if not isinstance(paragraph, dict):
                        continue
                    item = {key: paragraph[key] for key in
                            ("index", "kind", "start", "end", "page", "anchor", "heading", "selector")
                            if key in paragraph and isinstance(paragraph[key], (str, int))}
                    item.setdefault("index", index)
                    paragraph_text = paragraph.get("text") or paragraph.get("content")
                    if ("start" not in item and isinstance(paragraph_text, str) and
                            paragraph_text):
                        position = content.find(paragraph_text, cursor)
                        if position < 0:
                            position = content.find(paragraph_text)
                        if position >= 0:
                            item.update(start=position, end=position + len(paragraph_text))
                            cursor = item["end"]
                    if item and len(encoded(item)) <= 500:
                        positions.append(item)
                    if len(encoded(positions)) >= 90_000:
                        break
                detail["paragraphs"] = positions
            self._store_source_evidence_db(db, owner, run, material, content=content,
                                           metadata=detail)
            run.setdefault("materials", []).append(material)
            run["updated_at"] = timestamp()
            self._save_run(db, owner, run)
            return material

    def selected_evidence(self, owner, run_id, *, execution_id):
        """List only files selected and frozen when this run was created."""
        with self.transaction() as db:
            run = self._active_evidence_run(db, owner, run_id, execution_id)
            # Existing runs may predate this table; a currently deleted file
            # is never reconstructed from the frozen excerpt.
            self._selected_evidence_db(db, owner, run)
            snapshots = self._authorized_selected_db(db, owner, run)
            selected_ids = [item.get("file_id") for item in snapshots]
            frozen_names = {item.get("file_id"): item.get("name") for item in snapshots}
            rows = db.execute(
                "SELECT e.*,f.name,f.kind,f.origin,f.content AS file_content,f.metadata,f.deleted_at "
                "FROM explore_evidence e JOIN explore_files f ON f.id=e.file_id "
                "WHERE e.owner=? AND e.run_id=? AND e.role='selected' "
                "AND f.owner=? AND f.session_id=? AND f.deleted_at IS NULL",
                (owner, run_id, owner, run["session_id"])).fetchall()
            by_file = {}
            for row in rows:
                try:
                    selected_text, _ = self._selected_payload_db(
                        db, owner, run["session_id"], row, row["file_content"])
                except GatewayError:
                    continue
                if hashlib.sha256(selected_text.encode("utf-8")).hexdigest() == row["content_hash"]:
                    by_file[row["file_id"]] = (row, len(selected_text))
            return [{"evidence_id": row["id"], "file_id": row["file_id"],
                     "title": frozen_names.get(file_id) or row["name"],
                     "kind": row["kind"], "origin": row["origin"],
                     "content_kind": json.loads(row["metadata"]).get("content_kind"),
                     "content_hash": row["content_hash"], "chars": length,
                     "role": ("background" if row["kind"] == "report" else
                              "user_input" if row["origin"] == "user" else "selected_material")}
                    for file_id in selected_ids if (pair := by_file.get(file_id)) is not None
                    for row, length in (pair,)]

    def active_evidence_sources(self, owner, run_id, *, execution_id):
        """Return current-run sources whose archived files remain readable."""
        with self.transaction() as db:
            run = self._active_evidence_run(db, owner, run_id, execution_id)
            rows = db.execute(
                "SELECT e.source_id FROM explore_evidence e "
                "JOIN explore_files f ON f.id=e.file_id AND f.owner=e.owner "
                "AND f.session_id=e.session_id "
                "WHERE e.owner=? AND e.run_id=? AND e.execution_id=? "
                "AND e.role IN ('source','selected_read') AND f.deleted_at IS NULL",
                (owner, run_id, execution_id)).fetchall()
            allowed = {row["source_id"] for row in rows}
            return [item for item in run.get("materials") or [] if item.get("id") in allowed]

    def read_evidence(self, owner, run_id, evidence_id, *, offset=0, limit=4000, execution_id):
        """Read an authorized range by opaque ID; deletion is checked on each call."""
        if (not isinstance(evidence_id, str) or not re.fullmatch(r"[0-9a-f-]{36}", evidence_id)):
            raise GatewayError("证据不存在或无权访问。", 404)
        if (type(offset) is not int or offset < 0 or type(limit) is not int or
                limit < 1 or limit > MAX_EVIDENCE_READ_CHARS):
            raise GatewayError("证据读取范围无效。", 400)
        with self.transaction() as db:
            run = self._active_evidence_run(db, owner, run_id, execution_id)
            row = db.execute(
                "SELECT * FROM explore_evidence WHERE id=? AND owner=? AND run_id=? AND session_id=?",
                (evidence_id, owner, run_id, run["session_id"])).fetchone()
            if row is None or (row["role"] != "selected" and row["execution_id"] != execution_id):
                raise GatewayError("证据不存在或无权访问。", 404)
            file_row = self._file(db, owner, run["session_id"], row["file_id"])
            selected_details = {}
            if row["role"] == "selected":
                snapshots = {item.get("file_id"): item for item in
                             self._authorized_selected_db(db, owner, run)}
                if row["file_id"] not in snapshots:
                    raise GatewayError("证据不存在或无权访问。", 404)
                content, selected_details = self._selected_payload_db(
                    db, owner, run["session_id"], row, file_row["content"])
            else:
                if row["source_id"] not in {item.get("id") for item in run.get("materials") or []}:
                    raise GatewayError("证据不存在或无权访问。", 404)
                content = row["content"]
            if not isinstance(content, str) or hashlib.sha256(content.encode("utf-8")).hexdigest() != row["content_hash"]:
                raise GatewayError("证据内容已变化，不能继续读取。", 409)
            if offset > len(content):
                raise GatewayError("证据读取起点超过正文长度。", 400)
            promoted = None
            if row["role"] == "selected" and file_row["kind"] != "report":
                earlier = db.execute(
                    "SELECT source_id FROM explore_evidence WHERE owner=? AND run_id=? "
                    "AND execution_id=? AND file_id=? AND role='selected_read'",
                    (owner, run_id, execution_id, row["file_id"])).fetchone()
                if earlier is None:
                    meta = json.loads(file_row["metadata"])
                    excerpt = str(meta.get("context_text") or content)[:2400]
                    promoted = {
                        "id": self._next_source_id(run), "kind": file_row["origin"],
                        "title": snapshots[row["file_id"]].get("name") or file_row["name"], "summary": excerpt,
                        "source": file_row["origin"], "source_name": file_row["origin"],
                        "published_at": meta.get("published_at"), "retrieved_at": meta.get("retrieved_at"),
                        "url": safe_url(meta.get("url")), "provider_id": "",
                        "content_kind": meta.get("content_kind") or "selected_excerpt",
                        "evidence_role": ("user_input_unverified" if file_row["origin"] == "user"
                                          else "prior_material_unverified"),
                        "text_used": excerpt, "file_id": row["file_id"],
                    }
                    self._store_source_evidence_db(db, owner, run, promoted,
                                                   content=content, role="selected_read",
                                                   cluster_override=row["origin_cluster"])
                    run.setdefault("materials", []).append(promoted)
                    run["updated_at"] = timestamp()
                    self._save_run(db, owner, run)
                else:
                    promoted = next((item for item in run.get("materials") or []
                                     if item.get("id") == earlier["source_id"]), None)
            elif row["role"] != "selected":
                promoted = next((item for item in run.get("materials") or []
                                 if item.get("id") == row["source_id"]), None)
            end = min(len(content), offset + limit)
            details = json.loads(row["metadata"])
            if row["role"] == "selected":
                details = {**json.loads(file_row["metadata"]), **selected_details}
            result = {
                "evidence_id": promoted["evidence_id"] if promoted else evidence_id,
                "requested_evidence_id": evidence_id,
                "content_hash": row["content_hash"],
                "origin_cluster": row["origin_cluster"],
                "title": (snapshots[row["file_id"]].get("name") or file_row["name"]
                          if row["role"] == "selected" else file_row["name"]),
                "role": "background" if file_row["kind"] == "report" else row["role"],
                "content_kind": json.loads(file_row["metadata"]).get("content_kind"),
                "offset": offset, "end_offset": end, "total_chars": len(content),
                "text": content[offset:end], "has_more": end < len(content),
                "next_offset": end if end < len(content) else None,
            }
            if promoted:
                result["source_id"] = promoted["id"]
                compact_keys = ("id", "evidence_id", "file_id", "kind", "title", "source_name",
                                "published_at", "retrieved_at", "url", "content_kind",
                                "content_hash", "origin_cluster", "evidence_role", "http_status",
                                "publisher", "target_found", "truncated")
                result["source"] = {key: promoted[key] for key in compact_keys if key in promoted}
                for key, maximum in (("title", 160), ("url", 700), ("source_name", 100)):
                    if isinstance(result["source"].get(key), str):
                        result["source"][key] = result["source"][key][:maximum]
                if isinstance(promoted.get("locations"), list):
                    result["source"]["locations"] = promoted["locations"][:2]
            for key in ("status", "http_status", "final_url", "canonical_url", "publisher",
                        "target_section", "target_found", "target_paragraph_index",
                        "truncated", "content_type",
                        "extraction_method"):
                if key in details:
                    value = details[key]
                    result[key] = value[:700] if key in {"final_url", "canonical_url"} and isinstance(value, str) else value
            if isinstance(details.get("paragraphs"), list):
                result["paragraph_count"] = len(details["paragraphs"])
                result["locations"] = details["paragraphs"][:3]
            if file_row["kind"] == "report":
                result["note"] = "旧 AI 报告仅作研究背景，不是本次独立核实的事实来源。"
            while len(encoded(result)) > 7900 and end > offset:
                end = max(offset, end - max(50, len(encoded(result)) - 7850))
                result.update(end_offset=end, text=content[offset:end],
                              has_more=end < len(content),
                              next_offset=end if end < len(content) else None)
            if len(encoded(result)) > 7900:
                result.pop("locations", None)
                if "source" in result:
                    result["source"].pop("locations", None)
            if len(encoded(result)) > 7900:
                for key in ("canonical_url", "final_url", "target_section", "publisher"):
                    result.pop(key, None)
                    if len(encoded(result)) <= 7900:
                        break
            if len(encoded(result)) > 7900:
                raise GatewayError("证据读取结果超过单次上限。", 502)
            return result

    def apply_review_action(self, owner, run_id, action, key, request, mutate, max_concurrent):
        digest = hashlib.sha256(encoded({"run_id": run_id, "action": action, "request": request}).encode()).hexdigest()
        with self.transaction() as db:
            previous = db.execute("SELECT digest,run_id FROM review_actions WHERE owner=? AND key=?",
                                  (owner, key)).fetchone()
            if previous:
                if previous["digest"] != digest:
                    raise GatewayError("同一提交标识不能用于不同确认操作。", 409, "idempotency_conflict")
                return self._run(db, owner, previous["run_id"]), False
            run = self._run(db, owner, run_id)
            self._session(db, owner, run["session_id"], require_open=True)
            previous_status, previous_stage = run["status"], run["stage"]
            mutate(run)
            if run["status"] in ACTIVE and previous_status not in ACTIVE:
                self._check_capacity(db, run["session_id"], max_concurrent)
            run["updated_at"] = timestamp()
            if run["stage"] != previous_stage:
                run["stage_history"].append({"stage": run["stage"], "at": run["updated_at"]})
                self._event(db, owner, run, run["stage"])
            self._save_run(db, owner, run)
            db.execute("INSERT INTO review_actions VALUES(?,?,?,?)", (owner, key, digest, run_id))
            return run, True

    def complete(self, owner, run_id, report, status, *, execution_id=None,
                 required_evidence_ids=None, required_selected_file_ids=None):
        with self.transaction() as db:
            try:
                run = self._run(db, owner, run_id)
            except GatewayError as exc:
                if exc.status == 404:
                    return False
                raise
            if run["status"] not in ACTIVE:
                return False
            if execution_id is not None and run.get("execution_id") != execution_id:
                return False
            # Timeout checkpoints must not restore evidence removed between an
            # earlier guard and saving. Check tombstones inside this transaction.
            if required_selected_file_ids:
                readable = {row[0] for row in db.execute(
                    "SELECT id FROM explore_files WHERE owner=? AND session_id=? AND deleted_at IS NULL",
                    (owner, run['session_id']))}
                if not set(required_selected_file_ids) <= readable:
                    return False
            if required_evidence_ids:
                readable = {row[0] for row in db.execute(
                    "SELECT e.id FROM explore_evidence e JOIN explore_files f ON f.id=e.file_id "
                    "AND f.owner=e.owner AND f.session_id=e.session_id "
                    "WHERE e.owner=? AND e.run_id=? AND e.execution_id=? "
                    "AND e.role IN ('source','selected_read') AND f.deleted_at IS NULL",
                    (owner, run_id, execution_id))}
                if not set(required_evidence_ids) <= readable:
                    return False
            if (run["module"], report.get("report_format")) == ("relation", "relation_lite@2"):
                # Input and report publish together, only after cancellation and
                # source-deletion guards pass. Retries cannot duplicate files.
                now = report.get("created_at") or timestamp()
                origin_key = f"relation-input:{run_id}:{run.get('execution_id')}"
                input_file_id = file_id_for(origin_key)
                label = "用户输入，未经独立核实"
                content = "# 发现关联原始输入\n\n" + label + "\n\n" + run["question"] + "\n"
                context_text = label + "\n" + run["question"]
                if run.get("resolved_question") and run["resolved_question"] != run["question"]:
                    content += "\n## 明确后的问题\n\n" + run["resolved_question"] + "\n"
                    context_text += "\n明确后的问题：" + run["resolved_question"]
                self._insert_file(db, file_id=input_file_id, owner=owner, session_id=run["session_id"],
                    origin_key=origin_key, kind="material", origin="user",
                    name=self._file_name("发现关联原始输入", run["question"], now), content=content,
                    metadata={"source_run_id": run_id, "source_report_id": report["id"], "source_id": None,
                              "content_kind": "user_input", "phase": "relation_report",
                              "retrieved_at": now, "published_at": None, "url": None,
                              "context_text": context_text}, created_at=now)
                report["input_file_id"] = input_file_id
                run["input_file_id"] = input_file_id
            self._insert_report_file(db, owner, run, report)
            report["memory_input"] = self._validate_memory_db(
                db, owner, run["session_id"], run.get("memory_output") or run.get("memory_input"))
            report["model_memory_trace"] = list(run.get("model_memory_trace") or [])
            report["memory_output"] = self._publish_memory_db(db, owner, run, report=report)
            if has_final_choice(report):
                run["next_step_state"] = "pending"
            run["status"] = status
            run["stage"] = "completed"
            run["updated_at"] = timestamp()
            run["stage_history"].append({"stage": "completed", "at": run["updated_at"]})
            self._save_run(db, owner, run)
            db.execute("INSERT INTO reports VALUES(?,?,?,?,?)",
                       (report["id"], owner, run["session_id"], run_id, encoded(report)))
            self._event(db, owner, run, "completed")
            db.execute("UPDATE sessions SET updated_at=? WHERE id=? AND owner=?",
                       (run["updated_at"], run["session_id"], owner))
            return True

    def recover(self):
        with self.transaction() as db:
            rows = db.execute("SELECT owner,doc FROM runs WHERE status IN ('queued','running')").fetchall()
            for row in rows:
                run = json.loads(row["doc"])
                run.update(status="interrupted", stage="interrupted", updated_at=timestamp(),
                           error="服务中断，本次分析没有自动重新执行。")
                run["stage_history"].append({"stage": "interrupted", "at": run["updated_at"]})
                self._save_run(db, row["owner"], run)
                self._event(db, row["owner"], run, "interrupted")
