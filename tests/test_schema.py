from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from cascadia.durability.run_store import RunStore
from cascadia.shared.db import connect, ensure_database


class SchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = f"{self.tempdir.name}/test.db"
        self.store = RunStore(self.db_path)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_tables_exist(self) -> None:
        conn = connect(self.db_path)
        try:
            tables = {row['name'] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        finally:
            conn.close()
        for expected in {'meta', 'runs', 'steps', 'side_effects', 'approvals', 'run_trace'}:
            self.assertIn(expected, tables)

    def test_state_split_exists(self) -> None:
        conn = connect(self.db_path)
        try:
            columns = {row['name'] for row in conn.execute('PRAGMA table_info(runs)').fetchall()}
        finally:
            conn.close()
        self.assertIn('process_state', columns)
        self.assertIn('run_state', columns)

    def test_side_effects_unique_key_constraint(self) -> None:
        with self.store.connection() as conn:
            conn.execute("INSERT INTO runs (run_id, operator_id, tenant_id, goal, current_step, input_snapshot, state_snapshot, retry_count, last_checkpoint, process_state, run_state, blocked_reason, blocking_entity, dependency_request, created_at, updated_at) VALUES ('run1','op','default','g','s','{}','{}',0,NULL,'ready','running',NULL,NULL,NULL,'t','t')")
            conn.execute("INSERT INTO side_effects (run_id, step_index, effect_type, effect_key, status, target, payload, created_at, committed_at) VALUES ('run1',0,'email.send','ek1','planned','a','{}','t',NULL)")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO side_effects (run_id, step_index, effect_type, effect_key, status, target, payload, created_at, committed_at) VALUES ('run1',0,'email.send','ek1','planned','a','{}','t',NULL)")

    def test_migration_preserves_legacy_run(self) -> None:
        legacy = Path(self.tempdir.name) / 'legacy.db'
        conn = sqlite3.connect(legacy)
        try:
            conn.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY, operator_id TEXT, tenant_id TEXT, goal TEXT, current_step TEXT, input_snapshot TEXT, state_snapshot TEXT, retry_count INTEGER, last_checkpoint TEXT, resume_status TEXT, created_at TEXT, updated_at TEXT)")
            conn.execute("INSERT INTO runs VALUES ('legacy_run','op','default','goal','step','{}','{}',0,NULL,'running','t','t')")
            conn.commit()
        finally:
            conn.close()
        ensure_database(str(legacy))
        conn = connect(str(legacy))
        try:
            columns = {row['name'] for row in conn.execute('PRAGMA table_info(runs)').fetchall()}
            row = conn.execute("SELECT * FROM runs WHERE run_id = 'legacy_run'").fetchone()
        finally:
            conn.close()
        self.assertIn('process_state', columns)
        self.assertIn('run_state', columns)
        self.assertIsNotNone(row)

if __name__ == '__main__':
    unittest.main()
