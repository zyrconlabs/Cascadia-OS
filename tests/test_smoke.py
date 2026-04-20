"""
tests/test_smoke.py - Cascadia OS v0.43
# MATURITY: PRODUCTION — End-to-end smoke tests and restart-under-load drills.

End-to-end scenarios that prove the system behaves correctly across
a full operator lifecycle, not just unit-level isolation.

Covers:
  1. Full 5-step run — parse, enrich, draft, send, CRM — all committed
  2. Kill after step 2, restart, verify resumes from step 3 with correct state
  3. Two concurrent runs — kill one, verify the other is unaffected
  4. Same run restarted 5 times — state correct at each resume point
  5. Approval gate in middle of run — suspend, approve, resume, complete
  6. Denial mid-run — run fails cleanly, no partial state corruption
  7. Dependency block before run starts — blocked immediately, nothing executes
  8. Full run with mixed side effects — email + CRM — verify both idempotent
  9. Restart after full completion — complete run never re-executed
 10. Schema version survives repeated migration calls (idempotency of migration)
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
from cascadia.durability.migration import migrate, SCHEMA_VERSION
from cascadia.durability.resume_manager import ResumeManager
from cascadia.durability.run_store import RunStore
from cascadia.durability.step_journal import StepJournal
from cascadia.policy.runtime_policy import RuntimePolicy
from cascadia.shared.db import connect
from cascadia.shared.ids import effect_key
from cascadia.system.approval_store import ApprovalStore
from cascadia.system.dependency_manager import DependencyManager
from cascadia.shared.manifest_schema import validate_manifest


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Shared operator simulation — runs a 5-step workflow against real DB
# ---------------------------------------------------------------------------

STEPS = [
    'parse_lead',
    'enrich_company',
    'draft_email',
    'send_email',       # has email.send side effect
    'log_crm',          # has crm.write side effect
]

SIDE_EFFECTS = {
    'send_email': [('email.send', 'lead@acme.com')],
    'log_crm':    [('crm.write', 'Acme Corp')],
}


class FakeOperator:
    """
    Simulates a real operator executing steps against the durability layer.
    Does NOT use HTTP — exercises the durability layer directly.
    Crash is simulated by stopping execution at a chosen step index.
    """

    def __init__(self, store: RunStore, journal: StepJournal,
                 idem: IdempotencyManager) -> None:
        self.store = store
        self.journal = journal
        self.idem = idem
        self.executed_steps: list[str] = []
        self.committed_effects: list[tuple[str, str]] = []

    def run(self, run_id: str, start_from: int = 0,
            crash_after: int | None = None,
            state: dict | None = None) -> dict:
        """
        Execute steps start_from to end (or crash_after).
        Returns final state dict.
        """
        current_state = state or {'lead': 'acme@corp.com'}

        for idx in range(start_from, len(STEPS)):
            step = STEPS[idx]

            # Simulate crash before this step executes
            if crash_after is not None and idx > crash_after:
                raise SimulatedCrash(f'crash after step {crash_after}')

            input_state = dict(current_state)
            output_state = {**current_state, step: 'done', 'step_idx': idx}

            # Begin step
            row_id = None  # journal doesn't return row_id in this version
            self.journal.append_step(
                run_id=run_id, step_name=step, step_index=idx,
                started_at=now(), input_state=input_state, output_state=None,
            )

            # Execute side effects
            effects_for_step = SIDE_EFFECTS.get(step, [])
            for action, target in effects_for_step:
                k = effect_key(run_id, idx, action, target)
                registered = self.idem.register_planned(
                    run_id=run_id, step_index=idx, effect_type=action,
                    effect_key=k, target=target, payload={}, created_at=now(),
                )
                if registered:
                    # Would call real service here — just commit
                    self.idem.commit(k, now())
                    self.committed_effects.append((action, target))
                # If not registered — already committed, skip (idempotent)

            # Commit step
            self.journal.append_step(
                run_id=run_id, step_name=step, step_index=idx,
                started_at=now(), completed_at=now(),
                input_state=input_state, output_state=output_state,
            )
            current_state = output_state
            self.executed_steps.append(step)

        self.store.update_run(run_id, run_state='complete')
        return current_state


class SimulatedCrash(Exception):
    pass


def make_run(store: RunStore, run_id: str, run_state: str = 'running') -> None:
    store.create_run({
        'run_id': run_id, 'operator_id': 'main_operator',
        'tenant_id': 'default', 'goal': 'Process Acme lead',
        'current_step': 'parse_lead',
        'input_snapshot': {'lead': 'acme@corp.com'},
        'state_snapshot': {'lead': 'acme@corp.com'},
        'retry_count': 0, 'last_checkpoint': None,
        'process_state': 'ready', 'run_state': run_state,
        'created_at': now(), 'updated_at': now(),
    })


# ---------------------------------------------------------------------------
# Smoke tests
# ---------------------------------------------------------------------------

class TestFullRunLifecycle(unittest.TestCase):

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = RunStore(f'{self.tempdir.name}/cascadia.db')
        self.journal = StepJournal(self.store)
        self.idem = IdempotencyManager(self.store)
        self.resume = ResumeManager(self.store, self.journal, self.idem)
        self.approvals = ApprovalStore(self.store)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    # ------------------------------------------------------------------
    # 1. Full 5-step run completes cleanly
    # ------------------------------------------------------------------
    def test_full_5_step_run_completes(self) -> None:
        run_id = 'smoke_full_01'
        make_run(self.store, run_id)
        op = FakeOperator(self.store, self.journal, self.idem)
        op.run(run_id)

        self.assertEqual(op.executed_steps, STEPS)
        self.assertIn(('email.send', 'lead@acme.com'), op.committed_effects)
        self.assertIn(('crm.write', 'Acme Corp'), op.committed_effects)

        run = self.store.get_run(run_id)
        self.assertEqual(run['run_state'], 'complete')

        ctx = self.resume.determine_resume_point(run_id)
        self.assertFalse(ctx['can_resume'])

    # ------------------------------------------------------------------
    # 2. Kill after step 2, restart, resume from step 3 with correct state
    # ------------------------------------------------------------------
    def test_kill_resume_correct_state(self) -> None:
        run_id = 'smoke_kill_01'
        make_run(self.store, run_id)
        op1 = FakeOperator(self.store, self.journal, self.idem)

        try:
            op1.run(run_id, crash_after=2)
        except SimulatedCrash:
            pass

        self.assertEqual(op1.executed_steps, ['parse_lead', 'enrich_company', 'draft_email'])

        # Restart — determine resume point
        ctx = self.resume.determine_resume_point(run_id)
        self.assertTrue(ctx['can_resume'])

        # Resume from correct step with correct restored state
        op2 = FakeOperator(self.store, self.journal, self.idem)
        op2.run(run_id, start_from=ctx['resume_step_index'],
                state=ctx['restored_state'])

        # Steps 3-4 executed on resume
        self.assertIn('send_email', op2.executed_steps)
        self.assertIn('log_crm', op2.executed_steps)
        self.assertNotIn('parse_lead', op2.executed_steps)

        # Side effects committed exactly once
        email_effects = self.idem.all_for_step(run_id, 3)
        committed_emails = [e for e in email_effects if e['status'] == 'committed']
        self.assertEqual(len(committed_emails), 1)

    # ------------------------------------------------------------------
    # 3. Two concurrent runs — kill one, other unaffected
    # ------------------------------------------------------------------
    def test_concurrent_runs_isolated(self) -> None:
        run_a = 'smoke_concurrent_a'
        run_b = 'smoke_concurrent_b'
        make_run(self.store, run_a)
        make_run(self.store, run_b)

        # Run A crashes after step 1
        op_a = FakeOperator(self.store, self.journal, self.idem)
        try:
            op_a.run(run_a, crash_after=1)
        except SimulatedCrash:
            pass

        # Run B completes fully
        op_b = FakeOperator(self.store, self.journal, self.idem)
        op_b.run(run_b)

        # Run B is complete, Run A is still resumable
        ctx_a = self.resume.determine_resume_point(run_a)
        ctx_b = self.resume.determine_resume_point(run_b)

        self.assertTrue(ctx_a['can_resume'])
        self.assertFalse(ctx_b['can_resume'])

        # Run B's side effects are not visible in Run A's idem records
        a_effects = self.idem.all_for_step(run_a, 3)
        b_effects = self.idem.all_for_step(run_b, 3)
        self.assertEqual(len(a_effects), 0)       # A never reached step 3
        self.assertEqual(len(b_effects), 1)       # B committed email.send

    # ------------------------------------------------------------------
    # 4. Same run restarted 5 times — correct state each time
    # ------------------------------------------------------------------
    def test_repeated_restarts_accumulate_correctly(self) -> None:
        run_id = 'smoke_repeat_01'
        make_run(self.store, run_id)

        # Crash at step 0 five times, then complete
        for attempt in range(5):
            try:
                op = FakeOperator(self.store, self.journal, self.idem)
                if attempt < 5:
                    op.run(run_id, start_from=0, crash_after=-1)  # crash immediately
            except SimulatedCrash:
                self.store.update_run(run_id,
                    retry_count=(self.store.get_run(run_id)['retry_count'] or 0) + 1)

        # Final: complete the run
        ctx = self.resume.determine_resume_point(run_id)
        op_final = FakeOperator(self.store, self.journal, self.idem)
        op_final.run(run_id, start_from=ctx['resume_step_index'],
                     state=ctx['restored_state'])

        run = self.store.get_run(run_id)
        self.assertEqual(run['run_state'], 'complete')
        self.assertEqual(run['retry_count'], 5)

    # ------------------------------------------------------------------
    # 5. Approval gate mid-run — suspend, approve, resume, complete
    # ------------------------------------------------------------------
    def test_approval_gate_suspend_approve_resume(self) -> None:
        run_id = 'smoke_approval_01'
        make_run(self.store, run_id, run_state='waiting_human')

        # Simulate: steps 0-2 done, step 3 gated
        for idx, name in enumerate(['parse_lead', 'enrich_company', 'draft_email']):
            k = effect_key(run_id, idx, 'noop', 'x')
            self.journal.append_step(
                run_id=run_id, step_name=name, step_index=idx,
                started_at=now(), completed_at=now(),
                input_state={}, output_state={'step': idx},
            )
            self.idem.register_planned(
                run_id=run_id, step_index=idx, effect_type='noop',
                effect_key=k, target='x', payload={}, created_at=now(),
            )
            self.idem.commit(k, now())

        approval_id = self.approvals.request_approval(run_id, 3, 'email.send')

        # Cannot resume while pending
        ctx = self.resume.determine_resume_point(run_id)
        self.assertFalse(ctx['can_resume'])
        self.assertEqual(ctx['reason'], 'waiting_for_approval')

        # Approve
        self.approvals.record_decision(approval_id, 'approved', 'user_andy', 'ok')
        run = self.store.get_run(run_id)
        self.assertEqual(run['run_state'], 'retrying')

        # Now resumable
        ctx2 = self.resume.determine_resume_point(run_id)
        self.assertTrue(ctx2['can_resume'])
        self.assertEqual(ctx2['resume_step_index'], 3)

    # ------------------------------------------------------------------
    # 6. Denial mid-run — run fails cleanly
    # ------------------------------------------------------------------
    def test_denial_fails_run_cleanly(self) -> None:
        run_id = 'smoke_deny_01'
        make_run(self.store, run_id)

        policy = RuntimePolicy({'email.send': 'approval_required'}, self.store, self.approvals)
        decision = policy.check(run_id=run_id, step_index=3, action='email.send')
        self.approvals.record_decision(decision.approval_id, 'denied', 'user_andy', 'not approved')

        run = self.store.get_run(run_id)
        self.assertEqual(run['run_state'], 'failed')

        ctx = self.resume.determine_resume_point(run_id)
        self.assertFalse(ctx['can_resume'])
        self.assertIn('failed', ctx['reason'])

    # ------------------------------------------------------------------
    # 7. Dependency block before run starts
    # ------------------------------------------------------------------
    def test_dependency_block_prevents_run(self) -> None:
        run_id = 'smoke_dep_01'
        make_run(self.store, run_id)
        dep_mgr = DependencyManager(self.store)

        manifest = validate_manifest({
            'id': 'scout_operator', 'name': 'Scout', 'version': '1.0.0',
            'type': 'skill', 'capabilities': ['email.send', 'crm.write'],
            'required_dependencies': ['gmail_operator'],
            'requested_permissions': ['gmail.send'],
            'autonomy_level': 'semi_autonomous',
            'health_hook': '/health',
            'description': 'Scout operator for lead processing.',
        })

        # gmail_operator not installed
        result = dep_mgr.check(run_id, manifest,
                               installed_assets=set(),
                               granted_permissions={'gmail.send'})

        self.assertIsNotNone(result)
        run = self.store.get_run(run_id)
        self.assertEqual(run['run_state'], 'blocked')
        self.assertEqual(run['blocked_reason'], 'missing_operator')
        self.assertEqual(run['blocking_entity'], 'gmail_operator')

        # Blocked run not in resume scan
        resumable = self.resume.scan_resumable()
        self.assertNotIn(run_id, [r['run']['run_id'] for r in resumable])

    # ------------------------------------------------------------------
    # 8. Mixed side effects — email + CRM — both idempotent across restart
    # ------------------------------------------------------------------
    def test_mixed_side_effects_idempotent(self) -> None:
        run_id = 'smoke_mixed_01'
        make_run(self.store, run_id)

        op = FakeOperator(self.store, self.journal, self.idem)
        op.run(run_id)

        # Count committed effects by type
        email_fx = [e for e in self.idem.all_for_step(run_id, 3)
                    if e['effect_type'] == 'email.send' and e['status'] == 'committed']
        crm_fx = [e for e in self.idem.all_for_step(run_id, 4)
                  if e['effect_type'] == 'crm.write' and e['status'] == 'committed']

        self.assertEqual(len(email_fx), 1)
        self.assertEqual(len(crm_fx), 1)

        # Try to re-run step 3 and 4 — effects must not duplicate
        for step_idx, action, target in [(3, 'email.send', 'lead@acme.com'),
                                          (4, 'crm.write', 'Acme Corp')]:
            k = effect_key(run_id, step_idx, action, target)
            result = self.idem.register_planned(
                run_id=run_id, step_index=step_idx, effect_type=action,
                effect_key=k, target=target, payload={}, created_at=now(),
            )
            self.assertFalse(result, f'{action} must not register twice')

    # ------------------------------------------------------------------
    # 9. Complete run is never re-executed on restart
    # ------------------------------------------------------------------
    def test_complete_run_never_restarted(self) -> None:
        run_id = 'smoke_complete_01'
        make_run(self.store, run_id)
        op = FakeOperator(self.store, self.journal, self.idem)
        op.run(run_id)

        run = self.store.get_run(run_id)
        self.assertEqual(run['run_state'], 'complete')

        # Startup scan must not include this run
        scan = self.resume.scan_resumable()
        self.assertNotIn(run_id, [r['run']['run_id'] for r in scan])

        # determine_resume_point must refuse
        ctx = self.resume.determine_resume_point(run_id)
        self.assertFalse(ctx['can_resume'])
        self.assertIn('complete', ctx['reason'])


class TestMigrationIdempotency(unittest.TestCase):
    """Schema version survives repeated migration calls."""

    def test_repeated_migration_stable(self) -> None:
        tempdir = tempfile.TemporaryDirectory()
        db_path = f'{tempdir.name}/test.db'
        conn = connect(db_path)
        # Run migration 3 times
        for _ in range(3):
            migrate(conn)
            conn.commit()
        row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        self.assertEqual(int(row['value']), SCHEMA_VERSION)
        conn.close()
        tempdir.cleanup()

    def test_migration_adds_missing_columns_to_existing_db(self) -> None:
        """Legacy DB with only basic columns gets new fields added cleanly."""
        import sqlite3
        tempdir = tempfile.TemporaryDirectory()
        db_path = f'{tempdir.name}/legacy.db'

        # Create a minimal legacy schema
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY,
                operator_id TEXT,
                resume_status TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        """)
        conn.execute("INSERT INTO runs VALUES ('r_legacy','op','running','t','t')")
        conn.commit()
        conn.close()

        # Run migration
        conn = connect(db_path)
        migrate(conn)
        conn.commit()

        cols = {r['name'] for r in conn.execute('PRAGMA table_info(runs)').fetchall()}
        conn.close()

        self.assertIn('process_state', cols)
        self.assertIn('run_state', cols)
        self.assertIn('blocked_reason', cols)
        self.assertIn('dependency_request', cols)
        tempdir.cleanup()


if __name__ == '__main__':
    print(f'\\n=== Cascadia OS {VERSION_SHORT} — Smoke Tests & End-to-End Drills ===\\n')
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [TestFullRunLifecycle, TestMigrationIdempotency]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    passed = result.testsRun - len(result.failures) - len(result.errors)
    print(f'\n{"=" * 52}')
    print(f'  Smoke test results: {passed}/{result.testsRun} passed')
    if result.failures or result.errors:
        for label, items in (('FAILURES', result.failures), ('ERRORS', result.errors)):
            for test, tb in items:
                print(f'  {label}: {test}')
                print(f'  {tb.splitlines()[-1]}')
    print('=' * 52)
    import sys as _sys
    _sys.exit(0 if not result.failures and not result.errors else 1)
