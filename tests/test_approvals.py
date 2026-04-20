from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone

from cascadia.durability.idempotency import IdempotencyManager
from cascadia.durability.run_store import RunStore
from cascadia.policy.runtime_policy import RuntimePolicy
from cascadia.system.approval_store import ApprovalStore


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ApprovalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = RunStore(f"{self.tempdir.name}/test.db")
        self.approvals = ApprovalStore(self.store)
        self.policy = RuntimePolicy({'email.send': 'approval_required'}, self.store, self.approvals)
        self.idempotency = IdempotencyManager(self.store)
        self.run_id = 'run_approval'
        self.store.create_run({'run_id': self.run_id, 'operator_id': 'gmail_operator', 'tenant_id': 'default', 'goal': 'approval test', 'current_step': 'send_email', 'input_snapshot': {}, 'state_snapshot': {}, 'retry_count': 0, 'last_checkpoint': None, 'process_state': 'ready', 'run_state': 'running', 'created_at': now(), 'updated_at': now()})

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_approval_required_suspends(self) -> None:
        decision = self.policy.check(run_id=self.run_id, step_index=3, action='email.send')
        run = self.store.get_run(self.run_id)
        self.assertEqual(decision.decision, 'approval_required')
        self.assertEqual(run['run_state'], 'waiting_human')
        self.assertEqual(len(self.approvals.pending_approvals(self.run_id)), 1)

    def test_approve_resumes_run(self) -> None:
        decision = self.policy.check(run_id=self.run_id, step_index=3, action='email.send')
        self.approvals.record_decision(decision.approval_id, 'approved', 'user_1', 'looks good')
        run = self.store.get_run(self.run_id)
        self.assertEqual(run['run_state'], 'retrying')

    def test_deny_fails_run(self) -> None:
        decision = self.policy.check(run_id=self.run_id, step_index=3, action='email.send')
        self.approvals.record_decision(decision.approval_id, 'denied', 'user_1', 'no')
        run = self.store.get_run(self.run_id)
        self.assertEqual(run['run_state'], 'failed')

    def test_no_duplicate_after_approve(self) -> None:
        decision = self.policy.check(run_id=self.run_id, step_index=3, action='email.send')
        self.approvals.record_decision(decision.approval_id, 'approved', 'user_1', 'ok')
        self.assertTrue(self.idempotency.register_planned(run_id=self.run_id, step_index=3, effect_type='email.send', effect_key='k1', target='lead@example.com', payload={}, created_at=now()))
        self.idempotency.commit('k1', now())
        self.assertFalse(self.idempotency.register_planned(run_id=self.run_id, step_index=3, effect_type='email.send', effect_key='k1', target='lead@example.com', payload={}, created_at=now()))

if __name__ == '__main__':
    unittest.main()
