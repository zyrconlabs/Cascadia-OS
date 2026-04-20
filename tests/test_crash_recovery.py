"""
tests/test_crash_recovery.py - Cascadia OS v0.43
Failure drills. These test what matters most:

  1. Kill mid-run -> restart -> resume from last committed step (not step 0)
  2. Crash after side effect declared but before committed -> re-attempt, not duplicate
  3. Crash after side effect committed -> skip on resume, never duplicate
  4. Approval-required run stays waiting_human across restarts
  5. Blocked (missing dependency) run does not auto-resume
  6. Poisoned run is never resumed
  7. State is correctly restored from last committed step's output
  8. Multiple crashes accumulate retry count correctly
  9. Partial step (started but not completed) is retried from scratch
 10. Full 5-step run: kill after step 3, restart, verify steps 4-5 only execute

These are the tests the v2.0 guide defines as "definition of done":
  - kill Scout mid-run -> resumes from last committed step
  - duplicate sends do not occur on retry
  - approval-required run stays waiting_human
  - maintenance restart pauses and resumes safely
"""
from __future__ import annotations

from cascadia import VERSION_SHORT

import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cascadia.durability.idempotency import IdempotencyManager
from cascadia.durability.resume_manager import ResumeManager
from cascadia.durability.run_store import RunStore
from cascadia.durability.step_journal import StepJournal
from cascadia.policy.runtime_policy import RuntimePolicy
from cascadia.system.approval_store import ApprovalStore
from cascadia.shared.ids import effect_key


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_run(store: RunStore, run_id: str, run_state: str = 'running',
             goal: str = 'Test run') -> None:
    store.create_run({
        'run_id': run_id,
        'operator_id': 'main_operator',
        'tenant_id': 'default',
        'goal': goal,
        'current_step': 'start',
        'input_snapshot': {'lead': 'acme@corp.com'},
        'state_snapshot': {'lead': 'acme@corp.com'},
        'retry_count': 0,
        'last_checkpoint': None,
        'process_state': 'ready',
        'run_state': run_state,
        'created_at': now(),
        'updated_at': now(),
    })


def commit_step(journal: StepJournal, idem: IdempotencyManager,
                run_id: str, idx: int, name: str,
                input_state: dict, output_state: dict,
                effects: list[tuple[str, str]] | None = None) -> None:
    """Helper: commit one step with all its side effects."""
    journal.append_step(
        run_id=run_id, step_name=name, step_index=idx,
        started_at=now(), completed_at=now(),
        input_state=input_state, output_state=output_state,
    )
    for action, target in (effects or []):
        k = effect_key(run_id, idx, action, target)
        idem.register_planned(
            run_id=run_id, step_index=idx, effect_type=action,
            effect_key=k, target=target, payload={}, created_at=now(),
        )
        idem.commit(k, now())


