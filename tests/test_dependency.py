from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from cascadia.durability.run_store import RunStore
from cascadia.shared.manifest_schema import load_manifest
from cascadia.system.dependency_manager import DependencyManager


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DependencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = RunStore(f"{self.tempdir.name}/test.db")
        self.manager = DependencyManager(self.store)
        self.run_id = 'run_dep'
        self.store.create_run({'run_id': self.run_id, 'operator_id': 'gmail_operator', 'tenant_id': 'default', 'goal': 'dependency test', 'current_step': 'check', 'input_snapshot': {}, 'state_snapshot': {}, 'retry_count': 0, 'last_checkpoint': None, 'process_state': 'ready', 'run_state': 'running', 'created_at': now(), 'updated_at': now()})

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_missing_operator_detected(self) -> None:
        manifest = load_manifest(Path('cascadia/operators/main_operator.json'))
        manifest.required_dependencies.append('gmail_operator')
        payload = self.manager.check(self.run_id, manifest, installed_assets=set(), granted_permissions=set())
        run = self.store.get_run(self.run_id)
        self.assertEqual(run['blocked_reason'], 'missing_operator')
        self.assertEqual(run['blocking_entity'], 'gmail_operator')
        self.assertEqual(payload['type'], 'missing_operator')

    def test_missing_permission_detected(self) -> None:
        manifest = load_manifest(Path('cascadia/operators/gmail_operator.json'))
        payload = self.manager.check(self.run_id, manifest, installed_assets={'gmail_operator'}, granted_permissions={'gmail.readonly'})
        run = self.store.get_run(self.run_id)
        self.assertEqual(run['blocked_reason'], 'missing_permission')
        self.assertEqual(run['blocking_entity'], 'gmail.send')
        self.assertEqual(payload['type'], 'missing_permission')

if __name__ == '__main__':
    unittest.main()
