import json,os,time,urllib.request,urllib.error
from datetime import datetime,timezone
port=int(os.environ.get('COPAW_PROBE_PORT','18799'))
base=f'http://127.0.0.1:{port}'
def get(path):
    with urllib.request.urlopen(base+path,timeout=5) as response:return json.load(response)
for attempt in range(25):
    try:active=get('/api/models/active')['active_llm'];break
    except (urllib.error.URLError,TimeoutError):time.sleep(1)
else:raise SystemExit('CoPaw API did not become ready')
heartbeat=get('/api/config/heartbeat')
providers=get('/api/models')
provider=next(p for p in providers if p['id']==active['provider_id'])
model=next(m for m in [*provider.get('models',[]),*provider.get('extra_models',[])] if m['id']==active['model'])
usage=get('/api/token-usage')
assert active['model']=='gpt-5.6-luna'
assert heartbeat['enabled'] is False
assert model.get('generate_kwargs',{}).get('reasoning_effort')=='none'
print(json.dumps({'checked_at':datetime.now(timezone.utc).isoformat(),'active':active,'heartbeat':heartbeat,'model_kwargs':model.get('generate_kwargs'), 'usage':{k:usage[k] for k in ['total_calls','total_prompt_tokens','total_completion_tokens','by_model']},'passed':True},ensure_ascii=False,indent=2))
