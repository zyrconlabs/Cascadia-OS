from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cascadia.automation.stitch import WorkflowDefinition, WorkflowStep, StitchService
from cascadia.automation.workflow_runtime import WorkflowRuntime
from cascadia.chat.bell import BellService
from cascadia.durability.idempotency import IdempotencyManager
from cascadia.durability.run_store import RunStore
from cascadia.durability.step_journal import StepJournal


def make_definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        'lead_follow_up',
        'Lead Follow-Up',
        [
            WorkflowStep('parse_lead', 'main_operator', 'parse_lead'),
            WorkflowStep('enrich_company', 'main_operator', 'enrich_company'),
            WorkflowStep('draft_email', 'main_operator', 'draft_email'),
            WorkflowStep('send_email', 'gmail_operator', 'email.send'),
            WorkflowStep('log_crm', 'main_operator', 'crm.write'),
        ],
    )


class TestPriorityOneWorkflowRuntime(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = f'{self.tempdir.name}/cascadia.db'
        self.runtime = WorkflowRuntime(self.db_path)
        self.definition = make_definition()
        self.store = RunStore(self.db_path)
        self.journal = StepJournal(self.store)
        self.idem = IdempotencyManager(self.store)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_lead_follow_up_waits_for_approval_then_completes(self) -> None:
        first = self.runtime.execute('lead_follow_up', self.definition, {
            'content': 'Hi, this is John Doe from Acme Logistics. We need pricing for a conveyor upgrade. Email john@acme-logistics.com and this is urgent.',
            'goal': 'Follow up with inbound lead',
        }).to_dict()

        self.assertEqual(first['run_state'], 'waiting_human')
        self.assertEqual(first['current_step'], 'send_email')
        self.assertIsNotNone(first['pending_approval_id'])
        self.assertIn('Approval required', first['assistant_message'])

        self.runtime.approvals.record_decision(first['pending_approval_id'], 'approved', 'tester', 'looks good')
        second = self.runtime.execute('lead_follow_up', self.definition, {
            'run_id': first['run_id'],
        }).to_dict()

        self.assertEqual(second['run_state'], 'complete')
        self.assertEqual(second['current_step'], 'complete')
        self.assertEqual(second['state_snapshot']['crm_logged'], True)
        self.assertEqual(second['state_snapshot']['delivery_status'], 'simulated_sent')
        self.assertEqual(second['state_snapshot']['lead_email'], 'john@acme-logistics.com')

        email_effects = self.idem.all_for_step(first['run_id'], 3)
        committed = [e for e in email_effects if e['status'] == 'committed']
        self.assertEqual(len(committed), 1)

    def test_resume_after_commit_does_not_duplicate_send(self) -> None:
        first = self.runtime.execute('lead_follow_up', self.definition, {
            'content': 'Quote request from jane@orbit-freight.com for warehouse automation.',
        }).to_dict()
        self.runtime.approvals.record_decision(first['pending_approval_id'], 'approved', 'tester')
        second = self.runtime.execute('lead_follow_up', self.definition, {'run_id': first['run_id']}).to_dict()
        third = self.runtime.execute('lead_follow_up', self.definition, {'run_id': first['run_id']}).to_dict()

        self.assertEqual(second['run_state'], 'complete')
        self.assertEqual(third['run_state'], 'complete')

        email_effects = self.idem.all_for_step(first['run_id'], 3)
        committed = [e for e in email_effects if e['status'] == 'committed']
        self.assertEqual(len(committed), 1)


class TestBellToStitchWorkflow(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        (self.root / 'logs').mkdir()
        config = {
            'log_dir': str(self.root / 'logs'),
            'database_path': str(self.root / 'cascadia.db'),
            'components': [
                {'name': 'stitch', 'port': 16201, 'heartbeat_file': str(self.root / 'stitch.heartbeat')},
                {'name': 'bell', 'port': 16204, 'heartbeat_file': str(self.root / 'bell.heartbeat')},
            ],
        }
        self.config_path = self.root / 'config.json'
        self.config_path.write_text(json.dumps(config), encoding='utf-8')
        self.stitch = StitchService(str(self.config_path), 'stitch')
        self.bell = BellService(str(self.config_path), 'bell')

        def fake_post(_: int, path: str, payload: dict, timeout: float = 3.0):
            if path == '/run/execute':
                _, result = self.stitch.execute_run(payload)
                return result
            if path == '/run/resume':
                _, result = self.stitch.resume_run(payload)
                return result
            raise AssertionError(path)

        self.bell._http_post = fake_post  # type: ignore[method-assign]

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_bell_can_start_and_approve_workflow(self) -> None:
        _, start = self.bell.start_session({'tenant_id': 'acme'})
        session_id = start['session_id']

        _, response = self.bell.receive_message({
            'session_id': session_id,
            'workflow_id': 'lead_follow_up',
            'content': 'Hello, this is Sarah Smith from Axis Storage. Need pricing for mezzanine automation. Email sarah@axis-storage.com.',
        })
        self.assertEqual(response['run_state'], 'waiting_human')
        self.assertTrue(response['run_id'].startswith('run_'))
        self.assertIsNotNone(response['pending_approval_id'])

        _, approval = self.bell.receive_approval({
            'session_id': session_id,
            'approval_id': response['pending_approval_id'],
            'decision': 'approved',
            'reason': 'send it',
            'actor': 'andrey',
        })
        self.assertEqual(approval['decision'], 'approved')
        self.assertEqual(approval['resume_result']['run_state'], 'complete')
        state = approval['resume_result']['state_snapshot']
        self.assertEqual(state['lead_name'], 'Sarah Smith')
        self.assertEqual(state['company'], 'Axis Storage')
        self.assertEqual(state['draft_subject'], 'Follow-up for Axis Storage — Warehouse Automation')

        _, history = self.bell.get_history({'session_id': session_id})
        contents = [m['content'] for m in history['messages']]
        self.assertTrue(any('Approval required' in c for c in contents))
        self.assertTrue(any('Best,' in c for c in contents))
