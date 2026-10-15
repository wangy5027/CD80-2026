"""Opt-in synthetic end-to-end check using the configured model and WASI runtime.

No existing accounts, conversations or files are sent. Default mode only previews.
--execute approves this script's synthetic CSV plan; --reuse also saves/reuses it
inside a disposable account/database. It never publishes a personal application.
"""

import argparse
import io
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--reuse", action="store_true")
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
    from backend.app import create_app
    with tempfile.TemporaryDirectory(prefix="cd80-live-smoke-") as temporary:
        app = create_app({"DATA_DIR": temporary, "IMPORT_LEGACY_HISTORY": False})
        workbench = app.extensions["workbench_service"]
        platform = app.extensions["agent_platform"]
        client = app.test_client()
        model_outputs = []
        transport = platform.planner.provider.tokens
        def capture(*call_args, **call_kwargs):
            pieces = []
            model_outputs.append(pieces)
            for piece in transport(*call_args, **call_kwargs):
                pieces.append(piece)
                yield piece
        platform.planner.provider.tokens = capture
        try:
            if not workbench.runtime.provider.key:
                raise RuntimeError("Model key is not configured")
            response = client.post("/desktop/api/register", json={"email": "smoke@example.test", "password": "synthetic-test-only"})
            assert response.status_code == 200, response.json
            owner = response.json["user"]["id"]

            def upload(content):
                response = client.post("/desktop/api/files", data={"file": (io.BytesIO(content), "synthetic.csv")}, content_type="multipart/form-data")
                assert response.status_code == 201, response.json
                return response.json["id"]

            def wait(run_id, expected):
                for tick in range(2100):
                    doc = platform.repository.get(owner, run_id)
                    if doc["status"] in expected:
                        return doc
                    if doc["status"] in {"failed", "cancelled", "interrupted", "awaiting_input", "awaiting_authorization"}:
                        raise RuntimeError(json.dumps({"status": doc["status"], "error": doc["error"], "missingInputs": doc.get("missingInputs")}, ensure_ascii=False))
                    if tick and tick % 100 == 0:
                        print(json.dumps({"stage": "waiting", "status": doc["status"], "elapsedSeconds": round(tick * .3)}, ensure_ascii=False), flush=True)
                    time.sleep(.3)
                raise RuntimeError("Timed out waiting for run")

            file_id = upload(b"category,amount\nA,10\nB,7\nA,5\n")
            response = client.post("/desktop/api/agent-dispatch", json={"message": "创建一个新的可复用CSV分组求和应用，请生成新的工具和skill。输入就是所附CSV文件，UTF-8逗号分隔且有表头；按category列分组，对amount列作十进制求和（sum）。复用参数名为fileId、groupBy、valueColumn；空值、非法数值、缺列立即报错。输出summary.csv，表头为分组列名,total_amount；分组键升序，每行用换行符结束。不使用pandas等第三方库或外部数据。输出实际汇总说明。", "fileIds": [file_id], "idempotencyKey": "live-create"})
            assert response.status_code == 200, response.json
            assert response.json["intent"] == "create", response.json
            print(json.dumps({"stage": "dispatch", "intent": response.json["intent"]}), flush=True)
            doc = wait(response.json["run"]["id"], {"awaiting_confirmation"})
            inputs = dict(doc["input"])
            print(json.dumps({"stage": "preview", "status": doc["status"], "stepCount": len(doc["spec"]["pipeline"]), "toolCount": len(doc["spec"]["tools"]), "skillCount": len(doc["spec"]["skills"]), "executionPerformed": doc["executionPerformed"]}, ensure_ascii=False), flush=True)
            assert not doc["executionPerformed"]
            if not args.execute:
                return
            approval = doc["approvals"][0]
            response = client.post(f"/desktop/api/agent-runs/{doc['id']}/approvals/{approval['id']}", json={"decision": "approve", "bindingDigest": approval["bindingDigest"], "idempotencyKey": "live-approve"})
            assert response.status_code == 200, response.json
            doc = wait(doc["id"], {"succeeded"})
            artifact = next(item for item in doc["artifacts"] if item["name"].endswith(".csv"))
            data = client.get("/desktop/api/agent-artifacts/" + artifact["id"])
            text = data.data.decode("utf-8-sig").replace("\r\n", "\n")
            data.close()
            import csv
            from decimal import Decimal
            values = {row["category"]: Decimal(row["total_amount"]) for row in csv.DictReader(io.StringIO(text))}
            assert values == {"A": Decimal(15), "B": Decimal(7)}, text
            print(json.dumps({"stage": "execute", "status": doc["status"], "csv": text, "loadedSkills": doc["loadedSkills"], "explanation": doc["output"]["explanation"]}, ensure_ascii=False), flush=True)
            if args.reuse:
                saved = client.post("/desktop/api/agents", json={"runId": doc["id"], "idempotencyKey": "live-save"})
                assert saved.status_code == 201, saved.json
                second = upload(b"category,amount\nA,1\nC,4\nC,6\n")
                inputs["fileId"] = second
                response = client.post("/desktop/api/agent-runs", json={"mode": "invoke", "agentRef": saved.json["agentRef"], "input": inputs, "fileIds": [second], "idempotencyKey": "live-reuse"})
                assert response.status_code == 202, response.json
                reused = wait(response.json["id"], {"succeeded"})
                artifact = next(item for item in reused["artifacts"] if item["name"].endswith(".csv"))
                data = client.get("/desktop/api/agent-artifacts/" + artifact["id"])
                text = data.data.decode("utf-8-sig").replace("\r\n", "\n")
                data.close()
                values = {row["category"]: Decimal(row["total_amount"]) for row in csv.DictReader(io.StringIO(text))}
                assert values == {"A": Decimal(1), "C": Decimal(10)}, text
                assert not any(a["kind"] == "execute" for a in reused["approvals"])
                print(json.dumps({"stage": "reuse", "status": reused["status"], "csv": text, "repeatedDesignApproval": False}, ensure_ascii=False), flush=True)
        except Exception:
            if model_outputs:
                captured = "".join(model_outputs[-1])
                diagnostic = ROOT / "data" / "agent-live-smoke-output.txt"
                diagnostic.parent.mkdir(parents=True, exist_ok=True)
                diagnostic.write_text(captured, encoding="utf-8")
                candidate = captured.strip().removeprefix("```json").removesuffix("```").strip()
                try:
                    json.loads(candidate)
                except json.JSONDecodeError as parse_error:
                    print(json.dumps({"jsonError": str(parse_error), "nearError": candidate[max(0, parse_error.pos - 80):parse_error.pos + 80]}, ensure_ascii=True), flush=True)
                print(json.dumps({"syntheticModelOutput": {"characters": len(captured), "prefix": captured[:250], "suffix": captured[-1200:]}}, ensure_ascii=True), flush=True)
            raise
        finally:
            workbench.close()


if __name__ == "__main__":
    main()
