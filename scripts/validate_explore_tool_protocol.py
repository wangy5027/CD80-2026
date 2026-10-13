import sys,json,threading,time,argparse
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
parser=argparse.ArgumentParser(description='Opt-in real provider two-tool-turn compatibility test; no account data.')
parser.add_argument('--output', type=Path, required=True)
cli=parser.parse_args()
from dotenv import load_dotenv
load_dotenv(ROOT/'.env',override=False)
from backend.app.config import Config
from backend.app.modules.workbench.graph_runtime import GiteeProvider
from backend.app.modules.workbench.tool_registry import ToolRegistry,ToolSpec,ToolResult,ToolContext
cfg={k:getattr(Config,k) for k in dir(Config) if k.isupper()}
provider=GiteeProvider({**cfg,'GITEE_AI_MAX_TOKENS':2048,'GITEE_AI_READ_TIMEOUT_SECONDS':15,'GITEE_AI_CONNECT_TIMEOUT_SECONDS':5,'GITEE_AI_REASONING_EFFORT':cfg.get('EXPLORE_MODEL_REASONING_EFFORT','low')})
registry=ToolRegistry([ToolSpec('validation_echo','Return the exact step number. No external network or private data.',{'type':'object','properties':{'step':{'type':'integer','minimum':1,'maximum':2}},'required':['step'],'additionalProperties':False},lambda a,c:ToolResult({'step':a['step'],'marker':'EXACT_TOOL_RESULT_'+str(a['step'])}),max_calls=2)])
stop=threading.Event();active=[None];trace=[];start=time.monotonic()
def response(r):
 active[0]=r
 if r is not None and stop.is_set():r.abort()
def abort():
 stop.set()
 if active[0]:active[0].abort()
timer=threading.Timer(60,abort);timer.start()
messages=[{'role':'system','content':'This is a function-calling compatibility test. First call validation_echo with step=1. After receiving it call validation_echo with step=2 in a SEPARATE turn. After receiving step=2, return ONLY JSON {"first": first exact marker, "second": second exact marker}. Do not invent results.'},{'role':'user','content':'Run the two-tool-turn test.'}]
try:
 for n in range(4):
  body='';calls=[]
  for item in provider.events(messages,provider.model,stop,response,tools=registry.definitions()):
   if item['type']=='token':body+=item['text']
   elif item['type']=='turn_end':calls=item['tool_calls']
  trace.append({'round':n+1,'visible_output':body,'calls':calls})
  if not calls:break
  messages.append({'role':'assistant','content':body or None,'tool_calls':calls})
  for call in calls:
   f=call['function'];tool_args=json.loads(f['arguments'])
   result=registry.execute(f['name'],tool_args,ToolContext(stop,response,lambda e:None),call_count=sum(len(t['calls']) for t in trace[:-1]))
   trace[-1].setdefault('results',[]).append(result.content)
   messages.append({'role':'tool','tool_call_id':call['id'],'content':json.dumps(result.content)})
 final=json.loads(body)
 passed=final=={'first':'EXACT_TOOL_RESULT_1','second':'EXACT_TOOL_RESULT_2'} and sum(bool(x['calls']) for x in trace)>=2
 result={'status':'passed' if passed else 'failed','model':provider.model,'request_options':provider.request_options,'elapsed_seconds':time.monotonic()-start,'trace':trace,'final':final}
except Exception as e:result={'status':'failed','error':type(e).__name__+': '+str(e),'model':provider.model,'elapsed_seconds':time.monotonic()-start,'trace':trace}
finally:timer.cancel()
p=cli.output;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='trace'},ensure_ascii=False))

raise SystemExit(0 if result['status']=='passed' else 1)
