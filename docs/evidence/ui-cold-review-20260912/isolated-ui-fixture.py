"""Isolated UI review: real MCP workflows, synthetic trusted approval fixture."""
import asyncio,json,os,time,urllib.request
from decimal import Decimal
from pathlib import Path
for _ in range(30):
 try:
  urllib.request.urlopen('http://127.0.0.1:9000/api/v1/health',timeout=2).close();break
 except Exception:time.sleep(1)
from revguard.api import store,gateway,_mcp_team
from scripts.seed_demo import seed_store
seed_store(store,quiet=True)
phase=os.environ.get('PHASE','pending')
async def run():
 if phase=='pending':
  for cid in ['CASE-2026-0001','CASE-2026-0002','CASE-2026-0008']:
   await _mcp_team().run_to_human_gate(store.get_case(cid))
 else:
  for cid in ['CASE-2026-0001','CASE-2026-0002','CASE-2026-0008']:
   case=store.get_case(cid);approval=store.get_approval(cid)
   decided=gateway.decide_case_approval(case,{'approval_id':approval['approval_id'],'decision':'REJECTED' if cid.endswith('2') else 'APPROVED','human_subject':'@finance:isolated','human_display_name':'隔离测试审批人'},actor='finance.lead',assertion_ref='isolated-ui-review')
   if cid.endswith('2'):
    await _mcp_team().finalize_terminal(case,approval=decided)
   else:
    gateway._posting_tamper_amount=Decimal('1') if cid.endswith('8') else Decimal('0')
    await _mcp_team().execute_after_approval(case)
asyncio.run(run())
result={c['case_id']:c['status'] for c in store.list_cases()}
Path('/evidence').mkdir(exist_ok=True)
Path('/evidence/fixture-'+phase+'.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