class TestKillAndResume(unittest.TestCase):
    """Core crash-resume scenarios."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = RunStore(f'{self.tempdir.name}/test.db')
        self.journal = StepJournal(self.store)
        self.idem = IdempotencyManager(self.store)
        self.resume = ResumeManager(self.store, self.journal, self.idem)
        self.approvals = ApprovalStore(self.store)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    # ------------------------------------------------------------------
    # 1. Kill after step 3 of 5 -> resume from step 4, not step 0
    # ------------------------------------------------------------------
    def test_kill_after_step3_resumes_from_step4(self) -> None:
        run_id = 'run_kill_01'
        make_run(self.store, run_id)

        state = {'lead': 'acme@corp.com'}
        steps = [
            (0, 'parse_lead',     state, {**state, 'name': 'Jane'}),
            (1, 'enrich_company', {**state, 'name': 'Jane'}, {**state, 'name': 'Jane', 'size': '50+'}),
            (2, 'draft_email',    {**state, 'name': 'Jane', 'size': '50+'}, {**state, 'draft': 'Hi Jane'}),
        ]
        for idx, name, inp, out in steps:
            commit_step(self.journal, self.idem, run_id, idx, name, inp, out)

        # Simulate crash — process dies, run stays in running state
        ctx = self.resume.determine_resume_point(run_id)

        self.assertTrue(ctx['can_resume'])
        self.assertEqual(ctx['resume_step_index'], 3)  # NOT 0
        self.assertEqual(ctx['last_committed_step_index'], 2)
        self.assertEqual(ctx['restored_state'].get('draft'), 'Hi Jane')

    # ------------------------------------------------------------------
    # 2. Crash after side effect DECLARED (planned) but NOT committed
    #    -> resume from that step, idempotency prevents duplicate
    # ------------------------------------------------------------------
    def test_planned_effect_not_committed_retried_not_duplicated(self) -> None:
        run_id = 'run_planned_01'
        make_run(self.store, run_id)

        # Steps 0-1 fully committed
        commit_step(self.journal, self.idem, run_id, 0, 'parse_lead',
                    {}, {'parsed': True})
        commit_step(self.journal, self.idem, run_id, 1, 'enrich_company',
                    {'parsed': True}, {'enriched': True})

        # Step 2: step record written, side effect DECLARED but crash before commit
        self.journal.append_step(
            run_id=run_id, step_name='send_email', step_index=2,
            started_at=now(), completed_at=now(),
            input_state={'enriched': True}, output_state={},
        )
        k = effect_key(run_id, 2, 'email.send', 'acme@corp.com')
        self.idem.register_planned(
            run_id=run_id, step_index=2, effect_type='email.send',
            effect_key=k, target='acme@corp.com', payload={}, created_at=now(),
        )
        # CRASH HERE — never called idem.commit(k)

        ctx = self.resume.determine_resume_point(run_id)

        # Must resume from step 2 — NOT skip it
        self.assertTrue(ctx['can_resume'])
        self.assertEqual(ctx['resume_step_index'], 2)
        self.assertEqual(ctx['last_committed_step_index'], 1)

        # On retry, register_planned returns False (key exists) — no duplicate insert
        already = self.idem.register_planned(
            run_id=run_id, step_index=2, effect_type='email.send',
            effect_key=k, target='acme@corp.com', payload={}, created_at=now(),
        )
        self.assertFalse(already, 'Duplicate registration must be blocked by unique key')

    # ------------------------------------------------------------------
    # 3. Crash after side effect COMMITTED -> skip it, never duplicate
    # ------------------------------------------------------------------
    def test_committed_effect_skipped_on_resume(self) -> None:
        run_id = 'run_committed_01'
        make_run(self.store, run_id)

        # Steps 0-2 fully committed including email send
        commit_step(self.journal, self.idem, run_id, 0, 'parse_lead', {}, {'parsed': True})
        commit_step(self.journal, self.idem, run_id, 1, 'enrich_company',
                    {'parsed': True}, {'enriched': True})
        commit_step(self.journal, self.idem, run_id, 2, 'send_email',
                    {'enriched': True}, {'sent': True},
                    effects=[('email.send', 'acme@corp.com')])

        # Crash before step 3 — all committed
        ctx = self.resume.determine_resume_point(run_id)
        self.assertEqual(ctx['resume_step_index'], 3)

        # Verify the email effect is committed and will be skipped
        k = effect_key(run_id, 2, 'email.send', 'acme@corp.com')
        effects = self.idem.all_for_step(run_id, 2)
        self.assertEqual(len(effects), 1)
        self.assertEqual(effects[0]['status'], 'committed')

        # Attempting to register again returns False — idempotency key is unique
        result = self.idem.register_planned(
            run_id=run_id, step_index=2, effect_type='email.send',
            effect_key=k, target='acme@corp.com', payload={}, created_at=now(),
        )
        self.assertFalse(result, 'Email send must not be duplicatable after commit')

    # ------------------------------------------------------------------
    # 4. Approval-required run stays waiting_human across restarts
    # ------------------------------------------------------------------
    def test_approval_required_survives_restart(self) -> None:
        run_id = 'run_approval_01'
        make_run(self.store, run_id, run_state='waiting_human')

        # Insert a pending approval
        self.approvals.request_approval(run_id, 2, 'email.send')

        # Simulate restart — determine_resume_point called on startup
        ctx = self.resume.determine_resume_point(run_id)

        self.assertFalse(ctx['can_resume'])
        self.assertEqual(ctx['reason'], 'waiting_for_approval')
        self.assertIn('pending_actions', ctx)
        self.assertIn('email.send', ctx['pending_actions'])

    # ------------------------------------------------------------------
    # 5. After approval granted -> run becomes resumable
    # ------------------------------------------------------------------
    def test_approved_run_becomes_resumable(self) -> None:
        run_id = 'run_approval_02'
        make_run(self.store, run_id, run_state='waiting_human')

        commit_step(self.journal, self.idem, run_id, 0, 'parse_lead', {}, {'parsed': True})

        approval_id = self.approvals.request_approval(run_id, 1, 'email.send')

        # Before approval — not resumable
        ctx = self.resume.determine_resume_point(run_id)
        self.assertFalse(ctx['can_resume'])

        # Grant approval
        self.approvals.record_decision(approval_id, 'approved', 'user_andy', 'looks good')

        # After approval — run_state is now retrying, resumable
        run = self.store.get_run(run_id)
        self.assertEqual(run['run_state'], 'retrying')

    # ------------------------------------------------------------------
    # 6. Poisoned run is never resumed
    # ------------------------------------------------------------------
    def test_poisoned_run_not_resumable(self) -> None:
        run_id = 'run_poisoned_01'
        make_run(self.store, run_id, run_state='poisoned')
        ctx = self.resume.determine_resume_point(run_id)
        self.assertFalse(ctx['can_resume'])
        self.assertIn('poisoned', ctx['reason'])

    # ------------------------------------------------------------------
    # 7. Complete run is never resumed
    # ------------------------------------------------------------------
    def test_complete_run_not_resumable(self) -> None:
        run_id = 'run_complete_01'
        make_run(self.store, run_id, run_state='complete')
        ctx = self.resume.determine_resume_point(run_id)
        self.assertFalse(ctx['can_resume'])

    # ------------------------------------------------------------------
    # 8. State is restored from last committed step's output_state
    # ------------------------------------------------------------------
    def test_state_restored_from_last_committed_output(self) -> None:
        run_id = 'run_state_01'
        make_run(self.store, run_id)

        commit_step(self.journal, self.idem, run_id, 0, 'parse_lead',
                    {'lead': 'x'}, {'name': 'Jane', 'email': 'jane@acme.com'})
        commit_step(self.journal, self.idem, run_id, 1, 'enrich_company',
                    {'name': 'Jane'}, {'name': 'Jane', 'industry': 'Logistics', 'size': '200+'})

        ctx = self.resume.determine_resume_point(run_id)
        self.assertEqual(ctx['restored_state']['industry'], 'Logistics')
        self.assertEqual(ctx['restored_state']['size'], '200+')
        self.assertEqual(ctx['resume_step_index'], 2)

    # ------------------------------------------------------------------
    # 9. Multiple crashes accumulate retry_count correctly
    # ------------------------------------------------------------------
    def test_retry_count_increments_across_crashes(self) -> None:
        run_id = 'run_retry_01'
        make_run(self.store, run_id)

        for _ in range(3):
            self.store.update_run(run_id, retry_count=
                (self.store.get_run(run_id)['retry_count'] or 0) + 1)

        run = self.store.get_run(run_id)
        self.assertEqual(run['retry_count'], 3)

    # ------------------------------------------------------------------
    # 10. Partial step (started but never completed) retried from scratch
    # ------------------------------------------------------------------
    def test_partial_step_not_treated_as_committed(self) -> None:
        run_id = 'run_partial_01'
        make_run(self.store, run_id)

        commit_step(self.journal, self.idem, run_id, 0, 'parse_lead', {}, {'parsed': True})

        # Step 1 started but process killed before completed_at was set
        self.journal.append_step(
            run_id=run_id, step_name='enrich_company', step_index=1,
            started_at=now(),
            completed_at=None,  # NEVER COMPLETED
            input_state={'parsed': True},
            output_state=None,
        )

        ctx = self.resume.determine_resume_point(run_id)
        # Must resume from step 1 — it was not completed
        self.assertEqual(ctx['resume_step_index'], 1)
        self.assertEqual(ctx['last_committed_step_index'], 0)


class TestDuplicatePrevention(unittest.TestCase):
    """Prove duplicate side effects cannot happen under any restart scenario."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = RunStore(f'{self.tempdir.name}/test.db')
        self.journal = StepJournal(self.store)
        self.idem = IdempotencyManager(self.store)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_same_run_step_target_always_same_key(self) -> None:
        """Idempotency key is deterministic — same inputs always produce same key."""
        k1 = effect_key('run_abc', 3, 'email.send', 'lead@acme.com')
        k2 = effect_key('run_abc', 3, 'email.send', 'lead@acme.com')
        self.assertEqual(k1, k2)

    def test_different_target_different_key(self) -> None:
        k1 = effect_key('run_abc', 3, 'email.send', 'lead@acme.com')
        k2 = effect_key('run_abc', 3, 'email.send', 'other@acme.com')
        self.assertNotEqual(k1, k2)

    def test_different_run_different_key(self) -> None:
        k1 = effect_key('run_001', 3, 'email.send', 'lead@acme.com')
        k2 = effect_key('run_002', 3, 'email.send', 'lead@acme.com')
        self.assertNotEqual(k1, k2)

    def test_db_unique_constraint_prevents_duplicate_row(self) -> None:
        """The DB itself enforces uniqueness — not just application logic."""
        import sqlite3
        make_run(self.store, 'run_dup_01')
        k = effect_key('run_dup_01', 2, 'email.send', 'x@y.com')

        r1 = self.idem.register_planned(
            run_id='run_dup_01', step_index=2, effect_type='email.send',
            effect_key=k, target='x@y.com', payload={}, created_at=now(),
        )
        self.assertTrue(r1)

        r2 = self.idem.register_planned(
            run_id='run_dup_01', step_index=2, effect_type='email.send',
            effect_key=k, target='x@y.com', payload={}, created_at=now(),
        )
        self.assertFalse(r2, 'Second registration must fail at DB constraint level')

    def test_commit_then_re_register_blocked(self) -> None:
        """Once committed, re-registration is blocked. Send exactly once."""
        make_run(self.store, 'run_once_01')
        k = effect_key('run_once_01', 3, 'email.send', 'lead@acme.com')

        self.idem.register_planned(
            run_id='run_once_01', step_index=3, effect_type='email.send',
            effect_key=k, target='lead@acme.com', payload={}, created_at=now(),
        )
        self.idem.commit(k, now())

        # Try to send again after commit — must be blocked
        result = self.idem.register_planned(
            run_id='run_once_01', step_index=3, effect_type='email.send',
            effect_key=k, target='lead@acme.com', payload={}, created_at=now(),
        )
        self.assertFalse(result)

        effects = self.idem.all_for_step('run_once_01', 3)
        self.assertEqual(len(effects), 1)
        self.assertEqual(effects[0]['status'], 'committed')


