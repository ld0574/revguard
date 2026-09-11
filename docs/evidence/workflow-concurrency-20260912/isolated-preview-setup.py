"""Run only in isolated Docker; injects approval DB failure or a lost worker handoff."""
import asyncio, os, time, urllib.request
for _ in range(30):
    try:
        urllib.request.urlopen('http://127.0.0.1:9000/api/v1/health', timeout=2).close(); break
    except Exception: time.sleep(1)
from revguard.api import store, gateway, _mcp_team
from scripts.seed_demo import seed_store
case_id = 'CASE-2026-0008'
phase = os.environ.get('PHASE','failure')
if phase == 'allow':
    with gateway.journal.transaction() as tx:
        tx.execute('DROP TRIGGER reject_approval ON cases')
    print('Removed isolated approval-write failure')
else:
    seed_store(store, quiet=True)
    case = store.get_case(case_id)
    if phase in {'waiting','recovery'}:
        from scripts.seed_demo import load_golden_case
        from revguard.models import new_id
        fresh = load_golden_case(case_id); fresh['recording_id'] = new_id('REC')
        gateway.reprepare_case(fresh, expected_case=case, actor='fixture')
        case = store.get_case(case_id)
    asyncio.run(_mcp_team().run_to_human_gate(case))
    case = store.get_case(case_id)
    assert case['status'] == 'WAITING_FOR_APPROVAL'
    if phase == 'failure':
        with gateway.journal.transaction() as tx:
            tx.execute("""CREATE FUNCTION reject_approval() RETURNS trigger AS $$
                BEGIN IF NEW.status='READY_TO_EXECUTE' THEN RAISE EXCEPTION 'isolated approval failure';
                END IF; RETURN NEW; END; $$ LANGUAGE plpgsql;
                CREATE TRIGGER reject_approval BEFORE INSERT ON cases
                FOR EACH ROW EXECUTE FUNCTION reject_approval();""")
    elif phase == 'recovery':
        approval = store.get_approval(case_id)
        gateway.decide_case_approval(case, {'approval_id':approval['approval_id'], 'decision':'APPROVED',
            'human_subject':'@finance:isolated', 'human_display_name':'隔离测试审批人'}, actor='finance.lead', assertion_ref='fixture-only')
        case['team_run'].update(status='FAILED', error={'message':'隔离模拟：审批已提交后执行进程未启动'})
        store.save_case(case)
    print('Prepared isolated scenario:', phase)
