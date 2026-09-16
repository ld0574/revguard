"""Read-only resource inventory, run inside the 202 Controller."""
import http.client,json,socket,subprocess,urllib.request
from datetime import datetime,timezone

def resources(kind):
    result=subprocess.run(['agt','get',kind,'-o','json'],capture_output=True,check=True,text=True)
    return [{k:v for k,v in row.items() if k in ['name','phase','state','model','runtime','image','containerState']}
            for row in json.loads(result.stdout)[kind]]

def inspect(name):
    connection=http.client.HTTPConnection('localhost')
    connection.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
    connection.sock.connect('/var/run/docker.sock')
    connection.request('GET','/containers/'+name+'/json')
    response=connection.getresponse();data=json.loads(response.read());connection.close()
    if response.status==404:return {'name':name,'exists':False}
    assert response.status==200
    return {'name':name,'exists':True,'image':data['Config']['Image'],'image_id':data['Image'], 'running':data['State']['Running']}

def get(url):
    with urllib.request.urlopen(url,timeout=10) as r:return json.load(r)
workers=resources('workers');managers=resources('managers')
assert len(workers)==10 and all(w['model']=='gpt-5.6-luna' and w['state']=='Sleeping' for w in workers)
assert managers[0]['model']=='gpt-5.6-luna'
runtime=[inspect('agentteams-manager')]+[inspect('agentteams-worker-'+w['name']) for w in workers]
assert all(not r['running'] for r in runtime[1:] if r['exists'])
assert all(r['image'].endswith(':luna-20260912') for r in runtime if r['exists'])
base='http://agentteams-manager:18799'
heartbeat=get(base+'/api/config/heartbeat'); usage=get(base+'/api/token-usage');jobs=get(base+'/api/cron/jobs')
assert heartbeat['enabled'] is False and jobs==[]
assert usage['total_calls']==188
print(json.dumps({'checked_at':datetime.now(timezone.utc).isoformat(),'environment':'10.10.10.202 Docker','workers':workers,'managers':managers,'runtime':runtime,'manager_heartbeat':heartbeat,'manager_cron_jobs':jobs,'manager_total_calls':usage['total_calls'],'api_health':get('http://revguard-api:9000/api/v1/health'),'passed':True},ensure_ascii=False,indent=2))
