"""Isolated reproduction of a ledger/Store disagreement; no live services."""
import asyncio
import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from test_mcp_team import TestMcpTeamRunner
from revguard import skills
from revguard.models import CaseStatus
from revguard.state_machine import transition_case


async def main():
    fixture = TestMcpTeamRunner()
    fixture.setUp()
    try:
        runner, store, gateway = fixture.runner, fixture.store, fixture.gateway
        case_id = fixture.case['case_id']
        await runner.run_to_human_gate(fixture.case)
        approval = store.get_approval(case_id)
        decided = gateway.call(
            'workflow.decide_approval',
            {'approval_id': approval['approval_id'], 'decision': 'APPROVED'},
            case_id=case_id, actor='finance.lead', scope=['approval:decide'],
        )['data']
        store.save_approval({'case_id': case_id, **decided})
        case = store.get_case(case_id)
        transition_case(store, case, CaseStatus.READY_TO_EXECUTE,
                        'Isolated synthetic probe approval', actor='finance.lead')

        original_save = store.save_execution
        injected = False

        def fail_after_reversal(execution):
            nonlocal injected
            if execution['status'] == 'ROLLED_BACK' and not injected:
                injected = True
                raise sqlite3.OperationalError('injected Store outage after ledger reversal')
            return original_save(execution)

        failure = None
        with patch.object(store, 'save_execution', side_effect=fail_after_reversal):
            try:
                await runner.execute_after_approval(case)
            except Exception as exc:
                failure = type(exc).__name__

        ledger = gateway.call(
            'finance.get_commission_ledger', {'order_id': case['order_id']},
            case_id=case_id, actor='revguard-verifier', scope=['ledger:read'],
        )['data']['entries']
        reversal = next(item for item in ledger if item.get('reversal_of'))
        original_entry = next(item for item in ledger
                              if item['ledger_id'] == reversal['reversal_of'])
        stale_execution = next(item for item in store.list_executions(case_id)
                               if item['ledger_entry']['ledger_id'] == original_entry['ledger_id'])
        renewal = gateway.call(
            'workflow.renew_rollback_capability',
            {'case_id': case_id, 'ledger_id': original_entry['ledger_id'],
             'action_id': stale_execution['action_id']},
            case_id=case_id, actor='finance.lead', scope=['approval:decide'],
        )
        replay = gateway.call(
            'commission.reverse_adjustment',
            {'case_id': case_id, 'ledger_id': original_entry['ledger_id'],
             'rollback_token': stale_execution['rollback_token']},
            case_id=case_id, actor='revguard-executor', scope=['commission:reverse'],
            idempotency_key=f"{case_id}:{stale_execution['component']}:rollback",
        )
        output = {
            'scope': 'local synthetic MCP harness; injected Store method failure, not a real PolarDB failover',
            'failure': failure,
            'ledger_reversal_exists': bool(reversal),
            'store_execution_status': stale_execution['status'],
            'renewal_error_type': renewal['error']['type'],
            'same_key_replay_error_type': replay['error']['type'],
            'reversal_count': len([item for item in ledger if item.get('reversal_of')]),
        }
        assert output['failure'] == 'OperationalError'
        assert output['store_execution_status'] == 'SUBMITTED'
        assert output['renewal_error_type'] == 'DATA_CONFLICT'
        assert output['same_key_replay_error_type'] == 'IDEMPOTENCY_CONFLICT'

        class UncertainWriteGateway:
            def __init__(self):
                self.calls = 0

            def call(self, *args, **kwargs):
                self.calls += 1
                return {'success': False, 'data': None,
                        'error': {'type': 'TIMEOUT', 'message': 'outcome unknown', 'retryable': True},
                        'tool_receipt': 'probe'}

        uncertain = UncertainWriteGateway()
        try:
            skills.call_tool(
                uncertain, None, 'commission.reverse_adjustment', {},
                case_id='PROBE', actor='revguard-executor',
                scope=['commission:reverse'], idempotency_key='stable-probe-key',
                retry_backoff=0,
            )
        except Exception:
            pass
        output['write_timeout_attempts_without_status_query'] = uncertain.calls
        assert uncertain.calls == 3
        destination = (Path(sys.argv[1]) if len(sys.argv) > 1
                       else Path(__file__).with_name('recovery-probe.json'))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(output, ensure_ascii=False, indent=2))
    finally:
        fixture.tearDown()


asyncio.run(main())