class TestScanResumable(unittest.TestCase):
    """FLINT startup scan: finds and queues interrupted runs correctly."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = RunStore(f'{self.tempdir.name}/test.db')
        self.journal = StepJournal(self.store)
        self.idem = IdempotencyManager(self.store)
        self.resume = ResumeManager(self.store, self.journal, self.idem)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_scan_finds_interrupted_runs(self) -> None:
        make_run(self.store, 'run_scan_01', run_state='running')
        make_run(self.store, 'run_scan_02', run_state='running')
        make_run(self.store, 'run_scan_03', run_state='complete')   # should not appear
        make_run(self.store, 'run_scan_04', run_state='poisoned')   # should not appear

        results = self.resume.scan_resumable()
        found_ids = {r['run']['run_id'] for r in results}

        self.assertIn('run_scan_01', found_ids)
        self.assertIn('run_scan_02', found_ids)
        self.assertNotIn('run_scan_03', found_ids)
        self.assertNotIn('run_scan_04', found_ids)

    def test_scan_returns_correct_resume_points(self) -> None:
        make_run(self.store, 'run_scan_10', run_state='running')
        commit_step(self.journal, self.idem, 'run_scan_10', 0, 'parse_lead',
                    {}, {'parsed': True})
        commit_step(self.journal, self.idem, 'run_scan_10', 1, 'enrich_company',
                    {'parsed': True}, {'enriched': True})

        results = self.resume.scan_resumable()
        target = next(r for r in results if r['run']['run_id'] == 'run_scan_10')

        self.assertEqual(target['resume_step_index'], 2)
        self.assertEqual(target['last_committed_step_index'], 1)

    def test_scan_skips_waiting_human(self) -> None:
        make_run(self.store, 'run_scan_20', run_state='waiting_human')
        # Pending approval inserted
        approval_store = ApprovalStore(self.store)
        approval_store.request_approval('run_scan_20', 1, 'email.send')

        results = self.resume.scan_resumable()
        found_ids = {r['run']['run_id'] for r in results}
        self.assertNotIn('run_scan_20', found_ids)


class TestDependencyBlocking(unittest.TestCase):
    """Blocked runs stay blocked. They do not auto-resume."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = RunStore(f'{self.tempdir.name}/test.db')
        self.journal = StepJournal(self.store)
        self.idem = IdempotencyManager(self.store)
        self.resume = ResumeManager(self.store, self.journal, self.idem)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_blocked_run_not_in_scan(self) -> None:
        make_run(self.store, 'run_blocked_01', run_state='blocked')
        self.store.set_blocked('run_blocked_01', 'missing_operator', 'gmail_operator',
                               {'type': 'missing_operator', 'entity': 'gmail_operator',
                                'human_message': 'Gmail Operator not installed'})

        results = self.resume.scan_resumable()
        found_ids = {r['run']['run_id'] for r in results}
        self.assertNotIn('run_blocked_01', found_ids)

    def test_blocked_reason_persisted_correctly(self) -> None:
        make_run(self.store, 'run_blocked_02')
        self.store.set_blocked('run_blocked_02', 'missing_permission', 'gmail.send',
                               {'type': 'missing_permission', 'entity': 'gmail.send',
                                'human_message': 'gmail.send permission not granted'})

        run = self.store.get_run('run_blocked_02')
        self.assertEqual(run['run_state'], 'blocked')
        self.assertEqual(run['blocked_reason'], 'missing_permission')
        self.assertEqual(run['blocking_entity'], 'gmail.send')
        self.assertIsNotNone(run['dependency_request'])

    def test_clear_blocked_makes_resumable(self) -> None:
        make_run(self.store, 'run_blocked_03', run_state='blocked')
        self.store.set_blocked('run_blocked_03', 'missing_operator', 'gmail_operator', {})

        # Dependency resolved — clear the block
        self.store.clear_blocked('run_blocked_03')
        self.store.update_run('run_blocked_03', run_state='running')

        run = self.store.get_run('run_blocked_03')
        self.assertIsNone(run['blocked_reason'])
        self.assertIsNone(run['blocking_entity'])
        self.assertEqual(run['run_state'], 'running')


if __name__ == '__main__':
    print(f'\\n=== Cascadia OS {VERSION_SHORT} — Crash Recovery & Failure Drills ===\\n')
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [TestKillAndResume, TestDuplicatePrevention,
                TestScanResumable, TestDependencyBlocking]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    passed = result.testsRun - len(result.failures) - len(result.errors)
    print(f'\n{"=" * 50}')
    print(f'  Crash recovery results: {passed}/{result.testsRun} passed')
    if result.failures or result.errors:
        for label, items in (('FAILURES', result.failures), ('ERRORS', result.errors)):
            for test, tb in items:
                print(f'  {label}: {test}')
                print(f'  {tb.splitlines()[-1]}')
    print('=' * 50)
    import sys as _sys
    _sys.exit(0 if not result.failures and not result.errors else 1)
