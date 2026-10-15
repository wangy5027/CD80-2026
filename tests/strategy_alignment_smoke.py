"""Opt-in real-model check of one clarification followed by confirmed strategy code.

Run from the final project root; uses only a temporary account/data directory.
No market run or production account mutation is performed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
BASE = "/desktop/api/strategy-workshop"
IDEA = (
    "只用单只 ETF 日线，当收盘价高于含当日的20日简单均线时全仓买入，"
    "只做多、无杠杆、不用外部数据。卖出条件我还没有确定，请先让我确认卖出条件，"
    "不要自行选择卖出规则。"
)
ANSWER = (
    "确认：卖出条件是当日收盘价小于或等于同一条含当日的20日收盘价简单均线。"
    "买入条件保持当日收盘价严格高于该均线。全部信号按收盘判断，下一交易日开盘模拟成交，"
    "只做多、全仓或空仓、不加杠杆，不增加其他指标或交易条件。请按这些条件生成策略。"
)


def checked(response, status, stage):
    if response.status_code != status:
        error = response.json.get("error", {}) if response.is_json else {}
        raise RuntimeError(json.dumps({"stage": stage, "http": response.status_code,
                                      "errorCode": error.get("code") if isinstance(error, dict) else None}))
    return response.json


def report(stage, **values):
    print(json.dumps({"stage": stage, **values}, ensure_ascii=False), flush=True)


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
    from backend.app import create_app
    from backend.app.modules.strategy_workshop.custom_rules import plan_hash
    from tests.strategy_model_smoke import check_rule

    scratch = ROOT / "data/strategy-workshop-validation"
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="one-alignment-", dir=scratch) as temporary:
        app = create_app({"TESTING": True, "DATA_DIR": temporary, "IMPORT_LEGACY_HISTORY": False,
                          "AGENT_RUNTIME_DIR": str(ROOT / "data/agent-runtime")})
        try:
            model = app.config.get("GITEE_AI_MODEL", "")
            if "flash" not in model.lower():
                raise RuntimeError("Expected the configured Flash model")
            client = app.test_client()
            checked(client.post("/desktop/api/register", json={"email": "alignment-smoke@example.test",
                    "password": "temporary-alignment-test-password"}), 200, "register")
            session = checked(client.post(f"{BASE}/sessions", json={"idea": IDEA,
                "languageStyle": "beginner", "idempotencyKey": uuid4().hex}), 201, "match")
            report("match", model=model, fallbackReason=session["candidate"].get("fallbackReason"))
            body = {"matchRevision": session["matchRevision"],
                    "selectionDigest": session["candidate"]["selectionDigest"],
                    "languageStyle": "beginner", "idempotencyKey": uuid4().hex}
            aligned = checked(client.post(f"{BASE}/sessions/{session['id']}/customization", json=body), 200, "alignment")
            initial = aligned["customization"]
            report("alignment", status=initial["status"], questions=initial["questions"],
                   assumptions=initial["assumptions"])
            assert initial["status"] == "needs_clarification" and initial["questions"]
            body.update(instructions=ANSWER, idempotencyKey=uuid4().hex)
            final = checked(client.post(f"{BASE}/sessions/{session['id']}/customization", json=body), 200, "answer")
            custom = final["customization"]
            report("answer", status=custom["status"], questions=custom["questions"],
                   alignmentCompleted=custom.get("alignmentCompleted"),
                   generationAttempts=custom.get("generationAttempts"), rulePlan=custom.get("rulePlan"))
            assert custom["status"] == "rules_ready" and custom["questions"] == []
            assert custom["alignmentCompleted"] is True and check_rule(custom["rulePlan"])
            repeat = checked(client.post(f"{BASE}/sessions/{session['id']}/customization", json=body), 200, "idempotency")
            assert repeat["customization"]["id"] == custom["id"]
            confirmed = checked(client.post(f"{BASE}/sessions/{session['id']}/customization/confirm", json={
                "customizationId": custom["id"], "rulesDigest": custom["rulesDigest"],
                "idempotencyKey": uuid4().hex}), 200, "confirm")
            result = confirmed["customization"]
            evidence = result["validation"]
            assert result["status"] == "validated" and evidence["passed"] is True
            assert evidence["sourceHash"] == hashlib.sha256(custom["sourceCode"].encode()).hexdigest()
            assert evidence["ruleHash"] == plan_hash(custom["rulePlan"])
            report("confirm", status=result["status"], checks=evidence.get("checks"),
                   sourceHash=evidence["sourceHash"], ruleHash=evidence["ruleHash"],
                   duplicateAnswerReturnedSameVersion=True)
        finally:
            app.extensions["workbench_service"].close()


if __name__ == "__main__":
    main()
