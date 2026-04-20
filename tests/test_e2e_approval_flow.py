"""
tests/test_e2e_approval_flow.py - Cascadia OS v0.43
# MATURITY: PRODUCTION — End-to-end approval flow tests under restart conditions.

Proves the complete lifecycle for approval-gated runs:
  blocked -> waiting_human -> approved -> retrying -> complete

And the failure branch:
  blocked -> waiting_human -> denied -> failed

And restart safety:
  Run restarted while waiting_human -> still waiting_human, not auto-resumed
  Run restarted after approval -> resumes from correct step

These are the scenarios the reviewer asked for specifically.
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
from cascadia.shared.ids import effect_key
from cascadia.system.approval_store import ApprovalStore
from cascadia.system.dependency_manager import DependencyManager
from cascadia.shared.manifest_schema import validate_manifest


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_store(tempdir: str) -> tuple:
    store = RunStore(f'{tempdir}/cascadia.db')
    journal = StepJournal(store)
    idem = IdempotencyManager(store)
    approvals = ApprovalStore(store)
    resume = ResumeManager(store, journal, idem)
    return store, journal, idem, approvals, resume


def seed_run(store: RunStore, run_id: str, run_state: str = 'running') -> None:
    store.create_run({
        'run_id': run_id, 'operator_id': 'gmail_operator',
        'tenant_id': 'acme', 'goal': 'Send follow-up to lead',
        'current_step': 'draft_email',
        'input_snapshot': {'lead': 'cto@acme.com', 'company': 'Acme Corp'},
        'state_snapshot': {'lead': 'cto@acme.com', 'company': 'Acme Corp'},
        'retry_count': 0, 'last_checkpoint': None,
        'process_state': 'ready', 'run_state': run_state,
        'created_at': now(), 'updated_at': now(),
    })


def commit_steps_up_to(journal: StepJournal, idem: IdempotencyManager,
                       run_id: str, through_index: int) -> dict:
    """Commit steps 0..through_index and return final state."""
    steps = [
        (0, 'parse_lead',     {}, {'name': 'CTO', 'email': 'cto@acme.com'}),
        (1, 'enrich_company', {'name': 'CTO'}, {'name': 'CTO', 'industry': 'Logistics'}),
        (2, 'draft_email',    {'name': 'CTO', 'industry': 'Logistics'},
                              {'name': 'CTO', 'industry': 'Logistics', 'draft': 'Hi CTO'}),
    ]
    state = {}
    for idx, name, inp, out in steps:
        if idx > through_index:
            break
        k = effect_key(run_id, idx, 'noop', 'x')
        journal.append_step(run_id=run_id, step_name=name, step_index=idx,
                            started_at=now(), completed_at=now(),
                            input_state=inp, output_state=out)
        idem.register_planned(run_id=run_id, step_index=idx, effect_type='noop',
                              effect_key=k, target='x', payload={}, created_at=now())
        idem.commit(k, now())
        state = out
    return state


class TestApprovalFlowComplete(unittest.TestCase):
    """Full approval lifecycle: gate -> approve -> resume -> complete."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store, self.journal, self.idem, self.approvals, self.resume = \
            build_store(self.tempdir.name)
        self.policy = RuntimePolicy(
            {'email.send': 'approval_required', 'crm.write': 'allowed'},
            self.store, self.approvals,
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    # ------------------------------------------------------------------
    # 1. Complete blocked → waiting_human → approved → retrying → complete
    # ------------------------------------------------------------------
    def test_full_approval_lifecycle(self) -> None:
        run_id = 'e2e_full_01'
        seed_run(self.store, run_id)
        commit_steps_up_to(self.journal, self.idem, run_id, 2)

        # Step 3: policy gates email.send
        decision = self.policy.check(run_id=run_id, step_index=3, action='email.send')
        self.assertEqual(decision.decision, 'approval_required')

        run = self.store.get_run(run_id)
        self.assertEqual(run['run_state'], 'waiting_human')
        self.assertIsNotNone(decision.approval_id)

        # Verify approval record persisted
        pending = self.approvals.pending_approvals(run_id)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]['action_key'], 'email.send')
        self.assertEqual(pending[0]['decision'], 'pending')

        # Human approves
        self.approvals.record_decision(
            decision.approval_id, 'approved', 'andy_zyrcon', 'Approved for ACME'
        )

        # Run transitions to retrying
        run = self.store.get_run(run_id)
        self.assertEqual(run['run_state'], 'retrying')

        # Approval no longer pending
        pending = self.approvals.pending_approvals(run_id)
        self.assertEqual(len(pending), 0)

        # Resume from step 3 (not 0)
        ctx = self.resume.determine_resume_point(run_id)
        self.assertTrue(ctx['can_resume'])
        self.assertEqual(ctx['resume_step_index'], 3)
        self.assertEqual(ctx['restored_state'].get('draft'), 'Hi CTO')

        # Complete steps 3-4
        for idx, name, inp, out in [
            (3, 'send_email', ctx['restored_state'],
             {**ctx['restored_state'], 'email_sent': True}),
            (4, 'log_crm', {**ctx['restored_state'], 'email_sent': True},
             {**ctx['restored_state'], 'email_sent': True, 'crm_logged': True}),
        ]:
            self.journal.append_step(run_id=run_id, step_name=name, step_index=idx,
                                     started_at=now(), completed_at=now(),
                                     input_state=inp, output_state=out)
            if name == 'send_email':
                k = effect_key(run_id, idx, 'email.send', 'cto@acme.com')
                self.idem.register_planned(
                    run_id=run_id, step_index=idx, effect_type='email.send',
                    effect_key=k, target='cto@acme.com', payload={}, created_at=now(),
                )
                self.idem.commit(k, now())

        self.store.update_run(run_id, run_state='complete')

        # Final state
        run = self.store.get_run(run_id)
        self.assertEqual(run['run_state'], 'complete')

        # Email sent exactly once
        email_effects = self.idem.all_for_step(run_id, 3)
        committed = [e for e in email_effects if e['status'] == 'committed']
        self.assertEqual(len(committed), 1)

    # ------------------------------------------------------------------
    # 2. Approval required, run killed, restart — still waiting_human
    # ------------------------------------------------------------------
    def test_restart_while_waiting_preserves_gate(self) -> None:
        run_id = 'e2e_restart_01'
        seed_run(self.store, run_id)
        commit_steps_up_to(self.journal, self.idem, run_id, 2)

        decision = self.policy.check(run_id=run_id, step_index=3, action='email.send')
        self.assertEqual(run_id, self.store.get_run(run_id)['run_id'])

        # PROCESS KILLED HERE — simulate by checking state on restart
        # Startup scan must NOT auto-resume this run
        scan_results = self.resume.scan_resumable()
        scanned_ids = {r['run']['run_id'] for r in scan_results}
        self.assertNotIn(run_id, scanned_ids,
                         'Waiting-human run must not appear in startup resume scan')

        # determine_resume_point must refuse
        ctx = self.resume.determine_resume_point(run_id)
        self.assertFalse(ctx['can_resume'])
        self.assertEqual(ctx['reason'], 'waiting_for_approval')
        self.assertIn('email.send', ctx['pending_actions'])

    # ------------------------------------------------------------------
    # 3. Approval granted, process killed before execution, restart resumes correctly
    # ------------------------------------------------------------------
    def test_approved_then_killed_resumes_from_correct_step(self) -> None:
        run_id = 'e2e_approved_kill_01'
        seed_run(self.store, run_id)
        commit_steps_up_to(self.journal, self.idem, run_id, 2)

        decision = self.policy.check(run_id=run_id, step_index=3, action='email.send')
        self.approvals.record_decision(
            decision.approval_id, 'approved', 'andy_zyrcon', 'ok'
        )

        # PROCESS KILLED after approval but before execution
        # On restart: determine_resume_point
        ctx = self.resume.determine_resume_point(run_id)
        self.assertTrue(ctx['can_resume'])
        self.assertEqual(ctx['resume_step_index'], 3)  # NOT 0

    # ------------------------------------------------------------------
    # 4. Denial flow: waiting_human → denied → failed, never resumable
    # ------------------------------------------------------------------
    def test_denial_transitions_to_failed(self) -> None:
        run_id = 'e2e_deny_01'
        seed_run(self.store, run_id)

        decision = self.policy.check(run_id=run_id, step_index=3, action='email.send')
        self.approvals.record_decision(
            decision.approval_id, 'denied', 'andy_zyrcon', 'Not appropriate'
        )

        run = self.store.get_run(run_id)
        self.assertEqual(run['run_state'], 'failed')

        ctx = self.resume.determine_resume_point(run_id)
        self.assertFalse(ctx['can_resume'])
        self.assertIn('failed', ctx['reason'])

    # ------------------------------------------------------------------
    # 5. Previously approved action does not require re-approval on retry
    # ------------------------------------------------------------------
    def test_previously_approved_action_skips_gate(self) -> None:
        run_id = 'e2e_reapprove_01'
        seed_run(self.store, run_id)

        # First attempt: approval granted
        decision = self.policy.check(run_id=run_id, step_index=3, action='email.send')
        self.approvals.record_decision(
            decision.approval_id, 'approved', 'andy_zyrcon', 'approved'
        )

        # Second check for same action — should return allowed (not require new approval)
        decision2 = self.policy.check(run_id=run_id, step_index=3, action='email.send')
        self.assertEqual(decision2.decision, 'allowed',
                         'Previously approved action must not require second approval')

    # ------------------------------------------------------------------
    # 6. Multiple gated actions: each requires its own approval
    # ------------------------------------------------------------------
    def test_multiple_gated_actions_each_need_approval(self) -> None:
        run_id = 'e2e_multi_01'
        policy = RuntimePolicy(
            {'email.send': 'approval_required', 'billing.write': 'approval_required'},
            self.store, self.approvals,
        )
        seed_run(self.store, run_id)

        d1 = policy.check(run_id=run_id, step_index=3, action='email.send')
        # First approval gates run in waiting_human
        self.store.update_run(run_id, run_state='running')  # simulate reset for second check

        d2 = policy.check(run_id=run_id, step_index=4, action='billing.write')

        self.assertEqual(d1.decision, 'approval_required')
        self.assertEqual(d2.decision, 'approval_required')
        self.assertNotEqual(d1.approval_id, d2.approval_id)

        pending = self.approvals.pending_approvals(run_id)
        actions = {p['action_key'] for p in pending}
        self.assertIn('email.send', actions)
        self.assertIn('billing.write', actions)

    # ------------------------------------------------------------------
    # 7. Approval granted for wrong run does not affect other run
    # ------------------------------------------------------------------
    def test_approval_scoped_to_run(self) -> None:
        run_a = 'e2e_scope_a'
        run_b = 'e2e_scope_b'
        seed_run(self.store, run_a)
        seed_run(self.store, run_b)

        d_a = self.policy.check(run_id=run_a, step_index=3, action='email.send')
        d_b = self.policy.check(run_id=run_b, step_index=3, action='email.send')

        # Approve run_a only
        self.approvals.record_decision(d_a.approval_id, 'approved', 'user', 'ok')

        # run_b still pending
        pending_b = self.approvals.pending_approvals(run_b)
        self.assertEqual(len(pending_b), 1)
        self.assertEqual(pending_b[0]['decision'], 'pending')

        # run_a should not require re-approval
        d_a2 = self.policy.check(run_id=run_a, step_index=3, action='email.send')
        self.assertEqual(d_a2.decision, 'allowed')


