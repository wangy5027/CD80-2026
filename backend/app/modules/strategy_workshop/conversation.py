"""Public, append-only workshop dialogue; never persist provider internals here."""
from __future__ import annotations

import json

from .contracts import canonical, now
from .presentation import LANGUAGE_STYLE_VERSION, style_text


PARTIAL_NOTICE = "这段历史创建于完整对话记录功能上线前，已根据当时保存的记录恢复；未保存的对话和失败请求无法补回。"
CANDIDATE_FIELDS = ("strategyId", "name", "version", "summary", "quality", "intentValidity", "explanation",
                    "parameters", "rules", "suitableFor", "limitations", "sourceCode", "sourceHash", "strategyKind",
                    "ruleSummary", "baseStrategyId", "fallbackReason", "languageStyle", "languageStyleVersion")
CUSTOM_FIELDS = ("id", "status", "summary", "rulePlan", "ruleSummary", "assumptions", "questions", "dataGaps",
                 "clarifications", "alignmentCompleted", "alignmentQuestions", "alignmentIssues",
                 "sourceCode", "sourceHash", "validation", "rulesDigest", "skillVersion",
                 "generationAttempts", "ruleApproval", "createdAt", "updatedAt")
CUSTOM_FIELDS += ("externalDataMessage", "uploadAvailabilityMessage", "languageStyle", "languageStyleVersion")
PREFLIGHT_FIELDS = ("id", "requested", "actual", "instrument", "asof", "coverage", "gate",
                    "requiresCoverageConfirmation", "configRevision", "createdAt", "updatedAt")


def pick(value, fields):
    value = value or {}
    return {key: value[key] for key in fields if key in value}


def run_summary(run):
    return {**pick(run, ("id", "runId", "sessionId", "status", "createdAt", "updatedAt", "finishedAt")),
            "strategyName": (run.get("strategy") or {}).get("name", ""),
            "symbol": (run.get("requested") or {}).get("symbol", "")}


