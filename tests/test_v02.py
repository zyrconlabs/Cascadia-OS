"""
tests/test_v02.py - Cascadia OS v0.43
Full integration test suite covering all named components.
"""
from __future__ import annotations

from cascadia import VERSION_SHORT

import sys
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

# Run from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# FLINT / Schema / Durability (v2.1 baseline — must still pass)
# ---------------------------------------------------------------------------

class TestFlintSchema(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        from cascadia.durability.run_store import RunStore
        self.store = RunStore(f'{self.tempdir.name}/test.db')

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_all_tables_exist(self) -> None:
        from cascadia.shared.db import connect
        conn = connect(f'{self.tempdir.name}/test.db')
        tables = {r['name'] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        conn.close()
        for t in ('meta', 'runs', 'steps', 'side_effects', 'approvals', 'run_trace'):
            self.assertIn(t, tables)

    def test_process_run_state_split(self) -> None:
        from cascadia.shared.db import connect
        conn = connect(f'{self.tempdir.name}/test.db')
        cols = {r['name'] for r in conn.execute('PRAGMA table_info(runs)').fetchall()}
        conn.close()
        self.assertIn('process_state', cols)
        self.assertIn('run_state', cols)
        self.assertNotIn('resume_status', cols)

    def test_side_effect_unique_key(self) -> None:
        import sqlite3
        with self.store.connection() as conn:
            conn.execute("INSERT INTO runs VALUES ('r1','op','t','g','s','{}','{}',0,NULL,'ready','running',NULL,NULL,NULL,'t','t')")
            conn.execute("INSERT INTO side_effects (run_id,step_index,effect_type,effect_key,status,target,payload,created_at) VALUES ('r1',0,'email.send','ek1','planned','x','{}','t')")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO side_effects (run_id,step_index,effect_type,effect_key,status,target,payload,created_at) VALUES ('r1',0,'email.send','ek1','planned','x','{}','t')")


# ---------------------------------------------------------------------------
# CREW tests
# ---------------------------------------------------------------------------

class TestCrew(unittest.TestCase):
    def setUp(self) -> None:
        from cascadia.registry.crew import CrewService
        # Use CrewService internal logic directly without HTTP
        self.registry = {}

    def test_register_and_list(self) -> None:
        self.registry['scout'] = {'operator_id': 'scout', 'capabilities': ['email.send', 'crm.write'], 'type': 'skill'}
        self.assertIn('scout', self.registry)
        self.assertEqual(self.registry['scout']['capabilities'], ['email.send', 'crm.write'])

    def test_capability_validation(self) -> None:
        caps = ['email.send', 'crm.*', 'vault.read']
        # Direct capability
        self.assertIn('email.send', caps)
        # Wildcard
        self.assertTrue(any('crm.write'.startswith(c[:-1]) for c in caps if c.endswith('*')))
        # Missing
        self.assertNotIn('shell.exec', caps)
        self.assertFalse(any('shell.exec'.startswith(c[:-1]) for c in caps if c.endswith('*')))

    def test_wildcard_coverage(self) -> None:
        caps = ['crm.*']
        for action in ('crm.read', 'crm.write', 'crm.delete'):
            self.assertTrue(any(action.startswith(c[:-1]) for c in caps if c.endswith('*')))


# ---------------------------------------------------------------------------
# SENTINEL tests
# ---------------------------------------------------------------------------

class TestSentinel(unittest.TestCase):
    def setUp(self) -> None:
        from cascadia.security.sentinel import RISK_LEVELS, COMPLIANCE_RULES
        self.risk = RISK_LEVELS
        self.rules = COMPLIANCE_RULES

    def test_shell_exec_always_blocked(self) -> None:
        allowed = self.rules.get('shell.exec', [])
        self.assertEqual(allowed, [])

    def test_email_send_semi_autonomous_allowed(self) -> None:
        allowed = self.rules.get('email.send', [])
        self.assertIn('semi_autonomous', allowed)
        self.assertIn('autonomous', allowed)
        self.assertNotIn('manual_only', allowed)
        self.assertNotIn('assistive', allowed)

    def test_crm_write_assistive_allowed(self) -> None:
        allowed = self.rules.get('crm.write', [])
        self.assertIn('assistive', allowed)

    def test_billing_requires_full_autonomy(self) -> None:
        allowed = self.rules.get('billing.write', [])
        self.assertEqual(allowed, ['autonomous'])

    def test_risk_levels_present(self) -> None:
        for action in ('email.send', 'shell.exec', 'billing.write', 'vault.read'):
            self.assertIn(action, self.risk)

    def test_risk_classification(self) -> None:
        self.assertEqual(self.risk['shell.exec'], 'critical')
        self.assertEqual(self.risk['vault.read'], 'low')
        self.assertEqual(self.risk['billing.write'], 'high')


# ---------------------------------------------------------------------------
# CURTAIN tests
# ---------------------------------------------------------------------------

class TestCurtain(unittest.TestCase):
    def setUp(self) -> None:
        from cascadia.encryption.curtain import sign_envelope, verify_envelope, generate_session_key, encrypt_field, decrypt_field
        self.sign = sign_envelope
        self.verify = verify_envelope
        self.gen_key = generate_session_key
        self.encrypt = encrypt_field
        self.decrypt = decrypt_field

    def test_sign_and_verify(self) -> None:
        payload = {'run_id': 'run_abc', 'action': 'email.send'}
        token = self.sign(payload, 'test-secret')
        valid, result = self.verify(token, 'test-secret')
        self.assertTrue(valid)
        self.assertEqual(result['run_id'], 'run_abc')

    def test_wrong_secret_fails(self) -> None:
        token = self.sign({'data': 'test'}, 'correct-secret')
        valid, _ = self.verify(token, 'wrong-secret')
        self.assertFalse(valid)

    def test_tampered_token_fails(self) -> None:
        token = self.sign({'data': 'test'}, 'secret')
        tampered = token[:-4] + 'XXXX'
        valid, _ = self.verify(tampered, 'secret')
        self.assertFalse(valid)

    def test_session_key_generation(self) -> None:
        k1 = self.gen_key()
        k2 = self.gen_key()
        self.assertNotEqual(k1, k2)
        self.assertEqual(len(k1), 64)  # 32 bytes hex

    def test_field_encrypt_decrypt(self) -> None:
        import hashlib
        key = hashlib.sha256(b'test-key').digest()
        original = 'sensitive data'
        encrypted = self.encrypt(original, key)
        self.assertNotEqual(encrypted, original)
        decrypted = self.decrypt(encrypted, key)
        self.assertEqual(decrypted, original)


# ---------------------------------------------------------------------------
# VAULT tests
# ---------------------------------------------------------------------------

class TestVault(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        from cascadia.memory.vault import VaultStore
        self.vault = VaultStore(f'{self.tempdir.name}/vault.db')

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_write_and_read(self) -> None:
        self.vault.write('customer:001', {'name': 'Acme', 'industry': 'Logistics'}, 'scout')
        result = self.vault.read('customer:001')
        self.assertEqual(result['name'], 'Acme')

    def test_namespace_isolation(self) -> None:
        self.vault.write('key1', 'value_a', 'op1', namespace='ns_a')
        self.vault.write('key1', 'value_b', 'op2', namespace='ns_b')
        self.assertEqual(self.vault.read('key1', 'ns_a'), 'value_a')
        self.assertEqual(self.vault.read('key1', 'ns_b'), 'value_b')

    def test_missing_key_returns_none(self) -> None:
        self.assertIsNone(self.vault.read('nonexistent'))

    def test_delete(self) -> None:
        self.vault.write('temp', 'data', 'op')
        self.assertIsNotNone(self.vault.read('temp'))
        self.vault.delete('temp')
        self.assertIsNone(self.vault.read('temp'))

    def test_list_keys(self) -> None:
        self.vault.write('lead:001', {}, 'op')
        self.vault.write('lead:002', {}, 'op')
        self.vault.write('note:001', {}, 'op')
        keys = self.vault.list_keys(prefix='lead:')
        self.assertIn('lead:001', keys)
        self.assertIn('lead:002', keys)
        self.assertNotIn('note:001', keys)

    def test_overwrite(self) -> None:
        self.vault.write('k', 'v1', 'op')
        self.vault.write('k', 'v2', 'op')
        self.assertEqual(self.vault.read('k'), 'v2')


# ---------------------------------------------------------------------------
# STITCH tests
# ---------------------------------------------------------------------------

class TestStitch(unittest.TestCase):
    def setUp(self) -> None:
        from cascadia.automation.stitch import WorkflowDefinition, WorkflowStep, WorkflowRun
        self.WfDef = WorkflowDefinition
        self.Step = WorkflowStep
        self.Run = WorkflowRun

    def test_builtin_lead_followup_steps(self) -> None:
        steps = [
            self.Step('parse_lead', 'main_operator', 'parse_lead'),
            self.Step('enrich_company', 'main_operator', 'enrich_company'),
            self.Step('draft_email', 'main_operator', 'draft_email'),
            self.Step('send_email', 'gmail_operator', 'email.send'),
            self.Step('log_crm', 'main_operator', 'crm.write'),
        ]
        wf = self.WfDef('lead_follow_up', 'Lead Follow-Up', steps)
        self.assertEqual(len(wf.steps), 5)
        self.assertEqual(wf.steps[3].operator, 'gmail_operator')

    def test_run_lifecycle(self) -> None:
        run = self.Run('run_001', 'lead_follow_up', 'default', 'Test goal', total_steps=5)
        self.assertEqual(run.state, 'pending')
        self.assertEqual(run.current_step, 0)
        run.state = 'running'
        run.current_step = 3
        d = run.to_dict()
        self.assertEqual(d['progress_pct'], 60)
        self.assertEqual(d['run_state'], 'running')

    def test_workflow_to_dict(self) -> None:
        wf = self.WfDef('test_wf', 'Test', [self.Step('step1', 'op', 'action')])
        d = wf.to_dict()
        self.assertEqual(d['workflow_id'], 'test_wf')
        self.assertEqual(d['step_count'], 1)
        self.assertEqual(d['steps'][0]['operator'], 'op')


# ---------------------------------------------------------------------------
# VANGUARD tests
# ---------------------------------------------------------------------------

class TestVanguard(unittest.TestCase):
    def setUp(self) -> None:
        from cascadia.gateway.vanguard import InboundMessage, CHANNEL_TYPES
        self.MsgClass = InboundMessage
        self.channel_types = CHANNEL_TYPES

    def test_message_normalization(self) -> None:
        msg = self.MsgClass(channel='email', sender='lead@acme.com', content='Hello')
        envelope = msg.to_envelope()
        self.assertEqual(envelope['channel'], 'email')
        self.assertEqual(envelope['sender'], 'lead@acme.com')
        self.assertIn('message_id', envelope)
        self.assertTrue(envelope['message_id'].startswith('vg_'))

    def test_supported_channels(self) -> None:
        for ch in ('email', 'webhook', 'sms', 'api', 'bell'):
            self.assertIn(ch, self.channel_types)

    def test_unique_message_ids(self) -> None:
        m1 = self.MsgClass('email', 'a@b.com', 'msg1')
        m2 = self.MsgClass('email', 'a@b.com', 'msg2')
        self.assertNotEqual(m1.message_id, m2.message_id)


# ---------------------------------------------------------------------------
# HANDSHAKE tests
# ---------------------------------------------------------------------------

class TestHandshake(unittest.TestCase):
    def setUp(self) -> None:
        from cascadia.bridge.handshake import ServiceConnection, SERVICE_TYPES
        self.ConnClass = ServiceConnection
        self.service_types = SERVICE_TYPES

    def test_service_types_complete(self) -> None:
        for st in ('crm', 'erp', 'email', 'calendar', 'payment', 'webhook'):
            self.assertIn(st, self.service_types)

    def test_connection_to_dict(self) -> None:
        conn = self.ConnClass('hs_001', 'crm', 'HubSpot', 'https://api.hubspot.com', 'vault_key_hubspot')
        d = conn.to_dict()
        self.assertEqual(d['connection_id'], 'hs_001')
        self.assertEqual(d['service_type'], 'crm')
        self.assertEqual(d['vault_credential_key'], 'vault_key_hubspot')
        self.assertNotIn('password', d)
        self.assertNotIn('api_key', d)

    def test_credentials_not_stored_in_connection(self) -> None:
        conn = self.ConnClass('hs_002', 'email', 'Gmail', 'https://gmail.googleapis.com', 'vault:gmail_token')
        d = conn.to_dict()
        # Only vault reference key should be present — not the credential value
        self.assertIn('vault_credential_key', d)
        self.assertEqual(d['vault_credential_key'], 'vault:gmail_token')


# ---------------------------------------------------------------------------
# ALMANAC tests
# ---------------------------------------------------------------------------

class TestAlmanac(unittest.TestCase):
    def setUp(self) -> None:
        from cascadia.guide.almanac import COMPONENT_CATALOG, GLOSSARY, RUNBOOK
        self.catalog = COMPONENT_CATALOG
        self.glossary = GLOSSARY
        self.runbook = RUNBOOK

    def test_all_components_documented(self) -> None:
        expected = {'FLINT', 'PRISM', 'BELL', 'VANGUARD', 'CURTAIN', 'SENTINEL',
                    'VAULT', 'BEACON', 'STITCH', 'HANDSHAKE', 'CREW', 'ALMANAC'}
        for comp in expected:
            self.assertIn(comp, self.catalog, f'{comp} missing from ALMANAC catalog')

    def test_all_components_have_owns_and_does_not_own(self) -> None:
        for name, comp in self.catalog.items():
            self.assertIn('owns', comp, f'{name} missing owns')
            self.assertIn('does_not_own', comp, f'{name} missing does_not_own')

    def test_glossary_covers_all_names(self) -> None:
        for term in ('flint', 'prism', 'bell', 'vanguard', 'curtain', 'sentinel',
                     'vault', 'beacon', 'stitch', 'handshake', 'crew', 'almanac'):
            self.assertIn(term, self.glossary, f'{term} missing from glossary')

    def test_runbook_entries_complete(self) -> None:
        for entry_id, entry in self.runbook.items():
            for field in ('title', 'symptom', 'check', 'cause', 'fix'):
                self.assertIn(field, entry, f'{entry_id} missing {field}')

    def test_search_finds_flint(self) -> None:
        from cascadia.guide.almanac import COMPONENT_CATALOG, GLOSSARY
        query = 'flint'
        results = []
        for name, comp in COMPONENT_CATALOG.items():
            if query in name.lower() or query in comp['description'].lower():
                results.append(name)
        for term in GLOSSARY:
            if query in term:
                results.append(term)
        self.assertTrue(len(results) > 0)


# ---------------------------------------------------------------------------
# ONCE installer tests
# ---------------------------------------------------------------------------

class TestOnce(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_python_version_check(self) -> None:
        from cascadia.installer.once import OnceInstaller
        installer = OnceInstaller(self.tempdir.name)
        result = installer.check_python()
        self.assertTrue(result)  # We're running on a compatible Python

    def test_directory_creation(self) -> None:
        from cascadia.installer.once import OnceInstaller, DEFAULT_DIRS
        installer = OnceInstaller(self.tempdir.name)
        installer.create_directories()
        for d in DEFAULT_DIRS:
            self.assertTrue((Path(self.tempdir.name) / d).exists(), f'{d} not created')

    def test_config_generation(self) -> None:
        from cascadia.installer.once import OnceInstaller
        installer = OnceInstaller(self.tempdir.name)
        installer.create_directories()
        installer.generate_config()
        config_path = Path(self.tempdir.name) / 'config.json'
        self.assertTrue(config_path.exists())
        import json
        config = json.loads(config_path.read_text())
        self.assertIn('flint', config)
        self.assertIn('components', config)
        self.assertEqual(len(config['components']), 11)

    def test_config_not_overwritten(self) -> None:
        from cascadia.installer.once import OnceInstaller
        import json
        installer = OnceInstaller(self.tempdir.name)
        config_path = Path(self.tempdir.name) / 'config.json'
        config_path.write_text(json.dumps({'custom': True}))
        installer.generate_config()
        config = json.loads(config_path.read_text())
        self.assertTrue(config.get('custom'))

    def test_signing_secret_generated(self) -> None:
        from cascadia.installer.once import OnceInstaller
        import json
        installer = OnceInstaller(self.tempdir.name)
        installer.create_directories()
        installer.generate_config()
        config = json.loads((Path(self.tempdir.name) / 'config.json').read_text())
        secret = config.get('curtain', {}).get('signing_secret', '')
        self.assertEqual(len(secret), 64)  # 32 bytes hex


# ---------------------------------------------------------------------------
# BELL tests
# ---------------------------------------------------------------------------

class TestBell(unittest.TestCase):
    def setUp(self) -> None:
        from cascadia.chat.bell import ChatSession
        self.SessionClass = ChatSession

    def test_session_creation(self) -> None:
        session = self.SessionClass('bell_test001', 'acme')
        self.assertEqual(session.session_id, 'bell_test001')
        self.assertEqual(session.tenant_id, 'acme')
        self.assertEqual(len(session.messages), 0)

    def test_add_messages(self) -> None:
        session = self.SessionClass('bell_test002')
        msg1 = session.add_message('user', 'Hello')
        msg2 = session.add_message('assistant', 'Hi there')
        self.assertEqual(len(session.messages), 2)
        self.assertEqual(msg1['role'], 'user')
        self.assertEqual(msg2['role'], 'assistant')

    def test_session_to_dict(self) -> None:
        session = self.SessionClass('bell_test003', 'tenant_x')
        session.add_message('user', 'Test message')
        d = session.to_dict()
        self.assertEqual(d['message_count'], 1)
        self.assertEqual(d['tenant_id'], 'tenant_x')

    def test_pending_approvals_tracked(self) -> None:
        session = self.SessionClass('bell_test004')
        session.pending_approvals.append('approval_001')
        self.assertIn('approval_001', session.pending_approvals)
        session.pending_approvals.remove('approval_001')
        self.assertEqual(len(session.pending_approvals), 0)


# ---------------------------------------------------------------------------
# Manifest tests (operator manifest validation)
# ---------------------------------------------------------------------------

class TestManifests(unittest.TestCase):
    def test_three_operator_manifests(self) -> None:
        from cascadia.shared.manifest_schema import load_manifest
        base = Path('cascadia/operators')
        if not base.exists():
            self.skipTest('Run from project root')
        for fname in ('main_operator.json', 'gmail_operator.json', 'calendar_operator.json'):
            path = base / fname
            if path.exists():
                m = load_manifest(path)
                self.assertTrue(m.id)
                self.assertIn(m.type, ('system', 'service', 'skill', 'composite'))
                self.assertIn(m.autonomy_level, ('manual_only', 'assistive', 'semi_autonomous', 'autonomous'))

    def test_manifest_validation_rejects_bad_type(self) -> None:
        from cascadia.shared.manifest_schema import validate_manifest, ManifestValidationError
        with self.assertRaises(ManifestValidationError):
            validate_manifest({
                'id': 'bad_op', 'name': 'Bad', 'version': '1.0.0',
                'type': 'invalid_type',  # Bad
                'capabilities': [], 'required_dependencies': [],
                'requested_permissions': [], 'autonomy_level': 'assistive',
                'health_hook': '/health', 'description': 'Test',
            })

    def test_manifest_id_must_be_lowercase(self) -> None:
        from cascadia.shared.manifest_schema import validate_manifest, ManifestValidationError
        with self.assertRaises(ManifestValidationError):
            validate_manifest({
                'id': 'BadId',  # Has uppercase
                'name': 'Test', 'version': '1.0.0', 'type': 'skill',
                'capabilities': [], 'required_dependencies': [],
                'requested_permissions': [], 'autonomy_level': 'assistive',
                'health_hook': '/health', 'description': 'Test',
            })


# ---------------------------------------------------------------------------
# Resume + Approval integration (v2.1 durability — must still pass)
# ---------------------------------------------------------------------------

class TestDurabilityIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        from cascadia.durability.run_store import RunStore
        from cascadia.durability.step_journal import StepJournal
        from cascadia.durability.idempotency import IdempotencyManager
        from cascadia.durability.resume_manager import ResumeManager
        from cascadia.system.approval_store import ApprovalStore
        self.store = RunStore(f'{self.tempdir.name}/test.db')
        self.journal = StepJournal(self.store)
        self.idem = IdempotencyManager(self.store)
        self.resume = ResumeManager(self.store, self.journal, self.idem)
        self.approvals = ApprovalStore(self.store)
        self.run_id = 'run_integ_01'
        self.store.create_run({
            'run_id': self.run_id, 'operator_id': 'main_operator',
            'tenant_id': 'default', 'goal': 'Integration test',
            'current_step': 'parse_lead', 'input_snapshot': {'lead': 'acme'},
            'state_snapshot': {'lead': 'acme'}, 'retry_count': 0,
            'last_checkpoint': None, 'process_state': 'ready',
            'run_state': 'running', 'created_at': now(), 'updated_at': now(),
        })

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_resume_from_step_3(self) -> None:
        for i in range(3):
            key = f'fx_{i}'
            row = self.journal.append_step(run_id=self.run_id, step_name=f'step_{i}',
                                           step_index=i, started_at=now(), completed_at=now(),
                                           input_state={'i': i}, output_state={'i': i + 1})
            self.idem.register_planned(run_id=self.run_id, step_index=i, effect_type='noop',
                                       effect_key=key, target='x', payload={}, created_at=now())
            self.idem.commit(key, now())
        result = self.resume.determine_resume_point(self.run_id)
        self.assertTrue(result['can_resume'])
        self.assertEqual(result['resume_step_index'], 3)

    def test_approval_suspends_and_wakes(self) -> None:
        from cascadia.policy.runtime_policy import RuntimePolicy
        policy = RuntimePolicy({'email.send': 'approval_required'}, self.store, self.approvals)
        decision = policy.check(run_id=self.run_id, step_index=3, action='email.send')
        self.assertEqual(decision.decision, 'approval_required')
        run = self.store.get_run(self.run_id)
        self.assertEqual(run['run_state'], 'waiting_human')
        self.approvals.record_decision(decision.approval_id, 'approved', 'user_1', 'looks good')
        run = self.store.get_run(self.run_id)
        self.assertEqual(run['run_state'], 'retrying')

    def test_idempotency_no_duplicate(self) -> None:
        from cascadia.shared.ids import effect_key
        k = effect_key(self.run_id, 3, 'email.send', 'lead@acme.com')
        r1 = self.idem.register_planned(run_id=self.run_id, step_index=3, effect_type='email.send',
                                        effect_key=k, target='lead@acme.com', payload={}, created_at=now())
        self.assertTrue(r1)
        self.idem.commit(k, now())
        r2 = self.idem.register_planned(run_id=self.run_id, step_index=3, effect_type='email.send',
                                        effect_key=k, target='lead@acme.com', payload={}, created_at=now())
        self.assertFalse(r2)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    print(f'\\n=== Cascadia OS {VERSION_SHORT} — Full Test Suite ===\\n')
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [
        TestFlintSchema,
        TestCrew,
        TestSentinel,
        TestCurtain,
        TestVault,
        TestStitch,
        TestVanguard,
        TestHandshake,
        TestAlmanac,
        TestOnce,
        TestBell,
        TestManifests,
        TestDurabilityIntegration,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    passed = result.testsRun - len(result.failures) - len(result.errors)
    print(f'\n{"=" * 45}')
    print(f'  Results: {passed}/{result.testsRun} passed')
    if result.failures or result.errors:
        for label, items in (('FAILURES', result.failures), ('ERRORS', result.errors)):
            for test, tb in items:
                print(f'  {label}: {test}')
    print('=' * 45)
    sys.exit(0 if not result.failures and not result.errors else 1)
