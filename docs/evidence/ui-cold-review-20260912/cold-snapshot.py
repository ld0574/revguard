import hashlib,json
from collections import Counter
from revguard.api import store,gateway
cases=store.list_cases()
def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,default=str).encode()).hexdigest()
print(json.dumps({'case_count':len(cases),'statuses':dict(Counter(c['status'] for c in cases)),'case_hashes':{c['case_id']:digest(c) for c in cases},'execution_hashes':{c['case_id']:digest(store.list_executions(c['case_id'])) for c in cases},'ledger_count':len(gateway._ledger),'ledger_hash':digest(gateway._ledger),'audit_events_hash':digest({c['case_id']:store.list_audit(c['case_id']) for c in cases})},indent=2))
