"""Disposable browser fixture for the home Composer's APP creation mode.

It exercises the real authoring session and APP stores. Only candidate generation
is deterministic; no business code, verification, model network or official data
is executed here.
"""

import copy
import hashlib
import io
import json
from pathlib import Path
import signal
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from flask import jsonify
from werkzeug.datastructures import FileStorage
from werkzeug.serving import make_server

from backend.app import create_app
from backend.app.modules.capability_library.contracts import fail
from tests.test_asset_apps import sample

PORT = 49024
OWNER_EMAIL = "creation-owner@example.invalid"
OTHER_EMAIL = "creation-other@example.invalid"
PASSWORD = "fixture-password"
ORIGINAL_CASE = {
    "label": "原任务用例：合成价格序列",
    "input": {"prices": [100, 120, 90, 108]},
    "fileBindings": {},
    "expectation": {"mode": "json_equals", "value": {
        "analysis": {"change": 8, "drawdown": 25, "count": 4}}},
}


class AnswerModel:
    def __init__(self):
        self.calls = []

    def call(self, stage, payload, stopped, on_response):
        self.calls.append({"stage": stage, "message": payload.get("message", "")})
        if stage == "plan":
            return {"mode": "answer", "answer": "普通问答已收到。"}
        raise AssertionError("Unexpected model stage in home creation fixture: " + stage)


def forbidden(name, counters):
    def reject(*args, **kwargs):
        counters[name] += 1
        raise AssertionError("Creating an APP must not run " + name)
    return reject


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="cd80-home-creation-") as data_dir:
        app = create_app({
            "TESTING": True,
            "DATA_DIR": data_dir,
            "DESKTOP_PORT": PORT,
            "FRONTEND_DIST": str(ROOT / "frontend/dist"),
            "CAPABILITY_SKIP_PUBLIC_SEED": True,
            "IMPORT_LEGACY_HISTORY": False,
            "GITEE_AI_API_KEY": "fixture-only-no-network",
            "CD80_READ_API_KEY": "",
            "CD80_ADMIN_API_KEY": "",
            "SFC_TOKEN": "",
            "SFC_KEY_FILE": "",
        })
        workbench = app.extensions["workbench_service"]
        owner, _ = workbench.auth.register(OWNER_EMAIL, PASSWORD, "创建模式验收")
        other, _ = workbench.auth.register(OTHER_EMAIL, PASSWORD, "第二账号验收")
        file_meta = workbench.upload_file(owner["id"], FileStorage(
            stream=io.BytesIO(b"category,value\nsynthetic,7\n"), filename="creation-source.csv"), "", "data")
        app_assets = app.extensions["asset_apps"]
        counters = {"run": 0, "verify": 0, "plan": 0, "execute": 0,
                    "upstream": 0, "create": 0, "chat": 0}
        creates = []
        generated = []

        original_create = workbench.authoring.create

        def record_create(user_id, body, key):
            creates.append({"owner": user_id, "body": copy.deepcopy(body), "key": key})
            return original_create(user_id, body, key)

        def candidate(user_id, kind, instruction, *, content=None, context=None, stopped=None):
            assert kind == "app", kind
            assert user_id == owner["id"], "No other-account candidate should be generated"
            counters["create"] += 1
            generated.append({
                "kind": kind,
                "instruction": instruction,
                "sha256": hashlib.sha256(instruction.encode("utf-8")).hexdigest(),
                "fileIds": list((context or {}).get("fileIds", [])),
            })
            if "【需要补充】" in instruction and "用户回答：" not in instruction:
                fail("AUTHORING_INPUT_REQUIRED", "请确认输入价格数组是否以 prices 字段提供？", 422)
            value = sample()
            value["name"] = "浏览器创建模式合成价格 APP " + str(counters["create"])
            value["description"] = "按合成价格序列计算变化率、最大回撤及条数；仅处理输入 prices。"
            value["testCases"] = [copy.deepcopy(ORIGINAL_CASE)]
            return value

        workbench.authoring.create = record_create
        workbench.authoring.adapters.create = candidate
        workbench.authoring.adapters.plan = forbidden("plan", counters)
        workbench.authoring.adapters.validate = forbidden("verify", counters)
        workbench.platform.executor.execute = forbidden("execute", counters)
        app_assets._new_run = forbidden("run", counters)
        workbench.platform.planner._call = forbidden("upstream", counters)
        workbench.runtime.provider.events = forbidden("upstream", counters)
        answer_model = AnswerModel()
        app.extensions["sub_agents"].model = answer_model

        @app.get("/__home_creation_fixture__/state")
        def state():
            owner_apps = app_assets.listing(owner["id"])
            other_apps = app_assets.listing(other["id"])
            return jsonify({
                "fixture": True, "realModel": False, "temporaryAccounts": True,
                "ownerId": owner["id"], "otherId": other["id"],
                "fileId": file_meta["id"], "creates": creates,
                "generated": generated, "counters": counters,
                "chatCalls": answer_model.calls,
                "ownerApps": [{"id": row["id"], "versionId": row["versionId"],
                               "name": row["name"], "verification": row["verification"],
                               "enabledVersionId": row["enabledVersionId"],
                               "testCases": row.get("testCases", [])} for row in owner_apps],
                "otherAppCount": len(other_apps),
            })

        def stop(_signal, _frame):
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        server = make_server("127.0.0.1", PORT, app, threaded=True)
        print(json.dumps({"ready": True, "port": PORT, "realModel": False,
                          "disposableAccounts": True}), flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            workbench.close()
