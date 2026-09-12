"""Actual Docker CLI boundary checks, isolated nested daemon only."""
import fcntl,hashlib,json,selectors,subprocess,secrets
from pathlib import Path
root=Path('/workspace');out=root/'docs/evidence/deployment-safety-20260912'
def run(args,**kwargs):return subprocess.run(args,text=True,capture_output=True,**kwargs)
def inspect():return json.loads(run(['docker','inspect','revguard-api'],check=True).stdout)[0]
def stop():return run(['python3','scripts/quiesce_api.py','stop','--profile','local','--project','workspace','--owner','isolatedguardcheck'],cwd=root)
before=inspect()['Id'];env_hash=hashlib.sha256((root/'.env').read_bytes()).hexdigest()
with open('/tmp/revguard-deployment.lock','a+b') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX)
 concurrent=run(['bash','scripts/deploy_demo.sh','--local'],cwd=root)
 assert concurrent.returncode!=0 and '另一个部署' in concurrent.stderr
assert hashlib.sha256((root/'.env').read_bytes()).hexdigest()==env_hash
mismatch=run(['bash','scripts/deploy_demo.sh','--full','--reset'],cwd=root)
assert mismatch.returncode!=0 and '数据库拓扑' in mismatch.stderr
assert hashlib.sha256((root/'.env').read_bytes()).hexdigest()==env_hash
owner=secrets.token_hex(16)
source='import sys,os; from pathlib import Path; Path("/tmp/guard-"+sys.argv[1]).write_text(str(os.getpid())); from revguard.api import store; from revguard.runtime_barrier import acquire_runtime_lease\nwith acquire_runtime_lease(store):\n print("LEASE",flush=True)\n sys.stdin.buffer.read()'
holder=subprocess.Popen(['docker','exec','-i','revguard-api','python','-u','-c',source,owner],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
try:
 with selectors.DefaultSelector() as selector:
  selector.register(holder.stdout,selectors.EVENT_READ)
  assert selector.select(60) and holder.stdout.readline()==b'LEASE\n'
 denied=stop();assert denied.returncode!=0
 assert inspect()['Id']==before and inspect()['State']['Running']
finally:
 holder.stdin.close()
 cleanup='import os,signal,sys; from pathlib import Path; p=Path("/tmp/guard-"+sys.argv[1]); pid=int(p.read_text()) if p.exists() else 0; c=Path("/proc/"+str(pid)+"/cmdline");\nif pid and c.exists() and c.read_bytes().rstrip(b"\\0").split(b"\\0")[-1]==sys.argv[1].encode(): os.kill(pid,signal.SIGTERM)\np.unlink(missing_ok=True)'
 run(['docker','exec','revguard-api','python','-c',cleanup,owner],timeout=20)
 try:holder.wait(20)
 except subprocess.TimeoutExpired:holder.kill();holder.wait()
source='from revguard.api import store; from revguard.agent_bridge import create_agent_task; c=store.get_case("CASE-2026-0008"); t=create_agent_task(c,"CaseNormalizeSkill",{"raw_case":c}); store.save_agent_task(t); store.transition_agent_task(t["task_id"],expected={"PENDING"},status="RUNNING"); print(t["task_id"])'
task_id=run(['docker','exec','revguard-api','python','-c',source],check=True).stdout.strip()
try:
 denied=stop();assert denied.returncode!=0
 assert inspect()['Id']==before and inspect()['State']['Running']
finally:
 source='import sys; from revguard.api import store; store.transition_agent_task(sys.argv[1],expected={"RUNNING"},status="CANCELLED")'
 run(['docker','exec','revguard-api','python','-c',source,task_id],check=True)
result={'concurrent_deployment_rejected_before_config_edit':True,'topology_change_even_with_reset_rejected_before_config_edit':True,'active_shared_lease_refused_stop':True,'durable_running_task_without_team_run_refused_stop':True,'original_container_not_replaced':True,'passed':True}
(out/'guard-boundaries.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
