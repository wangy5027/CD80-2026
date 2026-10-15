"""Isolated local TC-SA-001 browser server. Never loads production .env."""
import sys
import tempfile
import threading
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from flask import jsonify
from werkzeug.serving import make_server
from backend.app import create_app
from tests.sub_agent_fixture import seed, FixtureModel, MESSAGE, FOLLOWUP
from backend.app.modules.workbench.errors import GatewayError

temp=tempfile.TemporaryDirectory(prefix='cd80-sub-browser-')
app=create_app({'TESTING':True,'DATA_DIR':temp.name,'DESKTOP_PORT':49002,'GITEE_AI_API_KEY':'fixture-only',
                'CAPABILITY_SKIP_PUBLIC_SEED':True,'GITEE_AI_BASE_URL':'https://ai.gitee.com/v1'})
w=app.extensions['workbench_service']; service=app.extensions['sub_agents']
user,_=w.auth.register('browser-sub@example.invalid','fixture-password','子任务验收')
refs=seed(w.capabilities,user['id']); service.model=FixtureModel(refs)
timeout_message = '测试模型持续超时：读取合成数据后分析并可视化。'
timeout_entered = threading.Event()
timeout_release = threading.Event()
original_call = service.model.call
def model_call(stage, payload, stopped, on_response):
    if payload.get('message') == timeout_message and stage == 'approve':
        timeout_entered.set()
        while not timeout_release.wait(.02):
            if stopped.is_set():
                raise GatewayError('已停止。', 409)
        raise GatewayError('模型规划超过整体时间上限，已终止本次请求。', 504, 'agent_model_deadline')
    return original_call(stage, payload, stopped, on_response)
service.model.call = model_call
w.platform.planner.classify=lambda *args,**kwargs:{'intent':'chat'}
entered=threading.Event();release=threading.Event()
def gate(owner,rid,stop):
    entered.set()
    while not release.wait(.02):
        if stop.is_set():return
service.before_code=gate

@app.get('/__fixture__/state')
def state():return jsonify({'entered':entered.is_set(),'message':MESSAGE,'followup':FOLLOWUP,'calls':service.model.calls,
                           'timeoutMessage': timeout_message, 'timeoutEntered': timeout_entered.is_set()})

@app.post('/__fixture__/release')
def unlock():release.set();return jsonify({'ok':True})

@app.post('/__fixture__/timeout-release')
def timeout_unlock():timeout_release.set();return jsonify({'ok':True})

if __name__=='__main__':
    server=make_server('127.0.0.1',49002,app,threaded=True)
    print('Sub-agent browser fixture ready on 49002',flush=True)
    try:server.serve_forever()
    finally:w.close();server.server_close();temp.cleanup()