def action_entries(action, body, response, *, legacy=False):
    """Render the exact public response while keeping internal prompts out."""
    kind = action.split(":", 1)[0]
    at = response.get("updatedAt") or response.get("createdAt") or now()
    entries = []
    style = response.get("languageStyle", "beginner")
    if kind == "customization-reset":
        style = (response.get("candidate") or {}).get("languageStyle", style)

    def add(role, stage, title, text="", **details):
        entry = {"at": at, "role": role, "stage": stage, "title": title,
                 "text": text, "details": details}
        if role == "assistant":
            entry.update(languageStyle=style, languageStyleVersion=LANGUAGE_STYLE_VERSION)
        entries.append(entry)

    candidate = pick(response.get("candidate") or response.get("baseCandidate"), CANDIDATE_FIELDS)
    if kind in {"sessions", "rematch"}:
        add("user", "matching", "投资想法" if kind == "sessions" else "重新描述策略",
            body.get("idea", response.get("idea", "")), sourceRunId=response.get("sourceRunId"))
        explanation = candidate.get("explanation") or {}
        if not isinstance(explanation, dict):
            explanation = {"understoodIdea": str(explanation)}
        add("assistant", "matching", "理解想法并匹配策略", explanation.get("understoodIdea", candidate.get("summary", "")),
            candidate=candidate, matchRevision=response.get("matchRevision"))
    elif kind == "personal-app-session":
        add("user", "configuration", "打开个人策略应用", "使用已保存的策略，重新选择回测参数。",
            sourcePersonalAppId=response.get("sourcePersonalAppId"), sourceRunId=response.get("sourceRunId"))
        add("assistant", "configuration", "复用已保存的策略",
            style_text(style, "这次仍用你保存的买卖规则。可以重新选股票、日期和起始资金，再测试一次。",
                       "已锁定保存版本的策略源码与交易规则。请重新配置回测标的、样本区间及初始资金。"),
            candidate=candidate, sourcePersonalAppId=response.get("sourcePersonalAppId"),
            sourceRunId=response.get("sourceRunId"), configuration=response.get("rerunDefaults"))
    elif kind == "selection":
        decision = body.get("decision") or (response.get("selectionApproval") or {}).get("decision")
        add("user", "selection", "选择策略", "采用这条策略进行回测。" if decision == "accept" else "不认可这条策略，重新描述。",
            decision=decision, strategyName=candidate.get("name"))
        add("assistant", "selection", "选择已保存", style_text(style, "选好要测试的股票或基金，再选开始和结束日期。", "请配置回测标的及样本区间。") if decision == "accept" else style_text(style, "再说说你想怎么买、怎么卖，我会重新找一个接近的策略。", "请补充投资假设或交易条件，重新进行策略映射。"))
    elif kind == "customization":
        custom = pick(response.get("customization"), CUSTOM_FIELDS)
        instructions = body.get("instructions", "")
        if legacy:
            # The old request table did not save request bodies; only this
            # cumulative record is known, so never pretend it was one turn.
            add("system", "customization", "已保存的补充要求", "旧记录保留了以下累计补充要求。",
                clarifications=custom.get("clarifications", []))
        else:
            add("user", "customization", "按我的想法修改策略", instructions or "坚持我的思路，基于现有策略整理自定义规则。")
        titles = {"needs_clarification": "需要补充策略条件", "alignment_incomplete": "本轮需求对齐未能形成规则",
                  "unsupported_data": "策略所需数据暂不支持", "rules_ready": "自定义规则待确认"}
        text = custom.get("summary", "")
        for key in ("externalDataMessage", "uploadAvailabilityMessage"):
            if custom.get(key) and custom[key] not in text:
                text += "\n\n" + custom[key]
        add("assistant", "customization", titles.get(custom.get("status"), "整理自定义规则"), text, customization=custom)
    elif kind == "customization-confirm":
        custom = pick(response.get("customization"), CUSTOM_FIELDS)
        add("user", "validation", "确认自定义规则", "确认这些规则，并校验策略代码。", customizationId=custom.get("id"), rulesDigest=custom.get("rulesDigest"))
        passed = custom.get("status") == "validated"
        add("assistant", "validation", "代码校验通过" if passed else "代码校验未通过",
            style_text(style, "代码检查通过了。确认采用后，就可以选股票和日期，用过去的数据试一遍。", "策略代码已通过隔离环境校验，确认采用后可配置回测标的及样本区间。") if passed else (custom.get("validation") or {}).get("message", "当前代码未通过校验。"), customization=custom)
    elif kind == "customization-reset":
        add("user", "selection", "返回现有策略", "返回匹配到的现有策略方案。", decision="reset", strategyName=candidate.get("name"))
        add("assistant", "selection", "现有策略方案", candidate.get("summary", ""), candidate=candidate)
    elif kind == "preflight":
        configuration = pick(body, ("symbol", "startDate", "endDate", "initialCash"))
        if legacy:
            add("system", "configuration", "已保存的回测配置", "以下为当时保存的配置结果，未保存原始表单输入。", configuration=response.get("requested"))
        else:
            add("user", "configuration", "设置回测", "检查这组回测配置与数据覆盖。", configuration=configuration)
        add("assistant", "configuration", "行情与回测区间检查", style_text(style, "请看看：你选的日期、实际能测试的日期，以及还有哪些数据没准备好。", "请核对请求样本区间、有效回测区间及数据完备性限制。"), preflight=pick(response, PREFLIGHT_FIELDS))
    elif kind == "runs":
        configuration = {"mode": response.get("mode"), "diagnosticAcknowledged": response.get("diagnosticAcknowledged"),
                         "coverageDecision": response.get("coverageApproval")}
        add("user", "backtest", "确认并开始回测", "接受当前配置，开始回测。", configuration=configuration, runId=response["id"])
        add("system", "backtest", "回测已提交", "回测任务已排队。", run=run_summary(response), status="queued")
    elif kind == "cancel":
        add("user", "backtest", "请求取消回测", "取消本次回测。", runId=response["id"])
    return entries


def append_action(db, owner, action, body, response):
    session_id = response.get("sessionId")
    if not session_id:
        return
    if "languageStyle" not in response:
        row = db.execute("SELECT doc FROM workshop_objects WHERE owner=? AND kind='session' AND id=?", (owner, session_id)).fetchone()
        if row:
            response = {**response, "languageStyle": json.loads(row["doc"]).get("languageStyle", "beginner")}
    run_id = response.get("runId") if action == "runs" or action.startswith("cancel:") else None
    for entry in action_entries(action, body, response):
        db.execute("INSERT INTO workshop_conversation(owner,session,run,source,request_key,doc) VALUES(?,?,?,?,?,?)",
                   (owner, session_id, run_id, action, body["idempotencyKey"], canonical(entry)))


def event_entry(event, run):
    status = event.get("status") or event.get("type", "").removeprefix("run.")
    titles = {"queued": "回测已排队", "validating": "检查冻结输入", "running": "正在运行回测", "succeeded": "回测完成",
              "failed": "回测失败", "cancelling": "正在取消回测", "cancelled": "回测已取消", "interrupted": "回测已中断"}
    details = {"run": run_summary(run), "status": status}
    details["run"]["status"] = status
    text = titles.get(status, "回测状态更新")
    if status in {"failed", "interrupted"}:
        # Worker failures may contain runtime diagnostics; expose only public
        # user-facing information and keep arbitrary exception internals out.
        error = run.get("error") or {}
        details["error"] = {"code": error.get("code"), "message": "运行未完成，可查看本次回测的错误提示后重试。"}
        text = details["error"]["message"]
    return {"at": event["at"], "role": "system", "stage": "backtest", "title": titles.get(status, "回测状态更新"),
            "text": text, "details": details}


