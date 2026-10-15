"""Temporary browser server: deterministic model, actual WASI, no production data."""
import json
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.app import create_app
from werkzeug.serving import make_server


class Provider:
    key='fixture-only'
    model='fixture-model'
    def tokens(self, *args, **kwargs):
        yield '{"intent":"chat","input":{}}'
    def events(self,messages,model,stopped,on_response,**kwargs):
        definitions=kwargs.get('tools') or []
        results = [m for m in messages if m['role'] == 'tool']
        if definitions and not any('0.30' in m.get('content', '') for m in results):
            tools=[d for d in definitions if d['function']['name'].startswith('cap_')]
            if tools:
                yield {'type':'turn_end','tool_calls':[{'id':'fixture-call','type':'function','function':{'name':tools[0]['function']['name'],'arguments':'{"values":["0.10","0.20"]}'}}]}
                return
            if '个人目录首批：' in messages[0]['content']:
                catalog = json.loads(messages[0]['content'].split('个人目录首批：')[-1])
                tool = next((item for item in catalog['items'] if item['kind'] == 'tool' and item['available']), None)
                if tool:
                    yield {'type': 'turn_end', 'tool_calls': [{'id': 'discover', 'type': 'function', 'function': {'name': 'library_load', 'arguments': json.dumps({k: tool[k] for k in ('assetId', 'versionId')})}}]}
                    return
        yield {'type':'token','text':'已根据实际工具结果完成，合计 0.30。'}
        yield {'type':'turn_end','tool_calls':[]}


if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='cd80-capability-browser-') as directory:
        app=create_app({'TESTING':True,'DATA_DIR':directory,'DESKTOP_PORT':48995,'GITEE_AI_API_KEY':'','AGENT_RUNTIME_DIR':str(ROOT/'data/agent-runtime')})
        service=app.extensions['workbench_service']
        service.runtime.provider=Provider()
        service.platform.planner.provider=service.runtime.provider
        service.model_id=Provider.model
        server=make_server('127.0.0.1',48995,app,threaded=True)
        print(json.dumps({'fixture':True,'realWasi':True,'url':'http://127.0.0.1:48995/desktop/'}),flush=True)
        try: server.serve_forever()
        except KeyboardInterrupt: pass
        finally: server.server_close(); service.close()
