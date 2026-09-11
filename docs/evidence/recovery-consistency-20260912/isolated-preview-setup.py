"""Isolated 202 Docker only: a stopped Worker plus real failed recovery commit."""
import asyncio,hashlib,json,os,time,urllib.request
from pathlib import Path
for _ in range(30):
    try:
        urllib.request.urlopen('http://127.0.0.1:9000/api/v1/health',timeout=2).close();break
    except Exception:time.sleep(1)
from revguard.api import store,gateway,_mcp_team
from revguard.agent_bridge import create_agent_task
from revguard.models import CaseStatus
from revguard.state_machine import transition_case
from scripts.seed_demo import seed_store
case_id='CASE-2026-0008';out=Path('/evidence')
def snapshot():
    def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()
    with gateway.journal.transaction() as tx:
        state=tx.state()
        operations=[dict(r) for r in tx.execute('SELECT * FROM money_operations ORDER BY operation_id').fetchall()]
        holds=[dict(r) for r in tx.execute('SELECT * FROM money_holds ORDER BY channel,case_id').fetchall()]
    return {name:digest(value) for name,value in {
        'case':store.get_case(case_id),'approval':store.get_approval(case_id),
        'executions':store.list_executions(case_id),'tasks':store.list_agent_tasks(case_id),
        'audit':[row for row in store.list_audit(case_id) if row['event'] != 'HUMAN_IDENTITY_VERIFIED'],'gateway':state,'operations':operations,'holds':holds,
    }.items()}
if os.environ.get('PHASE')=='allow':
    current=snapshot();before=json.loads((out/'recovery-before.json').read_text())
    assert current==before
    (out/'recovery-after-failure.json').write_text(json.dumps(current,indent=2)+'\n')
    with gateway.journal.transaction() as tx:tx.execute('DROP TRIGGER reject_recovery ON cases')
    print('Business, money and workflow audit snapshots unchanged; separate human identity audits retained; isolated trigger removed')
else:
    seed_store(store,quiet=True)
    case=store.get_case(case_id);asyncio.run(_mcp_team().run_to_human_gate(case))
    case=store.get_case(case_id);approval=store.get_approval(case_id)
    gateway.decide_case_approval(case,{'approval_id':approval['approval_id'],'decision':'APPROVED'},
        actor='finance.lead',assertion_ref='isolated-browser-fixture')
    transition_case(store,case,CaseStatus.EXECUTING,'isolated process interrupted')
    task=create_agent_task(case,'PermissionCheckSkill',{'action_type':'LEDGER_ADJUST','risk':case['risk_decision']})
    store.save_agent_task(task);store.transition_agent_task(task['task_id'],expected={'PENDING'},status='RUNNING')
    gateway.journal.prepare('isolated-unfinished-intent',case_id,'order:isolated','commission.submit_adjustment','isolated-digest')
    gateway.journal.hold('order:isolated',case_id,'unknown')
    transition_case(store,case,CaseStatus.RECOVERY_REQUIRED,'isolated interruption before first money write')
    case['team_run'].update(status='FAILED',updated_at='2020-01-01T00:00:00Z');store.save_case(case)
    (out/'recovery-before.json').write_text(json.dumps(snapshot(),indent=2)+'\n')
    with gateway.journal.transaction() as tx:
        tx.execute("""CREATE FUNCTION reject_recovery() RETURNS trigger AS $$
            BEGIN IF NEW.status='EXECUTING' THEN RAISE EXCEPTION 'isolated recovery failure';
            END IF; RETURN NEW; END; $$ LANGUAGE plpgsql;
            CREATE TRIGGER reject_recovery BEFORE INSERT ON cases FOR EACH ROW EXECUTE FUNCTION reject_recovery();""")
    print('Prepared isolated failed recovery commit with a RUNNING task and channel hold')
