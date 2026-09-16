"""Actual explicit-reset upgrade, nested Docker only, with a stale legacy file."""
import json,subprocess
from pathlib import Path
root=Path('/workspace');out=root/'docs/evidence/deployment-safety-20260912'
def run(args,**kwargs):return subprocess.run(args,text=True,capture_output=True,check=True,**kwargs)
# Existing committed demo artefacts, plus deliberately obsolete legacy JSON.
prepare='''import json
from pathlib import Path
from revguard.api import store,gateway,GATEWAY_STATE_PATH
c=store.get_case("CASE-2026-0008");c["status"]="CLOSED";store.save_case(c)
store.save_execution({"case_id":c["case_id"],"action_id":"OLD-ISOLATED","idempotency_key":"old","status":"DRAFT"})
legacy=gateway._state_snapshot();legacy["ledger"].append({"entry_id":"OLD-LEGACY-SENTINEL"})
Path(GATEWAY_STATE_PATH).write_text(json.dumps(legacy))
print("Prepared isolated completed recording and obsolete legacy JSON")
'''
run(['docker','exec','revguard-api','python','-c',prepare])
with (out/'reset-upgrade.log').open('w') as log:
 subprocess.run(['bash','scripts/deploy_demo.sh','--local','--reset'],cwd=root,stdout=log,stderr=subprocess.STDOUT,check=True)
verify='''import json
from revguard.api import store,gateway
assert store.count_cases()==8
assert all(c["status"]=="CREATED" and c.get("recording_id") for c in store.list_cases())
assert not store.list_executions("CASE-2026-0008")
assert all(r.get("entry_id")!="OLD-LEGACY-SENTINEL" for r in gateway._ledger)
assert gateway._ledger==gateway._initial_state["ledger"]
assert sum(e["event"]=="DEMO_RESET" for c in store.list_cases() for e in store.list_audit(c["case_id"]))==8
print(json.dumps({"explicit_reset_clears_completed_recording":True,"gateway_baseline_committed":True,"old_legacy_json_not_reimported":True,"reset_audit_per_case":True,"passed":True}))
'''
result=run(['docker','exec','revguard-api','python','-c',verify]);(out/'reset-upgrade.json').write_text(result.stdout);print(result.stdout)
