from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone

from cascadia.durability.idempotency import IdempotencyManager
from cascadia.durability.resume_manager import ResumeManager
from cascadia.durability.run_store import RunStore
from cascadia.durability.step_journal import StepJournal
from cascadia.system.approval_store import ApprovalStore


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = RunStore(f"{self.tempdir.name}/test.db")
        self.journal = StepJournal(self.store)
        self.idempotency = IdempotencyManager(self.store)
        self.resume = ResumeManager(self.store, self.journal, self.idempotency)
        self.approvals = ApprovalStore(self.store)
        self.run_id = 'run_test'
        self.store.create_run({'run_id': self.run_id, 'operator_id': 'scout', 'tenant_id': 'default', 'goal': 'test', 'current_step': 'parse_lead', 'input_snapshot': {'email': 'lead@example.com'}, 'state_snapshot': {'email': 'lead@example.com'}, 'retry_count': 0, 'last_checkpoint': None, 'process_state': 'ready', 'run_state': 'running', 'created_at': now(), 'updated_at': now()})

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_resume_from_step_3(self) -> None:
        for idx in range(3):
            self.journal.append_step(run_id=self.run_id, step_name=f'step_{idx}', step_index=idx, started_at=now(), completed_at=now(), input_state={'i': idx}, output_state={'i': idx + 1})
            key = f'fx_{idx}'
            self.idempotency.register_planned(run_id=self.run_id, step_index=idx, effect_type='noop', effect_key=key, target='x', payload={}, created_at=now())
            self.idempotency.commit(key, now())
        result = self.resume.determine_resume_point(self.run_id)
        self.assertTrue(result['can_resume'])
        self.assertEqual(result['resume_step_index'], 3)
        self.assertEqual(result['last_committed_step_index'], 2)

    def test_planned_not_committed(self) -> None:
        self.journal.append_step(run_id=self.run_id, step_name='prep', step_index=0, started_at=now(), completed_at=now(), input_state={}, output_state={'prepared': True})
        self.idempotency.register_planned(run_id=self.run_id, step_index=0, effect_type='noop', effect_key='ek0', target='x', payload={}, created_at=now())
        self.idempotency.commit('ek0', now())
        self.journal.append_step(run_id=self.run_id, step_name='send_email', step_index=1, started_at=now(), completed_at=now(), input_state={}, output_state={'done': False})
        self.idempotency.register_planned(run_id=self.run_id, step_index=1, effect_type='email.send', effect_key='ek2', target='lead@example.com', payload={}, created_at=now())
        result = self.resume.determine_resume_point(self.run_id)
        self.assertEqual(result['resume_step_index'], 1)

    def test_approval_blocks_resume(self) -> None:
        self.approvals.request_approval(self.run_id, 2, 'email.send')
        result = self.resume.determine_resume_point(self.run_id)
        self.assertFalse(result['can_resume'])
        self.assertEqual(result['reason'], 'waiting_for_approval')

if __name__ == '__main__':
    unittest.main()