class TestBlockedToResumedFlow(unittest.TestCase):
    """Dependency blocked → resolved → resumed flow end-to-end."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store, self.journal, self.idem, self.approvals, self.resume = \
            build_store(self.tempdir.name)
        self.dep_mgr = DependencyManager(self.store)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _make_manifest(self, required_deps: list, required_perms: list):
        return validate_manifest({
            'id': 'scout_operator', 'name': 'Scout', 'version': '1.0.0',
            'type': 'skill', 'capabilities': ['email.send', 'crm.write'],
            'required_dependencies': required_deps,
            'requested_permissions': required_perms,
            'autonomy_level': 'semi_autonomous',
            'health_hook': '/health',
            'description': 'Scout lead processing operator.',
        })

    def test_blocked_then_resolved_then_resumed(self) -> None:
        """Full flow: block → dependency installed → clear block → resume scan finds it."""
        run_id = 'e2e_dep_01'
        seed_run(self.store, run_id)
        commit_steps_up_to(self.journal, self.idem, run_id, 1)

        manifest = self._make_manifest(['gmail_operator'], ['gmail.send'])

        # Block: gmail_operator not installed
        result = self.dep_mgr.check(run_id, manifest,
                                    installed_assets=set(),
                                    granted_permissions={'gmail.send'})
        self.assertIsNotNone(result)
        run = self.store.get_run(run_id)
        self.assertEqual(run['run_state'], 'blocked')

        # Not in resume scan
        scan = self.resume.scan_resumable()
        self.assertNotIn(run_id, {r['run']['run_id'] for r in scan})

        # Dependency installed — re-check clears block
        result2 = self.dep_mgr.check(run_id, manifest,
                                     installed_assets={'gmail_operator'},
                                     granted_permissions={'gmail.send'})
        self.assertIsNone(result2)

        # Manually set run_state back to running (would be done by dependency resolution handler)
        self.store.update_run(run_id, run_state='running')

        run = self.store.get_run(run_id)
        self.assertIsNone(run['blocked_reason'])
        self.assertIsNone(run['blocking_entity'])
        self.assertEqual(run['run_state'], 'running')

        # Now appears in resume scan
        scan2 = self.resume.scan_resumable()
        found = {r['run']['run_id'] for r in scan2}
        self.assertIn(run_id, found)

        # Resumes from step 2 (after last committed step 1)
        ctx = self.resume.determine_resume_point(run_id)
        self.assertTrue(ctx['can_resume'])
        self.assertEqual(ctx['resume_step_index'], 2)

    def test_missing_permission_blocks_run(self) -> None:
        run_id = 'e2e_perm_01'
        seed_run(self.store, run_id)
        manifest = self._make_manifest([], ['gmail.send', 'gmail.readonly'])

        result = self.dep_mgr.check(run_id, manifest,
                                    installed_assets=set(),
                                    granted_permissions={'gmail.readonly'})  # Missing gmail.send
        self.assertIsNotNone(result)
        run = self.store.get_run(run_id)
        self.assertEqual(run['blocked_reason'], 'missing_permission')
        self.assertEqual(run['blocking_entity'], 'gmail.send')

    def test_all_deps_present_not_blocked(self) -> None:
        run_id = 'e2e_clear_01'
        seed_run(self.store, run_id)
        manifest = self._make_manifest(['gmail_operator'], ['gmail.send'])

        result = self.dep_mgr.check(run_id, manifest,
                                    installed_assets={'gmail_operator'},
                                    granted_permissions={'gmail.send'})
        self.assertIsNone(result)

        run = self.store.get_run(run_id)
        self.assertIsNone(run['blocked_reason'])
        self.assertEqual(run['run_state'], 'running')


if __name__ == '__main__':
    print(f'\\n=== Cascadia OS {VERSION_SHORT} — End-to-End Approval & Dependency Flow Tests ===\\n')
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [TestApprovalFlowComplete, TestBlockedToResumedFlow]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    passed = result.testsRun - len(result.failures) - len(result.errors)
    print(f'\n{"=" * 55}')
    print(f'  Approval flow results: {passed}/{result.testsRun} passed')
    if result.failures or result.errors:
        for label, items in (('FAILURES', result.failures), ('ERRORS', result.errors)):
            for test, tb in items:
                print(f'  {label}: {test}')
                print(f'  {tb.splitlines()[-1]}')
    print('=' * 55)
    import sys as _sys
    _sys.exit(0 if not result.failures and not result.errors else 1)
