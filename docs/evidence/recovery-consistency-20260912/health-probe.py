import json,os,time,urllib.request,urllib.error
from pathlib import Path
from datetime import datetime,timezone
root='http://revguard-api:9000'
out=Path('/evidence');out.mkdir(exist_ok=True)
token=Path('/run/observability/metrics-token').read_text().strip()
def read(url,auth=False):
    r=urllib.request.Request(url,headers={'Authorization':'Bearer '+token} if auth else {})
    with urllib.request.urlopen(r,timeout=25) as response:return json.load(response)
for attempt in range(30):
    try:
        health=read(root+'/api/v1/health')
        status=read(root+'/api/v1/ops/observability',True)
        targets=read('http://prometheus:9090/api/v1/targets')['data']['activeTargets']
        if status['available'] and all(t['health']=='up' for t in targets):break
    except (urllib.error.URLError,TimeoutError):pass
    time.sleep(2)
assert status['available']
assert len(targets)==4 and all(t['health']=='up' for t in targets)
blocked={}
for path in ['api/admin/settings','login','api/datasources/proxy/1/query','api/ds/query']:
    request=urllib.request.Request(root+'/grafana/'+path,method='POST' if path=='api/ds/query' else 'GET')
    try:
        with urllib.request.urlopen(request,timeout=10) as response:blocked[path]=response.status
    except urllib.error.HTTPError as e:blocked[path]=e.code
assert all(v==404 for v in blocked.values())
try:read(root+'/api/v1/ops/observability')
except urllib.error.HTTPError as e:assert e.code==401
else:raise AssertionError('Status endpoint should require viewer')
status['embed_url']='/grafana/public-dashboards/<dashboard>?theme=dark&kiosk'
result={'verified_at':datetime.now(timezone.utc).isoformat(),'environment':'10.10.10.202 Docker','health':health,'grafana':read('http://grafana:3000/grafana/api/health'),'embedding':status,'blocked_paths':blocked,'prometheus_targets':[{'job':t['labels']['job'],'health':t['health']} for t in targets],'passed':True}
(out/'production-health.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(result,ensure_ascii=False))