def append_event(db, owner, run_id, event, event_id):
    if event["type"] == "run.queued":
        return  # The run submission action already includes this response.
    row = db.execute("SELECT doc FROM workshop_objects WHERE owner=? AND kind='run' AND id=?", (owner, run_id)).fetchone()
    if row:
        run = json.loads(row["doc"])
        db.execute("INSERT INTO workshop_conversation(owner,session,run,source,request_key,doc) VALUES(?,?,?,?,?,?)",
                   (owner, run["sessionId"], run_id, "event", str(event_id), canonical(event_entry(event, run))))


def stored_entries(db, owner, session_id):
    rows = db.execute("SELECT seq,source,request_key,doc FROM workshop_conversation WHERE owner=? AND session=? ORDER BY seq",
                      (owner, session_id)).fetchall()
    return [{**json.loads(row["doc"]), "id": "conversation_" + str(row["seq"])} for row in rows], rows


def transcript(db, owner, session):
    entries, rows = stored_entries(db, owner, session["id"])
    complete = any(row["source"] == "sessions" or row["source"].startswith("personal-app-session:") for row in rows)
    if not complete:
        recorded = {(row["source"], row["request_key"]) for row in rows}
        recovered = []
        requests = db.execute("SELECT rowid,action,key,response FROM workshop_requests WHERE owner=? ORDER BY rowid", (owner,)).fetchall()
        for request in requests:
            response = json.loads(request["response"])
            if response.get("sessionId") != session["id"] or (request["action"], request["key"]) in recorded:
                continue
            for index, entry in enumerate(action_entries(request["action"], {}, response, legacy=True)):
                recovered.append({**entry, "id": f"recovered_{request['rowid']}_{index}"})
        if not recovered and not entries:
            recovered.append({"id": "recovered_session_" + session["id"], "at": session["createdAt"], "role": "system",
                "stage": "matching", "title": "已保存的策略想法", "text": session.get("idea", ""),
                "details": {"candidate": pick(session.get("candidate") or session.get("baseCandidate"), CANDIDATE_FIELDS)}})
        entries = recovered + entries
        # Event records existed before conversation history. Recover them only
        # where an append-only dialogue entry has not already captured them.
        saved_events = {row["request_key"] for row in rows if row["source"] == "event"}
        for run_row in db.execute("SELECT doc FROM workshop_objects WHERE owner=? AND kind='run'", (owner,)):
            run = json.loads(run_row["doc"])
            if run.get("sessionId") != session["id"]:
                continue
            for entry in run_events(db, owner, run):
                if entry["id"].removeprefix("run_event_") not in saved_events:
                    entries.append(entry)
        entries.sort(key=lambda entry: entry["at"])
    result = {"sessionId": session["id"], "complete": complete, "entries": entries}
    if not complete:
        result["notice"] = PARTIAL_NOTICE
    return result


def legacy_run_transcript(db, owner, run):
    """Cut old remembered responses at the exact run request, not a timestamp."""
    requests = db.execute("SELECT rowid,action,response FROM workshop_requests WHERE owner=? ORDER BY rowid", (owner,)).fetchall()
    entries, found = [], False
    for request in requests:
        response = json.loads(request["response"])
        if response.get("sessionId") != run["sessionId"]:
            continue
        for index, entry in enumerate(action_entries(request["action"], {}, response, legacy=True)):
            entries.append({**entry, "id": f"recovered_{request['rowid']}_{index}"})
        if request["action"] == "runs" and response.get("id") == run["id"]:
            found = True
            break
    if not found:
        # No reliable cutoff remains. The run itself is immutable and known;
        # using today's mutable session would fabricate an earlier dialogue.
        entries = [{"id": "recovered_run_" + run["id"], "at": run["createdAt"], "role": "system", "stage": "matching",
                    "title": "本次回测保存的策略", "text": run.get("idea", ""),
                    "details": {"candidate": pick(run.get("candidate"), CANDIDATE_FIELDS),
                                "configuration": run.get("requested"), "provenance": run.get("customizationProvenance")}}]
    return {"sessionId": run["sessionId"], "runId": run["id"], "complete": False,
            "notice": PARTIAL_NOTICE, "entries": entries}


def run_events(db, owner, run):
    result = []
    for row in db.execute("SELECT seq,doc FROM workshop_events WHERE owner=? AND run=? ORDER BY seq", (owner, run["id"])):
        event = json.loads(row["doc"])
        if event["type"] == "run.queued":
            continue
        result.append({**event_entry(event, run), "id": "run_event_" + str(row["seq"])})
    return result
