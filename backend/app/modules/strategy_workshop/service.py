"""Owner-bound matching, confirmed rule rewriting and frozen backtest runs."""
from __future__ import annotations

import base64
import copy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import threading
import uuid

from strategy_workshop import catalog

from .contracts import TERMINAL, custom_runtime_digest, digest, fail, fields, idea, key, now, object_id, revision, sha256_value
from .repository import Repository
from .presentation import LANGUAGE_STYLE_VERSION, language_style, style_text
from .worker import WorkerManager


def identifier(prefix):
    return prefix + "_" + uuid.uuid4().hex


def safe_result(value):
    """Public reports expose provenance facts without disclosing host paths."""
    if isinstance(value, dict):
        return {k: safe_result(v) for k, v in value.items()}
    if isinstance(value, list):
        return [safe_result(v) for v in value]
    if isinstance(value, str) and (re.match(r"^[A-Za-z]:[\\/]", value) or value.startswith(("/Users/", "/home/", "/tmp/"))):
        return "[本地冻结数据]"
    return value


class WorkshopService:
    def __init__(self, workbench, config):
        from .market import MarketData
        from .matching import MatchingService
        from .rewriting import StrategyRewriteSkill
        self.workbench, self.config = workbench, config
        self.repository = Repository(config["DATA_DIR"])
        self.repository.recover()
        self.market = MarketData(config)
        self.matching = MatchingService(workbench, config)
        self.rewriting = StrategyRewriteSkill(workbench, config)
        timeout = min(180, max(.05, float(config.get("STRATEGY_WORKSHOP_RUN_TIMEOUT", 180))))
        self.max_runs = min(2, max(1, int(config.get("STRATEGY_WORKSHOP_MAX_RUNS", 2))))
        self.sandbox_config = {"AGENT_RUNTIME_DIR": config.get("AGENT_RUNTIME_DIR"),
                               "AGENT_EXECUTION_TIMEOUT": min(15, float(config.get("AGENT_EXECUTION_TIMEOUT", 15))),
                               "AGENT_MEMORY_MB": min(128, int(config.get("AGENT_MEMORY_MB", 128)))}
        self.worker = WorkerManager(self.repository, config["DATA_DIR"], timeout=timeout, sandbox_config=self.sandbox_config)
        self.lock = threading.RLock()
        self.closed = False

    def close(self):
        self.closed = True
        self.worker.close()
        if hasattr(self.matching, "close"):
            self.matching.close()
        self.rewriting.close()

    def _health(self):
        if self.closed:
            fail("策略工坊服务已停止。", 503, "service_closed")
        if len(catalog.STRATEGIES) != 10 or catalog.FALLBACK_ID not in catalog.STRATEGIES:
            fail("策略库不完整，暂时无法匹配和运行。", 503, "catalog_unavailable")
        for strategy_id, module in catalog.STRATEGIES.items():
            spec = module.SPEC
            if spec.key != strategy_id or not callable(module.build_targets) or not spec.version:
                fail("策略库完整性校验失败。", 503, "catalog_unavailable")
            spec.resolved_parameters()

    def strategies(self):
        self._health()
        from .catalog_content import public_catalog
        return {"strategies": public_catalog(), "catalogRevision": catalog.implementation_hash()}

    def capabilities(self):
        self._health()
        result = dict(self.market.capabilities())
        result.update(catalogHealthy=True, strategyCount=10, catalogRevision=catalog.implementation_hash(),
                      formalReportAvailable=False, diagnosticAvailable=True,
                      limits={"maxConcurrentRuns": self.max_runs, "executionTimeoutSeconds": self.worker.timeout},
                      phase=2, customizationAvailable=True, externalDataAvailable=False,
                      customExecution="wasi", supportedCustomizationData=["daily_ohlcv"])
        return safe_result(result)

    def symbols(self, query=""):
        return safe_result(self.market.symbols(query))

    def _candidate(self, text, style="beginner", owner=None):
        self._health()
        from .matching import MatchingService
        from .model_traces import TraceFactory
        trace = TraceFactory(self.repository, owner, "match") if owner is not None else None
        options = {"language_style": style} if style != "beginner" else {}
        if trace and getattr(self.matching.match, "__func__", None) is MatchingService.match:
            options["trace_factory"] = trace
        value = dict(self.matching.match(text, **options))
        if trace and trace.ids:
            value["modelTraceIds"] = trace.ids
        value.update(languageStyle=style, languageStyleVersion=LANGUAGE_STYLE_VERSION)
        strategy_id = value.get("strategyId")
        if strategy_id not in catalog.STRATEGIES:
            fail("匹配结果未通过固定策略白名单检查。", 503, "invalid_match")
        module = catalog.get_strategy(strategy_id)
        from .catalog_content import public_catalog
        content = next(item for item in public_catalog() if item["strategyId"] == strategy_id)
        raw = Path(module.__file__).read_bytes()
        spec = module.SPEC
        value.update(name=spec.name, version=spec.version, summary=spec.summary,
                     strategy={"strategyId": spec.key, **asdict(spec), **content},
                     parameters=dict(spec.parameters), sourceCode=raw.decode("utf-8"),
                     sourceHash=hashlib.sha256(raw).hexdigest(), catalogRevision=catalog.implementation_hash())
        value.update({k: content[k] for k in ("rules", "suitableFor", "limitations")})
        value["explanationDigest"] = digest(value.get("explanation", {}))
        value["selectionDigest"] = digest({k: value[k] for k in ("strategyId", "version", "sourceHash", "catalogRevision", "explanationDigest")})
        return value

    def _public_preflight(self, doc):
        return safe_result({k: v for k, v in doc.items() if k not in {"marketSnapshotId", "packageSnapshotId"}})

    def session(self, owner, session_id):
        doc = self.repository.read(owner, "session", session_id)
        if doc.get("preflightId"):
            doc["preflight"] = self._public_preflight(self.repository.read(owner, "preflight", doc["preflightId"]))
        return doc

    def current(self, owner):
        doc = self.repository.current(owner)
        return {"session": self.session(owner, doc["id"]) if doc else None}

    def create_session(self, owner, body):
        self._health()
        fields(body, {"idea", "sourceRunId", "idempotencyKey", "languageStyle"}, {"idea", "idempotencyKey"})
        key(body)
        style = language_style(body.get("languageStyle", "beginner"))
        text = idea(body)
        if body.get("sourceRunId") is not None:
            object_id(body["sourceRunId"], "sourceRunId")
        with self.lock:
            with self.repository.transaction() as db:
                cached = self.repository.cached(db, owner, "sessions", body)
                if cached is not None:
                    return cached
                if body.get("sourceRunId"):
                    self.repository.get(db, owner, "run", body["sourceRunId"])
            candidate = self._candidate(text, style, owner)
            stamp, session_id = now(), identifier("ws")
            doc = {"id": session_id, "sessionId": session_id, "idea": text, "status": "awaiting_selection",
                   "revision": 1, "matchRevision": 1, "selectionRevision": None, "configRevision": 0,
                   "candidate": candidate, "baseCandidate": candidate, "customization": None, "languageStyle": style,
                   "selectionApproval": None, "preflightId": None, "runId": None,
                   "sourceRunId": body.get("sourceRunId"), "createdAt": stamp, "updatedAt": stamp}
            # Bind the candidate to this concrete session revision.
            candidate["selectionDigest"] = digest({"sessionId": session_id, "matchRevision": 1, "candidateDigest": candidate["selectionDigest"]})
            with self.repository.transaction() as db:
                cached = self.repository.cached(db, owner, "sessions", body)
                if cached is not None:
                    return cached
                self.repository.insert(db, owner, "session", doc)
                self.repository.remember(db, owner, "sessions", body, doc)
            return doc

    def rematch(self, owner, session_id, body):
        self._health()
        fields(body, {"idea", "expectedRevision", "idempotencyKey", "languageStyle"}, {"idea", "expectedRevision", "idempotencyKey"})
        key(body)
        style = language_style(body.get("languageStyle", "beginner"))
        expected, text = revision(body["expectedRevision"], "expectedRevision"), idea(body)
        action = "rematch:" + session_id
        with self.lock:
            with self.repository.transaction() as db:
                doc = self.repository.get(db, owner, "session", session_id)
                self._allow_strategy_changes(doc)
                cached = self.repository.cached(db, owner, action, body)
                if cached is not None:
                    return cached
                if doc["matchRevision"] != expected:
                    fail("描述已经更新，请读取最新会话。", 409, "stale_revision")
            candidate = self._candidate(text, style, owner)
            with self.repository.transaction() as db:
                doc = self.repository.get(db, owner, "session", session_id)
                if doc["matchRevision"] != expected:
                    fail("描述已经更新，请读取最新会话。", 409, "stale_revision")
                next_revision = expected + 1
                candidate["selectionDigest"] = digest({"sessionId": session_id, "matchRevision": next_revision, "candidateDigest": candidate["selectionDigest"]})
                doc.update(idea=text, status="awaiting_selection", candidate=candidate, baseCandidate=candidate,
                           customization=None, revision=next_revision, languageStyle=style,
                           matchRevision=next_revision, selectionRevision=None, selectionApproval=None,
                           preflightId=None, runId=None)
                doc.pop("savedPersonalApp", None)
                self.repository.put(db, owner, "session", doc)
                self.repository.remember(db, owner, action, body, doc)
                return doc

    def selection(self, owner, session_id, body):
        self._health()
        fields(body, {"matchRevision", "selectionDigest", "decision", "idempotencyKey"},
               {"matchRevision", "selectionDigest", "decision", "idempotencyKey"})
        key(body)
        revision(body["matchRevision"], "matchRevision")
        sha256_value(body["selectionDigest"], "selectionDigest")
        if body["decision"] not in {"accept", "reject"}:
            fail("请选择接受策略或重新描述。", field="decision")
        with self.lock, self.repository.transaction() as db:
            doc = self.repository.get(db, owner, "session", session_id)
            self._allow_strategy_changes(doc)
            action = "selection:" + session_id
            cached = self.repository.cached(db, owner, action, body)
            if cached is not None:
                return cached
            candidate = doc.get("candidate")
            if (doc["status"] not in {"awaiting_selection", "awaiting_custom_selection"} or not candidate or doc["matchRevision"] != body["matchRevision"]
                    or candidate["selectionDigest"] != body["selectionDigest"]):
                fail("策略确认已过期，请确认最新候选。", 409, "stale_selection")
            if candidate["catalogRevision"] != catalog.implementation_hash():
                fail("策略代码已更新，请重新匹配并确认。", 409, "catalog_changed")
            accepted = body["decision"] == "accept"
            if accepted and candidate.get("strategyKind") == "custom":
                custom = doc.get("customization") or {}
                if custom.get("status") != "validated" or custom.get("id") != candidate.get("customizationId"):
                    fail("自定义策略尚未通过规则确认和代码验证。", 409, "custom_validation_required")
            doc.update(status="configuring" if accepted else "idea_editing", preflightId=None,
                       selectionRevision=doc["matchRevision"] if accepted else None,
                       selectionApproval={"decision": body["decision"], "matchRevision": doc["matchRevision"],
                                          "selectionDigest": body["selectionDigest"], "at": now()})
            if not accepted:
                doc["candidate"] = None
            if accepted:
                from .saved_rules import save_rules
                doc["savedPersonalApp"] = save_rules(self, db, owner, doc)
            self.repository.put(db, owner, "session", doc)
            self.repository.remember(db, owner, action, body, doc)
            return doc

    def _archive_package(self):
        root = Path(catalog.__file__).parent
        files, code_hash = {}, hashlib.sha256()
        for path in sorted(root.rglob("*.py")):
            relative = path.relative_to(root).as_posix()
            raw = path.read_bytes()
            code_hash.update(relative.encode())
            code_hash.update(raw)
            files[relative] = base64.b64encode(raw).decode("ascii")
        return {"files": files, "implementationHash": code_hash.hexdigest(), "pythonVersion": sys.version,
                "archiveVersion": "workshop-package-1"}

    @staticmethod
    def _allow_strategy_changes(session):
        if session.get("sourcePersonalAppId"):
            fail("此处复用已保存应用的策略。若要修改买卖规则，请另开策略工坊需求。", 409, "saved_strategy_locked")

    def _saved_strategy_package(self, db, owner, session):
        """Resolve immutable source evidence; never use the mutable source session."""
        app = self.repository.get(db, owner, "personal_app", session["sourcePersonalAppId"])
        if app.get("_draftDefinition"):
            from .saved_rules import check_definition
            check_definition(app)
            source = self.repository.get(db, owner, "run", session.get("sourceRunId"))
            if source.get("savedPersonalAppId") != app["id"] or source["candidate"]["sourceHash"] != app["sourceHash"]:
                fail("已保存策略的验证运行不一致。", 503, "snapshot_corrupt")
            app = {**app, "runId": source["id"], **self._personal_app_snapshot(db, owner, source)}
        if app["runId"] != session.get("sourceRunId"):
            fail("已保存策略的来源不一致。", 503, "snapshot_corrupt")
        run = self.repository.get(db, owner, "run", app["runId"])
        frozen = self._personal_app_snapshot(db, owner, run)
        if any(app.get(name) != value for name, value in frozen.items()):
            fail("已保存策略的冻结版本不一致。", 503, "snapshot_corrupt")
        row = db.execute("SELECT doc FROM workshop_snapshots WHERE id=? AND owner=? AND kind='execution'",
                         (run["executionSnapshotId"], owner)).fetchone()
        package = json.loads(row["doc"])["package"]
        # MarketData computes warmup using the current trusted implementation.
        # Reject upgrades here rather than silently changing the saved algorithm.
        if package["implementationHash"] != catalog.implementation_hash():
            fail("该应用保存的策略运行版本与当前版本不同，暂时不能重新回测；原策略和结果仍然保留。",
                 409, "saved_strategy_runtime_changed")
        try:
            actual_hash = hashlib.sha256()
            for relative, encoded in sorted(package["files"].items()):
                if (not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts
                        or not relative.endswith(".py")):
                    raise ValueError("Invalid archive path")
                raw = base64.b64decode(encoded, validate=True)
                actual_hash.update(relative.encode())
                actual_hash.update(raw)
            if actual_hash.hexdigest() != package["implementationHash"]:
                raise ValueError("Archived package mismatch")
            custom = package.get("custom")
            if custom:
                from .custom_rules import compile_strategy, plan_hash
                if custom["runtimeHash"] != custom_runtime_digest():
                    fail("该应用保存的自定义策略编译版本与当前版本不同，暂时不能重新回测；原策略和结果仍然保留。",
                         409, "saved_strategy_runtime_changed")
                if (compile_strategy(custom["rulePlan"]) != custom["sourceCode"]
                        or plan_hash(custom["rulePlan"]) != custom["planHash"]
                        or not custom["validation"].get("passed")
                        or custom["validation"].get("sourceHash") != custom["sourceHash"]
                        or custom["strategy"] != run["candidate"]["strategy"]):
                    raise ValueError("Custom evidence mismatch")
            source_candidate = {name: value for name, value in run["candidate"].items() if name != "selectionDigest"}
            current_candidate = {name: value for name, value in session["candidate"].items() if name != "selectionDigest"}
            if source_candidate != current_candidate:
                raise ValueError("Saved candidate mismatch")
        except (KeyError, TypeError, ValueError):
            fail("已保存策略代码或校验证据不完整，暂时不能重新回测。", 503, "snapshot_corrupt")
        return package

    def _custom_context(self, db, owner, session_id, body):
        doc = self.repository.get(db, owner, "session", session_id)
        self._allow_strategy_changes(doc)
        candidate = doc.get("candidate")
        if (not candidate or doc["matchRevision"] != body["matchRevision"]
                or candidate["selectionDigest"] != body["selectionDigest"]):
            fail("策略版本已更新，请读取最新会话。", 409, "stale_selection")
        if candidate["catalogRevision"] != catalog.implementation_hash():
            fail("策略库已更新，请重新匹配。", 409, "catalog_changed")
        return doc

    def customization(self, owner, session_id, body):
        self._health()
        fields(body, {"matchRevision", "selectionDigest", "instructions", "idempotencyKey", "languageStyle"},
               {"matchRevision", "selectionDigest", "idempotencyKey"})
        key(body)
        style = language_style(body.get("languageStyle", "beginner"))
        revision(body["matchRevision"], "matchRevision")
        sha256_value(body["selectionDigest"], "selectionDigest")
        instructions = body.get("instructions", "")
        if not isinstance(instructions, str) or len(instructions) > 4000:
            fail("补充要求须为不超过4000字的文本。", field="instructions")
        answer = instructions.strip()
        from .custom_rules import compile_strategy, describe_plan, normalize_plan, plan_hash
        action = "customization:" + session_id
        with self.lock:
            with self.repository.transaction() as db:
                cached = self.repository.cached(db, owner, action, body)
                if cached is not None:
                    return cached
                doc = self._custom_context(db, owner, session_id, body)
                base = doc.get("baseCandidate") or doc["candidate"]
                previous = doc.get("customization") or {}
                if previous.get("status") == "alignment_incomplete":
                    fail("本次需求对齐已结束。请重新描述想法，或返回现有策略。", 409, "alignment_complete")
                final_pass = previous.get("status") == "needs_clarification" or previous.get("alignmentCompleted") is True
                if final_pass and not answer:
                    fail("请先回答已列出的策略条件，或重新描述想法。", 400, "alignment_answer_required", field="instructions")
                history = list(previous.get("clarifications", []))
                if answer:
                    history.append(answer)
                if len(history) > 12 or len("\n\n".join(history)) > 8000:
                    fail("补充要求过长，请重新整理策略描述后匹配。", 422, "clarification_limit")
            args = (doc["idea"], base, "\n\n".join(history))
            options = {} if style == "beginner" else {"language_style": style}
            if final_pass:
                options["alignment_context"] = {
                    "finalPass": True, "previousStatus": previous.get("status"),
                    "previousQuestions": (previous.get("questions") or previous.get("alignmentQuestions") or [])
                        if previous.get("status") == "needs_clarification" else [],
                    "previousSummary": previous.get("summary") or "",
                    "previousAssumptions": previous.get("assumptions") or [],
                    "latestAnswer": answer,
                }
            from .rewriting import StrategyRewriteSkill
            from .model_traces import TraceFactory
            trace = TraceFactory(self.repository, owner, "rewrite")
            if getattr(self.rewriting.prepare, "__func__", None) is StrategyRewriteSkill.prepare:
                options["trace_factory"] = trace
            result = self.rewriting.prepare(*args, **options)
            if final_pass:
                if result["status"] == "unsupported_data":
                    result = {**result, "questions": []}
                elif result["status"] == "needs_clarification" and result.get("dataGaps"):
                    result = {**result, "status": "unsupported_data", "questions": []}
                elif result["status"] == "needs_clarification" or result.get("questions"):
                    # Even a bypassed/mock rewrite implementation cannot return
                    # unresolved questions as a confirmable rule or start a loop.
                    result = {**result, "status": "alignment_incomplete", "plan": None,
                              "questions": [], "dataGaps": [],
                              "alignmentIssues": result.get("questions") or ["仍缺少可实现的买入或退出条件。"]}
            plan = normalize_plan(result["plan"]) if result["status"] == "rules_ready" else None
            source = compile_strategy(plan) if plan else None
            stamp = now()
            alignment_questions = (previous.get("alignmentQuestions") or previous.get("questions") or []) if final_pass else []
            if result["status"] == "needs_clarification":
                alignment_questions = result["questions"]
            custom = {"id": identifier("custom"), "sessionId": session_id,
                      "matchRevision": doc["matchRevision"], "status": result["status"],
                      "summary": result["summary"], "rulePlan": plan,
                      "languageStyle": style, "languageStyleVersion": LANGUAGE_STYLE_VERSION,
                      "ruleSummary": describe_plan(plan) if plan else None,
                      "assumptions": result["assumptions"], "questions": result["questions"],
                      "dataGaps": result["dataGaps"], "clarifications": history,
                      "alignmentCompleted": final_pass, "alignmentQuestions": alignment_questions,
                      "alignmentIssues": result.get("alignmentIssues", []),
                      "rulesDigest": None, "planHash": plan_hash(plan) if plan else None,
                      "sourceCode": source, "sourceHash": hashlib.sha256(source.encode()).hexdigest() if source else None,
                      "validation": None, "attempts": result.get("repairFeedback", []),
                      "generationAttempts": result.get("generationAttempts", 1),
                      "modelTraceIds": trace.ids,
                      "skillVersion": result.get("skillVersion", "strategy-rewrite-1.1.0"),
                      "runtimeHash": custom_runtime_digest(),
                      "createdAt": stamp, "updatedAt": stamp}
            if custom["status"] == "unsupported_data":
                from .rewriting import EXTERNAL_DATA_MESSAGE
                custom.update(externalDataMessage=EXTERNAL_DATA_MESSAGE,
                              uploadAvailabilityMessage="当前版本尚未开放外源数据上传入口。")
            if plan:
                custom["rulesDigest"] = digest({"customizationId": custom["id"], "matchRevision": doc["matchRevision"],
                    "plan": plan, "summary": custom["summary"], "assumptions": custom["assumptions"], "sourceHash": custom["sourceHash"]})
            with self.repository.transaction() as db:
                doc = self._custom_context(db, owner, session_id, body)
                doc.update(baseCandidate=base, candidate=base, customization=custom,
                           status="customizing", selectionRevision=None, selectionApproval=None, languageStyle=style,
                           preflightId=None, runId=None)
                doc.pop("savedPersonalApp", None)
                self.repository.insert(db, owner, "customization", custom)
                if custom["status"] == "rules_ready":
                    from .saved_rules import save_rules
                    doc["savedPersonalApp"] = save_rules(self, db, owner, doc)
                self.repository.put(db, owner, "session", doc)
                self.repository.remember(db, owner, action, body, doc)
                return doc

    def customization_confirm(self, owner, session_id, body):
        self._health()
        fields(body, {"customizationId", "rulesDigest", "idempotencyKey"},
               {"customizationId", "rulesDigest", "idempotencyKey"})
        key(body)
        object_id(body["customizationId"], "customizationId")
        sha256_value(body["rulesDigest"], "rulesDigest")
        from .custom_rules import compile_strategy, plan_warmup
        from .custom_sandbox import validate_strategy
        from ..agent_platform.executor import SandboxExecutor
        action = "customization-confirm:" + session_id
        with self.lock:
            with self.repository.transaction() as db:
                cached = self.repository.cached(db, owner, action, body)
                if cached is not None:
                    return cached
                doc = self.repository.get(db, owner, "session", session_id)
                self._allow_strategy_changes(doc)
                custom = doc.get("customization")
                if (not custom or custom["id"] != body["customizationId"] or custom["status"] != "rules_ready"
                        or custom["rulesDigest"] != body["rulesDigest"] or custom["matchRevision"] != doc["matchRevision"]):
                    fail("规则确认已过期，请确认最新自定义版本。", 409, "stale_customization")
                if doc["candidate"]["catalogRevision"] != catalog.implementation_hash():
                    fail("策略库已更新，请重新匹配。", 409, "catalog_changed")
                from .saved_rules import save_rules
                doc["savedPersonalApp"] = save_rules(self, db, owner, doc)
                self.repository.put(db, owner, "session", doc)
            if custom["runtimeHash"] != custom_runtime_digest() or compile_strategy(custom["rulePlan"]) != custom["sourceCode"]:
                fail("自定义源码与规则不一致，请重新生成。", 503, "custom_source_mismatch")
            try:
                evidence = validate_strategy(custom["rulePlan"], executor=SandboxExecutor(self.sandbox_config))
                if (not isinstance(evidence, dict) or evidence.get("passed") is not True
                        or evidence.get("sourceHash") != custom["sourceHash"]
                        or evidence.get("ruleHash") != custom["planHash"]):
                    raise ValueError("验证证据未通过或与确认的规则/源码摘要不一致")
                custom.update(status="validated", validation=evidence,
                              ruleApproval={"rulesDigest": body["rulesDigest"], "at": now()})
            except Exception as error:
                custom.update(status="validation_failed", validation={"passed": False,
                    "message": ("代码验证未通过：" + str(error)[:500] + "。请修改规则后重新生成。") if isinstance(error, ValueError)
                        else "隔离代码验证失败或运行时不可用，当前版本不能回测；请检查运行环境后重新生成。",
                    "code": getattr(error, "code", "rule_validation_failed"),
                    "errorType": type(error).__name__})
            with self.repository.transaction() as db:
                doc = self.repository.get(db, owner, "session", session_id)
                if not doc.get("customization") or doc["customization"]["id"] != custom["id"]:
                    fail("规则版本已更新，请读取最新会话。", 409, "stale_customization")
                doc["customization"] = custom
                if custom["status"] == "validated":
                    base = doc.get("baseCandidate") or doc["candidate"]
                    candidate = dict(base)
                    strategy_id = "custom_" + custom["planHash"][:16]
                    candidate.update(strategyId=strategy_id, name=custom["rulePlan"]["name"], version="2.0.0",
                        summary=custom["summary"], parameters={}, sourceCode=custom["sourceCode"],
                        languageStyle=custom.get("languageStyle", "beginner"), languageStyleVersion=custom.get("languageStyleVersion", LANGUAGE_STYLE_VERSION),
                        sourceHash=custom["sourceHash"], strategyKind="custom", customizationId=custom["id"],
                        rulesDigest=custom["rulesDigest"], ruleSummary=custom["ruleSummary"],
                        baseStrategyId=base["strategyId"],
                        rules=["买入：" + custom["ruleSummary"]["entry"], "退出：" + custom["ruleSummary"]["exit"],
                               "退出优先；未触发信号时保持上一目标。"],
                        strategy={"strategyId": strategy_id, "key": strategy_id, "name": custom["rulePlan"]["name"],
                            "summary": custom["summary"], "version": "2.0.0", "parameters": {},
                            "warmup_bars": plan_warmup(custom["rulePlan"]), "sources": []})
                    candidate["selectionDigest"] = digest({"sessionId": session_id, "matchRevision": doc["matchRevision"],
                        "customizationId": custom["id"], "rulesDigest": custom["rulesDigest"], "sourceHash": custom["sourceHash"],
                        "validationDigest": digest(custom["validation"])})
                    doc.update(candidate=candidate, status="awaiting_custom_selection")
                self.repository.put(db, owner, "customization", custom)
                self.repository.put(db, owner, "session", doc)
                self.repository.remember(db, owner, action, body, doc)
                return doc

    def customization_reset(self, owner, session_id, body):
        fields(body, {"matchRevision", "selectionDigest", "idempotencyKey"},
               {"matchRevision", "selectionDigest", "idempotencyKey"})
        key(body)
        revision(body["matchRevision"], "matchRevision")
        sha256_value(body["selectionDigest"], "selectionDigest")
        with self.lock, self.repository.transaction() as db:
            action = "customization-reset:" + session_id
            cached = self.repository.cached(db, owner, action, body)
            if cached is not None:
                return cached
            doc = self._custom_context(db, owner, session_id, body)
            doc.update(candidate=doc.get("baseCandidate") or doc["candidate"], customization=None,
                       status="awaiting_selection", selectionRevision=None, selectionApproval=None,
                       preflightId=None, runId=None)
            doc.pop("savedPersonalApp", None)
            self.repository.put(db, owner, "session", doc)
            self.repository.remember(db, owner, action, body, doc)
            return doc

    def preflight(self, owner, session_id, body):
        self._health()
        fields(body, {"selectionRevision", "symbol", "startDate", "endDate", "initialCash", "idempotencyKey"},
               {"selectionRevision", "symbol", "startDate", "endDate", "initialCash", "idempotencyKey"})
        key(body)
        revision(body["selectionRevision"], "selectionRevision")
        from datetime import date
        from decimal import Decimal, InvalidOperation
        from math import isfinite
        if not isinstance(body["symbol"], str) or not re.fullmatch(r"\d{6}(?:\.(?:SH|SZ))?", body["symbol"].strip().upper()):
            fail("请输入六位股票或 ETF 代码，可附 .SH 或 .SZ。", field="symbol")
        for name in ("startDate", "endDate"):
            if not isinstance(body[name], str):
                fail("日期须为 YYYY-MM-DD 或空值采用默认日期。", field=name)
            if body[name]:
                try:
                    if len(body[name]) != 10:
                        raise ValueError("Invalid date length")
                    date.fromisoformat(body[name])
                except ValueError:
                    fail("日期格式不正确。", field=name)
        cash = body["initialCash"]
        if isinstance(cash, bool) or not isinstance(cash, (int, float)) or not isfinite(cash) or not 0 < cash <= 1000000000:
            fail("初始资金须大于零且不超过十亿元。", field="initialCash")
        try:
            decimal_cash = Decimal(str(cash))
            if decimal_cash != decimal_cash.quantize(Decimal("0.01")):
                fail("初始资金最多保留两位小数。", field="initialCash")
        except InvalidOperation:
            fail("初始资金不正确。", field="initialCash")
        with self.lock:
            with self.repository.transaction() as db:
                doc = self.repository.get(db, owner, "session", session_id)
                action = "preflight:" + session_id
                cached = self.repository.cached(db, owner, action, body)
                if cached is not None:
                    return cached
                if not doc.get("selectionApproval") or doc["selectionApproval"]["decision"] != "accept" or doc["selectionRevision"] != body["selectionRevision"]:
                    fail("请先确认当前匹配策略。", 409, "selection_required")
                candidate = doc["candidate"]
                saved_package = self._saved_strategy_package(db, owner, doc) if doc.get("sourcePersonalAppId") else None
                if saved_package is None and candidate["catalogRevision"] != catalog.implementation_hash():
                    fail("策略代码已更新，请重新匹配并确认。", 409, "catalog_changed")
            custom = None
            if candidate.get("strategyKind") == "custom":
                custom = saved_package.get("custom") if saved_package is not None else doc.get("customization")
            if custom:
                if (saved_package is None and custom["status"] != "validated") or custom["id"] != candidate["customizationId"]:
                    fail("自定义策略尚未通过验证。", 409, "custom_validation_required")
                if custom["runtimeHash"] != custom_runtime_digest():
                    fail("策略编译器已更新，请重新生成并验证自定义策略。", 409, "custom_runtime_changed")
                prepared = dict(self.market.prepare(body["symbol"], body["startDate"], body["endDate"],
                    candidate["strategyId"], body["initialCash"], custom_plan=custom["rulePlan"]))
            else:
                prepared = dict(self.market.prepare(body["symbol"], body["startDate"], body["endDate"], candidate["strategyId"], body["initialCash"]))
            snapshot = prepared.pop("snapshot", None)
            if not isinstance(snapshot, dict) or not all(k in snapshot for k in ("bars", "actions", "config", "instrument", "provenance")):
                fail("行情适配未返回完整冻结输入。", 503, "invalid_market_snapshot")
            package = saved_package if saved_package is not None else self._archive_package()
            if custom and saved_package is None:
                package["custom"] = {key: custom[key] for key in ("id", "rulePlan", "rulesDigest", "planHash", "sourceCode", "sourceHash",
                    "ruleApproval", "validation", "skillVersion", "generationAttempts", "assumptions", "summary", "runtimeHash", "clarifications")}
                package["custom"]["strategy"] = candidate["strategy"]
                base = doc["baseCandidate"]
                package["custom"]["originalIdea"] = doc["idea"]
                package["custom"]["baseStrategy"] = {key: base[key] for key in
                    ("strategyId", "name", "version", "sourceHash", "summary", "parameters", "rules", "explanation")}
            if package["implementationHash"] != candidate["catalogRevision"]:
                fail("预检查期间策略包已变化，请重新匹配。", 409, "catalog_changed")
            # The current release has no approved independent formal validator.
            gate = dict(prepared.get("gate") or {})
            gate.update(eligible=False, formalValidatorApproved=False)
            prepared["gate"] = gate
            coverage = dict(prepared.get("coverage") or {"required": False, "reasons": []})
            coverage["digest"] = digest({"requested": prepared.get("requested"), "actual": prepared.get("actual"), "coverage": coverage})
            stamp, preflight_id = now(), identifier("pf")
            frozen = {**prepared, "id": preflight_id, "preflightId": preflight_id, "sessionId": session_id,
                      "selectionRevision": body["selectionRevision"], "configRevision": doc["configRevision"] + 1,
                      "coverage": coverage, "requiresCoverageConfirmation": bool(coverage.get("required")),
                      "marketSnapshotId": identifier("market"), "packageSnapshotId": identifier("code"),
                      "dataSnapshotDigest": digest(snapshot), "implementationHash": package["implementationHash"],
                      "createdAt": stamp, "updatedAt": stamp}
            frozen["configDigest"] = digest({"sessionId": session_id, "selectionDigest": candidate["selectionDigest"],
                                             "preflight": frozen})
            public = self._public_preflight(frozen)
            with self.repository.transaction() as db:
                doc = self.repository.get(db, owner, "session", session_id)
                self.repository.snapshot(db, owner, frozen["marketSnapshotId"], "market", snapshot)
                self.repository.snapshot(db, owner, frozen["packageSnapshotId"], "package", package)
                self.repository.insert(db, owner, "preflight", frozen)
                doc.update(preflightId=preflight_id, configRevision=frozen["configRevision"], status="awaiting_coverage_confirmation" if coverage.get("required") else "ready_to_run")
                self.repository.put(db, owner, "session", doc)
                self.repository.remember(db, owner, action, body, public)
            return public

    def create_run(self, owner, body):
        self._health()
        fields(body, {"sessionId", "preflightId", "configDigest", "coverageDecision", "mode", "diagnosticAcknowledged", "idempotencyKey"},
               {"sessionId", "preflightId", "configDigest", "idempotencyKey"})
        key(body)
        object_id(body["sessionId"], "sessionId")
        object_id(body["preflightId"], "preflightId")
        sha256_value(body["configDigest"], "configDigest")
        mode = body.get("mode", "formal")
        if mode not in {"formal", "diagnostic"}:
            fail("运行模式不正确。", field="mode")
        with self.lock:
            with self.repository.transaction() as db:
                cached = self.repository.cached(db, owner, "runs", body)
                if cached is not None:
                    return cached
                session = self.repository.get(db, owner, "session", body["sessionId"])
                frozen = self.repository.get(db, owner, "preflight", body["preflightId"])
                if (session.get("preflightId") != frozen["id"] or frozen["sessionId"] != session["id"]
                        or body["configDigest"] != frozen["configDigest"]
                        or session["selectionRevision"] != frozen["selectionRevision"]):
                    fail("配置确认已过期，请重新进行预检查。", 409, "stale_config")
                coverage = frozen["coverage"]
                if coverage.get("required"):
                    decision = body.get("coverageDecision")
                    if not isinstance(decision, dict) or decision != {"decision": "accept_actual_interval", "coverageDigest": coverage["digest"]}:
                        fail("请明确接受展示的实际回测区间。", 409, "coverage_confirmation_required")
                if mode == "formal":
                    fail("完整收益报告尚未通过独立数据验证门槛；可明确确认后运行工程诊断。", 422, "formal_data_gate", stage="preflight")
                if body.get("diagnosticAcknowledged") is not True:
                    fail("请明确确认当前结果只用于工程诊断。", 422, "diagnostic_acknowledgement_required")
                if self.repository.active_count(db) >= self.max_runs:
                    fail("整机回测并发名额已满，请稍后再试。", 429, "concurrency_limit", retryable=True)
                free = shutil.disk_usage(Path(self.config["DATA_DIR"])).free
                if free < 20 * 1024 * 1024:
                    fail("磁盘空间不足，现有历史保留，请释放空间后重试。", 503, "insufficient_storage")
                stamp, run_id = now(), identifier("run")
                strategy = {"strategyId": session["candidate"]["strategyId"], "name": session["candidate"]["name"], "version": session["candidate"]["version"], "parameters": session["candidate"]["parameters"]}
                run = {"id": run_id, "runId": run_id, "sessionId": session["id"], "preflightId": frozen["id"],
                       "sourceRunId": session.get("sourceRunId"), "status": "queued", "mode": mode,
                       "diagnosticAcknowledged": True, "idea": session["idea"], "candidate": session["candidate"],
                       "languageStyle": session.get("languageStyle", "beginner"),
                       "strategy": strategy, "requested": frozen.get("requested"), "actual": frozen.get("actual"),
                       "instrument": frozen.get("instrument"), "asof": frozen.get("asof"), "gate": frozen["gate"],
                       "configDigest": frozen["configDigest"], "coverageApproval": body.get("coverageDecision"),
                       "selectionApproval": session["selectionApproval"], "executionSnapshotId": identifier("execution"),
                       "conversationSnapshotId": identifier("conversation"),
                       "createdAt": stamp, "updatedAt": stamp, "startedAt": None, "finishedAt": None,
                       "result": None, "error": None}
                if session.get("sourcePersonalAppId"):
                    run["sourcePersonalAppId"] = session["sourcePersonalAppId"]
                if session.get("savedPersonalApp"):
                    run["savedPersonalAppId"] = session["savedPersonalApp"]["id"]
                # Read under the same RLock transaction; immutable snapshots are owner-bound.
                market_row = db.execute("SELECT doc,sha256 FROM workshop_snapshots WHERE id=? AND owner=?", (frozen["marketSnapshotId"], owner)).fetchone()
                code_row = db.execute("SELECT doc,sha256 FROM workshop_snapshots WHERE id=? AND owner=?", (frozen["packageSnapshotId"], owner)).fetchone()
                if not market_row or not code_row:
                    fail("预检查快照缺失，请重新预检查。", 503, "snapshot_missing")
                archived = {"market": json.loads(market_row["doc"]), "package": json.loads(code_row["doc"])}
                if (digest(archived["market"]) != market_row["sha256"] or digest(archived["package"]) != code_row["sha256"]
                        or digest(archived["market"]) != frozen["dataSnapshotDigest"]
                        or archived["package"].get("implementationHash") != frozen["implementationHash"]):
                    fail("冻结快照完整性检查失败，请重新预检查。", 503, "snapshot_corrupt")
                if archived["package"].get("custom"):
                    frozen_custom = archived["package"]["custom"]
                    run["customizationProvenance"] = {key: frozen_custom.get(key) for key in
                        ("originalIdea", "clarifications", "baseStrategy")}
                self.repository.snapshot(db, owner, run["executionSnapshotId"], "execution", archived)
                self.repository.insert(db, owner, "run", run)
                self.repository.event(db, owner, run_id, "run.queued", {"status": "queued"})
                session["runId"] = run_id
                self.repository.put(db, owner, "session", session)
                response = self._public_run(run)
                self.repository.remember(db, owner, "runs", body, response)
                from .conversation import transcript
                self.repository.snapshot(db, owner, run["conversationSnapshotId"], "conversation", transcript(db, owner, session))
            self.worker.start(owner, run)
            return response

    def _public_run(self, doc, include_result=True):
        result = {k: v for k, v in doc.items() if k not in {"executionSnapshotId", "conversationSnapshotId"}}
        if include_result:
            result["result"] = safe_result(doc.get("result"))
            result["report"] = result["result"]
        else:
            result.pop("result", None)
            candidate = dict(result.get("candidate") or {})
            candidate.pop("sourceCode", None)
            result["candidate"] = candidate
        return result

    def run(self, owner, run_id):
        with self.repository.transaction() as db:
            result = self._public_run(self.repository.get(db, owner, "run", run_id))
            app = self.repository.personal_app_for_run(db, owner, run_id)
            if result.get("savedPersonalAppId"):
                from .saved_rules import public_rules
                app = self.repository.get(db, owner, "personal_app", result["savedPersonalAppId"])
                result["personalApp"] = public_rules(self, db, owner, app)
                return result
            result["personalApp"] = self._public_personal_app(app) if app else None
            return result

    @staticmethod
    def _public_personal_app(app):
        return safe_result({k: v for k, v in app.items() if k not in {"executionSnapshotId"} and not k.startswith("_")})

    def save_session_app(self, owner, session_id, body):
        fields(body, {"idempotencyKey"}, {"idempotencyKey"})
        key(body)
        from .saved_rules import save_rules
        with self.lock, self.repository.transaction() as db:
            doc = self.repository.get(db, owner, "session", session_id)
            action = "save-rules:" + session_id
            cached = self.repository.cached(db, owner, action, body)
            if cached is not None:
                return cached
            app = save_rules(self, db, owner, doc)
            doc["savedPersonalApp"] = app
            self.repository.put(db, owner, "session", doc)
            result = {"app": app}
            self.repository.remember(db, owner, action, body, result)
            return result

    def _personal_app_snapshot(self, db, owner, run):
        if run["status"] != "succeeded" or not isinstance(run.get("result"), dict) or not run["result"]:
            fail("回测完成并得到结果后，才能添加到我的个人应用。", 409, "personal_app_result_required")
        row = db.execute("SELECT doc,sha256 FROM workshop_snapshots WHERE id=? AND owner=? AND kind='execution'",
                         (run.get("executionSnapshotId"), owner)).fetchone()
        if not row:
            fail("策略快照缺失，暂时无法保存或打开此应用。", 503, "snapshot_missing")
        archived = json.loads(row["doc"])
        snapshot_digest = digest(archived)
        if snapshot_digest != row["sha256"]:
            fail("冻结快照完整性检查失败。", 503, "snapshot_corrupt")
        try:
            package = archived["package"]
            custom = package.get("custom")
            if custom:
                raw = custom["sourceCode"].encode("utf-8")
                source_hash = hashlib.sha256(raw).hexdigest()
                if custom["sourceHash"] != source_hash:
                    raise ValueError("custom source hash mismatch")
            else:
                relative = "strategies/" + run["strategy"]["strategyId"] + ".py"
                raw = base64.b64decode(package["files"][relative], validate=True)
                source_hash = hashlib.sha256(raw).hexdigest()
            if run["candidate"]["sourceHash"] != source_hash:
                raise ValueError("candidate source hash mismatch")
            return {"executionSnapshotId": run["executionSnapshotId"], "executionSnapshotDigest": snapshot_digest,
                    "sourceHash": source_hash, "implementationHash": package["implementationHash"],
                    "strategyKind": "custom" if custom else "catalog",
                    "customizationId": custom["id"] if custom else None}
        except (KeyError, TypeError, ValueError):
            fail("冻结策略代码完整性检查失败。", 503, "snapshot_corrupt")

    def save_personal_app(self, owner, run_id, body):
        """Pin a completed strategy and its frozen inputs; never generate or execute."""
        object_id(run_id, "runId")
        fields(body, {"idempotencyKey"}, {"idempotencyKey"})
        key(body)
        with self.lock, self.repository.transaction() as db:
            run = self.repository.get(db, owner, "run", run_id)
            action = "personal-app:" + run_id
            frozen = self._personal_app_snapshot(db, owner, run)
            cached = self.repository.cached(db, owner, action, body)
            if cached is not None:
                return cached
            if run.get("savedPersonalAppId"):
                from .saved_rules import public_rules
                saved = self.repository.get(db, owner, "personal_app", run["savedPersonalAppId"])
                response = {"app": public_rules(self, db, owner, saved)}
                self.repository.remember(db, owner, action, body, response)
                return response
            app = self.repository.personal_app_for_run(db, owner, run_id)
            if app is None:
                stamp = now()
                symbol = (run.get("requested") or {}).get("symbol") or (run.get("instrument") or {}).get("symbol", "")
                strategy = run["strategy"]
                app = {"id": self.repository.personal_app_id(run_id), "kind": "strategy", "personal": True,
                       "name": strategy["name"] + (" · " + symbol if symbol else ""),
                       "description": run.get("idea") or run["candidate"].get("summary", ""),
                       "runId": run_id, "sessionId": run["sessionId"], "symbol": symbol,
                       "strategy": strategy, "requested": run.get("requested"), "actual": run.get("actual"),
                       "configDigest": run["configDigest"], "mode": run["mode"],
                       "createdAt": stamp, "updatedAt": stamp, **frozen}
                self.repository.insert(db, owner, "personal_app", app)
            elif any(app.get(k) != v for k, v in frozen.items()):
                fail("已保存策略的冻结版本不一致。", 503, "snapshot_corrupt")
            response = {"app": self._public_personal_app(app)}
            self.repository.remember(db, owner, action, body, response)
            return response

    def list_personal_apps(self, owner):
        from .saved_rules import public_rules
        with self.repository.transaction() as db:
            rows = db.execute("SELECT doc FROM workshop_objects WHERE owner=? AND kind='personal_app' ORDER BY updated DESC,id DESC", (owner,))
            apps = [json.loads(row["doc"]) for row in rows]
            return [public_rules(self, db, owner, app) if app.get("_draftDefinition") else self._public_personal_app(app) for app in apps]

    def personal_app(self, owner, app_id):
        object_id(app_id, "appId")
        with self.repository.transaction() as db:
            app = self.repository.get(db, owner, "personal_app", app_id)
            if app.get("_draftDefinition"):
                from .saved_rules import check_definition, public_rules
                public_app = public_rules(self, db, owner, app)
                run = self.repository.get(db, owner, "run", public_app["runId"]) if public_app.get("runId") else None
                if run and run["status"] == "succeeded":
                    self._personal_app_snapshot(db, owner, run)
                return {"app": public_app, "run": self._public_run(run) if run else None,
                        "definition": safe_result(check_definition(app))}
            run = self.repository.get(db, owner, "run", app["runId"])
            frozen = self._personal_app_snapshot(db, owner, run)
            if any(app.get(k) != v for k, v in frozen.items()):
                fail("已保存策略的冻结版本不一致。", 503, "snapshot_corrupt")
            public_app = self._public_personal_app(app)
            return {"app": public_app, "run": {**self._public_run(run), "personalApp": public_app}}

    def personal_app_session(self, owner, app_id, body):
        """Start a fresh configuration using this application's exact saved rules."""
        self._health()
        object_id(app_id, "appId")
        fields(body, {"idempotencyKey", "languageStyle", "mode"}, {"idempotencyKey"})
        key(body)
        if body.get("mode", "verify") not in {"verify", "edit"}:
            fail("请选择验证或修改策略。", field="mode")
        if "languageStyle" in body:
            language_style(body["languageStyle"])
        action = "personal-app-session:" + app_id
        with self.lock, self.repository.transaction() as db:
            app = self.repository.get(db, owner, "personal_app", app_id)
            cached = self.repository.cached(db, owner, action, body)
            if cached is not None:
                return cached
            if app.get("_draftDefinition"):
                from .saved_rules import open_rules, public_rules
                public_app = public_rules(self, db, owner, app)
                if public_app["verification"]["status"] != "passed" or body.get("mode") == "edit":
                    doc = open_rules(self, db, owner, app, body)
                    self.repository.remember(db, owner, action, body, doc)
                    return doc
                app = public_app
            run = self.repository.get(db, owner, "run", app["runId"])
            candidate = copy.deepcopy(run["candidate"])
            stamp, session_id = now(), identifier("ws")
            candidate["selectionDigest"] = digest({"sessionId": session_id, "matchRevision": 1,
                "sourcePersonalAppId": app_id, "sourceRunId": run["id"], "sourceHash": candidate["sourceHash"],
                "candidateDigest": digest(run["candidate"])})
            style = language_style(body.get("languageStyle", run.get("languageStyle", "beginner")))
            defaults = {name: (run.get("requested") or {}).get(name) for name in
                        ("symbol", "startDate", "endDate", "initialCash")}
            doc = {"id": session_id, "sessionId": session_id, "idea": run["idea"], "status": "configuring",
                   "revision": 1, "matchRevision": 1, "selectionRevision": 1, "configRevision": 0,
                   "candidate": candidate, "baseCandidate": copy.deepcopy(candidate), "customization": None,
                   "languageStyle": style, "sourcePersonalAppId": app_id, "sourceRunId": run["id"],
                   "rerunDefaults": defaults, "preflightId": None, "runId": None,
                   "selectionApproval": {"decision": "accept", "matchRevision": 1,
                       "selectionDigest": candidate["selectionDigest"], "sourcePersonalAppId": app_id,
                       "sourceRunId": run["id"], "at": stamp},
                   "createdAt": stamp, "updatedAt": stamp}
            package = self._saved_strategy_package(db, owner, doc)
            if package.get("custom"):
                doc["customization"] = {**copy.deepcopy(package["custom"]), "status": "validated",
                    "ruleSummary": candidate.get("ruleSummary"), "languageStyle": style}
            self.repository.insert(db, owner, "session", doc)
            self.repository.remember(db, owner, action, body, doc)
            return doc

    def history(self, owner, cursor=None):
        docs, next_cursor = self.repository.history(owner, cursor)
        values = [self._public_run(doc, False) for doc in docs]
        return {"runs": values, "items": values, "nextCursor": next_cursor}

    def sessions(self, owner, cursor=None, limit=20):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            fail("历史记录条数须为1至100。", field="limit")
        docs, next_cursor = self.repository.history(owner, cursor, limit, kind="session")
        values = []
        with self.repository.transaction() as db:
            runs = [json.loads(row["doc"]) for row in db.execute("SELECT doc FROM workshop_objects WHERE owner=? AND kind='run' ORDER BY updated DESC,id DESC", (owner,))]
            for doc in docs:
                related = [run for run in runs if run["sessionId"] == doc["id"]]
                current_run = next((run for run in related if run["id"] == doc.get("runId")), None)
                status = current_run["status"] if current_run else (doc.get("customization") or {}).get("status", doc["status"])
                values.append({"id": doc["id"], "sessionId": doc["id"], "idea": doc["idea"], "status": status,
                    "sessionStatus": doc["status"], "strategyName": (doc.get("candidate") or doc.get("baseCandidate") or {}).get("name", ""),
                    "createdAt": doc["createdAt"], "updatedAt": doc["updatedAt"], "runCount": len(related),
                    "latestRunId": related[0]["id"] if related else None,
                    "latestRunStatus": related[0]["status"] if related else None})
        return {"sessions": values, "nextCursor": next_cursor}

    def conversation(self, owner, *, session_id=None, run_id=None):
        from .conversation import legacy_run_transcript, run_events, run_summary, transcript
        with self.repository.transaction() as db:
            if run_id:
                run = self.repository.get(db, owner, "run", run_id)
                if run.get("conversationSnapshotId"):
                    row = db.execute("SELECT doc,sha256 FROM workshop_snapshots WHERE owner=? AND id=?", (owner, run["conversationSnapshotId"])).fetchone()
                    if not row or digest(json.loads(row["doc"])) != row["sha256"]:
                        fail("对话快照完整性检查失败。", 503, "snapshot_corrupt")
                    result = json.loads(row["doc"])
                else:
                    result = legacy_run_transcript(db, owner, run)
                result["runId"] = run_id
                result["entries"].extend(run_events(db, owner, run))
                # Cancellation is a later user action tied to this run, not a
                # change of strategy. Include it while excluding later rematches.
                for row in db.execute("SELECT seq,doc FROM workshop_conversation WHERE owner=? AND run=? AND source=? ORDER BY seq", (owner, run_id, "cancel:" + run_id)):
                    result["entries"].append({**json.loads(row["doc"]), "id": "conversation_" + str(row["seq"])})
                result["entries"].sort(key=lambda entry: entry["at"])
                runs = [run]
            else:
                session = self.repository.get(db, owner, "session", session_id)
                result = transcript(db, owner, session)
                runs = [json.loads(row["doc"]) for row in db.execute("SELECT doc FROM workshop_objects WHERE owner=? AND kind='run' ORDER BY updated DESC,id DESC", (owner,))]
                runs = [run for run in runs if run["sessionId"] == session_id]
            result["runs"] = [run_summary(run) for run in runs]
        return safe_result(result)

    def record_conversation_failure(self, owner, session_id, action, body, error):
        """Save public workflow failures without logging arbitrary invalid input."""
        messages = {
            "rewrite_unavailable": "策略改写服务失败或超时，请稍后重试；尚未生成可回测版本。",
            "rewrite_closed": "策略改写服务暂不可用，请稍后重试。",
            "rewrite_validation_failed": "策略规则连续三次未通过格式或表达校验，请补充或修改描述后重试。",
            "rewrite_finalization_failed": "本次规则生成连续三次未通过格式或表达校验；原回答已保留，请重试本次生成。",
            "concurrency_limit": "整机回测并发名额已满，请稍后再试。",
            "formal_data_gate": "完整收益报告尚未通过数据验证门槛。",
            "custom_source_mismatch": "自定义源码与规则不一致，请重新生成。",
        }
        if error.code not in messages or not isinstance(body, dict):
            return
        request_key = body.get("idempotencyKey")
        if not isinstance(request_key, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{8,128}", request_key):
            return
        from .conversation import pick
        allowed = pick(body, ("idea", "instructions", "customizationId", "symbol", "startDate", "endDate", "initialCash"))
        if any(isinstance(value, str) and len(value) > 4000 for value in allowed.values()):
            return
        with self.repository.transaction() as db:
            session = self.repository.get(db, owner, "session", session_id)
            style = body.get("languageStyle", session.get("languageStyle", "beginner"))
            if not isinstance(style, str) or style not in {"beginner", "expert"}:
                style = "beginner"
            simple_messages = {
                "rewrite_unavailable": "这次修改没有完成，请稍后再试。目前还不能用这版策略做测试。",
                "rewrite_closed": "现在暂时不能帮你修改策略，请稍后再试。",
                "rewrite_validation_failed": "这次尝试了三次，规则还是没能整理正确。请把买入和卖出的要求说得更具体，再试一次。",
                "rewrite_finalization_failed": "这次没有整理出有效规则，你的回答已保留。请重试本次回答。",
                "concurrency_limit": "目前同时测试的任务已满，请等一会儿再试。",
                "formal_data_gate": "还有一些数据没准备好，现在不能出完整的收益报告。",
                "custom_source_mismatch": "代码和你确认的规则没有对上，请重新生成。",
            }
            message = style_text(style, simple_messages[error.code], messages[error.code])
            source = "failure:" + action + ":" + error.code
            if db.execute("SELECT 1 FROM workshop_conversation WHERE owner=? AND session=? AND source=? AND request_key=?", (owner, session_id, source, request_key)).fetchone():
                return
            stamp = now()
            stage = "customization" if action.startswith("customization") else "backtest"
            entries = [{"at": stamp, "role": "user", "stage": stage, "title": "提交请求",
                        "text": allowed.get("instructions") or allowed.get("idea") or "提交当前步骤。", "details": {"request": allowed}},
                       {"at": stamp, "role": "assistant", "stage": stage, "title": "当前步骤未完成",
                        "text": message, "languageStyle": style, "languageStyleVersion": LANGUAGE_STYLE_VERSION,
                        "details": {"error": {"code": error.code, "message": message}}}]
            from .contracts import canonical
            for entry in entries:
                db.execute("INSERT INTO workshop_conversation(owner,session,run,source,request_key,doc) VALUES(?,?,?,?,?,?)",
                           (owner, session_id, None, source, request_key, canonical(entry)))

    def cancel(self, owner, run_id, body):
        fields(body, {"idempotencyKey"}, {"idempotencyKey"})
        key(body)
        with self.lock, self.repository.transaction() as db:
            run = self.repository.get(db, owner, "run", run_id)
            action = "cancel:" + run_id
            cached = self.repository.cached(db, owner, action, body)
            if cached is not None:
                return cached
            if run["status"] not in TERMINAL:
                run["status"] = "cancelling"
                self.repository.put(db, owner, "run", run)
                self.repository.event(db, owner, run_id, "run.cancelling", {"status": "cancelling"})
            response = self._public_run(run)
            self.repository.remember(db, owner, action, body, response)
        self.worker.cancel(run_id)
        return response

    def code(self, owner, run_id):
        run = self.repository.read(owner, "run", run_id)
        archived = self.repository.load_snapshot(owner, run["executionSnapshotId"])
        if archived["package"].get("custom"):
            custom = archived["package"]["custom"]
            return {"code": custom["sourceCode"], "sourceCode": custom["sourceCode"], "sourceHash": custom["sourceHash"],
                    "implementationHash": archived["package"]["implementationHash"], "dependencies": ["WASI Python", "confirmed rule DSL"],
                    "pythonVersion": archived["package"]["pythonVersion"], "strategyKind": "custom",
                    "rulesDigest": custom["rulesDigest"], "validation": custom["validation"],
                    "provenance": {key: custom.get(key) for key in ("originalIdea", "clarifications", "baseStrategy")}}
        relative = "strategies/" + run["strategy"]["strategyId"] + ".py"
        raw = base64.b64decode(archived["package"]["files"][relative], validate=True)
        return {"code": raw.decode("utf-8"), "sourceCode": raw.decode("utf-8"),
                "sourceHash": hashlib.sha256(raw).hexdigest(), "implementationHash": archived["package"]["implementationHash"],
                "dependencies": list(sorted(archived["package"]["files"])), "pythonVersion": archived["package"]["pythonVersion"]}

    def events(self, owner, run_id, after):
        return {"events": self.repository.events(owner, run_id, after)}
