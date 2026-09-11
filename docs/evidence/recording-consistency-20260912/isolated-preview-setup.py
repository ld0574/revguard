import json, time, urllib.request, urllib.error
for _ in range(30):
    try:
        with urllib.request.urlopen('http://127.0.0.1:9000/api/v1/health', timeout=2) as response:
            if json.load(response).get('ready'): break
    except (urllib.error.URLError, TimeoutError): pass
    time.sleep(1)
else: raise RuntimeError('isolated preview not ready')
from revguard.api import store, gateway, OUTPUT_DIR, REPORT_DIR
from revguard.orchestrator import Orchestrator
from scripts.seed_demo import seed_store
seed_store(store, quiet=True)
case = store.get_case('CASE-2026-0008')
Orchestrator(store, gateway, output_dir=OUTPUT_DIR, report_dir=REPORT_DIR, approval_mode='auto').run_case(case)
assert store.get_case(case['case_id'])['status'] == 'CLOSED'
assert len(store.list_executions(case['case_id'])) == 2
with gateway.journal.transaction() as tx:
    tx.execute("""CREATE FUNCTION reject_recording_insert() RETURNS trigger AS $$
        BEGIN IF NEW.status='CREATED' THEN RAISE EXCEPTION 'isolated browser fault';
        END IF; RETURN NEW; END; $$ LANGUAGE plpgsql;
        CREATE TRIGGER reject_recording BEFORE INSERT ON cases
        FOR EACH ROW EXECUTE FUNCTION reject_recording_insert();""")
print('Isolated preview seeded with a real closed workflow and a test-only DB trigger')
